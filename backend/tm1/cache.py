"""
SQLite schema cache for TM1 cube / dimension / element metadata.

Why this exists
---------------
Querying TM1 for element lists on every user request is slow and puts load
on the server.  More importantly, the AI needs a reliable element→dimension
map to place elements in the correct MDX axis.  The live-query approach only
samples 60 elements per dimension, so rare elements can be missed.  The cache
stores ALL elements for ALL dimensions, making the lookup 100 % accurate.

Sync strategy
-------------
* Auto-sync on first startup (cache is empty).
* Manual re-sync via POST /api/sync-schema whenever TM1 data changes.
* The sync opens ONE TM1 connection, fetches everything, then writes to
  SQLite in a single transaction — no partial updates.

Tables
------
cubes           name, description, measure_dim
dim_in_cube     cube_name, dim_name, position, is_measure, is_time_dim
elements        dim_name, element_name, element_type
dim_attributes  dim_name, attribute_name, attribute_type
element_aliases dim_name, element_name, alias_value
                One row per element, populated only for dimensions that have
                at least one Alias-type attribute.  Used by
                build_structured_preview to substitute numeric IDs with
                human-readable display names (e.g. "2" → "John Smith").
"""

import re
import sqlite3
from datetime import date
from pathlib import Path

from TM1py import TM1Service

from ..config import TM1_CONFIG

_DB_PATH = Path(__file__).parent.parent / "schema_cache.db"

# Patterns used to detect time/period dimensions from element names
_TIME_DIM_NAME = re.compile(
    r"\b(period|month|calendar|fiscal|year|quarter|week|date|time)\b", re.I
)
_TIME_ELEM_RE = [
    re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I),
    re.compile(r"^(january|february|march|april|may|june|july|august"
               r"|september|october|november|december)", re.I),
    re.compile(r"^(0[1-9]|1[0-2])$"),
    re.compile(r"^q[1-4](\b|$)", re.I),
    re.compile(r"^(19|20)\d{2}$"),
    re.compile(r"^(fy|cy)\d{2,4}$", re.I),
    re.compile(r"^(period|month|quarter|week|wk)\s*\d+", re.I),
]


def _detect_time_dim(dim_name: str, leaf_names: list[str]) -> bool:
    """Return True if dimension name or majority of leaf elements look time-like."""
    if _TIME_DIM_NAME.search(dim_name):
        return True
    if not leaf_names:
        return False
    matches = sum(
        1 for n in leaf_names
        if any(p.match(str(n).strip()) for p in _TIME_ELEM_RE)
    )
    return matches >= len(leaf_names) * 0.5


def _attr_type_value(raw) -> str:
    """Normalise TM1py ElementAttributeTypes enum → plain string (String/Numeric/Alias)."""
    if raw is None:
        return "String"
    if hasattr(raw, "value"):
        return str(raw.value)
    s = str(raw)
    for t in ("Numeric", "Alias", "String"):
        if t.lower() in s.lower():
            return t
    return "String"


def _elem_type_value(raw) -> str:
    """Normalise TM1py ElementTypes enum → plain string (Numeric/String/Consolidated)."""
    if raw is None:
        return "Numeric"
    if hasattr(raw, "value"):          # enum member
        return str(raw.value)
    s = str(raw)
    for t in ("Consolidated", "String", "Numeric"):
        if t.lower() in s.lower():
            return t
    return "Numeric"


def _normalise_month(value: object) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{1,2})", text)
    if not match:
        return text
    month = max(1, min(12, int(match.group(1))))
    return f"{month:02d}"


def _fallback_current_period() -> dict[str, str | bool]:
    today = date.today()
    return {
        "Year": str(today.year),
        "Month": f"{today.month:02d}",
        "current_year": str(today.year),
        "current_month": f"{today.month:02d}",
        "current_day": str(today.day),
        "source": "server_date",
        "used_real_current_date": True,
    }


