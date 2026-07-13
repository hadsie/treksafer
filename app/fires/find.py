"""
Find and summarize nearby wildfires

Design notes
------------
 - Fire sources (realtime ArcGIS layers) are declared in config.yaml.
 - Every successful fetch is recorded to the fire database, which is also
   the fallback when a source's API is unavailable.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, Any, Optional

import geopandas as gpd
import pytz
from shapely.geometry import Point
from shapely.ops import nearest_points

from . import db as firedb
from ..config import get_config, DataFile, RealtimeFireConfig
from . import growth
from .sources import fetch_fires, fetch_fires_by_id
from ..helpers import acres_to_hectares, compass_direction, epoch_ms_to_datetime, local_crs
from ..filters import apply_filters, STATUS_LEVELS


def _iso_datetime(value):
    """Parse an ISO8601 string (as stored in the fire database) to a datetime."""
    return datetime.fromisoformat(value) if value else None


TRANSFORMS = {
    "acres_to_hectares": acres_to_hectares,
    "epoch_ms": epoch_ms_to_datetime,
    "iso_datetime": _iso_datetime,
}

def _apply_transform(data_key, raw_value, mapping):
    """
    Applies a transform to a value if a transform is defined for this data key.
    """

    transform_name = mapping.get(f"{data_key.lower()}_transform")
    if transform_name:
        transform_func = TRANSFORMS.get(transform_name)
        if transform_func:
            return transform_func(raw_value)

    return raw_value


def status_to_level(value, status_map):
    """Return the StatusLevel for a raw status value, or None if unmapped."""
    for status, codes in status_map.items():
        if value in codes:
            return STATUS_LEVELS[status]
    return None


def _status_from_percent_contained(value):
    """Derive (display, level) from WFIGS percent contained.

    WFIGS has no stage-of-control field, so containment percent is the only
    status signal: 100% is under control, anything less is still active.
    Percent is absent for many fires; an unknown containment is treated as
    active so a fire is never hidden from a user under a status filter.
    """
    try:
        pct = float(value)
    except (TypeError, ValueError):
        pct = math.nan
    if math.isnan(pct):
        return "Active", STATUS_LEVELS['active']
    if pct >= 100:
        return "Contained", STATUS_LEVELS['controlled']
    if pct <= 0:
        return "Uncontained", STATUS_LEVELS['active']
    return f"{round(pct)}% contained", STATUS_LEVELS['active']


def _status_from_wfigs(value, get_value):
    """Derive (display, level) for a WFIGS incident.

    Prescribed burns display as "Prescribed" at the controlled level;
    wildfires derive their status from percent contained. The incident type
    field is IncidentTypeCategory on the live layer and INCID_TYPE in the
    downloaded file.
    """
    category = get_value('IncidentTypeCategory') or get_value('INCID_TYPE')
    if category == 'RX':
        return "Prescribed", STATUS_LEVELS['controlled']
    return _status_from_percent_contained(value)


def _status_from_stored(value, get_value):
    """Database rows carry the already-resolved status and level."""
    return value, get_value('StatusLevel')


STATUS_TRANSFORMS = {
    "wfigs_status": _status_from_wfigs,
    "stored": _status_from_stored,
}


def _resolve_status(raw_value, data_file, get_value_fn=None):
    """Return (display_status, status_level) for a source's raw status value.

    Sources with a stage-of-control field map raw codes via status_map; sources
    without one use a status_transform (e.g. wfigs_status). An unmapped code
    is logged and treated as active rather than silently dropped, so a
    provider status change is visible and no fire is hidden.

    Args:
        raw_value: The source's raw status value
        data_file: Data file config for the source
        get_value_fn: Accessor for other raw fields on the same record, for
            transforms that need more than the status value itself
    """
    transform_name = data_file.mapping.get("status_transform")
    if transform_name:
        return STATUS_TRANSFORMS[transform_name](raw_value, get_value_fn)

    level = status_to_level(raw_value, data_file.status_map)
    if level is None:
        logging.error(
            f"Unmapped {data_file.location} fire status {raw_value!r} "
            f"(known: {', '.join(data_file.status_map) or 'none'}); treating as "
            f"active so the fire is not hidden. status_map likely needs updating."
        )
        level = STATUS_LEVELS['active']
    return raw_value, level


def _process_fields(field_mapping, data_file, get_value_fn):
    """
    Process a set of fields and return processed values.

    Args:
        field_mapping: Dict of {data_key: source_key}
        data_file: Data file config
        get_value_fn: Function that takes source_key and returns raw value

    Returns:
        Dict of processed field values.
    """
    result = {}
    for data_key, source_key in field_mapping.items():
        raw_value = get_value_fn(source_key)
        if data_key == 'Status':
            result['Status'], result['StatusLevel'] = _resolve_status(raw_value, data_file, get_value_fn)
        else:
            result[data_key] = _apply_transform(data_key, raw_value, data_file.mapping)

    return result

def _fire_attributes(data_file, row) -> dict:
    """Normalize a fire row's mapped attributes (no location-derived fields)."""
    data = _process_fields(
        field_mapping=data_file.mapping.get("fields", {}),
        data_file=data_file,
        get_value_fn=lambda key: getattr(row, key, None),
    )
    # Strip None values
    return {k: v for k, v in data.items() if v is not None}


