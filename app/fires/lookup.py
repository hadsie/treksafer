"""Fire lookup by displayed identifier (fire number or name).

Unlike the radius search, a lookup targets one source at a time. Check the
database first to determine the source, if found, search only that specific
source, if not found, search all realtime APIs. Fire numbers that recycle
annually use the current season's fire; a number with no current fire serves
the most recent previous season.

A looked-up fire is served enriched: perimeter bounds, recent edge movement
derived from snapshot geometry history, and the time the served data was current.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import pytz
import requests_cache
from typing import Any, Dict, Optional

import geopandas as gpd
import shapely
from requests import RequestException
from shapely.errors import GEOSException
from shapely import wkb
from shapely.geometry import Point
from shapely.ops import nearest_points

from . import db as firedb
from .find import _DB_DATA_FILE, _normalize_row, _parse_source_timestamp, _realtime_data_file
from .growth import _parse_ts
from .sources import fetch_fire
from ..config import DataFile, get_config
from ..helpers import compass_direction, local_crs

# The smallest perimeter advance worth reporting as edge movement; below
# this, successive snapshots are treated as the same geometry.
_MIN_ADVANCE_M = 500


@lru_cache(maxsize=1)
def _enrichment_session():
    """Cached HTTP session for per-fire enrichment calls."""
    Path('cache').mkdir(exist_ok=True)
    return requests_cache.CachedSession(
        cache_name='cache/enrichment',
        expire_after=900,
        allowable_methods=['GET'],
    )


def _field_path(payload, path: str):
    """Walk a dotted path of dict keys and list indices ('features.0.
    properties.status_date'); None as soon as anything is missing."""
    value = payload
    for part in path.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
        if value is None:
            return None
    return value


def _enriched_updated(enrichment, values: Dict[str, str]):
    """The fire's last-update time from a source's enrichment API, or None.

    values supplies the url template's placeholders (the source's key
    fields). Enrichment is garnish: every failure logs and returns None.
    """
    try:
        url = enrichment.url.format(**{k: quote(str(v)) for k, v in values.items()})
    except KeyError as e:
        logging.error(f"Enrichment url placeholder {e} not among key fields {list(values)}")
        return None
    try:
        resp = _enrichment_session().get(url, timeout=10)
        resp.raise_for_status()
        return _parse_source_timestamp(_field_path(resp.json(), enrichment.updated_field), None)
    except (RequestException, ValueError, KeyError) as e:
        logging.warning(f"Enrichment lookup {url} failed: {e}")
        return None


def _to_local(geometry_4326, crs):
    """Project an EPSG:4326 geometry into a local true-meter CRS."""
    return gpd.GeoSeries([geometry_4326], crs='EPSG:4326').to_crs(crs).iloc[0]


def _is_synthetic_circle(geometry_m) -> bool:
    """Whether a geometry is a generated size circle rather than a mapped
    perimeter (see sources._size_circle: fires with no mapped perimeter get
    one). A generated circle fills ~99% of its minimum bounding circle;
    real fire perimeters are irregular and fall far below. Zero-area
    geometry (a bare report point) is no mapped perimeter either.
    """
    bounding = shapely.minimum_bounding_circle(geometry_m)
    if bounding.area == 0:
        return True
    return geometry_m.area / bounding.area > 0.95


def _edge_advance(current_m, prior_m) -> tuple[float, str]:
    """How far, and in which compass direction, the perimeter advanced."""
    points = shapely.points(shapely.get_coordinates(current_m))
    distances = shapely.distance(points, prior_m)
    farthest = distances.argmax()
    # Agency perimeters are often topologically invalid, and difference()
    # rejects invalid input; make_valid repairs most of it. When it still
    # fails, the farthest boundary point stands in for the growth centroid.
    try:
        growth = shapely.make_valid(current_m).difference(shapely.make_valid(prior_m))
        toward = growth.centroid if not growth.is_empty else points[farthest]
    except GEOSException:
        toward = points[farthest]
    return distances[farthest], compass_direction(prior_m.centroid, toward)


class FireLookup:
    """Resolve a single fire from its displayed fire number or name."""

    def __init__(self, term: str, coords: Optional[tuple[float, float]] = None):
        self.term = term
        self.coords = coords
        self.settings = get_config()
        # Last known update time, localized to the user (or fire).
        self.as_of: Optional[datetime] = None
        # {'bounds': (minlat, maxlat, minlon, maxlon)}
        self.perimeter: Optional[Dict[str, Any]] = None
        # {'advance_m': float, 'direction': str, 'since': datetime,
        #  'was_m': float | None} -- edge movement vs the last distinct
        # snapshot geometry; was_m is the requester's prior edge distance.
        self.edge: Optional[Dict[str, Any]] = None

    def result(self) -> Optional[Dict[str, Any]]:
        """Return the single matching fire dict, or None if nothing matched.

        Single result by design: the first source (in config order) that holds
        the fire wins. Same-name collisions across sources are deferred to
        HAD-209.
        """
        now = datetime.now(timezone.utc)
        for data_file in self.settings.data:
            fire = self._from_database(data_file, now)
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
                    live, _realtime_data_file(data_file.location, realtime),
                    data_file, now)
        return None

    def _from_database(self, data_file: DataFile,
                       now: datetime) -> Optional[Dict[str, Any]]:
        """Serve a database match, re-querying live when it is stale.

        Staleness is per fire (its last_seen, not the source's newest
        fetch), so a match outside the latest fetch's coverage -- or from
        a prior season -- refreshes or is served honestly aged."""
        stored = self._load_stored(data_file.location)
        if stored is None or stored.empty:
            return None

        seen_at = _parse_ts(stored.iloc[0]['LastSeen'])
        realtime = data_file.realtime
        needs_refresh = (realtime and realtime.enabled
                         and now - seen_at >= timedelta(seconds=realtime.cache_timeout))
        if needs_refresh:
            live = self._live(data_file.location, realtime)
            if live is not None and not live.empty:
                return self._normalize(
                    live, _realtime_data_file(data_file.location, realtime),
                    data_file, now)
            # Live failed, or the fire dropped from the agency feed: serve
            # the stored record, honestly timestamped.
        return self._normalize(stored, _DB_DATA_FILE, data_file, seen_at)

    def _load_stored(self, location: str) -> Optional[gpd.GeoDataFrame]:
        """The database's match for the term (newest last_seen), or None."""
        try:
            conn = firedb.connect(self.settings.database)
            try:
                return firedb.load_fire(conn, location, self.term)
            finally:
                conn.close()
        except sqlite3.Error as e:
            logging.error(f"Fire database read failed for {location}: {e}")
            return None

    def _live(self, location: str, realtime) -> Optional[gpd.GeoDataFrame]:
        """One targeted live query of a source, or None on failure."""
        try:
            return fetch_fire(realtime, self.term)
        except (RequestException, ValueError) as e:
            logging.warning(f"{location} fire lookup for {self.term!r} failed: {e}")
            return None

    def _normalize(self, frame: gpd.GeoDataFrame, effective: DataFile,
                   data_file: DataFile, data_time: datetime) -> Dict[str, Any]:
        """Normalize the matched fire, attaching distance/direction when the
        requester sent coordinates, and derive the perimeter enrichment."""
        row = frame.iloc[0]
        current = frame.to_crs(epsg=4326).geometry.iloc[0]
        # If the source joins perimeters on the fire-number field, enrich the
        # result with perimeter and edge data. Only applies to realtime sources.
        realtime = data_file.realtime
        if realtime and realtime.join == 'field':
            self._enrich(current, data_file.location)

        self.as_of = self._agency_updated(row, data_file) or data_time

        if self.coords is None:
            return _normalize_row(effective, row)

        origin = Point(0, 0)
        geometry = frame.to_crs(local_crs(self.coords)).geometry.iloc[0]
        closest = nearest_points(origin, geometry)[1]
        return _normalize_row(effective, row, distance=geometry.distance(origin),
                              direction=compass_direction(origin, closest))

    def _agency_updated(self, row, data_file: DataFile) -> Optional[datetime]:
        """The agency's own per-fire update time, or None.

        Checked in order: a fresh enrichment fetch; the row's SourceUpdated
        column (rows from db.load_fire; possibly back-filled by an earlier
        lookup); the row's updated_field column (rows from fetch_fire).
        """
        realtime = data_file.realtime
        # A fresh fetch outranks SourceUpdated, which may lag it, and is
        # written through to the fire's newest snapshot so it can serve
        # below on later lookups when enrichment is unavailable.
        # realtime.enabled gates every live call, including this one.
        if realtime and realtime.enabled and realtime.enrichment:
            fresh = _enriched_updated(
                realtime.enrichment, self._key_values(row, realtime.key_fields))
            if fresh:
                self._backfill(data_file.location, fresh)
                return fresh
        stored = row.get('SourceUpdated')
        if stored:
            return _parse_ts(stored)
        if realtime and realtime.updated_field:
            tz = pytz.timezone(realtime.timezone) if realtime.timezone else None
            return _parse_source_timestamp(row.get(realtime.updated_field), tz)
        return None

    def _backfill(self, location: str, updated: datetime) -> None:
        """Write an enrichment-fetched update time onto the fire's newest
        snapshot (a no-op for fires not in the database). Failures never
        break the lookup."""
        try:
            conn = firedb.connect(self.settings.database)
            try:
                firedb.backfill_source_updated(
                    conn, location, self.term, updated.isoformat())
            finally:
                conn.close()
        except sqlite3.Error as e:
            logging.warning(f"Could not backfill update time for {self.term!r}: {e}")

    @staticmethod
    def _key_values(row, key_fields) -> Dict[str, str]:
        """The fire's key-field values, for enrichment url placeholders.

        Live frames carry the raw columns; stored frames reconstruct them
        from fire_key, which normalize_for_db builds by joining the key
        fields with '-'.
        """
        values = {}
        fire_key = row.get('fire_key')
        if fire_key is not None:
            parts = str(fire_key).split('-', len(key_fields) - 1)
            if len(parts) == len(key_fields):
                values = dict(zip(key_fields, parts))
        for field in key_fields:
            value = row.get(field)
            if value is not None:
                values[field] = value
        return values

    def _enrich(self, current_4326, location: str) -> None:
        """Derive perimeter bounds and edge movement from the snapshot
        geometry history. Failures degrade to an unenriched reply, never a
        failed one.

        The lookup term doubles as the fire's identifier for the history
        query: it matched the identifier exactly (case-insensitively) on
        whichever path produced the frame.
        """
        minlon, minlat, maxlon, maxlat = current_4326.bounds
        centroid = current_4326.centroid
        fire_crs = local_crs((centroid.y, centroid.x))
        current_m = _to_local(current_4326, fire_crs)
        # A made-up perimeter (the size circle a fire gets when the agency
        # has not mapped one) is not worth reporting as geometry at all.
        if _is_synthetic_circle(current_m):
            return
        self.perimeter = {'bounds': (minlat, maxlat, minlon, maxlon)}

        try:
            conn = firedb.connect(self.settings.database)
            try:
                snapshots = firedb.fire_snapshots(conn, location, self.term)
            finally:
                conn.close()
        except sqlite3.Error as e:
            logging.error(f"Fire snapshot history read failed for {self.term!r}: {e}")
            return

        # The newest snapshot whose geometry meaningfully differs from what
        # is being served anchors the movement report. On the stored path the
        # first snapshot is the served one and skips itself naturally.
        for timestamp, geometry_wkb in snapshots:
            prior_4326 = wkb.loads(geometry_wkb)
            prior_m = _to_local(prior_4326, fire_crs)
            # Movement is only reported between two agency-mapped perimeters:
            # a prior that is a bare report point or generated circle would
            # make "the perimeter got mapped" read as fire movement.
            if _is_synthetic_circle(prior_m):
                continue
            try:
                advance, direction = _edge_advance(current_m, prior_m)
            except GEOSException as e:
                logging.warning(
                    f"Edge movement for {self.term!r} skipped a snapshot "
                    f"with unusable geometry: {e}")
                continue
            if advance < _MIN_ADVANCE_M:
                continue
            self.edge = {
                'advance_m': advance,
                'direction': direction,
                'since': _parse_ts(timestamp),
                'was_m': self._requester_distance(prior_4326),
            }
            return

    def _requester_distance(self, geometry_4326) -> Optional[float]:
        """Meters from the requester to a geometry, or None without coords."""
        if self.coords is None:
            return None
        return _to_local(geometry_4326, local_crs(self.coords)).distance(Point(0, 0))
