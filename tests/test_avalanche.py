"""Tests for generic avalanche provider functionality and base class."""

import pytest
from unittest.mock import Mock, patch
from pathlib import Path

from app.avalanche import AvalancheReport
from app.avalanche.base import AvalancheProvider
from app.config import get_config, AvalancheProviderConfig


class TestAvalancheReport:
    """Test generic AvalancheReport provider selection."""

    def test_bc_coordinates_select_canada_provider(self):
        """Test BC coordinates select AvalancheCanadaProvider."""
        coords = (50.1163, -122.9574)  # Whistler
        report = AvalancheReport(coords)

        assert report.provider is not None
        assert report.provider.__class__.__name__ == 'AvalancheCanadaProvider'

    def test_out_of_range_mexico(self):
        """Test coordinates in Mexico (no provider coverage)."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        assert report.provider is None

    def test_out_of_range_us_no_provider(self):
        """Test US coordinates outside NAC coverage."""
        coords = (30.2672, -97.7431)  # Austin, TX (no coverage)
        report = AvalancheReport(coords)

        # Could be None if no provider covers it
        # The actual result depends on whether NAC zones extend there
        if report.provider is None:
            assert report.out_of_range() is True
        else:
            # If a provider is selected, check it's out of range
            assert report.out_of_range() is True

    def test_exact_match_returns_immediately(self):
        """Test exact match returns immediately without checking other providers."""
        coords = (50.1163, -122.9574)  # Whistler (in Canada coverage)
        report = AvalancheReport(coords)

        # Verify that a provider was selected
        assert report.provider is not None

        # Verify exact match behavior (distance is None)
        distance = report.provider.distance_from_region(coords)
        assert distance is None

    def test_has_data_with_provider(self):
        """Test has_data returns True when provider exists."""
        coords = (50.1163, -122.9574)
        report = AvalancheReport(coords)

        # Should have a provider for Whistler
        assert report.has_data() is True

    def test_has_data_without_provider(self):
        """Test has_data returns False when no provider."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        assert report.has_data() is False

    def test_get_forecast_no_provider(self):
        """Test get_forecast returns error message when no provider."""
        coords = (19.4326, -99.1332)  # Mexico City
        report = AvalancheReport(coords)

        result = report.get_forecast()
        # Returns error message string when no provider
        assert isinstance(result, str)
        assert 'not available' in result.lower()


class TestAvalancheProviderBase:
    """Test base class functionality."""

    def test_load_geodata_success(self):
        """Test _load_geodata with successful load."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        # Create a minimal concrete provider for testing
        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)

        # Mock a successful geodata load
        mock_gdf = Mock()
        result = provider._load_geodata(lambda: mock_gdf)

        assert result == mock_gdf

    def test_load_geodata_file_not_found(self, caplog):
        """Test _load_geodata handles FileNotFoundError."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)

        # Test FileNotFoundError handling
        def raise_file_not_found():
            raise FileNotFoundError("File not found")

        result = provider._load_geodata(raise_file_not_found)

        assert result is None
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == 'WARNING'
        assert 'Geospatial data file not found' in caplog.records[0].message

    def test_load_geodata_import_error(self, caplog):
        """Test _load_geodata handles ImportError."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)

        # Test ImportError handling
        def raise_import_error():
            raise ImportError("geopandas not available")

        result = provider._load_geodata(raise_import_error)

        assert result is None
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == 'WARNING'
        assert 'geopandas not available' in caplog.records[0].message

    def test_calculate_distances_no_gdf(self):
        """Test _calculate_distances with no regions_gdf."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)
        provider.regions_gdf = None

        result = provider._calculate_distances((50.0, -122.0))
        assert result is None

    def test_distance_from_region_no_gdf(self):
        """Test distance_from_region returns inf when no regions_gdf."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)
        provider.regions_gdf = None

        result = provider.distance_from_region((50.0, -122.0))
        assert result == float('inf')

    def test_distance_from_region_exact_match(self):
        """Test distance_from_region returns None for exact match."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)

        # Mock regions_gdf with a point that contains the coords
        mock_gdf = Mock()
        mock_gdf.contains.return_value.any.return_value = True
        provider.regions_gdf = mock_gdf

        result = provider.distance_from_region((50.0, -122.0))
        assert result is None

    def test_distance_from_region_within_buffer(self):
        """Test distance_from_region returns distance when within buffer."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)

        # Mock regions_gdf
        mock_gdf = Mock()
        mock_gdf.contains.return_value.any.return_value = False

        # Mock _calculate_distances to return a GDF with distance
        mock_distances_gdf = Mock()
        mock_distance_series = Mock()
        mock_distance_series.min.return_value = 5000  # 5km in meters
        mock_distances_gdf.__getitem__ = Mock(return_value=mock_distance_series)

        with patch.object(provider, '_calculate_distances', return_value=mock_distances_gdf):
            provider.regions_gdf = mock_gdf

            result = provider.distance_from_region((50.0, -122.0))

            # Should return distance in km (5000m = 5km)
            # Buffer limit from config is 20km, so should return 5.0
            assert result == 5.0

    def test_distance_from_region_beyond_buffer(self):
        """Test distance_from_region returns inf when beyond buffer."""
        config = AvalancheProviderConfig(
            class_name='TestProvider',
            api_url='https://api.example.com',
            cache_timeout=3600
        )

        class TestProvider(AvalancheProvider):
            def get_forecast(self, coords):
                return None

            def out_of_range(self, coords):
                return True

        provider = TestProvider(config)

        # Mock regions_gdf
        mock_gdf = Mock()
        mock_gdf.contains.return_value.any.return_value = False

        # Mock _calculate_distances to return a GDF with large distance
        mock_distances_gdf = Mock()
        mock_distance_series = Mock()
        mock_distance_series.min.return_value = 50000  # 50km in meters
        mock_distances_gdf.__getitem__ = Mock(return_value=mock_distance_series)

        with patch.object(provider, '_calculate_distances', return_value=mock_distances_gdf):
            provider.regions_gdf = mock_gdf

            result = provider.distance_from_region((50.0, -122.0))

            # Should return inf (beyond 20km buffer)
            assert result == float('inf')
