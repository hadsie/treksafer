import logging
import math
import pytz
import re
import requests
import requests_cache

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from pyproj import CRS
from timezonefinder import TimezoneFinder
from urllib.parse import urlparse, parse_qs, unquote_plus

from .config import get_config

_LAT = r'-?\d{1,2}(?:\.\d+)?'   # up to ±90
_LON = r'-?\d{1,3}(?:\.\d+)?'   # up to ±180

_DEG_HEMI_PATTERNS = [
    # 1) "50.58225° N, 122.09114° W"
    re.compile(
        r'(?P<lat>-?\d{1,2}(?:\.\d{1,8})?)\s*[°º]?\s*(?P<lat_dir>[NS])\s*[,;]?\s*'
        r'(?P<lon>-?\d{1,3}(?:\.\d{1,8})?)\s*[°º]?\s*(?P<lon_dir>[EW])',
        re.IGNORECASE | re.UNICODE
    ),
    # 2) "N 50.58225°, W 122.09114°"
    re.compile(
        r'(?P<lat_dir>[NS])\s*(?P<lat>-?\d{1,2}(?:\.\d{1,8})?)\s*[°º]?\s*[,;]?\s*'
        r'(?P<lon_dir>[EW])\s*(?P<lon>-?\d{1,3}(?:\.\d{1,8})?)\s*[°º]?',
        re.IGNORECASE | re.UNICODE
    ),
]

def acres_to_hectares(acres):
    return round(float(acres)/2.4710538147, 2)

def epoch_ms_to_datetime(ms):
    """Convert an epoch-milliseconds timestamp to an aware UTC datetime.

    Returns None for missing values (None or NaN).
    """
    if ms is None or math.isnan(float(ms)):
        return None
    return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc)

def local_crs(coords) -> CRS:
    """An azimuthal equidistant CRS centered on coords (lat, lon).

    Distances and bearings measured from the center point, which projects
    to (0, 0), are true.
    """
    return CRS.from_proj4(
        f"+proj=aeqd +lat_0={coords[0]} +lon_0={coords[1]} +datum=WGS84 +units=m +no_defs")


@lru_cache(maxsize=1)
def _timezone_finder() -> TimezoneFinder:
    """Building a TimezoneFinder is slow; build it once and reuse it."""
    return TimezoneFinder()


def local_time(dt: datetime, coords) -> datetime:
    """Convert an aware datetime to the local timezone at coords (lat, lon).

    Returns the datetime unchanged when no timezone covers the point
    (open ocean).
    """
    tz_name = _timezone_finder().timezone_at(lat=coords[0], lng=coords[1])
    return dt.astimezone(pytz.timezone(tz_name)) if tz_name else dt


def compass_direction(pointA, pointB):
    """
    Calculates the compass direction from pointA to pointB.

    Points must be in a projection whose planar bearings are true at
    pointA (e.g. an azimuthal equidistant projection centered on it; see
    local_crs).

    :param Point pointA: The position of the requester.
    :param Point pointB: The closest point on the fire perimeter.
    :return: The compass direction to the fire perimeter.
    :rtype: str
    """
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW",
                  "SW", "WSW", "W", "WNW" ,"NW" ,"NNW", "N"]
    bearing = math.degrees(math.atan2(pointB.x - pointA.x, pointB.y - pointA.y)) % 360
    return directions[round(bearing/22.5)]

@lru_cache(maxsize=1)
def _aqi_session():
    """Cached HTTP session for AQI lookups (the data is hourly)."""
    Path('cache').mkdir(exist_ok=True)
    return requests_cache.CachedSession(
        cache_name='cache/aqi',
        expire_after=timedelta(hours=1),
        allowable_methods=['GET'],
        stale_if_error=True,
    )


