"""Tests for generic fire filtering functionality."""

import pytest
from app.filters import (STATUS_LEVELS, apply_filters,
                        apply_status_filter, apply_size_filter,
                        FILTER_HANDLERS)
from app.helpers import parse_message
from app.config import get_config


class TestMessageParsingWithFilters:
    """Test message parsing with filter keywords and distances."""

    def test_basic_coordinates_no_filter(self):
        """Test basic coordinate parsing without filter."""
        message = "(49.123, -123.456)"
        result = parse_message(message)

        # Check only the fields we care about
        assert result["coords"] == (49.123, -123.456)
        # parse_message adds default distance from config, so check for that
        assert "distance" in result["filters"]

    def test_coordinates_with_active_filter(self):
        """Test coordinate parsing with 'active' filter."""
        message = "(49.123, -123.456) active"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["filters"]["status"] == "active"

    def test_coordinates_with_all_filter(self):
        """Test coordinate parsing with 'all' filter."""
        message = "(49.123, -123.456) all"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["filters"]["status"] == "all"

    def test_coordinates_with_distance_filter_km(self):
        """Test coordinate parsing with distance filter in km."""
        message = "(49.123, -123.456) 25km"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["filters"]["distance"] == 25.0

    def test_coordinates_with_distance_filter_mi(self):
        """Test coordinate parsing with distance filter in miles."""
        message = "(49.123, -123.456) 10mi"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["filters"]["distance"] == 16.09344  # 10 * 1.609344

    def test_coordinates_with_multiple_filters(self):
        """Test coordinate parsing with both status and distance filters."""
        message = "(49.123, -123.456) active 50km"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["filters"]["status"] == "active"
        assert result["filters"]["distance"] == 50.0

    def test_filter_keyword_word_boundaries(self):
        """Test that filter detection uses word boundaries."""
        # Should NOT match 'active' in 'radioactive'
        message = "(49.123, -123.456) radioactive"
        result = parse_message(message)
        assert "status" not in result["filters"]

        # Should NOT match 'all' in 'ball'
        message = "(49.123, -123.456) ball"
        result = parse_message(message)
        assert "status" not in result["filters"]

    def test_distance_filter_word_boundaries(self):
        """Test that distance filter uses word boundaries."""
        # Should match standalone distance
        message = "(49.123, -123.456) 25km"
        result = parse_message(message)
        assert result["filters"]["distance"] == 25.0

        # Should NOT match distance in larger number (but default distance will be added)
        message = "(49.123, -123.456) call 1-800-225km-help"
        result = parse_message(message)
        # parse_message adds default distance from config, so just check it's the default
        assert result["filters"]["distance"] == 50  # default from config

    def test_filter_case_insensitive(self):
        """Test that filter detection is case insensitive."""
        message = "(49.123, -123.456) ACTIVE 25KM"
        result = parse_message(message)
        assert result["filters"]["status"] == "active"
        assert result["filters"]["distance"] == 25.0

        message = "(49.123, -123.456) All 10MI"
        result = parse_message(message)
        assert result["filters"]["status"] == "all"
        assert result["filters"]["distance"] == 16.09344

    def test_multiple_filter_keywords_precedence(self):
        """Test precedence when multiple filter keywords are present."""
        # 'active' should take precedence over 'all'
        message = "(49.123, -123.456) active all"
        result = parse_message(message)
        assert result["filters"]["status"] == "active"

    def test_no_coordinates_found(self):
        """Test when no coordinates are found."""
        message = "active all 25km"
        result = parse_message(message)
        assert result is None


