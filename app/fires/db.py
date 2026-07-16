"""Fire database: normalized fire records with snapshot history.

Only contains storage handling logic. Rows arrive already normalized (see fires.normalize_for_db).
Each fire has one identity row and a history of snapshots. Snapshots are written when the source
reports a change (its own update timestamp advancing where the source publishes one, a field/geometry
difference otherwise).

Snapshot geometry is the geometry the search path ran against, not necessarily an agency-mapped
perimeter. Fires with no mapped perimeter store their bare report point (field-joined sources).
Anything that would report geometry to a user must distinguish these from real perimeters (see
lookup._is_synthetic_circle).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely import wkb

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE fires (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    fire_key    TEXT NOT NULL,
    fire        TEXT NOT NULL,
    name        TEXT,
    location    TEXT,
    type        TEXT,
    discovered  TEXT,
    latitude    REAL,
    longitude   REAL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    UNIQUE (source, fire_key)
);

CREATE TABLE snapshots (
    id             INTEGER PRIMARY KEY,
    fire_id        INTEGER NOT NULL REFERENCES fires(id),
    fetched_at     TEXT NOT NULL,
    source_updated TEXT,
    size_ha        REAL,
    status         TEXT NOT NULL,
    status_level   INTEGER NOT NULL,
    geometry       BLOB NOT NULL,
    UNIQUE (fire_id, fetched_at)
);
CREATE INDEX idx_snapshots_fire ON snapshots (fire_id, id);

CREATE TABLE fetches (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    fire_count INTEGER NOT NULL
);
CREATE INDEX idx_fetches_source ON fetches (source, fetched_at);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open (creating and migrating if needed) the fire database."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        with conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _latest_snapshot(conn, fire_id: int):
    return conn.execute(
        "SELECT source_updated, size_ha, status, geometry FROM snapshots "
        "WHERE fire_id = ? ORDER BY id DESC LIMIT 1", (fire_id,)
    ).fetchone()


def _should_snapshot(previous, source_updated, size_ha, status, geometry_wkb) -> bool:
    """Apply the insert criterion against the fire's latest snapshot.

    Sources with a per-fire update timestamp gate on it advancing; sources
    without one gate on a field or geometry difference.
    """
    if previous is None:
        return True
    prev_updated, prev_size, prev_status, prev_geometry = previous
    if source_updated is not None:
        return prev_updated is None or source_updated > prev_updated
    return (size_ha, status, geometry_wkb) != (prev_size, prev_status, prev_geometry)


def record_fires(conn: sqlite3.Connection, source: str, fires: gpd.GeoDataFrame,
                 fetched_at: datetime) -> int:
    """Record a fetch's fires, snapshotting the ones that changed.

    Args:
        conn: Open database connection
        source: Source code (BC, AB, US, etc.)
        fires: Normalized fires in EPSG:4326 (see fires.normalize_for_db)
        fetched_at: When the fetch happened (UTC)

    Returns:
        Number of snapshots written.
    """
    fetched = fetched_at.isoformat()
    written = 0
    # BEGIN IMMEDIATE so the criterion check and insert are atomic against
    # concurrent cache-miss writers.
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in fires.itertuples(index=False):
            identity = {
                'fire': getattr(row, 'Fire'),
                'name': getattr(row, 'Name', None),
                'location': getattr(row, 'Location', None),
                'type': getattr(row, 'Type', None),
                'discovered': _iso(getattr(row, 'Discovered', None)),
                'latitude': getattr(row, 'latitude', None),
                'longitude': getattr(row, 'longitude', None),
            }
            fire_id = conn.execute(
                """
                INSERT INTO fires (source, fire_key, fire, name, location, type,
                                   discovered, latitude, longitude, first_seen, last_seen)
                VALUES (:source, :fire_key, :fire, :name, :location, :type,
                        :discovered, :latitude, :longitude, :fetched, :fetched)
                ON CONFLICT (source, fire_key) DO UPDATE SET
                    fire = :fire, name = :name, location = :location,
                    type = :type, discovered = :discovered,
                    latitude = :latitude, longitude = :longitude,
                    last_seen = :fetched
                RETURNING id
                """,
                {**identity, 'source': source, 'fire_key': row.fire_key, 'fetched': fetched},
            ).fetchone()[0]

            source_updated = _iso(getattr(row, 'Updated', None))
            size_ha = getattr(row, 'Size', None)
            status = getattr(row, 'Status')
            geometry_wkb = wkb.dumps(row.geometry)
            if _should_snapshot(_latest_snapshot(conn, fire_id), source_updated,
                                size_ha, status, geometry_wkb):
                conn.execute(
                    "INSERT INTO snapshots (fire_id, fetched_at, source_updated, "
                    "size_ha, status, status_level, geometry) VALUES (?,?,?,?,?,?,?)",
                    (fire_id, fetched, source_updated, size_ha, status,
                     getattr(row, 'StatusLevel'), geometry_wkb),
                )
                written += 1
        conn.execute(
            "INSERT INTO fetches (source, fetched_at, fire_count) VALUES (?,?,?)",
            (source, fetched, len(fires)),
        )
    return written


def first_seen_map(conn: sqlite3.Connection, source: str) -> dict:
    """fire_key -> first_seen for every fire recorded for a source."""
    return dict(conn.execute(
        "SELECT fire_key, first_seen FROM fires WHERE source = ?", (source,)
    ).fetchall())


def fire_first_seen(conn: sqlite3.Connection, source: str,
                    fire_key: str) -> Optional[str]:
    """Return the first fetch timestamp that included this fire, or None."""
    row = conn.execute(
        "SELECT first_seen FROM fires WHERE source = ? AND fire_key = ?",
        (source, fire_key),
    ).fetchone()
    return row[0] if row else None


def anchor_snapshot(conn: sqlite3.Connection, source: str, fire_key: str,
                    cutoff: str) -> Optional[tuple]:
    """Historical (size_ha, timestamp) for a size comparison: the newest
    snapshot at or before cutoff, else the fire's oldest snapshot.

    Snapshot time is the source's own update timestamp when published,
    otherwise the fetch time. Returns None when the fire has no snapshots.
    """
    base = (
        "SELECT s.size_ha, COALESCE(s.source_updated, s.fetched_at) "
        "FROM snapshots s JOIN fires f ON f.id = s.fire_id "
        "WHERE f.source = ? AND f.fire_key = ?"
    )
    row = conn.execute(
        base + " AND COALESCE(s.source_updated, s.fetched_at) <= ? "
        "ORDER BY s.id DESC LIMIT 1",
        (source, fire_key, cutoff),
    ).fetchone()
    if row is None:
        row = conn.execute(
            base + " ORDER BY s.id ASC LIMIT 1", (source, fire_key)
        ).fetchone()
    return row


def oldest_fetch(conn: sqlite3.Connection, source: str) -> Optional[str]:
    """Return the oldest fetch timestamp for a source, or None."""
    row = conn.execute(
        "SELECT MIN(fetched_at) FROM fetches WHERE source = ?", (source,)
    ).fetchone()
    return row[0]


def latest_fetches(conn: sqlite3.Connection) -> dict:
    """The newest fetch timestamp for every source that has one."""
    rows = conn.execute(
        "SELECT source, MAX(fetched_at) FROM fetches GROUP BY source"
    ).fetchall()
    return dict(rows)


def latest_fetch(conn: sqlite3.Connection, source: str) -> Optional[str]:
    """Return the newest fetch timestamp for a source, or None."""
    row = conn.execute(
        "SELECT MAX(fetched_at) FROM fetches WHERE source = ?", (source,)
    ).fetchone()
    return row[0]


# A source's fires, each with its latest snapshot. Callers append their own
# predicates and pass the matching parameters after (source,).
_FIRES_SQL = """
    SELECT f.fire, f.name, f.location, f.type, f.discovered,
           s.size_ha, s.status, s.status_level,
           COALESCE(s.source_updated, s.fetched_at), s.geometry, f.fire_key,
           s.source_updated, f.first_seen, f.last_seen
    FROM fires f
    JOIN snapshots s ON s.id = (
        SELECT id FROM snapshots WHERE fire_id = f.id ORDER BY id DESC LIMIT 1
    )
    WHERE f.source = ?
