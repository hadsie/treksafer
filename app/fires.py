#!venv/bin/python

import geopandas as gpd
import re
import requests

from functools import lru_cache
from pathlib import Path
from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import nearest_points

from .config import get_config
from .helpers import acres_to_hectares, compass_direction

TRANSFORMS = {
    "acres_to_hectares": acres_to_hectares,
}

def process_fire_value(data_key, raw_value, mapping):
    """
    Applies a transform to a value if a transform is defined for this data key.
    """
    transform_name = mapping.get(f"{data_key.lower()}_transform")
    if transform_name:
        transform_func = TRANSFORMS.get(transform_name)
        if transform_func:
            return transform_func(raw_value)
    return raw_value

def process_fire_row(mapping, row, location, closest_point, distance):
    """
    Normalizes a row of fire data from a shapefile according to a mapping dict.
    """
    data = {
        "Distance": distance,
        "Direction": compass_direction(location, closest_point),
    }

    fields = mapping.get("fields", {})
    for data_key, row_attr in fields.items():
        raw_value = getattr(row, row_attr, None)
        data[data_key] = process_fire_value(data_key, raw_value, mapping)

    # Optionally call the API for additional attributes if available.
    api_config = mapping.get("api")
    if api_config:
        try:
            # Build the API requires URL, and replace the variables with row field data.
            field_names = re.findall(r"{(\w+)}", api_config["url"])
            row_dict = {field: getattr(row, field, None) for field in field_names}
            url = api_config["url"].format(**row_dict)
            # Note: These requests are cached for 4 hours (unless set otherwise in the config yaml)
            response = requests.get(url, timeout=30).json()
            for data_key, api_field in api_config["fields"].items():
                raw_value = response.get(api_field)
                data[data_key] = process_fire_value(data_key, raw_value, mapping)
        except Exception as e:
            print(f"[WARN] Failed to fetch API data for fire {data.get('Fire', 'unknown')}: {e}")

    # Strip None values
    data = {k: v for k, v in data.items() if v is not None}

    return data

class FindFires:
    def __init__(self, coords):
        self.settings = get_config()
        self.distance_limit = self.settings.fire_radius * 1000
        self.location = self._convert_to_point(coords)
        self.sources = self._data_sources()

    def sources_map(self):
        sources_map = {}
        for data_file in self.settings.data:
            filename = data_file.filename.replace(r'{DATE}', r'*')
            target_dir = Path(self.settings.shapefiles) / data_file.location
            matches = sorted(target_dir.glob(filename), reverse=True)
            if matches:
                sources_map[data_file.location] = matches[0]
        return sources_map

    def _convert_to_point(self, coords):
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857")
        transformed = transformer.transform(coords[0], coords[1])
        location = Point(transformed[0], transformed[1])
        return location

    def out_of_range(self):
        return not bool(len(self.sources))

    @lru_cache
    def _data_sources(self):
        """Find all data sources that are within distance_limit to the location."""
        countries_filepath = "shapefiles/World_Countries_(Generalized)_-573431906301700955.zip"
        countries = gpd.read_file(countries_filepath)
        canada_provinces_filepath = "shapefiles/Canada_Provincial_boundaries_generalized_-3595751168909660783.zip"
        canada_provinces = gpd.read_file(canada_provinces_filepath)

        sources = []
        # Find all matching countries.
        for index, row in countries.to_crs(epsg=3857).iterrows():
            distance = row['geometry'].distance(self.location)
            if distance <= self.distance_limit:
                sources.append(row.ISO)
        # Find all matching Canadian provinces.
        for index, row in canada_provinces.to_crs(epsg=3857).iterrows():
            distance = row['geometry'].distance(self.location)
            if distance <= self.distance_limit:
                sources.append(row.postal)
        return sources

    def search(self, perimeters, mapping):
        fires = []
        for index, row in perimeters.iterrows():
            fire_perimeter = row['geometry']
            distance = fire_perimeter.distance(self.location)
            if distance < self.distance_limit:
                pointB = nearest_points(self.location, fire_perimeter)[1]
                data = process_fire_row(mapping, row, self.location, pointB, distance)
                fires.append(data)
        return fires

    def nearby(self):
        fires = []
        sources_map = self.sources_map()
        for source in self.sources:
            if source not in sources_map:
                continue
            fire_perimeters = gpd.read_file(sources_map[source]).to_crs(epsg=3857)
            mapping = None
            for data_file in self.settings.data:
                if data_file.location == source:
                    mapping = data_file.mapping
            fires += self.search(fire_perimeters, mapping)
        return fires
