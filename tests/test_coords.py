"""Integration tests for fire finding at specific coordinates."""

import pytest
from app.fires import FindFires


class TestFireLocationBasics:
    """Basic fire finding at known locations."""

    def test_lillooet_bc(self):
        """Test Lillooet, BC - known fire location."""
        coords = (50.7021714,-121.9725246)
        ff = FindFires(coords)
        fires = ff.nearby(filters={'status': 'all', 'distance': 20})

        assert len(fires) == 1
        assert ff.out_of_range() is False

    def test_manning_park_bc(self):
        """Test Manning Park, BC - known fire location."""
        coords = (49.064646, -120.7919022)
        ff = FindFires(coords)
        fires = ff.nearby(filters={'status': 'all', 'distance': 50})

        assert len(fires) == 3
        assert ff.out_of_range() is False


class TestBorderCases:
    """Test coordinates near provincial/national borders."""

    def test_jasper_bc_border(self):
        """Test BC/AB border west of Jasper - should find fires from both provinces."""
        # Coordinates on BC border west of Jasper
        coords = (53.012807, -118.649372)
        ff = FindFires(coords)
        fires = ff.nearby(filters={'status': 'all', 'distance': 70})

        # Should find fires from both AB and BC
        assert len(fires) == 2
        assert ff.out_of_range() is False

    def test_waterton_lakes_park(self):
        """Test Waterton Lakes National Park - multiple nearby fires."""
        coords = (49.0500, -113.9103)  # Waterton townsite
        ff = FindFires(coords)
        fires = ff.nearby(filters={'status': 'all', 'distance': 50})

        assert len(fires) == 3
        assert ff.out_of_range() is False


class TestOverlappingPerimeters:
    """Test locations with overlapping fire perimeters."""

    def test_overlapping_fires(self):
        """Test coordinates in overlapping fire perimeters."""
        coords = (58.164245, -121.038954)
        ff = FindFires(coords)
        # Distance = 0 as the coordinates are directly in a perimeter.
        fires = ff.nearby(filters={'status': 'all', 'distance': 0})

        # Should find multiple overlapping fires
        assert len(fires) == 2

        for fire in fires:
            assert fire['Distance'] == 0.0


class TestFilterBehavior:
    """Test filtering behavior at different locations."""

    def test_active_filter_reduces_results(self):
        """Test that active filter reduces or maintains result count."""
        # In manning park. 3 fires, 1 holding, 1 out of control, 1 out.
        coords = (49.078353, -121.012207)
        ff = FindFires(coords)

        all_fires = ff.nearby(filters={'status': 'all', 'distance': 30})
        active_fires = ff.nearby(filters={'status': 'active', 'distance': 30})

        # Active filter should never increase results
        assert len(active_fires) <= len(all_fires)

    def test_distance_filter_reduces_results(self):
        """Test that smaller distance reduces or maintains results."""
        coords = (49.064646, -120.7919022)
        ff = FindFires(coords)

        fires_25km = ff.nearby(filters={'status': 'all', 'distance': 25})
        fires_50km = ff.nearby(filters={'status': 'all', 'distance': 50})

        # Smaller radius should never increase results
        assert len(fires_25km) <= len(fires_50km)

    def test_combined_filters(self):
        """Test combining multiple filters."""
        coords = (51.398720, -116.491640)
        ff = FindFires(coords)

        # Test that combining filters reduces results
        all_fires = ff.nearby(filters={'status': 'all', 'distance': 150})
        filtered_fires = ff.nearby(filters={'status': 'active', 'distance': 50})

        assert len(filtered_fires) <= len(all_fires)


class TestEdgeCases:
    """Test edge cases and out of range scenarios."""

    def test_out_of_range_pacific(self):
        """Test coordinates in middle of Pacific Ocean."""
        coords = (40.250308, -152.961979)
        ff = FindFires(coords)

        assert ff.out_of_range() is True

        fires = ff.nearby()
        assert len(fires) == 0

    def test_out_of_range_arctic(self):
        """Test coordinates far north (no fire data coverage)."""
        coords = (75.0, -100.0)
        ff = FindFires(coords)

        assert ff.out_of_range() is True

        fires = ff.nearby()
        assert len(fires) == 0

    def test_out_of_range_atlantic(self):
        """Test coordinates in Atlantic Ocean."""
        coords = (45.0, -30.0)
        ff = FindFires(coords)

        assert ff.out_of_range() is True

        fires = ff.nearby()
        assert len(fires) == 0


class TestDataSources:
    """Test that correct data sources are selected."""

    def test_bc_coordinates_use_bc_data(self):
        """Test that BC coordinates select BC data source."""
        coords = (49.064646, -120.7919022)  # Manning Park, BC
        ff = FindFires(coords)

        assert 'BC' in ff.sources

    def test_ab_coordinates_use_ab_data(self):
        """Test that AB coordinates select AB data source."""
        coords = (51.0447, -114.0719)  # Calgary, AB
        ff = FindFires(coords)

        assert 'AB' in ff.sources

    def test_border_coordinates_use_multiple_sources(self):
        """Test that border coordinates select multiple data sources."""
        coords = (52.8737, -118.0814)  # BC/AB border
        ff = FindFires(coords)

        # Should include both BC and AB sources
        assert len(ff.sources) >= 2
