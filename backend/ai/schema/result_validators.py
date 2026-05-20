"""Deterministic result-shape validators used by the TM1 agent loop."""

import re
from typing import cast

from .tm1_lexicon import STATEMENT_PATTERN


def result_shape_issue(
    rows: list[dict[str, object]],
    layout: dict[str, object],
    dim_meta: dict[str, dict] | None = None,
    hierarchy_edges: dict[str, list[tuple[str, str]]] | None = None,
) -> str | None:
    """
    Detect noisy column axes caused by broad .Members expansion.

    Consolidated elements are allowed. The issue we want to catch is multiple
    ancestor consolidated elements on the same axis, which usually means the
    query expanded several hierarchy levels instead of selecting one meaningful
    summary level or one parent plus its children.
    """
    if not rows:
        return None

    column_dims = cast(list[str], layout.get("column_dimensions") or [])
    if not column_dims:
        return None

    dim_meta = dim_meta or {}
    hierarchy_edges = hierarchy_edges or {}

    for dim_name in column_dims:
        values = [
            str(row.get("_dimensions", {}).get(dim_name, ""))
            for row in rows
            if isinstance(row.get("_dimensions"), dict)
            and row.get("_dimensions", {}).get(dim_name, "") not in ("", None)
        ]
        unique_values = list(dict.fromkeys(values))
        if len(unique_values) < 8:
            continue

        consolidated = set(dim_meta.get(dim_name, {}).get("consolidated", set()))
        cons_values = [value for value in unique_values if value in consolidated]
        leaf_values = [value for value in unique_values if value not in consolidated]
        is_time_dim = bool(dim_meta.get(dim_name, {}).get("is_time_dim"))
        ancestor_pairs = ancestor_consolidated_pairs(
            cons_values,
            hierarchy_edges.get(dim_name, []),
        )

        if ancestor_pairs:
            examples = ", ".join(f"{parent} > {child}" for parent, child in ancestor_pairs[:3])
            return (
                "Result shape validation failed: column axis for dimension "
                f"'{dim_name}' returned multiple ancestor consolidated elements "
                f"on the same axis ({examples}). Do not use .Members here. "
                "Rewrite the column axis as an explicit set that keeps either "
                "one summary parent with its relevant children, or one sibling "
                "summary level, depending on the user's question."
            )

        no_edges_available = not hierarchy_edges.get(dim_name)
        broad_time_rollup_mix = (
            no_edges_available
            and is_time_dim
            and len(unique_values) > 14
            and len(cons_values) >= 3
            and len(leaf_values) >= 3
        )
        if broad_time_rollup_mix:
            return (
                "Result shape validation failed: column axis for dimension "
                f"'{dim_name}' returned {len(unique_values)} time elements with "
                f"{len(cons_values)} consolidated rollup element(s). This looks "
                "like a broad .Members expansion. Rewrite the column axis as an "
                "explicit set of only the relevant time elements."
            )

    return None


def specific_focus_issue(
    question: str,
    schema: dict,
    rows: list[dict[str, object]],
    layout: dict[str, object],
) -> str | None:
    """
    Detect when a question names a specific non-time element but the result
    still expands that same dimension across many sibling elements.
    """
    if not rows:
        return None

    focus = find_named_schema_element(question, schema)
    if not focus:
        return None

    dim_name, element_name = focus
    focus_dim = next(
        (d for d in schema.get("dimensions", []) if str(d.get("name", "")) == dim_name),
        {},
    )
    consolidated = {
        str(e).lower()
        for e in (
            list(focus_dim.get("consolidations", []) or [])
            + list(focus_dim.get("top_consolidations", []) or [])
        )
    }
    if element_name.lower() in consolidated:
        return None

    row_dims = set(cast(list[str], layout.get("row_dimensions") or []))
    col_dims = set(cast(list[str], layout.get("column_dimensions") or []))
    if dim_name not in row_dims and dim_name not in col_dims:
        return None

    values = [
        str(row.get("_dimensions", {}).get(dim_name, ""))
        for row in rows
        if isinstance(row.get("_dimensions"), dict)
        and row.get("_dimensions", {}).get(dim_name, "") not in ("", None)
    ]
    unique_values = set(values)
    if len(unique_values) <= 1:
        return None

    return (
        "Result focus validation failed: the current question specifically names "
        f"element '{element_name}' in dimension '{dim_name}', but the query returned "
        f"{len(unique_values)} elements from that same dimension. Filter '{dim_name}' "
        f"to '{element_name}' unless the user explicitly asks to compare it with siblings."
    )


