"""Avalanche Canada provider implementation."""
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


class AvalancheCanadaProvider(AvalancheProvider):
    """Avalanche Canada API provider."""

    def __init__(self, config: AvalancheProviderConfig):
        super().__init__(config)
        # https://github.com/avalanche-canada/forecast-polygons/blob/main/canadian_subregions.shp.zip
        self.regions_gdf = self._load_geodata(
            lambda: gpd.read_file('boundaries/canadian_subregions.shp.zip')
        )

    def _get_region(self, coords: tuple) -> Optional[str]:
        """Get avalanche region name from coordinates.

        First checks for exact match, then nearest region within configured radius.

        Args:
            coords: (latitude, longitude)

        Returns:
            Region name or None if not found
        """
        if self.regions_gdf is None:
            return None

        point_wgs84 = Point(coords[1], coords[0])  # lon, lat

        # Check for exact match first
        matches = self.regions_gdf[self.regions_gdf.contains(point_wgs84)]
        if not matches.empty:
            return matches.iloc[0]['polygon_na']

        # No exact match - find closest within radius
        settings = get_config()
        return self._find_closest_region(coords, settings.avalanche_distance_buffer)

    def _find_closest_region(self, coords: tuple, limit_km: int) -> Optional[str]:
        """Find closest region within distance limit.

        Args:
            coords: (latitude, longitude) in WGS84
            limit_km: Maximum distance in kilometers

        Returns:
            Region name or None if none within limit
        """
        # Calculate distances using helper
        gdf_with_distances = self._calculate_distances(coords)
        if gdf_with_distances is None:
            return None

        # Find nearest polygon within distance limit
        limit_meters = limit_km * 1000
        nearby = gdf_with_distances[gdf_with_distances['distance'] <= limit_meters]

        if not nearby.empty:
            nearest = nearby.sort_values('distance').iloc[0]
            return nearest['polygon_na']

        return None

    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside Canadian avalanche forecast area."""
        return self._get_region(coords) is None

    def get_forecast(self, coords: tuple) -> Optional[Dict[str, Any]]:
        """Get forecast from Avalanche Canada API."""
        try:
            # Replace {lang} template with actual language
            base_url = self.api_base.format(lang=self.config.language)
            url = f"{base_url}?lat={coords[0]}&long={coords[1]}"
            response = self._request(url)

            if response.status_code == 200:
                result = self._parse_forecast(response.json(), coords)
                if result is None:
                    logging.warning(f"Invalid or empty JSON response from Avalanche Canada API for coords {coords}")
                return result
            else:
                logging.warning(f"Avalanche Canada API returned status code {response.status_code} for coords {coords}")

        except RequestException as e:
            logging.warning(f"Network error checking Avalanche Canada data: {e}")

        return None

    def _parse_forecast(self, data: Dict, coords: tuple) -> Optional[Dict]:
        """Parse Avalanche Canada API response.

        Args:
            data: API response data
            coords: Coordinates to lookup subregion

        Returns dict with timezone and all available forecast dates.
        """
        if not data or 'report' not in data:
            return None

        report = data['report']
        if not report['id']:
            return None

        # Extract timezone from API response
        timezone = report.get('timezone', 'America/Vancouver')

        # Get region from shapefile, fall back to API title
        region = self._get_region(coords) or report.get('title', 'Unknown')

        # Parse all available danger ratings
        forecasts_by_date = {}
        danger_ratings = report.get('dangerRatings', [])

        if not danger_ratings:
            logging.warning(f"Avalanche Canada API returned empty danger ratings for coords {coords}")

        for rating in danger_ratings:
            # Use display value (day of week) as key
            day_name = rating['date']['display']

            # Extract ratings by elevation band
            ratings = {}
            for key, value in rating.get('ratings', {}).items():
                if key in ('alp', 'tln', 'btl'):
                    ratings[key] = value.get('rating', {}).get('display', 'No Rating')
                else:
                    logging.warning(f"Invalid avalanche band found in API response: {key}")

            forecasts_by_date[day_name] = {
                'alpine_rating': self._get_rating('alp', ratings),
                'treeline_rating': self._get_rating('tln', ratings),
                'below_treeline_rating': self._get_rating('btl', ratings),
            }

        # Extract avalanche problems (these typically apply to all forecast days)
        problems = []
        for problem in report.get('problems', []):
            prob_type = problem.get('type', {}).get('display', 'Unknown')
            prob_data = problem.get('data', {})

            # Extract elevations
            elevations = [e.get('display', '') for e in prob_data.get('elevations', [])]

            # Extract aspects
            aspects = [a.get('value', '') for a in prob_data.get('aspects', [])]

            # Extract likelihood and size
            likelihood = prob_data.get('likelihood', {}).get('display', '')
            min_size = prob_data.get('expectedSize', {}).get('min', '')
            max_size = prob_data.get('expectedSize', {}).get('max', '')

            problems.append({
                'type': prob_type,
                'elevations': elevations,
                'aspects': aspects,
                'likelihood': likelihood,
                'size_min': min_size,
                'size_max': max_size
            })

        return {
            'region': region,
            'date_issued': report.get('dateIssued', ''),
            'timezone': timezone,
            'forecasts': forecasts_by_date,
            'problems': problems,
            'url': data.get('url', '')
        }

    def _get_rating(self, elevation: str, ratings: Dict) -> str:
        """Return the rating string

        Normalizes by stripping out leading '# - ' such as in '2 - Moderate'.
        """
        rating = ratings.get(elevation , 'No Rating')
        # This will work as long as there's never a '-' in the actual term but
        # no "# - " prefixing the string.
        return rating.split('-', 1)[-1].strip()
