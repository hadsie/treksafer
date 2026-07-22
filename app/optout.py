"""Persistent SMS compliance state: opt-outs and first-contact records.

A number that texted STOP receives nothing until it texts START, and a
number's first message triggers a one-time opt-in confirmation. Both live
in their own database, separate from the fire database, so resetting fire
data can never erase compliance state. All functions raise sqlite3.Error
(or OSError creating the directory) to the caller: whether a failed check
blocks or allows a send is the transport's decision.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS optouts (
    number       TEXT PRIMARY KEY,
    opted_out_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    number           TEXT PRIMARY KEY,
    first_contact_at TEXT NOT NULL
);
"""


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def opt_out(path: str, number: str) -> None:
    """Record that a number must receive no further messages."""
    conn = _connect(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO optouts (number, opted_out_at) VALUES (?, ?) "
                "ON CONFLICT (number) DO NOTHING",
                (number, datetime.now(timezone.utc).isoformat()),
            )
    finally:
        conn.close()


def opt_in(path: str, number: str) -> None:
    """Clear a number's opt-out."""
    conn = _connect(path)
    try:
        with conn:
            conn.execute("DELETE FROM optouts WHERE number = ?", (number,))
    finally:
        conn.close()


def first_contact(path: str, number: str) -> bool:
    """Record a sender and report whether this was their first message.

    The insert and the check are one statement, so concurrent messages
    from the same number yield True exactly once.
    """
    conn = _connect(path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO contacts (number, first_contact_at) VALUES (?, ?) "
                "ON CONFLICT (number) DO NOTHING",
                (number, datetime.now(timezone.utc).isoformat()),
            )
        return cur.rowcount == 1
    finally:
        conn.close()


def is_opted_out(path: str, number: str) -> bool:
    """Whether a number has opted out."""
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT 1 FROM optouts WHERE number = ?", (number,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()
