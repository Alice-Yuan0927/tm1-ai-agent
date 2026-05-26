"""Element and cube lookup tools for the MDX generation agent.

Each tool is registered once in ELEMENT_REGISTRY. The registry:
  - exports provider-ready schemas via ELEMENT_REGISTRY.schemas()
  - validates LLM-supplied args before dispatching to the handler
  - returns JSON strings in every case (including errors)

To add a new tool: write a _handle_* function and call ELEMENT_REGISTRY.register().
"""

import json
import logging

from ..retrieval.embeddings import search_by_embedding
from ...tm1.cache import find_question_element_matches
from ...tm1.cache.db import connect
from ...tm1.service import get_cube_view_mdx, list_cube_views
from .registry import ToolRegistry, ToolSpec

_log = logging.getLogger(__name__)


# ── Handlers ──────────────────────────────────────────────────────────────────

def _handle_search_elements(args: dict, cube_schema: dict) -> str:
    dim = args.get("dimension", "")
    term = args.get("term", "")

    # Both finders return list[tuple[matched_phrase, dim_name, element_name]].

    # FTS first — local SQLite, no API call.
    fts_matches = find_question_element_matches(term)
    if dim:
        fts_matches = [m for m in fts_matches if m[1] == dim]
    results = [m[2] for m in fts_matches[:10] if m[2]]
    if results:
        return json.dumps({"dimension": dim, "matches": results, "matched_by": "fts"})

    # Embedding fallback — only when FTS finds nothing.
    _log.info("[search_elements] FTS miss for %r — calling embedding API", term[:60])
    try:
        candidate_dims = [dim] if dim else None
        emb_matches = search_by_embedding(term, candidate_dims=candidate_dims)
        results = [m[2] for m in emb_matches[:10] if m[2]]
        if results:
            return json.dumps({"dimension": dim, "matches": results, "matched_by": "embedding"})
    except Exception as exc:
        _log.warning("[search_elements] embedding API failed: %s", exc)

    return json.dumps({"dimension": dim, "matches": [], "note": "No matches found"})


def _handle_get_dimension_members(args: dict, cube_schema: dict) -> str:
    dim_name = args.get("dimension", "")
    for d in cube_schema.get("dimensions", []):
        if d.get("name") == dim_name:
            return json.dumps({
                "dimension": dim_name,
                "elements": d.get("elements", [])[:40],
                "consolidations": d.get("consolidations", [])[:20],
                "top_consolidations": d.get("top_consolidations", [])[:5],
                "default_element": d.get("default_element", ""),
            })
    return json.dumps({"error": f"Dimension '{dim_name}' not found in cube schema"})


def _handle_get_cube_summary(args: dict, cube_schema: dict) -> str:
    cube_name = str(args.get("cube", "") or cube_schema.get("cube", "")).strip()
    if not cube_name:
        return json.dumps({"error": "cube name required"})

    row = connect().execute(
        "SELECT business_purpose, grain, best_for, avoid_for, measures, default_filters"
        " FROM cube_summaries WHERE cube_name = ?",
        (cube_name,),
    ).fetchone()

    if row:
        def _loads(val: str | None, fallback) -> object:
            try:
                return json.loads(val or "null") or fallback
            except Exception:
                return fallback

        return json.dumps({
            "cube": cube_name,
            "business_purpose": row[0] or "",
            "grain": _loads(row[1], []),
            "best_for": _loads(row[2], []),
            "avoid_for": _loads(row[3], []),
            "measures": _loads(row[4], []),
            "default_filters": _loads(row[5], {}),
        })

    dims = [d.get("name", "") for d in cube_schema.get("dimensions", []) if d.get("name")]
    measures = [d.get("name", "") for d in cube_schema.get("dimensions", []) if d.get("is_measure")]
    return json.dumps({
        "cube": cube_name,
        "note": "No offline summary available — derived from schema.",
        "dimensions": dims,
        "measure_dimensions": measures,
    })


def _handle_list_views_from_cube(args: dict, cube_schema: dict) -> str:
    cube_name = str(args.get("cube", "") or cube_schema.get("cube", "")).strip()
    if not cube_name:
        return json.dumps({"error": "cube name required"})
    views = list_cube_views(cube_name)
    return json.dumps({"cube": cube_name, "views": views[:80], "count": len(views)})