def statement_line_item_issue(
    question: str,
    schema: dict,
    layout: dict[str, object],
    model_profile: dict | None = None,
) -> str | None:
    """Require statement-style questions to display account line items.

    A cube can have multiple "account"-like dimensions (e.g. Account, Account
    Report, Account Disclosure). Accept the result as valid if ANY of them is
    on ROWS, and prefer the dimension named by the model profile's
    finance_semantics when present.
    """
    if not _is_statement_question(question):
        return None

    candidates = _find_statement_line_item_dimensions(schema)
    profile_dim = _profile_line_item_dim(model_profile)
    if profile_dim:
        candidates = [profile_dim, *[c for c in candidates if c != profile_dim]]
    if not candidates:
        return None

    row_dims = set(cast(list[str], layout.get("row_dimensions") or []))
    if any(c in row_dims for c in candidates):
        return None

    preferred = candidates[0]
    return (
        "Result statement validation failed: the current question asks for a "
        f"financial statement, so dimension '{preferred}' (or one of {candidates}) "
        "should be displayed on ROWS as statement line items instead of being "
        "filtered to one total element."
    )


_DESCENDANTS_DIM_RE = re.compile(
    r"(?is)Descendants\s*\(\s*\[([^\]]+)\]\.\[[^\]]+\]\.\[[^\]]+\]"
)


def account_dim_choice_issue(question: str, schema: dict, mdx: str) -> str | None:
    """If MDX expands an 'account'-like dim on ROWS via Descendants, make sure
    it chose the SOURCE-OF-TRUTH account dim, not a sibling reporting/
    disclosure dim. Runs on every MDX, not just statement-keyword questions,
    so LLM can't sneak Account Report onto ROWS by phrasing the question
    without 'P&L' or 'income statement'.

    Reject only when:
      - the cube has multiple account-like dims (otherwise no choice to make)
      - the dim on rows isn't the highest-scored one by element content
      - the user didn't explicitly name the dim on rows
    """
    # Pull rows clause out of MDX
    rows_match = re.search(r"(?is)\bSELECT\b.+?\bON\s+ROWS\b", mdx)
    if not rows_match:
        return None
    rows_clause = rows_match.group(0)
    expanded_dims = set(_DESCENDANTS_DIM_RE.findall(rows_clause))
    if not expanded_dims:
        return None

    candidates = _find_statement_line_item_dimensions(schema)
    if len(candidates) < 2:
        return None  # No competing account dims - nothing to disambiguate.
    primary_dim = _resolve_line_item_dim(schema.get("dimensions", []) or [])
    if not primary_dim:
        return None
    primary_name = str(primary_dim.get("name", ""))
    if not primary_name:
        return None

    question_text = (question or "").lower()
    other_account_dims = [c for c in candidates if c in expanded_dims and c != primary_name]
    if not other_account_dims:
        return None

    # If user explicitly named the dim on rows (or its key elements), allow it.
    for offender in other_account_dims:
        if _question_mentions_element(question_text, offender):
            return None

    offender = other_account_dims[0]
    top = (
        (primary_dim.get("top_consolidations") or [None])[0]
        or primary_dim.get("default_element")
        or ""
    )
    suggestion = (
        f"Descendants([{primary_name}].[{primary_name}].[{top}])"
        if top else f"the [{primary_name}] dimension"
    )
    return (
        "Result account-dim validation failed: ROWS expands "
        f"[{offender}] via Descendants, but the source-of-truth line-item "
        f"dimension in this cube is [{primary_name}] (its elements/"
        "consolidations contain real P&L names like revenue / cost / expense / "
        f"depreciation; [{offender}] mostly contains layout codes). Put "
        f"{suggestion} on ROWS and filter [{offender}] in WHERE to its "
        "default_element."
    )


