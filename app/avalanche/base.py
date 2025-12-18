"""Base class for avalanche forecast providers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from pathlib import Path
from typing import Optional, Dict, Any

import requests_cache
from requests import RequestException

from ..config import AvalancheProviderConfig


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
            # @todo: Make timeout configurable via settings
            return self.session.get(url, timeout=30)
        except RequestException as e:
            logging.error(f"Avalanche API request failed: {e}")
            raise