"""


def _fires_frame(rows) -> gpd.GeoDataFrame:
    """Build the normalized EPSG:3857 frame from _FIRES_SQL rows."""
    frame = gpd.GeoDataFrame(
        {
            'Fire': [r[0] for r in rows],
            'Name': [r[1] for r in rows],
            'Location': [r[2] for r in rows],
            'Type': [r[3] for r in rows],
            'Discovered': [r[4] for r in rows],
            'Size': [r[5] for r in rows],
            'Status': [r[6] for r in rows],
            'StatusLevel': [r[7] for r in rows],
            'Updated': [r[8] for r in rows],
            'fire_key': [r[10] for r in rows],
            # The source's own per-fire update time, un-coalesced: None for
            # sources that publish none (BC, CA).
            'SourceUpdated': [r[11] for r in rows],
            'FirstSeen': [r[12] for r in rows],
            'LastSeen': [r[13] for r in rows],
        },
        geometry=gpd.GeoSeries([wkb.loads(r[9]) for r in rows], crs='EPSG:4326'),
    )
    return frame.to_crs(epsg=3857)


def load_source(conn: sqlite3.Connection, source: str) -> Optional[gpd.GeoDataFrame]:
    """Load the current fires for a source: the latest snapshot of every fire
    still present in the source's newest fetch.

    Returns a GeoDataFrame in EPSG:3857 matching the realtime normalized
    shape, or None when the source has no data at all.
    """
    newest = latest_fetch(conn, source)
    if newest is None:
        return None
    rows = conn.execute(
        _FIRES_SQL + " AND f.last_seen = ?", (source, newest)
    ).fetchall()
    return _fires_frame(rows)


def load_fire(conn: sqlite3.Connection, source: str,
              fire: str) -> gpd.GeoDataFrame:
    """Load the most recently seen fire matching its displayed identifier.

    Matches case-insensitively and exactly against the displayed fire field.
    Fires no longer in the source's feed still match, newest last_seen
    first, so a fire number that recycles annually resolves to the current
    season's fire and falls back to the most recent previous season only
    when no current fire carries the number. The frame's LastSeen column
    tells the caller how current the data is. The identifier is a bound
    parameter, so % and _ are matched literally rather than as LIKE
    wildcards.

    Returns a one-row (or empty) GeoDataFrame in EPSG:3857.
    """
    rows = conn.execute(
        _FIRES_SQL + " AND f.fire = ? COLLATE NOCASE "
        "ORDER BY f.last_seen DESC LIMIT 1",
        (source, fire),
    ).fetchall()
    return _fires_frame(rows)


def backfill_source_updated(conn: sqlite3.Connection, source: str, fire: str,
                            source_updated: str) -> None:
    """Set the newest snapshot's source_updated for one fire."""
    conn.execute(
        """
        UPDATE snapshots SET source_updated = ?
        WHERE id = (
            SELECT s.id FROM snapshots s JOIN fires f ON f.id = s.fire_id
            WHERE f.source = ? AND f.fire = ? COLLATE NOCASE
            ORDER BY s.id DESC LIMIT 1
        )
        """,
        (source_updated, source, fire),
    )
    conn.commit()


def fire_snapshots(conn: sqlite3.Connection, source: str, fire: str,
                   limit: int = 6) -> list[tuple[str, bytes]]:
    """Newest-first (timestamp, geometry WKB) snapshots for one fire, matched
    exactly and case-insensitively by its displayed identifier.

    Timestamp is the source's own update time when published, otherwise the
    fetch time. Geometry is EPSG:4326 WKB as stored.
    """
    return conn.execute(
        """
        SELECT COALESCE(s.source_updated, s.fetched_at), s.geometry
        FROM snapshots s JOIN fires f ON f.id = s.fire_id
        WHERE f.source = ? AND f.fire = ? COLLATE NOCASE
        ORDER BY s.id DESC LIMIT ?
        """,
        (source, fire, limit),
    ).fetchall()
