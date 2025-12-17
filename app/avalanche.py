"""Avalanche forecast integration with multi-provider support.

Usage:
    avalanche = AvalancheReport((49.25, -123.1))
    if avalanche.has_data():
        forecast = avalanche.get_forecast()
        print(format_avalanche_response(forecast))
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any

import pytz
import requests_cache
from pyproj import Transformer
from requests import RequestException
from shapely.geometry import Point
from timezonefinder import TimezoneFinder

from .config import get_config, AvalancheProviderConfig


class AvalancheProvider(ABC):
    """Base class for avalanche forecast providers."""

    def __init__(self, config: AvalancheProviderConfig):
        """Initialize provider with configuration.

        Args:
            config: Provider configuration from settings
        """
        self.config = config
        self.cache_timeout = config.cache_timeout
        self.forecast_cutoff_hour = config.forecast_cutoff_hour
        self.api_base = config.api_url

        self.session = requests_cache.CachedSession(
            cache_name=f'cache/avalanche_{self.__class__.__name__}',
            expire_after=timedelta(seconds=self.cache_timeout),
            allowable_methods=['GET'],
            stale_if_error=True
        )

    @abstractmethod
    def get_forecast(self, coords: tuple) -> Optional[Dict[str, Any]]:
        """Get avalanche forecast data for coordinates.

        Returns dict with all available forecast dates and timezone info.
        """
        pass

    @abstractmethod
    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside forecast coverage area.

        Returns:
            bool: True if coordinates are out of range, False otherwise.
        """
        pass

    @abstractmethod
    def distance_from_region(self, coords: tuple) -> Optional[float]:
        """Calculate distance from coordinates to nearest region.

        Returns:
            None: If exact match (point in region)
            float: Distance in km to nearest region
            float('inf'): If no region data available
        """
        pass

    def _request(self, url: str) -> requests_cache.Response:
        """Make cached HTTP request."""
        try:
            return self.session.get(url, timeout=30)
        except RequestException as e:
            logging.error(f"Avalanche API request failed: {e}")
            raise


class AvalancheCanadaProvider(AvalancheProvider):
    """Avalanche Canada API provider."""

    def __init__(self, config: AvalancheProviderConfig):
        super().__init__(config)
        self.subregions_gdf = self._load_subregions()

    def _load_subregions(self):
        """Load subregion shapefile once on initialization."""
        try:
            import geopandas as gpd
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
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x, y = transformer.transform(coords[1], coords[0])  # (lon, lat)
        point_meters = Point(x, y)

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
            url = f"{self.api_base}products/point?lat={coords[0]}&long={coords[1]}"
            response = self._request(url)

            if response.status_code == 200:
                return self._parse_forecast(response.json(), coords)

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
        for rating in report.get('dangerRatings', []):
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

# Provider registry
AVALANCHE_PROVIDERS = {
    'CA': AvalancheCanadaProvider,
}


