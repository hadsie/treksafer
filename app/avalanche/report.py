"""Avalanche report and request handling."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import pytz
from requests import RequestException

from .base import AvalancheProvider
from ..config import get_config
from ..messaging.avalanche import AvalancheMessages


def _get_provider_class(class_name: str):
    """Dynamically get avalanche provider class by name.

    Args:
        class_name: Name of the provider class (e.g., 'AvalancheCanadaProvider')

    Returns:
        The provider class

    Raises:
        ValueError: If the class name is not found
    """
    # Import here to avoid circular dependency
    from .avcan import AvalancheCanadaProvider
    from .quebec import AvalancheQuebecProvider
    from .us_nac import NationalAvalancheProvider

    providers = {
        'AvalancheCanadaProvider': AvalancheCanadaProvider,
        'AvalancheQuebecProvider': AvalancheQuebecProvider,
        'NationalAvalancheProvider': NationalAvalancheProvider,
    }

    provider_class = providers.get(class_name)
    if not provider_class:
        raise ValueError(f"Unknown avalanche provider class: {class_name}")
    return provider_class


class AvalancheReport(AvalancheMessages):
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
            try:
                # Get provider class dynamically from config
                provider_class = _get_provider_class(provider_config.class_name)
            except ValueError as e:
                logging.warning(f"Skipping provider {provider_key}: {e}")
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
        except RequestException as e:
            logging.warning(f"Network error checking avalanche data: {e}")
            return False

    def out_of_season(self) -> bool:
        """Check if the location's forecast is an out-of-season report.

        Kept separate from has_data() so auto-detection can fall back to fire
        while an explicit avalanche request still returns the report.
        """
        if not self.provider:
            return False

        try:
            forecast = self.provider.get_forecast(self.coords)
        except RequestException as e:
            logging.warning(f"Network error checking avalanche season: {e}")
            return False

        return bool(forecast) and self.provider.is_out_of_season(forecast)

    def get_forecast(self, avalanche_filters: Optional[Dict] = None, format: str = 'abbrev') -> Optional[str]:
        """Get formatted avalanche forecast.

        Args:
            avalanche_filters: Dict with 'forecast' key: 'current'|'tomorrow'|'all'
            format: 'full' or 'abbrev' for formatting style

        Returns:
            Formatted forecast string or None
        """
        if not self.provider:
            return self.no_provider_msg()

        # Fetch all forecast data
        forecast_data = self.provider.get_forecast(self.coords)

        if not forecast_data:
            return self.no_forecast_msg()

        # Apply filters
        filters = avalanche_filters or {}
        forecast_filter = filters.get('forecast', 'all')

        # Get filtered dates
        try:
            dates = self._apply_filter(forecast_data, forecast_filter)
        except ValueError:
            return self.broken_forecast_msg('date')

        # Format based on requested style
        if format == 'abbrev':
            return self._format_forecast_abbrev(forecast_data, dates)
        else:
            return self._format_forecast_full(forecast_data, dates)

    def _apply_filter(self, forecast_data: Dict[str, Any], forecast_filter: str) -> list:
        """Apply forecast filter to select day names.

        Args:
            forecast_data: Full forecast data with all days
            forecast_filter: 'current'|'tomorrow'|'all'

        Returns:
            List of day name strings to include in forecast

        Raises:
            ValueError: If no forecast days available
        """
        forecasts = forecast_data['forecasts']
        days = list(forecasts.keys())  # ["Friday", "Saturday", "Sunday"]

        if not days:
            raise ValueError("No forecast dates available")

        if forecast_filter == 'current':
            return [days[0]]

        elif forecast_filter == 'tomorrow':
            # Get tomorrow's day name
            tz = pytz.timezone(forecast_data['timezone'])
            tomorrow = (datetime.now(tz) + timedelta(days=1)).strftime('%A')

            if tomorrow in days:
                return [tomorrow]
            else:
                logging.warning(f"Tomorrow's forecast not available, using first available")
                return [days[0]]

        # 'all' - return all days
        return days
