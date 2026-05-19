"""
Query history store for RAG-based MDX generation.

Uses SQLite FTS5 (built-in, no extra dependencies) to store and retrieve
past successful (question -> cube + MDX) pairs. When the AI generates a
new MDX it receives the top-k similar past queries as few-shot examples,
reducing dimension/element mistakes over time.

Schema
------
query_history  FTS5 virtual table  - full-text search on question + cube
query_meta     regular table       - timestamp and row_count per entry
"""

import json
import re
import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent.parent / "query_history.db"


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables if they don't exist yet. Safe to call on every startup."""
    with _connect() as conn:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS query_history USING fts5(
                question,
                cube,
                mdx,
                tokenize = 'unicode61'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_meta (
                rowid    INTEGER PRIMARY KEY,
                cube     TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                row_count     INTEGER DEFAULT 0,
                grounded_json TEXT
            )
        """)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _to_structural_template(mdx: str) -> str:
    """Strip specific element values while preserving the axis shape.

    Do not rewrite explicit sets to .Members. That teaches the MDX generator a
    broad expansion pattern the validator and prompt rules intentionally reject.
    """
    return re.sub(
        r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[[^\]]+\]",
        r"[\1].[\2].[?]",
        mdx,
    )


def save_query(
    question: str,
    cube: str,
    mdx: str,
    row_count: int = 0,
    grounded_members: list | None = None,
) -> None:
    """Persist a structural template of a successful query for future few-shot use."""
    template = _to_structural_template(mdx)
    grounded_json = json.dumps(grounded_members or [], ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO query_history(question, cube, mdx) VALUES (?, ?, ?)",
            (question, cube, template),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO query_meta(rowid, cube, row_count, grounded_json) VALUES (?, ?, ?, ?)",
            (rowid, cube, row_count, grounded_json),
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def retrieve_similar(question: str, cube: str | None = None, limit: int = 3) -> list[dict]:
    """
    Return up to `limit` past queries whose question text is similar to the
    current question, optionally filtered to the same cube.

    Results are ranked by FTS5 relevance (best match first).
    """
    terms = _fts_terms(question)
    if not terms:
        return []

    with _connect() as conn:
        if cube:
            rows = conn.execute(
                """
                SELECT qh.question, qh.cube, qh.mdx, qm.grounded_json
                FROM query_history qh
                JOIN query_meta qm ON qm.rowid = qh.rowid
                WHERE query_history MATCH ? AND qm.cube = ?
                ORDER BY rank
                LIMIT ?
                """,
                (terms, cube, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT qh.question, qh.cube, qh.mdx, qm.grounded_json
                FROM query_history qh
                JOIN query_meta qm ON qm.rowid = qh.rowid
                WHERE query_history MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (terms, limit),
            ).fetchall()

    results = []
    for q, c, m, gj in rows:
        entry: dict = {"question": q, "cube": c, "mdx": m}
        if gj:
            try:
                entry["grounded_members"] = json.loads(gj)
            except Exception:
                pass
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fts_terms(text: str) -> str:
    """
    Convert free-form question text into an FTS5 query.
    Each word becomes an optional term (OR logic) so partial matches still
    surface relevant results.
    """
    words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", text)
    if not words:
        return ""
    # Wrap each word in quotes to prevent FTS5 operator interpretation.
    return " OR ".join(f'"{w}"' for w in words)
