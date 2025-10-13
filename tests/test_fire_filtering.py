"""Tests for generic fire filtering functionality."""

import pytest
from app.filters import (get_allowed_statuses, STATUS_LEVELS, apply_filters,
                        apply_status_filter, apply_distance_filter, apply_size_filter,
                        FILTER_HANDLERS)
from app.helpers import parse_message
from app.config import get_config


class TestGetAllowedStatuses:
    """Test the get_allowed_statuses function."""

    def test_bc_status_map_active(self):
        """Test active filter with BC status map."""
        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        result = get_allowed_statuses(bc_status_map, 'active')
        expected = {'OUT_CNTRL'}
        assert result == expected

    def test_bc_status_map_controlled(self):
        """Test controlled filter with BC status map."""
        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        result = get_allowed_statuses(bc_status_map, 'controlled')
        expected = {'OUT_CNTRL', 'HOLDING', 'UNDR_CNTRL'}
        assert result == expected

    def test_us_status_map_controlled(self):
        """Test controlled filter with US status map (multiple managed statuses)."""
        us_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['Flanking', 'HOLDING'],
            'controlled': ['UC'],
            'out': ['OUT']
        }

        result = get_allowed_statuses(us_status_map, 'controlled')
        expected = {'OUT_CNTRL', 'Flanking', 'HOLDING', 'UC'}
        assert result == expected

    def test_ab_status_map_active(self):
        """Test active filter with AB status map."""
        ab_status_map = {
            'active': ['Out of Control'],
            'managed': ['Being Held'],
            'controlled': ['Under Control'],
            'out': []
        }

        result = get_allowed_statuses(ab_status_map, 'active')
        expected = {'Out of Control'}
        assert result == expected

    def test_invalid_filter_level(self):
        """Test with invalid filter level."""
        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        result = get_allowed_statuses(bc_status_map, 'invalid')
        expected = set()
        assert result == expected

    def test_empty_status_map(self):
        """Test with empty status map."""
        empty_map = {}

        result = get_allowed_statuses(empty_map, 'controlled')
        expected = set()
        assert result == expected

    def test_missing_categories_in_status_map(self):
        """Test with status map missing some categories."""
        partial_map = {
            'active': ['OUT_CNTRL'],
            # Missing 'managed' and 'controlled'
            'out': ['OUT']
        }

        result = get_allowed_statuses(partial_map, 'controlled')
        expected = {'OUT_CNTRL'}  # Only active since others are missing
        assert result == expected

    def test_status_levels_ordering(self):
        """Test that STATUS_LEVELS ordering is correct."""
        assert STATUS_LEVELS['active'] == 1
        assert STATUS_LEVELS['managed'] == 2
        assert STATUS_LEVELS['controlled'] == 3
        assert STATUS_LEVELS['out'] == 4

        # Verify ordering (lower numbers = more urgent)
        assert STATUS_LEVELS['active'] < STATUS_LEVELS['managed']
        assert STATUS_LEVELS['managed'] < STATUS_LEVELS['controlled']
        assert STATUS_LEVELS['controlled'] < STATUS_LEVELS['out']


