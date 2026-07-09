"""
Find and summarize nearby wildfires

Usage:

    fires = FindFires((49.25, -123.1))   # lat, lon in WGS-84
    if fires.out_of_range():
        print("No fire data sources near you.")
    else:
        print(fires.nearby())

Design notes
------------
* Fire sources (realtime ArcGIS layers) are declared in config.yaml.
* Every successful fetch is recorded to the fire database, which is also
  the fallback when a source's API is unavailable.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, Any, Optional

import geopandas as gpd
import pytz
from shapely.ops import nearest_points

from . import db as firedb
from ..config import get_config, DataFile, RealtimeFireConfig
from .sources import fetch_fires
from ..helpers import acres_to_hectares, compass_direction, coords_to_point_meters, epoch_ms_to_datetime
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

def _normalize_row(data_file, row, location, closest_point, distance) -> dict:
    """
    Normalizes a row of fire data according to the supplied data_file.mapping.
    """
    data = {
        "Distance": distance,
        "Direction": compass_direction(location, closest_point),
    }
    data.update(_process_fields(
        field_mapping=data_file.mapping.get("fields", {}),
        data_file=data_file,
        get_value_fn=lambda key: getattr(row, key, None),
    ))

    # Strip None values
    data = {k: v for k, v in data.items() if v is not None}

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
        data['fire_key'] = '-'.join(str(getattr(row, field)) for field in realtime.key_fields)
        data['Updated'] = (_parse_source_timestamp(getattr(row, realtime.updated_field, None), tz)
                           if realtime.updated_field else None)
        data['latitude'] = getattr(row, 'latitude', None)
        data['longitude'] = getattr(row, 'longitude', None)
        records.append(data)
    gdf = gpd.GeoDataFrame(records, geometry=list(fires.geometry), crs=fires.crs)
    return gdf.to_crs(epsg=4326)


class FindFires:
    """Locate fires within [radius]km of a lat/lon coordinate."""

    def __init__(self, coords, filters=None):
        self.settings = get_config()
        self.distance_limit = self.settings.max_radius * 1000
        self.coords = coords
        self.location = coords_to_point_meters(coords)
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

        fires = []
        for _, row in perimeters.iterrows():
            fire_perimeter = row['geometry']
            distance = fire_perimeter.distance(self.location)
            if distance > search_limit:
                continue
            pointB = nearest_points(self.location, fire_perimeter)[1]
            data = _normalize_row(data_file, row, self.location, pointB, distance)
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

    def _load_stored(self, location: str) -> Optional[gpd.GeoDataFrame]:
        """Serve a source from the database, at any age, logging staleness.

        Returns None (and marks the source unavailable) when nothing is
        stored: an empty database must produce "data unavailable", never a
        confident "no fires".
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
                return fires, _realtime_data_file(data_file.location, realtime)
            logging.warning(
                f"Realtime {data_file.location} fire data unavailable; using stored data."
            )
        return self._load_stored(data_file.location), _DB_DATA_FILE

    def nearby(self) -> list[Dict[str, Any]]:
        """Find all fires within distance limit.

        Returns:
            List of normalized fire data dictionaries
        """
        fires = []
        for source in self.sources:
            data_file = next((df for df in self.settings.data if df.location == source), None)
            if not data_file:
                continue
            fire_perimeters, data_file = self._load_source(data_file)
            if fire_perimeters is None:
                continue
            fires += self.search(fire_perimeters, self.filters, data_file)

        # Sort by status priority (active, managed, controlled, out), then distance.
        fires.sort(key=lambda f: (f.get('StatusLevel', float('inf')), f['Distance']))

        return fires

    @lru_cache
    def _data_sources(self):
        """
        Return list of ISO country codes or Canadian province codes whose
        polygon centroids lie within self.distance_limit of the query point.
        """
        countries_filepath = "boundaries/countries.zip"
        countries = gpd.read_file(countries_filepath)
        canada_provinces_filepath = "boundaries/canada_provinces.zip"
        canada_provinces = gpd.read_file(canada_provinces_filepath)

        sources = []
        # Find all matching countries.
        for _, row in countries.to_crs(epsg=3857).iterrows():
            distance = row['geometry'].distance(self.location)
            if distance <= self.distance_limit:
                sources.append(row.ISO)
        # Find all matching Canadian provinces.
        for _, row in canada_provinces.to_crs(epsg=3857).iterrows():
            distance = row['geometry'].distance(self.location)
            if distance <= self.distance_limit:
                sources.append(row.postal)
        return sources
