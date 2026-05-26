"""Top-level TM1 financial analysis agentic loop.

Replaces the old two-stage pipeline (cube-selection LLM call + per-cube MDX
agent) with a single self-directed loop. The agent:

  1. Calls rag_search first (mandatory).
  2. Decides to reuse a cached MDX or explore.
  3. Calls list_model_cubes → get_cube_schema to discover the right cube.
  4. Resolves element names with search_elements / get_dimension_members / etc.
  5. Calls execute_mdx; stops as soon as row_count > 0.

run_agent is a GENERATOR — it yields ('event', dict) tuples while a tool
is being dispatched so the SSE layer can show live progress to the user,
and finally yields one ('result', AgentResult) tuple before returning.

Special tools handled directly by the loop (not dispatched through registry):
  - get_cube_schema  — updates internal cube_schema state; sends compact view to LLM
  - execute_mdx      — normalises MDX, runs execute_once, checks for success
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ...config import MDX_MAX_TOKENS, get_llm_temperature
from ...tm1.service import get_cube_schema as _tm1_get_cube_schema
from ..mdx.context import MdxContext
from ..mdx.directives import (
    consolidated_member_directive,
    grounded_members_section,
    schema_dimensions_for_prompt,
)
from ..mdx.normalize import normalize_mdx
from ..providers import call_with_tools
from ..schema.dim_roles import get_dim_role
from ..tools.element_search import resolve_members
from .prompt import build_system_prompt
from .tools import AGENT_REGISTRY, ALL_AGENT_TOOLS

_log = logging.getLogger(__name__)

_MAX_AGENT_STEPS = 25


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class AgentStep:
    tool: str
    args: dict
    result_summary: str


@dataclass
class AgentResult:
    rows: list[dict]
    layout: dict
    mdx: str
    cube: str
    cube_schema: dict
    reasoning: str
    steps: list[AgentStep] = field(default_factory=list)
    clarification: str | None = None
    error: str | None = None


# ── Compact schema + per-cube context for LLM messages ────────────────────────

def _compact_schema(
    full_schema: dict,
    question: str,
    model_profile: dict | None,
) -> dict:
    """Return token-efficient schema view PLUS:
      - per-dimension `role` tag (currency_view, entity_subject, line_item, ...)
        derived from dim_roles map + content classification — no element names
        are hardcoded here.
      - grounded_members: candidates resolved from the question's literal terms
        (e.g., "SLIM-HK" → Company.SLIM-HK), role-tagged so the agent can
        respect accepted_for_entity_mentions.
      - consolidated_member_directive: when the user named a consolidated
        element by its literal name, use DRILLDOWNLEVEL instead of full
        Descendants (avoids blowing up the result).
    """
    profile_roles = (model_profile or {}).get("dim_roles") or {}
    dims_raw = full_schema.get("dimensions", []) or []

    cube_dim_names = [str(d.get("name", "")) for d in dims_raw if d.get("name")]
    try:
        grounded = resolve_members(question, cube_dim_names)
    except Exception as exc:
        _log.debug("[agent] resolve_members failed: %s", exc)
        grounded = []

    dims = schema_dimensions_for_prompt(dims_raw, grounded_members=grounded)
    # Attach role to each dim so the LLM can apply role-based rules
    # (e.g., currency_view → look up Entity Currency element via attributes).
    for dim_view, dim_raw in zip(dims, dims_raw):
        try:
            dim_view["role"] = get_dim_role(dim_raw, profile_roles)
        except Exception:
            dim_view["role"] = "unclassified"

    notes: list[str] = []
    try:
        consolidated_note = consolidated_member_directive(
            question, grounded, full_schema
        ).strip()
        if consolidated_note:
            notes.append(consolidated_note)
    except Exception as exc:
        _log.debug("[agent] consolidated_member_directive failed: %s", exc)

    try:
        grounded_note = grounded_members_section(
            grounded, full_schema, model_profile
        ).strip()
        if grounded_note:
            notes.append(grounded_note)
    except Exception as exc:
        _log.debug("[agent] grounded_members_section failed: %s", exc)

    result: dict = {"cube": full_schema.get("cube", ""), "dimensions": dims}
    if notes:
        result["question_specific_notes"] = notes
    return result


# ── Progress event helpers ────────────────────────────────────────────────────

def _short_args(tool: str, args: dict) -> str:
    """One-line summary of tool args for progress display."""
    if tool == "rag_search":
        return str(args.get("query", ""))[:80]
    if tool == "list_model_cubes":
        ft = args.get("filter_term", "")
        return f"filter={ft}" if ft else "(all)"
    if tool == "get_cube_schema":
        return str(args.get("cube", ""))
    if tool in ("search_elements", "get_dimension_members", "get_children", "get_element_attributes"):
        dim = args.get("dimension", "")
        term = args.get("term") or args.get("element") or ""
        return f"{dim}" + (f" / {term}" if term else "")
    if tool == "get_cube_summary":
        return str(args.get("cube", "") or "(current)")
    if tool == "list_views_from_cube":
        return str(args.get("cube", "") or "(current)")
    if tool == "get_mdx_from_view":
        return str(args.get("view", ""))
    if tool == "validate_mdx":
        return str(args.get("mdx", ""))[:60]
    if tool == "execute_mdx":
        return str(args.get("mdx", ""))[:80]
    return ""


def _event(phase: str, tool: str, args: dict, summary: str = "") -> tuple[str, dict]:
    """Build a progress event tuple."""
    return ("event", {
        "phase": phase,
        "tool": tool,
        "args_summary": _short_args(tool, args),
        "result_summary": summary,
    })


# ── Main agent loop (generator) ───────────────────────────────────────────────

def run_agent(
    question: str,
    history: list[dict] | None,
    model_profile: dict | None,
    selected_cubes: list[str] | None = None,
    max_steps: int = _MAX_AGENT_STEPS,
) -> Iterator[tuple[str, Any]]:
    """Drive the analysis agent, yielding progress events while running.

    Yields:
      ("event",  {phase, tool, args_summary, result_summary})  — many of these
      ("result", AgentResult)                                  — exactly one
    """
    from ...services.mdx_execution import execute_once

    system_prompt = build_system_prompt(model_profile, selected_cubes, history)
    messages: list[dict] = [
        {"role": "user", "content": f'{system_prompt}\n\nUser question: "{question}"'},
    ]

    steps: list[AgentStep] = []
    current_cube_schema: dict = {}

    for step_num in range(max_steps):
        text, tool_calls = call_with_tools(
            messages,
            ALL_AGENT_TOOLS,
            max_tokens=MDX_MAX_TOKENS,
            temperature=get_llm_temperature("mdx_temperature"),
        )

        if text:
            _log.info("[agent] stopped at step %d: %s", step_num, text[:120])
            yield ("result", AgentResult(
                rows=[], layout={}, mdx="",
                cube=current_cube_schema.get("cube", ""),
                cube_schema=current_cube_schema,
                reasoning="agent stopped",
                steps=steps,
                clarification=text,
            ))
            return

        if step_num >= max_steps - 1:
            break

        # Preserve extended thinking content so providers can relay it.
        raw_content = tool_calls[0].pop("_raw_content", None) if tool_calls else None
        reasoning_content = tool_calls[0].pop("_reasoning_content", None) if tool_calls else None
        msg: dict = {"role": "assistant", "tool_calls": tool_calls}
        if raw_content:
            msg["_raw_content"] = raw_content
        if reasoning_content:
            msg["_reasoning_content"] = reasoning_content
        messages.append(msg)

        for tc in tool_calls:
            tool_name: str = tc["name"]
            tool_args: dict = tc["args"]
            tool_result: str

            yield _event("start", tool_name, tool_args)

            # ── Special: get_cube_schema ──────────────────────────────────────
            if tool_name == "get_cube_schema":
                cube_name = str(tool_args.get("cube", "")).strip()
                try:
                    full_schema = _tm1_get_cube_schema(cube_name)
                    current_cube_schema = full_schema
                    compact = _compact_schema(full_schema, question, model_profile)
                    tool_result = json.dumps(compact, ensure_ascii=False)
                    _log.info(
                        "[agent] loaded schema for %s (%d dims, %d notes)",
                        cube_name,
                        len(full_schema.get("dimensions", [])),
                        len(compact.get("question_specific_notes", [])),
                    )
                except Exception as exc:
                    tool_result = json.dumps({"error": f"Could not load schema: {exc}"})
                    _log.warning("[agent] get_cube_schema failed for %r: %s", cube_name, exc)

                steps.append(AgentStep(tool_name, tool_args, tool_result[:120]))

            # ── Special: execute_mdx ──────────────────────────────────────────
            elif tool_name == "execute_mdx":
                raw_mdx = str(tool_args.get("mdx", "")).strip()

                try:
                    clean_mdx = normalize_mdx(raw_mdx, question, model_profile)
                except Exception as exc:
                    tool_result = json.dumps({"success": False, "error": f"MDX normalisation: {exc}"})
                    steps.append(AgentStep(tool_name, tool_args, tool_result[:120]))
                    yield _event("end", tool_name, tool_args, tool_result[:120])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tool_name,
                        "content": tool_result,
                    })
                    continue

                ctx = MdxContext(
                    question=question,
                    cube_schema=current_cube_schema,
                    history=history,
                    model_profile=model_profile,
                )
                rows, layout, warnings, error = execute_once(ctx, clean_mdx)

                if error is None and rows:
                    cube = current_cube_schema.get("cube", "")
                    _log.info(
                        "[agent] success step=%d cube=%s rows=%d",
                        step_num, cube, len(rows),
                    )
                    success_summary = f"success rows={len(rows)}"
                    steps.append(AgentStep(tool_name, tool_args, success_summary))
                    # Note: we no longer auto-save to RAG here. The frontend
                    # exposes 👍 (save) and 👎 (forget) buttons so only
                    # user-confirmed-good queries enter the knowledge base.
                    yield _event("end", tool_name, tool_args, success_summary)
                    yield ("result", AgentResult(
                        rows=rows,
                        layout=layout,
                        mdx=clean_mdx,
                        cube=cube,
                        cube_schema=current_cube_schema,
                        reasoning=f"Found data in {cube}",
                        steps=steps,
                    ))
                    return

                feedback: dict = {"success": error is None, "row_count": len(rows)}
                if error:
                    feedback["error"] = error
                if warnings:
                    feedback["warnings"] = warnings
                if error is None and not rows:
                    feedback["hint"] = (
                        "Query returned 0 rows. Verify element names with "
                        "search_elements, expand to a parent consolidation, "
                        "or remove a restrictive filter and retry."
                    )
                tool_result = json.dumps(feedback)
                steps.append(AgentStep(tool_name, tool_args, tool_result[:120]))
                _log.debug("[agent] execute_mdx step=%d: %s", step_num, tool_result[:200])

            # ── All other tools — dispatch through registry ───────────────────
            else:
                tool_result = AGENT_REGISTRY.execute(tool_name, tool_args, current_cube_schema)
                steps.append(AgentStep(tool_name, tool_args, tool_result[:120]))
                _log.debug("[agent] %s step=%d: %s", tool_name, step_num, tool_result[:120])

            yield _event("end", tool_name, tool_args, tool_result[:120])

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tool_name,
                "content": tool_result,
            })

    _log.warning("[agent] exhausted %d steps — synthesising clarification", max_steps)
    clarification = _synthesise_exhaustion_clarification(messages, question, steps)
    yield ("result", AgentResult(
        rows=[], layout={}, mdx="",
        cube=current_cube_schema.get("cube", ""),
        cube_schema=current_cube_schema,
        reasoning="exhausted exploration budget",
        steps=steps,
        clarification=clarification,
    ))


def _synthesise_exhaustion_clarification(
    messages: list[dict],
    question: str,
    steps: list[AgentStep],
) -> str:
    """When the agent runs out of steps without finding data, ask the LLM to
    summarise what it tried and propose a refinement to the user — in plain
    text only, no tool calls."""
    # Compact summary of what was attempted, to ground the closing message.
    attempts = []
    for s in steps[-12:]:
        args_str = ", ".join(f"{k}={str(v)[:40]}" for k, v in (s.args or {}).items())
        attempts.append(f"- {s.tool}({args_str}) → {s.result_summary[:120]}")
    attempts_block = "\n".join(attempts) or "(no tool calls recorded)"

    closing_prompt = (
        f'The user asked: "{question}"\n\n'
        f"You ({{agent}}) tried these tool calls without producing usable data:\n"
        f"{attempts_block}\n\n"
        "Write a short user-facing message (2–4 sentences, markdown allowed) that:\n"
        "1. Acknowledges briefly what you tried (1 sentence).\n"
        "2. States what was NOT available in the data (e.g., missing concept, "
        "wrong year, no matching entity).\n"
        "3. Offers a concrete alternative as a question — name a specific "
        "year, scenario, statement type, cube, or entity that the user could "
        "try instead. Be specific based on what the tool calls revealed.\n"
        "Do not call any tools. Reply with prose only."
    )

    # Reuse the same provider but with no tools to force a text response.
    closing_messages = messages + [{"role": "user", "content": closing_prompt}]
    try:
        text, _ = call_with_tools(
            closing_messages,
            tools=[],
            max_tokens=400,
            temperature=0.3,
        )
        if text and text.strip():
            return text.strip()
    except Exception as exc:
        _log.debug("[agent] exhaustion clarification failed: %s", exc)

    # Last-resort deterministic fallback so the user always sees something useful.
    return (
        "I couldn't find data matching your question after exploring the "
        "available cubes. Could you refine the request — for example by "
        "naming a specific cube, year, scenario, or a different statement "
        "type (P&L instead of balance sheet, etc.)?"
    )
