"""Unit tests for the MDX agent loop — LLM and execute_once are mocked."""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from backend.ai.mdx.agent import AgentResult, run_mdx_agent
from backend.ai.mdx.context import MdxContext


# ── Shared helpers ────────────────────────────────────────────────────────────

def _ctx(**overrides) -> MdxContext:
    defaults = dict(
        question="show revenue",
        cube_schema={
            "cube": "SalesCube",
            "dimensions": [
                {"name": "Account", "elements": ["Revenue", "COGS"]},
                {"name": "Measure", "is_measure": True, "elements": ["Amount"]},
            ],
        },
    )
    defaults.update(overrides)
    return MdxContext(**defaults)


def _tool_call(name: str, args: dict, call_id: str = "tc1") -> dict:
    return {"name": name, "args": args, "id": call_id}


_SIMPLE_MDX = "SELECT [Measure].[Measure].[Amount] ON COLUMNS FROM [SalesCube]"

_PATCH_LLM = "backend.ai.mdx.agent.call_with_tools"
_PATCH_EXEC = "backend.services.mdx_execution.execute_once"
_PATCH_NORM = "backend.ai.mdx.agent.normalize_mdx"
_PATCH_MERGE = "backend.ai.mdx.agent._merge_historical_grounded"


def _identity_merge(ctx: MdxContext) -> MdxContext:
    return ctx


# ── Success on first execute ──────────────────────────────────────────────────

def test_agent_succeeds_on_first_execute():
    ctx = _ctx()
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=lambda mdx, *a, **kw: mdx), \
         patch(_PATCH_EXEC, return_value=([{"Account": "Revenue"}], {}, [], None)), \
         patch(_PATCH_LLM, return_value=(None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX})])) as mock_llm:
        result = run_mdx_agent(ctx, max_steps=5)

    assert result.error is None
    assert result.rows == [{"Account": "Revenue"}]
    assert result.mdx == _SIMPLE_MDX
    assert mock_llm.call_count == 1


def test_agent_records_steps():
    ctx = _ctx()
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=lambda mdx, *a, **kw: mdx), \
         patch(_PATCH_EXEC, return_value=([{"r": 1}], {}, [], None)), \
         patch(_PATCH_LLM, return_value=(None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX})])):
        result = run_mdx_agent(ctx, max_steps=5)

    assert len(result.steps) == 1
    assert result.steps[0].tool == "execute_mdx"


# ── Retry on 0 rows ───────────────────────────────────────────────────────────

def test_agent_retries_after_zero_rows():
    ctx = _ctx()
    llm_responses = [
        (None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX}, "tc1")]),
        (None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX}, "tc2")]),
    ]
    exec_results = [
        ([], {}, [], None),              # step 0: 0 rows → retry
        ([{"Account": "Revenue"}], {}, [], None),  # step 1: success
    ]
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=lambda mdx, *a, **kw: mdx), \
         patch(_PATCH_EXEC, side_effect=exec_results), \
         patch(_PATCH_LLM, side_effect=llm_responses):
        result = run_mdx_agent(ctx, max_steps=5)

    assert result.error is None
    assert result.rows == [{"Account": "Revenue"}]


def test_agent_retry_provides_hint_to_llm():
    """After 0 rows, the tool result sent back must include a hint."""
    ctx = _ctx()
    captured_messages: list[list] = []

    def capture_llm(messages, tools, **kwargs):
        captured_messages.append(list(messages))
        if len(captured_messages) == 1:
            return (None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX}, "tc1")])
        return (None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX}, "tc2")])

    exec_results = [([], {}, [], None), ([{"r": 1}], {}, [], None)]
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=lambda mdx, *a, **kw: mdx), \
         patch(_PATCH_EXEC, side_effect=exec_results), \
         patch(_PATCH_LLM, side_effect=capture_llm):
        run_mdx_agent(ctx, max_steps=5)

    # The second LLM call must have received a tool result containing the hint
    second_call_messages = captured_messages[1]
    tool_contents = [m.get("content", "") for m in second_call_messages if m.get("role") == "tool"]
    assert any("hint" in c or "0 rows" in c or "search_elements" in c for c in tool_contents)


# ── Gives up on text response ─────────────────────────────────────────────────

def test_agent_gives_up_on_text_response():
    ctx = _ctx()
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_LLM, return_value=("I cannot write MDX for this question.", [])):
        result = run_mdx_agent(ctx, max_steps=5)

    assert result.error is not None
    assert "gave up" in result.error
    assert result.rows == []
    assert result.mdx == ""


def test_agent_gives_up_includes_llm_explanation_in_error():
    ctx = _ctx()
    explanation = "The cube does not contain requested data."
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_LLM, return_value=(explanation, [])):
        result = run_mdx_agent(ctx, max_steps=5)

    assert explanation[:50] in result.error


# ── Max steps exhaustion ──────────────────────────────────────────────────────

def test_agent_exhausts_max_steps():
    ctx = _ctx()
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=lambda mdx, *a, **kw: mdx), \
         patch(_PATCH_EXEC, return_value=([], {}, [], None)), \
         patch(_PATCH_LLM, return_value=(None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX})])) as mock_llm:
        result = run_mdx_agent(ctx, max_steps=3)

    assert result.error is not None
    assert "exceeded" in result.error
    assert "3" in result.error
    # LLM called for each step (3 times)
    assert mock_llm.call_count == 3


# ── Non-execute_mdx tool calls (element lookups) ──────────────────────────────

def test_agent_dispatches_non_execute_tools_via_registry():
    ctx = _ctx()
    llm_responses = [
        (None, [_tool_call("get_dimension_members", {"dimension": "Account"}, "tc1")]),
        (None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX}, "tc2")]),
    ]
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=lambda mdx, *a, **kw: mdx), \
         patch(_PATCH_EXEC, return_value=([{"r": 1}], {}, [], None)), \
         patch(_PATCH_LLM, side_effect=llm_responses):
        result = run_mdx_agent(ctx, max_steps=5)

    assert result.error is None
    # Both steps recorded: get_dimension_members + execute_mdx
    assert len(result.steps) == 2
    assert result.steps[0].tool == "get_dimension_members"
    assert result.steps[1].tool == "execute_mdx"


# ── MDX normalization error ───────────────────────────────────────────────────

def test_agent_handles_normalize_mdx_error_and_continues():
    """If normalize_mdx raises, the agent feeds the error back to the LLM and retries."""
    ctx = _ctx()
    norm_calls = [0]

    def norm_side_effect(mdx, *a, **kw):
        norm_calls[0] += 1
        if norm_calls[0] == 1:
            raise ValueError("bad MDX syntax")
        return mdx

    llm_responses = [
        (None, [_tool_call("execute_mdx", {"mdx": "BAD MDX"}, "tc1")]),
        (None, [_tool_call("execute_mdx", {"mdx": _SIMPLE_MDX}, "tc2")]),
    ]
    with patch(_PATCH_MERGE, side_effect=_identity_merge), \
         patch(_PATCH_NORM, side_effect=norm_side_effect), \
         patch(_PATCH_EXEC, return_value=([{"r": 1}], {}, [], None)), \
         patch(_PATCH_LLM, side_effect=llm_responses):
        result = run_mdx_agent(ctx, max_steps=5)

    # Agent recovers and eventually succeeds
    assert result.error is None
