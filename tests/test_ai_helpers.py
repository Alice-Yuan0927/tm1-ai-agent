"""Pure-function smoke tests for the refactored ai/ helpers (no LLM/TM1 needed)."""

from backend.ai.output.conversation import conversation_context
from backend.ai.mdx.context import MdxContext
from backend.ai.mdx.normalize import (
    normalize_mdx,
    validate_generated_mdx,
)
from backend.tm1.service import build_structured_preview, get_view_layout_from_mdx
from backend.ai.output.narrative import parse_suggestions
from backend.ai.intent.preflight import is_unclear_question
import pytest


# ── preflight ────────────────────────────────────────────────────────────────

def test_is_unclear_short_text():
    assert is_unclear_question("hi")
    assert is_unclear_question("?")


def test_is_unclear_only_filler():
    assert is_unclear_question("yo .")  # single short word + symbol


def test_is_unclear_real_question():
    assert not is_unclear_question("Show me labor cost trends by month")


# ── conversation ─────────────────────────────────────────────────────────────

def test_conversation_context_empty():
    assert conversation_context(None) == "No previous messages."
    assert conversation_context([]) == "No previous messages."


def test_conversation_context_includes_cube_tag():
    out = conversation_context([
        {"question": "show salary", "analysis": "ok", "chosen_cube": "Comp"},
    ])
    assert "[cube: Comp]" in out
    assert "show salary" in out


def test_conversation_context_truncates_long_analysis():
    long_analysis = "x" * 5000
    out = conversation_context([
        {"question": "q", "analysis": long_analysis, "chosen_cube": "C"},
    ])
    assert "..." in out
    assert len(out) < 2000


# ── MdxContext ───────────────────────────────────────────────────────────────

def test_mdxcontext_cube_name_derives_from_schema():
    ctx = MdxContext(
        question="q",
        cube_schema={"cube": "SalesCube", "dimensions": []},
    )
    assert ctx.cube_name == "SalesCube"


def test_mdxcontext_is_frozen():
    ctx = MdxContext(question="q", cube_schema={"cube": "C"})
    with pytest.raises(Exception):
        ctx.question = "other"  # type: ignore[misc]


# ── mdx_normalize ────────────────────────────────────────────────────────────

def test_normalize_mdx_strips_code_fences():
    raw = "```mdx\nSELECT {} ON COLUMNS,\n{} ON ROWS\nFROM [Cube]\n```"
    out = normalize_mdx(raw, "show", None)
    assert "```" not in out
    assert "FROM [Cube]" in out


def test_normalize_mdx_fixes_where_before_from():
    raw = "SELECT {} ON COLUMNS, {} ON ROWS WHERE ([Year].[Year].[2025]) FROM [Cube]"
    out = normalize_mdx(raw, "show", None)
    from_pos = out.upper().index("FROM ")
    where_pos = out.upper().index("WHERE ")
    assert from_pos < where_pos


def test_normalize_mdx_preserves_all_period_for_year_comparison():
    raw = (
        "SELECT {[Year].[Year].[2024], [Year].[Year].[2023]} ON COLUMNS, "
        "{[Account].[Account].[Revenue]} ON ROWS "
        "FROM [Cube] WHERE ([Month].[Month].[All MTD], [Scenario].[Scenario].[ACT])"
    )
    profile = {"default_filters": {"Month": "01"}}

    out = normalize_mdx(
        raw,
        "How do SLIM-GZ's 2024 results compare to the prior year (2023 actual)?",
        profile,
    )

    assert "[Month].[Month].[All MTD]" in out
    assert "[Month].[Month].[01]" not in out


def test_normalize_mdx_still_replaces_unrequested_rollup_for_single_year():
    raw = (
        "SELECT {[Year].[Year].[2024]} ON COLUMNS, "
        "{[Account].[Account].[Revenue]} ON ROWS "
        "FROM [Cube] WHERE ([Month].[Month].[All MTD], [Scenario].[Scenario].[ACT])"
    )
    profile = {"default_filters": {"Month": "01"}}

    out = normalize_mdx(raw, "show SLIM-GZ results in 2024", profile)

    assert "[Month].[Month].[01]" in out
    assert "[Month].[Month].[All MTD]" not in out


