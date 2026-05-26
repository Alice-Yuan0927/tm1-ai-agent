"""Agentic MDX generation and execution loop.

Replaces the old two-step flow (generate MDX as text → execute separately).
The LLM has three tools and drives the whole loop itself:
  - search_elements / get_dimension_members  — look up element names
  - execute_mdx                              — run MDX and see the results

The loop ends when execute_mdx returns rows > 0, the LLM gives up with a
text response, or max_steps is exhausted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ...config import MDX_MAX_TOKENS, get_llm_temperature
from ..output.conversation import conversation_context
from ..prompts import MDX_HARD_RULES
from ..prompts.prompt_rules import GENERAL_AGENT_CONTRACT
from ..providers import call_with_tools
from ..tools.element_tools import ELEMENT_REGISTRY
from .context import MdxContext
from .directives import (
    consolidated_member_directive,
    grounded_members_section,
    line_item_directive,
    profile_defaults_directive,
    schema_dimensions_for_prompt,
)
from .generation import _examples_section, _merge_historical_grounded, _profile_section
from .normalize import normalize_mdx

_log = logging.getLogger(__name__)

_MAX_AGENT_STEPS = 10

_EXECUTE_MDX_TOOL: dict = {
    "name": "execute_mdx",
    "description": (
        "Execute a TM1 MDX SELECT statement against the cube and return the results. "
        "Always call this to verify your MDX returns data. "
        "If it returns 0 rows, check your element names and filters — "
        "use search_elements to find the exact spelling, then revise and retry. "
        "If it returns an error, read the message carefully — it often names the "
        "invalid member or dimension."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mdx": {
                "type": "string",
                "description": "A complete TM1 MDX SELECT statement to execute.",
            }
        },
        "required": ["mdx"],
    },
}

_AGENT_TOOLS: list[dict] = ELEMENT_REGISTRY.schemas() + [_EXECUTE_MDX_TOOL]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

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
    steps: list[AgentStep] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run_mdx_agent(
    ctx: MdxContext,
    *,
    max_steps: int = _MAX_AGENT_STEPS,
) -> AgentResult:
    """LLM-driven MDX loop: search → write → execute → check result → retry."""
    from ...services.mdx_execution import execute_once

    ctx = _merge_historical_grounded(ctx)
    cube_name = ctx.cube_name

    dims_json = json.dumps(
        schema_dimensions_for_prompt(
            ctx.cube_schema.get("dimensions", []),
            ctx.grounded_members,
        ),
        indent=2,
        ensure_ascii=False,
    )
    statement_directive = line_item_directive(ctx.question, ctx.cube_schema, ctx.model_profile)
    consolidated_directive = consolidated_member_directive(
        ctx.question, ctx.grounded_members, ctx.cube_schema
    )
    defaults_directive = profile_defaults_directive(
        ctx.cube_schema, ctx.model_profile, ctx.question
    )
    grounded_section = grounded_members_section(
        ctx.grounded_members, ctx.cube_schema, ctx.model_profile
    )
    examples_section = _examples_section(ctx.similar_queries)
    profile_section = _profile_section(ctx.model_profile)

    system_prompt = f"""You are an expert TM1 / IBM Planning Analytics MDX query writer.

{GENERAL_AGENT_CONTRACT}

Cube: {cube_name}

<schema>
Dimensions and their available elements:
{dims_json}
</schema>
{grounded_section}{statement_directive}{consolidated_directive}{defaults_directive}{examples_section}
{profile_section}
<conversation>
{conversation_context(ctx.history)}
</conversation>

<workflow>
0. For financial statement requests (P&L, balance sheet, cash flow, trial balance),
   call list_views_from_cube first. If a relevant view exists, call
   get_mdx_from_view and adapt that MDX's filters/axes instead of inventing the
   statement row structure from scratch.
1. If you need business context about the cube (what it tracks, typical uses,
   available measures), call get_cube_summary first.
2. If uncertain about element names, call search_elements or get_dimension_members.
   To understand a consolidation's children before using Descendants(), call get_children.
   To get aliases or descriptions for coded elements, call get_element_attributes.