class AvalancheReport:
    """Get avalanche forecast for a location."""

    def __init__(self, coords: tuple):
        self.coords = coords
        self.settings = get_config()
        self.provider = self._select_provider()

    def _select_provider(self) -> Optional[AvalancheProvider]:
        """Select provider based on location and configuration.

        Loops through all configured providers and selects the best match:
        - Returns first provider with exact match (point in region)
        - Otherwise returns closest provider within distance buffer
        - Returns None if all providers are out of range
        """
        if not self.settings.avalanche:
            logging.warning("No avalanche configuration found in settings")
            return None

        best_provider = None
        best_distance = float('inf')

        for provider_key, provider_config in self.settings.avalanche.providers.items():
            provider_class = AVALANCHE_PROVIDERS.get(provider_key)
            if not provider_class:
                continue

            # Instantiate provider
            provider = provider_class(provider_config)

            # Get distance to region
            distance = provider.distance_from_region(self.coords)

            # Exact match (distance is None) - use immediately
            if distance is None:
                return provider

            # Within radius and closer than current best
            if distance <= self.settings.avalanche_distance_buffer and distance < best_distance:
                best_provider = provider
                best_distance = distance

        return best_provider  # None if all out of range

    def out_of_range(self) -> bool:
        """Check if avalanche forecast is available for this location."""
        if not self.provider:
            return True
        return self.provider.out_of_range(self.coords)

    def has_data(self) -> bool:
        """Check if avalanche data is available for this location."""
        if not self.provider:
            return False

        try:
            forecast = self.provider.get_forecast(self.coords)
            return forecast is not None
        except requests.RequestException as e:
            logging.warning(f"Network error checking avalanche data: {e}")
            return False

    def get_forecast(self, avalanche_filters: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Get formatted avalanche forecast.

        Args:
            avalanche_filters: Dict with 'forecast' key: 'current'|'today'|'tomorrow'|'all'

        Returns:
            Formatted forecast string or None
        """
        if not self.provider:
            return "Avalanche forecasts not available for this location."

        # Fetch all forecast data
        forecast_data = self.provider.get_forecast(self.coords)

        if not forecast_data:
            return "No avalanche forecast available for this location."

        # Apply filters
        filters = avalanche_filters or {}
        forecast_filter = filters.get('forecast', 'current')

        return self._apply_filter(forecast_data, forecast_filter)

    def _apply_filter(self, forecast_data: Dict[str, Any], forecast_filter: str) -> str:
        """Apply forecast filter to select dates and format.

        Args:
            forecast_data: Full forecast data with all dates
            forecast_filter: 'current'|'today'|'tomorrow'|'all'

        Returns:
            Formatted forecast string
        """
        # Get timezone from API response
        tz = pytz.timezone(forecast_data['timezone'])
        current_time = datetime.now(tz)

        # Build list of dates to show
        if forecast_filter == 'current':
            # Use cutoff logic
            if current_time.hour >= self.provider.forecast_cutoff_hour:
                dates = [(current_time + timedelta(days=1)).date()]
            else:
                dates = [current_time.date()]

        elif forecast_filter == 'today':
            dates = [current_time.date()]

        elif forecast_filter == 'tomorrow':
            dates = [(current_time + timedelta(days=1)).date()]

        elif forecast_filter == 'all':
            # Convert all available forecast dates to date objects
            dates = [datetime.strptime(d, '%Y-%m-%d').date()
                     for d in sorted(forecast_data['forecasts'].keys())]

        else:
            # Unknown filter, default to current
            dates = [current_time.date()]

        return self._format_forecast(forecast_data, dates)

    def _format_problems(self, problems: list, indent: str = "  ") -> list:
        """Format avalanche problems."""
        if not problems:
            return []

        parts = ["Problems:"]
        for problem in problems:
            parts.append(f"{indent}• {problem['type']}")

            if problem.get('elevations'):
                parts.append(f"{indent}  Elevations: {', '.join(problem['elevations'])}")

            if problem.get('aspects'):
                parts.append(f"{indent}  Aspects: {', '.join(problem['aspects'])}")

            if problem.get('likelihood') and problem.get('size_min'):
                size_range = f"{problem['size_min']}-{problem['size_max']}"
                parts.append(f"{indent}  {problem['likelihood']}, Size {size_range}")

        return parts

    def _format_forecast(self, forecast_data: Dict[str, Any], dates: list) -> str:
        """Format forecast for any number of dates.

        Args:
            forecast_data: Full forecast data
            dates: List of date objects to include in forecast

        Returns:
            Formatted forecast string
        """
        parts = [f"Avalanche Forecast: {forecast_data['region']}"]

        # Header: show specific date for single, or "Issued" for multiple
        if len(dates) == 1:
            parts.append(f"Date: {dates[0].strftime('%Y-%m-%d')}")
        else:
            parts.append(f"Issued: {forecast_data['date_issued']}")

        parts.append("")

        # Format each date
        for date in dates:
            date_str = date.strftime('%Y-%m-%d')

            if date_str not in forecast_data['forecasts']:
                continue

            # For multiple dates, label each date
            if len(dates) > 1:
                parts.append(f"Date: {date_str}")

            parts.append("Danger Ratings:")
            # Indent more for multi-date to distinguish dates
            indent = "  " if len(dates) == 1 else "    "
            day_forecast = forecast_data['forecasts'][date_str]
            ratings = [
                f"{indent}Alpine: {day_forecast['alpine_rating']}",
                f"{indent}Treeline: {day_forecast['treeline_rating']}",
                f"{indent}Below Treeline: {day_forecast['below_treeline_rating']}"
            ]
            parts.extend(ratings)
            parts.append("")

        # Problems shown once at end
        if forecast_data.get('problems'):
            parts.extend(self._format_problems(forecast_data['problems']))

        return "\n".join(parts)

    def outside_of_area_msg(self):
        return 'TrekSafer ERROR: GPS coordinates outside of supported avalanche forecast area. No data available.'
