"""Avalanche report and request handling."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import pytz
import requests
from timezonefinder import TimezoneFinder

from .base import AvalancheProvider
from ..config import get_config


# Provider registry - imported in __init__.py
AVALANCHE_PROVIDERS = {}


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