def entity_filter_issue(
    question: str,
    schema: dict,
    mdx: str,
    model_profile: dict | None = None,
) -> str | None:
    """FATAL check: user explicitly named an entity but the entity_subject dim
    is not filtered to it.

    Called inside the MDX repair loop — returning a non-None value triggers a
    re-generation attempt.  Only fires for entity_subject dims; generic default
    mismatches are handled separately by default_filter_warning().
    """
    from .dim_roles import accepts_entity_name, get_dim_role
    profile_roles = (model_profile or {}).get("dim_roles") or {}

    axis_dims = set()
    select_match = re.search(r"(?is)\bSELECT\b(.+?)\bFROM\b", mdx)
    if select_match:
        axis_dims.update(re.findall(r"\[([^\]]+)\]\.\[[^\]]+\]", select_match.group(1)))

    where_match = re.search(r"(?is)\bWHERE\s*\((.+)\)\s*$", mdx)
    if not where_match:
        return None

    filters = {
        dim: element
        for dim, _hier, element in re.findall(
            r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\]",
            where_match.group(1),
        )
    }
    if not filters:
        return None

    def _shape(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())

    # Only count a mention as "already consumed" when it appears in a WHERE
    # filter for an entity_subject dim. A same-name element in Segment or
    # Intercompany does NOT satisfy a Company entity mention.
    entity_dims_in_schema = {
        str(d.get("name", ""))
        for d in schema.get("dimensions", [])
        if accepts_entity_name(get_dim_role(d, profile_roles))
    }
    consumed_shapes = {
        _shape(v)
        for dim_name, v in filters.items()
        if v and dim_name in entity_dims_in_schema
    }

    question_text = question.lower()
    entity_candidates: list[tuple[str, str]] = []
    try:
        from ...tm1.cache import resolve_question_members

        if entity_dims_in_schema:
            grounded = resolve_question_members(question, candidate_dims=list(entity_dims_in_schema))
            entity_candidates.extend(
                (str(item.get("dimension", "")), str(item.get("element", "")))
                for item in grounded
                if str(item.get("dimension", "")) in entity_dims_in_schema
            )
    except Exception:
        pass

    for dimension in schema.get("dimensions", []):
        dim_name = str(dimension.get("name", ""))
        if not dim_name or dim_name in axis_dims or dimension.get("is_measure") or dimension.get("is_time_dim"):
            continue
        actual = str(filters.get(dim_name, ""))
        if not actual:
            continue
        if _question_mentions_element(question_text, actual):
            continue
        # Only entity_subject dims can be wrong here — non-entity dims (Segment,
        # Intercompany, …) keep their default_element regardless of question text.
        if not accepts_entity_name(get_dim_role(dimension, profile_roles)):
            continue
        local_candidates = [element for candidate_dim, element in entity_candidates if candidate_dim == dim_name]
        local_candidates.extend(str(element) for element in dimension.get("elements", []))
        for element in dict.fromkeys(local_candidates):
            element_name = str(element)
            if element_name == actual:
                continue
            if _shape(element_name) in consumed_shapes:
                continue  # Already honored by a different dim's filter.
            if _question_mentions_element(question_text, element_name):
                return (
                    "Default filter validation failed: the user mentioned "
                    f"'{element_name}' in dimension '{dim_name}', but WHERE filters "
                    f"that dimension to '{actual}'. Filter '{dim_name}' to "
                    f"'{element_name}' (or remove that filter and put the dimension "
                    "on an axis) unless the user is asking to compare both."
                )
    return None


def default_filter_warning(
    question: str,
    schema: dict,
    mdx: str,
    model_profile: dict | None = None,
) -> str | None:
    """Non-fatal warning: a WHERE filter differs from the model-profile default.

    Entity-mention mismatches are handled separately by entity_filter_issue()
    (which is fatal).  This warning fires for any non-entity dim whose WHERE
    value doesn't match the profile's recorded default_element — useful for
    debugging unexpected filter drift.
    """
    from .dim_roles import accepts_entity_name, get_dim_role
    profile_roles = (model_profile or {}).get("dim_roles") or {}
    profile_defaults = (model_profile or {}).get("default_filters") or {}
    if not profile_defaults:
        return None

    where_match = re.search(r"(?is)\bWHERE\s*\((.+)\)\s*$", mdx)
    if not where_match:
        return None
    filters = {
        dim: element
        for dim, _hier, element in re.findall(
            r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\]",
            where_match.group(1),
        )
    }
    if not filters:
        return None

    select_match = re.search(r"(?is)\bSELECT\b(.+?)\bFROM\b", mdx)
    axis_dims: set[str] = set()
    if select_match:
        axis_dims.update(re.findall(r"\[([^\]]+)\]\.\[[^\]]+\]", select_match.group(1)))

    for dimension in schema.get("dimensions", []):
        dim_name = str(dimension.get("name", ""))
        if not dim_name or dim_name in axis_dims:
            continue
        if dimension.get("is_measure") or dimension.get("is_time_dim"):
            continue
        # Entity-subject mismatches are handled by entity_filter_issue() (fatal).
        if accepts_entity_name(get_dim_role(dimension, profile_roles)):
            continue
        actual = filters.get(dim_name, "")
        if not actual:
            continue
        expected = str(profile_defaults.get(dim_name, "")).strip()
        if expected and actual.lower() != expected.lower():
            return (
                f"warning: WHERE filter for '{dim_name}' is '{actual}' but "
                f"model profile default is '{expected}'."
            )
    return None


