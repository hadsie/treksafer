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
        self.quebec_wgs84 = None
        self.quebec_meters = None
        self._load_quebec()

    def _load_quebec(self):
        """Load and prepare Quebec province geodata."""
        try:
            provinces = gpd.read_file('boundaries/canada_provinces.zip')
            quebec = provinces[provinces['postal'] == 'QC']

            if quebec.empty:
                logging.warning("Quebec province not found in shapefile")
                return

            # Store in WGS84 for contains() checks
            self.quebec_wgs84 = quebec.to_crs(epsg=4326)

            # Store in meters for distance calculations
            self.quebec_meters = quebec.to_crs(epsg=3857)

        except FileNotFoundError as e:
            logging.warning(f"Canada provinces shapefile not found: {e}")

    def _is_in_quebec(self, coords: tuple) -> bool:
        """Check if coordinates are in Quebec province."""
        if self.quebec_wgs84 is None:
            return False

        point = Point(coords[1], coords[0])  # lon, lat
        return self.quebec_wgs84.iloc[0]['geometry'].contains(point)

    def distance_from_region(self, coords: tuple) -> Optional[float]:
        """Calculate distance from Quebec province."""
        if self.quebec_meters is None:
            return float('inf')

        # Check if in Quebec
        if self._is_in_quebec(coords):
            return None  # Exact match

        # Calculate distance (both already in meters)
        point_meters = coords_to_point_meters(coords)
        distance_m = self.quebec_meters.iloc[0]['geometry'].distance(point_meters)
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
                result = self._parse_forecast(response.json(), coords)
                if result is None:
                    logging.warning(f"Invalid or empty JSON response from Avalanche Quebec API for coords {coords}")
                return result
            else:
                logging.warning(f"Avalanche Quebec API returned status code {response.status_code} for coords {coords}")

        except RequestException as e:
            logging.warning(f"Network error checking Quebec avalanche data: {e}")

        return None

    def _parse_forecast(self, data: Dict[str, Any], coords: tuple) -> Optional[Dict[str, Any]]:
        """Parse Avalanche Quebec API response."""
        if not data or 'dangerRatings' not in data:
            return None

        # Parse danger ratings
        forecasts_by_date = {}
        danger_ratings = data.get('dangerRatings', [])

        if not danger_ratings:
            logging.warning(f"Avalanche Quebec API returned empty danger ratings for coords {coords}")

        for rating in danger_ratings:
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
