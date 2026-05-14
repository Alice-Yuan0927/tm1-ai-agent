"""Rule-based query intent classifier + per-dim schema pre-filter.

The classifier inspects the user's question, finds which "intent type"
(income_statement / balance_sheet / segment_breakdown / ...) it falls
under, and then narrows each dimension's `top_consolidations` to elements
that are actually relevant to that intent. The narrowed schema is what
gets fed to both the planner and the LLM, so neither has to wade through
dozens of noisy parents that the question has nothing to do with.

This is the "Python loops first, AI sees only what survives" approach -
LLM is only used as a fallback when the rules can't pin the intent.
"""

from __future__ import annotations

import re
from typing import TypedDict


class QueryIntent(TypedDict):
    primary: str
    secondaries: list[str]
    confidence: float
    matched: list[tuple[str, str]]
    currency: str   # "local" | "parent" — drives data_source dim picking


# ── Intent detection ──────────────────────────────────────────────────────────

# Currency view detection. For data_source role dims, the right element
# depends on whether the user wants the entity's local view or the parent /
# group / translated view.
_CURRENCY_VIEW_PATTERNS: list[tuple[str, str]] = [
    # parent / consolidated / translated
    (r"\b(in\s+)?usd\s*(view|amount|value|terms|p\s*&\s*l)?\b", "parent"),
    (r"\bus\s+dollars?\b", "parent"),
    (r"\bparent\s+currency\b", "parent"),
    (r"\bpct\b", "parent"),
    (r"\bconsolidated\s+(view|p\s*&\s*l|amount|usd)?\b", "parent"),
    (r"\bconsol\s+usd\b", "parent"),
    (r"\bgroup\s+(currency|view|reporting|p\s*&\s*l|level)\b", "parent"),
    (r"\btranslated\b", "parent"),
    # local / entity
    (r"\blocal\s+currency\b", "local"),
    (r"\blcy\b", "local"),
    (r"\bentity\s+currency\b", "local"),
    (r"\bas\s+reported\b", "local"),
    (r"\bin\s+local\b", "local"),
]


def _detect_currency_view(question: str) -> str:
    """Return 'local' or 'parent'. Default 'local' for entity-level questions
    where no currency is explicitly named."""
    text = (question or "").lower()
    for pattern, view in _CURRENCY_VIEW_PATTERNS:
        if re.search(pattern, text):
            return view
    return "local"


# Pattern -> intent. Order matters; first match becomes primary, subsequent
# matches go to secondaries. Patterns are matched against lowercased question.
_INTENT_PATTERNS: list[tuple[str, str]] = [
    # Financial statements
    (r"\b(p\s*&\s*l|p\s+and\s+l|profit\s+and\s+loss|income\s+statement|pnl|p\.?\s*&\s*l\.?\s*statement)\b", "income_statement"),
    (r"\b(balance\s+sheet|statement\s+of\s+financial\s+position|bs)\b", "balance_sheet"),
    (r"\b(cash\s+flow|cashflow|statement\s+of\s+cash\s+flows)\b", "cash_flow"),
    (r"\b(trial\s+balance|tb)\b", "trial_balance"),
    # Breakdowns - secondary modifiers that compose with the primary
    (r"\bby\s+(segment|segments|division|department|channel|category|bu|business\s+unit)\b", "segment_breakdown"),
    (r"\bby\s+(month|months|quarter|quarters|year|years|period|periods)\b", "time_breakdown"),
    (r"\bby\s+(intercompany|counterparty|partner)\b", "intercompany_breakdown"),
    # Comparisons
    (r"\b(actuals?|budget|budgeted|forecast|forecasted|plan|planned)\s+(?:vs\.?|versus|compared\s+to|against)\s+(actuals?|budget|budgeted|forecast|forecasted|plan|planned)\b", "scenario_comparison"),
    (r"\b20\d{2}\s+(?:vs\.?|versus|compared\s+to|against)\s+20\d{2}\b", "year_comparison"),
    (r"\b(variance|vs|versus|compare|comparison)\b", "comparison"),
]


def detect_query_intent(question: str) -> QueryIntent:
    """Classify the question into one primary intent + optional secondaries.
    Each rule is keyword-based; this gets us 70-80% of common questions
    deterministically. Unknown / unclear questions get `primary='unknown'`
    and the caller can choose to either keep a fuller schema view or invoke
    an LLM intent classifier as fallback."""
    text = (question or "").lower()
    matched: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, intent in _INTENT_PATTERNS:
        if intent in seen:
            continue
        match = re.search(pattern, text)
        if not match:
            continue
        matched.append((intent, match.group(0)))
        seen.add(intent)

    primary = matched[0][0] if matched else "unknown"
    secondaries = [intent for intent, _ in matched[1:]]
    confidence = 0.0
    if primary != "unknown":
        confidence = 0.85 if len(matched) >= 2 else 0.7
    return {
        "currency": _detect_currency_view(question),
        "primary": primary,
        "secondaries": secondaries,
        "confidence": confidence,
        "matched": matched,
    }


