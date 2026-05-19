"""SQLite connection + schema DDL + migrations for the TM1 schema cache."""

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "schema_cache.db"

# Per-thread cached connection. SQLite connections are not safe to share across
# threads, but reusing one per worker thread avoids the open/close cost on every
# helper call (a single /api/analyze flow can hit the cache 30+ times).
_tls = threading.local()


def connect() -> sqlite3.Connection:
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        _tls.conn = conn
    return conn


def qmarks(n: int) -> str:
    """Return '?,?,?' for use in `IN (...)` clauses."""
    return ",".join("?" * n)


_ALLOWED_MIGRATION_TABLES = frozenset(
    {"cubes", "dim_in_cube", "elements", "element_edges",
     "dim_attributes", "element_aliases", "element_attribute_values", "member_search"}
)


def _add_col_if_missing(conn: sqlite3.Connection, table: str, col: str, defn: str) -> None:
    if table not in _ALLOWED_MIGRATION_TABLES:
        raise ValueError(f"_add_col_if_missing: unknown table '{table}'")
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")


def _backfill_member_search_if_empty(conn: sqlite3.Connection) -> None:
    member_rows = conn.execute("SELECT COUNT(*) FROM member_search").fetchone()[0]
    element_rows = conn.execute("SELECT COUNT(*) FROM elements").fetchone()[0]
    if member_rows or not element_rows:
        return
    rows = conn.execute(
        "SELECT e.dim_name, e.element_name,"
        "       e.element_name || ' ' || COALESCE(ea.alias_value, '') || ' ' ||"
        "       COALESCE(GROUP_CONCAT(eav.attr_name || ' ' || eav.attr_value, ' '), '')"
        " FROM elements e"
        " LEFT JOIN element_aliases ea"
        "   ON ea.dim_name = e.dim_name AND ea.element_name = e.element_name"
        " LEFT JOIN element_attribute_values eav"
        "   ON eav.dim_name = e.dim_name AND eav.element_name = e.element_name"
        " GROUP BY e.dim_name, e.element_name, ea.alias_value"
    ).fetchall()
    if rows:
        conn.executemany(
            "INSERT INTO member_search(dim_name, element_name, searchable_text)"
            " VALUES (?, ?, ?)",
            rows,
        )


def init_schema_db() -> None:
    """Create tables and indexes. Safe to call on every startup (idempotent)."""
    conn = connect()
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

        CREATE VIRTUAL TABLE IF NOT EXISTS member_search USING fts5(
            dim_name UNINDEXED,
            element_name UNINDEXED,
            searchable_text,
            tokenize = 'unicode61'
        );

        CREATE TABLE IF NOT EXISTS element_embeddings (
            dim_name      TEXT NOT NULL,
            element_name  TEXT NOT NULL,
            embed_text    TEXT,
            embedding     BLOB,
            model         TEXT,
            PRIMARY KEY (dim_name, element_name)
        );

        CREATE TABLE IF NOT EXISTS cube_summaries (
            cube_name        TEXT PRIMARY KEY,
            business_purpose TEXT DEFAULT '',
            grain            TEXT DEFAULT '[]',
            dimensions       TEXT DEFAULT '[]',
            measures         TEXT DEFAULT '[]',
            best_for         TEXT DEFAULT '[]',
            avoid_for        TEXT DEFAULT '[]',
            default_filters  TEXT DEFAULT '{}',
            generated_at     TEXT DEFAULT (datetime('now'))
        );
    """)
    _add_col_if_missing(conn, "elements",    "element_type", "TEXT NOT NULL DEFAULT 'Numeric'")
    _add_col_if_missing(conn, "dim_in_cube", "is_time_dim",  "INTEGER DEFAULT 0")
    _backfill_member_search_if_empty(conn)
    conn.commit()


def is_empty() -> bool:
    """True when the cache has never been populated."""
    row = connect().execute("SELECT COUNT(*) FROM cubes").fetchone()
    return row[0] == 0