def get_aqi(coords):
    """
    Fetch the current US Air Quality Index (AQI) for given coordinates.

    Makes a request to the Open-Meteo Air Quality API to grab the most
    recent AQI reading.

    Args:
        coords (tuple): A tuple containing (latitude, longitude) as floats.

    Returns:
        int or None: The current US Air Quality Index value, or None if unavailable.
    """
    try:
        url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={coords[0]}&longitude={coords[1]}"
            "&hourly=us_aqi&timezone=America%2FLos_Angeles&forecast_days=1"
        )

        resp = _aqi_session().get(url, timeout=10)
        resp.raise_for_status()  # Raise for 4xx/5xx errors
        data = resp.json()

        # Get the current "hour" in the same timezone as the JSON data is giving us.
        api_timezone = data["timezone"]
        current_time = datetime.now(pytz.timezone(api_timezone))
        current_hour = current_time.strftime('%Y-%m-%dT%H:00')

        # Find the index of current time in the hourly time array, and match that to the AQI array.
        current_index = data["hourly"]["time"].index(current_hour)

        # Get latest AQI
        return data["hourly"]["us_aqi"][current_index]

    except requests.RequestException as e:
        logging.warning(f"Failed to fetch AQI data: network error - {e}")
        return None
    except (KeyError, ValueError, IndexError) as e:
        logging.warning(f"Failed to parse AQI data: {e}")
        return None

_FIRE_KEYWORD_RE = re.compile(r'\bfires?\b[\s:.,-]*', re.IGNORECASE)


def _fire_query(message: str) -> str | None:
    """Extract a fire identifier or name from a "fire <id-or-name>" message.

    Returns the text following the fire keyword with coordinates, device
    links, and recognized filter words stripped out, or None when nothing
    usable remains (e.g. a plain "fire" request carrying only coordinates).

    Fire identifiers never contain a decimal point or degree mark, so only
    those coordinate forms are removed; bare integers and hyphens are kept
    so identifiers like HWF-096-2026 and QC-2026-001 survive intact.
    """
    match = _FIRE_KEYWORD_RE.search(message)
    if not match:
        return None
    term = message[match.end():]
    term = re.sub(r'https?://\S+', ' ', term)
    term = re.sub(r'(?:www\.)?(?:inreachlink\.com|sms2zoleo\.com)/\S+', ' ', term,
                  flags=re.IGNORECASE)
    term = re.sub(r'[-+]?\d{1,3}\.\d+\s*[°º]?\s*[NSEW]?\b', ' ', term,  # decimal coords
                  flags=re.IGNORECASE)
    term = re.sub(r'[°º]', ' ', term)
    term = re.sub(r'\b\d+\s*(?:km|mi)\b', ' ', term, flags=re.IGNORECASE)  # distance filter
    term = re.sub(r'\b(?:active|all|current|tomorrow|avalanches?)\b',
                  ' ', term, flags=re.IGNORECASE)            # filter keywords
    term = re.sub(r'\s+', ' ', term).strip(' \t\n\r,;:.?!()[]')
    # A term with no letters or digits (stray punctuation like "Fires?") is
    # not an identifier.
    return term if re.search(r'[A-Za-z0-9]', term) else None


def parse_message(message):
    """Parse an SMS message for lat/long coordinates and optional filters.

    Supports:
        - Various coordinate formats, see coords_from_message().
        - A specific fire lookup: "fire <id-or-name>" (see _fire_query()).
        - Filter keywords: "active", "all"
        - Distance filters: "25km", "10mi"
        - Data type keywords: "avalanche", "fire"
        - Forecast time keywords: "current", "tomorrow", "all"

    Returns:
        dict: {
            "coords": (lat, lon) or None,
            "fire_id": str or None,
            "fire_filters": dict,
            "data_type": str,
            "avalanche_filters": dict
        }
        or None if neither coordinates nor a fire lookup were found
    """

    # Extract filters from message (case insensitive, using word boundaries)
    settings = get_config()
    filters = {}
    message_lower = message.lower()

    # Status filter
    if re.search(r'\bactive\b', message_lower):
        filters['status'] = 'active'
    elif re.search(r'\ball\b', message_lower):
        filters['status'] = 'all'

    # Distance filter (support km and mi) - ensure it's standalone
    distance_match = re.search(r'(?:^|\s)(\d+)\s*(km|mi)(?=\s|$)', message_lower)
    if distance_match:
        value, unit = distance_match.groups()
        # Convert to km if needed
        km_value = float(value) if unit == 'km' else float(value) * 1.609344
        filters['distance'] = km_value
    else:
        filters['distance'] = settings.fire_radius

    # Data type detection (left-side word boundary only to match plurals)
    data_type = "auto"
    if re.search(r'\bavalanche', message_lower):
        data_type = "avalanche"
    elif re.search(r'\bfire', message_lower):
        data_type = "fire"

    # Avalanche forecast filters (similar to fire status filters)
    avalanche_filters = {}
    if re.search(r'\bcurrent\b', message_lower):
        avalanche_filters['forecast'] = 'current'
    elif re.search(r'\btomorrow\b', message_lower):
        avalanche_filters['forecast'] = 'tomorrow'
    elif re.search(r'\ball\b', message_lower):
        avalanche_filters['forecast'] = 'all'

    coords = coords_from_message(message)
    fire_id = _fire_query(message)

    if not coords and not fire_id:
        return None

    return {
        "coords": coords,
        "fire_id": fire_id,
        "fire_filters": filters,
        "data_type": data_type,
        "avalanche_filters": avalanche_filters
    }

