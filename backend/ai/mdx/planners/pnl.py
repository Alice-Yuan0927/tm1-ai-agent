"""P&L / income-statement MDX sub-planner."""

from __future__ import annotations

import re

from ...schema.tm1_lexicon import STATEMENT_PATTERN
from .scenario import _resolve_scenario_axis
from .shared import (
    MdxPlan,
    _BY_MONTH_PATTERN,
    _BY_QUARTER_PATTERN,
    _BY_SCENARIO_PATTERN,
    _YEAR_PATTERN,
    _dedup_preserve,
    _detect_currency_view,
    _get_finance_concept,
    _matches_by_dim,
    _mdx_member,
    _pick_currency_code_element,
    _pick_currency_view_element,
    _pick_measure,
    _pick_period,
    _pick_year,
    _profile_dim_defaults,
    _split_time_dims,
)

_PNL_PATTERN = STATEMENT_PATTERN

_LINE_ITEM_NAME_HINTS = ("account", "line item", "chart of accounts", "p&l account", "gl")

_PNL_ELEMENT_TOKENS = (
    "revenue", "sales", "income", "cost", "expense", "expenses",
    "gross profit", "operating profit", "operating expense", "operating income",
    "net income", "net profit", "ebitda", "ebit", "earnings", "margin",
    "depreciation", "amortization", "amortisation", "impairment", "impairm",
    "interest", "tax", "taxes", "deferred",
    "cogs", "cost of goods", "cost of sales",
    "salary", "salaries", "wages", "compensation", "benefits", "bonus",
    "rent", "utilities", "marketing", "advertising",
    "loss", "profit", "fee", "fees", "charge", "charges",
)

# Ordered by specificity: full statement names before bottom lines so that
# "show P&L" expands the statement hierarchy when such a parent exists.
_PNL_PARENT_HINTS = [
    "income statement",
    "profit and loss",
    "profit loss statement",
    "p and l",
    "pnl",
    "p l statement",
    "net income",
    "net profit",
    "net earnings",
    "profit after tax",
    "loss after tax",
    "earnings after tax",
    "result for year",
    "result for period",
    "total comprehensive income",
    "comprehensive income",
    "profit before tax",
    "loss before tax",
    "earnings before tax",
    "ebit",
    "ebitda",
    "operating profit",
    "operating income",
    "operating result",
    "gross profit",
    "gross margin",
    "all account",
]

_PNL_QUALIFIER_RULES: list[tuple[str, set[str], str]] = [
    (r"\b(before|pre)[\s\-]*tax\b|\bpbt\b",            {"before", "tax"},       "before tax"),
    (r"\b(after|post)[\s\-]*tax\b|\bpat\b",            {"after", "tax"},        "after tax"),
    (r"\bebitda\b",                                     {"ebitda"},              "EBITDA"),
    (r"\bebit\b(?!da)",                                 {"ebit"},                "EBIT"),
    (r"\boperating\s+profit\b",                         {"operating", "profit"}, "operating profit"),
    (r"\boperating\s+income\b",                         {"operating", "income"}, "operating income"),
    (r"\boperating\s+result\b",                         {"operating", "result"}, "operating result"),
    (r"\bgross\s+profit\b",                             {"gross", "profit"},     "gross profit"),
    (r"\bgross\s+margin\b",                             {"gross", "margin"},     "gross margin"),
    (r"\b(total\s+)?comprehensive\s+income\b",          {"comprehensive", "income"}, "comprehensive income"),
    (r"\bnet\s+income\b",                               {"net", "income"},       "net income"),
    (r"\bnet\s+profit\b",                               {"net", "profit"},       "net profit"),
    (r"\bnet\s+earnings\b",                             {"net", "earnings"},     "net earnings"),
]


# ---------------------------------------------------------------------------
# Line-item dim detection
# ---------------------------------------------------------------------------

def _score_line_item_dim(dim: dict) -> int:
    """Count P&L-flavoured tokens across the dim's elements + consolidations."""
    bag: list[str] = []
    bag.extend(str(e) for e in dim.get("elements", []) or [])
    bag.extend(str(e) for e in dim.get("consolidations", []) or [])
    bag.extend(str(e) for e in dim.get("top_consolidations", []) or [])
    text = " ".join(b.lower() for b in bag)
    return sum(1 for token in _PNL_ELEMENT_TOKENS if token in text)


