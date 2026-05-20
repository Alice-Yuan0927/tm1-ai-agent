"""Smoke tests for pure helpers in services.analyze_pipeline."""

from backend.services.analyze_pipeline import (
    _CubeResult,
    _RequestSchemaCache,
    _analysis_fallback_message,
)
from backend.ai.intent.layout_followup import effective_question_from_history
from backend.services.mdx_execution import is_connection_error


def test_request_schema_cache_memoizes(monkeypatch):
    calls = []

    def fake_get_schema(cube: str) -> dict:
        calls.append(cube)
        return {"cube": cube, "dimensions": []}

    monkeypatch.setattr(
        "backend.services.analyze_pipeline.get_cube_schema",
        fake_get_schema,
    )

    cache = _RequestSchemaCache()
    a = cache.get("X")
    b = cache.get("X")
    c = cache.get("Y")
    assert a is b  # same object on hit
    assert c is not a
    assert calls == ["X", "Y"]


def test_effective_question_carries_previous_turn_for_followups():
    history = [{"question": "show P&L statement for 2025"}]
    out = effective_question_from_history("by month", history)
    assert "P&L" in out
    assert "by month" in out


def test_effective_question_axis_followup_preserves_previous_columns(monkeypatch):
    monkeypatch.setattr(
        "backend.ai.intent.layout_followup.complete_text",
        lambda *args, **kwargs: (
            '{"intent":"modify_layout","row_action":"replace","column_action":"keep",'
            '"row_target":"segment 1","column_target":"","clarification":"",'
            '"reason":"User asked for a row breakdown by segment 1."}'
        ),
    )
    history = [{
        "question": "show actual P&L by year",
        "chosen_cube": "Consol GL Company",
        "data_sources": [{
            "cube": "Consol GL Company",
            "generated_mdx": (
                "SELECT {[Year].[Year].[2024], [Year].[Year].[2023]} ON COLUMNS, "
                "{Descendants([Account].[Account].[Profit and Loss])} ON ROWS "
                "FROM [Consol GL Company]"
            ),
            "structured_preview": {
                "row_dimensions": ["Account"],
                "column_dimensions": ["Year"],
                "filters": [{"dimension": "Scenario", "element": "ACT"}],
                "columns": ["2024", "2023"],
            },
        }],
    }]

    out = effective_question_from_history("show me by segment 1", history)

    assert "Follow-up layout instruction" in out
    assert "ROWS action: replace -> segment 1" in out
    assert "COLUMNS action: keep" in out
    assert "Keep the previous COLUMNS axis unchanged" in out
    assert "Do not put 'segment 1' on COLUMNS" in out


def test_effective_question_comparison_followup_preserves_previous_rows(monkeypatch):
    monkeypatch.setattr(
        "backend.ai.intent.layout_followup.complete_text",
        lambda *args, **kwargs: (
            '{"intent":"modify_layout","row_action":"keep","column_action":"replace",'
            '"row_target":"","column_target":"scenario variance","clarification":"",'
            '"reason":"Variance should be shown as a comparison on columns."}'
        ),
    )
    history = [{
        "question": "show actual P&L by account",
        "chosen_cube": "Consol GL Company",
        "data_sources": [{
            "cube": "Consol GL Company",
            "generated_mdx": (
                "SELECT {[Year].[Year].[2024]} ON COLUMNS, "
                "{Descendants([Account].[Account].[Profit and Loss])} ON ROWS "
                "FROM [Consol GL Company]"
            ),
            "structured_preview": {
                "row_dimensions": ["Account"],
                "column_dimensions": ["Year"],
                "filters": [{"dimension": "Scenario", "element": "ACT"}],
                "columns": ["2024"],
            },
        }],
    }]

    out = effective_question_from_history("show variance by scenario", history)

    assert "Follow-up layout instruction" in out
    assert "ROWS action: keep" in out
    assert "COLUMNS action: replace -> scenario variance" in out
    assert "Keep the previous ROWS axis unchanged" in out
    assert "Do not put 'scenario variance' on ROWS" in out


def test_effective_question_keeps_long_question_alone():
    history = [{"question": "previous"}]
    long = "Show me the full revenue breakdown by region and quarter for Q4 2025"
    out = effective_question_from_history(long, history)
    assert out == long


def test_effective_question_no_history_passthrough():
    assert effective_question_from_history("any question", None) == "any question"


def test_looks_like_connection_error_matches_patterns():
    assert is_connection_error("Connection refused")
    assert is_connection_error("SSL handshake failed")
    assert is_connection_error("authentication denied")
    assert not is_connection_error("MDX syntax error")


def test_analysis_fallback_message_calls_out_rate_limit():
    msg = _analysis_fallback_message("HTTP 429 rate_limit_exceeded")
    assert "rate limit" in msg.lower()


def test_analysis_fallback_message_generic_for_other_errors():
    msg = _analysis_fallback_message("Some random error")
    assert "narrative step failed" in msg


def test_cuberesult_to_source_dict_has_required_keys():
    result = _CubeResult(
        cube="C", reasoning="r",
        rows=[{"value": 1}], layout={}, mdx="m",
        mdx_attempts=[], full_preview={}, limited_preview={},
        effective_row_count=1,
    )
    d = result.to_source_dict()
    assert d["cube"] == "C"
    assert d["data_row_count"] == 1