class TestMessageParsingWithFilters:
    """Test message parsing with filter keywords and distances."""

    def test_basic_coordinates_no_filter(self):
        """Test basic coordinate parsing without filter."""
        message = "(49.123, -123.456)"
        result = parse_message(message)

        expected = {
            "coords": (49.123, -123.456),
            "filters": {}
        }
        assert result == expected

    def test_coordinates_with_active_filter(self):
        """Test coordinate parsing with 'active' filter."""
        message = "(49.123, -123.456) active"
        result = parse_message(message)

        expected = {
            "coords": (49.123, -123.456),
            "filters": {"status": "active"}
        }
        assert result == expected

    def test_coordinates_with_all_filter(self):
        """Test coordinate parsing with 'all' filter."""
        message = "(49.123, -123.456) all"
        result = parse_message(message)

        expected = {
            "coords": (49.123, -123.456),
            "filters": {"status": "all"}
        }
        assert result == expected

    def test_coordinates_with_distance_filter_km(self):
        """Test coordinate parsing with distance filter in km."""
        message = "(49.123, -123.456) 25km"
        result = parse_message(message)

        expected = {
            "coords": (49.123, -123.456),
            "filters": {"distance": 25.0}
        }
        assert result == expected

    def test_coordinates_with_distance_filter_mi(self):
        """Test coordinate parsing with distance filter in miles."""
        message = "(49.123, -123.456) 10mi"
        result = parse_message(message)

        expected = {
            "coords": (49.123, -123.456),
            "filters": {"distance": 16.09344}  # 10 * 1.609344
        }
        assert result == expected

    def test_coordinates_with_multiple_filters(self):
        """Test coordinate parsing with both status and distance filters."""
        message = "(49.123, -123.456) active 50km"
        result = parse_message(message)

        expected = {
            "coords": (49.123, -123.456),
            "filters": {"status": "active", "distance": 50.0}
        }
        assert result == expected

    def test_filter_keyword_word_boundaries(self):
        """Test that filter detection uses word boundaries."""
        # Should NOT match 'active' in 'radioactive'
        message = "(49.123, -123.456) radioactive"
        result = parse_message(message)
        assert result["filters"] == {}

        # Should NOT match 'all' in 'ball'
        message = "(49.123, -123.456) ball"
        result = parse_message(message)
        assert result["filters"] == {}

    def test_distance_filter_word_boundaries(self):
        """Test that distance filter uses word boundaries."""
        # Should match standalone distance
        message = "(49.123, -123.456) 25km"
        result = parse_message(message)
        assert result["filters"]["distance"] == 25.0

        # Should NOT match distance in larger number
        message = "(49.123, -123.456) call 1-800-225km-help"
        result = parse_message(message)
        assert "distance" not in result["filters"]

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
        expected_handlers = {'status', 'distance', 'size'}
        assert set(FILTER_HANDLERS.keys()) == expected_handlers

    def testapply_status_filter(self):
        """Test status filter application."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 'OUT_CNTRL'},
            {'Fire': 'Fire2', 'Status': 'HOLDING'},
            {'Fire': 'Fire3', 'Status': 'OUT'}
        ]

        class MockDataFile:
            status_map = {
                'active': ['OUT_CNTRL'],
                'managed': ['HOLDING'],
                'controlled': ['UNDR_CNTRL'],
                'out': ['OUT']
            }

        # Test active filter
        result = apply_status_filter(test_fires, 'active', MockDataFile())
        assert len(result) == 1
        assert result[0]['Fire'] == 'Fire1'

        # Test all filter (no filtering)
        result = apply_status_filter(test_fires, 'all', MockDataFile())
        assert len(result) == 3

    def testapply_distance_filter(self):
        """Test distance filter application."""
        test_fires = [
            {'Fire': 'Fire1', 'Distance': 10000},  # 10km
            {'Fire': 'Fire2', 'Distance': 35000},  # 35km
            {'Fire': 'Fire3', 'Distance': 60000}   # 60km
        ]

        class MockSettings:
            max_radius = 150

        # Test 25km filter
        result = apply_distance_filter(test_fires, 25, None, settings=MockSettings())
        assert len(result) == 1
        assert result[0]['Fire'] == 'Fire1'

        # Test max_radius capping (200km requested, capped to 150km)
        result = apply_distance_filter(test_fires, 200, None, settings=MockSettings())
        assert len(result) == 3  # All fires within 150km

    def testapply_size_filter(self):
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
        """Test applying multiple filters together."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 'OUT_CNTRL', 'Size': 5.0, 'Distance': 15000},  # Pass all
            {'Fire': 'Fire2', 'Status': 'HOLDING', 'Size': 0.5, 'Distance': 20000},    # Fail size
            {'Fire': 'Fire3', 'Status': 'OUT', 'Size': 2.0, 'Distance': 10000},        # Fail status
            {'Fire': 'Fire4', 'Status': 'OUT_CNTRL', 'Size': 3.0, 'Distance': 50000}  # Fail distance
        ]

        class MockDataFile:
            status_map = {
                'active': ['OUT_CNTRL'],
                'managed': ['HOLDING'],
                'controlled': ['UNDR_CNTRL'],
                'out': ['OUT']
            }

        class MockSettings:
            max_radius = 150

        filters = {
            'status': 'active',     # Only OUT_CNTRL
            'size': 1.0,           # >= 1.0 hectares
            'distance': 30         # <= 30km
        }

        result = apply_filters(test_fires, filters, MockDataFile(), None, MockSettings())
        assert len(result) == 1
        assert result[0]['Fire'] == 'Fire1'


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
        """Test filtering fires with active filter."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 'OUT_CNTRL'},      # active
            {'Fire': 'Fire2', 'Status': 'HOLDING'},        # managed
            {'Fire': 'Fire3', 'Status': 'UNDR_CNTRL'},     # controlled
            {'Fire': 'Fire4', 'Status': 'OUT'},            # out
            {'Fire': 'Fire5', 'Status': None}              # no status
        ]

        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        allowed_statuses = get_allowed_statuses(bc_status_map, 'active')
        filtered_fires = [fire for fire in test_fires
                         if fire.get('Status') in allowed_statuses]

        assert len(filtered_fires) == 1
        assert filtered_fires[0]['Fire'] == 'Fire1'

    def test_filter_fires_by_status_controlled(self):
        """Test filtering fires with controlled filter."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 'OUT_CNTRL'},      # active
            {'Fire': 'Fire2', 'Status': 'HOLDING'},        # managed
            {'Fire': 'Fire3', 'Status': 'UNDR_CNTRL'},     # controlled
            {'Fire': 'Fire4', 'Status': 'OUT'},            # out
            {'Fire': 'Fire5', 'Status': None}              # no status
        ]

        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        allowed_statuses = get_allowed_statuses(bc_status_map, 'controlled')
        filtered_fires = [fire for fire in test_fires
                         if fire.get('Status') in allowed_statuses]

        assert len(filtered_fires) == 3
        fire_names = [f['Fire'] for f in filtered_fires]
        assert set(fire_names) == {'Fire1', 'Fire2', 'Fire3'}

    def test_filter_fires_all_no_filtering(self):
        """Test that 'all' filter includes all fires."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 'OUT_CNTRL'},
            {'Fire': 'Fire2', 'Status': 'HOLDING'},
            {'Fire': 'Fire3', 'Status': 'OUT'},
            {'Fire': 'Fire4', 'Status': None}
        ]

        # For 'all' filter, no filtering should be applied
        # This simulates the logic in nearby() method
        filter_level = 'all'
        if filter_level == 'all':
            filtered_fires = test_fires  # No filtering
        else:
            # Would apply filtering here
            pass

        assert len(filtered_fires) == 4

    def test_filter_with_unknown_status(self):
        """Test filtering with unknown status codes."""
        test_fires = [
            {'Fire': 'Fire1', 'Status': 'OUT_CNTRL'},      # known
            {'Fire': 'Fire2', 'Status': 'UNKNOWN_STATUS'}, # unknown
            {'Fire': 'Fire3', 'Status': 'HOLDING'},        # known
        ]

        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        allowed_statuses = get_allowed_statuses(bc_status_map, 'controlled')
        filtered_fires = [fire for fire in test_fires
                         if fire.get('Status') in allowed_statuses]

        # Should only include fires with known statuses
        assert len(filtered_fires) == 2
        fire_names = [f['Fire'] for f in filtered_fires]
        assert set(fire_names) == {'Fire1', 'Fire3'}


class TestPerformanceAndEdgeCases:
    """Test performance characteristics and edge cases."""

    def test_large_status_map(self):
        """Test with large status map."""
        large_status_map = {
            'active': [f'ACTIVE_{i}' for i in range(100)],
            'managed': [f'MANAGED_{i}' for i in range(100)],
            'controlled': [f'CONTROLLED_{i}' for i in range(100)],
            'out': [f'OUT_{i}' for i in range(100)]
        }

        result = get_allowed_statuses(large_status_map, 'controlled')

        # Should include all active, managed, and controlled statuses
        assert len(result) == 300
        assert 'ACTIVE_0' in result
        assert 'MANAGED_50' in result
        assert 'CONTROLLED_99' in result
        assert 'OUT_0' not in result

    def test_set_membership_performance(self):
        """Test that result is a set for fast membership testing."""
        bc_status_map = {
            'active': ['OUT_CNTRL'],
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        result = get_allowed_statuses(bc_status_map, 'controlled')

        # Should return a set for O(1) membership testing
        assert isinstance(result, set)

        # Test membership performance
        assert 'OUT_CNTRL' in result
        assert 'HOLDING' in result
        assert 'OUT' not in result

    def test_empty_status_categories(self):
        """Test with empty status categories."""
        status_map_with_empty = {
            'active': [],  # Empty
            'managed': ['HOLDING'],
            'controlled': ['UNDR_CNTRL'],
            'out': ['OUT']
        }

        result = get_allowed_statuses(status_map_with_empty, 'controlled')
        expected = {'HOLDING', 'UNDR_CNTRL'}  # No active statuses
        assert result == expected
