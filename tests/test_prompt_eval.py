from backend.ai.schema import finance_semantics, result_validators
from backend.ai.prompts import MDX_HARD_RULES, prompt_rules
from backend.ai.intent.clarification import find_clarifications
from backend.ai.schema.dim_roles import get_dim_role
from backend.ai.mdx.normalize import validate_generated_mdx
from backend.ai.providers.openai_provider import (
    _is_unsupported_temperature_error as is_unsupported_temperature_error,
    _needs_min_token_budget as needs_openai_min_token_budget,
    _output_text as openai_output_text,
    _token_budget as openai_token_budget,
)
from backend.ai.retrieval.rag import _to_structural_template
from backend.response_messages import no_usable_data_message
from backend.util.llm_json import parse_llm_json as parse_cube_selection_json

GENERAL_AGENT_CONTRACT = prompt_rules.GENERAL_AGENT_CONTRACT
PROMPT_MANAGEMENT_RULES = prompt_rules.PROMPT_MANAGEMENT_RULES
REASONING_MODEL_RULES = prompt_rules.REASONING_MODEL_RULES
result_shape_issue = result_validators.result_shape_issue
specific_focus_issue = result_validators.specific_focus_issue
statement_line_item_issue = result_validators.statement_line_item_issue
entity_filter_issue = result_validators.entity_filter_issue
default_filter_warning = result_validators.default_filter_warning
build_finance_semantic_profile = finance_semantics.build_finance_semantic_profile


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


def test_specific_focus_allows_named_consolidation_children():
    schema = {
        "dimensions": [
            {
                "name": "Account",
                "elements": ["Revenue", "Product Revenue", "Service Revenue"],
                "consolidations": ["Revenue"],
            },
            {"name": "Month", "is_time": True, "elements": ["01"]},
        ]
    }
    rows = _rows("Account", ["Revenue", "Product Revenue", "Service Revenue"])
    layout = {"row_dimensions": ["Account"], "column_dimensions": ["Month"]}

    issue = specific_focus_issue("show Revenue", schema, rows, layout)

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




def test_statement_schema_filter_keeps_non_top_profit_and_loss_parent():
    from backend.ai.intent.query_intent import prepare_schema_for_query

    schema = {
        "dimensions": [
            {
                "name": "Account",
                "elements": ["Revenue", "COGS"],
                "consolidations": ["Profit and Loss", "Gross Profit"],
                "top_consolidations": ["All Accounts"],
            }
        ]
    }
    model_profile = {"dim_roles": {"Account": "line_item"}}

    filtered = prepare_schema_for_query(schema, "show P&L", model_profile=model_profile)

    account = filtered["dimensions"][0]
    assert account["top_consolidations"][0] == "Profit and Loss"


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


def test_schema_clarification_uses_semantic_members_for_any_dimension(monkeypatch):
    from backend.ai.intent import clarification

    monkeypatch.setattr(clarification, "find_question_element_matches", lambda _question: [])

    calls = []

    def fake_search(question, candidate_dims=None, top_k=4, threshold=0.68):
        calls.append((question, candidate_dims, top_k, threshold))
        return [
            ("hk region", "Region", "Region 11"),
            ("hk region", "Region", "Region 9"),
            ("slim company", "Company", "SLIM-HK"),
        ]

    monkeypatch.setattr(clarification, "search_by_embedding", fake_search)

    message = clarification.find_schema_clarification(
        "show me the P&L for hk region",
        [{"cube": "P&L", "dimensions": ["Region", "Company", "Account"]}],
        [],
    )

    assert message
    assert "Region.Region 11" in message
    assert "Region.Region 9" in message
    assert "Company.SLIM-HK" in message
    assert calls[0][1] == ["Region", "Company", "Account"]


def test_schema_clarification_skips_semantic_lookup_when_exact_element_exists(monkeypatch):
    from backend.ai.intent import clarification

    monkeypatch.setattr(
        clarification,
        "find_question_element_matches",
        lambda _question: [("SLIM-HK", "Company", "SLIM-HK")],
    )

    def fail_search(*_args, **_kwargs):
        raise AssertionError("semantic lookup should not run when exact elements exist")

    monkeypatch.setattr(clarification, "search_by_embedding", fail_search)

    message = clarification.find_schema_clarification(
        "show me the P&L for SLIM HK",
        [{"cube": "P&L", "dimensions": ["Region", "Company", "Account"]}],
        [],
    )

    assert message is None


