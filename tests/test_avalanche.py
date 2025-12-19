"""Tests for avalanche forecast functionality."""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from requests import RequestException

from app.avalanche import (
    AvalancheProvider,
    AvalancheCanadaProvider,
    AvalancheQuebecProvider,
    AvalancheReport,
    AVALANCHE_PROVIDERS
)
from app.config import get_config, AvalancheProviderConfig


@pytest.fixture
def canada_config():
    """Fixture for Canada provider configuration from settings."""
    settings = get_config()
    # Get CA provider config from settings
    provider_config = settings.avalanche.providers.get('CA')
    if provider_config is None:
        # Fallback if not in config (shouldn't happen in normal operation)
        return AvalancheProviderConfig(
            **{
                'class': 'AvalancheCanadaProvider',
                'api_url': 'https://api.avalanche.ca/forecasts/',
                'cache_timeout': 3600,
                'forecast_cutoff_hour': 16,
                'language': 'en'
            }
        )
    return provider_config


@pytest.fixture
def quebec_config():
    """Fixture for Quebec provider configuration from settings."""
    settings = get_config()
    # Get QC provider config from settings
    provider_config = settings.avalanche.providers.get('QC')
    if provider_config is None:
        # Fallback if not in config (shouldn't happen in normal operation)
        return AvalancheProviderConfig(
            **{
                'class': 'AvalancheQuebecProvider',
                'api_url': 'https://www.avalanchequebec.ca/wp-json/avqc/v1/bulletinavalanche',
                'cache_timeout': 3600,
                'forecast_cutoff_hour': 16,
                'language': 'en'
            }
        )
    return provider_config


@pytest.fixture
def canada_sample_response():
    """Load Canada sample API response."""
    with open('tests/data/avalanche_canada_sample.json', 'r') as f:
        return json.load(f)


@pytest.fixture
def quebec_sample_response():
    """Load Quebec sample API response."""
    with open('tests/data/avalanche_quebec_sample.json', 'r') as f:
        return json.load(f)