def _normalize_row(data_file, row, location, closest_point, distance) -> dict:
    """
    Normalizes a row of fire data according to the supplied data_file.mapping.
    """
    data = {
        "Distance": distance,
        "Direction": compass_direction(location, closest_point),
    }
    data.update(_fire_attributes(data_file, row))
    return data


def _realtime_data_file(location: str, realtime: RealtimeFireConfig) -> DataFile:
    """Build the DataFile that normalizes a source's realtime columns."""
    mapping = {'fields': realtime.mapping}
    mapping.update({
        f'{key.lower()}_transform': name
        for key, name in realtime.transforms.items()
    })
    return DataFile(location=location, mapping=mapping, status_map=realtime.status_map)


# Database rows are stored pre-normalized, so every source shares this
# identity mapping on the fallback read path.
_DB_DATA_FILE = DataFile(
    location='DB',
    mapping={
        'fields': {key: key for key in
                   ('Fire', 'Name', 'Location', 'Type', 'Size', 'Status', 'Discovered')},
        'status_transform': 'stored',
        'discovered_transform': 'iso_datetime',
    },
    status_map={},
)


def _parse_source_timestamp(value, tz):
    """Parse a source's per-fire update timestamp to an aware UTC datetime.

    ArcGIS date fields arrive as epoch milliseconds; some sources publish
    zoneless local strings instead, parsed in the source's configured IANA
    zone (a zoneless string without a configured zone fails loudly).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, float)):
        return epoch_ms_to_datetime(value)
    if tz is None:
        raise ValueError(f"Zoneless timestamp {value!r} needs a configured source timezone")
    naive = datetime.strptime(str(value).strip(), '%Y/%m/%d %H:%M:%S')
    return tz.localize(naive).astimezone(timezone.utc)


def fire_keys(frame: gpd.GeoDataFrame, key_fields) -> list[str]:
    """Season-stable per-fire join keys derived from a source's key fields."""
    missing = [field for field in key_fields if field not in frame.columns]
    if missing:
        raise ValueError(f"Fire frame is missing key field(s): {', '.join(missing)}")
    rows = frame[list(key_fields)].itertuples(index=False, name=None)
    return ['-'.join(str(value) for value in row) for row in rows]


def normalize_for_db(fires: gpd.GeoDataFrame, location: str,
                     realtime: RealtimeFireConfig) -> gpd.GeoDataFrame:
    """Normalize a merged realtime frame into fire database rows.

    Applies the source's field mapping and transforms (the same ones the
    search path uses), derives the season-stable fire_key from key_fields,
    and parses the source's update timestamp. Returned in EPSG:4326 for
    storage.
    """
    data_file = _realtime_data_file(location, realtime)
    tz = pytz.timezone(realtime.timezone) if realtime.timezone else None
    records = []
    for _, row in fires.iterrows():
        data = _process_fields(
            field_mapping=realtime.mapping,
            data_file=data_file,
            get_value_fn=lambda key: getattr(row, key, None),
        )
        data['Updated'] = (_parse_source_timestamp(getattr(row, realtime.updated_field, None), tz)
                           if realtime.updated_field else None)
        data['latitude'] = getattr(row, 'latitude', None)
        data['longitude'] = getattr(row, 'longitude', None)
        records.append(data)
    gdf = gpd.GeoDataFrame(records, geometry=list(fires.geometry), crs=fires.crs)
    gdf['fire_key'] = fire_keys(fires, realtime.key_fields)
    return gdf.to_crs(epsg=4326)


