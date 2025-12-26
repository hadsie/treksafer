"""Tests for helper utility functions.

Focuses on non-messaging utilities like coordinate transformations,
compass direction calculations, and external API integrations.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime
from shapely.geometry import Point
from requests import RequestException

from app.helpers import (
    acres_to_hectares,
    compass_direction,
    coords_to_point_meters,
    get_aqi,
)


class TestAcresToHectares:
    """Test area conversion."""

    def test_zero_acres(self):
        """Zero acres is zero hectares."""
        assert acres_to_hectares(0) == 0

    def test_one_acre(self):
        """1 acre → 0.4 ha (rounded)."""
        assert acres_to_hectares(1) == 0.4

    def test_exact_hectare(self):
        """2.47 acres → exactly 1 ha."""
        assert acres_to_hectares(2.4710538147) == 1

    def test_large_area(self):
        """Test larger area conversion."""
        assert acres_to_hectares(100) == 40.47


class TestCoordsToPointMeters:
    """Test WGS84 to EPSG:3857 (Web Mercator) coordinate transformation."""

    def test_returns_point_object(self):
        """Function returns a shapely Point."""
        coords = (49.2827, -123.1207)  # Vancouver
        point = coords_to_point_meters(coords)
        assert isinstance(point, Point)

    def test_equator_origin(self):
        """Null Island (0, 0) transforms to (0, 0) in Web Mercator."""
        point = coords_to_point_meters((0, 0))
        assert point.x == pytest.approx(0, abs=1)
        assert point.y == pytest.approx(0, abs=1)

    def test_vancouver_coordinates(self):
        """Vancouver produces expected Web Mercator values."""
        coords = (49.2827, -123.1207)
        point = coords_to_point_meters(coords)
        # Vancouver should be around x=-13.7M, y=6.3M in EPSG:3857
        assert point.x == pytest.approx(-13706077, abs=1000)
        assert point.y == pytest.approx(6322967, abs=1000)

    def test_positive_longitude(self):
        """Positive longitude (Eastern hemisphere) works."""
        coords = (51.5074, 0.1278)  # London
        point = coords_to_point_meters(coords)
        assert point.x > 0  # East of prime meridian
        assert point.y > 0  # North of equator

    def test_negative_latitude(self):
        """Negative latitude (Southern hemisphere) works."""
        coords = (-33.8688, 151.2093)  # Sydney
        point = coords_to_point_meters(coords)
        assert point.x > 0  # East of prime meridian
        assert point.y < 0  # South of equator

    def test_pole_coordinates(self):
        """Near-pole coordinates work (though Web Mercator distorts them)."""
        coords = (85.0, 0.0)  # Near North Pole
        point = coords_to_point_meters(coords)
        assert isinstance(point, Point)
        assert abs(point.y) > 10000000  # Very large y value near pole


class TestCompassDirection:
    """Test compass bearing calculations between points."""

    def test_due_north(self):
        """Point directly north."""
        origin = Point(0, 0)
        north = Point(0, 1000)
        assert compass_direction(origin, north) == "N"

    def test_due_east(self):
        """Point directly east."""
        origin = Point(0, 0)
        east = Point(1000, 0)
        assert compass_direction(origin, east) == "E"

    def test_due_south(self):
        """Point directly south."""
        origin = Point(0, 0)
        south = Point(0, -1000)
        assert compass_direction(origin, south) == "S"

    def test_due_west(self):
        """Point directly west."""
        origin = Point(0, 0)
        west = Point(-1000, 0)
        assert compass_direction(origin, west) == "W"

    def test_northeast(self):
        """Point in NE quadrant."""
        origin = Point(0, 0)
        ne = Point(707, 707)  # ~45 degrees
        assert compass_direction(origin, ne) == "NE"

    def test_southeast(self):
        """Point in SE quadrant."""
        origin = Point(0, 0)
        se = Point(707, -707)  # ~135 degrees
        assert compass_direction(origin, se) == "SE"

    def test_southwest(self):
        """Point in SW quadrant."""
        origin = Point(0, 0)
        sw = Point(-707, -707)  # ~225 degrees
        assert compass_direction(origin, sw) == "SW"

    def test_northwest(self):
        """Point in NW quadrant."""
        origin = Point(0, 0)
        nw = Point(-707, 707)  # ~315 degrees
        result = compass_direction(origin, nw)
        assert result in {"NW", "NNW", "WNW"}  # Approximate bearing

    def test_from_non_origin(self):
        """Works when origin is not (0, 0)."""
        origin = Point(1000, 2000)
        target = Point(2000, 2000)  # Due east of origin
        assert compass_direction(origin, target) == "E"

    def test_short_distance(self):
        """Works with very short distances."""
        origin = Point(0, 0)
        nearby = Point(1, 0)  # Just 1 meter east
        assert compass_direction(origin, nearby) == "E"

    def test_long_distance(self):
        """Works with long distances."""
        origin = Point(0, 0)
        far = Point(1000000, 0)  # 1000km east
        assert compass_direction(origin, far) == "E"


class TestGetAqi:
    """Test Air Quality Index API integration."""

    @patch('app.helpers.requests.get')
    @patch('app.helpers.datetime')
    def test_success_response(self, mock_datetime, mock_get):
        """Successful API response returns AQI value."""
        # Mock current time
        mock_now = Mock()
        mock_now.strftime.return_value = "2025-12-24T14:00"
        mock_datetime.now.return_value = mock_now

        # Mock API response
        mock_response = Mock()
        mock_response.json.return_value = {
            "timezone": "America/Los_Angeles",
            "hourly": {
                "time": ["2025-12-24T13:00", "2025-12-24T14:00", "2025-12-24T15:00"],
                "us_aqi": [38, 42, 45]
            }
        }
        mock_get.return_value = mock_response

        coords = (49.25, -123.01)
        aqi = get_aqi(coords)

        # Should return current hour's AQI
        assert aqi == 42

    @patch('app.helpers.requests.get')
    @patch('app.helpers.datetime')
    def test_first_hour_in_array(self, mock_datetime, mock_get):
        """Returns correct AQI when current hour is first in array."""
        mock_now = Mock()
        mock_now.strftime.return_value = "2025-12-24T00:00"
        mock_datetime.now.return_value = mock_now

        mock_response = Mock()
        mock_response.json.return_value = {
            "timezone": "America/Vancouver",
            "hourly": {
                "time": ["2025-12-24T00:00", "2025-12-24T01:00"],
                "us_aqi": [25, 30]
            }
        }
        mock_get.return_value = mock_response

        aqi = get_aqi((50.0, -120.0))
        assert aqi == 25

    @patch('app.helpers.requests.get')
    @patch('app.helpers.datetime')
    def test_last_hour_in_array(self, mock_datetime, mock_get):
        """Returns correct AQI when current hour is last in array."""
        mock_now = Mock()
        mock_now.strftime.return_value = "2025-12-24T23:00"
        mock_datetime.now.return_value = mock_now

        mock_response = Mock()
        mock_response.json.return_value = {
            "timezone": "America/Vancouver",
            "hourly": {
                "time": ["2025-12-24T22:00", "2025-12-24T23:00"],
                "us_aqi": [35, 40]
            }
        }
        mock_get.return_value = mock_response

        aqi = get_aqi((50.0, -120.0))
        assert aqi == 40

    @patch('app.helpers.requests.get')
    @patch('app.helpers.datetime')
    def test_constructs_correct_url(self, mock_datetime, mock_get):
        """API URL is constructed correctly with coordinates."""
        # Mock current time
        mock_now = Mock()
        mock_now.strftime.return_value = "2025-12-24T14:00"
        mock_datetime.now.return_value = mock_now

        mock_response = Mock()
        mock_response.json.return_value = {
            "timezone": "America/Los_Angeles",
            "hourly": {
                "time": ["2025-12-24T14:00"],
                "us_aqi": [42]
            }
        }
        mock_get.return_value = mock_response

        coords = (49.25, -123.01)
        get_aqi(coords)

        # Verify URL construction
        call_args = mock_get.call_args
        url = call_args[0][0]
        assert "latitude=49.25" in url
        assert "longitude=-123.01" in url
        assert "air-quality-api.open-meteo.com" in url

    @patch('app.helpers.requests.get')
    def test_network_error_returns_none(self, mock_get):
        """Network errors return None gracefully."""
        mock_get.side_effect = RequestException("Network timeout")

        coords = (49.25, -123.01)
        result = get_aqi(coords)
        assert result is None
