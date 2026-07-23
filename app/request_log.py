"""Persistent log of inbound requests: who asked, what they asked, and
what kind of reply they got.

Rows feed the daily digest (re-request pairs, volume and outcome counts).
The data is PII (sender identity plus location) and lives in its own
database that stays on the server. All functions raise sqlite3.Error (or
OSError creating the directory) to the caller.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id            INTEGER PRIMARY KEY,
    received_at   TEXT NOT NULL,
    sender        TEXT NOT NULL,
    message       TEXT NOT NULL,
    lat           REAL,
    lon           REAL,
    response_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_received_at ON requests (received_at);
"""

_COLUMNS = ('received_at', 'sender', 'message', 'lat', 'lon', 'response_type')


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def record(path: str, sender: str, message: str,
           coords: Optional[tuple[float, float]], response_type: str) -> None:
    """Insert one request row."""
    lat, lon = coords if coords else (None, None)
    conn = _connect(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO requests "
                "(received_at, sender, message, lat, lon, response_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), sender, message,
                 lat, lon, response_type),
            )
    finally:
        conn.close()


def requests_since(path: str, since: datetime) -> list[dict]:
    """Rows received at or after `since`, oldest first."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM requests "
            "WHERE received_at >= ? ORDER BY received_at, id",
            (since.isoformat(),),
        ).fetchall()
        return [dict(zip(_COLUMNS, row)) for row in rows]
    finally:
        conn.close()
