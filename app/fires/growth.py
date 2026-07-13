"""Response-time history enrichment: recent size change and new-fire flag.

Reads the fire database's snapshot history to annotate normalized fire
dicts. History is an enhancement: any failure degrades to an unannotated
fire report, never a failed response.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import db as firedb
from ..filters import STATUS_LEVELS

# Size-change anchor: prefer the newest snapshot at least this old. Also
# the age a fire (or a source's history) must exceed to count for NEW.
WINDOW = timedelta(hours=24)
# Youngest usable anchor for fires without a full window of history.
MIN_SPAN = timedelta(hours=1)
# A change is noise unless it reaches either threshold.
NOISE_FLOOR_HA = 10
NOISE_FLOOR_FRACTION = 0.05


def _parse_ts(value: str) -> datetime:
    """Parse a stored ISO timestamp; naive values are UTC."""
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def enrich(fires: list[Dict[str, Any]], db_path: str,
           now: Optional[datetime] = None) -> None:
    """Annotate fire dicts with 'New' and 'SizeChange' from snapshot history.

    Each fire needs 'Source' and 'FireKey' to join its history; fires
    without them are left untouched. 'DataTime' (ISO timestamp of the
    fire's current data, set on the database-fallback path) anchors the
    comparison window; realtime data uses now.

    'New': True when the fire entered the feed within WINDOW.
    'SizeChange': {'delta': int hectares, 'hours': float span since the
    anchor snapshot} for active fires whose change clears the noise floor.
    """
    if not fires:
        return
    now = now or datetime.now(timezone.utc)
    try:
        conn = firedb.connect(db_path)
    except (sqlite3.Error, OSError) as e:
        logging.error(f"Fire history unavailable, skipping enrichment: {e}")
        return
    try:
        history_floor: Dict[str, Optional[str]] = {}
        for fire in fires:
            source, key = fire.get('Source'), fire.get('FireKey')
            if not source or not key:
                continue
            try:
                if source not in history_floor:
                    history_floor[source] = firedb.oldest_fetch(conn, source)
                _flag_new(conn, fire, source, key, history_floor[source], now)
                # A NEW fire's delta is its whole size; the label plus the
                # current size carries the story.
                if not fire.get('New'):
                    _size_change(conn, fire, source, key, now)
            except sqlite3.Error as e:
                logging.error(f"Fire history read failed for {source} {key}: {e}")
    finally:
        conn.close()


def _flag_new(conn, fire, source, key, oldest_fetch, now):
    """Flag a fire first seen within WINDOW, unless the source's own
    history is younger than WINDOW (nothing to be new against)."""
    floor = (now - WINDOW).isoformat()
    if oldest_fetch is None or oldest_fetch > floor:
        return
    first_seen = firedb.fire_first_seen(conn, source, key)
    if first_seen is not None and first_seen > floor:
        fire['New'] = True


def _size_change(conn, fire, source, key, now):
    """Compute the fire's size change against its anchor snapshot."""
    if fire.get('StatusLevel') != STATUS_LEVELS['active']:
        return
    size = fire.get('Size')
    if size is None:
        return
    data_time = _parse_ts(fire['DataTime']) if fire.get('DataTime') else now
    anchor = firedb.anchor_snapshot(conn, source, key,
                                    (data_time - WINDOW).isoformat())
    if anchor is None or anchor[0] is None:
        return
    anchor_size, anchor_time = anchor[0], _parse_ts(anchor[1])
    if data_time - anchor_time < MIN_SPAN:
        return
    delta = float(size) - anchor_size
    if abs(delta) < NOISE_FLOOR_HA and abs(delta) < NOISE_FLOOR_FRACTION * float(size):
        return
    fire['SizeChange'] = {
        'delta': round(delta),
        'hours': (now - anchor_time) / timedelta(hours=1),
    }
