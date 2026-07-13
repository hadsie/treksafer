"""Fire lookup by displayed identifier (fire number or name).

Unlike the radius search, a lookup targets one source at a time. The fire
database aggregates every source's recent fires, so it answers existence
cheaply; only a database match whose fetch is older than the source's cache
window triggers a single live re-query of that one source. All four sources
are searched in config order -- the requester may be asking about a fire near
someone else, so their own location never narrows the search.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import geopandas as gpd
from requests import RequestException
from shapely.geometry import Point
from shapely.ops import nearest_points

from . import db as firedb
from .find import _DB_DATA_FILE, _normalize_row, _realtime_data_file, is_stale
from .sources import fetch_fire
from ..config import DataFile, get_config
from ..helpers import compass_direction, local_crs


class FireLookup:
    """Resolve a single fire from its displayed fire number or name."""

    def __init__(self, term: str, coords: Optional[tuple[float, float]] = None):
        self.term = term
        self.coords = coords
        self.settings = get_config()
        # Fetch time to show in a "Data from" marker, or None for no marker.
        self.marker_fetched: Optional[datetime] = None
        # (lat, lon) whose timezone localizes the marker: the requester's when
        # present, otherwise the matched fire's own location.
        self.marker_coords: Optional[tuple[float, float]] = None

    def result(self) -> Optional[Dict[str, Any]]:
        """Return the single matching fire dict, or None if nothing matched.

        Single result by design: the first source (in config order) that holds
        the fire wins. Same-name collisions across sources are deferred to
        HAD-209.
        """
        for data_file in self.settings.data:
            fire = self._from_database(data_file)
            if fire is not None:
                return fire
        # No stored match anywhere: query realtime sources live, first wins.
        # These fetches are a filtered slice, so they are never recorded.
        for data_file in self.settings.data:
            realtime = data_file.realtime
            if not (realtime and realtime.enabled):
                continue
            live = self._live(data_file.location, realtime)
            if live is not None and not live.empty:
                return self._normalize(
                    live, _realtime_data_file(data_file.location, realtime))
        return None

    def _from_database(self, data_file: DataFile) -> Optional[Dict[str, Any]]:
        """Serve a database match, re-querying live when it is stale."""
        stored, fetched = self._load_stored(data_file.location)
        if stored is None or stored.empty:
            return None

        fetched_at = datetime.fromisoformat(fetched)
        now = datetime.now(timezone.utc)
        realtime = data_file.realtime
        needs_refresh = (realtime and realtime.enabled
                         and now - fetched_at >= timedelta(seconds=realtime.cache_timeout))
        if not needs_refresh:
            # Fresh, or realtime disabled (stored is the normal mode): no marker.
            return self._normalize(stored, _DB_DATA_FILE)

        live = self._live(data_file.location, realtime)
        if live is None:
            # Live failed: serve stored, marked only if it is genuinely stale.
            marker = fetched_at if is_stale(fetched_at, self.settings, now) else None
            return self._normalize(stored, _DB_DATA_FILE, marker)
        if live.empty:
            # Dropped from the agency feed: serve stored, always marked -- we
            # know it is not current.
            return self._normalize(stored, _DB_DATA_FILE, fetched_at)
        return self._normalize(live, _realtime_data_file(data_file.location, realtime))

    def _load_stored(self, location: str) -> tuple[Optional[gpd.GeoDataFrame], Optional[str]]:
        """Return (matching fire frame, newest fetch time) from the database."""
        try:
            conn = firedb.connect(self.settings.database)
            try:
                return firedb.load_fire(conn, location, self.term), firedb.latest_fetch(conn, location)
            finally:
                conn.close()
        except sqlite3.Error as e:
            logging.error(f"Fire database read failed for {location}: {e}")
            return None, None

    def _live(self, location: str, realtime) -> Optional[gpd.GeoDataFrame]:
        """One targeted live query of a source, or None on failure."""
        try:
            return fetch_fire(realtime, self.term)
        except (RequestException, ValueError) as e:
            logging.warning(f"{location} fire lookup for {self.term!r} failed: {e}")
            return None

    def _normalize(self, frame: gpd.GeoDataFrame, data_file: DataFile,
                   marker: Optional[datetime] = None) -> Dict[str, Any]:
        """Normalize the matched fire, attaching distance/direction when the
        requester sent coordinates and recording the marker to serve."""
        self.marker_fetched = marker
        row = frame.iloc[0]
        if self.coords is None:
            fire_point = frame.to_crs(epsg=4326).geometry.iloc[0].centroid
            self.marker_coords = (fire_point.y, fire_point.x)
            return _normalize_row(data_file, row)

        self.marker_coords = self.coords

        origin = Point(0, 0)
        geometry = frame.to_crs(local_crs(self.coords)).geometry.iloc[0]
        closest = nearest_points(origin, geometry)[1]
        return _normalize_row(data_file, row, distance=geometry.distance(origin),
                              direction=compass_direction(origin, closest))
