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
cubes         name, description, measure_dim
dim_in_cube   cube_name, dim_name, position, is_measure
elements      dim_name, element_name          ← the key lookup table
"""

import sqlite3
from pathlib import Path

from TM1py import TM1Service

from ..config import TM1_CONFIG

_DB_PATH = Path(__file__).parent.parent / "schema_cache.db"


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_schema_db() -> None:
    """Create tables and indexes. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cubes (
                name        TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                measure_dim TEXT DEFAULT '',
                synced_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS dim_in_cube (
                cube_name  TEXT    NOT NULL,
                dim_name   TEXT    NOT NULL,
                position   INTEGER NOT NULL,
                is_measure INTEGER DEFAULT 0,
                PRIMARY KEY (cube_name, dim_name)
            );

            CREATE TABLE IF NOT EXISTS elements (
                dim_name     TEXT NOT NULL,
                element_name TEXT NOT NULL,
                PRIMARY KEY (dim_name, element_name)
            );

            CREATE INDEX IF NOT EXISTS idx_elem_lower
                ON elements (LOWER(element_name), dim_name);
        """)


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
    # --- fetch everything from TM1 in one connection ---
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

        # Fetch elements once per unique dimension (dims are shared across cubes)
        unique_dims = {dim for dims in cube_dims.values() for dim in dims}
        dim_elements: dict[str, list[str]] = {}
        for dim_name in unique_dims:
            try:
                dim_elements[dim_name] = list(
                    tm1.elements.get_element_names(dim_name, dim_name)
                )
            except Exception:
                dim_elements[dim_name] = []

    # --- write atomically to SQLite ---
    with _connect() as conn:
        conn.execute("DELETE FROM elements")
        conn.execute("DELETE FROM dim_in_cube")
        conn.execute("DELETE FROM cubes")

        for cube_name, dims in cube_dims.items():
            desc = str(desc_map.get(cube_name, "")).strip()
            measure = cube_measure.get(cube_name, "")
            conn.execute(
                "INSERT INTO cubes(name, description, measure_dim) VALUES (?, ?, ?)",
                (cube_name, desc, measure),
            )
            for pos, dim_name in enumerate(dims):
                conn.execute(
                    "INSERT INTO dim_in_cube(cube_name, dim_name, position, is_measure)"
                    " VALUES (?, ?, ?, ?)",
                    (cube_name, dim_name, pos, 1 if dim_name == measure else 0),
                )

        total_elems = 0
        for dim_name, elems in dim_elements.items():
            if elems:
                conn.executemany(
                    "INSERT OR IGNORE INTO elements(dim_name, element_name) VALUES (?, ?)",
                    [(dim_name, e) for e in elems],
                )
                total_elems += len(elems)

    return {"cubes": len(cube_dims), "dims": len(unique_dims), "elements": total_elems}


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
            "SELECT dim_name, is_measure FROM dim_in_cube"
            " WHERE cube_name = ? ORDER BY position",
            (cube_name,),
        ).fetchall()

        dimensions = []
        for dim_name, is_measure in dim_rows:
            elems = conn.execute(
                "SELECT element_name FROM elements WHERE dim_name = ?"
                " ORDER BY CASE WHEN LOWER(element_name) LIKE 'all%'"
                "               OR LOWER(element_name) = 'total' THEN 0 ELSE 1 END,"
                " element_name LIMIT 60",
                (dim_name,),
            ).fetchall()
            dimensions.append({
                "name": dim_name,
                "is_measure": bool(is_measure),
                "usage": "",
                "elements": [r[0] for r in elems],
            })

    return {"cube": cube_name, "dimensions": dimensions}


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
