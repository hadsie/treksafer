"""Tests for generic fire filtering functionality."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.filters import (STATUS_LEVELS, apply_filters,
                        apply_status_filter, apply_size_filter,
                        FILTER_HANDLERS)
from app.fires import FindFires
from app.fires.find import _resolve_status
from app.config import DataFile
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
        assert "distance" in result["fire_filters"]

    def test_coordinates_with_active_filter(self):
        """Test coordinate parsing with 'active' filter."""
        message = "(49.123, -123.456) active"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["fire_filters"]["status"] == "active"

    def test_coordinates_with_all_filter(self):
        """Test coordinate parsing with 'all' filter."""
        message = "(49.123, -123.456) all"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["fire_filters"]["status"] == "all"

    def test_coordinates_with_distance_filter_km(self):
        """Test coordinate parsing with distance filter in km."""
        message = "(49.123, -123.456) 25km"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["fire_filters"]["distance"] == 25.0

    def test_coordinates_with_distance_filter_mi(self):
        """Test coordinate parsing with distance filter in miles."""
        message = "(49.123, -123.456) 10mi"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["fire_filters"]["distance"] == 16.09344  # 10 * 1.609344

    def test_coordinates_with_multiple_filters(self):
        """Test coordinate parsing with both status and distance filters."""
        message = "(49.123, -123.456) active 50km"
        result = parse_message(message)

        assert result["coords"] == (49.123, -123.456)
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 50.0

    def test_filter_keyword_word_boundaries(self):
        """Test that filter detection uses word boundaries."""
        # Should NOT match 'active' in 'radioactive'
        message = "(49.123, -123.456) radioactive"
        result = parse_message(message)
        assert "status" not in result["fire_filters"]

        # Should NOT match 'all' in 'ball'
        message = "(49.123, -123.456) ball"
        result = parse_message(message)
        assert "status" not in result["fire_filters"]

    def test_distance_filter_word_boundaries(self):
        """Test that distance filter uses word boundaries."""
        # Should match standalone distance
        message = "(49.123, -123.456) 25km"
        result = parse_message(message)
        assert result["fire_filters"]["distance"] == 25.0

        # Should NOT match distance in larger number (but default distance will be added)
        message = "(49.123, -123.456) call 1-800-225km-help"
        result = parse_message(message)
        # parse_message adds default distance from config, so just check it's the default
        assert result["fire_filters"]["distance"] == 50  # default from config

    def test_filter_case_insensitive(self):
        """Test that filter detection is case insensitive."""
        message = "(49.123, -123.456) ACTIVE 25KM"
        result = parse_message(message)
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 25.0

        message = "(49.123, -123.456) All 10MI"
        result = parse_message(message)
        assert result["fire_filters"]["status"] == "all"
        assert result["fire_filters"]["distance"] == 16.09344

    def test_multiple_filter_keywords_precedence(self):
        """Test precedence when multiple filter keywords are present."""
        # 'active' should take precedence over 'all'
        message = "(49.123, -123.456) active all"
        result = parse_message(message)
        assert result["fire_filters"]["status"] == "active"

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
        # StatusLevel is now a numeric level (1=active, 2=managed, 3=controlled, 4=out)
        test_fires = [
            {'Fire': 'Fire1', 'StatusLevel': 1},  # active
            {'Fire': 'Fire2', 'StatusLevel': 2},  # managed
            {'Fire': 'Fire3', 'StatusLevel': 4}   # out
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        # Test active filter (level <= 1)
        result = apply_status_filter(test_fires, 'active')
        assert len(result) == 1
        assert result[0]['Fire'] == 'Fire1'

        # Test controlled filter (level <= 3)
        result = apply_status_filter(test_fires, 'controlled')
        assert len(result) == 2
        assert set(f['Fire'] for f in result) == {'Fire1', 'Fire2'}

        # Test all filter (no filtering)
        result = apply_status_filter(test_fires, 'all')
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
        result = apply_size_filter(test_fires, 1.0)
        assert len(result) == 2
        fire_names = [f['Fire'] for f in result]
        assert set(fire_names) == {'Fire1', 'Fire3'}

    def test_size_filter_exempts_new_fires(self):
        """Fires discovered within the new-fire window bypass the size minimum."""
        now = datetime.now(timezone.utc)

        class MockSettings:
            new_fire_age_days = 7

        test_fires = [
            {'Fire': 'NewSmall', 'Size': 0.01, 'Discovered': now - timedelta(days=2)},
            {'Fire': 'NewNoSize', 'Discovered': now - timedelta(days=1)},
            {'Fire': 'OldSmall', 'Size': 0.01, 'Discovered': now - timedelta(days=8)},
            {'Fire': 'OldNoDate', 'Size': 0.01},
            {'Fire': 'OldBig', 'Size': 50.0, 'Discovered': now - timedelta(days=30)},
        ]

        result = apply_size_filter(test_fires, 1.0, settings=MockSettings())
        assert set(f['Fire'] for f in result) == {'NewSmall', 'NewNoSize', 'OldBig'}

    def test_size_filter_falls_back_to_first_seen(self):
        """Fires whose source publishes no discovery date use their first
        appearance in the fetch history for the new-fire exemption."""
        now = datetime.now(timezone.utc)

        class MockSettings:
            new_fire_age_days = 7

        test_fires = [
            {'Fire': 'NewSmall', 'Size': 0.5, 'FirstSeen': now - timedelta(days=2)},
            {'Fire': 'OldSmall', 'Size': 0.5, 'FirstSeen': now - timedelta(days=9)},
            {'Fire': 'NoHistorySmall', 'Size': 0.5},
        ]

        result = apply_size_filter(test_fires, 1.0, settings=MockSettings())
        assert [f['Fire'] for f in result] == ['NewSmall']

    def test_agency_discovered_outranks_first_seen(self):
        """A fire the agency dates outside the window is not exempted by a
        recent first appearance (e.g. a fire that entered our history late)."""
        now = datetime.now(timezone.utc)

        class MockSettings:
            new_fire_age_days = 7

        test_fires = [
            {'Fire': 'OldFire', 'Size': 0.5,
             'Discovered': now - timedelta(days=30),
             'FirstSeen': now - timedelta(days=1)},
        ]

        assert apply_size_filter(test_fires, 1.0, settings=MockSettings()) == []

    def test_size_filter_without_settings_has_no_exemption(self):
        """Direct calls without settings keep plain size semantics."""
        test_fires = [
            {'Fire': 'NewSmall', 'Size': 0.01,
             'Discovered': datetime.now(timezone.utc) - timedelta(days=1)},
        ]

        result = apply_size_filter(test_fires, 1.0)
        assert result == []

    def test_apply_filters_multiple(self):
        """Test applying multiple filters together (status + size only)."""
        # StatusLevel is now numeric level, distance filtering happens in search()
        test_fires = [
            {'Fire': 'Fire1', 'StatusLevel': 1, 'Size': 5.0},   # Pass both
            {'Fire': 'Fire2', 'StatusLevel': 2, 'Size': 0.5},   # Fail size
            {'Fire': 'Fire3', 'StatusLevel': 4, 'Size': 2.0},   # Fail status
            {'Fire': 'Fire4', 'StatusLevel': 1, 'Size': 3.0}    # Pass both
        ]

        class MockSettings:
            max_radius = 150
            new_fire_age_days = 7

        filters = {
            'status': 'active',  # Level <= 1
            'size': 1.0,         # >= 1.0 hectares
        }

        result = apply_filters(test_fires, filters, MockSettings())
        assert len(result) == 2
        assert set(f['Fire'] for f in result) == {'Fire1', 'Fire4'}


class TestUnmappedStatusFailsLoud:
    """An unmapped provider status must be logged and shown, never silently dropped."""

    def _data_file(self):
        return DataFile(
            location='AB',
            filename='x_{DATE}.zip',
            mapping={'fields': {}},
            status_map={'active': ['Out of Control'], 'controlled': ['Under Control']},
        )

    def test_unmapped_status_logged_as_error(self, caplog):
        """A status not in the status_map is logged at ERROR and kept as-is for display."""
        with caplog.at_level(logging.ERROR):
            display, level = _resolve_status('Modified Response', self._data_file())

        assert display == 'Modified Response'      # raw status kept for the user
        assert level == STATUS_LEVELS['active']     # treated as active, not hidden
        assert any('Unmapped' in r.message and r.levelname == 'ERROR'
                   for r in caplog.records)

    def test_mapped_status_resolves_without_error(self, caplog):
        """A known status resolves normally and logs nothing."""
        with caplog.at_level(logging.ERROR):
            display, level = _resolve_status('Under Control', self._data_file())

        assert (display, level) == ('Under Control', STATUS_LEVELS['controlled'])
        assert caplog.records == []

    def test_unmapped_status_survives_default_filter(self):
        """An unmapped-status fire is not dropped by the default 'controlled' filter."""
        display, level = _resolve_status('Some New Code', self._data_file())
        fire = {'Fire': 'F1', 'Status': display, 'StatusLevel': level}

        kept = apply_status_filter([fire], 'controlled')
        assert len(kept) == 1


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
        # StatusLevel is now numeric: 1=active, 2=managed, 3=controlled, 4=out
        test_fires = [
            {'Fire': 'Fire1', 'StatusLevel': 1},     # active
            {'Fire': 'Fire2', 'StatusLevel': 2},     # managed
            {'Fire': 'Fire3', 'StatusLevel': 3},     # controlled
            {'Fire': 'Fire4', 'StatusLevel': 4},     # out
            {'Fire': 'Fire5', 'StatusLevel': None}   # no status (filtered out)
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        filtered_fires = apply_status_filter(test_fires, 'active')

        assert len(filtered_fires) == 1
        assert filtered_fires[0]['Fire'] == 'Fire1'

    def test_filter_fires_by_status_controlled(self):
        """Test filtering fires with controlled filter (numeric levels)."""
        # StatusLevel is now numeric: 1=active, 2=managed, 3=controlled, 4=out
        test_fires = [
            {'Fire': 'Fire1', 'StatusLevel': 1},     # active
            {'Fire': 'Fire2', 'StatusLevel': 2},     # managed
            {'Fire': 'Fire3', 'StatusLevel': 3},     # controlled
            {'Fire': 'Fire4', 'StatusLevel': 4},     # out
            {'Fire': 'Fire5', 'StatusLevel': None}   # no status (filtered out)
        ]

        class MockDataFile:
            status_map = {}  # Not used in new implementation

        filtered_fires = apply_status_filter(test_fires, 'controlled')

        assert len(filtered_fires) == 3
        fire_names = [f['Fire'] for f in filtered_fires]
        assert set(fire_names) == {'Fire1', 'Fire2', 'Fire3'}

    def test_filter_fires_all_no_filtering(self):
        """Test that 'all' filter includes all fires."""
        test_fires = [
            {'Fire': 'Fire1', 'StatusLevel': 1},
            {'Fire': 'Fire2', 'StatusLevel': 2},
            {'Fire': 'Fire3', 'StatusLevel': 4},
            {'Fire': 'Fire4', 'StatusLevel': None}
        ]

        class MockDataFile:
            status_map = {}

        # 'all' filter should return all fires
        filtered_fires = apply_status_filter(test_fires, 'all')
        assert len(filtered_fires) == 4

    def _nearby_with_fires(self, fires):
        """Run FindFires.nearby() with search()/shapefile I/O stubbed out."""
        find = FindFires((49.25, -123.1))
        find.sources = ['BC']
        with patch.object(FindFires, '_load_source', return_value=(object(), object())), \
             patch.object(FindFires, 'search', return_value=fires):
            return find.nearby()

    def test_nearby_sorts_by_status_then_distance(self):
        """Fires are ordered by status priority, then by distance within a status."""
        fires = [
            {'Fire': 'ManagedNear', 'StatusLevel': 2, 'Distance': 3000},
            {'Fire': 'ActiveFar', 'StatusLevel': 1, 'Distance': 20000},
            {'Fire': 'ActiveNear', 'StatusLevel': 1, 'Distance': 3000},
            {'Fire': 'ControlledNear', 'StatusLevel': 3, 'Distance': 1000},
        ]

        result = self._nearby_with_fires(fires)

        assert [f['Fire'] for f in result] == [
            'ActiveNear', 'ActiveFar', 'ManagedNear', 'ControlledNear'
        ]

    def test_nearby_sorts_missing_status_level_last(self):
        """Fires without a StatusLevel sort after any known status."""
        fires = [
            {'Fire': 'NoStatus', 'Distance': 500},
            {'Fire': 'Controlled', 'StatusLevel': 3, 'Distance': 40000},
        ]

        result = self._nearby_with_fires(fires)

        assert [f['Fire'] for f in result] == ['Controlled', 'NoStatus']

    def test_filter_with_no_status_level(self):
        """Test filtering fires with missing status levels."""
        test_fires = [
            {'Fire': 'Fire1', 'StatusLevel': 1},     # known
            {'Fire': 'Fire2', 'StatusLevel': None},  # no status (treated as inf)
            {'Fire': 'Fire3', 'StatusLevel': 2},     # known
        ]

        class MockDataFile:
            status_map = {}

        filtered_fires = apply_status_filter(test_fires, 'controlled')

        # Should only include fires with status levels <= 3
        # None is treated as float('inf') so it's filtered out
        assert len(filtered_fires) == 2
        fire_names = [f['Fire'] for f in filtered_fires]
        assert set(fire_names) == {'Fire1', 'Fire3'}


class TestWfigsStatus:
    """The wfigs_status transform: prescribed burns vs percent-contained wildfires."""

    def _data_file(self):
        return DataFile(
            location='US',
            filename='x_{DATE}.zip',
            mapping={'fields': {}, 'status_transform': 'wfigs_status'},
            status_map={},
        )

    def test_prescribed_burn(self):
        get_value = {'IncidentTypeCategory': 'RX'}.get
        display, level = _resolve_status(None, self._data_file(), get_value)

        assert display == 'Prescribed'
        assert level == STATUS_LEVELS['controlled']

    def test_prescribed_burn_from_fallback_column(self):
        get_value = {'INCID_TYPE': 'RX'}.get
        display, level = _resolve_status(None, self._data_file(), get_value)

        assert display == 'Prescribed'
        assert level == STATUS_LEVELS['controlled']

    def test_wildfire_uses_percent_contained(self):
        get_value = {'IncidentTypeCategory': 'WF'}.get
        display, level = _resolve_status(60, self._data_file(), get_value)

        assert display == '60% contained'
        assert level == STATUS_LEVELS['active']


class TestFirstSeenSizeBypass:
    """End to end: fires whose feed row carries no discovery date are
    exempted from the size minimum via the fetch history, guarded on that
    history reaching past the window."""

    ON_COORDS = (49.9, -91.3)

    @staticmethod
    def _settings(db):
        settings = get_config().model_copy(deep=True)
        on = next(df for df in settings.data if df.location == 'ON')
        on.realtime = on.realtime.model_copy(update={'enabled': False})
        settings.data = [on]
        settings.database = db
        return settings

    @staticmethod
    def _on_fires(rows):
        import geopandas as gpd
        from shapely.geometry import Point
        records = [{'Fire': fire, 'Name': None, 'Location': None, 'Type': None,
                    'Discovered': None, 'Updated': None, 'Size': size,
                    'Status': 'Out of Control', 'StatusLevel': 1,
                    'latitude': 49.95, 'longitude': -91.35,
                    'fire_key': f'2026-{fire}'} for fire, size in rows]
        return gpd.GeoDataFrame(records, geometry=[Point(-91.35, 49.95)] * len(rows),
                                crs='EPSG:4326')

    def _build_db(self, db, fetches):
        from app.fires import db as firedb
        conn = firedb.connect(db)
        try:
            for age_days, rows in fetches:
                firedb.record_fires(
                    conn, 'ON', self._on_fires(rows),
                    datetime.now(timezone.utc) - timedelta(days=age_days))
        finally:
            conn.close()

    def _nearby(self, db):
        with patch('app.fires.find.get_config', return_value=self._settings(db)):
            return FindFires(self.ON_COORDS).nearby()

    def test_new_small_fire_bypasses_size_filter(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        self._build_db(db, [
            (8, [('NIP991', 850.0)]),
            (2, [('NIP991', 850.0), ('SLK995', 0.5)]),
        ])

        assert {f['Fire'] for f in self._nearby(db)} == {'NIP991', 'SLK995'}

    def test_long_known_small_fire_is_filtered(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        self._build_db(db, [
            (10, [('NIP991', 850.0), ('SLK995', 0.5)]),
            (2, [('NIP991', 850.0), ('SLK995', 0.5)]),
        ])

        assert {f['Fire'] for f in self._nearby(db)} == {'NIP991'}

    def test_fresh_history_suppresses_the_bypass(self, tmp_path):
        """A source whose whole history is younger than the window would
        mark every fire new; the bypass stays off until it matures."""
        db = str(tmp_path / 'fires.db')
        self._build_db(db, [(2, [('NIP991', 850.0), ('SLK995', 0.5)])])

        assert {f['Fire'] for f in self._nearby(db)} == {'NIP991'}


class TestOntarioStatusVisibility:
    """Being Observed fires are uncontained, so they classify as active."""

    def test_being_observed_shows_under_active_filter(self):
        fires = FindFires((49.9, -91.5), filters={'status': 'active'}).nearby()
        by_id = {f['Fire']: f for f in fires}

        assert by_id['DRY992']['Status'] == 'Being Observed'

    def test_ca_source_excludes_provinces_with_dedicated_sources(self):
        ca = next(d for d in get_config().data if d.location == 'CA').realtime

        assert "'ON'" in ca.points_where
        assert "'Ontario'" in ca.perimeters_where
