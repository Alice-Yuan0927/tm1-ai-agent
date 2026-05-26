"""SQLite read/write helpers for cube_summaries.

Uses the same schema_cache.db as the rest of the cache layer.
The cube_summaries table is created by tm1/cache/db.py init_schema_db().
"""

import json
import logging
from typing import Any

from ..tm1.cache.db import cache_scope_matches, connect

_log = logging.getLogger(__name__)


def save_cube_summary(cube_name: str, summary: dict) -> None:
    """Upsert a cube summary. All list/dict fields are stored as JSON strings."""
    conn = connect()
    if not cache_scope_matches(conn):
        return
    conn.execute(
        """
        INSERT INTO cube_summaries
            (cube_name, business_purpose, grain, dimensions, measures,
             best_for, avoid_for, default_filters, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(cube_name) DO UPDATE SET
            business_purpose = excluded.business_purpose,
            grain            = excluded.grain,
            dimensions       = excluded.dimensions,
            measures         = excluded.measures,
            best_for         = excluded.best_for,
            avoid_for        = excluded.avoid_for,
            default_filters  = excluded.default_filters,
            generated_at     = excluded.generated_at
        """,
        (
            cube_name,
            summary.get("business_purpose", ""),
            json.dumps(summary.get("grain", []), ensure_ascii=False),
            json.dumps(summary.get("dimensions", []), ensure_ascii=False),
            json.dumps(summary.get("measures", []), ensure_ascii=False),
            json.dumps(summary.get("best_for", []), ensure_ascii=False),
            json.dumps(summary.get("avoid_for", []), ensure_ascii=False),
            json.dumps(summary.get("default_filters", {}), ensure_ascii=False),
        ),
    )
    conn.commit()


def load_cube_summary(cube_name: str) -> dict | None:
    """Return the stored summary for one cube, or None if not found."""
    conn = connect()
    if not cache_scope_matches(conn):
        return None
    row = conn.execute(
        "SELECT * FROM cube_summaries WHERE cube_name = ?", (cube_name,)
    ).fetchone()
    if row is None:
        return None
    return _deserialise(row)


def load_all_summaries() -> dict[str, dict]:
    """Return {cube_name: summary} for every stored cube."""
    conn = connect()
    if not cache_scope_matches(conn):
        return {}
    rows = conn.execute("SELECT * FROM cube_summaries").fetchall()
    return {row["cube_name"]: _deserialise(row) for row in rows}


def delete_cube_summary(cube_name: str) -> None:
    conn = connect()
    if not cache_scope_matches(conn):
        return
    conn.execute("DELETE FROM cube_summaries WHERE cube_name = ?", (cube_name,))
    conn.commit()


def _deserialise(row) -> dict:
    def _j(val: Any, default):
        if not val:
            return default
        try:
            return json.loads(val)
        except Exception:
            return default

    return {
        "cube": row["cube_name"],
        "business_purpose": row["business_purpose"] or "",
        "grain": _j(row["grain"], []),
        "dimensions": _j(row["dimensions"], []),
        "measures": _j(row["measures"], []),
        "best_for": _j(row["best_for"], []),
        "avoid_for": _j(row["avoid_for"], []),
        "default_filters": _j(row["default_filters"], {}),
        "generated_at": row["generated_at"],
    }
