"""Streaming SSE pipeline for /api/analyze.

The top-level `analyze_sse_gen` is intentionally short: it walks the user
through clarification, cube selection, per-cube data fetching, and analysis
streaming. Each stage lives in its own function so the control flow reads
top-to-bottom.
"""

import json as _json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from anyio import from_thread
from fastapi import Request

from ..ai.intent.attribute_intent import detect_attribute_intent
from ..ai.intent.clarification import find_clarifications, find_schema_clarification
from ..ai.intent.cube_selection import select_cubes_with_profile
from ..ai.intent.layout_followup import effective_question_from_history
from ..ai.intent.preflight import is_unclear_question
from ..ai.intent.query_intent import detect_query_intent, prepare_schema_for_query
from ..ai.mdx.agent import run_mdx_agent
from ..ai.mdx.context import MdxContext
from ..ai.mdx.planner import MdxPlan, SURFACE_THRESHOLD as PLAN_SURFACE_THRESHOLD, try_plan_mdx
from ..ai.output.narrative import (
    parse_suggestions as _parse_suggestions,
    stream_financial_analysis,
)
from ..ai.tools.element_search import find_element_candidates, resolve_members
from ..ai.tools.execution import run_mdx
from ..ai.tools.preview import build_cube_preview
from ..ai.tools.rag_tools import get_similar_queries, record_query
from ..config import (
    AI_MAX_COLUMNS,
    AI_MAX_ROWS,
    CUBE_SELECT_LIMIT_AUTO,
    CUBE_SELECT_LIMIT_MANUAL,
    PREVIEW_ROW_LIMIT,
)
from ..response_messages import no_usable_data_message
from ..schemas import QuestionRequest
from ..tm1.service import (
    get_cube_schema,
    get_cubes_with_descriptions,
)
from .model_profile import cube_context_for_selection, load_current_model_profile

_log = logging.getLogger(__name__)


# ── Request-scoped schema cache ──────────────────────────────────────────────