def _handle_get_mdx_from_view(args: dict, cube_schema: dict) -> str:
    cube_name = str(args.get("cube", "") or cube_schema.get("cube", "")).strip()
    view_name = str(args.get("view", "")).strip()
    private = bool(args.get("private", False))
    if not cube_name or not view_name:
        return json.dumps({"error": "cube and view are required"})
    mdx = get_cube_view_mdx(cube_name, view_name, private=private)
    return json.dumps({"cube": cube_name, "view": view_name, "private": private, "mdx": mdx})


def _handle_get_element_attributes(args: dict, cube_schema: dict) -> str:
    dim_name = str(args.get("dimension", "")).strip()
    elements: list[str] = [str(e) for e in (args.get("elements") or []) if e]

    conn = connect()
    if elements:
        placeholders = ",".join("?" * len(elements))
        lower_elems = [e.lower() for e in elements]
        attr_rows = conn.execute(
            f"SELECT element_name, attr_name, attr_value"
            f" FROM element_attribute_values"
            f" WHERE dim_name = ? AND LOWER(element_name) IN ({placeholders})",
            [dim_name, *lower_elems],
        ).fetchall()
        alias_rows = conn.execute(
            f"SELECT element_name, alias_value FROM element_aliases"
            f" WHERE dim_name = ? AND LOWER(element_name) IN ({placeholders})",
            [dim_name, *lower_elems],
        ).fetchall()
    else:
        attr_rows = conn.execute(
            "SELECT element_name, attr_name, attr_value"
            " FROM element_attribute_values WHERE dim_name = ? LIMIT 300",
            (dim_name,),
        ).fetchall()
        alias_rows = conn.execute(
            "SELECT element_name, alias_value FROM element_aliases"
            " WHERE dim_name = ? LIMIT 50",
            (dim_name,),
        ).fetchall()

    result: dict[str, dict[str, str]] = {}
    for ename, aname, aval in attr_rows:
        if aval:
            result.setdefault(str(ename), {})[str(aname)] = str(aval)
    for ename, aval in alias_rows:
        if aval:
            result.setdefault(str(ename), {}).setdefault("Alias", str(aval))

    if not result:
        return json.dumps({
            "dimension": dim_name,
            "attributes": {},
            "note": "No attribute values found for the requested elements.",
        })
    return json.dumps({"dimension": dim_name, "attributes": result})


def _handle_validate_mdx(args: dict, cube_schema: dict) -> str:
    import re as _re
    mdx = str(args.get("mdx", "")).strip()
    if not mdx:
        return json.dumps({"valid": False, "errors": ["MDX is empty"], "warnings": []})

    cube_name = str(cube_schema.get("cube", "")).strip()
    errors: list[str] = []
    warnings: list[str] = []

    if not _re.search(r"(?i)^\s*SELECT\b", mdx):
        errors.append("Statement must start with SELECT")

    if cube_name and not _re.search(
        rf"(?i)\bFROM\s+\[{_re.escape(cube_name)}\]", mdx
    ):
        errors.append(f"Missing or wrong cube in FROM clause — expected FROM [{cube_name}]")

    if not _re.search(r"(?i)\bON\s+(COLUMNS|0)\b", mdx):
        errors.append("Missing ON COLUMNS axis")

    open_b, close_b = mdx.count("["), mdx.count("]")
    if open_b != close_b:
        errors.append(f"Unbalanced brackets: {open_b} '[' vs {close_b} ']'")

    from_m = _re.search(r"(?i)\bFROM\b", mdx)
    where_m = _re.search(r"(?i)\bWHERE\b", mdx)
    if from_m and where_m and where_m.start() < from_m.start():
        warnings.append("WHERE appears before FROM — normalize_mdx will reorder, but verify intent")

    schema_dim_names = {
        str(d.get("name", "")).lower()
        for d in cube_schema.get("dimensions", [])
        if d.get("name")
    }
    mentioned_dims = {
        dim
        for dim, _hier, _elem in _re.findall(
            r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\]", mdx
        )
        if dim.lower() not in schema_dim_names
        and dim.lower() != cube_name.lower()
    }
    if mentioned_dims:
        warnings.append(f"Dimension(s) not in schema: {sorted(mentioned_dims)}")

    return json.dumps({"valid": not errors, "errors": errors, "warnings": warnings})


def _handle_get_children(args: dict, cube_schema: dict) -> str:
    dim_name = str(args.get("dimension", "")).strip()
    element = str(args.get("element", "")).strip()

    rows = connect().execute(
        "SELECT child_name FROM element_edges"
        " WHERE dim_name = ? AND LOWER(parent_name) = LOWER(?)"
        " ORDER BY child_name LIMIT 100",
        (dim_name, element),
    ).fetchall()

    children = [r[0] for r in rows]
    if not children:
        return json.dumps({
            "dimension": dim_name,
            "element": element,
            "children": [],
            "note": "No children found. Element may be a leaf node or not exist.",
        })
    return json.dumps({"dimension": dim_name, "element": element, "children": children})


