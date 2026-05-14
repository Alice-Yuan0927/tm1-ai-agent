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
element_edges   dim_name, parent_name, child_name, weight
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
    """Normalise TM1py ElementTypes enum → plain string (Numeric/String/Consolidated).

    TM1py's ElementTypes enum uses numeric values: NUMERIC=1, STRING=2, CONSOLIDATED=3.
    Earlier versions of this function returned `.value` (the int code) instead of the
    name, which silently broke every downstream "element_type = 'Consolidated'" query.
    """
    if raw is None:
        return "Numeric"
    # Prefer the enum's name (e.g. ElementTypes.CONSOLIDATED.name == "CONSOLIDATED").
    name = getattr(raw, "name", None)
    if name:
        candidate = str(name).strip().lower()
        for t in ("Consolidated", "String", "Numeric"):
            if t.lower() == candidate:
                return t
    s = str(raw).strip()
    # TM1py / REST API sometimes hands back integer codes as strings.
    code_map = {"1": "Numeric", "2": "String", "3": "Consolidated"}
    if s in code_map:
        return code_map[s]
    for t in ("Consolidated", "String", "Numeric"):
        if t.lower() in s.lower():
            return t
    return "Numeric"


def _component_name(component) -> str:
    for attr in ("element_name", "name", "component_name"):
        value = getattr(component, attr, None)
        if value:
            return str(value)
    if isinstance(component, dict):
        for key in ("element_name", "name", "component_name"):
            if component.get(key):
                return str(component[key])
    return str(component or "")


def _fetch_dim_edges(tm1, dim_name: str) -> list[tuple[str, str, float]]:
    """Fall-back hierarchy fetch when `element.components` came back empty.

    TM1py's `get_elements()` does not always populate the `components` field
    depending on server/REST version, so we try a few alternative APIs and
    keep the first one that returns edges. Order is most-direct to most-
    expensive.
    """
    elements_ns = getattr(tm1, "elements", None)
    if elements_ns is None:
        return []

    # 1. tm1.elements.get_edges(dim, hier) -> dict {(parent, child): weight}
    get_edges = getattr(elements_ns, "get_edges", None)
    if callable(get_edges):
        try:
            raw = get_edges(dim_name, dim_name)
            if isinstance(raw, dict):
                return [
                    (str(p), str(c), float(w) if w is not None else 1.0)
                    for (p, c), w in raw.items()
                    if p and c
                ]
            if raw:
                edges: list[tuple[str, str, float]] = []
                for item in raw:
                    if isinstance(item, (tuple, list)) and len(item) >= 2:
                        parent, child, *rest = item
                        weight = float(rest[0]) if rest and rest[0] is not None else 1.0
                        edges.append((str(parent), str(child), weight))
                if edges:
                    return edges
        except Exception:
            pass

    # 2. tm1.dimensions.hierarchies.get(dim, hier).edges
    try:
        hierarchies = tm1.dimensions.hierarchies.get(dim_name, dim_name)
        edges_obj = getattr(hierarchies, "edges", None)
        if edges_obj:
            if isinstance(edges_obj, dict):
                return [
                    (str(p), str(c), float(w) if w is not None else 1.0)
                    for (p, c), w in edges_obj.items()
                    if p and c
                ]
    except Exception:
        pass

    # 3. Per-consolidation children lookup (slowest, only if needed)
    try:
        cons_names = elements_ns.get_consolidated_element_names(dim_name, dim_name)
    except Exception:
        cons_names = []
    edges: list[tuple[str, str, float]] = []
    for parent in cons_names or []:
        try:
            children = elements_ns.get_members_under_consolidation(dim_name, dim_name, parent, max_depth=1)
            for c in children or []:
                child = str(getattr(c, "name", c))
                if child and child != parent:
                    edges.append((str(parent), child, 1.0))
        except Exception:
            continue
    return edges


