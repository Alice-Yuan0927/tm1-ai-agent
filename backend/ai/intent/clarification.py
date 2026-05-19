"""Decide whether the user needs to be asked for missing details before we
query TM1. Detection is deterministic; phrasing is LLM-driven."""

import json
import logging
import re

from ...tm1.cache import find_question_element_matches
from ...util.llm_json import parse_llm_json
from ..output.conversation import conversation_context
from ..prompts.prompt_rules import GENERAL_AGENT_CONTRACT
from ..providers import complete_text
from ..schema.tm1_lexicon import SCENARIO_ALIASES, TIME_PERIOD_PATTERN

_log = logging.getLogger(__name__)


def _has_time_period(text: str) -> bool:
    return bool(TIME_PERIOD_PATTERN.search(text))


_FIELD_HINTS = {
    "time_breakdown": "user asked for a trend/over-time view but did not say by month, quarter, or year",
    "year": "no specific year mentioned (e.g., 2025 or a range)",
    "scenario": "no scenario specified (Actual, Budget, Forecast, Plan, etc.)",
    "month_range": "user asked for a monthly breakdown but did not say which months (a range like Jan-Jun, full year, YTD)",
    "month_or_period": "snapshot/statement query without a specific month or period (single month like Apr, full year, YTD, or a quarter)",
}

_FIELD_FALLBACK_LABELS = {
    "time_breakdown": "**Time breakdown**: by month, by quarter, or by year?",
    "year": "**Which year?** (for example, 2025 or a range like 2024-2026)",
    "scenario": "**Which scenario?** (Actual, Budget, Forecast, or Actual vs Budget)",
    "month_range": "**Which months?** (for example, Jan-Jun, full year, or YTD)",
    "month_or_period": "**Which month or period?** (for example, Apr, full year, or YTD)",
}


def find_clarifications(
    question: str,
    history: list[dict] | None = None,
    model_profile: dict | None = None,
) -> str | None:
    """Return a clarification message if mandatory fields are missing, else None.

    Detection is deterministic and cheap (no LLM call when nothing is missing).
    Schema/business-term ambiguity is handled by find_schema_clarification().
    """
    prior_questions = [str(msg.get("question", "")) for msg in (history or [])]
    all_user_input = " ".join(prior_questions + [question]).strip()
    combined = all_user_input.lower()
    original = question.lower()

    missing: list[str] = []

    asks_for_trend = any(term in original for term in (
        "trend", "trends", "over time", "movement", "variance over time",
    ))
    has_time_grain = any(term in combined for term in (
        "by month", "monthly", "month", "mtd",
        "by year", "yearly", "annual", "annually", "year over year", "yoy",
        "by quarter", "quarterly", "quarter", "qtr",
        "week", "weekly", "day", "daily",
    ))
    if asks_for_trend and not has_time_grain:
        missing.append("time_breakdown")

    has_year = bool(re.search(r"\b(20\d{2}|19\d{2})\b", all_user_input))
    has_yearly_breakdown = any(term in combined for term in (
        "by year", "yearly", "annual", "annually", "year over year", "yoy",
    ))
    has_current_period_hint = any(term in combined for term in (
        "current", "this year", "this month", "latest", "today",
    ))
    if not has_year and not has_yearly_breakdown and not has_current_period_hint:
        missing.append("year")

    has_scenario = any(re.search(rf"\b{re.escape(term)}\b", combined) for term in SCENARIO_ALIASES)
    if not has_scenario:
        missing.append("scenario")

    has_any_month = _has_time_period(combined)
    asks_monthly = any(term in combined for term in ("by month", "monthly"))
    asks_snapshot = any(term in combined for term in (
        "p&l", "p & l", "profit and loss", "income statement", "profit & loss",
        "balance sheet", "cash flow",
    ))
    if has_year and not has_any_month and (asks_monthly or asks_snapshot):
        missing.append("month_range" if asks_monthly else "month_or_period")

    if not missing:
        return None

    return _compose_clarification_message(question, missing, history, model_profile)


