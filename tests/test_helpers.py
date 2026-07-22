"""Tests for helper utility functions.

Focuses on non-messaging utilities like coordinate transformations,
compass direction calculations, and external API integrations.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from shapely.geometry import Point
from requests import RequestException

from app.helpers import (
    acres_to_hectares,
    compass_direction,
    epoch_ms_to_datetime,
    get_aqi,
    local_crs,
    local_time,
    quoted,
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

    def test_none_returns_none(self):
        """A source with no size estimate publishes null acres."""
        assert acres_to_hectares(None) is None

    def test_nan_returns_none(self):
        """Null acres arrive as NaN in numeric frame columns."""
        assert acres_to_hectares(float('nan')) is None


class TestEpochMsToDatetime:
    """Test epoch-milliseconds conversion."""

    def test_converts_to_aware_utc_datetime(self):
        result = epoch_ms_to_datetime(1782543600000)
        assert result == datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)

    def test_none_returns_none(self):
        assert epoch_ms_to_datetime(None) is None

    def test_nan_returns_none(self):
        assert epoch_ms_to_datetime(float('nan')) is None


class TestLocalCrs:
    """The user-centered azimuthal equidistant projection."""

    def test_center_projects_to_origin(self):
        import geopandas as gpd
        gdf = gpd.GeoDataFrame(geometry=[Point(-122.5, 50.5)], crs='EPSG:4326')
        projected = gdf.to_crs(local_crs((50.5, -122.5)))
        assert abs(projected.geometry.iloc[0].x) < 1e-6
        assert abs(projected.geometry.iloc[0].y) < 1e-6

    def test_distances_from_center_are_true(self):
        """One degree of latitude is ~111.2 km everywhere, including 60N
        (where Web Mercator would report ~222 km)."""
        import geopandas as gpd
        gdf = gpd.GeoDataFrame(geometry=[Point(-122.5, 61.0)], crs='EPSG:4326')
        projected = gdf.to_crs(local_crs((60.0, -122.5)))
        distance_km = projected.geometry.iloc[0].distance(Point(0, 0)) / 1000
        assert abs(distance_km - 111.2) < 0.5


class TestLocalTime:
    def test_converts_to_timezone_at_coords(self):
        utc = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

        local = local_time(utc, (49.28, -123.12))  # Vancouver, PDT in July

        assert (local.hour, local.minute) == (5, 0)
        assert local == utc

    def test_winter_offset(self):
        utc = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

        local = local_time(utc, (49.28, -123.12))  # Vancouver, PST in January

        assert local.hour == 4


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

    @patch('app.helpers._aqi_session')
    @patch('app.helpers.datetime')
    def test_success_response(self, mock_datetime, mock_session):
        mock_get = mock_session.return_value.get
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

    @patch('app.helpers._aqi_session')
    @patch('app.helpers.datetime')
    def test_first_hour_in_array(self, mock_datetime, mock_session):
        mock_get = mock_session.return_value.get
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

    @patch('app.helpers._aqi_session')
    @patch('app.helpers.datetime')
    def test_last_hour_in_array(self, mock_datetime, mock_session):
        mock_get = mock_session.return_value.get
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

    @patch('app.helpers._aqi_session')
    @patch('app.helpers.datetime')
    def test_constructs_correct_url(self, mock_datetime, mock_session):
        mock_get = mock_session.return_value.get
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

    @patch('app.helpers._aqi_session')
    def test_network_error_returns_none(self, mock_session):
        mock_get = mock_session.return_value.get
        """Network errors return None gracefully."""
        mock_get.side_effect = RequestException("Network timeout")

        coords = (49.25, -123.01)
        result = get_aqi(coords)
        assert result is None


class TestQuoted:
    """Log framing: every content line prefixed with '> ', so a message
    can never read as a log record."""

    def test_prefixes_every_line(self):
        assert quoted('a\nb') == '> a\n> b'
        assert quoted('') == '> '
        assert quoted(None) == '> '

    def test_injected_log_lines_are_quoted(self):
        forged = 'Fires\n2026-07-12 08:00:02 sms INFO From: +19995550000'

        assert quoted(forged).splitlines() == [
            '> Fires', '> 2026-07-12 08:00:02 sms INFO From: +19995550000']
