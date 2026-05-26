import sqlite3

from backend.tm1.cache import db


def test_cache_scope_matches_current_tm1_connection(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_tm1_config",
        lambda: {
            "address": "host.docker.internal",
            "port": 30072,
            "namespace": "",
            "user": "admin",
            "ssl": False,
        },
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)"
    )

    assert db.cache_scope_matches(conn) is False

    db.mark_cache_scope(conn)

    assert db.cache_scope_matches(conn) is True

    conn.execute(
        "UPDATE cache_meta SET value = ? WHERE key = 'scope_id'",
        ("other-host|30072||admin|plain",),
    )

    assert db.cache_scope_matches(conn) is False