def _expand_short_link(url):
    """Resolve a shortened map link to its final URL.

    Chains can be multi-hop (ZOLEO hops through an intermediate shortener
    before landing on a Google Maps URL), so redirects are followed to the
    end and the final URL returned.
    """
    try:
        resp = requests.get(url, timeout=10)
        return resp.url
    except requests.RequestException as e:
        logging.warning(f"Failed to expand short map link {url}: {e}")
        return None


_DEVICE_LINK_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(inreachlink\.com|sms2zoleo\.com)/([\w-]+)', re.IGNORECASE)


def _coords_from_device_link(message):
    """Resolve a satellite messenger's own share link (inReach, ZOLEO) to
    the device's send location.

    inReach share pages embed the location as JSON; ZOLEO links redirect
    (via an intermediate shortener) to a Google Maps URL. Network failures
    and pages without coordinates return None.
    """
    m = _DEVICE_LINK_RE.search(message)
    if not m:
        return None
    domain, code = m.group(1).lower(), m.group(2)
    url = f'https://{domain}/{code}'

    if domain == 'sms2zoleo.com':
        expanded = _expand_short_link(url)
        if expanded:
            parsed = urlparse(expanded)
            if 'google.' in parsed.netloc and '/maps' in parsed.path:
                return _coords_from_google(parsed) or None
        logging.warning(f"ZOLEO link {url} resolved without usable coordinates")
        return None

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logging.warning(f"Failed to resolve inReach link {url}: {e}")
        return None
    lat_m = re.search(r'"Latitude"\s*:\s*(-?\d+\.\d+)', resp.text)
    lon_m = re.search(r'"Longitude"\s*:\s*(-?\d+\.\d+)', resp.text)
    if lat_m and lon_m:
        lat, lon = float(lat_m.group(1)), float(lon_m.group(1))
        if _valid_coords(lat, lon):
            return lat, lon
    logging.warning(f"inReach link {url} resolved without usable coordinates")
    return None


