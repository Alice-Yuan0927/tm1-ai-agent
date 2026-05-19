"""Consolidation-expansion MDX sub-planner."""

from __future__ import annotations

from .scenario import _resolve_scenario_axis
from .shared import (
    MdxPlan,
    _BY_MONTH_PATTERN,
    _BY_QUARTER_PATTERN,
    _BY_SCENARIO_PATTERN,
    _YEAR_PATTERN,
    _detect_currency_view,
    _estimate_descendants,
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

# Descendant count threshold: ≤ this → full Descendants() expansion;
# > this → member.Children (single level) to avoid thousands of rows.
_EXPANSION_THRESHOLD = 150


def _try_consolidation_expansion_plan(
    question: str,
    schema: dict,
    model_profile: dict | None,
    element_matches: list[tuple[str, str, str]],
) -> MdxPlan | None:
    """Build an expansion plan when the user names a consolidated element.

    Fires when element_matches contains a consolidation in a line_item or
    business_classifier dim. Depth chosen automatically based on descendant count.
    """
    from ...schema.dim_roles import get_dim_role
    from ....tm1.cache.read import is_consolidated_element as _is_consolidated

    cube_name = str(schema.get("cube", ""))
    dimensions = list(schema.get("dimensions", []))
    if not cube_name or not dimensions:
        return None

    profile_roles = (model_profile or {}).get("dim_roles") or {}
    dim_by_name = {str(d.get("name", "")): d for d in dimensions if d.get("name")}

    target_dim: dict | None = None
    target_element: str = ""
    for _phrase, dim_name, element_name in element_matches:
        dim = dim_by_name.get(dim_name)
        if not dim:
            continue
        role = get_dim_role(dim, profile_roles)
        if role not in ("line_item", "business_classifier"):
            continue
        try:
            if _is_consolidated(dim_name, element_name):
                target_dim = dim
                target_element = element_name
                break
        except Exception:
            continue

    if not target_dim or not target_element:
        return None

    dim_name = target_dim["name"]
    desc_count = _estimate_descendants(dim_name, target_element, limit=_EXPANSION_THRESHOLD + 1)
    if desc_count > _EXPANSION_THRESHOLD:
        rows_expr = f"{{{_mdx_member(dim_name, target_element)}.Children}}"
        depth_note = "children only (large hierarchy)"
    else:
        rows_expr = f"{{Descendants({_mdx_member(dim_name, target_element)})}}"
        depth_note = "full hierarchy"

    assumptions: list[str] = [f"Expanding **{dim_name}.{target_element}** ({depth_note})"]
    confidence = 0.75

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
            assumptions.append("Scenario set on columns: " + " + ".join(f"**{e}**" for e in scenario_elements))
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

    measure_dim = next((d for d in dimensions if d.get("is_measure")), None)
    finance_semantics = _get_finance_concept(model_profile, "income_statement")
    measure_element, _ = _pick_measure(measure_dim, finance_semantics)

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

    explicit_names = {d["name"] for d in (target_dim, measure_dim, year_dim, period_dim, scenario_dim) if d}
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

    mdx = (
        f"SELECT {cols} ON COLUMNS, {rows_expr} ON ROWS "
        f"FROM [{cube_name}] "
        f"WHERE ({', '.join(where_parts)})"
    )

    confidence = max(0.0, min(1.0, confidence))
    return MdxPlan(
        mdx=mdx,
        confidence=confidence,
        pattern="consolidation_expansion",
        assumptions=assumptions,
    )
