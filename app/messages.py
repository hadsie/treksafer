"""Parses inbound text, locates fires, and generates the responses."""

import logging
import re
from datetime import date, datetime
from typing import Dict, Optional

from .config import get_config
from .health import health_report
from .helpers import parse_message, get_aqi, local_time, quoted, commands
from .fires import FindFires, FireLookup
from .avalanche import AvalancheReport
from .messaging import FireMessages

# The message "health" (any case, surrounding whitespace allowed, nothing
# else) requests a health summary instead of a fire/avalanche report.
# Note: This will fail on most satellite messengers by default, as they
# can include a link and coordinates.
_HEALTH_PATTERN = re.compile(r'\s*health\s*', re.IGNORECASE)

# Help answers only when it is the whole message, so "help" inside a real
# request never hijacks it. Usage answers all requests when `usage` is
# the first word in the message.
_HELP_PATTERN = re.compile(r'\s*(help|info)\s*', re.IGNORECASE)
_USAGE_PATTERN = re.compile(r'\s*(usage|examples)\b', re.IGNORECASE)

class Messages(FireMessages):
    """Service copy and operator replies."""

    def no_gps(self) -> str:
        return 'No valid GPS coordinates found. Enable location on your device, or send coords as "(lat, long)" e.g. (50.5,-122.1), and check the values.'

    def system_error(self) -> str:
        return ('TrekSafer ERROR: Something went wrong and your request could not be '
                'processed. The failure has been logged and reported.')

    def help(self) -> str:
        """The HELP/INFO reply. Must match the help copy declared in the SMS transport
        messaging campaign registration verbatim."""
        return ('TrekSafer: Wildfire & avalanche info. Text GPS coordinates '
                '(e.g. fires (49.2, -123.1)) to get a report. '
                'Contact info@treksafer.com. Reply STOP to opt out.')

    def usage(self) -> str:
        """The USAGE/EXAMPLES reply: the advanced guide."""
        return ('Keyword: fire or avalanche w/ coords\n'
                'Filters: active|all|25km|10mi (max 150km)\n'
                'fireid K70597 - single fire\n'
                'Coords: (lat,lon) or map link')

    def opt_in_notice(self) -> str:
        """The one-time confirmation a number's first message triggers,
        sent ahead of the reply itself. Must match the opt-in copy declared
        in the SMS transport messaging campaign registration verbatim."""
        return ('Welcome to TrekSafer wildfire & avalanche reports. '
                'Message frequency varies. Msg&Data rates may apply. '
                'Reply HELP for help or STOP to opt out.')

    def opt_out_confirmed(self) -> str:
        """The one reply a STOP still receives. Must match the opt-out copy
        declared in the SignalWire campaign registration verbatim."""
        return ('TrekSafer: You are opted out and will receive no further '
                'messages. Reply START to opt back in.')

    def opt_in_confirmed(self) -> str:
        return ('TrekSafer: You are opted back in and will receive replies '
                'to your requests. Reply STOP to opt out.')

    def health(self, report: dict) -> str:
        """Human-readable health summary, compact enough for one SMS."""
        if report['status'] != 'ok':
            return f"TrekSafer health ERROR: {report['error']}"
        lines = ['TrekSafer OK. Data fetched (UTC):']
        for source, info in report['sources'].items():
            fetched = info['latest_fetch']
            stamp = self._timestamp(datetime.fromisoformat(fetched)) if fetched else 'never'
            lines.append(f'{source} {stamp}')
        return '\n'.join(lines)


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
        return aqi_message + responses.outside_of_area(coords)

    fires = findfires.nearby()
    # A source that produced no data at all must not read as "no fires".
    if not fires and findfires.unavailable_sources:
        return aqi_message + responses.data_unavailable()

    # When a realtime source failed and stored data was used instead, add
    # a freshness marker so old data is never presented as current. It goes
    # after the per-fire formatting, where SMS downsizing can't drop it.
    marker = ''
    if findfires.fallback_fetched:
        marker = "\n\n" + responses.data_age(
            local_time(findfires.fallback_fetched, coords))

    if not fires:
        distance = min(findfires.filters['distance'], settings.max_radius)
        status_filter = fire_filters.get('status')
        return aqi_message + responses.no_fires(distance, coords, status_filter) + marker

    fire_messages = [responses.fire(fire) for fire in fires]
    return aqi_message + "\n\n".join(fire_messages) + marker