# ── Per-dim schema pre-filter ────────────────────────────────────────────────

# Statement-level intents that share the same line-item-dim filtering rule.
_STATEMENT_INTENTS = {"income_statement", "balance_sheet", "cash_flow", "trial_balance"}


def prepare_schema_for_query(
    schema: dict,
    question: str,
    intent: QueryIntent | None = None,
    model_profile: dict | None = None,
) -> dict:
    """Return a focused copy of `schema` where each dim's `top_consolidations`
    is narrowed to only those elements that the intent actually needs. The
    full original schema stays untouched."""
    from .dim_roles import get_dim_role

    intent = intent or detect_query_intent(question)
    profile_roles = (model_profile or {}).get("dim_roles") or {}
    question_lower = (question or "").lower()

    filtered_dims = []
    for dim in schema.get("dimensions", []) or []:
        role = get_dim_role(dim, profile_roles)
        narrowed = _filter_top_consolidations(
            dim, role, intent, question_lower
        )
        filtered_dim = dict(dim)
        filtered_dim["top_consolidations"] = narrowed
        filtered_dims.append(filtered_dim)
    return {**schema, "dimensions": filtered_dims, "_query_intent": intent}


def _filter_top_consolidations(
    dim: dict,
    role: str,
    intent: QueryIntent,
    question_lower: str,
) -> list[str]:
    """Decide which top_consolidations to keep for one dim, given the role
    and detected intent."""
    from ..tm1.cache import _looks_like_pnl_bottom_line, _base_word_variants, _dim_base_name

    all_tops = list(dim.get("top_consolidations") or [])
    if not all_tops:
        return all_tops
    primary = intent["primary"]
    secondaries = set(intent.get("secondaries", []))

    # Always keep elements whose name appears in the question - even if our
    # role/intent rule would have dropped them, the user named them explicitly.
    def shape(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())
    question_shape = shape(question_lower)
    name_mentioned = [t for t in all_tops if shape(t) and shape(t) in question_shape]

    base = _dim_base_name(dim.get("name", ""))
    base_variants = _base_word_variants(base) if base else set()
    universals = [
        t for t in all_tops
        if any(t.lower() == f"all {v}" for v in base_variants)
    ]

    # --- Line-item dim under a statement question ---
    if role == "line_item" and primary in _STATEMENT_INTENTS:
        # Keep P&L / BS / CF bottom-line names + the universal "All <basename>"
        # rollup. Drop "Total <Category>" siblings that only cover one slice.
        relevant = [t for t in all_tops if _looks_like_pnl_bottom_line(t)]
        kept = _dedup_preserve(name_mentioned + relevant + universals)
        if kept:
            return kept[:15]
        # No specific bottom-line found - fall back to "All ..." parents.
        return _dedup_preserve(name_mentioned + [t for t in all_tops if t.lower().startswith("all ")])[:10]

    # --- Scenario dim under a comparison ---
    if role == "scenario" and primary in {"scenario_comparison", "comparison"}:
        return _dedup_preserve(name_mentioned + all_tops[:15])

    # --- Time dim under a time breakdown ---
    if role == "time" and ("time_breakdown" in secondaries or primary == "time_breakdown"):
        return _dedup_preserve(name_mentioned + all_tops[:20])

    # --- Classifier dim under its matching breakdown ---
    if role == "business_classifier" and (
        "segment_breakdown" in secondaries or primary == "segment_breakdown"
    ):
        return _dedup_preserve(name_mentioned + all_tops[:15])

    # --- Counterparty dim under intercompany breakdown ---
    if role == "counterparty" and (
        "intercompany_breakdown" in secondaries or primary == "intercompany_breakdown"
    ):
        return _dedup_preserve(name_mentioned + all_tops[:15])

    # --- Default: keep a small, generic fallback set ---
    # The dim isn't directly the subject of this query, but the planner / LLM
    # still needs the universal aggregate to filter WHERE. Keep "All XYZ"
    # universals + any element the user named, drop everything else.
    fallback = name_mentioned + universals
    if not fallback:
        # No "All <basename>" - pick the shortest "All ..."/"Total ..." as the
        # cleanest generic aggregate.
        all_prefixed = sorted(
            (t for t in all_tops if re.match(r"(?i)^(all|total)\s", t)),
            key=len,
        )
        fallback = all_prefixed[:3] or all_tops[:3]
    return _dedup_preserve(fallback)[:8]


def _dedup_preserve(items: list[str]) -> list[str]:
    """Order-preserving dedup."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
