import importlib.util
from pathlib import Path


def _load_module(name: str, relative_path: str):
    module_path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


prompt_rules = _load_module("prompt_rules", "backend/ai/prompt_rules.py")
result_validators = _load_module("result_validators", "backend/ai/result_validators.py")
finance_semantics = _load_module("finance_semantics", "backend/ai/finance_semantics.py")
from backend.ai import service as ai_service
from backend.response_messages import no_usable_data_message

GENERAL_AGENT_CONTRACT = prompt_rules.GENERAL_AGENT_CONTRACT
PROMPT_MANAGEMENT_RULES = prompt_rules.PROMPT_MANAGEMENT_RULES
REASONING_MODEL_RULES = prompt_rules.REASONING_MODEL_RULES
result_shape_issue = result_validators.result_shape_issue
specific_focus_issue = result_validators.specific_focus_issue
statement_line_item_issue = result_validators.statement_line_item_issue
default_filter_issue = result_validators.default_filter_issue
build_finance_semantic_profile = finance_semantics.build_finance_semantic_profile
is_unsupported_temperature_error = ai_service._is_unsupported_temperature_error
needs_openai_min_token_budget = ai_service._needs_openai_min_token_budget
openai_token_budget = ai_service._openai_token_budget
openai_output_text = ai_service._openai_output_text
parse_cube_selection_json = ai_service._parse_cube_selection_json
validate_generated_mdx = ai_service.validate_generated_mdx
find_clarifications = ai_service.find_clarifications


def _rows(dim_name, values):
    return [{"_dimensions": {dim_name: value}, "value": 1} for value in values]


def test_general_prompt_rules_cover_current_question_focus_and_grounding():
    rules = GENERAL_AGENT_CONTRACT.lower()

    assert "current user question" in rules
    assert "previous conversation is context" in rules
    assert "schema" in rules
    assert "examples as structural patterns" in rules
    assert "scope to a specific item" in rules
    assert "developer/application instructions" in rules
    assert "retrieved context" in rules
    assert "validation failures" in rules
    assert "do not expose hidden reasoning" in rules


def test_prompt_management_rules_keep_instructions_and_data_separate():
    rules = PROMPT_MANAGEMENT_RULES.lower()

    assert "fixed instructions" in rules
    assert "variable request data" in rules
    assert "xml-style tags" in rules
    assert "context limits" in rules
    assert "static repeated content first" in rules
    assert "exact prefix matches" in rules
    assert "cached input tokens" in rules
    assert "eval-driven development" in rules
    assert "production logs" in rules
    assert "historical regressions" in rules
    assert "automated pass/fail" in rules
    assert "prompt-optimizer suggestions" in rules
    assert "representative test cases" in rules


def test_reasoning_model_rules_prefer_validators_over_chain_of_thought():
    rules = REASONING_MODEL_RULES.lower()

    assert "ambiguous, multistep" in rules
    assert "simple and direct" in rules
    assert "do not ask the model to reveal chain-of-thought" in rules
    assert "zero-shot" in rules
    assert "deterministic validators/evals" in rules
    assert "code for execution, validation, retries" in rules


def test_specific_focus_rejects_expanding_all_benefits_when_question_names_pension():
    schema = {
        "dimensions": [
            {
                "name": "Benefit",
                "elements": [
                    "Group Insurance",
                    "Hospital Insurance",
                    "Pension Contribution",
                    "Meal Vouchers",
                ],
            },
            {"name": "Grade", "elements": ["Junior", "Manager", "VP"]},
            {"name": "Month", "is_time": True, "elements": ["01", "02", "03"]},
        ]
    }
    rows = _rows(
        "Benefit",
        ["Group Insurance", "Hospital Insurance", "Pension Contribution", "Meal Vouchers"],
    )
    layout = {"row_dimensions": ["Benefit"], "column_dimensions": ["Month"]}

    issue = specific_focus_issue(
        "why Pension contributions are the largest benefit category? show me by months and grades",
        schema,
        rows,
        layout,
    )

    assert issue is not None
    assert "Pension Contribution" in issue
    assert "returned 4 elements" in issue


def test_specific_focus_allows_filtered_focus_with_requested_breakdowns():
    schema = {
        "dimensions": [
            {
                "name": "Benefit",
                "elements": ["Group Insurance", "Pension Contribution", "Meal Vouchers"],
            },
            {"name": "Grade", "elements": ["Junior", "Manager", "VP"]},
            {"name": "Month", "is_time": True, "elements": ["01", "02", "03"]},
        ]
    }
    rows = _rows("Grade", ["Junior", "Manager", "VP"])
    layout = {"row_dimensions": ["Grade"], "column_dimensions": ["Month"]}

    issue = specific_focus_issue(
        "why Pension contributions are the largest benefit category? show me by months and grades",
        schema,
        rows,
        layout,
    )

    assert issue is None


