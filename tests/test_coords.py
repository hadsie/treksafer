"""Integration tests for fire finding at specific coordinates."""

import pytest
from app.fires import FindFires
from app.config import get_config


class TestFireLocationBasics:
    """Basic fire finding at known locations."""

    def test_lillooet_bc(self, mock_bc_fire_api):
        """Test Lillooet, BC - known fire location."""
        coords = (50.7021714,-121.9725246)
        ff = FindFires(coords, filters={'status': 'all', 'distance': 20})
        fires = ff.nearby()

        assert len(fires) == 1
        assert isinstance(fires[0]['Status'], str), "Status should be a string, not a numeric level"
        assert ff.out_of_range() is False

    def test_manning_park_bc(self, mock_bc_fire_api):
        """Test Manning Park, BC - known fire location. 'all' includes the sub-hectare fire."""
        coords = (49.064646, -120.7919022)
        ff = FindFires(coords, filters={'status': 'all', 'distance': 50})
        fires = ff.nearby()

        assert len(fires) == 4
        assert ff.out_of_range() is False


class TestBorderCases:
    """Test coordinates near provincial/national borders."""

    def test_jasper_bc_border(self, mock_bc_fire_api):
        """Test BC/AB border west of Jasper - should find fires from both provinces."""
        # Coordinates on BC border west of Jasper
        coords = (53.012807, -118.649372)
        ff = FindFires(coords, filters={'status': 'all', 'distance': 70})
        fires = ff.nearby()

        # Should find fires from both AB and BC
        assert len(fires) == 2
        assert ff.out_of_range() is False

    def test_waterton_lakes_park(self):
        """Test Waterton Lakes National Park - multiple nearby fires."""
        coords = (49.0500, -113.9103)  # Waterton townsite
        ff = FindFires(coords, filters={'status': 'all', 'distance': 50})
        fires = ff.nearby()

        assert len(fires) == 3
        assert ff.out_of_range() is False


class TestOverlappingPerimeters:
    """Test locations with overlapping fire perimeters."""

    def test_overlapping_fires(self, mock_bc_fire_api):
        """Test coordinates in overlapping fire perimeters."""
        coords = (58.164245, -121.038954)
        # Distance = 0 as the coordinates are directly in a perimeter.
        ff = FindFires(coords, filters={'status': 'all', 'distance': 0})
        fires = ff.nearby()

        # Should find multiple overlapping fires
        assert len(fires) == 2

        for fire in fires:
            assert fire['Distance'] == 0.0


class TestFilterBehavior:
    """Test filtering behavior at different locations."""

    def test_active_filter_reduces_results(self, mock_bc_fire_api):
        """Test that active filter reduces or maintains result count."""
        # In manning park. 3 fires, 1 holding, 1 out of control, 1 out.
        coords = (49.078353, -121.012207)

        ff_all = FindFires(coords, filters={'status': 'all', 'distance': 30, 'size': 0})
        all_fires = ff_all.nearby()

        ff_active = FindFires(coords, filters={'status': 'active', 'distance': 30, 'size': 0})
        active_fires = ff_active.nearby()

        # Active filter should never increase results
        assert len(active_fires) < len(all_fires)

    def test_distance_filter_reduces_results(self, mock_bc_fire_api):
        """Test that smaller distance reduces or maintains results."""
        coords = (49.064646, -120.7919022)

        ff_25 = FindFires(coords, filters={'status': 'all', 'distance': 25})
        fires_25km = ff_25.nearby()

        ff_50 = FindFires(coords, filters={'status': 'all', 'distance': 50})
        fires_50km = ff_50.nearby()

        # Smaller radius should never increase results
        assert len(fires_25km) < len(fires_50km)

    def test_combined_filters(self, mock_bc_fire_api):
        """Test combining multiple filters."""
        coords = (51.398720, -116.491640)

        # Test that combining filters reduces results
        ff_all = FindFires(coords, filters={'status': 'all', 'distance': 150})
        all_fires = ff_all.nearby()

        ff_filtered = FindFires(coords, filters={'status': 'active', 'distance': 50})
        filtered_fires = ff_filtered.nearby()

        assert len(filtered_fires) < len(all_fires)

    def test_max_radius(self, mock_bc_fire_api):
        """Test that max_radius is enforced."""
        coords = (49.078353, -121.012207)
        config = get_config()

        # Test that combining filters reduces results
        ff_max = FindFires(coords, filters={'status': 'all', 'distance': config.max_radius})
        all_fires = ff_max.nearby()

        ff_beyond = FindFires(coords, filters={'status': 'all', 'distance': config.max_radius * 1000})
        outside_of_max_fires = ff_beyond.nearby()

        assert len(outside_of_max_fires) == len(all_fires)