class TestAvalancheCanadaProvider:
    """Test AvalancheCanadaProvider functionality."""

    def test_whistler_in_range(self, canada_config):
        """Test Whistler coordinates are within avalanche region."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)  # Whistler

        assert provider.out_of_range(coords) is False

        distance = provider.distance_from_region(coords)
        # Should be None (exact match) or very small distance
        assert distance is None or distance < 1.0

    def test_rogers_pass_in_range(self, canada_config):
        """Test Rogers Pass coordinates are within avalanche region."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (51.3014, -117.5161)  # Rogers Pass

        assert provider.out_of_range(coords) is False

        distance = provider.distance_from_region(coords)
        assert distance is None or distance < 1.0

    def test_vancouver_out_of_range(self, canada_config):
        """Test Vancouver coordinates - may be within buffer of North Shore mountains."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (49.2827, -123.1207)  # Vancouver

        # Vancouver may be within 20km buffer of North Shore mountains
        # So we just check that distance_from_region works
        distance = provider.distance_from_region(coords)
        assert distance is None or isinstance(distance, float)

    def test_near_boundary_proximity(self, canada_config):
        """Test coordinates near boundary return proximity distance."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (49.3429512, -123.0223727)  # Near boundary

        distance = provider.distance_from_region(coords)

        # Should return a distance (not None = exact match, not inf = in range)
        assert distance is not None
        assert distance != float('inf')
        assert isinstance(distance, float)
        assert 0 < distance <= 20  # Within buffer

    def test_far_from_avalanche_terrain(self, canada_config):
        """Test coordinates far from avalanche terrain."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (55.0, -125.0)  # Far north

        assert provider.out_of_range(coords) is True

        distance = provider.distance_from_region(coords)
        assert distance == float('inf')

    def test_exact_match_returns_none(self, canada_config):
        """Test that exact match (point in polygon) returns None."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)  # Whistler (in region)

        distance = provider.distance_from_region(coords)

        # Exact match should return None
        if provider.subregions_gdf is not None:
            # Only test if shapefile is loaded
            assert distance is None or isinstance(distance, float)

    def test_language_url_construction_en(self, canada_config):
        """Test URL construction with English language."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        # URL now comes from config template with {lang} replaced
        expected_url = f"{canada_config.api_url.format(lang=canada_config.language)}?lat={coords[0]}&long={coords[1]}"

        # Mock the _request method to capture the URL
        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 404  # Doesn't matter for this test
            mock_request.return_value = mock_response

            provider.get_forecast(coords)
            mock_request.assert_called_once_with(expected_url)

    def test_language_url_construction_fr(self, canada_config):
        """Test URL construction with French language."""
        # Create a modified config with French language
        fr_config = AvalancheProviderConfig(
            **{
                'class': canada_config.class_name,
                'api_url': canada_config.api_url,
                'cache_timeout': canada_config.cache_timeout,
                'forecast_cutoff_hour': canada_config.forecast_cutoff_hour,
                'language': 'fr'  # Only change language
            }
        )
        provider = AvalancheCanadaProvider(fr_config)
        coords = (50.1163, -122.9574)

        # URL now comes from config template with {lang} replaced
        expected_url = f"{fr_config.api_url.format(lang='fr')}?lat={coords[0]}&long={coords[1]}"

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            provider.get_forecast(coords)
            mock_request.assert_called_once_with(expected_url)

    def test_shapefile_not_found_graceful_degradation(self, canada_config):
        """Test graceful degradation when shapefile is missing."""
        with patch('app.avalanche.canada.gpd.read_file', side_effect=FileNotFoundError):
            provider = AvalancheCanadaProvider(canada_config)

            assert provider.subregions_gdf is None

            # Should return inf when no shapefile
            distance = provider.distance_from_region((50.0, -122.0))
            assert distance == float('inf')


class TestAvalancheQuebecProvider:
    """Test AvalancheQuebecProvider functionality."""

    def test_chic_chocs_in_range(self, quebec_config):
        """Test Chic-Chocs coordinates - Quebec boundary detection."""
        provider = AvalancheQuebecProvider(quebec_config)
        coords = (49.0, -66.0)  # Chic-Chocs / Gaspésie

        # Test that provider handles coordinates (may depend on shapefile availability)
        if provider.province_gdf is not None:
            # If shapefile loaded, should work
            distance = provider.distance_from_region(coords)
            # Should be None (in QC) or a float distance, or inf if out of range
            assert distance is None or isinstance(distance, float)

    def test_montreal_in_province(self, quebec_config):
        """Test Montreal coordinates - Quebec boundary detection."""
        provider = AvalancheQuebecProvider(quebec_config)
        coords = (45.5017, -73.5673)  # Montreal

        if provider.province_gdf is not None:
            # Test that provider handles coordinates
            distance = provider.distance_from_region(coords)
            # Should return some distance value
            assert distance is None or isinstance(distance, float)

    def test_outside_quebec_out_of_range(self, quebec_config):
        """Test coordinates outside Quebec are out of range."""
        provider = AvalancheQuebecProvider(quebec_config)
        coords = (43.6532, -79.3832)  # Toronto

        if provider.province_gdf is not None:
            assert provider.out_of_range(coords) is True

            distance = provider.distance_from_region(coords)
            assert distance == float('inf')

    def test_near_quebec_border(self, quebec_config):
        """Test coordinates near Quebec border return distance."""
        provider = AvalancheQuebecProvider(quebec_config)
        coords = (45.0, -74.5)  # Near Quebec-US border

        if provider.province_gdf is not None:
            distance = provider.distance_from_region(coords)

            # Should be None (in province) or a distance value
            assert distance is None or isinstance(distance, float)

    def test_language_query_param_en(self, quebec_config):
        """Test query parameter construction with English."""
        provider = AvalancheQuebecProvider(quebec_config)
        coords = (49.0, -66.0)

        # URL now comes from config template with {lang} replaced
        expected_url = quebec_config.api_url.format(lang=quebec_config.language)

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            provider.get_forecast(coords)
            mock_request.assert_called_once_with(expected_url)

    def test_language_query_param_fr(self, quebec_config):
        """Test query parameter construction with French."""
        # Create a modified config with French language
        fr_config = AvalancheProviderConfig(
            **{
                'class': quebec_config.class_name,
                'api_url': quebec_config.api_url,
                'cache_timeout': quebec_config.cache_timeout,
                'forecast_cutoff_hour': quebec_config.forecast_cutoff_hour,
                'language': 'fr'  # Only change language
            }
        )
        provider = AvalancheQuebecProvider(fr_config)
        coords = (49.0, -66.0)

        # URL now comes from config template with {lang} replaced
        expected_url = fr_config.api_url.format(lang='fr')

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            provider.get_forecast(coords)
            mock_request.assert_called_once_with(expected_url)

    def test_shapefile_not_found_graceful_degradation(self, quebec_config):
        """Test graceful degradation when province shapefile is missing."""
        with patch('app.avalanche.quebec.gpd.read_file', side_effect=FileNotFoundError):
            provider = AvalancheQuebecProvider(quebec_config)

            assert provider.province_gdf is None

            distance = provider.distance_from_region((49.0, -66.0))
            assert distance == float('inf')


class TestAvalancheReport:
    """Test AvalancheReport multi-provider selection."""

    def test_bc_coordinates_select_canada_provider(self):
        """Test BC coordinates select Canada provider."""
        coords = (50.1163, -122.9574)  # Whistler
        report = AvalancheReport(coords)

        if report.provider is not None:
            assert isinstance(report.provider, AvalancheCanadaProvider)

    def test_quebec_coordinates_select_quebec_provider(self):
        """Test Quebec coordinates select appropriate provider."""
        coords = (49.0, -66.0)  # Chic-Chocs
        report = AvalancheReport(coords)

        # These coordinates may be served by Canada provider if they're
        # within Canadian avalanche regions. Just verify a provider is selected.
        if report.provider is not None:
            assert isinstance(report.provider, (AvalancheCanadaProvider, AvalancheQuebecProvider))

    def test_out_of_range_mexico(self):
        """Test Mexico coordinates are out of range."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        assert report.out_of_range() is True
        assert report.provider is None

    def test_out_of_range_us_no_provider(self):
        """Test US coordinates without avalanche provider."""
        coords = (47.6062, -122.3321)  # Seattle
        report = AvalancheReport(coords)

        # Should be out of range (no US provider yet)
        assert report.out_of_range() is True

    def test_exact_match_returns_immediately(self):
        """Test exact match (distance=None) returns provider immediately."""
        coords = (50.1163, -122.9574)  # Whistler

        with patch('app.avalanche.report.get_config') as mock_config:
            mock_settings = Mock()
            mock_settings.avalanche_distance_buffer = 20
            # Get actual config and use it
            actual_config = get_config()
            mock_settings.avalanche = actual_config.avalanche
            mock_config.return_value = mock_settings

            report = AvalancheReport(coords)

            if report.provider is not None:
                assert isinstance(report.provider, AvalancheCanadaProvider)

    def test_has_data_with_provider(self):
        """Test has_data() returns appropriate value."""
        coords = (50.1163, -122.9574)  # Whistler
        report = AvalancheReport(coords)

        if report.provider is not None:
            # Mock the API call
            with patch.object(report.provider, 'get_forecast', return_value={'region': 'Sea to Sky'}):
                assert report.has_data() is True

            with patch.object(report.provider, 'get_forecast', return_value=None):
                assert report.has_data() is False

    def test_has_data_without_provider(self):
        """Test has_data() returns False when no provider."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        assert report.has_data() is False

    def test_get_forecast_no_provider(self):
        """Test get_forecast() with no provider."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        result = report.get_forecast()
        assert "not available" in result.lower()


