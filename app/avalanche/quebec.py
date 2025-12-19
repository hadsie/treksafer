"""Avalanche Quebec provider implementation."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Dict, Any

import geopandas as gpd
from requests import RequestException
from shapely.geometry import Point

from .base import AvalancheProvider
from ..config import get_config, AvalancheProviderConfig
from ..helpers import coords_to_point_meters


class AvalancheQuebecProvider(AvalancheProvider):
    """Avalanche Quebec API provider."""

    def __init__(self, config: AvalancheProviderConfig):
        super().__init__(config)
        self.province_gdf = self._load_provinces()

    def _load_provinces(self):
        """Load Canadian provinces shapefile."""
        try:
            return gpd.read_file('boundaries/canada_provinces.zip')
        except FileNotFoundError as e:
            logging.warning(f"Canada provinces shapefile not found: {e}")
            return None

    def _is_in_quebec(self, coords: tuple) -> bool:
        """Check if coordinates are in Quebec province."""
        if self.province_gdf is None:
            return False

        point = Point(coords[1], coords[0])  # lon, lat

        # Find Quebec province
        quebec = self.province_gdf[self.province_gdf['postal'] == 'QC']
        if quebec.empty:
            return False

        return quebec.iloc[0]['geometry'].contains(point)

    def distance_from_region(self, coords: tuple) -> Optional[float]:
        """Calculate distance from Quebec province."""
        if self.province_gdf is None:
            return float('inf')

        # Check if in Quebec
        if self._is_in_quebec(coords):
            return None  # Exact match

        # Calculate distance to Quebec border
        quebec = self.province_gdf[self.province_gdf['postal'] == 'QC']

        if quebec.empty:
            return float('inf')

        # Convert to meters for distance calculation
        point_meters = coords_to_point_meters(coords)

        quebec_meters = quebec.to_crs(epsg=3857)
        distance_m = quebec_meters.iloc[0]['geometry'].distance(point_meters)
        distance_km = distance_m / 1000

        # Apply limit
        settings = get_config()
        if distance_km > settings.avalanche_distance_buffer:
            return float('inf')

        return distance_km

    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside Quebec."""
        return not self._is_in_quebec(coords)

    def get_forecast(self, coords: tuple) -> Optional[Dict[str, Any]]:
        """Get forecast from Avalanche Quebec API."""
        try:
            # Replace {lang} template with actual language
            url = self.api_base.format(lang=self.config.language)
            response = self._request(url)

            if response.status_code == 200:
                return self._parse_forecast(response.json(), coords)

        except RequestException as e:
            logging.warning(f"Network error checking Quebec avalanche data: {e}")

        return None

    def _parse_forecast(self, data: Dict[str, Any], coords: tuple) -> Optional[Dict[str, Any]]:
        """Parse Avalanche Quebec API response."""
        if not data or 'dangerRatings' not in data:
            return None

        # Parse danger ratings
        forecasts_by_date = {}
        for rating in data.get('dangerRatings', []):
            dt = datetime.strptime(rating['date']['value'], '%Y-%m-%dT%H:%M:%SZ')
            date_str = dt.strftime('%Y-%m-%d')

            ratings = rating.get('ratings', {})
            forecasts_by_date[date_str] = {
                'alpine_rating': ratings.get('alp', {}).get('rating', {}).get('display', 'No Rating'),
                'treeline_rating': ratings.get('tln', {}).get('rating', {}).get('display', 'No Rating'),
                'below_treeline_rating': ratings.get('btl', {}).get('rating', {}).get('display', 'No Rating'),
            }

        # Simplified problems (Quebec uses image URLs)
        problems = []
        for problem in data.get('problems', []):
            problems.append({
                'type': problem.get('type', 'Unknown'),
                'elevations': [],
                'aspects': [],
                'likelihood': '',
                'size_min': '',
                'size_max': ''
            })

        return {
            'region': 'Chic-Chocs',  # Quebec only has one main region
            'date_issued': data.get('dateIssued', ''),
            'timezone': 'America/Toronto',  # Eastern Time
            'forecasts': forecasts_by_date,
            'problems': problems
        }
