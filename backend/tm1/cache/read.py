"""Public read helpers backed by the SQLite schema cache."""

from .db import cache_scope_matches, connect, qmarks
from .defaults import looks_like_pnl_bottom_line, pick_default_element


# ── Element lookups ───────────────────────────────────────────────────────────

def lookup_element_dim(element_name: str, candidate_dims: list[str] | None = None) -> str | None:
    """Return the dimension that owns element_name, or None."""
    conn = connect()
    if not cache_scope_matches(conn):
        return None
    if candidate_dims:
        row = conn.execute(
            f"SELECT dim_name FROM elements"
            f" WHERE LOWER(element_name) = LOWER(?)"
            f"   AND dim_name IN ({qmarks(len(candidate_dims))})"
            f" LIMIT 1",
            [element_name, *candidate_dims],
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT dim_name FROM elements"
            " WHERE LOWER(element_name) = LOWER(?) LIMIT 1",
            (element_name,),
        ).fetchone()
    return row[0] if row else None


def element_exists(dim_name: str, element_name: str) -> bool:
    conn = connect()
    if not cache_scope_matches(conn):
        return False
    row = conn.execute(
        "SELECT 1 FROM elements"
        " WHERE dim_name = ? AND LOWER(element_name) = LOWER(?)"
        " LIMIT 1",
        (dim_name, element_name),
    ).fetchone()
    return bool(row)


def is_consolidated_element(dim_name: str, element_name: str) -> bool:
    conn = connect()
    if not cache_scope_matches(conn):
        return False
    row = conn.execute(
        "SELECT 1 FROM elements"
        " WHERE dim_name = ? AND LOWER(element_name) = LOWER(?)"
        "   AND element_type = 'Consolidated'"
        " LIMIT 1",
        (dim_name, element_name),
    ).fetchone()
    return bool(row)


# ── Whole-cube schema (for AI prompting) ──────────────────────────────────────

def get_cube_schema_cached(cube_name: str) -> dict | None:
    """Full schema for one cube from cache, or None if uncached.

    Element list is capped at 60 per dimension to keep AI prompts manageable.
    """
    conn = connect()
    if not cache_scope_matches(conn):
        return None
    cube_row = conn.execute(
        "SELECT measure_dim FROM cubes WHERE name = ?", (cube_name,)
    ).fetchone()
    if not cube_row:
        return None

    dim_rows = conn.execute(
        "SELECT dim_name, is_measure, is_time_dim FROM dim_in_cube"
        " WHERE cube_name = ? ORDER BY position",
        (cube_name,),
    ).fetchall()

    dimensions = []
    for dim_name, is_measure, is_time_dim in dim_rows:
        elems = conn.execute(
            "SELECT element_name FROM elements WHERE dim_name = ?"
            " ORDER BY CASE WHEN LOWER(element_name) LIKE 'all%'"
            "               OR LOWER(element_name) = 'total' THEN 0 ELSE 1 END,"
            " CASE WHEN element_name GLOB '[0-9]*'"
            "        AND element_name NOT GLOB '*[^0-9]*' THEN 0 ELSE 1 END,"
            " CASE WHEN element_name GLOB '[0-9]*'"
            "        AND element_name NOT GLOB '*[^0-9]*'"
            "      THEN CAST(element_name AS INTEGER) END,"
            " element_name LIMIT 60",
            (dim_name,),
        ).fetchall()
        consolidations = conn.execute(
            "SELECT element_name FROM elements"
            " WHERE dim_name = ? AND element_type = 'Consolidated'"
            " ORDER BY CASE WHEN LOWER(element_name) LIKE '%profit%loss%'"
            "               OR LOWER(element_name) LIKE '%p&l%'"
            "               OR LOWER(element_name) LIKE '%pnl%'"
            "               OR LOWER(element_name) LIKE '%income statement%' THEN 0"
            "               WHEN LOWER(element_name) LIKE 'all%'"
            "               OR LOWER(element_name) LIKE 'total%' THEN 1 ELSE 2 END,"
            " element_name LIMIT 30",
            (dim_name,),
        ).fetchall()
        all_top_rows = conn.execute(
            "SELECT e.element_name FROM elements e"
            " WHERE e.dim_name = ? AND e.element_type = 'Consolidated'"
            "   AND NOT EXISTS ("
            "       SELECT 1 FROM element_edges ee"
            "       WHERE ee.dim_name = e.dim_name AND ee.child_name = e.element_name"
            "   )"
            " ORDER BY CASE WHEN LOWER(e.element_name) LIKE 'all%'"
            "               OR LOWER(e.element_name) LIKE 'total%' THEN 0 ELSE 1 END,"
            " e.element_name",
            (dim_name,),
        ).fetchall()
        attrs = conn.execute(
            "SELECT attribute_name, attribute_type FROM dim_attributes"
            " WHERE dim_name = ? ORDER BY attribute_name",
            (dim_name,),
        ).fetchall()
        attr_value_rows = conn.execute(
            "SELECT element_name, attr_name, attr_value FROM element_attribute_values"
            " WHERE dim_name = ?",
            (dim_name,),
        ).fetchall()
        element_attr_values: dict[str, dict[str, str]] = {}
        for ename, aname, aval in attr_value_rows:
            if not ename or not aname or aval in (None, ""):
                continue
            element_attr_values.setdefault(str(ename), {})[str(aname)] = str(aval)
        alias_rows = conn.execute(
            "SELECT element_name, alias_value FROM element_aliases WHERE dim_name = ?",
            (dim_name,),
        ).fetchall()
        for ename, aval in alias_rows:
            if not ename or aval in (None, ""):
                continue
            element_attr_values.setdefault(str(ename), {}).setdefault("Alias", str(aval))

        edge_rows = conn.execute(
            "SELECT parent_name, child_name, weight FROM element_edges WHERE dim_name = ?",
            (dim_name,),
        ).fetchall()
        child_weights: dict[str, float] = {}
        for _parent, child, weight in edge_rows:
            if child:
                try:
                    w = float(weight) if weight is not None else 1.0
                except (TypeError, ValueError):
                    w = 1.0
                child_weights[str(child)] = max(child_weights.get(str(child), 0.0), w)

        all_top_names = [r[0] for r in all_top_rows]
        pnl_first = [n for n in all_top_names if looks_like_pnl_bottom_line(n)]
        non_pnl = [n for n in all_top_names if not looks_like_pnl_bottom_line(n)]
        top_names = pnl_first + non_pnl
        consolidation_names = [r[0] for r in consolidations]
        element_names = [r[0] for r in elems]
        default = pick_default_element(
            dim_name,
            all_top_names,
            consolidation_names,
            element_names,
        )
        dimensions.append({
            "name":               dim_name,
            "is_measure":         bool(is_measure),
            "is_time_dim":        bool(is_time_dim),
            "usage":              "",
            "elements":           element_names,
            "consolidations":     consolidation_names,
            "top_consolidations": top_names,
            "default_element":    default,
            "attributes":         [{"name": r[0], "type": r[1]} for r in attrs],
            "element_attr_values": element_attr_values,
            "child_weights":      child_weights,
        })

    return {"cube": cube_name, "dimensions": dimensions}