class FindFires:
    """Locate fires within [radius]km of a lat/lon coordinate."""

    def __init__(self, coords, filters=None):
        self.settings = get_config()
        self.distance_limit = self.settings.max_radius * 1000
        self.coords = coords
        # All geometry math runs in a projection centered on the user, where
        # the user is the origin and distances/bearings are true.
        self.crs = local_crs(coords)
        self.location = Point(0, 0)
        self.sources = self._data_sources()

        # Build filters with defaults
        filters = filters or {}
        default_filters = {
            'status': self.settings.fire_status,
            'distance': self.settings.fire_radius,
            'size': self.settings.fire_size
        }
        self.filters = {**default_filters, **filters}
        # An explicit "all" shows every fire: drop the default size minimum
        # so small and not-yet-sized fires are included.
        if filters.get('status') == 'all' and 'size' not in filters:
            del self.filters['size']

        # Sources that produced no data at all (API down, nothing stored),
        # as opposed to sources with zero nearby fires.
        self.unavailable_sources: list[str] = []
        # Fetch time per source on stored data fallback after a realtime failure.
        self.fallback_fetches: dict[str, str] = {}

    def out_of_range(self) -> bool:
        """
        Checks if the fire sources are within range of the given coordinates.

        Returns:
            bool: True if no data sources in range, False otherwise.
        """
        return not bool(self.sources)

    def search(self, perimeters: gpd.GeoDataFrame, filters: Dict[str, Any], data_file: DataFile) -> list[Dict[str, Any]]:
        """Search for fires within distance limit.

        Args:
            perimeters: GeoDataFrame containing fire perimeter geometries
            filters: Dictionary of filter criteria (distance, status, size)
            data_file: Data file configuration for this source

        Returns:
            List of normalized fire data dictionaries
        """
        user_distance = filters.get('distance', self.settings.fire_radius)
        search_limit = min(user_distance, self.settings.max_radius) * 1000

        perimeters = perimeters.to_crs(self.crs)
        fires = []
        for _, row in perimeters.iterrows():
            fire_perimeter = row['geometry']
            distance = fire_perimeter.distance(self.location)
            if distance > search_limit:
                continue
            pointB = nearest_points(self.location, fire_perimeter)[1]
            data = _normalize_row(data_file, row, self.location, pointB, distance)
            # History join identity and (on the database path) the data's
            # own timestamp, consumed by growth.enrich().
            if row.get('fire_key') is not None:
                data['FireKey'] = row['fire_key']
            if isinstance(row.get('Updated'), str):
                data['DataTime'] = row['Updated']
            fires.append(data)

        # Return filtered fires.
        return apply_filters(fires, filters, self.settings)


    def _record(self, location: str, fires: gpd.GeoDataFrame, realtime: RealtimeFireConfig):
        """Record fetched fires to the database.

        Storage failures must never break the user's request; they are
        logged and the response proceeds from the fetched data.
        """
        try:
            normalized = normalize_for_db(fires, location, realtime)
            conn = firedb.connect(self.settings.database)
            try:
                firedb.record_fires(conn, location, normalized, datetime.now(timezone.utc))
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError, KeyError, AttributeError) as e:
            logging.error(f"Failed to record {location} fires to the database: {e}")

    def _load_stored(self, location: str, fallback: bool = False) -> Optional[gpd.GeoDataFrame]:
        """Serve a source from the database, at any age, logging staleness.

        fallback means the realtime fetch failed, so the response gets a
        freshness marker. When realtime is disabled by config, stored data
        is the normal mode and no marker is shown.

        Returns None (and marks the source unavailable) when nothing is stored.
        """
        fires = None
        try:
            conn = firedb.connect(self.settings.database)
            try:
                fires = firedb.load_source(conn, location)
                fetched = firedb.latest_fetch(conn, location)
            finally:
                conn.close()
        except sqlite3.Error as e:
            logging.error(f"Fire database read failed for {location}: {e}")
        if fires is None:
            logging.error(f"No stored fire data for {location}; source unavailable.")
            self.unavailable_sources.append(location)
            return None
        logging.warning(f"Serving {location} fires from the database (fetched {fetched}).")
        if fallback:
            self.fallback_fetches[location] = fetched
        return fires

    def _load_source(self, data_file: DataFile) -> tuple[gpd.GeoDataFrame | None, DataFile]:
        """Return (fires GeoDataFrame, effective DataFile) for a source.

        Realtime sources are queried live, recorded to the database, and
        use the realtime field mapping; when the API is unavailable (or
        realtime is disabled) the latest stored data is served with the
        shared database mapping.
        """
        realtime = data_file.realtime
        if realtime and realtime.enabled:
            radius_km = min(self.filters['distance'], self.settings.max_radius)
            fires = fetch_fires(realtime, self.coords, radius_km)
            if fires is not None:
                self._record(data_file.location, fires, realtime)
                fires = fires.assign(fire_key=fire_keys(fires, realtime.key_fields))
                return fires, _realtime_data_file(data_file.location, realtime)
            logging.warning(
                f"Realtime {data_file.location} fire data unavailable; using stored data."
            )
            return self._load_stored(data_file.location, fallback=True), _DB_DATA_FILE
        return self._load_stored(data_file.location), _DB_DATA_FILE

    @property
    def fallback_fetched(self) -> Optional[datetime]:
        """Oldest fetch time among sources whose stored fallback data is
        older than the configured staleness window, or None.
        """
        window = timedelta(hours=self.settings.stale_data_hours)
        now = datetime.now(timezone.utc)
        stale = [fetched_at for fetched in self.fallback_fetches.values()
                 if now - (fetched_at := datetime.fromisoformat(fetched)) > window]
        return min(stale) if stale else None

    def nearby(self) -> list[Dict[str, Any]]:
        """Find all fires within distance limit.

        Returns:
            List of normalized fire data dictionaries
        """
        fires = []
        for source in self.sources:
            # _data_sources() only returns configured locations.
            data_file = next(df for df in self.settings.data if df.location == source)
            fire_perimeters, data_file = self._load_source(data_file)
            if fire_perimeters is None:
                continue
            found = self.search(fire_perimeters, self.filters, data_file)
            for fire in found:
                fire['Source'] = source
            fires += found

        growth.enrich(fires, self.settings.database)

        # Sort by status priority (active, managed, controlled, out), then distance.
        fires.sort(key=lambda f: (f.get('StatusLevel', float('inf')), f['Distance']))

        return fires

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_boundaries(filepath):
        """Load, simplify, and cache a boundary file.

        Boundaries only gate which sources are near (within max_radius), so
        kilometer-scale simplification is lossless for that decision and
        makes the per-request reprojection ~100x cheaper. Long edges are then
        re-densified: reprojection maps vertices only, and a sparse straight
        edge (e.g. the 49th-parallel border) reprojects as a chord that can
        cut tens of km inside the true curved boundary.
        """
        boundaries = gpd.read_file(filepath)
        geographic = boundaries.crs.is_geographic
        boundaries.geometry = (boundaries.geometry
                               .simplify(0.01 if geographic else 1000)
                               .segmentize(0.5 if geographic else 50_000))
        return boundaries

    @lru_cache
    def _data_sources(self):
        """
        Return list of configured data source locations (ISO country codes or
        Canadian province codes) whose polygons lie within self.distance_limit
        of the query point.

        The boundary files cover the whole world, so codes without a
        configured data source are dropped: a point is only "in coverage"
        when there is a source that can answer for it.
        """
        countries = self._load_boundaries("boundaries/countries.zip")
        canada_provinces = self._load_boundaries("boundaries/canada_provinces.zip")

        sources = []
        # Find all matching countries.
        for _, row in countries.to_crs(self.crs).iterrows():
            distance = row['geometry'].distance(self.location)
            if distance <= self.distance_limit:
                sources.append(row.ISO)
        # Find all matching Canadian provinces.
        for _, row in canada_provinces.to_crs(self.crs).iterrows():
            distance = row['geometry'].distance(self.location)
            if distance <= self.distance_limit:
                sources.append(row.postal)

        configured = {df.location for df in self.settings.data}
        return [source for source in sources if source in configured]