class TestEdgeCases:
    """Test edge cases and out of range scenarios."""

    def test_out_of_range_pacific(self):
        """Test coordinates in middle of Pacific Ocean."""
        coords = (40.250308, -152.961979)
        ff = FindFires(coords)

        assert ff.out_of_range() is True

        fires = ff.nearby()
        assert len(fires) == 0

    def test_northern_canada(self):
        """Test coordinates far north."""
        coords = (75.0, -100.0)
        ff = FindFires(coords)

        # All of Canada has coverage.
        assert ff.out_of_range() is False

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

    def test_bc_coordinates_use_bc_data(self, mock_bc_fire_api):
        """Test that BC coordinates select BC data source."""
        coords = (49.064646, -120.7919022)  # Manning Park, BC
        ff = FindFires(coords)

        assert 'BC' in ff.sources
        assert 'CA' in ff.sources

        assert 'AB' not in ff.sources   # AB is further than max_radius away.

    def test_ab_coordinates_use_ab_data(self):
        """Test that AB coordinates select AB data source."""
        coords = (51.0447, -114.0719)  # Calgary, AB
        ff = FindFires(coords)

        assert 'AB' in ff.sources
        assert 'BC' in ff.sources
        assert 'CA' in ff.sources
        assert 'US' not in ff.sources # US is further than max_radius away.

    def test_border_coordinates_use_multiple_sources(self):
        """Test that border coordinates select multiple data sources."""
        coords = (52.8737, -118.0814)  # BC/AB border
        ff = FindFires(coords)

        # Should include BC, AB, and CA sources
        assert len(ff.sources) == 3


class TestCanadaWideSource:
    """Integration tests for the national (CA) fire source.

    The CA fixtures sit in boreal Quebec, far from the BC/AB fixtures, so this
    region is covered only by the national source.
    """

    QUEBEC_COORDS = (48.50, -72.00)  # Inside the CA fixture region

    def test_quebec_fires_come_from_ca_source_only(self):
        """Quebec coords load fires from CA without pulling in BC/AB."""
        ff = FindFires(self.QUEBEC_COORDS, filters={'status': 'all', 'distance': 50})
        fires = ff.nearby()

        assert 'CA' in ff.sources
        assert 'BC' not in ff.sources
        assert 'AB' not in ff.sources

        # All four CA fixture fires fall within 50km.
        assert len(fires) == 4
        # CA has no API enrichment, so Status is the raw stage-of-control code.
        assert all(f['Status'] in {'OC', 'BH', 'UC'} for f in fires)

    def test_ca_active_filter_keeps_out_of_control_only(self):
        """The active filter keeps only out-of-control (OC) CA fires."""
        ff = FindFires(self.QUEBEC_COORDS, filters={'status': 'active', 'distance': 50})
        fires = ff.nearby()

        # Two of the four fixture fires are OC.
        assert len(fires) == 2
        assert all(f['Status'] == 'OC' for f in fires)


class TestUSSource:
    """Integration tests for the US (WFIGS) source.

    WFIGS has no stage-of-control field, so status is derived from the numeric
    attr_PercentContained. The fixture (Wyoming) is far from all Canadian
    fixtures, so this region is covered only by the US source. It is stored as a
    FileGDB to preserve the real WFIGS field names the mapping depends on.
    """

    US_COORDS = (44.0, -110.0)  # Inside the US fixture cluster

    def test_us_status_derived_from_percent_contained(self):
        """Status is a human-readable string derived from percent contained."""
        ff = FindFires(self.US_COORDS, filters={'status': 'all', 'distance': 50, 'size': 0})
        fires = ff.nearby()

        assert 'US' in ff.sources
        assert len(fires) == 6
        statuses = {f['Status'] for f in fires}
        assert 'Contained' in statuses       # 100% contained
        assert 'Uncontained' in statuses     # 0% contained
        assert '60% contained' in statuses   # partial
        assert 'Active' in statuses          # unknown (null) percent contained

    def test_us_active_filter_excludes_fully_contained(self):
        """The active filter keeps only fires that are not fully contained."""
        ff = FindFires(self.US_COORDS, filters={'status': 'active', 'distance': 50, 'size': 0})
        fires = ff.nearby()

        # Four fixture fires are not fully contained (0%, 60%, 30%, unknown).
        assert len(fires) == 4
        assert all(f['Status'] != 'Contained' for f in fires)

    def test_us_unknown_containment_not_hidden_by_default_filter(self):
        """A fire with no percent-contained value is treated as active, never hidden."""
        # Default status filter is 'controlled', which excludes 'out'.
        ff = FindFires(self.US_COORDS, filters={'distance': 50, 'size': 0})
        fires = ff.nearby()

        wy06 = next((f for f in fires if f['Fire'] == 'WY06'), None)
        assert wy06 is not None, "fire with unknown containment must still be shown"
        assert wy06['Status'] == 'Active'

    def test_us_size_filter_uses_hectares(self):
        """acres_to_hectares runs before the size filter, dropping the sub-hectare fire."""
        ff = FindFires(self.US_COORDS, filters={'status': 'all', 'distance': 50, 'size': 1})
        fires = ff.nearby()

        # The 2-acre (~0.81 ha) fire falls below the 1 ha minimum; the other five remain.
        assert len(fires) == 5
        assert all(f['Fire'] != 'WY05' for f in fires)
