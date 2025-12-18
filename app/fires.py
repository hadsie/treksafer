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
from .filters import apply_filters, create_fire_filters

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

def _normalize_row(mapping, row, location, closest_point, distance):
    """
    Normalizes a row of fire data from a shapefile according to the supplied
    mapping dict.
    """
    data = {
        "Distance": distance,
        "Direction": compass_direction(location, closest_point),
    }

    fields = mapping.get("fields", {})
    for data_key, row_attr in fields.items():
        raw_value = getattr(row, row_attr, None)
        data[data_key] = _apply_transform(data_key, raw_value, mapping)

    # Optionally call the API for additional attributes if available.
    api_config = mapping.get("api")
    if api_config:
        try:
            # Build the API requires URL, and replace the variables with row field data.
            field_names = re.findall(r"{(\w+)}", api_config["url"])
            row_dict = {field: getattr(row, field, None) for field in field_names}
            url = api_config["url"].format(**row_dict)
            # Note: These requests are cached for 4 hours (unless set otherwise in the config yaml)
            # @todo: Make timeout configurable via settings
            response = requests.get(url, timeout=30).json()
            for data_key, api_field in api_config["fields"].items():
                raw_value = response.get(api_field)
                data[data_key] = _apply_transform(data_key, raw_value, mapping)
        except (requests.RequestException, KeyError, ValueError) as e:
            logging.warning(f"Failed to fetch API data for fire {data.get('Fire', 'unknown')}: {e}")

    # Strip None values
    data = {k: v for k, v in data.items() if v is not None}

    return data


class FindFires:
    """Locate fires within [radius]km of a lat/lon coordinate."""

    def __init__(self, coords):
        self.settings = get_config()
        self.distance_limit = self.settings.fire_radius * 1000
        self.location = coords_to_point_meters(coords)
        self.sources = self._data_sources()

    def out_of_range(self) -> bool:
        """
        Checks if the fire sources are within range of the given coordinates.

        Returns:
            bool: True if no data sources in range, False otherwise.
        """
        return not bool(self.sources)

    def search(self, perimeters, mapping):
        fires = []
        for _, row in perimeters.iterrows():
            fire_perimeter = row['geometry']
            distance = fire_perimeter.distance(self.location)
            if distance < self.distance_limit:
                pointB = nearest_points(self.location, fire_perimeter)[1]
                data = _normalize_row(mapping, row, self.location, pointB, distance)
                fires.append(data)

        return fires


    @staticmethod
    @lru_cache(maxsize=16)
    def _load_shapefile(filepath):
        """Load and cache shapefile with CRS transformation."""
        return gpd.read_file(filepath).to_crs(epsg=3857)

    def nearby(self, filters=None):
        fires = []
        sources_map = self.sources_map()

        # Build default filters from configuration using factory function
        default_filters = create_fire_filters(self.settings)

        # Merge user filters with defaults (user filters override defaults)
        final_filters = {**default_filters, **(filters or {})}

        for source in self.sources:
            if source not in sources_map:
                continue
            fire_perimeters = self._load_shapefile(str(sources_map[source]))
            mapping = None
            data_file = None
            for df in self.settings.data:
                if df.location == source:
                    mapping = df.mapping
                    data_file = df
                    break

            # Search fires in this source (without filtering)
            source_fires = self.search(fire_perimeters, mapping)

            # Apply generic filtering
            source_fires = apply_filters(
                source_fires, final_filters, data_file, self.location, self.settings
            )

            fires += source_fires
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
