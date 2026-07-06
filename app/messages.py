"""Parses inbound text, locates fires, and generates the responses."""

import logging
from datetime import date
from typing import Dict, Any, Optional

from .config import get_config
from .helpers import parse_message, get_aqi
from .fires import FindFires
from .avalanche import AvalancheReport
from .filters import STATUS_LEVELS

_SMS_LIMIT = 160

class Messages:
    def no_gps(self) -> str:
        return 'TrekSafer ERROR: No valid GPS coordinates found. Enable location on your device, or send coords as "(lat, long)" e.g. (50.5,-122.1), and check the values.'

    def outside_of_area(self) -> str:
        return 'TrekSafer ERROR: GPS coordinates outside of supported fire perimeter area. No data available.'

    def no_fires(self, distance: float, status_filter: str = None) -> str:
        """Generate no fires message with optional status filter context.

        Args:
            distance: Search radius in km
            status_filter: Status filter applied ('active', 'managed', 'controlled', 'out', 'all', or None)

        Returns:
            Formatted message string
        """
        base_msg = f'No fires reported within {distance}km of your location.'

        # If no filter or 'all'/'out', return simple message
        if not status_filter or status_filter in ('all', 'out'):
            return base_msg

        # Get all statuses included in this filter level
        filter_level = STATUS_LEVELS.get(status_filter)
        if not filter_level:
            return base_msg

        # Include all statuses at or below this level
        included_statuses = [
            status for status, level in STATUS_LEVELS.items()
            if level <= filter_level
        ]
        included_statuses.sort(key=lambda s: STATUS_LEVELS[s])  # Sort by level

        # Format message with included statuses
        status_list = ', '.join(included_statuses)
        return f'No fires reported within {distance}km of your location. (Showing: {status_list})'

    def fires(self, fires: list[Dict]) -> list[str]:
        messages = []
        for fire in fires:
            messages.append(self.fire(fire))
        return messages

    def fire(self, fire: Dict, size: str = "full") -> str:
        message = self._fire(fire, size)
        return message

    def _fire(self, fire, size = "full"):
        """
        Format the message for this specific fire.

        :param dict fire: The fire data dictionary.
        :param str size: The message size, one of full, medium, short.
        :return: The formatted message.
        :rtype: str
        """
        level_fields = {
            "full": [
                ("FullName", "Fire: {}"),
                ("Location", "Location: {}"),
                ("DistDir", "{}"),
                ("Size", "Size: {} ha"),
                ("Status", "Status: {}")
            ],
            "medium": [
                ("FullName", "Fire: {}"),
                ("DistDir", "{}"),
                ("Size", "Size: {} ha")
            ],
            "short": [
                ("Fire", "{}"),
                ("DistDir", "{}"),
                ("Size", "{}ha")
            ]
        }
        fields = level_fields[size]

        # Strip all strings
        fire = {k:str(v).strip() for k,v in fire.items()}

        fire['FullName'] = fire['Fire']
        if 'Name' in fire and fire['Name'] != fire['Fire']:
            if size == "full":
                fire['FullName'] = f"{fire['Name']} ({fire['Fire']})"
            elif size == "medium":
                fire['FullName'] = f"{fire['Name']} {fire['Fire']}"

        distance = self._format_distance(fire['Distance'])
        fire['DistDir'] = f"{distance}km {fire['Direction']}"
        fire['Size'] = round(float(fire['Size']))
        message = []

        for key, template in fields:
            value = fire.get(key)
            if not value:
                continue
            message.append(template.format(value))

        message = "\n".join(message)
        msg_length = self._message_length(message)
        if msg_length > _SMS_LIMIT and size != "short":
            new_size = "medium" if size == "full" else "short"
            message = self._fire(fire, new_size)

        return message

    @staticmethod
    def _message_length(message: str) -> float:
        """Computes the byte length of a string including emojis."""
        return len(message.encode(encoding='utf_16_le'))/2

    @staticmethod
    def _format_distance(meters: float) -> int | float:
        """Return a nicely formatted distance string in km.

        Rules
        -----
        1. < 10 km  → round to 1 decimal place
        2. ≥ 10 km  → round to nearest whole km
        3. Never show a trailing .0
        """
        km = float(meters)/1000
        if km < 10:
            # scale up, round to nearest integer, scale back
            # otherwise we have inconsistent rounding on round(x.95, 1)
            # See round(7.95, 1) vs round(8.95, 1)
            km_rounded = round(km * 10) / 10
        else:
            km_rounded = round(km)

        # Strip the trailing “.0” if the number is an integer
        return int(km_rounded) if km_rounded == int(km_rounded) else km_rounded