class TestGenericFilterSystem:
    """Test the new generic filtering system."""

    def test_filter_handlers_registry(self):
        """Test that all filter handlers are registered."""
        # Distance filtering now happens in search(), not as a separate filter
        expected_handlers = {'status', 'size'}
        assert set(FILTER_HANDLERS.keys()) == expected_handlers

    def test_apply_status_filter(self):
        """Test status filter application with numeric levels."""
        # Status is now a numeric level (1=active, 2=managed, 3=controlled, 4=out)
        test_fires = [
            {'Fire': 'Fire1', 'Status': 1},  # active
            {'Fire': 'Fire2', 'Status': 2},  # managed
            {'Fire': 'Fire3', 'Status': 4}   # out
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        # Test active filter (level <= 1)
        result = apply_status_filter(test_fires, 'active', MockDataFile())
        assert len(result) == 1
        assert result[0]['Fire'] == 'Fire1'

        # Test controlled filter (level <= 3)
        result = apply_status_filter(test_fires, 'controlled', MockDataFile())
        assert len(result) == 2
        assert set(f['Fire'] for f in result) == {'Fire1', 'Fire2'}

        # Test all filter (no filtering)
        result = apply_status_filter(test_fires, 'all', MockDataFile())
        assert len(result) == 3

    def test_apply_size_filter(self):
        """Test size filter application."""
        test_fires = [
            {'Fire': 'Fire1', 'Size': 5.5},   # Above threshold
            {'Fire': 'Fire2', 'Size': 0.5},   # Below threshold
            {'Fire': 'Fire3', 'Size': 10.0},  # Above threshold
            {'Fire': 'Fire4', 'Size': None},  # No size data
            {'Fire': 'Fire5', 'Size': 'invalid'}  # Invalid size
        ]

        # Test 1.0 hectare minimum
        result = apply_size_filter(test_fires, 1.0, None)
        assert len(result) == 2
        fire_names = [f['Fire'] for f in result]
        assert set(fire_names) == {'Fire1', 'Fire3'}

    def test_apply_filters_multiple(self):
        """Test applying multiple filters together (status + size only)."""
        # Status is now numeric level, distance filtering happens in search()
        test_fires = [
            {'Fire': 'Fire1', 'Status': 1, 'Size': 5.0},   # Pass both
            {'Fire': 'Fire2', 'Status': 2, 'Size': 0.5},   # Fail size
            {'Fire': 'Fire3', 'Status': 4, 'Size': 2.0},   # Fail status
            {'Fire': 'Fire4', 'Status': 1, 'Size': 3.0}    # Pass both
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        class MockSettings:
            max_radius = 150

        filters = {
            'status': 'active',  # Level <= 1
            'size': 1.0,         # >= 1.0 hectares
        }

        result = apply_filters(test_fires, filters, MockDataFile(), None, MockSettings())
        assert len(result) == 2
        assert set(f['Fire'] for f in result) == {'Fire1', 'Fire4'}


class TestConfigurationIntegration:
    """Test integration with updated configuration."""

    def test_new_configuration_fields(self):
        """Test that new configuration fields are loaded correctly."""
        config = get_config()

        # Test existing fields
        assert hasattr(config, 'fire_radius')
        assert config.fire_radius == 50

        # Test new fields
        assert hasattr(config, 'max_radius')
        assert config.max_radius == 150

        assert hasattr(config, 'fire_status')
        assert config.fire_status == 'controlled'

        assert hasattr(config, 'fire_size')
        assert config.fire_size == 1


class TestFireFilteringIntegration:
    """Integration tests for fire filtering functionality."""

    def test_filter_fires_by_status_active(self):
        """Test filtering fires with active filter (numeric levels)."""
        # Status is now numeric: 1=active, 2=managed, 3=controlled, 4=out
        test_fires = [
            {'Fire': 'Fire1', 'Status': 1},     # active
            {'Fire': 'Fire2', 'Status': 2},     # managed
            {'Fire': 'Fire3', 'Status': 3},     # controlled
            {'Fire': 'Fire4', 'Status': 4},     # out
            {'Fire': 'Fire5', 'Status': None}   # no status (filtered out)
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        filtered_fires = apply_status_filter(test_fires, 'active', MockDataFile())

        assert len(filtered_fires) == 1
        assert filtered_fires[0]['Fire'] == 'Fire1'

    def test_filter_fires_by_status_controlled(self):
        """Test filtering fires with controlled filter (numeric levels)."""
        # Status is now numeric: 1=active, 2=managed, 3=controlled, 4=out
        test_fires = [
            {'Fire': 'Fire1', 'Status': 1},     # active
            {'Fire': 'Fire2', 'Status': 2},     # managed
            {'Fire': 'Fire3', 'Status': 3},     # controlled
            {'Fire': 'Fire4', 'Status': 4},     # out
            {'Fire': 'Fire5', 'Status': None}   # no status (filtered out)
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        filtered_fires = apply_status_filter(test_fires, 'controlled', MockDataFile())

        assert len(filtered_fires) == 3
        fire_names = [f['Fire'] for f in filtered_fires]
        assert set(fire_names) == {'Fire1', 'Fire2', 'Fire3'}

    def test_filter_fires_all_no_filtering(self):
        """Test that 'all' filter includes all fires."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 1},
            {'Fire': 'Fire2', 'Status': 2},
            {'Fire': 'Fire3', 'Status': 4},
            {'Fire': 'Fire4', 'Status': None}
        ]

        class MockDataFile:
            status_map = {}

        # 'all' filter should return all fires
        filtered_fires = apply_status_filter(test_fires, 'all', MockDataFile())
        assert len(filtered_fires) == 4

    def test_filter_with_no_status_level(self):
        """Test filtering fires with missing status levels."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 1},     # known
            {'Fire': 'Fire2', 'Status': None},  # no status (treated as inf)
            {'Fire': 'Fire3', 'Status': 2},     # known
        ]

        class MockDataFile:
            status_map = {}

        filtered_fires = apply_status_filter(test_fires, 'controlled', MockDataFile())

        # Should only include fires with status levels <= 3
        # None is treated as float('inf') so it's filtered out
        assert len(filtered_fires) == 2
        fire_names = [f['Fire'] for f in filtered_fires]
        assert set(fire_names) == {'Fire1', 'Fire3'}
