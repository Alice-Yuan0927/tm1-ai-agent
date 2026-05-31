"""SQLite connection + schema DDL + migrations for the TM1 schema cache."""

import re
import sqlite3
import threading

from ...config import DATA_DIR, get_tm1_config
DB_PATH = DATA_DIR / "schema_cache.db"

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
     "dim_attributes", "element_aliases", "element_attribute_values", "member_search",
     "cube_relationships", "process_cube_links"}
)

_SCHEMA_DATA_TABLES = (
    "process_cube_links",
    "cube_relationships",
    "cube_summaries",
    "element_embeddings",
    "member_search",
    "element_attribute_values",
    "element_aliases",
    "element_edges",
    "dim_attributes",
    "elements",
    "dim_in_cube",
    "cubes",
)


def cache_scope_id() -> str:
    """Stable non-secret identity for the active TM1 connection."""
    config = get_tm1_config()
    parts = [
        str(config.get("address", "")).strip().lower(),
        str(config.get("port", "")).strip(),
        str(config.get("namespace", "")).strip().lower(),
        str(config.get("user", "")).strip().lower(),
        "ssl" if config.get("ssl") else "plain",
    ]
    raw = "|".join(parts)
    return re.sub(r"[^a-z0-9_.|:-]+", "_", raw).strip("_") or "default"


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

        CREATE TABLE IF NOT EXISTS cube_relationships (
            from_cube         TEXT NOT NULL,
            to_cube           TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            source_name       TEXT DEFAULT '',
            snippet           TEXT DEFAULT '',
            synced_at         TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (from_cube, to_cube, relationship_type, source_name, snippet)
        );

        CREATE INDEX IF NOT EXISTS idx_cube_relationships_from
            ON cube_relationships (from_cube);

        CREATE INDEX IF NOT EXISTS idx_cube_relationships_to
            ON cube_relationships (to_cube);

        CREATE TABLE IF NOT EXISTS process_cube_links (
            process_name    TEXT NOT NULL,
            cube_name       TEXT NOT NULL,
            role            TEXT NOT NULL,
            datasource_type TEXT DEFAULT '',
            object_name     TEXT DEFAULT '',
            snippet         TEXT DEFAULT '',
            synced_at       TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (process_name, cube_name, role, object_name, snippet)
        );

        CREATE INDEX IF NOT EXISTS idx_process_cube_links_cube
            ON process_cube_links (cube_name);

        CREATE INDEX IF NOT EXISTS idx_process_cube_links_process
            ON process_cube_links (process_name);

        CREATE TABLE IF NOT EXISTS cache_meta (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    _add_col_if_missing(conn, "elements",    "element_type", "TEXT NOT NULL DEFAULT 'Numeric'")
    _add_col_if_missing(conn, "dim_in_cube", "is_time_dim",  "INTEGER DEFAULT 0")
    _clear_if_scope_mismatch(conn)
    _backfill_member_search_if_empty(conn)
    conn.commit()


def cache_scope_matches(conn: sqlite3.Connection | None = None) -> bool:
    """True when cached data belongs to the active TM1 connection."""
    connection = conn or connect()
    try:
        row = connection.execute(
            "SELECT value FROM cache_meta WHERE key = 'scope_id'"
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0] == cache_scope_id())


def mark_cache_scope(conn: sqlite3.Connection | None = None) -> None:
    connection = conn or connect()
    connection.execute(
        "INSERT OR REPLACE INTO cache_meta(key, value, updated_at)"
        " VALUES ('scope_id', ?, datetime('now'))",
        (cache_scope_id(),),
    )


def clear_schema_cache(conn: sqlite3.Connection | None = None) -> None:
    connection = conn or connect()
    for table in _SCHEMA_DATA_TABLES:
        connection.execute(f"DELETE FROM {table}")


def _clear_if_scope_mismatch(conn: sqlite3.Connection) -> None:
    cube_count = conn.execute("SELECT COUNT(*) FROM cubes").fetchone()[0]
    if cube_count and not cache_scope_matches(conn):
        clear_schema_cache(conn)


def is_empty() -> bool:
    """True when the cache has no data for the active TM1 connection."""
    if not cache_scope_matches():
        return True
    row = connect().execute("SELECT COUNT(*) FROM cubes").fetchone()
    return row[0] == 0