def _first_match(matches: gpd.GeoDataFrame, data_file: DataFile,
                 coords: Optional[tuple]) -> Dict[str, Any]:
    """Normalize the first matched fire, adding distance/direction if coords."""
    if coords is not None:
        matches = matches.to_crs(local_crs(coords))
    row = matches.iloc[0]
    data = _fire_attributes(data_file, row)
    if coords is not None:
        origin = Point(0, 0)
        geometry = row['geometry']
        data['Distance'] = geometry.distance(origin)
        data['Direction'] = compass_direction(origin, nearest_points(origin, geometry)[1])
    return data


def _is_stale(fetched: Optional[str], ttl_seconds: int) -> bool:
    """True when a source's newest fetch is older than its cache TTL."""
    if not fetched:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(fetched) > timedelta(seconds=ttl_seconds)


def _stale_marker(fetched: Optional[str]) -> Optional[datetime]:
    """Fetch time to surface as a freshness marker, or None when the stored
    data is fresh enough to present without one (the same staleness window
    the radius search's marker uses)."""
    if not fetched:
        return None
    settings = get_config()
    fetched_dt = datetime.fromisoformat(fetched)
    if datetime.now(timezone.utc) - fetched_dt > timedelta(hours=settings.stale_data_hours):
        return fetched_dt
    return None