def static_mdx_schema_issue(mdx: str, schema: dict) -> str | None:
    """Validate generated MDX against cached cube schema before TM1 execution."""
    cube_dims = [
        str(d.get("name", ""))
        for d in schema.get("dimensions", []) or []
        if d.get("name")
    ]
    if not cube_dims:
        return None
    cube_dim_set = set(cube_dims)

    select_match = re.search(r"(?is)\bSELECT\b(.+?)\bFROM\b", mdx or "")
    if not select_match:
        return "Static MDX validation failed: missing SELECT axes before FROM."
    axes_text = select_match.group(1)
    where_match = re.search(r"(?is)\bWHERE\s*\((.+)\)\s*$", mdx or "")
    where_text = where_match.group(1) if where_match else ""

    axis_dims = _ordered_unique(
        dim for dim, _hier in re.findall(r"\[([^\]]+)\]\.\[([^\]]+)\]", axes_text)
    )
    where_members = re.findall(
        r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\]",
        where_text,
    )
    where_dims = _ordered_unique(dim for dim, _hier, _elem in where_members)

    unknown_dims = [dim for dim in axis_dims + where_dims if dim not in cube_dim_set]
    if unknown_dims:
        return (
            "Static MDX validation failed: dimension "
            f"'{unknown_dims[0]}' is not in cube [{schema.get('cube', '')}]."
        )

    duplicate_dims = [dim for dim in axis_dims if dim in where_dims]
    if duplicate_dims:
        return (
            "Static MDX validation failed: dimension "
            f"'{duplicate_dims[0]}' appears on an axis and in WHERE. "
            "Each dimension must appear exactly once."
        )

    missing_dims = [dim for dim in cube_dims if dim not in set(axis_dims + where_dims)]
    if missing_dims:
        return (
            "Static MDX validation failed: missing dimension "
            f"'{missing_dims[0]}'. Put it on an axis or add one WHERE member."
        )

    repeated = _duplicates(axis_dims + where_dims)
    if repeated:
        return (
            "Static MDX validation failed: dimension "
            f"'{repeated[0]}' appears more than once."
        )

    if where_text and re.search(r"(?i)\.Members|\.Children|Descendants\s*\(|\{|\}", where_text):
        return (
            "Static MDX validation failed: WHERE contains a set expression. "
            "WHERE must contain only single members [Dim].[Dim].[Element]."
        )

    element_issue = _member_existence_issue(mdx, schema)
    if element_issue:
        return element_issue

    set_parent_issue = _set_parent_issue(mdx, schema)
    if set_parent_issue:
        return set_parent_issue

    return None


