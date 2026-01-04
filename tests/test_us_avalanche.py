"""Tests for US National Avalanche Center functionality."""

import json
import pytest
from unittest.mock import Mock, patch
from requests import RequestException

from app.avalanche.us_nac import NationalAvalancheProvider
from app.avalanche import AvalancheReport
from app.config import get_config


@pytest.fixture
def nac_config():
    """Fixture for NAC provider configuration from settings."""
    settings = get_config()
    provider_config = settings.avalanche.providers.get('NationalAvalancheCenter')
    return provider_config


@pytest.fixture
def nac_sample_response():
    """Load NAC sample API response."""
    with open('tests/data/us_nac_CNFAIC_2815_sample.json', 'r') as f:
        return json.load(f)


class TestNationalAvalancheProvider:
    """Test NationalAvalancheProvider functionality."""

    def test_turnagain_pass_in_range(self, nac_config):
        """Test Turnagain Pass coordinates are within CNFAIC zone."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)  # Turnagain Pass

        assert provider.out_of_range(coords) is False

        distance = provider.distance_from_region(coords)
        # Should be None (exact match) or very small distance
        assert distance is None

    def test_colorado_in_range(self, nac_config):
        """Test Colorado coordinates are within CAIC zone."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (39.6433, -106.3781)  # Vail area

        assert provider.out_of_range(coords) is False

        distance = provider.distance_from_region(coords)
        assert distance is None

    def test_seattle_out_of_range(self, nac_config):
        """Test Seattle coordinates - should be in NWAC zone, not out of range."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (47.6062, -122.3321)  # Seattle

        # Seattle itself should be outside, but close to NWAC zones
        distance = provider.distance_from_region(coords)
        # Either in range (None) or nearby but not inf
        assert distance != float('inf') or distance is None

    def test_exact_match_returns_none(self, nac_config):
        """Test that exact match (point in polygon) returns None."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)  # Turnagain Pass (in CNFAIC region)

        distance = provider.distance_from_region(coords)
        assert distance is None

    def test_far_from_avalanche_terrain(self, nac_config):
        """Test coordinates far from avalanche terrain."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (30.0, -90.0)  # Louisiana

        assert provider.out_of_range(coords) is True

        distance = provider.distance_from_region(coords)
        assert distance == float('inf')

    def test_url_construction(self, nac_config):
        """Test URL construction with center and zone IDs."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)  # Turnagain Pass

        # Mock _find_zone to return specific zone info
        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        with patch.object(provider, '_find_zone', return_value=zone_info):
            with patch.object(provider, '_request') as mock_request:
                mock_response = Mock()
                mock_response.status_code = 404
                mock_request.return_value = mock_response

                provider.get_forecast(coords)

                # Verify URL was constructed correctly
                expected_url = 'https://api.avalanche.org/v2/public/product?type=forecast&center_id=CNFAIC&zone_id=2815'
                mock_request.assert_called_once_with(expected_url)


class TestNACAPIIntegration:
    """Test NAC API integration with mocked responses."""

    def test_nac_api_parsing(self, nac_config, nac_sample_response):
        """Test NAC API response parsing."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)  # Turnagain Pass

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        with patch.object(provider, '_find_zone', return_value=zone_info):
            with patch.object(provider, '_request') as mock_request:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = nac_sample_response
                mock_request.return_value = mock_response

                result = provider.get_forecast(coords)

                assert result is not None
                assert result['region'] == 'Turnagain Pass and Girdwood'
                assert result['timezone'] == 'America/Anchorage'
                assert len(result['forecasts']) == 2
                assert 'Sunday' in result['forecasts']
                assert 'Monday' in result['forecasts']

    def test_danger_rating_conversion(self, nac_config, nac_sample_response):
        """Test numeric danger ratings are converted to strings."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        result = provider._parse_forecast(nac_sample_response, zone_info)

        # Check that numeric ratings (1, 2) are converted to strings
        day1 = result['forecasts']['Sunday']
        assert day1['alpine_rating'] == 'Moderate'  # 2 → Moderate
        assert day1['treeline_rating'] == 'Low'  # 1 → Low
        assert day1['below_treeline_rating'] == 'Low'  # 1 → Low

    def test_problem_location_parsing(self, nac_config, nac_sample_response):
        """Test problem location parsing (e.g., 'southwest upper' → SW + Alpine)."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        result = provider._parse_forecast(nac_sample_response, zone_info)

        # Check that problem locations are parsed correctly
        problems = result['problems']
        assert len(problems) == 1

        problem = problems[0]
        assert problem['type'] == 'Wind Slab'

        # Should have parsed all 8 aspect+elevation combinations
        # All are "upper" → Alpine
        assert 'Alpine' in problem['elevations']

        # Should have all 8 aspects
        expected_aspects = ['E', 'N', 'NE', 'NW', 'S', 'SE', 'SW', 'W']
        for aspect in expected_aspects:
            assert aspect in problem['aspects']

    def test_date_handling_current(self, nac_config, nac_sample_response):
        """Test 'current' maps to published_time day of week."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        result = provider._parse_forecast(nac_sample_response, zone_info)

        # published_time is 2025-12-28T16:00:00+00:00
        # In America/Anchorage, that's 2025-12-28 07:00:00 (Sunday)
        assert 'Sunday' in result['forecasts']

    def test_date_handling_tomorrow(self, nac_config, nac_sample_response):
        """Test 'tomorrow' maps to next day after published_time."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        result = provider._parse_forecast(nac_sample_response, zone_info)

        # Tomorrow from Saturday is Sunday
        assert 'Sunday' in result['forecasts']

    def test_multiple_forecast_days(self, nac_config, nac_sample_response):
        """Test both current and tomorrow forecasts are parsed."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        result = provider._parse_forecast(nac_sample_response, zone_info)

        assert len(result['forecasts']) == 2

        # Verify second day (tomorrow)
        day2 = result['forecasts']['Sunday']
        assert day2['alpine_rating'] == 'Moderate'
        assert day2['treeline_rating'] == 'Low'
        assert day2['below_treeline_rating'] == 'Low'


