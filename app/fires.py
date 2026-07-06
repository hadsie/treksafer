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
* Data sources (shapefiles + optional REST APIs) are declared in config/<env>.yaml.
* The heavy geopandas look-ups are memoised with `@lru_cache`, so repeated
  calls for the same coords are cheap.
"""
from __future__ import annotations

import logging
import math
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any

import geopandas as gpd
import requests
from shapely.ops import nearest_points

from .arcgis import fetch_fires
from .config import get_config, DataFile
from .helpers import acres_to_hectares, compass_direction, coords_to_point_meters, epoch_ms_to_datetime
from .filters import apply_filters, STATUS_LEVELS

TRANSFORMS = {
    "acres_to_hectares": acres_to_hectares,
    "epoch_ms": epoch_ms_to_datetime,
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


STATUS_TRANSFORMS = {
    "percent_contained": _status_from_percent_contained,
}


def _resolve_status(raw_value, data_file):
    """Return (display_status, status_level) for a source's raw status value.

    Sources with a stage-of-control field map raw codes via status_map; sources
    with only a numeric signal use a status_transform (e.g. percent_contained).
    An unmapped code is logged and treated as active rather than silently
    dropped, so a provider status change is visible and no fire is hidden.
    """
    transform_name = data_file.mapping.get("status_transform")
    if transform_name:
        return STATUS_TRANSFORMS[transform_name](raw_value)

    level = status_to_level(raw_value, data_file.status_map)
    if level is None:
        logging.error(
            f"Unmapped {data_file.location} fire status {raw_value!r} "
            f"(known: {', '.join(data_file.status_map) or 'none'}); treating as "
            f"active so the fire is not hidden. status_map likely needs updating."
        )
        level = STATUS_LEVELS['active']
    return raw_value, level


def _gdal_path(filepath):
    """Return a GDAL-readable path for a fire source file.

    FileGDB sources arrive as a .zip wrapping a .gdb directory whose name varies
    per export, so locate the .gdb and build a /vsizip/ path. Zipped shapefiles
    are read directly.
    """
    if filepath.endswith(".zip"):
        with zipfile.ZipFile(filepath) as archive:
            entry = next((n for n in archive.namelist() if ".gdb/" in n), None)
        if entry:
            gdb_dir = entry[: entry.index(".gdb") + len(".gdb")]
            return f"/vsizip/{filepath}/{gdb_dir}"
    return filepath


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
            result['Status'], result['StatusLevel'] = _resolve_status(raw_value, data_file)
        else:
            result[data_key] = _apply_transform(data_key, raw_value, data_file.mapping)

    return result

def _normalize_row(data_file, row, location, closest_point, distance) -> dict:
    """
    Normalizes a row of fire data from a shapefile according to the supplied
    data_file.mapping dict.
    """
    data = {
        "Distance": distance,
        "Direction": compass_direction(location, closest_point),
    }

    # Process shapefile fields
    data.update(_process_fields(
        field_mapping=data_file.mapping.get("fields", {}),
        data_file=data_file,
        get_value_fn=lambda key: getattr(row, key, None),
    ))

    # Optionally call the API for additional attributes if available.
    api_config = data_file.mapping.get("api")
    if api_config:
        try:
            # Build the API requires URL, and replace the variables with row field data.
            field_names = re.findall(r"{(\w+)}", api_config["url"])
            row_dict = {field: getattr(row, field, None) for field in field_names}
            url = api_config["url"].format(**row_dict)
            # Note: These requests are cached for 4 hours (unless set otherwise in the config yaml)
            response = requests.get(url, timeout=30).json()

            data.update(_process_fields(
                field_mapping=api_config["fields"],
                data_file=data_file,
                get_value_fn=lambda key: response.get(key),
            ))
        except (requests.RequestException, KeyError, ValueError) as e:
            logging.warning(f"Failed to fetch API data for fire {data.get('Fire', 'unknown')}: {e}")

    # Strip None values
    data = {k: v for k, v in data.items() if v is not None}

    return data


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
        return apply_filters(fires, filters, data_file, self.location, self.settings)


    @staticmethod
    @lru_cache(maxsize=16)
    def _load_shapefile(filepath):
        """Load and cache a fire source (zipped shapefile or FileGDB)."""
        return gpd.read_file(_gdal_path(filepath)).to_crs(epsg=3857)

    def _load_source(self, data_file: DataFile, sources_map: Dict) -> tuple[gpd.GeoDataFrame | None, DataFile]:
        """Return (fires GeoDataFrame, effective DataFile) for a source.

        Realtime sources are queried live and use the realtime field mapping;
        when the API is unavailable (or realtime is disabled) the newest
        downloaded file is used with the source's regular mapping.
        """
        realtime = data_file.realtime
        if realtime and realtime.enabled:
            radius_km = min(self.filters['distance'], self.settings.max_radius)
            fires = fetch_fires(realtime, self.coords, radius_km)
            if fires is not None:
                mapping = {'fields': realtime.mapping}
                mapping.update({
                    f'{key.lower()}_transform': name
                    for key, name in realtime.transforms.items()
                })
                return fires, DataFile(
                    location=data_file.location,
                    filename=data_file.filename,
                    mapping=mapping,
                    status_map=realtime.status_map,
                )
            logging.warning(
                f"Realtime {data_file.location} fire data unavailable; using downloaded data."
            )

        filepath = sources_map.get(data_file.location)
        if filepath is None:
            return None, data_file
        return self._load_shapefile(str(filepath)), data_file

    def nearby(self) -> list[Dict[str, Any]]:
        """Find all fires within distance limit.

        Returns:
            List of normalized fire data dictionaries
        """
        fires = []
        sources_map = self.sources_map()

        for source in self.sources:
            data_file = next((df for df in self.settings.data if df.location == source), None)
            if not data_file:
                continue
            fire_perimeters, data_file = self._load_source(data_file, sources_map)
            if fire_perimeters is None:
                continue
            fires += self.search(fire_perimeters, self.filters, data_file)

        # Sort by status priority (active, managed, controlled, out), then distance.
        fires.sort(key=lambda f: (f.get('StatusLevel', float('inf')), f['Distance']))

        return fires

    def sources_map(self):
        """
        Returns a dictionary mapping data source locations to their respective
        filenames. Grabs the most recent shapefile in the folder ordered by date.
        """
        sources_map = {}
        for data_file in self.settings.data:
            filename = data_file.filename.replace(r'{DATE}', r'*')
            target_dir = Path(self.settings.shapefiles) / data_file.location
            matches = sorted(target_dir.glob(filename), reverse=True)
            if matches:
                sources_map[data_file.location] = matches[0]
        return sources_map

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