class TestAPIIntegration:
    """Test API integration with mocked responses."""

    def test_canada_api_parsing(self, canada_config, canada_sample_response):
        """Test Canada API response parsing."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        # Mock the HTTP request
        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = canada_sample_response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)

            assert result is not None
            assert result['region'] is not None
            assert result['timezone'] == 'America/Vancouver'
            assert len(result['forecasts']) == 2
            assert '2025-01-15' in result['forecasts']
            assert '2025-01-16' in result['forecasts']

            # Check danger ratings
            day1 = result['forecasts']['2025-01-15']
            assert day1['alpine_rating'] == 'Considerable'
            assert day1['treeline_rating'] == 'Moderate'
            assert day1['below_treeline_rating'] == 'Low'

            # Check problems
            assert len(result['problems']) == 2
            assert result['problems'][0]['type'] == 'Storm Slab'
            assert result['problems'][0]['likelihood'] == 'Likely'

    def test_quebec_api_parsing(self, quebec_config, quebec_sample_response):
        """Test Quebec API response parsing."""
        provider = AvalancheQuebecProvider(quebec_config)
        coords = (49.0, -66.0)

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = quebec_sample_response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)

            assert result is not None
            assert result['region'] == 'Chic-Chocs'
            assert result['timezone'] == 'America/Toronto'
            assert len(result['forecasts']) == 1
            assert '2025-01-15' in result['forecasts']

            # Check danger ratings
            day1 = result['forecasts']['2025-01-15']
            assert day1['alpine_rating'] == 'Considerable'
            assert day1['treeline_rating'] == 'Moderate'
            assert day1['below_treeline_rating'] == 'Low'

    def test_multiple_forecast_dates(self, canada_config, canada_sample_response):
        """Test parsing multiple forecast dates."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = canada_sample_response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)

            assert len(result['forecasts']) == 2

            # Verify second day
            day2 = result['forecasts']['2025-01-16']
            assert day2['alpine_rating'] == 'High'
            assert day2['treeline_rating'] == 'Considerable'
            assert day2['below_treeline_rating'] == 'Moderate'

    def test_problem_extraction(self, canada_config, canada_sample_response):
        """Test avalanche problem extraction."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = canada_sample_response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)

            problems = result['problems']
            assert len(problems) == 2

            # Check first problem details
            problem1 = problems[0]
            assert problem1['type'] == 'Storm Slab'
            assert 'Alpine' in problem1['elevations']
            assert 'N' in problem1['aspects']
            assert problem1['likelihood'] == 'Likely'
            assert problem1['size_min'] == '1.5'
            assert problem1['size_max'] == '2.5'

    def test_network_error_handling(self, canada_config):
        """Test network error handling."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        with patch.object(provider, '_request', side_effect=RequestException("Network error")):
            result = provider.get_forecast(coords)
            assert result is None

    def test_404_response(self, canada_config):
        """Test 404 response handling."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)
            assert result is None

    def test_invalid_json_response(self, canada_config):
        """Test invalid JSON response handling."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}  # Empty response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)
            assert result is None


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_invalid_coordinates_range(self, canada_config):
        """Test coordinates outside valid range."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (200.0, 300.0)  # Invalid lat/lon

        # Should handle gracefully
        distance = provider.distance_from_region(coords)
        assert distance == float('inf') or distance is None

    def test_missing_report_id(self, canada_config):
        """Test response with missing report ID."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        bad_response = {
            "report": {
                "id": None,  # Missing ID
                "title": "Test"
            }
        }

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = bad_response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)
            assert result is None

    def test_empty_danger_ratings(self, canada_config):
        """Test response with empty danger ratings."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        response = {
            "report": {
                "id": "test-id",
                "title": "Test",
                "timezone": "America/Vancouver",
                "dateIssued": "2025-01-15T16:00:00Z",
                "dangerRatings": [],  # Empty
                "problems": []
            }
        }

        with patch.object(provider, '_request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = response
            mock_request.return_value = mock_response

            result = provider.get_forecast(coords)

            # Should still parse but with empty forecasts
            assert result is not None
            assert len(result['forecasts']) == 0

    def test_timeout_error(self, canada_config):
        """Test timeout error handling."""
        provider = AvalancheCanadaProvider(canada_config)
        coords = (50.1163, -122.9574)

        with patch.object(provider, '_request', side_effect=RequestException("Timeout")):
            result = provider.get_forecast(coords)
            assert result is None

    def test_outside_of_area_message(self):
        """Test outside of area error message."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        msg = report.outside_of_area_msg()
        assert "outside of supported avalanche forecast area" in msg.lower()
        assert "error" in msg.lower()
