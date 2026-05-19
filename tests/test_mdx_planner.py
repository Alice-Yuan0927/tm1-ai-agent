from backend.ai.mdx.planner import try_plan_mdx


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
        "default_filters": {"Year": "2025", "Month": "All Months"},
    }


def test_planner_ignores_non_statement_question_without_grounded_consolidation():
    plan = try_plan_mdx("show revenue by customer", _pnl_schema(), model_profile=_profile())

    assert plan is None


def test_planner_puts_year_comparison_on_columns_not_where():
    plan = try_plan_mdx(
        "compare actual P&L 2024 vs 2025",
        _pnl_schema(),
        model_profile=_profile(),
    )

    assert plan is not None
    assert "{[Year].[Year].[2024], [Year].[Year].[2025]}" in plan.mdx
    assert "WHERE ([Year].[Year]" not in plan.mdx
    assert any("Year set on columns" in item for item in plan.assumptions)


def test_planner_puts_month_breakdown_on_columns_and_period_out_of_where():
    plan = try_plan_mdx(
        "show actual P&L in 2025 by month",
        _pnl_schema(),
        model_profile=_profile(),
    )

    assert plan is not None
    assert "{[Month].[Month].[2025].Children}" in plan.mdx
    assert "[Month].[Month].[All Months]" not in plan.mdx


def test_planner_defers_when_scenario_pair_cannot_be_resolved():
    schema = _pnl_schema()
    scenario = next(d for d in schema["dimensions"] if d["name"] == "Scenario")
    scenario["elements"] = ["Actual"]

    plan = try_plan_mdx(
        "compare actual vs budget P&L in 2025",
        schema,
        model_profile=_profile(),
    )

    assert plan is None