def test_no_usable_data_message_is_user_friendly():
    message, detail = no_usable_data_message([
        {"cube": "Consol GL Company Entry", "status": "no data"},
        {"cube": "Consol GL Company", "status": "error: raw tm1 detail"},
    ])

    assert "No usable data" not in message
    assert "Cannot execute MDX" not in message
    assert "Try confirming" in message
    assert "Consol GL Company Entry" in detail


def test_entity_filter_validator_rejects_same_name_non_entity_dimension():
    schema = {
        "dimensions": [
            {"name": "Company", "is_measure": False, "is_time_dim": False, "elements": ["SLIM-HK"], "default_element": "All Companies", "top_consolidations": ["All Companies"]},
            {"name": "Segment", "is_measure": False, "is_time_dim": False, "elements": ["SLIM-HK"], "default_element": "All Segments", "top_consolidations": ["All Segments"]},
            {"name": "Measure", "is_measure": True, "default_element": "Amount"},
        ]
    }
    mdx = (
        "SELECT {[Measure].[Measure].[Amount]} ON COLUMNS "
        "FROM [Cube] "
        "WHERE ([Company].[Company].[All Companies], [Segment].[Segment].[SLIM-HK])"
    )

    issue = entity_filter_issue("show me SLIM HK P&L", schema, mdx)

    assert issue
    assert "Company" in issue
    assert "SLIM-HK" in issue


def test_entity_filter_validator_keeps_user_named_elements():
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

    assert entity_filter_issue("show me SLIM HK P&L", schema, mdx) is None


def test_default_filter_mismatch_is_reported_as_nonfatal_warning():
    schema = {
        "dimensions": [
            {"name": "Segment", "is_measure": False, "is_time_dim": False, "default_element": "All Segments"},
            {"name": "Measure", "is_measure": True, "default_element": "Amount"},
        ]
    }
    model_profile = {"default_filters": {"Segment": "All Segments"}}
    mdx = (
        "SELECT {[Measure].[Measure].[Amount]} ON COLUMNS "
        "FROM [Cube] "
        "WHERE ([Segment].[Segment].[Retail])"
    )

    warning = default_filter_warning("show me P&L", schema, mdx, model_profile=model_profile)

    assert warning
    assert "Segment" in warning
    assert "All Segments" in warning


def test_currency_alias_promotes_data_source_dim_to_currency_view_role():
    schema = {
        "cube": "Consol GL Company",
        "dimensions": [
            {
                "name": "Account",
                "is_measure": False,
                "is_time_dim": False,
                "elements": ["Revenue", "Cost of Sales"],
                "top_consolidations": ["Net Income"],
                "default_element": "Net Income",
            },
            {
                "name": "Year",
                "is_time_dim": True,
                "elements": ["2024"],
                "default_element": "2024",
            },
            {
                "name": "Scenario",
                "is_time_dim": False,
                "elements": ["ACT"],
                "default_element": "ACT",
            },
            {
                "name": "S Consol GL Company",
                "is_time_dim": False,
                "elements": ["All Data Sources List", "EC", "PCT"],
                "default_element": "All Data Sources List",
                "element_attr_values": {
                    "EC": {"Alias": "Entity Currency"},
                    "PCT": {"Alias": "Parent Currency", "Description": "Parent Currency Total"},
                },
            },
            {
                "name": "M Consol GL Company",
                "is_measure": True,
                "elements": ["Amount"],
                "default_element": "Amount",
            },
        ],
    }
    model_profile = {
        "dim_roles": {"S Consol GL Company": "data_source"},
        "finance_semantics": {
            "income_statement": {
                "confidence": 1.0,
                "line_item_dimension": "Account",
                "preferred_measures": ["Amount"],
            }
        },
    }
    source_dim = next(d for d in schema["dimensions"] if d["name"] == "S Consol GL Company")

    assert get_dim_role(source_dim, model_profile["dim_roles"]) == "currency_view"

    assert get_dim_role(source_dim, model_profile["dim_roles"]) == "currency_view"