def find_fire(term: str,
              coords: Optional[tuple] = None) -> tuple[list[Dict[str, Any]], Optional[datetime]]:
    """Look up a single fire by its displayed identifier, ignoring location.

    The identifier is matched exactly (case-insensitive) against each
    source's Fire field. The database is checked first; a stored match whose
    source is older than that source's cache TTL triggers one targeted live
    refresh of that source, with the stored record kept as the fallback if
    the live query fails. Only if no stored fire matches at all are the
    realtime layers queried, stopping at the first match. Fires sharing a
    name across sources beyond the first are not returned. When coords are
    supplied, the match also gets its distance and compass direction.

    Args:
        term: The fire identifier or name to search for
        coords: Optional (latitude, longitude) to measure distance from

    Returns:
        (fires, stale_fetched): a single-element list with the matched fire
        (empty when nothing matched), and the fetch time of served stored
        data old enough to warrant a "Data from" marker (None otherwise).
    """
    settings = get_config()
    try:
        conn = firedb.connect(settings.database)
    except (sqlite3.Error, OSError) as e:
        logging.error(f"Fire database unavailable for lookup: {e}")
        conn = None

    try:
        # 1. The database aggregates every source's recent fires; prefer it,
        #    refreshing a single source live only when its data is stale.
        if conn is not None:
            for data_file in settings.data:
                try:
                    stored = firedb.find_by_fire(conn, data_file.location, term)
                except sqlite3.Error as e:
                    logging.error(f"Fire database read failed for {data_file.location}: {e}")
                    continue
                if stored is None or stored.empty:
                    continue
                realtime = data_file.realtime
                fetched = firedb.latest_fetch(conn, data_file.location)
                if realtime and realtime.enabled and _is_stale(fetched, realtime.cache_timeout):
                    live = fetch_fires_by_id(realtime, term, data_file.location)
                    if live is not None and not live.empty:
                        return [_first_match(
                            live, _realtime_data_file(data_file.location, realtime), coords)], None
                    # The live refresh failed; serve stored data, flagged as aged.
                    return [_first_match(stored, _DB_DATA_FILE, coords)], _stale_marker(fetched)
                return [_first_match(stored, _DB_DATA_FILE, coords)], None

        # 2. Not stored yet: query the realtime layers, first match wins. An
        #    identifier query is a filtered slice, not the source's full state,
        #    so it is deliberately not recorded to the database.
        for data_file in settings.data:
            realtime = data_file.realtime
            if not (realtime and realtime.enabled):
                continue
            matches = fetch_fires_by_id(realtime, term, data_file.location)
            if matches is not None and not matches.empty:
                return [_first_match(
                    matches, _realtime_data_file(data_file.location, realtime), coords)], None

        return [], None
    finally:
        if conn is not None:
            conn.close()
