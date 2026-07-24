"""Weather conditions via Open-Meteo (data CC-BY 4.0, open-meteo.com).

This module is the app's only window onto the weather service: consumers
call get_aqi/get_wind and receive plain domain values, so replacing the
provider (or adding a fallback) stays contained here.
"""
import logging

from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import requests
import requests_cache

_COMPASS_POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"]


@dataclass
class AqiReport:
    """US Air Quality Index at a point."""
    # The current hour's value.
    current: int
    # The highest value within the forecast window after now.
    peak: int


@dataclass
class WindReport:
    """Current wind at a point; all speeds in km/h."""
    # Sustained wind speed.
    speed: int
    # Compass point the wind blows FROM (meteorological convention).
    direction: str
    # Strongest sustained speed forecast within the requested window
    # after now. None when unavailable.
    peak: int | None


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


def get_wind(coords, forecast_hours: int) -> WindReport | None:
    """Fetch current sustained wind and its forecast peak.

    Args:
        coords (tuple): A tuple containing (latitude, longitude) as floats.
        forecast_hours (int): How far past the current hour the peak looks.

    Returns:
        WindReport or None if unavailable.
    """
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={coords[0]}&longitude={coords[1]}"
            "&current=wind_speed_10m,wind_direction_10m"
            # The hourly series starts AT the current hour, so looking N
            # hours past it takes N+1 entries.
            f"&hourly=wind_speed_10m&forecast_hours={forecast_hours + 1}"
        )
        resp = _session('wind').get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        current = data["current"]
        speed = current.get("wind_speed_10m")
        direction = current.get("wind_direction_10m")
        if speed is None or direction is None:
            return None
        upcoming = [v for v in data.get("hourly", {}).get("wind_speed_10m", [])
                    if v is not None]
        return WindReport(
            speed=round(speed),
            direction=_compass(direction),
            peak=round(max(upcoming)) if upcoming else None,
        )
    except requests.RequestException as e:
        logging.warning(f"Failed to fetch wind data: network error - {e}")
        return None
    except (KeyError, ValueError, IndexError, TypeError) as e:
        logging.warning(f"Failed to parse wind data: {e}")
        return None


def get_aqi(coords, forecast_hours: int):
    """
    Fetch the current US Air Quality Index (AQI) for given coordinates,
    with the highest value forecast over the next {forecast_hours}.

    Args:
        coords (tuple): A tuple containing (latitude, longitude) as floats.
        forecast_hours (int): How far past the current hour the peak looks.

    Returns:
        AqiReport or None if unavailable.
    """
    try:
        # The hourly series starts AT the current hour, so looking N hours
        # past it takes N+1 entries.
        url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={coords[0]}&longitude={coords[1]}"
            f"&current=us_aqi&hourly=us_aqi&forecast_hours={forecast_hours + 1}"
        )

        resp = _session('aqi').get(url, timeout=10)
        resp.raise_for_status()  # Raise for 4xx/5xx errors
        data = resp.json()

        current = data["current"].get("us_aqi")
        if current is None:
            return None
        upcoming = [v for v in data.get("hourly", {}).get("us_aqi", [])
                    if v is not None]
        return AqiReport(current=round(current),
                         peak=round(max([current] + upcoming)))

    except requests.RequestException as e:
        logging.warning(f"Failed to fetch AQI data: network error - {e}")
        return None
    except (KeyError, ValueError, IndexError) as e:
        logging.warning(f"Failed to parse AQI data: {e}")
        return None
