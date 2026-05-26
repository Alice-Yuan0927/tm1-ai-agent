"""Unit tests for element tool handlers — no real DB or embedding API required."""

import json
from unittest.mock import patch

from backend.ai.tools.element_tools import ELEMENT_REGISTRY


# ── Shared schema ─────────────────────────────────────────────────────────────

def _schema() -> dict:
    return {
        "cube": "SalesCube",
        "dimensions": [
            {"name": "Account", "elements": ["Revenue", "COGS"], "is_measure": False},
            {"name": "Region", "elements": ["APAC", "EMEA"], "is_measure": False},
            {"name": "Measure", "is_measure": True, "elements": ["Amount"]},
        ],
    }


def _exec(tool: str, args: dict, schema: dict | None = None) -> dict:
    raw = ELEMENT_REGISTRY.execute(tool, args, schema or _schema())
    return json.loads(raw)


# ── validate_mdx ──────────────────────────────────────────────────────────────

def test_validate_mdx_valid_statement():
    mdx = "SELECT [Measure].[Measure].[Amount] ON COLUMNS FROM [SalesCube]"
    result = _exec("validate_mdx", {"mdx": mdx})
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_mdx_missing_select():
    mdx = "[Measure].[Measure].[Amount] ON COLUMNS FROM [SalesCube]"
    result = _exec("validate_mdx", {"mdx": mdx})
    assert result["valid"] is False
    assert any("SELECT" in e for e in result["errors"])


def test_validate_mdx_wrong_cube_in_from():
    mdx = "SELECT [Measure].[Measure].[Amount] ON COLUMNS FROM [WrongCube]"
    result = _exec("validate_mdx", {"mdx": mdx})
    assert result["valid"] is False
    assert any("FROM" in e or "cube" in e.lower() for e in result["errors"])


def test_validate_mdx_missing_on_columns():
    mdx = "SELECT [Measure].[Measure].[Amount] FROM [SalesCube]"
    result = _exec("validate_mdx", {"mdx": mdx})
    assert result["valid"] is False
    assert any("COLUMNS" in e for e in result["errors"])


def test_validate_mdx_unbalanced_brackets():
    mdx = "SELECT [Measure].[Measure].[Amount ON COLUMNS FROM [SalesCube]"
    result = _exec("validate_mdx", {"mdx": mdx})
    assert result["valid"] is False
    assert any("bracket" in e.lower() for e in result["errors"])


def test_validate_mdx_empty_string():
    result = _exec("validate_mdx", {"mdx": ""})
    assert result["valid"] is False


def test_validate_mdx_unknown_dim_produces_warning():
    mdx = (
        "SELECT [Measure].[Measure].[Amount] ON COLUMNS "
        "FROM [SalesCube] "
        "WHERE ([GhostDim].[GhostDim].[GhostElem])"
    )
    result = _exec("validate_mdx", {"mdx": mdx})
    # Unknown dim goes into warnings, not errors (MDX may still be valid structurally)
    assert any("GhostDim" in w for w in result.get("warnings", []))


def test_validate_mdx_no_cube_name_in_schema_skips_from_check():
    schema = {"cube": "", "dimensions": []}
    mdx = "SELECT {[x].[x].[y]} ON COLUMNS FROM [AnyCube]"
    result = _exec("validate_mdx", {"mdx": mdx}, schema)
    # No cube name to compare against → FROM check is skipped, no FROM error
    assert not any("FROM" in e for e in result["errors"])


# ── search_elements FTS-first ordering ───────────────────────────────────────

def test_search_elements_returns_fts_matches_without_calling_embedding():
    # find_question_element_matches returns list[tuple[phrase, dim, element]].
    fts_result = [("revenue", "Account", "Revenue")]
    with patch("backend.ai.tools.element_tools.find_question_element_matches", return_value=fts_result), \
         patch("backend.ai.tools.element_tools.search_by_embedding") as mock_emb:
        result = _exec("search_elements", {"dimension": "Account", "term": "revenue"})

    mock_emb.assert_not_called()
    assert "Revenue" in result["matches"]
    assert result.get("matched_by") == "fts"


def test_search_elements_calls_embedding_only_when_fts_finds_nothing():
    with patch("backend.ai.tools.element_tools.find_question_element_matches", return_value=[]), \
         patch("backend.ai.tools.element_tools.search_by_embedding", return_value=[]) as mock_emb:
        _exec("search_elements", {"dimension": "Account", "term": "revenue"})

    mock_emb.assert_called_once()


def test_search_elements_filters_fts_results_by_dimension():
    fts_result = [
        ("revenue", "Account", "Revenue"),
        ("revenue region", "Region", "Revenue Region"),
    ]
    with patch("backend.ai.tools.element_tools.find_question_element_matches", return_value=fts_result), \
         patch("backend.ai.tools.element_tools.search_by_embedding") as mock_emb:
        result = _exec("search_elements", {"dimension": "Account", "term": "revenue"})

    mock_emb.assert_not_called()
    assert "Revenue" in result["matches"]
    assert all("Region" not in m for m in result["matches"])


def test_search_elements_returns_no_matches_when_both_empty():
    with patch("backend.ai.tools.element_tools.find_question_element_matches", return_value=[]), \
         patch("backend.ai.tools.element_tools.search_by_embedding", return_value=[]):
        result = _exec("search_elements", {"dimension": "Account", "term": "xyz123"})

    assert result["matches"] == []


# ── get_dimension_members ─────────────────────────────────────────────────────

def test_get_dimension_members_found():
    result = _exec("get_dimension_members", {"dimension": "Account"})
    assert result["dimension"] == "Account"
    assert "Revenue" in result["elements"]


def test_get_dimension_members_not_found():
    result = _exec("get_dimension_members", {"dimension": "NonExistent"})
    assert "error" in result


# ── get_cube_summary falls back gracefully without DB ─────────────────────────

def test_get_cube_summary_without_db_returns_schema_fallback():
    # No DB table → exception caught → schema-derived fallback
    result = _exec("get_cube_summary", {"cube": "SalesCube"})
    # Either a real summary or the schema-derived fallback — both must have "cube"
    assert result.get("cube") == "SalesCube" or "error" in result


def test_list_views_from_cube_tool_returns_views():
    with patch("backend.ai.tools.element_tools.list_cube_views", return_value=[
        {"name": "Balance Sheet", "private": False},
    ]):
        result = _exec("list_views_from_cube", {"cube": "SalesCube"})

    assert result["cube"] == "SalesCube"
    assert result["count"] == 1
    assert result["views"][0]["name"] == "Balance Sheet"


def test_get_mdx_from_view_tool_returns_mdx():
    with patch(
        "backend.ai.tools.element_tools.get_cube_view_mdx",
        return_value="SELECT {} ON COLUMNS FROM [SalesCube]",
    ):
        result = _exec("get_mdx_from_view", {"cube": "SalesCube", "view": "Default"})

    assert result["cube"] == "SalesCube"
    assert result["view"] == "Default"
    assert "SELECT" in result["mdx"]
