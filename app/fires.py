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
import re
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import requests
from shapely.ops import nearest_points

from .config import get_config
from .helpers import acres_to_hectares, compass_direction, coords_to_point_meters
from .filters import apply_filters, STATUS_LEVELS

TRANSFORMS = {
    "acres_to_hectares": acres_to_hectares,
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


def status_to_level(value, status_map) -> int:
    for status, codes in status_map.items():
        if value in codes:
            return STATUS_LEVELS[status]
    return float('inf')


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
            result[data_key] = status_to_level(raw_value, data_file.status_map)
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

    def __init__(self, coords):
        self.settings = get_config()
        self.distance_limit = self.settings.max_radius * 1000
        self.location = coords_to_point_meters(coords)
        self.sources = self._data_sources()

    def out_of_range(self) -> bool:
        """
        Checks if the fire sources are within range of the given coordinates.

        Returns:
            bool: True if no data sources in range, False otherwise.
        """
        return not bool(self.sources)

    def search(self, perimeters, filters, data_file):

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
        """Load and cache shapefile with CRS transformation."""
        return gpd.read_file(filepath).to_crs(epsg=3857)

    def nearby(self, filters=None):
        fires = []
        sources_map = self.sources_map()

        # Build default filters from configuration using factory function
        default_filters = {
            'status': self.settings.fire_status,
            'distance': self.settings.fire_radius,
            'size': self.settings.fire_size
        }

        # Merge user filters with defaults (user filters override defaults)
        final_filters = {**default_filters, **(filters or {})}

        for source in self.sources:
            if source not in sources_map:
                continue
            fire_perimeters = self._load_shapefile(str(sources_map[source]))
            # Grab the matching data file settings
            data_file = next((df for df in self.settings.data if df.location == source), None)
            if data_file:
                fires += self.search(fire_perimeters, final_filters, data_file)

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
