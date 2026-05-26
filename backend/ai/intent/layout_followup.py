"""Layout-followup intent: detect, classify, and build axis directives for re-layout questions."""

import json as _json
import logging
import re

from ..providers import complete_text
from ...util.llm_json import parse_llm_json

_log = logging.getLogger(__name__)

_LAYOUT_FOLLOWUP_HINT_RE = re.compile(
    r"\b(by|compare|comparison|variance|var|delta|diff|vs\.?|versus|against|same\s+rows?|same\s+columns?)\b",
    re.IGNORECASE,
)

_CORRECTION_FOLLOWUP_RE = re.compile(
    r"\b(i\s+mean|should\s+be|instead|use\s+.+\s+(?:for|as|instead)|"
    r"set\s+.+\s+to|change\s+.+\s+to|filter\s+.+\s+to)\b",
    re.IGNORECASE,
)

_QUERY_STATE_DELTA_HINT_RE = re.compile(
    r"\b(measure|measures|metric|metrics|amount|value|display|show\s+me\s+the\s+measure|"
    r"show\s+the\s+measure|as\s+amount|in\s+amount)\b",
    re.IGNORECASE,
)


def _previous_axis_context(history: list[dict] | None) -> dict[str, object]:
    if not history:
        return {}
    previous = history[-1] or {}
    sources = previous.get("data_sources")
    if isinstance(sources, list) and sources:
        for source in sources:
            if not isinstance(source, dict):
                continue
            preview = source.get("structured_preview") or {}
            if isinstance(preview, dict):
                return {
                    "cube": source.get("cube") or previous.get("chosen_cube") or "",
                    "generated_mdx": source.get("generated_mdx") or "",
                    "row_dimensions": preview.get("row_dimensions") or [],
                    "column_dimensions": preview.get("column_dimensions") or [],
                    "filters": preview.get("filters") or [],
                    "columns": preview.get("columns") or [],
                    "measure_dimension": preview.get("measure_dimension") or "",
                }
    return {}


