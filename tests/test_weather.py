"""Tests for the weather module (Open-Meteo AQI and wind integrations)."""
import pytest
from unittest.mock import Mock, patch
from requests import RequestException

from app.weather import get_aqi, get_wind, WindReport, _compass


def _wind_response(speed=12.4, direction=225, gusts=30.1, hourly_gusts=None):
    """A mocked Open-Meteo forecast payload with the fields get_wind reads."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "current": {
            "wind_speed_10m": speed,
            "wind_direction_10m": direction,
            "wind_gusts_10m": gusts,
        },
        "hourly": {
            "time": ["2026-07-18T16:00"],
            "wind_gusts_10m": hourly_gusts if hourly_gusts is not None else [gusts],
        },
    }
    return mock_response


class TestCompass:
    """Test wind degrees to 8-point compass conversion."""

    @pytest.mark.parametrize("degrees,expected", [
        (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
        (180, "S"), (225, "SW"), (270, "W"), (315, "NW"),
        (360, "N"),
        (281, "W"),    # rounds to the nearest point
        (22, "N"),     # just inside N's half-window
        (23, "NE"),    # just inside NE's
    ])
    def test_degrees_map_to_nearest_point(self, degrees, expected):
        assert _compass(degrees) == expected


class TestGetWind:
    """Test wind forecast API integration."""

    @patch('app.weather._session')
    def test_success_returns_rounded_report(self, mock_session):
        mock_session.return_value.get.return_value = _wind_response(
            speed=12.4, direction=225, gusts=30.1, hourly_gusts=[19.1, 30.1, 44.6])
        report = get_wind((50.7, -121.9))
        assert report == WindReport(speed=12, gusts=30, direction="SW", peak_gust=45)

    @patch('app.weather._session')
    def test_peak_gust_is_max_of_forecast_hours(self, mock_session):
        mock_session.return_value.get.return_value = _wind_response(
            gusts=20.0, hourly_gusts=[18.0, 35.2, 22.0, 61.9, 40.0])
        report = get_wind((50.0, -120.0))
        assert report.peak_gust == 62

    @patch('app.weather._session')
    def test_constructs_correct_url(self, mock_session):
        mock_get = mock_session.return_value.get
        mock_get.return_value = _wind_response()
        get_wind((49.25, -123.01))
        url = mock_get.call_args[0][0]
        assert "latitude=49.25" in url
        assert "longitude=-123.01" in url
        assert "api.open-meteo.com" in url
        assert "forecast_hours=12" in url

    @patch('app.weather._session')
    def test_network_error_returns_none(self, mock_session):
        mock_session.return_value.get.side_effect = RequestException("Network timeout")
        assert get_wind((49.25, -123.01)) is None

    @patch('app.weather._session')
    def test_malformed_response_returns_none(self, mock_session):
        mock_response = Mock()
        mock_response.json.return_value = {"current": {}}
        mock_session.return_value.get.return_value = mock_response
        assert get_wind((49.25, -123.01)) is None

    @patch('app.weather._session')
    def test_empty_hourly_gusts_returns_none(self, mock_session):
        mock_session.return_value.get.return_value = _wind_response(hourly_gusts=[])
        assert get_wind((49.25, -123.01)) is None


class TestGetAqi:
    """Test Air Quality Index API integration."""

    @patch('app.weather._session')
    @patch('app.weather.datetime')
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

    @patch('app.weather._session')
    @patch('app.weather.datetime')
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

    @patch('app.weather._session')
    @patch('app.weather.datetime')
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

    @patch('app.weather._session')
    @patch('app.weather.datetime')
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

    @patch('app.weather._session')
    def test_network_error_returns_none(self, mock_session):
        mock_get = mock_session.return_value.get
        """Network errors return None gracefully."""
        mock_get.side_effect = RequestException("Network timeout")

        coords = (49.25, -123.01)
        result = get_aqi(coords)
        assert result is None