def _handle_fire_lookup(coords: tuple[float, float] | None, terms: list[str],
                        responses: 'Messages') -> str:
    """Resolve a "fireid <id> [<id> ...]" lookup to a block per id.

    Each found fire's block is enriched with the perimeter extent, recent edge
    movement, and the time the served data was current. Ids that match nothing
    are collected into a single trailing not-found line naming the misses.
    """
    blocks = []
    misses = []
    for term in terms:
        lookup = FireLookup(term, coords)
        fire = lookup.result()
        if fire is None:
            misses.append(term)
            continue
        lines = [responses.fire(fire)]
        if lookup.perimeter:
            lines.append(responses.fire_perimeter(lookup.perimeter))
        if lookup.edge:
            lines.append(responses.fire_edge(lookup.edge))
        lines.append(responses.as_of(lookup.as_of))
        blocks.append("\n".join(lines))
    if misses:
        blocks.append(responses.fire_not_found(misses))
    return "\n\n".join(blocks)


def handle_avalanche_request(coords: tuple[float, float], avalanche_filters: Dict) -> str:
    """Handle avalanche forecast requests.

    Args:
        coords: Tuple of (latitude, longitude)
        avalanche_filters: Dict with 'forecast' key: 'current'|'today'|'tomorrow'|'all'

    Returns:
        str: Formatted avalanche forecast
    """
    avalanche = AvalancheReport(coords)

    if avalanche.out_of_range():
        return avalanche.outside_of_area_msg()

    forecast = avalanche.get_forecast(avalanche_filters)
    return forecast


def safe_handle_message(message: str) -> str:
    """Transport boundary for handle_message to ensure the user always gets a reply."""
    try:
        return handle_message(message)
    except Exception:
        logging.exception(f"handle_message crashed on message: {message!r}")
        return Messages().system_error()


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
    if _HEALTH_PATTERN.fullmatch(message):
        return responses.health(health_report())
    if _HELP_PATTERN.fullmatch(message):
        return responses.help()
    # "usage" as a bare keyword answers only at the start; the "!usage"
    # command is recognized anywhere in the message.
    if _USAGE_PATTERN.match(message) or 'usage' in commands(message):
        return responses.usage()

    parsed_data = parse_message(message)
    if not parsed_data:
        logging.warning('No GPS coords found in message:\n%s', quoted(message))
        return responses.no_gps()

    coords = parsed_data["coords"]
    fire_filters = parsed_data["fire_filters"]
    fire_ids = parsed_data.get("fire_ids", [])
    data_type = parsed_data.get("data_type", "auto")
    avalanche_filters = parsed_data.get("avalanche_filters", {})

    # An explicit "fireid" lookup outranks data-type routing: the user asked
    # about one or more specific fires.
    if fire_ids:
        return _handle_fire_lookup(coords, fire_ids, responses)

    # Auto-detect data type. During fire season, default straight to fire;
    # otherwise use avalanche when available, with out-of-season reports
    # falling back to fire.
    if data_type == "auto":
        data_type = "fire"
        if not in_fire_season():
            avalanche = AvalancheReport(coords)
            if avalanche.has_data() and not avalanche.out_of_season():
                data_type = "avalanche"

    logging.info("Message:\n%s", quoted(message))
    logging.info(f"Data type: {data_type}")

    # Route to appropriate handler
    if data_type == "avalanche":
        return handle_avalanche_request(coords, avalanche_filters)
    elif data_type == "fire":
        return handle_fire_request(coords, fire_filters)
    else:
        return f"Unknown data type: {data_type}"
