"""Deterministic result-shape validators used by the TM1 agent loop."""

import re
from typing import cast


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
    from .mdx_planner import _resolve_line_item_dim  # avoid module-level cycle

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
    primary_dim = _resolve_line_item_dim(schema.get("dimensions", []) or [], None)
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
        f"Descendants([{primary_name}].[{primary_name}].[{top}], 99, LEAVES)"
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


def default_filter_issue(
    question: str,
    schema: dict,
    mdx: str,
    model_profile: dict | None = None,
) -> str | None:
    """Detect dimension filters that contradict what the user explicitly asked for.

    Three layers keep us from flagging the wrong dim when the user mentions an
    entity name that exists in multiple dims (e.g. "SLIM-HK" is a member of
    Company AND Intercompany AND Segment 2):

      1. ROLE gate - only `entity_subject` dims (Company, Cost Center,
         Customer, Employee, ...) accept an entity-name match. Counterparty
         (Intercompany), business_classifier (Segment, Category, Disclosure),
         data_source, metadata dims are exempt; their default filter is the
         right answer regardless of which entity name appears in the question.
      2. Consumed-shape gate - if another dim's WHERE filter already uses
         this element name, the user's mention is honored there.
      3. Default-element pass-through - a WHERE element equal to the dim's
         default_element (or to the user's mention) is always OK.
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

    consumed_shapes = {_shape(v) for v in filters.values() if v}

    question_text = question.lower()
    for dimension in schema.get("dimensions", []):
        dim_name = str(dimension.get("name", ""))
        if not dim_name or dim_name in axis_dims or dimension.get("is_measure") or dimension.get("is_time_dim"):
            continue
        actual = str(filters.get(dim_name, ""))
        if not actual:
            continue
        if _question_mentions_element(question_text, actual):
            continue
        # ROLE gate: non-entity dims are never wrong here. Their elements may
        # contain entity-looking strings but those mentions don't belong to
        # this dim.
        if not accepts_entity_name(get_dim_role(dimension, profile_roles)):
            continue
        for element in dimension.get("elements", []):
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


def _question_mentions_element(question_text: str, element: str) -> bool:
    normalized_element = re.sub(r"[^a-z0-9]+", " ", element.lower()).strip()
    normalized_question = re.sub(r"[^a-z0-9]+", " ", question_text).strip()
    return bool(normalized_element and normalized_element in normalized_question)


def _is_statement_question(question: str) -> bool:
    text = str(question).lower()
    return bool(
        re.search(r"\bp\s*&\s*l\b", text)
        or re.search(r"\bprofit\s+and\s+loss\b", text)
        or re.search(r"\bincome\s+statement\b", text)
        or re.search(r"\bp\s+and\s+l\b", text)
    )


_LINE_ITEM_TOKENS = ("account", "line item", "chart of accounts", "p&l account", "gl")


def _find_statement_line_item_dimensions(schema: dict) -> list[str]:
    """Return account/line-item candidate dims, ranked the same way the planner
    ranks them: by P&L-flavoured element content first, schema position as
    tie-breaker. Keeps validator and planner in lock-step on which dim is the
    'real' line-item dim, so the loop never bounces between repaired MDXs."""
    from .mdx_planner import _resolve_line_item_dim, _score_line_item_dim  # avoid module-level cycle

    dims = list(schema.get("dimensions", []) or [])
    primary = _resolve_line_item_dim(dims, None)
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
        if not dim_name or dimension.get("is_time"):
            continue
        if any(token in dim_name.lower() for token in ("year", "month", "period", "time", "date")):
            continue
        for element in dimension.get("elements", []):
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
    normalised = [
        token[:-1] if len(token) > 4 and token.endswith("s") else token
        for token in tokens
    ]
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