# ── Registry ──────────────────────────────────────────────────────────────────

ELEMENT_REGISTRY: ToolRegistry = ToolRegistry()

ELEMENT_REGISTRY.register(ToolSpec(
    name="search_elements",
    description=(
        "Search for TM1 dimension elements matching a term. "
        "Use when uncertain whether an element name exists or what its exact "
        "spelling is (entity names, account labels, scenario names, etc.)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "dimension": {
                "type": "string",
                "description": "TM1 dimension name to search within.",
            },
            "term": {
                "type": "string",
                "description": "Search term (e.g. 'Singapore', 'labor cost', 'actual').",
            },
        },
        "required": ["dimension", "term"],
    },
    handler=_handle_search_elements,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="get_dimension_members",
    description=(
        "Get elements and consolidations for a specific dimension. "
        "Use when you need to see available members or find the right "
        "consolidation parent for a Descendants/Children call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "dimension": {
                "type": "string",
                "description": "TM1 dimension name.",
            },
        },
        "required": ["dimension"],
    },
    handler=_handle_get_dimension_members,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="get_cube_summary",
    description=(
        "Get the pre-analyzed business summary for a TM1 cube: what it tracks, "
        "data grain, which question types it answers well, and which to avoid. "
        "Call this when you are unsure whether the cube contains the data the "
        "user wants, or to understand available measures and default filters."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cube": {
                "type": "string",
                "description": "TM1 cube name (omit to use the current cube).",
            },
        },
        "required": [],
    },
    handler=_handle_get_cube_summary,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="list_views_from_cube",
    description=(
        "List public/private TM1 cube views for the current cube. For financial "
        "statement requests such as P&L, balance sheet, or cash flow, call this "
        "early to find a trusted existing view before writing MDX from scratch."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cube": {
                "type": "string",
                "description": "TM1 cube name (omit to use the current cube).",
            },
        },
        "required": [],
    },
    handler=_handle_list_views_from_cube,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="get_mdx_from_view",
    description=(
        "Get the MDX definition from a named TM1 cube view. Use this after "
        "list_views_from_cube finds a relevant statement/reporting view; adapt "
        "only the requested filters instead of inventing a new row structure."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cube": {
                "type": "string",
                "description": "TM1 cube name (omit to use the current cube).",
            },
            "view": {
                "type": "string",
                "description": "TM1 view name.",
            },
            "private": {
                "type": "boolean",
                "description": "Whether the view is private.",
            },
        },
        "required": ["view"],
    },
    handler=_handle_get_mdx_from_view,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="get_element_attributes",
    description=(
        "Get attribute values (aliases, display labels, descriptions) for one or "
        "more elements in a dimension. Use when: (1) the user wants human-readable "
        "names for coded IDs, (2) you need to find an element by its alias, or "
        "(3) you want to verify what a coded element actually represents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "dimension": {
                "type": "string",
                "description": "TM1 dimension name.",
            },
            "elements": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Element names to look up. Leave empty to fetch attributes "
                    "for all elements in the dimension (limited to 50)."
                ),
            },
        },
        "required": ["dimension"],
    },
    handler=_handle_get_element_attributes,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="validate_mdx",
    description=(
        "Validate the structure of an MDX SELECT statement before executing it. "
        "Checks: SELECT/FROM/ON COLUMNS presence, correct cube name in FROM, "
        "balanced brackets, and whether all dimension names exist in the schema. "
        "Call this when you are uncertain about MDX structure to catch errors "
        "without consuming an execute_mdx attempt."
    ),
    parameters={
        "type": "object",
        "properties": {
            "mdx": {
                "type": "string",
                "description": "The MDX SELECT statement to validate.",
            },
        },
        "required": ["mdx"],
    },
    handler=_handle_validate_mdx,
))

ELEMENT_REGISTRY.register(ToolSpec(
    name="get_children",
    description=(
        "Get the immediate children of a consolidated element in a dimension "
        "hierarchy. Call this before using Descendants() or .Children in MDX "
        "to verify the hierarchy structure and avoid returning too many rows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "dimension": {
                "type": "string",
                "description": "TM1 dimension name.",
            },
            "element": {
                "type": "string",
                "description": "Consolidated element whose children you want.",
            },
        },
        "required": ["dimension", "element"],
    },
    handler=_handle_get_children,
))