def get_current_period_defaults() -> dict[str, str | bool]:
    """
    Return current-period defaults from the TM1 Sys Parameter cube.

    Falls back to the backend server date when the cube or cells are unavailable.
    Expected TM1 parameters include:
      - Current Actual Year
      - Current Actual Month (e.g. M05)
      - Current Actual Day in Month
    """
    fallback = _fallback_current_period()
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            cube_name = "Sys Parameter"
            dims = list(tm1.cubes.get_dimension_names(cube_name))
            measure_dim = tm1.cubes.get_measure_dimension(cube_name)
            param_dim = next((d for d in dims if d != measure_dim), "")
            if not param_dim or not measure_dim:
                return fallback

            measure_elements = list(tm1.elements.get_element_names(measure_dim, measure_dim))
            text_measure = next((m for m in measure_elements if m.lower() == "text"), None)
            if not text_measure:
                return fallback

            params = [
                "Current Actual Year",
                "Current Actual Month",
                "Current Actual Day in Month",
                "Current Actual Week",
                "Current Forecast Year",
            ]
            rows = ", ".join(f"[{param_dim}].[{param_dim}].[{p}]" for p in params)
            mdx = (
                f"SELECT {{[{measure_dim}].[{measure_dim}].[{text_measure}]}} ON COLUMNS, "
                f"{{{rows}}} ON ROWS FROM [{cube_name}]"
            )
            cellset = tm1.cells.execute_mdx(mdx, skip_zeros=False)

        values: dict[str, str] = {}
        for coords, cell in cellset.items():
            param = ""
            for coord in coords:
                text = str(coord)
                if text.startswith(f"[{param_dim}]."):
                    param = text.rsplit("[", 1)[-1][:-1]
                    break
            raw_value = cell.get("Value") if isinstance(cell, dict) else cell
            if param and raw_value not in (None, ""):
                values[param] = str(raw_value).strip()

        year = values.get("Current Actual Year") or fallback["Year"]
        month = _normalise_month(values.get("Current Actual Month") or fallback["Month"])
        day = values.get("Current Actual Day in Month") or fallback["current_day"]
        return {
            "Year": str(year),
            "Month": month,
            "current_year": str(year),
            "current_month": month,
            "current_day": str(day),
            "current_week": values.get("Current Actual Week", ""),
            "forecast_year": values.get("Current Forecast Year", ""),
            "source": "tm1_sys_parameter",
            "used_real_current_date": False,
        }
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_schema_db() -> None:
    """Create tables and indexes. Safe to call on every startup (idempotent)."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cubes (
                name        TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                measure_dim TEXT DEFAULT '',
                synced_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS dim_in_cube (
                cube_name   TEXT    NOT NULL,
                dim_name    TEXT    NOT NULL,
                position    INTEGER NOT NULL,
                is_measure  INTEGER DEFAULT 0,
                is_time_dim INTEGER DEFAULT 0,
                PRIMARY KEY (cube_name, dim_name)
            );

            CREATE TABLE IF NOT EXISTS elements (
                dim_name     TEXT NOT NULL,
                element_name TEXT NOT NULL,
                element_type TEXT NOT NULL DEFAULT 'Numeric',
                PRIMARY KEY (dim_name, element_name)
            );

            CREATE INDEX IF NOT EXISTS idx_elem_lower
                ON elements (LOWER(element_name), dim_name);

            CREATE TABLE IF NOT EXISTS dim_attributes (
                dim_name       TEXT NOT NULL,
                attribute_name TEXT NOT NULL,
                attribute_type TEXT NOT NULL DEFAULT 'String',
                PRIMARY KEY (dim_name, attribute_name)
            );

            CREATE TABLE IF NOT EXISTS element_aliases (
                dim_name     TEXT NOT NULL,
                element_name TEXT NOT NULL,
                alias_value  TEXT NOT NULL,
                PRIMARY KEY (dim_name, element_name)
            );

            CREATE TABLE IF NOT EXISTS element_attribute_values (
                dim_name     TEXT NOT NULL,
                element_name TEXT NOT NULL,
                attr_name    TEXT NOT NULL,
                attr_value   TEXT NOT NULL,
                PRIMARY KEY (dim_name, element_name, attr_name)
            );
        """)
        # Migration: add new columns to existing databases without resetting
        _add_col_if_missing(conn, "elements",    "element_type", "TEXT NOT NULL DEFAULT 'Numeric'")
        _add_col_if_missing(conn, "dim_in_cube", "is_time_dim",  "INTEGER DEFAULT 0")


