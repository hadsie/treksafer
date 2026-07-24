"""Tests for the weather module (Open-Meteo AQI and wind integrations)."""
import pytest
from unittest.mock import Mock, patch
from requests import RequestException

from app.weather import get_aqi, get_wind, AqiReport, WindReport, _compass


def _wind_response(speed=12.4, direction=225, hourly_speeds=None):
    """A mocked Open-Meteo forecast payload with the fields get_wind reads."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "current": {
            "wind_speed_10m": speed,
            "wind_direction_10m": direction,
        },
        "hourly": {
            "time": ["2026-07-18T16:00"],
            "wind_speed_10m": hourly_speeds if hourly_speeds is not None else [speed],
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
            speed=12.4, direction=225, hourly_speeds=[19.1, 30.1, 44.6])
        report = get_wind((50.7, -121.9), forecast_hours=12)
        assert report == WindReport(speed=12, direction="SW", peak=45)

    @patch('app.weather._session')
    def test_peak_is_max_of_forecast_hours(self, mock_session):
        mock_session.return_value.get.return_value = _wind_response(
            speed=20.0, hourly_speeds=[18.0, 35.2, 22.0, 61.9, 40.0])
        report = get_wind((50.0, -120.0), forecast_hours=12)
        assert report.peak == 62

    @patch('app.weather._session')
    def test_constructs_correct_url(self, mock_session):
        mock_get = mock_session.return_value.get
        mock_get.return_value = _wind_response()
        get_wind((49.25, -123.01), forecast_hours=12)
        url = mock_get.call_args[0][0]
        assert "latitude=49.25" in url
        assert "longitude=-123.01" in url
        assert "api.open-meteo.com" in url
        assert "hourly=wind_speed_10m" in url
        # Looking 12 hours past the current hour takes 13 entries.
        assert "forecast_hours=13" in url

    @patch('app.weather._session')
    def test_network_error_returns_none(self, mock_session):
        mock_session.return_value.get.side_effect = RequestException("Network timeout")
        assert get_wind((49.25, -123.01), forecast_hours=12) is None

    @patch('app.weather._session')
    def test_malformed_response_returns_none(self, mock_session):
        mock_response = Mock()
        mock_response.json.return_value = {"current": {}}
        mock_session.return_value.get.return_value = mock_response
        assert get_wind((49.25, -123.01), forecast_hours=12) is None

    @patch('app.weather._session')
    def test_missing_forecast_degrades_to_no_peak(self, mock_session):
        mock_session.return_value.get.return_value = _wind_response(hourly_speeds=[])
        report = get_wind((49.25, -123.01), forecast_hours=12)
        assert report.peak is None
        assert report.speed == 12

    @patch('app.weather._session')
    def test_null_speed_means_no_report(self, mock_session):
        response = _wind_response()
        response.json.return_value['current']['wind_speed_10m'] = None
        mock_session.return_value.get.return_value = response
        assert get_wind((49.25, -123.01), forecast_hours=12) is None


def _aqi_response(current=42, hourly=None):
    """A mocked Open-Meteo air-quality payload with the fields get_aqi reads."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "current": {"us_aqi": current},
        "hourly": {
            "time": ["2026-07-23T23:00"],
            "us_aqi": hourly if hourly is not None else [current],
        },
    }
    return mock_response


class TestGetAqi:
    """Test Air Quality Index API integration."""

    @patch('app.weather._session')
    def test_success_returns_current_and_peak(self, mock_session):
        mock_session.return_value.get.return_value = _aqi_response(
            current=42, hourly=[42, 45, 43, 40])
        assert get_aqi((49.25, -123.01), forecast_hours=4) == AqiReport(current=42, peak=45)

    @patch('app.weather._session')
    def test_peak_never_below_current(self, mock_session):
        mock_session.return_value.get.return_value = _aqi_response(
            current=90, hourly=[90, 60, 40])
        assert get_aqi((49.25, -123.01), forecast_hours=4) == AqiReport(current=90, peak=90)

    @patch('app.weather._session')
    def test_null_forecast_hours_are_ignored(self, mock_session):
        mock_session.return_value.get.return_value = _aqi_response(
            current=30, hourly=[30, None, 90])
        assert get_aqi((49.25, -123.01), forecast_hours=4) == AqiReport(current=30, peak=90)

    @patch('app.weather._session')
    def test_constructs_correct_url(self, mock_session):
        mock_get = mock_session.return_value.get
        mock_get.return_value = _aqi_response()
        get_aqi((49.25, -123.01), forecast_hours=4)
        url = mock_get.call_args[0][0]
        assert "latitude=49.25" in url
        assert "longitude=-123.01" in url
        assert "air-quality-api.open-meteo.com" in url
        assert "current=us_aqi" in url
        # Looking 4 hours past the current hour takes 5 entries.
        assert "forecast_hours=5" in url

    @patch('app.weather._session')
    def test_null_current_means_no_report(self, mock_session):
        mock_session.return_value.get.return_value = _aqi_response(current=None)
        assert get_aqi((49.25, -123.01), forecast_hours=4) is None

    @patch('app.weather._session')
    def test_network_error_returns_none(self, mock_session):
        mock_session.return_value.get.side_effect = RequestException("Network timeout")
        assert get_aqi((49.25, -123.01), forecast_hours=4) is None