def _compose_clarification_message(
    question: str,
    missing: list[str],
    history: list[dict] | None,
    model_profile: dict | None,
) -> str:
    """LLM-phrased clarification that references the user's actual question."""
    defaults = (model_profile or {}).get("default_filters") or {}
    defaults_json = json.dumps(defaults, ensure_ascii=False) if defaults else "{}"
    needs = "\n".join(f"- {field}: {_FIELD_HINTS.get(field, field)}" for field in missing)

    prompt = f"""You help a TM1 / IBM Planning Analytics assistant ask the user for missing details.

User question: "{question}"

Previous conversation:
{conversation_context(history)}

Model default filters (already applied automatically if the user does not override):
{defaults_json}

Detected missing details:
{needs}

Write ONE short clarification message (1-3 sentences, markdown) that:
- Opens with brief context referencing what the user is actually asking about (e.g., the entity, statement, or metric they mentioned)
- Asks for the detected missing fields above. Do not silently treat defaults as user confirmation for scenario, month, period, or full-year choices.
- Uses **bold** for each field name and gives 2-4 concrete example choices tied to the user's question (e.g., "Apr", "full year YTD", "Q1")
- Friendly, conversational tone - combine into one natural sentence when possible instead of bullet spam

Reply with ONLY the markdown message, no JSON, no extra text."""

    try:
        message = complete_text(prompt, max_tokens=240, temperature=0.3).strip()
        if message.upper().startswith("NONE"):
            return _fallback_clarification(missing)
        return message
    except Exception:
        return _fallback_clarification(missing)


def _fallback_clarification(missing: list[str]) -> str:
    prefix = (
        "One more detail before I fetch the TM1 data:"
        if len(missing) == 1
        else "Before I pull the TM1 data, I need a few more details:"
    )
    bullets = "\n".join(
        f"- {_FIELD_FALLBACK_LABELS[f]}" for f in missing if f in _FIELD_FALLBACK_LABELS
    )
    return prefix + "\n\n" + bullets


def find_schema_clarification(
    question: str,
    cubes: list[dict],
    history: list[dict] | None = None,
    model_profile: dict | None = None,
) -> str | None:
    """Ask for clarification when a business term maps to multiple TM1 concepts."""
    matches = find_question_element_matches(question)
    if matches:
        sample = ", ".join(f"{m[0]} -> {m[1]}.{m[2]}" for m in matches[:5])
        _log.info("[clarify-skip] question references real elements: %s", sample)
        return None
    cube_context = json.dumps(cubes[:12], indent=2, ensure_ascii=False)
    profile_section = (
        f"\nSemantic model profile:\n{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )
    prompt = f"""You are a TM1 / IBM Planning Analytics query router.

{GENERAL_AGENT_CONTRACT}

Available cube and dimension context:
{cube_context}
{profile_section}

Current user question: "{question}"

Previous conversation:
{conversation_context(history)}

Decide whether the question has HIGH-RISK schema ambiguity that should be
clarified before running a TM1 query. Only ask when multiple plausible mappings
would materially change the data retrieved.

Examples of high-risk ambiguity:
- a term like staff could mean Employee detail, Headcount, FTE, or labor cost
- a term like forecast could mean a Scenario element or a version/year-specific label
- a number could be an employee ID rather than a year, unless it is a clear 4-digit year
- requested breakdown is not clearly available in the chosen business area

Do NOT ask if the existing conversation already resolves the ambiguity.
Do NOT ask generic questions about year/scenario/month; another checker handles those.

Reply ONLY with valid JSON:
{{"clarify": false}}
or
{{"clarify": true, "message": "<short markdown question with 2-4 concrete choices>"}}"""

    try:
        raw = complete_text(prompt, max_tokens=240, temperature=0)
        parsed = parse_llm_json(raw)
        if isinstance(parsed, dict) and parsed.get("clarify") and parsed.get("message"):
            return str(parsed["message"]).strip()
    except Exception as exc:
        _log.debug("schema clarification check failed: %s", exc)
    return None