def coords_from_message(message: str) -> tuple[float, float]|None:
    """Extract latitude, longitude coordinates from a plain text message.

    Supports:
        - Plain decimal degrees: (49.123, -123.456)
        - Apple Maps links
        - Google Maps links
        - Degrees with hemisphere letters: 50.58225° N, 122.09114° W

    Every format is matched across the whole message and the earliest valid
    match wins, so coordinates a user typed take precedence over the
    device location a satellite messenger appends at the end.

    Returns:
      tuple of (lat, long) coordinates, or None if no valid patterns were found.
    """
    candidates = []  # (position in message, lat, lon)

    # Google or Apple map shares.
    for m in re.finditer(r'https?://\S+', message):
        parsed = urlparse(m.group())
        coords = False
        # Short share domains redirect to a full map URL.
        host = parsed.netloc.removeprefix('www.')
        if (host in ('maps.apple', 'maps.app.goo.gl')
                or (host == 'goo.gl' and parsed.path.startswith('/maps'))):
            expanded = _expand_short_link(m.group())
            if expanded:
                parsed = urlparse(expanded)
        if 'maps.apple.com' in parsed.netloc:
            coords = _coords_from_apple(parsed)
        elif any(domain in parsed.netloc for domain in ('google.', 'goo.gl')) and '/maps' in parsed.path:
            coords = _coords_from_google(parsed)
        if coords:
            candidates.append((m.start(), *coords))

    # Degree + hemisphere letters.
    for pattern in _DEG_HEMI_PATTERNS:
        for m in pattern.finditer(message):
            lat = _apply_hemisphere(float(m.group('lat')), m.group('lat_dir'), for_lat=True)
            lon = _apply_hemisphere(float(m.group('lon')), m.group('lon_dir'), for_lat=False)
            if _valid_coords(lat, lon):
                candidates.append((m.start(), lat, lon))

    # Plain decimal pairs. Coordinates must include a decimal point: every
    # satellite messenger and map share emits decimals, so requiring them
    # avoids matching incidental integers (e.g. "party of 2, ...") as
    # coordinates. The lookbehind/lookahead stop a pair from matching a
    # fragment of a longer number (e.g. "122.09" must not yield "09") and
    # keep leading signs intact.
    lat_coord = r'[-+]?\d{1,2}\.\d+'
    long_coord = r'[-+]?\d{1,3}\.\d+'
    pair_re = r'(?<![\d.])(%s)\s*,\s*(%s)(?!\.?\d)' % (lat_coord, long_coord)
    for m in re.finditer(pair_re, message):
        lat, lon = float(m.group(1)), float(m.group(2))
        if _valid_coords(lat, lon):
            candidates.append((m.start(), lat, lon))

    if candidates:
        _, lat, lon = min(candidates, key=lambda c: c[0])
        return lat, lon

    # Last resort: a message carrying only a satellite messenger's share
    # link (inReach, ZOLEO) resolves to the device's send location. These
    # links are appended automatically, so anything else wins first.
    return _coords_from_device_link(message)

def _apply_hemisphere(value: float, hemi: str, for_lat: bool) -> float:
    """Apply hemisphere direction to coordinate value.

    Hemisphere letter takes precedence over sign (e.g., "-50 N" becomes +50).
    Uses absolute value first to strip any existing sign, then applies direction.
    """
    v = abs(value)
    hemi = hemi.upper()
    if for_lat:
        return v if hemi == 'N' else -v
    else:
        return -v if hemi == 'W' else v

def _valid_coords(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180

def _coords_from_apple(url):
    qs = parse_qs(url.query)
    if 'coordinate' in qs:
        try:
            lat, lon = map(float, qs['coordinate'][0].split(','))
            if _valid_coords(lat, lon):
                return lat, lon
        except ValueError:
            pass
    return None

def _coords_from_google(url):
    """Extract coordinates from Google Maps URL.

    Tries multiple parsing strategies in order:
    1. Path-based: maps.google.com/@lat,lon,zoom
    2. Pin data blob (!3dlat!4dlon) or a path pair (/maps/place/lat,lon/)
    3. Query-based: ...?q=lat,lon or ...?query=lat,lon
    """
    # Attempt 1: Path format (@lat,lon)
    m = re.search(r'@(' + _LAT + r'),(' + _LON + r')', url.path)
    if m and _valid_coords(*(float(x) for x in m.groups())):
        return float(m.group(1)), float(m.group(2))

    # Attempt 1b: the data blob's pin location (!3dlat!4dlon), the exact
    # shared point; then a bare path pair (/maps/place/lat,lon/).
    m = re.search(r'!3d(' + _LAT + r')!4d(' + _LON + r')', url.path)
    if m and _valid_coords(*(float(x) for x in m.groups())):
        return float(m.group(1)), float(m.group(2))
    m = re.search(r'/(' + _LAT + r'),(' + _LON + r')(?:/|$)', url.path)
    if m and _valid_coords(*(float(x) for x in m.groups())):
        return float(m.group(1)), float(m.group(2))

    qs = parse_qs(url.query)

    # Attempt 2: Query parameters (q= or query=)
    for key in ('q', 'query'):
        if key in qs:
            first = unquote_plus(qs[key][0])
            m = re.match(r'\s*(' + _LAT + r')\s*,\s*(' + _LON + r')\s*$', first)
            if m and _valid_coords(*(float(x) for x in m.groups())):
                return float(m.group(1)), float(m.group(2))

    return None
