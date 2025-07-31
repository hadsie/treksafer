import re
import osmnx as ox
from pyproj import Transformer

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
    return False