def _component_weight(component) -> float:
    for attr in ("weight", "factor"):
        value = getattr(component, attr, None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 1.0
    if isinstance(component, dict):
        for key in ("weight", "factor"):
            if component.get(key) is not None:
                try:
                    return float(component[key])
                except (TypeError, ValueError):
                    return 1.0
    return 1.0


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

            CREATE TABLE IF NOT EXISTS element_edges (
                dim_name    TEXT NOT NULL,
                parent_name TEXT NOT NULL,
                child_name  TEXT NOT NULL,
                weight      REAL DEFAULT 1,
                PRIMARY KEY (dim_name, parent_name, child_name)
            );

            CREATE INDEX IF NOT EXISTS idx_element_edges_child
                ON element_edges (dim_name, child_name);

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
        dim_edges:    dict[str, list[tuple[str, str, float]]] = {}
        dim_is_time:  dict[str, bool] = {}
        dim_attrs:    dict[str, list[tuple[str, str]]] = {}

        for dim_name in unique_dims:
            try:
                elems = tm1.elements.get_elements(dim_name, dim_name)
                pairs: list[tuple[str, str]] = [
                    (e.name, _elem_type_value(e.element_type)) for e in elems
                ]
                # Try the in-element `components` attribute first; if TM1py
                # didn't populate it, fall through to `get_edges()` (or
                # equivalents) on the hierarchy.
                edges: list[tuple[str, str, float]] = []
                for element in elems:
                    if _elem_type_value(getattr(element, "element_type", None)) != "Consolidated":
                        continue
                    parent = str(getattr(element, "name", "") or "")
                    for component in getattr(element, "components", []) or []:
                        child = _component_name(component)
                        if child:
                            edges.append((parent, child, _component_weight(component)))
                if not edges:
                    edges = _fetch_dim_edges(tm1, dim_name)
                leaf_names = [name for name, etype in pairs if etype != "Consolidated"]
                dim_elements[dim_name] = pairs
                dim_edges[dim_name] = edges
                dim_is_time[dim_name] = _detect_time_dim(dim_name, leaf_names)
            except Exception:
                # Fallback: names only, type unknown
                try:
                    names = list(tm1.elements.get_element_names(dim_name, dim_name))
                    dim_elements[dim_name] = [(n, "Numeric") for n in names]
                    dim_edges[dim_name] = _fetch_dim_edges(tm1, dim_name)
                    dim_is_time[dim_name] = _detect_time_dim(dim_name, names)
                except Exception:
                    dim_elements[dim_name] = []
                    dim_edges[dim_name] = []
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
        conn.execute("DELETE FROM element_edges")
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

        total_edges = 0
        for dim_name, edges in dim_edges.items():
            if edges:
                conn.executemany(
                    "INSERT OR IGNORE INTO element_edges(dim_name, parent_name, child_name, weight)"
                    " VALUES (?, ?, ?, ?)",
                    [(dim_name, parent, child, weight) for parent, child, weight in edges],
                )
                total_edges += len(edges)

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
        "element_edges": total_edges,
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


_ELEMENT_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalise_element_token(value: str) -> str:
    """Lower-case and collapse non-alphanumeric runs to a single space."""
    return _ELEMENT_NORM_RE.sub(" ", (value or "").lower()).strip()


_IDENTIFIER_PUNCT_RE = re.compile(r"[-_/\.]")


def _is_strong_element_token(token: str) -> bool:
    """
    Only count an element as a 'strong' match for clarification suppression if
    it looks like a domain-specific identifier - rules out generic English
    words like 'Available' or 'Use' that happen to also be element names.
    """
    cleaned = token.strip()
    if not cleaned:
        return False
    if _IDENTIFIER_PUNCT_RE.search(cleaned):
        return True
    if len(cleaned.split()) >= 2:
        return True
    if any(ch.isdigit() for ch in cleaned):
        return True
    return False


def find_question_element_matches(question: str, *, max_phrase_words: int = 4, min_token_len: int = 3) -> list[tuple[str, str, str]]:
    """
    Find element names that appear inside the user's question after normalising
    punctuation and whitespace (so "SLIM-HK" matches "Slim HK"). Only returns
    elements that look like domain-specific identifiers, not generic English
    words that happen to also be element names.

    Returns a list of (matched_element, dim_name, element_name). Used to suppress
    premature schema-clarification questions: if the user typed a real element,
    the AI should not pretend it doesn't exist.
    """
    text = (question or "").strip()
    if not text:
        return []
    norm_question = " " + _normalise_element_token(text) + " "
    with _connect() as conn:
        rows = conn.execute(
            "SELECT dim_name, element_name FROM elements"
            " WHERE LENGTH(element_name) >= 2"
        ).fetchall()
    results: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for dim_name, element_name in rows:
        if not element_name:
            continue
        token = str(element_name).strip()
        if len(token.split()) > max_phrase_words:
            continue
        if not _is_strong_element_token(token):
            continue
        needle = _normalise_element_token(token)
        if len(needle.replace(" ", "")) < min_token_len:
            continue
        if f" {needle} " in norm_question:
            key = (dim_name, element_name)
            if key in seen:
                continue
            seen.add(key)
            results.append((token, dim_name, element_name))
    return results


def get_cubes_cached() -> list[dict]:
    """All non-system cubes with their descriptions."""
    with _connect() as conn:
        rows = conn.execute("SELECT name, description FROM cubes").fetchall()
    return [{"cube": r[0], "description": r[1] or r[0]} for r in rows]


def _pick_default_element(
    dim_name: str,
    top_consolidations: list[str],
    consolidations: list[str],
    elements: list[str],
    element_attr_values: dict[str, dict[str, str]] | None = None,
    child_weights: dict[str, float] | None = None,
) -> str:
    """Pick the "broadest aggregate" element to use as default_element.

    Pure naming/structural heuristics — currency-view / source-of-truth
    style picking happens at QUERY time in the planner, where the user's
    intent (local vs parent currency, specific element mention, etc.) is
    known and can override this static default.

      1. Exact match "All <DimBaseName>" / plural variant.
      2. Name contains the dim base as a word.
      3. Among "All ..."/"Total ..." parents, shortest = most generic.
      4. First available consolidation, then any element.

    Unused params (element_attr_values, child_weights) kept on the signature
    so callers don't break, but their information is consumed downstream.
    """
    _ = element_attr_values  # noqa: F841 - intentionally unused here
    _ = child_weights        # noqa: F841 - consumed by planner instead

    candidates = top_consolidations or consolidations
    if not candidates:
        return (elements[:1] or [""])[0]

    base = _dim_base_name(dim_name).lower()
    base_variants = _base_word_variants(base) if base else set()

    # Tier 1: exact "All <base-variant>"
    if base_variants:
        for variant in sorted(base_variants, key=len, reverse=True):
            target = f"all {variant}"
            for name in candidates:
                if name.lower() == target:
                    return name

    # Tier 2: name contains the dim base (any variant) as a word
    if base_variants:
        for name in candidates:
            lower = name.lower()
            if any(re.search(rf"\b{re.escape(v)}\b", lower) for v in base_variants):
                return name

    # Tier 3: among "All ..."/"Total ..." parents, pick the shortest
    all_total = [n for n in candidates if re.match(r"(?i)^(all|total)\s", n)]
    if all_total:
        all_total.sort(key=len)
        return all_total[0]

    return candidates[0]


def _dim_base_name(dim_name: str) -> str:
    """'Segment 1' -> 'segment', 'Account Report' -> 'account report'.
    Strips trailing numeric suffix used to disambiguate multi-instance dims."""
    cleaned = re.sub(r"\s+\d+$", "", str(dim_name).strip())
    return cleaned.lower()


# Tokens that, taken together, identify a P&L bottom-line consolidation.
# Each tuple is a set of words that must ALL appear (in any order, anywhere)
# in the candidate name. "Profit / (Loss) after Tax" matches because its
# words are {profit, loss, after, tax} which is a superset of (profit, after,
# tax). Matching is punctuation-insensitive.
_PNL_BOTTOMLINE_TOKEN_SETS: list[set[str]] = [
    {"net", "income"},
    {"net", "profit"},
    {"net", "earnings"},
    {"profit", "after", "tax"},
    {"loss", "after", "tax"},
    {"earnings", "after", "tax"},
    {"profit", "before", "tax"},
    {"loss", "before", "tax"},
    {"earnings", "before", "tax"},
    {"operating", "profit"},
    {"operating", "income"},
    {"operating", "result"},
    {"gross", "profit"},
    {"gross", "margin"},
    {"comprehensive", "income"},
    {"income", "statement"},
    {"profit", "and", "loss"},
    {"ebit"},
    {"ebitda"},
    {"result", "for", "year"},
    {"result", "for", "period"},
]


def _looks_like_pnl_bottom_line(name: str) -> bool:
    words = set(re.findall(r"[a-z]+", str(name).lower()))
    if not words:
        return False
    return any(tokens.issubset(words) for tokens in _PNL_BOTTOMLINE_TOKEN_SETS)


def _base_word_variants(base: str) -> set[str]:
    """Generate common English plural/singular variants of the dim base name.
    company -> {company, companies}, account -> {account, accounts},
    segment -> {segment, segments}, entity -> {entity, entities}."""
    variants: set[str] = {base}
    if base.endswith("y") and len(base) > 1:
        variants.add(base[:-1] + "ies")
    elif base.endswith("ies") and len(base) > 3:
        variants.add(base[:-3] + "y")
    if base.endswith("s") and len(base) > 1:
        variants.add(base[:-1])
    else:
        variants.add(base + "s")
    return variants


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
            consolidations = conn.execute(
                "SELECT element_name FROM elements"
                " WHERE dim_name = ? AND element_type = 'Consolidated'"
                " ORDER BY CASE WHEN LOWER(element_name) LIKE 'all%'"
                "               OR LOWER(element_name) LIKE 'total%' THEN 0 ELSE 1 END,"
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

            # Per-element attribute values (Description / Label / Alias).
            # The actual SEMANTIC of each element lives here - "EC" is just a
            # code, but its Description "Local Currency" is what tells us what
            # the data feed represents. We surface them to the picker / LLM.
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
            # Also pull simple alias from element_aliases (the principal alias
            # used for display, which may not appear in element_attribute_values
            # if the alias attribute name varies).
            alias_rows = conn.execute(
                "SELECT element_name, alias_value FROM element_aliases WHERE dim_name = ?",
                (dim_name,),
            ).fetchall()
            for ename, aval in alias_rows:
                if not ename or aval in (None, ""):
                    continue
                element_attr_values.setdefault(str(ename), {}).setdefault("Alias", str(aval))

            # Parent-edge weights so the picker can skip non-aggregating "weight 0"
            # children (TM1 convention for alternate-view siblings that are in the
            # hierarchy for display only).
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
                    # If the same child appears under multiple parents, keep
                    # the max weight (worst case: at least one parent uses it).
                    child_weights[str(child)] = max(child_weights.get(str(child), 0.0), w)

            # FULL top consolidations preserved here. Per-query filtering
            # (drop unrelated category rollups, keep only P&L bottom lines for
            # P&L questions, etc.) is done by query_intent.prepare_schema_for_query
            # at request time, so each request sees a focused view tailored to
            # its intent rather than a one-size-fits-all truncation.
            # P&L-flavoured names still come first as a sane storage order.
            all_top_names = [r[0] for r in all_top_rows]
            pnl_first = [n for n in all_top_names if _looks_like_pnl_bottom_line(n)]
            non_pnl = [n for n in all_top_names if not _looks_like_pnl_bottom_line(n)]
            top_names = pnl_first + non_pnl
            consolidation_names = [r[0] for r in consolidations]
            element_names = [r[0] for r in elems]
            default = _pick_default_element(
                dim_name,
                all_top_names,
                consolidation_names,
                element_names,
                element_attr_values=element_attr_values,
                child_weights=child_weights,
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


def get_dim_hierarchy_edges(dim_names: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Return parent-child edges for dimensions from the schema cache."""
    if not dim_names:
        return {}

    placeholders = ",".join("?" * len(dim_names))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT dim_name, parent_name, child_name FROM element_edges"
            f" WHERE dim_name IN ({placeholders})",
            dim_names,
        ).fetchall()

    result: dict[str, list[tuple[str, str]]] = {d: [] for d in dim_names}
    for dim_name, parent_name, child_name in rows:
        result.setdefault(dim_name, []).append((parent_name, child_name))
    return result


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
