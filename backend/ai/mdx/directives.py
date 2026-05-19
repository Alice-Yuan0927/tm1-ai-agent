"""Directive-text builders that augment MDX prompts with cube/profile context."""

import json
import re

from ..schema.tm1_lexicon import STATEMENT_PATTERN

_STATEMENT_QUESTION_RE = re.compile(
    rf"(?:{STATEMENT_PATTERN.pattern}|balance\s+sheet|cash\s+flow|trial\s+balance)",
    re.IGNORECASE,
)


_SPECIFIC_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2}\s*月"
    r"|q[1-4]|quarter|month\s+\d)",
    re.IGNORECASE,
)


def profile_defaults_directive(
    cube_schema: dict,
    model_profile: dict | None,
    question: str = "",
) -> str:
    """Emit a hard directive listing per-dim WHERE overrides from the
    profile's `default_filters`. These pinned values come from the user or
    known-good probes and must be used unless the user named something
    different — this prevents the LLM from using a mechanical schema default
    on a consolidation with no data feed."""
    from .planner import _profile_dim_defaults, _best_all_period
    overrides = _profile_dim_defaults(model_profile)
    schema_dims = {str(d.get("name", "")): d for d in (cube_schema.get("dimensions") or [])}
    relevant = {k: v for k, v in overrides.items() if k in schema_dims}

    # For period/month dims excluded from _profile_dim_defaults, inject the
    # all-periods consolidation unless the question explicitly names a month.
    # Aligns with mdx_hard_rules rule 26: when no specific month/quarter is
    # mentioned, the LLM should use the all-period aggregate, not a leaf like
    # "00" or the profile's current_month.
    has_specific_month = bool(_SPECIFIC_MONTH_RE.search(question or ""))
    if not has_specific_month:
        for dim in (cube_schema.get("dimensions") or []):
            dim_name = str(dim.get("name", ""))
            if not dim.get("is_time_dim") or dim_name in relevant:
                continue
            if not any(kw in dim_name.lower() for kw in ("month", "period")):
                continue
            consolidations: list[str] = [str(e) for e in dim.get("consolidations", [])]
            all_period = _best_all_period(consolidations, dim_name)
            if all_period:
                relevant[dim_name] = all_period

    if not relevant:
        return ""
    lines = "\n".join(
        f"  - [{name}].[{name}].[{element}]"
        for name, element in relevant.items()
    )
    return (
        "\nProfile default-filter overrides (HARD RULE: use these in WHERE "
        "for the named dimensions unless the user explicitly mentioned a "
        "different element for the same dimension - they are the cube's "
        "known data-bearing defaults, NOT the schema's mechanical "
        "default_element):\n"
        f"{lines}\n"
    )


def line_item_directive(question: str, cube_schema: dict) -> str:
    """For financial-statement questions, lock the source-of-truth line-item
    dim onto ROWS using its top consolidation."""
    if not _STATEMENT_QUESTION_RE.search(question or ""):
        return ""
    from .planner import _resolve_line_item_dim
    dim = _resolve_line_item_dim(cube_schema.get("dimensions", []) or [], None)
    if not dim:
        return ""
    name = str(dim.get("name", ""))
    top = (
        (dim.get("top_consolidations") or [None])[0]
        or dim.get("default_element")
        or ""
    )
    if not name or not top:
        return ""
    return (
        f"\nFinancial-statement line-item directive (HARD RULE for this question):\n"
        f"- Put dimension [{name}] on ROWS using "
        f"Descendants([{name}].[{name}].[{top}]).\n"
        f"- This dim was chosen because its elements/consolidations contain the most "
        f"P&L-style names (revenue, cost, expense, depreciation, etc.).\n"
        f"- Do NOT put any other similarly-named dimension on ROWS (e.g. a separate "
        f"reporting / disclosure dim whose elements are just codes or layout labels). "
        f"Filter those in WHERE to their default_element.\n"
    )