def get_cubes_cached() -> list[dict]:
    """All non-system cubes with their descriptions."""
    conn = connect()
    if not cache_scope_matches(conn):
        return []
    rows = conn.execute("SELECT name, description FROM cubes").fetchall()
    return [{"cube": r[0], "description": r[1] or r[0]} for r in rows]


def get_last_synced_at() -> str | None:
    conn = connect()
    if not cache_scope_matches(conn):
        return None
    row = conn.execute("SELECT MAX(synced_at) FROM cubes").fetchone()
    return row[0] if row and row[0] else None


# ── Alias + attribute lookups ────────────────────────────────────────────────

def get_alias_maps(dim_names: list[str]) -> dict[str, dict[str, str]]:
    """{dim_name: {element_name: alias_value}}"""
    if not dim_names:
        return {}
    conn = connect()
    if not cache_scope_matches(conn):
        return {}
    rows = conn.execute(
        f"SELECT dim_name, element_name, alias_value FROM element_aliases"
        f" WHERE dim_name IN ({qmarks(len(dim_names))})",
        dim_names,
    ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for dim_name, elem_name, alias_val in rows:
        result.setdefault(dim_name, {})[elem_name] = alias_val
    return result


def get_alias_attribute_names(dim_names: list[str]) -> dict[str, str]:
    """{dim_name: attr_name} for the first Alias-type attribute in each dim."""
    if not dim_names:
        return {}
    conn = connect()
    if not cache_scope_matches(conn):
        return {}
    rows = conn.execute(
        f"SELECT dim_name, MIN(attribute_name) FROM dim_attributes"
        f" WHERE dim_name IN ({qmarks(len(dim_names))}) AND attribute_type = 'Alias'"
        f" GROUP BY dim_name",
        dim_names,
    ).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def get_named_attribute_map(dim_name: str, attr_name: str) -> dict[str, str]:
    """{element_name: attr_value} for one specific attribute (case-insensitive)."""
    conn = connect()
    if not cache_scope_matches(conn):
        return {}
    rows = conn.execute(
        "SELECT element_name, attr_value FROM element_attribute_values"
        " WHERE dim_name = ? AND LOWER(attr_name) = LOWER(?)",
        (dim_name, attr_name),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_dim_metadata(dim_names: list[str]) -> dict[str, dict]:
    """{dim_name: {is_time_dim: bool, consolidated: set[str]}}"""
    if not dim_names:
        return {}

    conn = connect()
    if not cache_scope_matches(conn):
        return {}
    time_rows = conn.execute(
        f"SELECT DISTINCT dim_name, MAX(is_time_dim)"
        f" FROM dim_in_cube WHERE dim_name IN ({qmarks(len(dim_names))})"
        f" GROUP BY dim_name",
        dim_names,
    ).fetchall()
    is_time_map = {r[0]: bool(r[1]) for r in time_rows}

    cons_rows = conn.execute(
        f"SELECT dim_name, element_name FROM elements"
        f" WHERE dim_name IN ({qmarks(len(dim_names))})"
        f"   AND element_type = 'Consolidated'",
        dim_names,
    ).fetchall()

    consolidated: dict[str, set[str]] = {d: set() for d in dim_names}
    for dim_name, elem_name in cons_rows:
        consolidated[dim_name].add(elem_name)

    return {
        d: {
            "is_time_dim":  is_time_map.get(d, False),
            "consolidated": consolidated.get(d, set()),
        }
        for d in dim_names
    }


def get_dim_hierarchy_edges(dim_names: list[str]) -> dict[str, list[tuple[str, str]]]:
    if not dim_names:
        return {}
    conn = connect()
    if not cache_scope_matches(conn):
        return {}
    rows = conn.execute(
        f"SELECT dim_name, parent_name, child_name FROM element_edges"
        f" WHERE dim_name IN ({qmarks(len(dim_names))})",
        dim_names,
    ).fetchall()
    result: dict[str, list[tuple[str, str]]] = {d: [] for d in dim_names}
    for dim_name, parent_name, child_name in rows:
        result.setdefault(dim_name, []).append((parent_name, child_name))
    return result