3. Write an MDX SELECT statement and call execute_mdx to test it.
4. If execute_mdx returns 0 rows:
   - Call search_elements to verify the exact element name spelling.
   - Adjust the MDX filter or expand to a parent consolidation, then retry.
5. If execute_mdx returns an error:
   - Read the error message — it usually names the invalid member.
   - Call search_elements for that member, fix the MDX, then retry.
6. Once execute_mdx returns row_count > 0, stop immediately.
7. Only if you genuinely cannot write a valid MDX for this question, reply with
   a plain text explanation of why (no MDX, no tool call).
</workflow>

Hard rules for valid TM1 MDX:
1. FROM [{cube_name}] must come immediately after the axes - always BEFORE WHERE
{MDX_HARD_RULES}"""

    messages: list[dict] = [
        {
            "role": "user",
            "content": f'{system_prompt}\n\nUser question: "{ctx.question}"',
        }
    ]

    steps: list[AgentStep] = []

    for step_num in range(max_steps):
        text, tool_calls = call_with_tools(
            messages,
            _AGENT_TOOLS,
            max_tokens=MDX_MAX_TOKENS,
            temperature=get_llm_temperature("mdx_temperature"),
        )

        if text:
            # LLM gave up — returned explanation text instead of calling execute_mdx
            _log.warning(
                "[mdx-agent] %s gave up at step %d: %s",
                cube_name, step_num, text[:120],
            )
            return AgentResult(
                rows=[], layout={}, mdx="", steps=steps,
                error=f"agent gave up: {text[:300]}",
            )

        if step_num >= max_steps - 1:
            break

        raw_content = tool_calls[0].pop("_raw_content", None) if tool_calls else None
        reasoning_content = tool_calls[0].pop("_reasoning_content", None) if tool_calls else None
        msg: dict = {"role": "assistant", "tool_calls": tool_calls}
        if raw_content:
            msg["_raw_content"] = raw_content
        if reasoning_content:
            msg["_reasoning_content"] = reasoning_content
        messages.append(msg)

        for tc in tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]

            if tool_name == "execute_mdx":
                raw_mdx = str(tool_args.get("mdx", "")).strip()
                try:
                    clean_mdx = normalize_mdx(raw_mdx, ctx.question, ctx.model_profile)
                except Exception as exc:
                    tool_result = json.dumps({"success": False, "error": f"MDX syntax: {exc}"})
                    steps.append(AgentStep("execute_mdx", tool_args, tool_result[:120]))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tool_name,
                        "content": tool_result,
                    })
                    continue

                rows, layout, warnings, error = execute_once(ctx, clean_mdx)

                if error is None and rows:
                    _log.info(
                        "[mdx-agent] %s success step=%d rows=%d mdx=%s",
                        cube_name, step_num, len(rows), clean_mdx,
                    )
                    steps.append(AgentStep("execute_mdx", tool_args, f"success rows={len(rows)}"))
                    return AgentResult(rows=rows, layout=layout, mdx=clean_mdx, steps=steps)

                # Build feedback for the LLM
                feedback: dict = {"success": error is None, "row_count": len(rows)}
                if error:
                    feedback["error"] = error
                if warnings:
                    feedback["warnings"] = warnings
                if error is None and not rows:
                    feedback["hint"] = (
                        "Query returned 0 rows. Verify element names with search_elements, "
                        "then try expanding to a parent consolidation or removing a restrictive filter."
                    )
                tool_result = json.dumps(feedback)
                steps.append(AgentStep("execute_mdx", tool_args, tool_result[:120]))
                _log.debug("[mdx-agent] execute_mdx step=%d %s", step_num, tool_result[:200])

            else:
                tool_result = ELEMENT_REGISTRY.execute(tool_name, tool_args, ctx.cube_schema)
                steps.append(AgentStep(tool_name, tool_args, tool_result[:120]))
                _log.debug("[mdx-agent] %s step=%d %s", tool_name, step_num, tool_result[:120])

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tool_name,
                "content": tool_result,
            })

    _log.warning("[mdx-agent] %s exhausted %d steps", cube_name, max_steps)
    return AgentResult(rows=[], layout={}, mdx="", steps=steps, error=f"exceeded {max_steps} steps")