def _resolve_line_item_dim(dimensions: list[dict], finance_semantics: dict | None) -> dict | None:
    """Pick the dim whose element content is most P&L-flavoured."""
    candidates: list[tuple[int, int, dict]] = []
    for index, dim in enumerate(dimensions):
        if dim.get("is_measure"):
            continue
        name = str(dim.get("name", "")).lower()
        name_match = any(hint in name for hint in _LINE_ITEM_NAME_HINTS)
        element_score = _score_line_item_dim(dim)
        if not name_match and element_score == 0:
            continue
        score = element_score * 3 + (1 if name_match else 0)
        candidates.append((score, -index, dim))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


# ---------------------------------------------------------------------------
# P&L top-consolidation picker
# ---------------------------------------------------------------------------

def _detect_pnl_qualifier(question: str) -> tuple[set[str], str] | None:
    text = (question or "").lower()
    for pattern, must_match, label in _PNL_QUALIFIER_RULES:
        if re.search(pattern, text):
            return must_match, label
    return None


def _pnl_hint_score(candidate: str, hint: str) -> int:
    """Return specificity score if all hint words appear in the candidate name."""
    normalised = re.sub(r"\s*&\s*", " and ", candidate.lower())
    cand_words = set(re.findall(r"[a-z]+", normalised))
    hint_words = set(re.findall(r"[a-z]+", hint.lower()))
    if not hint_words or not hint_words.issubset(cand_words):
        return 0
    return len(hint_words)


def _pick_pnl_top_consolidation(line_dim: dict, question: str = "") -> tuple[str, str]:
    """Return (chosen_element, status). Status: 'exact' / 'qualifier_missing' / 'fuzzy'."""
    tops = list(line_dim.get("top_consolidations") or [])
    cons = list(line_dim.get("consolidations") or [])
    candidates = _dedup_preserve(tops + cons)
    if not candidates:
        return "", ""
    if len(candidates) == 1:
        return candidates[0], "exact"

    qualifier = _detect_pnl_qualifier(question)
    if qualifier is not None:
        qualifier_tokens, _label = qualifier
        for candidate in candidates:
            cand_words = set(re.findall(r"[a-z]+", candidate.lower()))
            if qualifier_tokens.issubset(cand_words):
                return candidate, "exact"
        fallback_status = "qualifier_missing"
    else:
        fallback_status = "fuzzy"

    for hint in _PNL_PARENT_HINTS:
        best: tuple[int, str] = (0, "")
        for candidate in candidates:
            score = _pnl_hint_score(candidate, hint)
            if score > best[0]:
                best = (score, candidate)
        if best[0]:
            return best[1], fallback_status if fallback_status == "qualifier_missing" else "exact"

    default = str(line_dim.get("default_element", "")).strip()
    if default and default in candidates:
        return default, "exact"
    return candidates[0], "fuzzy"


# ---------------------------------------------------------------------------
# Main P&L planner
# ---------------------------------------------------------------------------