def test_validate_generated_mdx_requires_select():
    import pytest
    with pytest.raises(RuntimeError, match="SELECT"):
        validate_generated_mdx("not mdx", "Cube")


def test_validate_generated_mdx_requires_correct_cube():
    import pytest
    with pytest.raises(RuntimeError, match="FROM"):
        validate_generated_mdx("SELECT {} ON COLUMNS FROM [Other]", "Cube")


def test_validate_generated_mdx_passes_valid_mdx():
    validate_generated_mdx("SELECT {} ON COLUMNS FROM [Cube]", "Cube")  # no raise


def test_structured_preview_exposes_row_hierarchy_levels():
    mdx = (
        "SELECT {[Year].[Year].[2025]} ON COLUMNS, "
        "{Descendants([Account].[Account].[Profit and Loss], 99)} ON ROWS "
        "FROM [P&L]"
    )
    layout = get_view_layout_from_mdx(mdx)
    rows = [
        {"_dimensions": {"Year": "2025", "Account": "Revenue"}, "value": 10},
        {"_dimensions": {"Year": "2025", "Account": "Product Revenue"}, "value": 8},
    ]
    preview = build_structured_preview(
        rows,
        layout,
        hierarchy_edges={
            "Account": [
                ("Profit and Loss", "Revenue"),
                ("Revenue", "Product Revenue"),
            ]
        },
    )

    assert preview["row_hierarchy"]["Account"] == {
        "Revenue": 0,
        "Product Revenue": 1,
    }


def test_structured_preview_marks_consolidated_row_values():
    mdx = (
        "SELECT {[Year].[Year].[2025]} ON COLUMNS, "
        "{[Account].[Account].[Revenue], [Account].[Account].[Product Revenue]} ON ROWS "
        "FROM [P&L]"
    )
    layout = get_view_layout_from_mdx(mdx)
    rows = [
        {"_dimensions": {"Year": "2025", "Account": "Revenue"}, "value": 10},
        {"_dimensions": {"Year": "2025", "Account": "Product Revenue"}, "value": 8},
    ]
    preview = build_structured_preview(
        rows,
        layout,
        dim_metadata={"Account": {"consolidated": {"Revenue"}}},
    )

    assert preview["row_consolidations"] == {"Account": ["Revenue"]}


# ── narrative.parse_suggestions ──────────────────────────────────────────────

def test_structured_preview_marks_visible_hierarchy_root_as_level_zero():
    mdx = (
        "SELECT {[Year].[Year].[2025]} ON COLUMNS, "
        "{DRILLDOWNLEVEL({[Account].[Account].[Profit / (Loss) after Tax]})} ON ROWS "
        "FROM [P&L]"
    )
    layout = get_view_layout_from_mdx(mdx)
    rows = [
        {"_dimensions": {"Year": "2025", "Account": "Profit / (Loss) after Tax"}, "value": 10},
        {"_dimensions": {"Year": "2025", "Account": "Profit / (Loss) before Tax"}, "value": 8},
    ]
    preview = build_structured_preview(
        rows,
        layout,
        hierarchy_edges={
            "Account": [
                ("Profit / (Loss) after Tax", "Profit / (Loss) before Tax"),
            ]
        },
    )

    assert preview["row_hierarchy"]["Account"] == {
        "Profit / (Loss) after Tax": 0,
        "Profit / (Loss) before Tax": 1,
    }


def test_parse_suggestions_with_marker():
    text = 'Analysis here.\nSUGGESTIONS: ["q1", "q2", "q3"]'
    analysis, suggestions = parse_suggestions(text)
    assert analysis == "Analysis here."
    assert suggestions == ["q1", "q2", "q3"]


def test_parse_suggestions_without_marker():
    analysis, suggestions = parse_suggestions("just text, no marker")
    assert analysis == "just text, no marker"
    assert suggestions == []


def test_parse_suggestions_invalid_json():
    text = "Analysis.\nSUGGESTIONS: not a json array"
    analysis, suggestions = parse_suggestions(text)
    # Falls back to original text + empty suggestions
    assert suggestions == []