def handle_fire_request(coords: tuple[float, float], fire_filters: Dict) -> str:
    """Handle fire information requests.

    Args:
        coords: Tuple of (latitude, longitude)
        fire_filters: Dictionary of fire-specific filters

    Returns:
        str: Formatted fire report with AQI
    """
    responses = Messages()

    # Get AQI if configured
    aqi_message = ""
    settings = get_config()
    if settings.include_aqi:
        aqi = get_aqi(coords)
        aqi_message = f"AQI: {aqi}\n\n" if aqi else ''

    # Find fires
    findfires = FindFires(coords, fire_filters)
    if findfires.out_of_range():
        return aqi_message + responses.outside_of_area()

    fires = findfires.nearby()
    if not fires:
        distance = min(findfires.filters['distance'], settings.max_radius)
        status_filter = fire_filters.get('status')
        return aqi_message + responses.no_fires(distance, status_filter)

    # Format response
    fire_messages = []
    for fire in fires:
        fire_messages.append(responses.fire(fire))
    return aqi_message + "\n\n".join(fire_messages)


def handle_avalanche_request(coords: tuple[float, float], avalanche_filters: Dict) -> str:
    """Handle avalanche forecast requests.

    Args:
        coords: Tuple of (latitude, longitude)
        avalanche_filters: Dict with 'forecast' key: 'current'|'today'|'tomorrow'|'all'

    Returns:
        str: Formatted avalanche forecast
    """
    responses = Messages()
    avalanche = AvalancheReport(coords)

    if avalanche.out_of_range():
        return avalanche.outside_of_area_msg()

    forecast = avalanche.get_forecast(avalanche_filters)
    return forecast


def in_fire_season(today: Optional[date] = None) -> bool:
    """Check whether a date falls within the configured fire season window.

    The window is defined by the fire_season_start/fire_season_end settings
    (MM-DD, inclusive) and may wrap the year boundary.

    :param date today: Date to check, defaults to the current date
    :return: True if the date is within the fire season window
    :rtype: bool
    """
    settings = get_config()
    today = today or date.today()
    start = tuple(map(int, settings.fire_season_start.split("-")))
    end = tuple(map(int, settings.fire_season_end.split("-")))
    month_day = (today.month, today.day)
    if start <= end:
        return start <= month_day <= end
    return month_day >= start or month_day <= end


def handle_message(message: str) -> str:
    """Route message to appropriate data handler.

    This function parses the incoming message to extract GPS coordinates,
    determines the data type (fire/avalanche), and routes to the appropriate
    handler function.

    :param str message: The inbound message containing location information
    :return: Formatted response message(s) or error messages
    :rtype: str
    """
    responses = Messages()
    parsed_data = parse_message(message)
    if not parsed_data:
        logging.warning('No GPS coords found in message.')
        logging.warning(message)
        return responses.no_gps()

    coords = parsed_data["coords"]
    fire_filters = parsed_data["fire_filters"]
    data_type = parsed_data.get("data_type", "auto")
    avalanche_filters = parsed_data.get("avalanche_filters", {})

    # Auto-detect data type. During fire season, default straight to fire;
    # otherwise use avalanche when available, with out-of-season reports
    # falling back to fire.
    if data_type == "auto":
        data_type = "fire"
        if not in_fire_season():
            avalanche = AvalancheReport(coords)
            if avalanche.has_data() and not avalanche.out_of_season():
                data_type = "avalanche"

    logging.info(f"Message: {message}")
    logging.info(f"Data type: {data_type}")

    # Route to appropriate handler
    if data_type == "avalanche":
        return handle_avalanche_request(coords, avalanche_filters)
    elif data_type == "fire":
        return handle_fire_request(coords, fire_filters)
    else:
        return f"Unknown data type: {data_type}"