def consolidated_member_directive(
    question: str,
    grounded_members: list[dict] | None,
    cube_schema: dict | None = None,
) -> str:
    """For a directly named consolidated member, show that member plus its
    immediate children instead of expanding the whole descendant tree."""
    if not grounded_members or _STATEMENT_QUESTION_RE.search(question or ""):
        return ""

    schema_dims = {
        str(dim.get("name", "")): dim
        for dim in (cube_schema or {}).get("dimensions", []) or []
        if dim.get("name")
    }
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in grounded_members:
        dim_name = str(item.get("dimension", ""))
        element = str(item.get("element", ""))
        if not dim_name or not element:
            continue
        key = (dim_name, element)
        if key in seen:
            continue
        seen.add(key)
        element_type = str(item.get("element_type", ""))
        dim = schema_dims.get(dim_name, {})
        consolidations = {str(e).lower() for e in dim.get("consolidations", []) or []}
        is_consolidated = (
            element_type.lower() == "consolidated"
            or element.lower() in consolidations
        )
        if not is_consolidated:
            continue
        member = f"[{dim_name}].[{dim_name}].[{element}]"
        lines.append(
            f"- If [{dim_name}] is used on an axis for '{element}', use "
            f"DRILLDOWNLEVEL({{{member}}}). If this TM1 version rejects "
            f"DRILLDOWNLEVEL, fall back to Union({{{member}}}, {member}.Children). "
            f"Do NOT use Descendants({member}, 99) for this focused request."
        )
        if len(lines) >= 5:
            break

    if not lines:
        return ""
    return (
        "\nNamed consolidated-member directive (HARD RULE for this question):\n"
        "The user named a consolidated element. Show the named parent and its "
        "immediate children only, so the result stays focused and bounded. "
        "This follows TM1 subset MDX drilldown semantics: DRILLDOWNLEVEL returns "
        "the parent plus immediate children, while recursive Descendants / "
        "TM1DRILLDOWNMEMBER can return a large full tree.\n"
        + "\n".join(lines)
        + "\n"
    )


def grounded_members_section(
    grounded_members: list[dict] | None,
    cube_schema: dict | None = None,
    model_profile: dict | None = None,
) -> str:
    if not grounded_members:
        return ""
    from ..schema.dim_roles import accepts_entity_name, get_dim_role

    profile_roles = (model_profile or {}).get("dim_roles") or {}
    dim_by_name = {
        str(dim.get("name", "")): dim
        for dim in (cube_schema or {}).get("dimensions", []) or []
        if dim.get("name")
    }
    compact = [
        _grounded_member_prompt_item(item, dim_by_name, profile_roles, get_dim_role, accepts_entity_name)
        for item in grounded_members[:40]
    ]
    return (
        "\nGrounded member candidates from the current question "
        "(HARD RULE: use exact unique_name values for concrete members. "
        "Entity/company mentions may only use candidates marked "
        "accepted_for_entity_mentions=true):\n"
        f"{json.dumps(compact, indent=2, ensure_ascii=False)}\n"
    )


def _grounded_member_prompt_item(
    item: dict,
    dim_by_name: dict[str, dict],
    profile_roles: dict,
    get_dim_role,
    accepts_entity_name,
) -> dict:
    dim_name = str(item.get("dimension", ""))
    role = get_dim_role(dim_by_name.get(dim_name, {"name": dim_name}), profile_roles)
    return {
        "dimension": dim_name,
        "role": role,
        "accepted_for_entity_mentions": bool(accepts_entity_name(role)),
        "element": item.get("element", ""),
        "element_type": item.get("element_type", ""),
        "unique_name": item.get("unique_name", ""),
        "matched_text": item.get("matched_text", ""),
        "matched_by": item.get("matched_by", ""),
    }


def schema_dimensions_for_prompt(
    dimensions: list[dict],
    grounded_members: list[dict] | None = None,
) -> list[dict]:
    """Compact schema view that drops irrelevant element_attr_values."""
    grounded_by_dim: dict[str, set[str]] = {}
    for item in grounded_members or []:
        dim = str(item.get("dimension", ""))
        elem = str(item.get("element", ""))
        if dim and elem:
            grounded_by_dim.setdefault(dim, set()).add(elem)

    prompt_dims: list[dict] = []
    for dim in dimensions or []:
        dim_name = str(dim.get("name", ""))
        item = dict(dim)
        item.pop("child_weights", None)

        keep_elements = set(str(e) for e in item.get("elements", []) or [])
        keep_elements.update(str(e) for e in item.get("consolidations", []) or [])
        keep_elements.update(str(e) for e in item.get("top_consolidations", []) or [])
        if item.get("default_element"):
            keep_elements.add(str(item["default_element"]))
        keep_elements.update(grounded_by_dim.get(dim_name, set()))

        attr_values = item.get("element_attr_values") or {}
        if isinstance(attr_values, dict):
            filtered_attrs = {
                str(element): values
                for element, values in attr_values.items()
                if str(element) in keep_elements
            }
            if filtered_attrs:
                item["element_attr_values"] = filtered_attrs
            else:
                item.pop("element_attr_values", None)
        prompt_dims.append(item)
    return prompt_dims