def _add_col_if_missing(conn: sqlite3.Connection, table: str, col: str, defn: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")


def is_empty() -> bool:
    """True when the cache has never been populated."""
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM cubes").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_schema() -> dict:
    """
    Pull all cubes, dimensions, and elements from TM1 and write to SQLite.

    Returns a summary: {"cubes": n, "dims": n, "elements": n}
    """
    with TM1Service(**TM1_CONFIG) as tm1:
        all_names = tm1.cubes.get_all_names(skip_control_cubes=True) or []
        cube_names = [
            n for n in all_names
            if not n.startswith("}") and not n.startswith("Sys")
        ]

        try:
            desc_map = (
                tm1.elements.get_attribute_of_elements("}Cubes", "}Cubes", "Description")
                or {}
            )
        except Exception:
            desc_map = {}

        cube_dims: dict[str, list[str]] = {}
        cube_measure: dict[str, str] = {}
        for cube_name in cube_names:
            try:
                cube_dims[cube_name] = tm1.cubes.get_dimension_names(cube_name)
                cube_measure[cube_name] = tm1.cubes.get_measure_dimension(cube_name)
            except Exception:
                continue

        unique_dims = {dim for dims in cube_dims.values() for dim in dims}

        # Fetch elements WITH their types (Numeric / String / Consolidated)
        dim_elements: dict[str, list[tuple[str, str]]] = {}
        dim_is_time:  dict[str, bool] = {}
        dim_attrs:    dict[str, list[tuple[str, str]]] = {}

        for dim_name in unique_dims:
            try:
                elems = tm1.elements.get_elements(dim_name, dim_name)
                pairs: list[tuple[str, str]] = [
                    (e.name, _elem_type_value(e.element_type)) for e in elems
                ]
                leaf_names = [name for name, etype in pairs if etype != "Consolidated"]
                dim_elements[dim_name] = pairs
                dim_is_time[dim_name] = _detect_time_dim(dim_name, leaf_names)
            except Exception:
                # Fallback: names only, type unknown
                try:
                    names = list(tm1.elements.get_element_names(dim_name, dim_name))
                    dim_elements[dim_name] = [(n, "Numeric") for n in names]
                    dim_is_time[dim_name] = _detect_time_dim(dim_name, names)
                except Exception:
                    dim_elements[dim_name] = []
                    dim_is_time[dim_name] = False

            # Fetch attribute definitions (names + types) for this dimension
            try:
                attr_objs = tm1.elements.get_element_attributes(dim_name, dim_name)
                dim_attrs[dim_name] = [
                    (a.name, _attr_type_value(getattr(a, "attribute_type", None)))
                    for a in attr_objs
                ]
            except Exception:
                dim_attrs[dim_name] = []

        # Fetch all String + Alias attribute values for every dimension.
        # Stored in element_attribute_values so the AI can apply any attribute
        # by name (e.g. "Employee Name", "Grade", "Department") without
        # needing a new TM1 query.
        # element_aliases is also populated from the first Alias-type attribute
        # for backward-compatibility with build_structured_preview defaults.
        dim_alias_values: dict[str, dict[str, str]] = {}
        dim_all_attr_values: dict[str, dict[str, dict[str, str]]] = {}  # {dim: {attr: {elem: val}}}
        for dim_name in unique_dims:
            for attr_name, atype in dim_attrs.get(dim_name, []):
                if atype not in ("Alias", "String"):
                    continue
                try:
                    raw = tm1.elements.get_attribute_of_elements(
                        dim_name, dim_name, attr_name
                    ) or {}
                    values = {k: str(v).strip() for k, v in raw.items() if v and str(v).strip()}
                    if not values:
                        continue
                    dim_all_attr_values.setdefault(dim_name, {})[attr_name] = values
                    # First Alias-type attribute → backward-compat element_aliases
                    if atype == "Alias" and dim_name not in dim_alias_values:
                        dim_alias_values[dim_name] = values
                except Exception:
                    pass

    # Write atomically to SQLite
    with _connect() as conn:
        conn.execute("DELETE FROM element_attribute_values")
        conn.execute("DELETE FROM element_aliases")
        conn.execute("DELETE FROM dim_attributes")
        conn.execute("DELETE FROM elements")
        conn.execute("DELETE FROM dim_in_cube")
        conn.execute("DELETE FROM cubes")

        for cube_name, dims in cube_dims.items():
            desc    = str(desc_map.get(cube_name, "")).strip()
            measure = cube_measure.get(cube_name, "")
            conn.execute(
                "INSERT INTO cubes(name, description, measure_dim) VALUES (?, ?, ?)",
                (cube_name, desc, measure),
            )
            for pos, dim_name in enumerate(dims):
                conn.execute(
                    "INSERT INTO dim_in_cube"
                    "(cube_name, dim_name, position, is_measure, is_time_dim)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        cube_name, dim_name, pos,
                        1 if dim_name == measure else 0,
                        1 if dim_is_time.get(dim_name, False) else 0,
                    ),
                )

        total_elems = 0
        for dim_name, pairs in dim_elements.items():
            if pairs:
                conn.executemany(
                    "INSERT OR IGNORE INTO elements(dim_name, element_name, element_type)"
                    " VALUES (?, ?, ?)",
                    [(dim_name, name, etype) for name, etype in pairs],
                )
                total_elems += len(pairs)

        for dim_name, attrs in dim_attrs.items():
            if attrs:
                conn.executemany(
                    "INSERT OR IGNORE INTO dim_attributes(dim_name, attribute_name, attribute_type)"
                    " VALUES (?, ?, ?)",
                    [(dim_name, name, atype) for name, atype in attrs],
                )

        total_aliases = 0
        for dim_name, alias_map in dim_alias_values.items():
            if alias_map:
                conn.executemany(
                    "INSERT OR IGNORE INTO element_aliases(dim_name, element_name, alias_value)"
                    " VALUES (?, ?, ?)",
                    [(dim_name, elem, alias) for elem, alias in alias_map.items()],
                )
                total_aliases += len(alias_map)

        total_attr_vals = 0
        for dim_name, attr_dict in dim_all_attr_values.items():
            for attr_name, elem_vals in attr_dict.items():
                if elem_vals:
                    conn.executemany(
                        "INSERT OR IGNORE INTO element_attribute_values"
                        "(dim_name, element_name, attr_name, attr_value) VALUES (?, ?, ?, ?)",
                        [(dim_name, elem, attr_name, val) for elem, val in elem_vals.items()],
                    )
                    total_attr_vals += len(elem_vals)

    return {
        "cubes": len(cube_dims),
        "dims": len(unique_dims),
        "elements": total_elems,
        "aliases": total_aliases,
        "attribute_values": total_attr_vals,
    }


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def lookup_element_dim(element_name: str, candidate_dims: list[str] | None = None) -> str | None:
    """
    Return the dimension that owns *element_name*, or None if not found.

    candidate_dims  limits the search to the cube's own dimensions, which
    prevents false matches when the same element name exists in multiple dims.
    """
    with _connect() as conn:
        if candidate_dims:
            placeholders = ",".join("?" * len(candidate_dims))
            row = conn.execute(
                f"SELECT dim_name FROM elements"
                f" WHERE LOWER(element_name) = LOWER(?)"
                f"   AND dim_name IN ({placeholders})"
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


def get_cubes_cached() -> list[dict]:
    """All non-system cubes with their descriptions."""
    with _connect() as conn:
        rows = conn.execute("SELECT name, description FROM cubes").fetchall()
    return [{"cube": r[0], "description": r[1] or r[0]} for r in rows]


def get_cube_schema_cached(cube_name: str) -> dict | None:
    """
    Full schema for one cube from cache.
    Returns None if the cube isn't cached yet.
    Element list is capped at 60 per dimension to keep AI prompts manageable
    (the full list is still in SQLite for accurate element→dim lookups).
    """
    with _connect() as conn:
        cube_row = conn.execute(
            "SELECT measure_dim FROM cubes WHERE name = ?", (cube_name,)
        ).fetchone()
        if not cube_row:
            return None
        measure_dim = cube_row[0]

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
            attrs = conn.execute(
                "SELECT attribute_name, attribute_type FROM dim_attributes"
                " WHERE dim_name = ? ORDER BY attribute_name",
                (dim_name,),
            ).fetchall()
            dimensions.append({
                "name":        dim_name,
                "is_measure":  bool(is_measure),
                "is_time_dim": bool(is_time_dim),
                "usage":       "",
                "elements":    [r[0] for r in elems],
                "attributes":  [{"name": r[0], "type": r[1]} for r in attrs],
            })

    return {"cube": cube_name, "dimensions": dimensions}


def get_last_synced_at() -> str | None:
    """Return the most recent synced_at timestamp from the cubes table, or None."""
    with _connect() as conn:
        row = conn.execute("SELECT MAX(synced_at) FROM cubes").fetchone()
    return row[0] if row and row[0] else None


def get_alias_maps(dim_names: list[str]) -> dict[str, dict[str, str]]:
    """
    Return alias lookup tables for the given dimensions.

    Result shape: {dim_name: {element_name: alias_value}}
    Only dimensions that have at least one alias entry are included.
    Used by build_structured_preview to replace element IDs with display names.
    """
    if not dim_names:
        return {}
    placeholders = ",".join("?" * len(dim_names))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT dim_name, element_name, alias_value FROM element_aliases"
            f" WHERE dim_name IN ({placeholders})",
            dim_names,
        ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for dim_name, elem_name, alias_val in rows:
        result.setdefault(dim_name, {})[elem_name] = alias_val
    return result


def get_alias_attribute_names(dim_names: list[str]) -> dict[str, str]:
    """
    Return {dim_name: attr_name} for the first Alias-type attribute in each dimension.
    Used to derive the display column header when applying default alias substitution.
    """
    if not dim_names:
        return {}
    placeholders = ",".join("?" * len(dim_names))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT dim_name, MIN(attribute_name) FROM dim_attributes"
            f" WHERE dim_name IN ({placeholders}) AND attribute_type = 'Alias'"
            f" GROUP BY dim_name",
            dim_names,
        ).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def get_named_attribute_map(dim_name: str, attr_name: str) -> dict[str, str]:
    """
    Return {element_name: attr_value} for one specific dimension attribute.
    Case-insensitive match on attr_name.
    Used when the AI explicitly requests a particular attribute for display substitution.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT element_name, attr_value FROM element_attribute_values"
            " WHERE dim_name = ? AND LOWER(attr_name) = LOWER(?)",
            (dim_name, attr_name),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_dim_metadata(dim_names: list[str]) -> dict[str, dict]:
    """
    Return per-dimension metadata for a list of dimension names.

    Result shape: {dim_name: {"is_time_dim": bool, "consolidated": set[str]}}
    Used by build_structured_preview to annotate chart metadata.
    """
    if not dim_names:
        return {}

    placeholders = ",".join("?" * len(dim_names))
    with _connect() as conn:
        # is_time_dim from any cube that contains this dimension
        time_rows = conn.execute(
            f"SELECT DISTINCT dim_name, MAX(is_time_dim)"
            f" FROM dim_in_cube WHERE dim_name IN ({placeholders})"
            f" GROUP BY dim_name",
            dim_names,
        ).fetchall()
        is_time_map = {r[0]: bool(r[1]) for r in time_rows}

        # Consolidated element names per dimension
        cons_rows = conn.execute(
            f"SELECT dim_name, element_name FROM elements"
            f" WHERE dim_name IN ({placeholders})"
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


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