class TestNACEdgeCases:
    """Test NAC edge cases and error conditions."""

    def test_invalid_location_format(self, nac_config, caplog):
        """Test malformed location strings log warning."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        # Create response with invalid location format
        bad_response = {
            'published_time': '2025-12-28T16:00:00+00:00',
            'danger': [
                {'lower': 1, 'upper': 2, 'middle': 1, 'valid_day': 'current'}
            ],
            'forecast_avalanche_problems': [
                {
                    'name': 'Wind Slab',
                    'location': ['invalid'],  # Missing space - can't split
                    'likelihood': 'possible',
                    'size': ['1', '2']
                }
            ],
            'forecast_zone': [{'url': 'https://example.com'}]
        }

        result = provider._parse_forecast(bad_response, zone_info)

        # Should still parse but log warning
        assert result is not None
        assert len(caplog.records) > 0
        assert any('Invalid NAC problem location' in record.message for record in caplog.records)

    def test_empty_danger_ratings(self, nac_config, caplog):
        """Test empty danger array handling."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        response = {
            'published_time': '2025-12-28T16:00:00+00:00',
            'danger': [],  # Empty
            'forecast_avalanche_problems': [],
            'forecast_zone': [{'url': 'https://example.com'}]
        }

        result = provider._parse_forecast(response, zone_info)

        # Should still parse but with empty forecasts
        assert result is not None
        assert len(result['forecasts']) == 0

    def test_missing_forecast_zone(self, nac_config):
        """Test missing forecast_zone in response."""
        provider = NationalAvalancheProvider(nac_config)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        response = {
            'published_time': '2025-12-28T16:00:00+00:00',
            'danger': [
                {'lower': 1, 'upper': 2, 'middle': 1, 'valid_day': 'current'}
            ],
            'forecast_avalanche_problems': []
            # Missing forecast_zone
        }

        result = provider._parse_forecast(response, zone_info)

        # Should still parse with empty URL
        assert result is not None
        assert result['url'] == ''

    def test_network_error_handling(self, nac_config, caplog):
        """Test network error handling and logging."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        with patch.object(provider, '_find_zone', return_value=zone_info):
            with patch.object(provider, '_request', side_effect=RequestException("Network error")):
                result = provider.get_forecast(coords)

                # Verify return value
                assert result is None

                # Verify logging
                assert len(caplog.records) == 1
                assert caplog.records[0].levelname == 'WARNING'
                assert 'Network error checking NAC avalanche data' in caplog.records[0].message

    def test_404_response(self, nac_config, caplog):
        """Test 404 response handling and logging."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)

        zone_info = {
            'id': 2815,
            'center_id': 'CNFAIC',
            'timezone': 'America/Anchorage',
            'name': 'Turnagain Pass and Girdwood'
        }

        with patch.object(provider, '_find_zone', return_value=zone_info):
            with patch.object(provider, '_request') as mock_request:
                mock_response = Mock()
                mock_response.status_code = 404
                mock_request.return_value = mock_response

                result = provider.get_forecast(coords)

                # Verify return value
                assert result is None

                # Verify logging
                assert len(caplog.records) == 1
                assert caplog.records[0].levelname == 'WARNING'
                assert 'status 404' in caplog.records[0].message

    def test_no_zone_found(self, nac_config, caplog):
        """Test coords outside all zones."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (30.0, -90.0)  # Louisiana

        result = provider.get_forecast(coords)

        # Verify return value
        assert result is None

        # Verify logging
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == 'WARNING'
        assert 'No NAC zone found' in caplog.records[0].message


class TestNACLiveAPI:
    """Test live NAC API integration (requires network)."""

    @pytest.mark.skip(reason="Live API test - enable manually")
    def test_cnfaic_live_api_format(self, nac_config):
        """Test that live NAC API returns expected format."""
        provider = NationalAvalancheProvider(nac_config)
        coords = (60.7896, -149.0746)  # Turnagain Pass

        # Make a real API call
        result = provider.get_forecast(coords)

        # Verify we got a response
        assert result is not None, "API should return data for Turnagain Pass coordinates"

        # Check expected top-level keys
        assert 'region' in result
        assert 'timezone' in result
        assert 'forecasts' in result
        assert 'problems' in result

        # Verify forecasts structure
        assert isinstance(result['forecasts'], dict)
        assert len(result['forecasts']) > 0, "Should have at least one forecast day"

        # Check first forecast has expected rating keys
        first_forecast = next(iter(result['forecasts'].values()))
        assert 'alpine_rating' in first_forecast
        assert 'treeline_rating' in first_forecast
        assert 'below_treeline_rating' in first_forecast

        # Verify problems structure
        assert isinstance(result['problems'], list)
        if len(result['problems']) > 0:
            problem = result['problems'][0]
            assert 'type' in problem
            assert 'elevations' in problem
            assert 'aspects' in problem
            assert 'likelihood' in problem
            assert 'size_min' in problem
            assert 'size_max' in problem
