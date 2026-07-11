"""Base class for avalanche forecast providers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Callable

import geopandas as gpd
import requests_cache
from requests import RequestException
from shapely.geometry import Point

from ..config import AvalancheProviderConfig, get_config
from ..helpers import local_crs


class AvalancheProvider(ABC):
    """Base class for avalanche forecast providers."""

    def __init__(self, config: AvalancheProviderConfig):
        """Initialize provider with configuration.

        Args:
            config: Provider configuration from settings
        """
        self.config = config
        self.cache_timeout = config.cache_timeout
        self.api_base = config.api_url
        self.regions_gdf = None

        # Ensure cache directory exists
        cache_dir = Path('cache')
        cache_dir.mkdir(exist_ok=True)

        self.session = requests_cache.CachedSession(
            cache_name=str(cache_dir / f'avalanche_{self.__class__.__name__}'),
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

    def is_out_of_season(self, forecast: Dict[str, Any]) -> bool:
        """Check whether a returned forecast is an out-of-season report.

        Out-of-season markers (e.g. "Spring Conditions") are configured per
        provider. A forecast counts as out of season only when every band
        rating across all days is one of those markers; providers with no
        configured markers are never out of season.
        """
        markers = set(self.config.out_of_season)
        if not markers:
            return False

        ratings = [
            rating
            for day in forecast.get('forecasts', {}).values()
            for rating in (day['alpine_rating'], day['treeline_rating'], day['below_treeline_rating'])
        ]
        return bool(ratings) and all(rating in markers for rating in ratings)

    def distance_from_region(self, coords: tuple) -> Optional[float]:
        """Calculate distance from coordinates to nearest region.

        Returns:
            None: If exact match (point in region)
            float: Distance in km to nearest region
            float('inf'): If no region data available
        """
        if self.regions_gdf is None:
            return float('inf')

        point_wgs84 = Point(coords[1], coords[0])

        # Check for exact match
        if self.regions_gdf.contains(point_wgs84).any():
            return None

        # Calculate distance to nearest region
        gdf_with_distances = self._calculate_distances(coords)
        if gdf_with_distances is None:
            return float('inf')

        # Get nearest distance
        nearest_distance_m = gdf_with_distances['distance'].min()
        nearest_distance_km = nearest_distance_m / 1000

        # Apply buffer limit
        settings = get_config()
        if nearest_distance_km > settings.avalanche_distance_buffer:
            return float('inf')

        return nearest_distance_km

    def _load_geodata(self, loader_fn: Callable) -> Optional[gpd.GeoDataFrame]:
        """Load GeoDataFrame with consistent error handling.

        Args:
            loader_fn: Callable that loads and returns a GeoDataFrame

        Returns:
            GeoDataFrame or None if loading failed
        """
        try:
            return loader_fn()
        except FileNotFoundError as e:
            logging.warning(f"Geospatial data file not found: {e}")
            return None
        except ImportError as e:
            logging.warning(f"geopandas not available for geospatial lookup: {e}")
            return None

    def _calculate_distances(self, coords: tuple) -> Optional[gpd.GeoDataFrame]:
        """Calculate distances from coordinates to all regions.

        Args:
            coords: (latitude, longitude) in WGS84

        Returns:
            GeoDataFrame with 'distance' column (in meters), or None if no data
        """
        if self.regions_gdf is None:
            return None

        # Project regions into a user-centered CRS where distances from the
        # origin (the user) are true.
        gdf_meters = self.regions_gdf.to_crs(local_crs(coords))
        gdf_meters['distance'] = gdf_meters.geometry.distance(Point(0, 0))

        return gdf_meters

    def _request(self, url: str) -> requests_cache.Response:
        """Make cached HTTP request."""
        try:
            # @todo: Make timeout configurable via settings
            return self.session.get(url, timeout=30)
        except RequestException as e:
            logging.error(f"Avalanche API request failed: {e}")
            raise
