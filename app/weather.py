"""Weather conditions via Open-Meteo (data CC-BY 4.0, open-meteo.com).

This module is the app's only window onto the weather service: consumers
call get_aqi/get_wind and receive plain domain values, so replacing the
provider (or adding a fallback) stays contained here.
"""
import logging

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pytz
import requests
import requests_cache

_COMPASS_POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"]


@dataclass
class WindReport:
    """Current wind at a point, in km/h, with the worst gust coming up.

    direction is the compass point the wind blows FROM (meteorological
    convention). peak_gust is the strongest gust forecast over the next
    12 hours, which is what turns a quiet fire into a moving one.
    """
    speed: int
    gusts: int
    direction: str
    peak_gust: int


@lru_cache(maxsize=2)
def _session(name: str):
    """Cached HTTP session per endpoint (the data is hourly)."""
    Path('cache').mkdir(exist_ok=True)
    return requests_cache.CachedSession(
        cache_name=f'cache/{name}',
        expire_after=timedelta(hours=1),
        allowable_methods=['GET'],
        stale_if_error=True,
    )


def _compass(degrees: float) -> str:
    """8-point compass direction for a wind bearing in degrees."""
    return _COMPASS_POINTS[round(degrees / 45)]


def get_wind(coords) -> WindReport | None:
    """Fetch current wind conditions and the 12-hour peak gust.

    Args:
        coords (tuple): A tuple containing (latitude, longitude) as floats.

    Returns:
        WindReport or None if unavailable.
    """
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={coords[0]}&longitude={coords[1]}"
            "&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
            "&hourly=wind_gusts_10m&forecast_hours=12"
        )
        resp = _session('wind').get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        current = data["current"]
        return WindReport(
            speed=round(current["wind_speed_10m"]),
            gusts=round(current["wind_gusts_10m"]),
            direction=_compass(current["wind_direction_10m"]),
            peak_gust=round(max(data["hourly"]["wind_gusts_10m"])),
        )
    except requests.RequestException as e:
        logging.warning(f"Failed to fetch wind data: network error - {e}")
        return None
    except (KeyError, ValueError, IndexError, TypeError) as e:
        logging.warning(f"Failed to parse wind data: {e}")
        return None


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

        resp = _session('aqi').get(url, timeout=10)
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
