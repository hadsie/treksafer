import osmnx as ox
import pytz
import re
import requests

from datetime import datetime
from pyproj import Transformer
from urllib.parse import urlparse, parse_qs, unquote_plus

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

def compass_direction(pointA, pointB):
    """
    Calculates the compass direction between two points.

    :param Point pointA: The position of the requester.
    :param Point pointB: The closest point on the fire perimeter.
    :return: The compass direction to the fire perimeter.
    :rtype: str
    """
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW",
                  "SW", "WSW", "W", "WNW" ,"NW" ,"NNW", "N"]
    to_latlong = Transformer.from_crs("EPSG:3857", "EPSG:4326")
    pointa = to_latlong.transform(pointA.x, pointA.y)
    pointb = to_latlong.transform(pointB.x, pointB.y)

    bearing = ox.bearing.calculate_bearing(pointa[0], pointa[1], pointb[0], pointb[1])
    return directions[round(bearing/22.5)]

def get_aqi(coords):
    """
    Fetch the current US Air Quality Index (AQI) for given coordinates.

    Makes a request to the Open-Meteo Air Quality API to grab the most
    recent AQI reading.

    Args:
        coords (tuple): A tuple containing (latitude, longitude) as floats.

    Returns:
        int: The current US Air Quality Index value.
    """
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={coords[0]}&longitude={coords[1]}"
        "&hourly=us_aqi&timezone=America%2FLos_Angeles&forecast_days=1"
    )

    resp = requests.get(url)
    data = resp.json()

    # Get the current "hour" in the same timezone as the JSON data is giving us.
    api_timezone = data["timezone"]
    current_time = datetime.now(pytz.timezone(api_timezone))
    current_hour = current_time.strftime('%Y-%m-%dT%H:00')

    # Find the index of current time in the hourly time array, and match that to the AQI array.
    current_index = data["hourly"]["time"].index(current_hour)

    # Get latest AQI
    return data["hourly"]["us_aqi"][current_index]

def parse_message(message):
    """Parse an SMS message for lat/long coordinates and optional fire status commands.

    Supports:
        - Plain decimal degrees: (49.123, -123.456)
        - Apple Maps links
        - Google Maps links
        - Degrees with hemisphere letters: 50.58225° N, 122.09114° W
        - Fire status commands: 'active', 'all'

    Returns:
        tuple: ((lat, lon), fire_level_override) where:
            - coordinates: (lat, lon) tuple or None if not found
            - fire_level_override: 'active', 'out', or None
    """

    # Step 1: Extract fire level command (if present)
    fire_level_override = None

    # Look for "active" or "all" commands (case insensitive)
    fire_command_pattern = r'\b(active|all)\b'
    match = re.search(fire_command_pattern, message, re.IGNORECASE)
    if match:
        command = match.group(1).lower()
        if command == 'active':
            fire_level_override = 'active'
        elif command == 'all':
            fire_level_override = 'out'  # 'out' level shows all fires

    # Step 2: Extract coordinates (existing logic)
    coords = None

    # Check for Google or Apple map shares.
    for url_txt in re.findall(r'https?://\S+', message):
        parsed = urlparse(url_txt)
        if 'maps.apple.com' in parsed.netloc:
            coords = _coords_from_apple(parsed)
        elif any(domain in parsed.netloc for domain in ('google.', 'goo.gl')) and '/maps' in parsed.path:
            coords = _coords_from_google(parsed)
        if coords:
            return (coords, fire_level_override)

    lat_coord = r'-?\d{1,2}\.\d{1,8}|-?\d{1,2}'
    long_coord = r'-?\d{1,3}\.\d{1,8}|-?\d{1,3}'

    # inReach has the coordinates at the end of the message in brackets.
    m = re.search(r'\((%s),\s*(%s)\)\s*$' % (lat_coord, long_coord), message)

    lat = long = None
    if m != None and len(m.groups()) == 2:
        lat = float(m.group(1))
        long = float(m.group(2))
    else:
        # Find a matching coordinate pair anywhere in the string.
        m = re.findall(r'\b(%s)\s*,\s*(%s)\b' % (lat_coord, long_coord), message)
        for coords_match in m:
            coords_vals = [float(x) for x in coords_match]
            # Find the first number pair that matches lat/long coords.
            if coords_vals[0] <= 90 and coords_vals[0] >= -90 and coords_vals[1] <= 180 and coords_vals[1] >= -180:
                lat = coords_vals[0]
                long = coords_vals[1]
                break

    # If decimal parsing didn't hit, try degree+hemisphere patterns.
    if lat is None or long is None:
        for pat in _DEG_HEMI_PATTERNS:
            m = pat.search(message)
            if m:
                lat_val = float(m.group('lat'))
                lon_val = float(m.group('lon'))
                lat_dir = m.group('lat_dir')
                lon_dir = m.group('lon_dir')
                lat = _apply_hemisphere(lat_val, lat_dir, for_lat=True)
                long = _apply_hemisphere(lon_val, lon_dir, for_lat=False)
                # Sanity check bounds
                if -90 <= lat <= 90 and -180 <= long <= 180:
                    break
                else:
                    lat = long = None

    if lat is not None and long is not None:
        return ((lat, long), fire_level_override)

    return None


def _apply_hemisphere(value: float, hemi: str, for_lat: bool) -> float:
    # hemisphere wins over sign if both appear (e.g., "-50 N" -> +50)
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
    # URL format: maps.google.com/@lat,lon,zoom
    m = re.search(r'@(' + _LAT + r'),(' + _LON + r')', url.path)
    if m and _valid_coords(*(float(x) for x in m.groups())):
        return float(m.group(1)), float(m.group(2))

    qs = parse_qs(url.query)

    # Attempt 2 ...?q=lat,lon or ...?query=lat,lon
    for key in ('q', 'query'):
        if key in qs:
            first = unquote_plus(qs[key][0])
            m = re.match(r'\s*(' + _LAT + r')\s*,\s*(' + _LON + r')\s*$', first)
            if m and _valid_coords(*(float(x) for x in m.groups())):
                return float(m.group(1)), float(m.group(2))
    return None
