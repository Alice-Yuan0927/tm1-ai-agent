"""Unit tests for MDX directive builders — no LLM or DB required."""

from backend.ai.mdx.directives import (
    _best_all_period,
    _line_item_dim_from_profile,
    _profile_dim_defaults,
    line_item_directive,
    profile_defaults_directive,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _pnl_schema() -> dict:
    return {
        "cube": "P&L",
        "dimensions": [
            {
                "name": "Account",
                "elements": ["Revenue", "COGS", "Gross Profit", "Net Income"],
                "consolidations": ["Profit and Loss", "Gross Profit", "Net Income"],
                "top_consolidations": ["Profit and Loss"],
                "default_element": "Profit and Loss",
            },
            {
                "name": "Year",
                "is_time_dim": True,
                "elements": ["2024", "2025"],
                "default_element": "2025",
            },
            {
                "name": "Month",
                "is_time_dim": True,
                "elements": ["All Months", "Jan", "Feb", "Mar"],
                "consolidations": ["All Months"],
                "default_element": "All Months",
            },
            {
                "name": "Scenario",
                "elements": ["Actual", "Budget"],
                "default_element": "Actual",
            },
            {
                "name": "Measure",
                "is_measure": True,
                "elements": ["Amount"],
                "default_element": "Amount",
            },
        ],
    }


def _profile() -> dict:
    return {
        "finance_semantics": {
            "concepts": {
                "income_statement": {
                    "confidence": 1.0,
                    "line_item_dimension": "Account",
                    "preferred_measures": ["Amount"],
                }
            }
        },
        "default_filters": {
            "Year": "2025",
            "Month": "All Months",
            "Scenario": "Actual",
        },
    }


# ── _profile_dim_defaults ─────────────────────────────────────────────────────

def test_profile_dim_defaults_returns_non_reserved_keys():
    profile = {"default_filters": {"Scenario": "Actual", "Year": "2025"}}
    result = _profile_dim_defaults(profile)
    # "Year" is in _RESERVED_DEFAULT_FILTER_KEYS, "Scenario" is not
    assert "Scenario" in result
    assert "Year" not in result


def test_profile_dim_defaults_excludes_all_reserved_keys():
    profile = {
        "default_filters": {
            "current_year": "2025",
            "current_month": "Jan",
            "Month": "All Months",
            "Year": "2025",
            "forecast_year": "2026",
        }
    }
    result = _profile_dim_defaults(profile)
    assert result == {}


def test_profile_dim_defaults_skips_non_string_values():
    profile = {"default_filters": {"Entity": 42, "Scenario": "Actual"}}
    result = _profile_dim_defaults(profile)
    assert "Entity" not in result
    assert result["Scenario"] == "Actual"


def test_profile_dim_defaults_strips_whitespace():
    profile = {"default_filters": {"Entity": "  HQ  "}}
    result = _profile_dim_defaults(profile)
    assert result["Entity"] == "HQ"


def test_profile_dim_defaults_none_profile():
    assert _profile_dim_defaults(None) == {}


def test_profile_dim_defaults_missing_default_filters():
    assert _profile_dim_defaults({"other_key": "x"}) == {}


# ── _best_all_period ──────────────────────────────────────────────────────────

def test_best_all_period_returns_empty_when_no_aggregates():
    assert _best_all_period(["Jan", "Feb", "Mar"]) == ""


def test_best_all_period_returns_empty_list():
    assert _best_all_period([]) == ""


def test_best_all_period_returns_only_aggregate():
    result = _best_all_period(["Jan", "All Months", "Feb"])
    assert result == "All Months"


def test_best_all_period_returns_first_when_one_aggregate():
    result = _best_all_period(["All Months"])
    assert result == "All Months"


def test_best_all_period_picks_smallest_when_no_db():
    # With no DB, _estimate_descendants returns 0 for all → falls back to first agg.
    result = _best_all_period(["All Years", "Total Year"], dim_name="Year")
    assert result in ("All Years", "Total Year")


def test_best_all_period_matching_is_case_insensitive():
    result = _best_all_period(["all months"])
    assert result == "all months"


# ── _line_item_dim_from_profile ───────────────────────────────────────────────

def test_line_item_dim_profile_first_returns_account():
    dim = _line_item_dim_from_profile(_pnl_schema(), _profile())
    assert dim is not None
    assert dim.get("name") == "Account"


def test_line_item_dim_profile_with_unknown_dim_name_falls_back():
    profile = {
        "finance_semantics": {
            "concepts": {"income_statement": {"line_item_dimension": "NonExistentDim"}}
        }
    }
    # "NonExistentDim" is not in schema → should fall back to schema scoring
    dim = _line_item_dim_from_profile(_pnl_schema(), profile)
    # Fallback scores Account highest (revenue, cost, etc. in elements)
    assert dim is not None
    assert dim.get("name") == "Account"


def test_line_item_dim_no_profile_uses_schema_scoring():
    dim = _line_item_dim_from_profile(_pnl_schema(), None)
    assert dim is not None
    assert dim.get("name") == "Account"


def test_line_item_dim_returns_none_when_no_pnl_signals():
    schema = {
        "cube": "HR",
        "dimensions": [
            {"name": "Employee", "elements": ["Alice", "Bob"]},
            {"name": "Department", "elements": ["Engineering", "Marketing"]},
            {"name": "Measure", "is_measure": True, "elements": ["Headcount"]},
        ],
    }
    dim = _line_item_dim_from_profile(schema, None)
    assert dim is None


def test_line_item_dim_skips_measure_dims():
    schema = {
        "cube": "Test",
        "dimensions": [
            {
                "name": "Measures",
                "is_measure": True,
                "elements": ["Revenue", "Gross Profit", "Net Income", "EBITDA"],
            },
            {"name": "Year", "is_time_dim": True, "elements": ["2025"]},
        ],
    }
    # Measure dim has PNL tokens but should be skipped
    dim = _line_item_dim_from_profile(schema, None)
    assert dim is None


# ── line_item_directive ───────────────────────────────────────────────────────

def test_line_item_directive_empty_for_non_statement_question():
    result = line_item_directive("show revenue by department", _pnl_schema(), _profile())
    assert result == ""


def test_line_item_directive_returns_directive_for_pnl_question():
    result = line_item_directive("show me the P&L statement", _pnl_schema(), _profile())
    assert result != ""
    assert "Account" in result
    assert "Profit and Loss" in result


def test_line_item_directive_returns_directive_for_income_statement():
    result = line_item_directive("income statement for 2025", _pnl_schema(), _profile())
    assert result != ""
    assert "Account" in result


def test_line_item_directive_empty_when_no_matching_dim():
    schema = {
        "cube": "HR",
        "dimensions": [
            {"name": "Employee", "elements": ["Alice"]},
            {"name": "Measure", "is_measure": True, "elements": ["Count"]},
        ],
    }
    result = line_item_directive("show the P&L", schema, None)
    assert result == ""


# ── profile_defaults_directive ────────────────────────────────────────────────

def test_profile_defaults_directive_emits_scenario_override():
    result = profile_defaults_directive(_pnl_schema(), _profile())
    assert "Scenario" in result
    assert "Actual" in result


def test_profile_defaults_directive_empty_when_no_relevant_overrides():
    schema = {
        "cube": "HR",
        "dimensions": [{"name": "Employee", "elements": ["Alice"]}],
    }
    profile = {"default_filters": {"Year": "2025"}}  # reserved key only
    result = profile_defaults_directive(schema, profile)
    assert result == ""


def test_profile_defaults_directive_injects_all_period_for_month_dim():
    # No Scenario in profile → only Month time dim gets all-period injection.
    profile = {"default_filters": {}}
    result = profile_defaults_directive(_pnl_schema(), profile, question="show revenue")
    # Month dim has "All Months" consolidation → should appear
    assert "All Months" in result


def test_profile_defaults_directive_skips_all_period_when_month_named():
    profile = {"default_filters": {}}
    result = profile_defaults_directive(_pnl_schema(), profile, question="show March revenue")
    # Question names a specific month → all-period injection is suppressed
    assert "All Months" not in result


def test_profile_defaults_directive_pins_currency_view_to_entity_currency():
    schema = {
        "cube": "Consol GL Group",
        "dimensions": [
            {
                "name": "S Consol GL Group",
                "elements": ["All Data Sources List", "LOCAL_VIEW", "LOCAL_TOTAL", "PARENT_VIEW"],
                "default_element": "All Data Sources List",
                "consolidations": ["All Data Sources List", "LOCAL_TOTAL"],
                "element_attr_values": {
                    "LOCAL_VIEW": {"Description": "Entity Currency"},
                    "LOCAL_TOTAL": {"Description": "Entity Currency Total"},
                    "PARENT_VIEW": {"Description": "Parent Currency"},
                },
            }
        ],
    }
    profile = {"dim_roles": {"S Consol GL Group": "data_source"}}

    result = profile_defaults_directive(schema, profile, question="show P&L")

    assert "[S Consol GL Group].[S Consol GL Group].[LOCAL_VIEW]" in result
    assert "[S Consol GL Group].[S Consol GL Group].[All Data Sources List]" not in result


def test_profile_defaults_directive_honors_explicit_dimension_member_correction():
    schema = {
        "cube": "Consol GL Group",
        "dimensions": [
            {
                "name": "S Consol GL Group",
                "elements": ["All Data Sources List", "LOCAL_VIEW", "PARENT_TOTAL"],
                "default_element": "All Data Sources List",
                "element_attr_values": {
                    "LOCAL_VIEW": {"Description": "Entity Currency"},
                    "PARENT_TOTAL": {"Description": "Parent Currency Total"},
                },
            }
        ],
    }
    profile = {"dim_roles": {"S Consol GL Group": "currency_view"}}

    result = profile_defaults_directive(
        schema,
        profile,
        question="show P&L for SLIM HK. i mean the S Consol GL Group should be PARENT_TOTAL",
    )

    assert "[S Consol GL Group].[S Consol GL Group].[PARENT_TOTAL]" in result
    assert "[S Consol GL Group].[S Consol GL Group].[LOCAL_VIEW]" not in result
