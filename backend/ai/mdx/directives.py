"""Directive-text builders that augment MDX prompts with cube/profile context."""

import json
import re

from ..schema.tm1_lexicon import CURRENCY_VIEW_PATTERNS, STATEMENT_PATTERN

_STATEMENT_QUESTION_RE = re.compile(
    rf"(?:{STATEMENT_PATTERN.pattern}|balance\s+sheet|cash\s+flow|trial\s+balance)",
    re.IGNORECASE,
)

_SPECIFIC_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2}\s*月"
    r"|q[1-4]|quarter|month\s+\d)",
    re.IGNORECASE,
)

# Keys in default_filters that are time/reserved and not generic dim overrides.
_RESERVED_DEFAULT_FILTER_KEYS = {
    "current_year", "current_month", "current_day", "current_week",
    "forecast_year", "source", "used_real_current_date",
    "Month", "Year",
}

# Vocabulary used by the schema-scoring fallback in _line_item_dim_from_profile.
_LINE_ITEM_NAME_HINTS = ("account", "line item", "chart of accounts", "p&l account", "gl")
_PNL_ELEMENT_TOKENS = (
    "revenue", "sales", "income", "cost", "expense",
    "gross profit", "operating profit", "net income", "ebitda", "ebit",
    "depreciation", "amortization", "amortisation", "interest", "tax",
)


# ── Private helpers ───────────────────────────────────────────────────────────

def _profile_dim_defaults(model_profile: dict | None) -> dict[str, str]:
    """Per-dimension WHERE overrides from default_filters in the profile."""
    raw = (model_profile or {}).get("default_filters") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): v.strip()
        for k, v in raw.items()
        if k not in _RESERVED_DEFAULT_FILTER_KEYS
        and isinstance(v, str) and v.strip()
    }


def _question_currency_view(question: str) -> str:
    text = (question or "").lower()
    for pattern, view in CURRENCY_VIEW_PATTERNS:
        if re.search(pattern, text):
            return view
    return "local"


def _element_attr_text(dim: dict, element: str) -> str:
    values = (dim.get("element_attr_values") or {}).get(element) or {}
    return " ".join(str(v) for v in values.values()).lower()


def _currency_view_score(dim: dict, element: str, view: str) -> int:
    blob = f"{element} {_element_attr_text(dim, element)}".lower()
    score = 0
    if view == "parent":
        if "parent currency" in blob:
            score += 40
        if "group currency" in blob:
            score += 35
        if "reporting currency" in blob or "translated" in blob:
            score += 25
    else:
        if "entity currency" in blob:
            score += 40
        if "local currency" in blob:
            score += 35
        if re.search(r"\blcy\b", blob):
            score += 25

    if re.search(r"\b(total|adjustment|adj|gaap)\b", blob):
        score -= 12
    if re.search(r"\b(all|list)\b", blob):
        score -= 25
    if element in {str(e) for e in dim.get("consolidations", []) or []}:
        score -= 8
    return score


def _currency_view_default(dim: dict, question: str) -> str:
    """Pick the data-bearing currency-view leaf for a local/parent request."""
    elements = [str(e) for e in dim.get("elements", []) or [] if e]
    if not elements:
        return ""

    view = _question_currency_view(question)
    scored = [
        (_currency_view_score(dim, element, view), -index, element)
        for index, element in enumerate(elements)
    ]
    scored = [item for item in scored if item[0] > 0]
    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][2]


