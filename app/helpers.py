import osmnx as ox
import pytz
import re
import requests

from datetime import datetime
from pyproj import Transformer
from shapely.geometry import Point
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

def coords_to_point_meters(coords):
    """Convert WGS84 coordinates to EPSG:3857 point for distance calculations.

    Args:
        coords: Tuple of (latitude, longitude) in WGS84 format

    Returns:
        shapely.geometry.Point in EPSG:3857 (meters)
    """
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(coords[1], coords[0])
    return Point(x, y)

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
    """Parse an SMS message for lat/long coordinates and optional filters.

    Supports:
        - Plain decimal degrees: (49.123, -123.456)
        - Apple Maps links
        - Google Maps links
        - Degrees with hemisphere letters: 50.58225° N, 122.09114° W
        - Filter keywords: "active", "all"
        - Distance filters: "25km", "10mi"
        - Data type keywords: "avalanche", "fire"
        - Forecast time keywords: "today", "tomorrow"

    Returns:
        dict: {"coords": (lat, lon), "filters": dict, "data_type": str, "forecast_time": str} or None if no coords found
    """

    # Extract filters from message (case insensitive, using word boundaries)
    filters = {}
    message_lower = message.lower()

    # Status filter
    if re.search(r'\bactive\b', message_lower):
        filters['status'] = 'active'
    elif re.search(r'\ball\b', message_lower):
        filters['status'] = 'all'

    # Distance filter (support km and mi) - ensure it's standalone
    distance_match = re.search(r'(?:^|\s)(\d+)(km|mi)(?=\s|$)', message_lower)
    if distance_match:
        value, unit = distance_match.groups()
        # Convert to km if needed
        km_value = float(value) if unit == 'km' else float(value) * 1.609344
        filters['distance'] = km_value

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
    elif re.search(r'\btoday\b', message_lower):
        avalanche_filters['forecast'] = 'today'
    elif re.search(r'\btomorrow\b', message_lower):
        avalanche_filters['forecast'] = 'tomorrow'
    elif re.search(r'\ball\b', message_lower):
        avalanche_filters['forecast'] = 'all'

    # Check for Google or Apple map shares.
    for url_txt in re.findall(r'https?://\S+', message):
        parsed = urlparse(url_txt)
        coords = None
        if 'maps.apple.com' in parsed.netloc:
            coords = _coords_from_apple(parsed)
        elif any(domain in parsed.netloc for domain in ('google.', 'goo.gl')) and '/maps' in parsed.path:
            coords = _coords_from_google(parsed)
        if coords:
            return {"coords": coords, "filters": filters, "data_type": data_type, "avalanche_filters": avalanche_filters}


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
        for coords in m:
            coords = [float(x) for x in coords]
            # Find the first number pair that matches lat/long coords.
            if coords[0] <= 90 and coords[0] >= -90 and coords[1] <= 180 and coords[1] >= -180:
                lat = coords[0]
                long = coords[1]
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
        return {
            "coords": (lat, long),
            "filters": filters,
            "data_type": data_type,
            "avalanche_filters": avalanche_filters
        }

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