class _RequestSchemaCache:
    """Memoize get_cube_schema() for the duration of one request.

    cube_context_for_selection() and the per-cube loop both call get_cube_schema
    for every selected cube; for 30-cube models that's 60+ duplicate SQLite
    multi-table reads per /api/analyze. This cache dedupes them.
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}

    def get(self, cube: str) -> dict:
        if cube not in self._cache:
            self._cache[cube] = get_cube_schema(cube)
        return self._cache[cube]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class _CubeResult:
    cube: str
    reasoning: str
    rows: list[dict[str, object]]
    layout: dict[str, object]
    mdx: str
    mdx_attempts: list[str]
    full_preview: dict
    limited_preview: dict
    effective_row_count: int
    plan: MdxPlan | None

    def to_source_dict(self) -> dict:
        plan_dict = None
        if self.plan is not None and self.plan.confidence < PLAN_SURFACE_THRESHOLD:
            plan_dict = self.plan.to_dict()
        return {
            "cube": self.cube,
            "reasoning": self.reasoning,
            "data_row_count": self.effective_row_count,
            "data_preview": self.rows[:PREVIEW_ROW_LIMIT],
            "structured_preview": self.limited_preview,
            "applied_filters": self.layout.get("applied_filters", []),
            "generated_mdx": self.mdx,
            "mdx_attempts": self.mdx_attempts,
            "analysis_rows": self.rows,
            "plan": plan_dict,
            "_full_preview": self.full_preview,
        }


# ── Small helpers ────────────────────────────────────────────────────────────

@dataclass
class _CubeSelectionOutcome:
    selected_cubes: list[dict]
    reasoning: str = ""
    user_scoped: bool = False
    error: str | None = None
    clarification: str | None = None


def _source_for_ai(s: dict) -> dict:
    fp: dict = s.get("_full_preview") or s.get("structured_preview") or {}
    columns = list(fp.get("columns", []) or [])[:AI_MAX_COLUMNS]
    rows = list(fp.get("rows", []) or [])[:AI_MAX_ROWS]
    return {
        "cube":            s["cube"],
        "reasoning":       s.get("reasoning", ""),
        "data_row_count":  s["data_row_count"],
        "applied_filters": s.get("applied_filters", []),
        "data":            {**fp, "columns": columns, "rows": rows},
    }


def _analysis_fallback_message(error: str) -> str:
    text = error.lower()
    if "rate_limit" in text or "rate limit" in text or "tokens per minute" in text:
        return (
            "I found matching TM1 data, but the AI narrative step hit the LLM "
            "rate limit. The table and export are still available above; retry "
            "after a short pause or narrow the query to fewer rows/columns."
        )
    return (
        "I found matching TM1 data, but the AI narrative step failed. The table "
        "and export are still available above."
    )



def _client_disconnected(request: Request | None) -> bool:
    if request is None:
        return False
    try:
        return bool(from_thread.run(request.is_disconnected))
    except RuntimeError:
        return False


# ── Stage 1: clarification ───────────────────────────────────────────────────

def _early_clarification(question: str, history: list[dict], model_profile: dict | None) -> str | None:
    if not history and is_unclear_question(question):
        return (
            "Looks like this may be a test message or an incomplete question. "
            "Ask me something specific about your TM1 data, such as **labor cost trends**, "
            "**headcount movement**, **salary variance**, **actuals vs budget**, "
            "or a specific cube, cost center, month, or scenario."
        )
    return find_clarifications(question, history, model_profile)


def _forced_attribute_intent(
    question: str, history: list[dict], cache: _RequestSchemaCache
) -> dict[str, str]:
    """Detect when a follow-up wants an attribute display from the previous cube."""
    if not history:
        return {}
    prev_cube = str(history[-1].get("chosen_cube", "")).strip()
    if not prev_cube:
        return {}
    try:
        prev_schema = cache.get(prev_cube)
        dim_attrs: dict[str, list[str]] = {
            d["name"]: [
                a["name"] for a in d.get("attributes", [])
                if a.get("type") in ("Alias", "String")
            ]
            for d in prev_schema.get("dimensions", [])
            if not d.get("is_measure") and d.get("attributes")
        }
        if dim_attrs:
            intent = detect_attribute_intent(question, history, dim_attrs)
            if intent:
                return {intent["dim_name"]: intent["attr_name"]}
    except Exception as exc:
        _log.debug("forced attribute intent detection failed: %s", exc)
    return {}


# ── Stage 2: cube selection ──────────────────────────────────────────────────

def _select_cubes_stage(
    question: str,
    req: QuestionRequest,
    model_profile: dict | None,
) -> _CubeSelectionOutcome:
    """Select candidate cubes, or return a typed clarification/error outcome."""
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        return _CubeSelectionOutcome([], error=str(exc))

    user_scoped = bool(req.selected_cubes)
    if user_scoped:
        wanted = {c.strip().lower() for c in req.selected_cubes if c.strip()}
        manual_cubes = [c for c in cubes if str(c.get("cube", "")).lower() in wanted]
        if not manual_cubes:
            return _CubeSelectionOutcome(
                [],
                user_scoped=True,
                error="Selected cubes are no longer available in the connected TM1 model.",
            )
        selected_cubes = [
            {"cube": c["cube"], "reasoning": "User-selected scope"}
            for c in manual_cubes
        ]
        reasoning = f"User restricted scope to {len(selected_cubes)} cube(s)"
        return _CubeSelectionOutcome(selected_cubes, reasoning=reasoning, user_scoped=True)

    cube_context = cube_context_for_selection(cubes, model_profile=model_profile)
    schema_clarification = find_schema_clarification(
        question, cube_context, req.history, model_profile=model_profile,
    )
    if schema_clarification:
        return _CubeSelectionOutcome([], clarification=schema_clarification)

    try:
        selection = select_cubes_with_profile(
            question, cube_context, req.history, model_profile=model_profile,
        )
    except RuntimeError as exc:
        return _CubeSelectionOutcome([], error=str(exc))

    selected_cubes = selection.get("cubes") or []
    reasoning = selection.get("reasoning", "")
    if not selected_cubes:
        return _CubeSelectionOutcome([], error="AI did not return a valid cube selection")
    return _CubeSelectionOutcome(selected_cubes, reasoning=reasoning)


# ── Stage 3: process one cube ────────────────────────────────────────────────

def _try_planner_fast_path(
    question: str,
    focused_schema: dict,
    cube_dims: list[str],
    model_profile: dict | None,
) -> tuple[MdxPlan | None, str | None]:
    """Run the rule-based planner. Returns (plan, hint_mdx).

    - plan is non-None when confidence >= PLAN_SURFACE_THRESHOLD (fast path).
    - hint_mdx is non-None when MIN_CONFIDENCE <= confidence < PLAN_SURFACE_THRESHOLD
      (low-confidence plan passed as a hint to the agent).
    """
    if "Follow-up layout instruction:" in question:
        return None, None
    try:
        element_matches = find_element_candidates(question, cube_dims)
        plan = try_plan_mdx(
            question, focused_schema,
            model_profile=model_profile,
            element_matches=element_matches,
        )
    except Exception as exc:
        _log.warning("[mdx-planner] error: %s", exc)
        return None, None

    if plan is None:
        return None, None
    if plan.confidence >= PLAN_SURFACE_THRESHOLD:
        _log.info("[mdx-planner] high-conf pattern=%s conf=%.2f", plan.pattern, plan.confidence)
        return plan, None
    # Low confidence — pass MDX as a hint to the agent
    _log.info("[mdx-planner] low-conf pattern=%s conf=%.2f — passing hint to agent", plan.pattern, plan.confidence)
    return None, plan.mdx


def _process_cube(
    selected: dict,
    effective_question: str,
    req: QuestionRequest,
    model_profile: dict | None,
    forced_apply_attributes: dict[str, str],
    schema_cache: _RequestSchemaCache,
) -> _CubeResult | dict:
    """Return _CubeResult on success or a skipped-source dict on failure."""
    cube = str(selected.get("cube", "")).strip()
    source_reasoning = str(selected.get("reasoning", "")).strip()

    try:
        schema = schema_cache.get(cube)
    except RuntimeError as exc:
        return {"cube": cube, "reasoning": source_reasoning, "status": f"schema error: {exc}"}

    similar = get_similar_queries(effective_question, cube)
    intent = detect_query_intent(effective_question)
    focused_schema = prepare_schema_for_query(
        schema, effective_question, intent=intent, model_profile=model_profile,
    )
    _log.info(
        "[query-intent] %s primary=%s sec=%s conf=%.2f",
        cube, intent["primary"], intent["secondaries"], intent["confidence"],
    )
    cube_dims = [
        str(d.get("name", "")) for d in schema.get("dimensions", []) if d.get("name")
    ]
    grounded_members = resolve_members(effective_question, cube_dims)
    if grounded_members:
        _log.info("[member-grounding] %s candidates=%d", cube, len(grounded_members))

    # ctx uses full schema so execute_once validation has all element names.
    ctx = MdxContext(
        question=effective_question,
        cube_schema=schema,
        history=req.history,
        model_profile=model_profile,
        grounded_members=grounded_members,
        similar_queries=similar,
    )

    # ── Fast path: high-confidence rule planner (no LLM needed) ──────────────
    plan, plan_hint = _try_planner_fast_path(
        effective_question, focused_schema, cube_dims, model_profile
    )
    if plan is not None:
        try:
            rows, layout, plan_mdx, mdx_attempts = run_mdx(ctx, plan.mdx)
            if rows:
                preview = build_cube_preview(rows, layout, forced_apply_attributes or None)
                record_query(
                    effective_question, cube, plan_mdx,
                    row_count=preview.effective_row_count,
                    grounded_members=grounded_members,
                )
                return _CubeResult(
                    cube=cube, reasoning=source_reasoning,
                    rows=rows, layout=layout,
                    mdx=plan_mdx, mdx_attempts=mdx_attempts,
                    full_preview=preview.full, limited_preview=preview.limited,
                    effective_row_count=preview.effective_row_count,
                    plan=plan,
                )
            _log.info("[mdx-planner] %s returned 0 rows — falling to agent", cube)
        except RuntimeError as exc:
            if "connection" in str(exc).lower() or "timeout" in str(exc).lower():
                return {"cube": cube, "reasoning": source_reasoning, "status": f"error: {exc}"}
            _log.info("[mdx-planner] %s failed: %s — falling to agent", cube, exc)
        plan = None  # planner didn't work; reset so _CubeResult gets plan=None

    # ── Agentic loop: LLM searches elements, writes MDX, executes, retries ───
    agent_result = run_mdx_agent(ctx, plan_hint=plan_hint)

    if agent_result.error or not agent_result.rows:
        status = agent_result.error or "no data"
        return {
            "cube": cube,
            "reasoning": source_reasoning,
            "status": status,
            "generated_mdx": agent_result.mdx,
            "mdx_attempts": [f"{s.tool}: {s.result_summary}" for s in agent_result.steps],
        }

    preview = build_cube_preview(
        agent_result.rows, agent_result.layout, forced_apply_attributes or None
    )
    record_query(
        effective_question, cube, agent_result.mdx,
        row_count=preview.effective_row_count,
        grounded_members=grounded_members,
    )
    return _CubeResult(
        cube=cube,
        reasoning=source_reasoning,
        rows=agent_result.rows,
        layout=agent_result.layout,
        mdx=agent_result.mdx,
        mdx_attempts=[f"{s.tool}: {s.result_summary}" for s in agent_result.steps],
        full_preview=preview.full,
        limited_preview=preview.limited,
        effective_row_count=preview.effective_row_count,
        plan=None,
    )


# ── Stage 4: stream analysis ─────────────────────────────────────────────────

def _stream_analysis(
    question: str,
    sources: list[dict],
    skipped_sources: list[dict],
    history: list[dict],
) -> Iterator[tuple[str, str]]:
    """Yield (chunk_kind, payload) pairs: ('chunk', text) per delta, then ('done', full_text)."""
    ai_sources = [_source_for_ai(s) for s in sources]
    full_text = ""
    try:
        for chunk in stream_financial_analysis(question, ai_sources, skipped_sources, history):
            full_text += chunk
            yield "chunk", chunk
    except RuntimeError as exc:
        full_text = _analysis_fallback_message(str(exc))
    yield "done", full_text


# ── Top-level generator ──────────────────────────────────────────────────────

def _evt(data: dict) -> str:
    return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"


def analyze_sse_gen(req: QuestionRequest, request: Request | None = None):
    """SSE generator for /api/analyze. Yields `data: {...}\\n\\n` strings."""
    question = req.question.strip()
    if _client_disconnected(request):
        return

    model_profile = load_current_model_profile()
    if _client_disconnected(request):
        return

    clarification = _early_clarification(question, req.history, model_profile)
    if clarification:
        yield _evt({"type": "done", "data": {
            "success": True, "type": "clarification",
            "question": question, "analysis": clarification,
        }})
        return

    schema_cache = _RequestSchemaCache()
    forced_apply_attributes = _forced_attribute_intent(question, req.history, schema_cache)

    yield _evt({"type": "step", "step": 1})
    cube_selection = _select_cubes_stage(
        question, req, model_profile,
    )
    if cube_selection.error is not None:
        yield _evt({"type": "error", "message": cube_selection.error})
        return
    if cube_selection.clarification is not None:
        yield _evt({"type": "done", "data": {
            "success": True, "type": "clarification",
            "question": question, "analysis": cube_selection.clarification,
        }})
        return

    if _client_disconnected(request):
        return
    yield _evt({"type": "step", "step": 2})

    effective_question = effective_question_from_history(question, req.history)
    selected_cubes = cube_selection.selected_cubes
    reasoning = cube_selection.reasoning
    user_scoped = cube_selection.user_scoped
    cube_limit = CUBE_SELECT_LIMIT_MANUAL if user_scoped else CUBE_SELECT_LIMIT_AUTO
    sources: list[_CubeResult] = []
    skipped_sources: list[dict[str, Any]] = []
    seen_cubes: set[str] = set()

    for selected in selected_cubes[:cube_limit]:
        if _client_disconnected(request):
            return
        cube = str(selected.get("cube", "")).strip()
        if not cube or cube in seen_cubes:
            continue
        seen_cubes.add(cube)

        outcome_obj = _process_cube(
            selected, effective_question, req, model_profile,
            forced_apply_attributes, schema_cache,
        )
        if isinstance(outcome_obj, _CubeResult):
            sources.append(outcome_obj)
        else:
            skipped_sources.append(outcome_obj)

    if not sources:
        message, detail = no_usable_data_message(skipped_sources)
        payload: dict[str, Any] = {
            "type": "error",
            "message": message,
            "detail": detail,
            "skipped": skipped_sources,
        }
        if user_scoped:
            payload["scope_filtered"] = True
            payload["scoped_cubes"] = [str(c.get("cube", "")) for c in selected_cubes]
        yield _evt(payload)
        return

    if _client_disconnected(request):
        return
    yield _evt({"type": "step", "step": 3})

    source_dicts = [s.to_source_dict() for s in sources]
    _internal = {"analysis_rows", "_full_preview"}
    response_sources = [{k: v for k, v in s.items() if k not in _internal} for s in source_dicts]
    yield _evt({
        "type": "sources",
        "sources": response_sources,
        "skipped": skipped_sources,
        "reasoning": reasoning,
    })

    full_text = ""
    for kind, payload in _stream_analysis(question, source_dicts, skipped_sources, req.history):
        if _client_disconnected(request):
            return
        if kind == "chunk":
            full_text += payload
            yield _evt({"type": "chunk", "text": payload})
        elif kind == "done":
            full_text = payload

    analysis, suggestions = _parse_suggestions(full_text)
    first = source_dicts[0]
    # Re-attach analysis_rows so the frontend can offer full CSV export.
    for rs, s in zip(response_sources, source_dicts):
        rs["analysis_rows"] = s["analysis_rows"]
    yield _evt({"type": "done", "data": {
        "success": True, "type": "analysis", "question": question,
        "chosen_cube": first["cube"],
        "reasoning": reasoning,
        "data_row_count": sum(s["data_row_count"] for s in source_dicts),
        "data_preview": first["data_preview"],
        "data_sources": response_sources,
        "skipped_sources": skipped_sources,
        "analysis": analysis,
        "suggestions": suggestions,
    }})