def _batch_element_exists(pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Return the subset of (dim_name, LOWER(element_name)) pairs that exist in cache."""
    from ...tm1.cache import connect
    from collections import defaultdict

    by_dim: dict[str, list[str]] = defaultdict(list)
    for dim, elem in pairs:
        by_dim[dim].append(elem.lower())

    found: set[tuple[str, str]] = set()
    try:
        with connect() as conn:
            for dim, elems in by_dim.items():
                placeholders = ",".join("?" * len(elems))
                rows = conn.execute(
                    f"SELECT LOWER(element_name) FROM elements"
                    f" WHERE dim_name = ? AND LOWER(element_name) IN ({placeholders})",
                    [dim, *elems],
                ).fetchall()
                for (elem_lower,) in rows:
                    found.add((dim, elem_lower))
    except Exception:
        pass
    return found


def _batch_consolidated_exists(pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Return subset of (dim_name, LOWER(element_name)) that are Consolidated in cache."""
    from ...tm1.cache import connect
    from collections import defaultdict

    by_dim: dict[str, list[str]] = defaultdict(list)
    for dim, elem in pairs:
        by_dim[dim].append(elem.lower())

    found: set[tuple[str, str]] = set()
    try:
        with connect() as conn:
            for dim, elems in by_dim.items():
                placeholders = ",".join("?" * len(elems))
                rows = conn.execute(
                    f"SELECT LOWER(element_name) FROM elements"
                    f" WHERE dim_name = ? AND element_type = 'Consolidated'"
                    f"   AND LOWER(element_name) IN ({placeholders})",
                    [dim, *elems],
                ).fetchall()
                for (elem_lower,) in rows:
                    found.add((dim, elem_lower))
    except Exception:
        pass
    return found


def _member_existence_issue(mdx: str, schema: dict) -> str | None:
    schema_elements = {
        str(d.get("name", "")): {
            str(e).lower()
            for e in (
                list(d.get("elements", []) or [])
                + list(d.get("consolidations", []) or [])
                + list(d.get("top_consolidations", []) or [])
                + ([d.get("default_element")] if d.get("default_element") else [])
            )
        }
        for d in schema.get("dimensions", []) or []
    }

    triples = re.findall(r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\]", mdx or "")
    unknown_pairs = [
        (dim, element)
        for dim, _hier, element in triples
        if element != "?" and element.lower() not in schema_elements.get(dim, set())
    ]

    if not unknown_pairs:
        return None

    found_in_cache = _batch_element_exists(unknown_pairs)
    for dim, element in unknown_pairs:
        if (dim, element.lower()) not in found_in_cache:
            return (
                "Static MDX validation failed: element "
                f"'{element}' was not found in dimension '{dim}'. Use an exact "
                "element from the schema or grounded member candidates."
            )
    return None


def _set_parent_issue(mdx: str, schema: dict) -> str | None:
    schema_consolidations = {
        str(d.get("name", "")): {
            str(e).lower()
            for e in (
                list(d.get("consolidations", []) or [])
                + list(d.get("top_consolidations", []) or [])
            )
        }
        for d in schema.get("dimensions", []) or []
    }
    parent_refs = re.findall(
        r"(?is)(?:Descendants\s*\(\s*)?\[([^\]]+)\]\.\[[^\]]+\]\.\[([^\]]+)\]\s*(?:,\s*\d+[^)]*\)|\.Children)",
        mdx or "",
    )

    unknown_parents = [
        (dim, element)
        for dim, element in parent_refs
        if element.lower() not in schema_consolidations.get(dim, set())
    ]

    if not unknown_parents:
        return None

    found_consolidated = _batch_consolidated_exists(unknown_parents)
    for dim, element in unknown_parents:
        if (dim, element.lower()) not in found_consolidated:
            return (
                "Static MDX validation failed: set function parent "
                f"'{element}' in dimension '{dim}' is not a consolidated element. "
                "Use .Children or Descendants only on consolidated elements."
            )
    return None


def _ordered_unique(items) -> list[str]:
    return list(dict.fromkeys(str(item) for item in items if item))


def _duplicates(items: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for item in items:
        if item in seen and item not in dupes:
            dupes.append(item)
        seen.add(item)
    return dupes


def _question_mentions_element(question_text: str, element: str) -> bool:
    normalized_element = re.sub(r"[^a-z0-9]+", " ", element.lower()).strip()
    normalized_question = re.sub(r"[^a-z0-9]+", " ", question_text).strip()
    return bool(normalized_element and normalized_element in normalized_question)


def _is_statement_question(question: str) -> bool:
    return bool(STATEMENT_PATTERN.search(question or ""))


_LINE_ITEM_TOKENS = ("account", "line item", "chart of accounts", "p&l account", "gl")

_PNL_ELEMENT_TOKENS = (
    "revenue", "sales", "income", "cost", "expense", "expenses",
    "gross profit", "operating profit", "operating expense", "operating income",
    "net income", "net profit", "ebitda", "ebit", "earnings", "margin",
    "depreciation", "amortization", "amortisation", "impairment",
    "interest", "tax", "taxes", "deferred",
    "cogs", "cost of goods", "cost of sales",
    "salary", "salaries", "wages", "compensation", "benefits", "bonus",
    "rent", "utilities", "marketing", "advertising",
    "loss", "profit", "fee", "fees", "charge", "charges",
)


def _score_line_item_dim(dim: dict) -> int:
    """Count P&L-flavoured tokens across the dim's elements and consolidations."""
    bag: list[str] = []
    bag.extend(str(e) for e in dim.get("elements", []) or [])
    bag.extend(str(e) for e in dim.get("consolidations", []) or [])
    bag.extend(str(e) for e in dim.get("top_consolidations", []) or [])
    text = " ".join(b.lower() for b in bag)
    return sum(1 for token in _PNL_ELEMENT_TOKENS if token in text)


def _resolve_line_item_dim(dimensions: list[dict]) -> dict | None:
    """Pick the dim whose element content is most P&L-flavoured."""
    candidates: list[tuple[int, int, dict]] = []
    for index, dim in enumerate(dimensions):
        if dim.get("is_measure"):
            continue
        name = str(dim.get("name", "")).lower()
        name_match = any(hint in name for hint in _LINE_ITEM_TOKENS)
        element_score = _score_line_item_dim(dim)
        if not name_match and element_score == 0:
            continue
        score = element_score * 3 + (1 if name_match else 0)
        candidates.append((score, -index, dim))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _find_statement_line_item_dimensions(schema: dict) -> list[str]:
    """Return account/line-item candidate dims ranked by P&L element content,
    with schema position as tie-breaker."""
    dims = list(schema.get("dimensions", []) or [])
    primary = _resolve_line_item_dim(dims)
    primary_name = str(primary.get("name", "")) if primary else ""

    candidates: list[tuple[int, int, str]] = []
    for index, dimension in enumerate(dims):
        if dimension.get("is_measure"):
            continue
        dim_name = str(dimension.get("name", ""))
        if not dim_name:
            continue
        norm = dim_name.lower()
        name_match = any(token in norm for token in _LINE_ITEM_TOKENS)
        elem_score = _score_line_item_dim(dimension)
        if not name_match and elem_score == 0:
            continue
        score = elem_score * 3 + (1 if name_match else 0)
        candidates.append((score, -index, dim_name))
    candidates.sort(reverse=True)
    ranked = [name for _s, _i, name in candidates]
    if primary_name and primary_name in ranked and ranked[0] != primary_name:
        ranked.remove(primary_name)
        ranked.insert(0, primary_name)
    return ranked


def _profile_line_item_dim(model_profile: dict | None) -> str:
    if not isinstance(model_profile, dict):
        return ""
    semantics = model_profile.get("finance_semantics") or {}
    concepts = semantics.get("concepts") or {}
    info = concepts.get("income_statement") or {}
    return str(info.get("line_item_dimension", "")).strip()


def find_named_schema_element(question: str, schema: dict) -> tuple[str, str] | None:
    text = normalise_focus_text(question)
    matches: list[tuple[int, str, str]] = []
    for dimension in schema.get("dimensions", []):
        dim_name = str(dimension.get("name", ""))
        if not dim_name or dimension.get("is_time_dim"):
            continue
        if any(token in dim_name.lower() for token in ("year", "month", "period", "time", "date")):
            continue
        candidates = (
            list(dimension.get("elements", []) or [])
            + list(dimension.get("consolidations", []) or [])
            + list(dimension.get("top_consolidations", []) or [])
        )
        for element in dict.fromkeys(candidates):
            element_name = str(element)
            cleaned = normalise_focus_text(element_name)
            if len(cleaned) < 4:
                continue
            pattern = rf"(?<![a-z0-9]){re.escape(cleaned)}(?![a-z0-9])"
            if re.search(pattern, text):
                matches.append((len(cleaned), dim_name, element_name))
    if not matches:
        return None
    _length, dim_name, element_name = max(matches, key=lambda item: item[0])
    return dim_name, element_name


def normalise_focus_text(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", str(value).lower().replace("_", " ").replace("-", " "))
    normalised = []
    for token in tokens:
        # Strip trailing plural 's' only when safe — avoid mangling words like
        # "analysis" → "analysi", "status" → "statu", "bonus" → "bonu".
        if (
            len(token) > 4
            and token.endswith("s")
            and not token.endswith(("ss", "us", "is", "as", "os"))
        ):
            normalised.append(token[:-1])
        else:
            normalised.append(token)
    return " ".join(normalised)


def ancestor_consolidated_pairs(
    consolidated_values: list[str],
    edges: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    if len(consolidated_values) < 2 or not edges:
        return []

    cons_set = set(consolidated_values)
    children_by_parent: dict[str, set[str]] = {}
    for parent, child in edges:
        children_by_parent.setdefault(parent, set()).add(child)

    pairs: list[tuple[str, str]] = []
    for parent in consolidated_values:
        stack = list(children_by_parent.get(parent, set()))
        seen: set[str] = set()
        while stack:
            child = stack.pop()
            if child in seen:
                continue
            seen.add(child)
            if child in cons_set:
                pairs.append((parent, child))
            stack.extend(children_by_parent.get(child, set()))
    return pairs