def _shape(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _question_explicit_dim_member_overrides(cube_schema: dict, question: str) -> dict[str, str]:
    """Parse explicit corrections like "Dimension X should be Element Y"."""
    question_shape = _shape(question)
    if not question_shape:
        return {}

    overrides: dict[str, str] = {}
    for dim in cube_schema.get("dimensions") or []:
        dim_name = str(dim.get("name", ""))
        if not dim_name or _shape(dim_name) not in question_shape:
            continue

        candidates = (
            list(dim.get("elements", []) or [])
            + list(dim.get("consolidations", []) or [])
            + list(dim.get("top_consolidations", []) or [])
        )
        matches = [
            str(element)
            for element in dict.fromkeys(candidates)
            if _shape(str(element)) and _shape(str(element)) in question_shape
        ]
        if not matches:
            continue
        matches.sort(key=lambda value: len(_shape(value)), reverse=True)
        overrides[dim_name] = matches[0]
    return overrides


def _data_source_default(dim: dict) -> str:
    """Pick the primary data-bearing leaf for a data_source dim.

    These dims (e.g. S Consol GL Company Entry) have a catch-all consolidation
    ('All Data Sources') and several named leaf elements that represent real data
    feeds. The first leaf whose attributes signal entity/local currency is
    returned. Returns '' when no clear primary leaf can be determined.
    """
    elements = [str(e) for e in dim.get("elements", []) or [] if e]
    if not elements:
        return ""
    consolidations = {str(e) for e in dim.get("consolidations", []) or []}

    for element in elements:
        if element in consolidations:
            continue
        blob = f"{element} {_element_attr_text(dim, element)}".lower()
        if "entity currency" in blob or "local currency" in blob:
            return element

    return ""


def _estimate_descendants(dim_name: str, element_name: str, limit: int) -> int:
    """BFS on element_edges; stops as soon as `limit` nodes are counted."""
    try:
        from ...tm1.cache.db import connect
        rows = connect().execute(
            "SELECT parent_name, child_name FROM element_edges WHERE dim_name = ?",
            (dim_name,),
        ).fetchall()
    except Exception:
        return 0

    children_by_parent: dict[str, list[str]] = {}
    for parent, child in rows:
        children_by_parent.setdefault(str(parent), []).append(str(child))

    seen: set[str] = set()
    queue = list(children_by_parent.get(element_name, []))
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        if len(seen) >= limit:
            return len(seen)
        queue.extend(children_by_parent.get(node, []))
    return len(seen)


def _best_all_period(candidates: list[str], dim_name: str = "") -> str:
    """Return the minimum-leaf all-periods aggregate from candidates."""
    agg_candidates = [c for c in candidates if re.search(r"(?i)^(all|total)\s", c)]
    if not agg_candidates:
        return ""

    if dim_name and len(agg_candidates) > 1:
        scores: list[tuple[int, str]] = []
        for c in agg_candidates:
            n = _estimate_descendants(dim_name, c, limit=200)
            scores.append((n if n > 0 else 9999, c))
        scores.sort(key=lambda x: x[0])
        best_n, best_name = scores[0]
        if best_n < 9999:
            return best_name

    return agg_candidates[0]


def _line_item_dim_from_profile(
    cube_schema: dict,
    model_profile: dict | None,
) -> dict | None:
    """Return the P&L line-item dim dict: semantic-profile first, schema-scoring fallback.

    The semantic profile stores a pre-computed line_item_dimension per finance concept
    (income_statement, balance_sheet, etc.).  Reading it here removes the query-time
    dependency on planner heuristics and keeps business vocabulary in the profile.
    """
    dims = cube_schema.get("dimensions") or []
    dim_by_name = {
        str(d.get("name", "")): d
        for d in dims
        if d.get("name") and not d.get("is_measure")
    }

    # 1. Semantic profile lookup — no heuristics needed when profile is available.
    finance = (model_profile or {}).get("finance_semantics") or {}
    for concept in finance.get("concepts", {}).values():
        line_dim_name = str(concept.get("line_item_dimension", "")).strip()
        if line_dim_name and line_dim_name in dim_by_name:
            return dim_by_name[line_dim_name]

    # 2. Schema-scoring fallback — used when no profile exists yet.
    candidates: list[tuple[int, int, dict]] = []
    for index, dim in enumerate(dims):
        if dim.get("is_measure"):
            continue
        name = str(dim.get("name", "")).lower()
        name_match = any(hint in name for hint in _LINE_ITEM_NAME_HINTS)
        bag = " ".join(
            str(e).lower()
            for e in (
                list(dim.get("elements", []) or [])
                + list(dim.get("consolidations", []) or [])
                + list(dim.get("top_consolidations", []) or [])
            )
        )
        elem_score = sum(1 for t in _PNL_ELEMENT_TOKENS if t in bag)
        if not name_match and elem_score == 0:
            continue
        score = elem_score * 3 + (1 if name_match else 0)
        candidates.append((score, -index, dim))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


# ── Directive builders ────────────────────────────────────────────────────────

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
    overrides = _profile_dim_defaults(model_profile)
    schema_dims = {str(d.get("name", "")): d for d in (cube_schema.get("dimensions") or [])}
    relevant = {k: v for k, v in overrides.items() if k in schema_dims}

    from ..schema.dim_roles import get_dim_role
    profile_roles = (model_profile or {}).get("dim_roles") or {}
    for dim in (cube_schema.get("dimensions") or []):
        dim_name = str(dim.get("name", ""))
        if not dim_name or dim_name in relevant:
            continue
        if get_dim_role(dim, profile_roles) != "currency_view":
            continue
        default = _currency_view_default(dim, question)
        if default:
            relevant[dim_name] = default

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

    relevant.update(_question_explicit_dim_member_overrides(cube_schema, question))

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


def line_item_directive(question: str, cube_schema: dict, model_profile: dict | None = None) -> str:
    """For financial-statement questions, lock the source-of-truth line-item
    dim onto ROWS using its top consolidation."""
    if not _STATEMENT_QUESTION_RE.search(question or ""):
        return ""
    dim = _line_item_dim_from_profile(cube_schema, model_profile)
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
