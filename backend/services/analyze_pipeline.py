"""Streaming SSE pipeline for /api/analyze.

The top-level `analyze_sse_gen` is intentionally short: it walks the user
through clarification, cube selection, per-cube data fetching, and analysis
streaming. Each stage lives in its own function so the control flow reads
top-to-bottom.
"""

import json as _json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from anyio import from_thread
from fastapi import Request

from ..ai.agent import run_agent
from ..ai.intent.clarification import find_clarifications
from ..ai.intent.layout_followup import effective_question_from_history
from ..ai.intent.preflight import is_unclear_question
from ..ai.output.narrative import (
    parse_suggestions as _parse_suggestions,
    stream_financial_analysis,
)
from ..ai.tools.preview import build_cube_preview
from ..config import (
    AI_MAX_COLUMNS,
    AI_MAX_ROWS,
    PREVIEW_ROW_LIMIT,
)
from ..response_messages import no_usable_data_message
from ..schemas import QuestionRequest
from .model_profile import load_current_model_profile

_log = logging.getLogger(__name__)


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

    def to_source_dict(self) -> dict:
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
            "plan": None,
            "_full_preview": self.full_preview,
        }


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




# ── Stage 2: agentic loop ────────────────────────────────────────────────────
# (cube selection + MDX generation now handled by run_agent in ai/agent/loop.py)




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

    # Stage 1: Deterministic early clarification (year/scenario/unclear query).
    clarification = _early_clarification(question, req.history, model_profile)
    if clarification:
        yield _evt({"type": "done", "data": {
            "success": True, "type": "clarification",
            "question": question, "analysis": clarification,
        }})
        return

    effective_question = effective_question_from_history(question, req.history)

    yield _evt({"type": "step", "step": 1})

    # Stage 2: Agentic loop — discovers cube, resolves elements, generates MDX.
    # run_agent is a generator: yields ('event', dict) for live tool progress,
    # then exactly one ('result', AgentResult) before returning.
    agent_result = None
    for kind, payload in run_agent(
        effective_question,
        req.history,
        model_profile,
        selected_cubes=req.selected_cubes or None,
    ):
        if _client_disconnected(request):
            return
        if kind == "event":
            yield _evt({"type": "agent_step", **payload})
        elif kind == "result":
            agent_result = payload
            break

    if agent_result is None:
        yield _evt({"type": "error", "message": "Agent terminated without a result."})
        return

    if _client_disconnected(request):
        return

    if agent_result.clarification:
        yield _evt({"type": "done", "data": {
            "success": True, "type": "clarification",
            "question": question, "analysis": agent_result.clarification,
        }})
        return

    if agent_result.error or not agent_result.rows:
        message = agent_result.error or "No data found for this question."
        yield _evt({"type": "error", "message": message})
        return

    # Stage 3: Build structured preview from the agent's result.
    preview = build_cube_preview(agent_result.rows, agent_result.layout)

    cube_result = _CubeResult(
        cube=agent_result.cube,
        reasoning=agent_result.reasoning,
        rows=agent_result.rows,
        layout=agent_result.layout,
        mdx=agent_result.mdx,
        mdx_attempts=[f"{s.tool}: {s.result_summary}" for s in agent_result.steps],
        full_preview=preview.full,
        limited_preview=preview.limited,
        effective_row_count=preview.effective_row_count,
    )

    yield _evt({"type": "step", "step": 2})

    source_dicts = [cube_result.to_source_dict()]
    _internal = {"analysis_rows", "_full_preview"}
    response_sources = [{k: v for k, v in s.items() if k not in _internal} for s in source_dicts]
    yield _evt({
        "type": "sources",
        "sources": response_sources,
        "skipped": [],
        "reasoning": agent_result.reasoning,
    })

    # Stage 4: Stream narrative analysis.
    yield _evt({"type": "step", "step": 3})

    full_text = ""
    for kind, payload in _stream_analysis(question, source_dicts, [], req.history):
        if _client_disconnected(request):
            return
        if kind == "chunk":
            full_text += payload
            yield _evt({"type": "chunk", "text": payload})
        elif kind == "done":
            full_text = payload

    analysis, suggestions = _parse_suggestions(full_text)
    first = source_dicts[0]
    for rs, s in zip(response_sources, source_dicts):
        rs["analysis_rows"] = s["analysis_rows"]
    yield _evt({"type": "done", "data": {
        "success": True, "type": "analysis", "question": question,
        "chosen_cube": first["cube"],
        "reasoning": agent_result.reasoning,
        "data_row_count": cube_result.effective_row_count,
        "data_preview": first["data_preview"],
        "data_sources": response_sources,
        "skipped_sources": [],
        "analysis": analysis,
        "suggestions": suggestions,
    }})