def test_profile_dim_roles_map_uses_content_based_roles():
    from backend.ai.schema.dim_roles import build_dim_roles_map

    schema_summary = {
        "cubes": [
            {
                "cube": "P&L",
                "dimensions": [
                    {
                        "name": "S Consol GL Company",
                        "elements": ["All Data Sources List", "EC", "PCT"],
                        "element_attr_values": {
                            "EC": {"Alias": "Entity Currency"},
                            "PCT": {"Description": "Parent Currency Total"},
                        },
                    },
                    {
                        "name": "Reporting Currency",
                        "elements": ["USD", "EUR", "HKD", "AUD", "Not a currency"],
                    },
                ],
            }
        ],
    }

    roles = build_dim_roles_map(schema_summary)

    assert roles["S Consol GL Company"] == "currency_view"
    assert roles["Reporting Currency"] == "currency_code"


def test_profile_role_merge_keeps_content_detected_currency_roles():
    from backend.services.model_profile import merge_dim_roles

    merged = merge_dim_roles(
        {"S Consol GL Group": "currency_view", "Scenario": "scenario"},
        {"S Consol GL Group": "data_source", "Scenario": "scenario"},
    )

    assert merged["S Consol GL Group"] == "currency_view"
    assert merged["Scenario"] == "scenario"




def test_grounded_members_section_marks_entity_eligible_candidates():
    from backend.ai.mdx.directives import grounded_members_section
    section = grounded_members_section(
        [
            {"dimension": "Company", "element": "SLIM-HK", "unique_name": "[Company].[Company].[SLIM-HK]"},
            {"dimension": "Segment", "element": "SLIM-HK", "unique_name": "[Segment].[Segment].[SLIM-HK]"},
        ],
        {
            "dimensions": [
                {"name": "Company"},
                {"name": "Segment"},
            ]
        },
    )

    assert '"dimension": "Company"' in section
    assert '"role": "entity_subject"' in section
    assert '"accepted_for_entity_mentions": true' in section
    assert '"dimension": "Segment"' in section
    assert '"role": "business_classifier"' in section
    assert '"accepted_for_entity_mentions": false' in section


def test_consolidated_member_directive_limits_named_parent_to_children():
    from backend.ai.mdx.directives import consolidated_member_directive, grounded_members_section

    grounded = [
        {
            "dimension": "Account",
            "element": "Revenue",
            "element_type": "Consolidated",
            "unique_name": "[Account].[Account].[Revenue]",
        }
    ]
    schema = {
        "dimensions": [
            {
                "name": "Account",
                "consolidations": ["Revenue"],
            }
        ]
    }

    section = grounded_members_section(grounded, schema)
    directive = consolidated_member_directive("show Revenue", grounded, schema)

    assert '"element_type": "Consolidated"' in section
    assert "DRILLDOWNLEVEL({[Account].[Account].[Revenue]})" in directive
    assert "Union({[Account].[Account].[Revenue]}, [Account].[Account].[Revenue].Children)" in directive
    assert "Descendants([Account].[Account].[Revenue], 99)" in directive


def test_mdx_hard_rules_include_relative_period_and_subset_guidance():
    assert "LastPeriods(N, [Dim].[Dim].[Period])" in MDX_HARD_RULES
    assert ".NextMember / .PrevMember" in MDX_HARD_RULES
    assert 'TM1SubsetToSet([Dim], "Subset Name")' in MDX_HARD_RULES
    assert 'TM1Member(TM1SubsetToSet([Dim], "Subset Name").Item(0), 0)' in MDX_HARD_RULES


def test_rag_structural_template_does_not_introduce_members_expansion():
    mdx = (
        "SELECT {[Month].[Month].[01], [Month].[Month].[02]} ON COLUMNS, "
        "{Descendants([Account].[Account].[Net Income], 99, LEAVES)} ON ROWS "
        "FROM [P&L] WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[Actual])"
    )

    template = _to_structural_template(mdx)

    assert ".Members" not in template
    assert "Descendants([Account].[Account].[?], 99, LEAVES)" in template
    assert "[Month].[Month].[?]" in template