def _try_pnl_plan(
    question: str,
    schema: dict,
    model_profile: dict | None,
    element_matches: list[tuple[str, str, str]],
) -> MdxPlan | None:
    if not _PNL_PATTERN.search(question):
        return None

    cube_name = str(schema.get("cube", ""))
    dimensions = list(schema.get("dimensions", []))
    if not cube_name or not dimensions:
        return None

    confidence = 1.0
    assumptions: list[str] = []

    finance_semantics = _get_finance_concept(model_profile, "income_statement")
    if not finance_semantics or finance_semantics.get("confidence", 0) < 0.4:
        confidence -= 0.25

    line_dim = _resolve_line_item_dim(dimensions, finance_semantics)
    if not line_dim:
        return None
    top_consolidation, picked_by = _pick_pnl_top_consolidation(line_dim, question)
    if not top_consolidation:
        return None
    if picked_by == "qualifier_missing":
        confidence -= 0.20
        qualifier_label = (_detect_pnl_qualifier(question) or (set(), ""))[1]
        assumptions.append(
            f"Line items: expanding **{line_dim['name']}.{top_consolidation}** "
            f"(you asked for **{qualifier_label}**, but this cube has no consolidation "
            f"with that name — fell back to the default P&L bottom line)"
        )
    elif picked_by == "fuzzy":
        confidence -= 0.10
        assumptions.append(
            f"Line items: expanding **{line_dim['name']}.{top_consolidation}** "
            f"(picked the top consolidation; reply with a different parent if you meant another section)"
        )
    else:
        assumptions.append(f"Line items: expanding **{line_dim['name']}.{top_consolidation}**")

    measure_dim = next((d for d in dimensions if d.get("is_measure")), None)
    measure_element, measure_picked = _pick_measure(measure_dim, finance_semantics)
    if measure_dim and not measure_element:
        return None
    if measure_dim and measure_picked == "default":
        confidence -= 0.05
        assumptions.append(f"Measure: **{measure_element}** (default for cube)")
    elif measure_dim and measure_picked == "profile":
        assumptions.append(f"Measure: **{measure_element}**")

    year_dim, period_dim = _split_time_dims(dimensions)
    year_element, year_source = _pick_year(question, model_profile, year_dim)
    if year_dim and not year_element:
        return None

    scenario_dim, scenario_elements, scenario_status = _resolve_scenario_axis(question, dimensions)
    if scenario_status == "unresolved":
        return None

    wants_breakdown_by_month = bool(_BY_MONTH_PATTERN.search(question))
    wants_breakdown_by_quarter = bool(_BY_QUARTER_PATTERN.search(question))
    wants_breakdown_by_scenario = bool(_BY_SCENARIO_PATTERN.search(question))

    year_set: list[str] = []
    if year_dim:
        years_in_question = list(dict.fromkeys(_YEAR_PATTERN.findall(question)))
        elements_lower = {str(e).lower(): str(e) for e in year_dim.get("elements", [])}
        resolved = [elements_lower[y.lower()] for y in years_in_question if y.lower() in elements_lower]
        if len(resolved) >= 2:
            year_set = resolved[:4]

    period_on_columns = bool(period_dim) and (wants_breakdown_by_month or wants_breakdown_by_quarter)
    scenario_on_columns = (
        scenario_dim is not None
        and (len(scenario_elements) >= 2 or wants_breakdown_by_scenario)
    )
    year_on_columns = bool(year_set)

    if year_dim and not year_on_columns:
        if year_source != "question":
            confidence -= 0.10
            assumptions.append(f"Year: **{year_element}** ({year_source})")
        else:
            assumptions.append(f"Year: **{year_element}**")

    if period_on_columns:
        period_parent = (
            period_dim.get("default_element") if year_on_columns
            else (year_element or period_dim.get("default_element"))
        )
        assumptions.append(
            f"Columns: **{period_dim['name']}.{period_parent}.Children**"
            if period_parent else
            f"Columns: **{period_dim['name']}** members"
        )
    if year_on_columns:
        assumptions.append("Year set on columns: " + " + ".join(f"**{y}**" for y in year_set))
    if scenario_on_columns:
        if scenario_elements:
            assumptions.append(
                "Scenario set on columns: " + " + ".join(f"**{e}**" for e in scenario_elements)
            )
        else:
            assumptions.append(f"Columns: **{scenario_dim['name']}** members")

    col_sets: list[str] = []
    if year_on_columns and year_dim:
        col_sets.append(", ".join(_mdx_member(year_dim["name"], y) for y in year_set))
    if period_on_columns and period_dim:
        parent = (
            period_dim.get("default_element") if year_on_columns
            else (year_element or period_dim.get("default_element"))
        )
        if not parent:
            return None
        col_sets.append(f"{_mdx_member(period_dim['name'], parent)}.Children")
    if scenario_on_columns and scenario_dim is not None:
        if scenario_elements:
            col_sets.append(", ".join(_mdx_member(scenario_dim["name"], e) for e in scenario_elements))
        else:
            parent = scenario_dim.get("default_element")
            if not parent:
                return None
            col_sets.append(f"{_mdx_member(scenario_dim['name'], parent)}.Children")
    if not col_sets:
        if year_dim and year_element:
            col_sets.append(_mdx_member(year_dim["name"], year_element))
        else:
            col_sets.append("")
    cols = " * ".join(f"{{{s}}}" for s in col_sets if s)

    cols_dim_names: set[str] = set()
    if year_on_columns and year_dim:
        cols_dim_names.add(year_dim["name"])
    if period_on_columns and period_dim:
        cols_dim_names.add(period_dim["name"])
    if scenario_on_columns and scenario_dim is not None:
        cols_dim_names.add(scenario_dim["name"])
    if col_sets and col_sets[0] != "" and not period_on_columns and not scenario_on_columns and not year_on_columns and year_dim:
        cols_dim_names.add(year_dim["name"])

    profile_roles = (model_profile or {}).get("dim_roles") or {}
    matched_by_dim = _matches_by_dim(element_matches, dimensions, profile_roles)
    where_parts: list[str] = []

    if year_dim and year_dim["name"] not in cols_dim_names and year_element:
        where_parts.append(_mdx_member(year_dim["name"], year_element))
    if period_dim and period_dim["name"] not in cols_dim_names:
        period_filter = _pick_period(
            question, model_profile, period_dim,
            year_on_columns=year_on_columns,
            element_matches=element_matches,
        )
        if not period_filter:
            return None
        assumptions.append(f"Period filter: **{period_filter}**")
        where_parts.append(_mdx_member(period_dim["name"], period_filter))
    if measure_dim and measure_element:
        where_parts.append(_mdx_member(measure_dim["name"], measure_element))
    if scenario_dim and scenario_dim["name"] not in cols_dim_names:
        if len(scenario_elements) == 1:
            assumptions.append(f"Scenario: **{scenario_elements[0]}**")
            where_parts.append(_mdx_member(scenario_dim["name"], scenario_elements[0]))
        else:
            default = scenario_dim.get("default_element")
            if not default:
                return None
            confidence -= 0.05
            where_parts.append(_mdx_member(scenario_dim["name"], default))

    explicit_names = {d["name"] for d in (line_dim, measure_dim, year_dim, period_dim, scenario_dim) if d}
    other_dims = [
        d for d in dimensions
        if d.get("name")
        and d["name"] not in explicit_names
        and d["name"] not in cols_dim_names
    ]
    profile_overrides = _profile_dim_defaults(model_profile)
    currency_view = _detect_currency_view(question)
    for dim in other_dims:
        name = dim.get("name", "")
        if not name:
            continue
        chosen = matched_by_dim.get(name)
        if chosen:
            assumptions.append(f"{name}: **{chosen}** (from question)")
            where_parts.append(_mdx_member(name, chosen))
            continue
        from ...schema.dim_roles import get_dim_role
        role = get_dim_role(dim, profile_roles)
        if role == "currency_view":
            cv_element = _pick_currency_view_element(dim, currency_view)
            if cv_element:
                assumptions.append(f"{name}: **{cv_element}** ({currency_view} currency view)")
                where_parts.append(_mdx_member(name, cv_element))
                continue
        elif role == "currency_code":
            cc_element = _pick_currency_code_element(dim, currency_view, question)
            if cc_element:
                assumptions.append(f"{name}: **{cc_element}** ({currency_view} currency)")
                where_parts.append(_mdx_member(name, cc_element))
                continue
        override = profile_overrides.get(name)
        if override:
            assumptions.append(f"{name}: **{override}** (profile default)")
            where_parts.append(_mdx_member(name, override))
            continue
        default = dim.get("default_element")
        if not default:
            return None
        confidence -= 0.04
        where_parts.append(_mdx_member(name, default))

    rows = f"{{Descendants({_mdx_member(line_dim['name'], top_consolidation)})}}"
    mdx = (
        f"SELECT {cols} ON COLUMNS, {rows} ON ROWS "
        f"FROM [{cube_name}] "
        f"WHERE ({', '.join(where_parts)})"
    )

    confidence = max(0.0, min(1.0, confidence))
    return MdxPlan(
        mdx=mdx,
        confidence=confidence,
        pattern="income_statement",
        assumptions=assumptions,
    )