def test_result_shape_rejects_multiple_ancestor_consolidated_time_rollups():
    month_values = [
        "All MTD",
        "01",
        "02",
        "03",
        "04",
        "05",
        "All MTD FY",
        "All QTR",
        "Q1",
        "Q2",
    ]
    rows = _rows("Month", month_values)
    layout = {"column_dimensions": ["Month"], "row_dimensions": ["Grade"]}
    dim_meta = {
        "Month": {
            "is_time_dim": True,
            "consolidated": {"All MTD", "All MTD FY", "All QTR", "Q1", "Q2"},
        }
    }
    hierarchy_edges = {
        "Month": [
            ("All MTD FY", "All MTD"),
            ("All QTR", "Q1"),
            ("All QTR", "Q2"),
            ("Q1", "01"),
            ("Q1", "02"),
            ("Q1", "03"),
            ("Q2", "04"),
            ("Q2", "05"),
        ]
    }

    issue = result_shape_issue(rows, layout, dim_meta, hierarchy_edges)

    assert issue is not None
    assert "multiple ancestor consolidated elements" in issue
    assert "Do not use .Members" in issue


def test_result_shape_allows_sibling_summary_level_without_ancestor_pairs():
    values = ["Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "Budget FY"]
    rows = _rows("Period View", values)
    layout = {"column_dimensions": ["Period View"], "row_dimensions": ["Grade"]}
    dim_meta = {
        "Period View": {
            "is_time_dim": True,
            "consolidated": {"Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "Budget FY"},
        }
    }
    hierarchy_edges = {
        "Period View": [
            ("Q1", "01"),
            ("Q2", "04"),
            ("Q3", "07"),
            ("Q4", "10"),
            ("H1", "01"),
            ("H2", "07"),
            ("FY", "01"),
            ("Budget FY", "Budget Q1"),
        ]
    }

    assert result_shape_issue(rows, layout, dim_meta, hierarchy_edges) is None


def test_mock_llm_output_eval_repairs_pension_focus_from_all_benefits_to_filtered_item():
    bad_llm_mdx = (
        "SELECT {[Month].[Month].[01], [Month].[Month].[02]} ON COLUMNS, "
        "{[Benefit].[Benefit].[Group Insurance], [Benefit].[Benefit].[Hospital Insurance], "
        "[Benefit].[Benefit].[Pension Contribution], [Benefit].[Benefit].[Meal Vouchers]} "
        "* {[Grade].[Grade].[Junior], [Grade].[Grade].[Manager]} ON ROWS "
        "FROM [Benefits] WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[ACT])"
    )
    repaired_mdx = (
        "SELECT {[Month].[Month].[01], [Month].[Month].[02]} ON COLUMNS, "
        "{[Grade].[Grade].[Junior], [Grade].[Grade].[Manager]} ON ROWS "
        "FROM [Benefits] WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[ACT], "
        "[Benefit].[Benefit].[Pension Contribution])"
    )
    schema = {
        "dimensions": [
            {
                "name": "Benefit",
                "elements": [
                    "Group Insurance",
                    "Hospital Insurance",
                    "Pension Contribution",
                    "Meal Vouchers",
                ],
            },
            {"name": "Grade", "elements": ["Junior", "Manager"]},
            {"name": "Month", "is_time": True, "elements": ["01", "02"]},
        ]
    }
    question = "why Pension contributions are the largest benefit category? show me by months and grades"

    bad_rows = [
        {"_dimensions": {"Benefit": benefit, "Grade": "Junior", "Month": "01"}, "value": 1}
        for benefit in [
            "Group Insurance",
            "Hospital Insurance",
            "Pension Contribution",
            "Meal Vouchers",
        ]
    ]
    bad_layout = {"row_dimensions": ["Benefit", "Grade"], "column_dimensions": ["Month"]}
    repaired_rows = _rows("Grade", ["Junior", "Manager"])
    repaired_layout = {"row_dimensions": ["Grade"], "column_dimensions": ["Month"]}

    assert "[Benefit].[Benefit].[Pension Contribution]" in bad_llm_mdx
    assert "Meal Vouchers" in bad_llm_mdx
    assert specific_focus_issue(question, schema, bad_rows, bad_layout) is not None

    assert "[Benefit].[Benefit].[Pension Contribution])" in repaired_mdx
    assert "Meal Vouchers" not in repaired_mdx
    assert specific_focus_issue(question, schema, repaired_rows, repaired_layout) is None


def test_mock_llm_output_eval_repairs_month_members_to_explicit_relevant_periods():
    bad_llm_mdx = (
        "SELECT {[Month].[Month].Members} ON COLUMNS, "
        "{[Grade].[Grade].[Junior], [Grade].[Grade].[Manager]} ON ROWS "
        "FROM [Headcount] WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[ACT])"
    )
    repaired_mdx = (
        "SELECT {[Month].[Month].[All MTD], [Month].[Month].[01], [Month].[Month].[02], "
        "[Month].[Month].[03], [Month].[Month].[04], [Month].[Month].[05]} ON COLUMNS, "
        "{[Grade].[Grade].[Junior], [Grade].[Grade].[Manager]} ON ROWS "
        "FROM [Headcount] WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[ACT])"
    )
    layout = {"column_dimensions": ["Month"], "row_dimensions": ["Grade"]}
    dim_meta = {
        "Month": {
            "is_time_dim": True,
            "consolidated": {"All MTD", "All MTD FY", "All QTR", "Q1", "Q2"},
        }
    }
    hierarchy_edges = {
        "Month": [
            ("All MTD FY", "All MTD"),
            ("All QTR", "Q1"),
            ("All QTR", "Q2"),
            ("Q1", "01"),
            ("Q1", "02"),
            ("Q1", "03"),
            ("Q2", "04"),
            ("Q2", "05"),
        ]
    }
    bad_rows = _rows(
        "Month",
        ["All MTD", "01", "02", "03", "04", "05", "All MTD FY", "All QTR", "Q1", "Q2"],
    )
    repaired_rows = _rows("Month", ["All MTD", "01", "02", "03", "04", "05"])

    assert ".Members" in bad_llm_mdx
    assert result_shape_issue(bad_rows, layout, dim_meta, hierarchy_edges) is not None

    assert ".Members" not in repaired_mdx
    assert "[Month].[Month].[All MTD FY]" not in repaired_mdx
    assert "[Month].[Month].[All QTR]" not in repaired_mdx
    assert result_shape_issue(repaired_rows, layout, dim_meta, hierarchy_edges) is None


def test_statement_question_requires_account_line_items_on_rows():
    schema = {
        "dimensions": [
            {"name": "Account", "elements": ["Total P&L", "Revenue", "COGS", "Gross Profit"]},
            {"name": "Store", "elements": ["All Stores", "Store 01"]},
            {"name": "Month", "is_time": True, "elements": ["05"]},
        ]
    }
    bad_layout = {
        "row_dimensions": ["Store"],
        "column_dimensions": ["Month"],
        "applied_filters": [{"dimension": "Account", "element": "Total P&L"}],
    }
    good_layout = {
        "row_dimensions": ["Account"],
        "column_dimensions": ["Month"],
        "applied_filters": [{"dimension": "Store", "element": "All Stores"}],
    }

    bad_issue = statement_line_item_issue("I would like to see the P&L statement", schema, bad_layout)
    good_issue = statement_line_item_issue("I would like to see the P&L statement", schema, good_layout)

    assert bad_issue is not None
    assert "Account" in bad_issue
    assert good_issue is None


def test_finance_semantics_maps_pnl_without_model_specific_rules():
    schema_summary = {
        "cubes": [
            {
                "cube": "Financial Reporting",
                "description": "Income statement and profitability reporting",
                "dimensions": ["Reporting Line", "Store", "Month", "Scenario"],
                "dimension_elements": {
                    "Reporting Line": [
                        "Net Revenue",
                        "COGS",
                        "Gross Profit",
                        "Operating Expense",
                        "Operating Profit",
                    ],
                    "Store": ["All Stores", "Store 01"],
                },
                "measure_dimension": "Measure",
                "measures": ["Value", "Units"],
                "attributes": ["Statement", "Section"],
            }
        ]
    }

    profile = build_finance_semantic_profile(schema_summary)
    income_statement = profile["concepts"]["income_statement"]

    assert income_statement["primary_cube"] == "Financial Reporting"
    assert income_statement["line_item_dimension"] == "Reporting Line"
    assert "Value" in income_statement["preferred_measures"]
    assert income_statement["confidence"] >= 0.5


def test_finance_semantics_handles_explicit_pnl_cube_and_account_dimension():
    schema_summary = {
        "cubes": [
            {
                "cube": "P&L",
                "description": "Profit and loss by store",
                "dimensions": ["Account", "Store", "Month", "Scenario"],
                "dimension_elements": {
                    "Account": ["Revenue", "COGS", "Gross Profit", "Net Income"],
                    "Store": ["All Stores", "Store 01"],
                },
                "measure_dimension": "Measure",
                "measures": ["Amount"],
                "attributes": [],
            }
        ]
    }

    profile = build_finance_semantic_profile(schema_summary)
    income_statement = profile["concepts"]["income_statement"]

    assert income_statement["primary_cube"] == "P&L"
    assert income_statement["line_item_dimension"] == "Account"
    assert income_statement["confidence"] >= 0.7


def test_unsupported_temperature_error_is_detected():
    exc = RuntimeError("Unsupported parameter: 'temperature' is not supported with this model.")

    assert is_unsupported_temperature_error(exc)


def test_gpt5_models_use_larger_token_budget():
    assert needs_openai_min_token_budget("gpt-5.5")
    assert needs_openai_min_token_budget("gpt-5-mini")
    assert needs_openai_min_token_budget("o3")
    assert not needs_openai_min_token_budget("gpt-4.1")
    assert openai_token_budget(800, "gpt-5.5") == 4096
    assert openai_token_budget(800, "gpt-4.1") == 800


def test_empty_openai_response_reports_token_budget_exhaustion():
    class Details:
        reason = "max_output_tokens"

    class Response:
        output_text = ""
        status = "incomplete"
        incomplete_details = Details()

    try:
        openai_output_text(Response())
    except RuntimeError as exc:
        assert "output token budget" in str(exc)
    else:
        raise AssertionError("incomplete empty output should explain token budget exhaustion")


def test_cube_selection_parser_accepts_compact_json():
    parsed = parse_cube_selection_json(
        '{"cubes":[{"cube":"Consol GL Company Entry","reasoning":"P&L source"}],"reasoning":"best match"}'
    )

    assert parsed["cubes"][0]["cube"] == "Consol GL Company Entry"


def test_empty_generated_mdx_is_rejected_before_tm1_call():
    try:
        validate_generated_mdx("", "Consol GL Company")
    except RuntimeError as exc:
        assert "empty MDX" in str(exc)
    else:
        raise AssertionError("empty MDX should fail validation")


def test_pnl_statement_question_requires_scenario_and_period_without_history_defaults():
    clarification = find_clarifications("show me SLIM HK P&L in 2024", [])

    assert clarification
    assert "Scenario" in clarification or "scenario" in clarification
    assert "period" in clarification.lower() or "full year" in clarification.lower() or "month" in clarification.lower()


def test_history_can_supply_scenario_and_period_for_followups():
    history = [{"question": "show me actual full year revenue in 2025", "analysis": "Done"}]

    clarification = find_clarifications("show me SLIM HK P&L in 2024", history)

    assert clarification is None


def test_no_usable_data_message_is_user_friendly():
    message, detail = no_usable_data_message([
        {"cube": "Consol GL Company Entry", "status": "no data"},
        {"cube": "Consol GL Company", "status": "error: raw tm1 detail"},
    ])

    assert "No usable data" not in message
    assert "Cannot execute MDX" not in message
    assert "Try confirming" in message
    assert "Consol GL Company Entry" in detail


def test_default_filter_validator_requires_default_element_for_unspecified_dimensions():
    schema = {
        "dimensions": [
            {"name": "Company", "is_measure": False, "is_time_dim": False, "default_element": "All Companies", "top_consolidations": ["All Companies"]},
            {"name": "Segment", "is_measure": False, "is_time_dim": False, "default_element": "All Segments", "top_consolidations": ["All Segments"]},
            {"name": "Measure", "is_measure": True, "default_element": "Amount"},
        ]
    }
    mdx = (
        "SELECT {[Measure].[Measure].[Amount]} ON COLUMNS "
        "FROM [Cube] "
        "WHERE ([Company].[Company].[SLIM-HK], [Segment].[Segment].[Retail])"
    )

    issue = default_filter_issue("show me SLIM HK P&L", schema, mdx)

    assert issue
    assert "Segment" in issue
    assert "All Segments" in issue


def test_default_filter_validator_keeps_user_named_elements():
    schema = {
        "dimensions": [
            {"name": "Company", "is_measure": False, "is_time_dim": False, "default_element": "All Companies", "top_consolidations": ["All Companies"]},
            {"name": "Measure", "is_measure": True, "default_element": "Amount"},
        ]
    }
    mdx = (
        "SELECT {[Measure].[Measure].[Amount]} ON COLUMNS "
        "FROM [Cube] "
        "WHERE ([Company].[Company].[SLIM-HK])"
    )

    assert default_filter_issue("show me SLIM HK P&L", schema, mdx) is None
