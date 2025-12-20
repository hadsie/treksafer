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
        self.subregions_gdf = self._load_subregions()

    def _load_subregions(self):
        """Load subregion shapefile once on initialization."""
        try:
            # https://github.com/avalanche-canada/forecast-polygons/blob/main/canadian_subregions.shp.zip
            return gpd.read_file('boundaries/canadian_subregions.shp.zip')
        except FileNotFoundError as e:
            logging.warning(f"Avalanche subregion shapefile not found: {e}")
            return None
        except ImportError as e:
            logging.warning(f"geopandas not available for subregion lookup: {e}")
            return None

    def _get_subregion(self, coords: tuple) -> Optional[str]:
        """Get avalanche subregion name from coordinates.

        First checks for exact match, then nearest region within configured radius.

        Args:
            coords: (latitude, longitude)

        Returns:
            Subregion name or None if not found
        """
        if self.subregions_gdf is None:
            return None

        point_wgs84 = Point(coords[1], coords[0])  # lon, lat

        # Check for exact match first
        matches = self.subregions_gdf[self.subregions_gdf.contains(point_wgs84)]
        if not matches.empty:
            return matches.iloc[0]['polygon_na']

        # No exact match - find closest within radius
        settings = get_config()
        return self._find_closest_subregion(coords, settings.avalanche_distance_buffer)

    def _calculate_distances(self, coords: tuple):
        """Calculate distances from coordinates to all subregions.

        Args:
            coords: (latitude, longitude) in WGS84

        Returns:
            GeoDataFrame with 'distance' column (in meters), or None if no shapefile
        """
        if self.subregions_gdf is None:
            return None

        # Convert coordinates to EPSG:3857 (meters)
        point_meters = coords_to_point_meters(coords)

        # Convert polygons to EPSG:3857 and calculate distances
        gdf_meters = self.subregions_gdf.to_crs(epsg=3857)
        gdf_meters['distance'] = gdf_meters.geometry.distance(point_meters)

        return gdf_meters

    def _find_closest_subregion(self, coords: tuple, limit_km: int) -> Optional[str]:
        """Find closest subregion within distance limit.

        Args:
            coords: (latitude, longitude) in WGS84
            limit_km: Maximum distance in kilometers

        Returns:
            Subregion name or None if none within limit
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

    def distance_from_region(self, coords: tuple) -> Optional[float]:
        """Calculate distance from coordinates to nearest Canadian subregion."""
        if self.subregions_gdf is None:
            return float('inf')  # No shapefile = infinite distance

        point_wgs84 = Point(coords[1], coords[0])

        # Check for exact match first
        matches = self.subregions_gdf[self.subregions_gdf.contains(point_wgs84)]
        if not matches.empty:
            return None  # Exact match

        # Calculate distances using helper
        gdf_with_distances = self._calculate_distances(coords)
        if gdf_with_distances is None:
            return float('inf')

        # Get nearest distance
        nearest_distance_m = gdf_with_distances['distance'].min()
        nearest_distance_km = nearest_distance_m / 1000

        # Apply same limit as _find_closest_subregion
        settings = get_config()
        if nearest_distance_km > settings.avalanche_distance_buffer:
            return float('inf')  # Beyond buffer limit

        return nearest_distance_km

    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside Canadian avalanche forecast area."""
        return self._get_subregion(coords) is None

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
            logging.warning(f"Network error checking avalanche data: {e}")

        return None

    def _parse_forecast(self, data: Dict[str, Any], coords: tuple) -> Optional[Dict[str, Any]]:
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
        region = self._get_subregion(coords) or report.get('title', 'Unknown')

        # Parse all available danger ratings
        forecasts_by_date = {}
        danger_ratings = report.get('dangerRatings', [])

        if not danger_ratings:
            logging.warning(f"Avalanche Canada API returned empty danger ratings for coords {coords}")

        for rating in danger_ratings:
            dt = datetime.strptime(rating['date']['value'], '%Y-%m-%dT%H:%M:%SZ')
            date_str = dt.strftime('%Y-%m-%d')

            # Extract ratings by elevation band
            ratings = {}
            for key, value in rating.get('ratings', {}).items():
                if key in ('alp', 'tln', 'btl'):
                    ratings[key] = value.get('rating', {}).get('display', 'No Rating')
                else:
                    logging.warning(f"Invalid avalanche band found in API response: {key}")

            forecasts_by_date[date_str] = {
                'alpine_rating': ratings.get('alp', 'No Rating'),
                'treeline_rating': ratings.get('tln', 'No Rating'),
                'below_treeline_rating': ratings.get('btl', 'No Rating'),
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
            'problems': problems
        }
