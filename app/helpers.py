import re
import osmnx as ox
from pyproj import Transformer
from urllib.parse import urlparse, parse_qs, unquote_plus

_LAT = r'-?\d{1,2}(?:\.\d+)?'   # up to ±90
_LON = r'-?\d{1,3}(?:\.\d+)?'   # up to ±180

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

def parse_message(message):
    """Parse an SMS message for lat/long coordinates."""

    # Check for Google or Apple map shares.
    for url_txt in re.findall(r'https?://\S+', message):
        parsed = urlparse(url_txt)
        coords = None
        if 'maps.apple.com' in parsed.netloc:
            coords = _coords_from_apple(parsed)
        elif any(domain in parsed.netloc for domain in ('google.', 'goo.gl')) and '/maps' in parsed.path:
            coords = _coords_from_google(parsed)
        if coords:
            return coords

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

    if lat and long:
        return (lat, long)

    return None

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