def _shape(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _state_dimensions(previous_axis: dict[str, object]) -> list[str]:
    dims: list[str] = []
    dims.extend(str(d) for d in previous_axis.get("row_dimensions") or [])
    dims.extend(str(d) for d in previous_axis.get("column_dimensions") or [])
    if previous_axis.get("measure_dimension"):
        dims.append(str(previous_axis.get("measure_dimension")))
    filters = previous_axis.get("filters") or []
    if isinstance(filters, list):
        dims.extend(
            str(item.get("dimension", ""))
            for item in filters
            if isinstance(item, dict)
        )
    return list(dict.fromkeys(d for d in dims if d))


def _mentions_previous_state_dimension(question: str, previous_axis: dict[str, object]) -> bool:
    question_shape = _shape(question)
    if not question_shape:
        return False
    for dim in _state_dimensions(previous_axis):
        dim_shape = _shape(dim)
        if dim_shape and dim_shape in question_shape:
            return True
    return False


def _baseline_state_directive(previous_axis: dict[str, object]) -> str:
    previous_rows = ", ".join(str(d) for d in previous_axis.get("row_dimensions") or []) or "the previous rows"
    previous_columns = ", ".join(str(d) for d in previous_axis.get("column_dimensions") or []) or "the previous columns"
    measure_dimension = str(previous_axis.get("measure_dimension") or "").strip()
    visible_columns = ", ".join(str(c) for c in previous_axis.get("columns") or [])
    filters = previous_axis.get("filters") or []
    filter_text = ""
    if isinstance(filters, list) and filters:
        filter_text = " Previous filters: " + ", ".join(
            f"{item.get('dimension')}={item.get('element')}"
            for item in filters[:16]
            if isinstance(item, dict)
        ) + "."
    previous_mdx = str(previous_axis.get("generated_mdx") or "").strip()
    mdx_text = f" Previous MDX for exact baseline reference: {previous_mdx}" if previous_mdx else ""
    measure_text = ""
    if measure_dimension or visible_columns:
        measure_text = (
            f" Previous measure dimension: {measure_dimension or 'unknown'}."
            f" Previous visible measure/column elements: {visible_columns or 'none'}."
        )
    return (
        "Follow-up query-state instruction: treat the current user message as a "
        "delta to the previous query state, not as a brand-new query. Preserve "
        f"previous ROWS axis ({previous_rows}), previous COLUMNS axis "
        f"({previous_columns}), and previous filters unless the current message "
        f"explicitly changes one of them.{filter_text}{measure_text}{mdx_text}"
    )


def _looks_like_layout_followup(question: str) -> bool:
    token_count = len(re.findall(r"[A-Za-z0-9]+", question or ""))
    return bool(token_count <= 8 and _LAYOUT_FOLLOWUP_HINT_RE.search(question or ""))


def _classify_layout_followup(question: str, previous_axis: dict[str, object]) -> dict[str, object]:
    prompt = f"""You classify a short follow-up question for a TM1 / Planning Analytics table.

Current follow-up:
{question}

Previous table layout:
{_json.dumps(previous_axis, ensure_ascii=False, indent=2)}

Decide whether the follow-up changes rows, columns, both, or is ambiguous.

Rules:
- Do not execute MDX and do not invent element names.
- A row breakdown means the requested dimension/member should be on ROWS.
- A comparison, variance, vs, scenario comparison, year comparison, or delta usually belongs on COLUMNS.
- If the user asks for a table "by X" but the business subject is missing and previous layout is insufficient, mark needs_clarification.
- Preserve axes not explicitly changed.

Reply ONLY with JSON:
{{
  "intent": "modify_layout" | "new_query" | "needs_clarification",
  "row_action": "keep" | "replace" | "augment",
  "column_action": "keep" | "replace" | "augment",
  "row_target": "<requested row dimension/member or empty>",
  "column_target": "<requested column dimension/member/measure or empty>",
  "clarification": "<short question if needed, else empty>",
  "reason": "<short rationale>"
}}"""
    raw = complete_text(prompt, max_tokens=420, temperature=0)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("layout classifier returned non-object JSON")
    return parsed


def _layout_directive_from_intent(intent: dict[str, object], previous_axis: dict[str, object]) -> str:
    intent_type = str(intent.get("intent") or "").strip()
    if intent_type == "new_query":
        return ""
    if intent_type == "needs_clarification":
        clarification = str(intent.get("clarification") or "").strip()
        if not clarification:
            clarification = "Ask the user which rows and columns they want before generating MDX."
        return f"Follow-up layout instruction: the requested layout is ambiguous. {clarification}"
    if intent_type != "modify_layout":
        return ""

    previous_rows = ", ".join(str(d) for d in previous_axis.get("row_dimensions") or []) or "the previous rows"
    previous_columns = ", ".join(str(d) for d in previous_axis.get("column_dimensions") or []) or "the previous columns"
    filters = previous_axis.get("filters") or []
    filter_text = ""
    if isinstance(filters, list) and filters:
        filter_text = "; keep previous filters unless the new question contradicts them: " + ", ".join(
            f"{item.get('dimension')}={item.get('element')}"
            for item in filters[:12]
            if isinstance(item, dict)
        )

    row_action = str(intent.get("row_action") or "keep").strip()
    column_action = str(intent.get("column_action") or "keep").strip()
    row_target = str(intent.get("row_target") or "").strip()
    column_target = str(intent.get("column_target") or "").strip()
    reason = str(intent.get("reason") or "").strip()
    previous_mdx = str(previous_axis.get("generated_mdx") or "").strip()

    parts = [
        "Follow-up layout instruction: use the previous table as the baseline, not a brand-new table.",
        f"Previous ROWS axis: {previous_rows}.",
        f"Previous COLUMNS axis: {previous_columns}.",
        f"ROWS action: {row_action}" + (f" -> {row_target}" if row_target else "") + ".",
        f"COLUMNS action: {column_action}" + (f" -> {column_target}" if column_target else "") + ".",
    ]
    if row_action == "keep":
        parts.append("Keep the previous ROWS axis unchanged.")
    if column_action == "keep":
        parts.append("Keep the previous COLUMNS axis unchanged.")
    if row_target and column_action == "keep":
        parts.append(f"Do not put '{row_target}' on COLUMNS.")
    if column_target and row_action == "keep":
        parts.append(f"Do not put '{column_target}' on ROWS.")
    if filter_text:
        parts.append(filter_text.lstrip("; "))
    if previous_mdx:
        parts.append(f"Previous MDX for axis reference: {previous_mdx}")
    if reason:
        parts.append(f"Classifier rationale: {reason}")
    return " ".join(parts)


def _axis_followup_directive(question: str, history: list[dict] | None) -> str:
    if not _looks_like_layout_followup(question):
        return ""
    previous_axis = _previous_axis_context(history)
    if not previous_axis:
        return ""
    try:
        intent = _classify_layout_followup(question, previous_axis)
        return _layout_directive_from_intent(intent, previous_axis)
    except Exception as exc:
        _log.debug("layout follow-up classifier failed: %s", exc)
        return ""


def effective_question_from_history(question: str, history: list[dict] | None) -> str:
    """Expand a follow-up question with prior context and any detected layout directive."""
    if not history:
        return question
    text = question.strip()
    lowered = text.lower()
    axis_directive = _axis_followup_directive(text, history)
    if axis_directive:
        previous = str((history[-1] or {}).get("question", "")).strip()
        return " ".join(part for part in (previous, text, axis_directive) if part)
    previous_axis = _previous_axis_context(history)
    state_followup = bool(previous_axis) and (
        bool(_CORRECTION_FOLLOWUP_RE.search(text))
        or bool(_QUERY_STATE_DELTA_HINT_RE.search(text))
        or _mentions_previous_state_dimension(text, previous_axis)
    )
    if state_followup:
        previous = str((history[-1] or {}).get("question", "")).strip()
        directive = _baseline_state_directive(previous_axis)
        return " ".join(part for part in (previous, text, directive) if part)
    token_count = len(re.findall(r"[A-Za-z0-9]+", text))
    followup = (
        token_count <= 5
        or bool(_CORRECTION_FOLLOWUP_RE.search(text))
        or any(term in lowered for term in (
            "same", "this", "that", "those", "it",
            "show by", "by month", "by quarter",
            "by year", "add ", "what about", "compare with",
        ))
    )
    if not followup:
        return question
    previous = str((history[-1] or {}).get("question", "")).strip()
    return " ".join(part for part in (previous, question) if part)
