"""Persistent dismissal memory backed by SQLite.

When an admin marks a flag as 'dismiss' (e.g. expected reorg, planned
ramp-up), we suppress that ``dedupe_id`` on subsequent runs. Without
persistence the same noise re-fires every week.

Schema lives in its own DB file so we don't bloat the existing
``schema_cache.db`` and so deletion (during dev) is easy.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from .flag import Flag

_log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "anomaly.db"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(_DB_PATH))


def init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS dismissals (
                cube TEXT NOT NULL,
                dedupe_id TEXT NOT NULL,
                dismissed_at REAL NOT NULL,
                note TEXT DEFAULT '',
                PRIMARY KEY (cube, dedupe_id)
            )
            """
        )


def dismiss(cube: str, flag: Flag, note: str = "") -> None:
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO dismissals(cube, dedupe_id, dismissed_at, note) VALUES (?,?,?,?)",
            (cube, flag.dedupe_id, time.time(), note),
        )


def list_dismissed_ids(cube: str) -> set[str]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT dedupe_id FROM dismissals WHERE cube=?", (cube,)
        ).fetchall()
    return {r[0] for r in rows}


def clear(cube: str, dedupe_ids: Iterable[str] | None = None) -> int:
    init_db()
    with _conn() as c:
        if dedupe_ids is None:
            cur = c.execute("DELETE FROM dismissals WHERE cube=?", (cube,))
        else:
            ids = list(dedupe_ids)
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            cur = c.execute(
                f"DELETE FROM dismissals WHERE cube=? AND dedupe_id IN ({placeholders})",
                (cube, *ids),
            )
        return cur.rowcount or 0
