"""Tests for FindFires source loading (realtime vs downloaded)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.fires import db as firedb
from app.config import get_config, RealtimeFireConfig
from app.fires import FindFires, find_fire
from app.fires.find import fire_keys

BC_COORDS = (50.7021714, -121.9725246)

REALTIME = RealtimeFireConfig(
    enabled=True,
    points_url='https://example.test/points/query',
    perimeters_url='https://example.test/perims/query',
    join_field='FIRE_NUMBER',
    perimeter_fire_field='FIRE_NUMBER',
    key_fields=['FIRE_NUMBER'],
    mapping={
        'Fire': 'FIRE_NUMBER',
        'Name': 'INCIDENT_NAME',
        'Location': 'GEOGRAPHIC_DESCRIPTION',
        'Size': 'CURRENT_SIZE',
        'Status': 'FIRE_STATUS',
        'Discovered': 'IGNITION_DATE',
    },
    transforms={'Discovered': 'epoch_ms'},
    status_map={
        'active': ['Out of Control', 'Fire of Note'],
        'managed': ['Being Held'],
        'controlled': ['Under Control'],
        'out': ['Out'],
    },
)


def realtime_settings(enabled=True, database=':memory:'):
    """Settings copy where BC is the only source and has realtime enabled.

    Recording goes to a throwaway in-memory database by default so tests
    never write into the session fixture database.
    """
    settings = get_config().model_copy(deep=True)
    bc = next(df for df in settings.data if df.location == 'BC')
    bc.realtime = REALTIME.model_copy(update={'enabled': enabled})
    settings.data = [bc]
    if database is not None:
        settings.database = database
    return settings


def realtime_gdf(lat, lon, status='Out of Control', size=25.0, ignition_date=None):
    """A single-fire GeoDataFrame as fetch_fires would return it."""
    return gpd.GeoDataFrame(
        {
            'FIRE_NUMBER': ['K1'],
            'INCIDENT_NAME': ['Test Fire'],
            'GEOGRAPHIC_DESCRIPTION': ['Test Creek'],
            'CURRENT_SIZE': [size],
            'FIRE_STATUS': [status],
            'IGNITION_DATE': [ignition_date],
        },
        geometry=gpd.GeoSeries([Point(lon, lat)], crs='EPSG:4326'),
    ).to_crs(epsg=3857)


class TestLoadSource:
    def test_realtime_success_uses_realtime_mapping(self):
        gdf = realtime_gdf(*BC_COORDS)
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=gdf) as mock_fetch:
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            data_file = ff.settings.data[0]
            fires_gdf, effective = ff._load_source(data_file)

        assert fires_gdf.drop(columns='fire_key').equals(gdf)
        assert list(fires_gdf['fire_key']) == ['K1']
        assert effective.mapping == {'fields': REALTIME.mapping, 'discovered_transform': 'epoch_ms'}
        assert effective.status_map == REALTIME.status_map
        mock_fetch.assert_called_once_with(data_file.realtime, BC_COORDS, 20)

    def test_realtime_success_records_to_database(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        gdf = realtime_gdf(*BC_COORDS)
        with patch('app.fires.find.get_config', return_value=realtime_settings(database=db)), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            ff._load_source(ff.settings.data[0])

        conn = firedb.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT fire FROM fires").fetchone()[0] == 'K1'
        conn.close()

    def test_recording_failure_never_breaks_the_request(self, caplog):
        """A broken database degrades to logging; the fetched fires still serve."""
        gdf = realtime_gdf(*BC_COORDS)
        settings = realtime_settings(database='/dev/null/nope/fires.db')
        with patch('app.fires.find.get_config', return_value=settings), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            fires_gdf, _ = ff._load_source(ff.settings.data[0])

        assert fires_gdf.drop(columns='fire_key').equals(gdf)
        assert 'Failed to record' in caplog.text

    def test_realtime_failure_falls_back_to_database(self, caplog):
        """API down: the source serves from the session fixture database."""
        with patch('app.fires.find.get_config', return_value=realtime_settings(database=None)), \
             patch('app.fires.find.fetch_fires', return_value=None):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            fires_gdf, effective = ff._load_source(ff.settings.data[0])

        assert fires_gdf is not None
        assert not fires_gdf.empty
        assert 'Status' in fires_gdf.columns
        assert effective.mapping.get('status_transform') == 'stored'
        assert 'Serving BC fires from the database' in caplog.text

    def test_realtime_disabled_never_queries_api(self):
        with patch('app.fires.find.get_config', return_value=realtime_settings(enabled=False, database=None)), \
             patch('app.fires.find.fetch_fires') as mock_fetch:
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            fires_gdf, _ = ff._load_source(ff.settings.data[0])

        mock_fetch.assert_not_called()
        assert fires_gdf is not None

    def test_no_api_and_empty_database_marks_source_unavailable(self, tmp_path, caplog):
        db = str(tmp_path / 'empty.db')
        with patch('app.fires.find.get_config', return_value=realtime_settings(enabled=False, database=db)):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            fires_gdf, _ = ff._load_source(ff.settings.data[0])

        assert fires_gdf is None
        assert ff.unavailable_sources == ['BC']
        assert 'source unavailable' in caplog.text

    def test_radius_capped_at_max_radius(self):
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=realtime_gdf(*BC_COORDS)) as mock_fetch:
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 9999})
            ff._load_source(ff.settings.data[0])

        radius = mock_fetch.call_args.args[2]
        assert radius == ff.settings.max_radius


class TestNearbyRealtime:
    def test_nearby_returns_normalized_realtime_fire(self):
        """End to end: a realtime fire is normalized, statused, and sorted."""
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1])
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50, 'size': 0})
            fires = ff.nearby()

        assert len(fires) == 1
        fire = fires[0]
        assert fire['Fire'] == 'K1'
        assert fire['Name'] == 'Test Fire'
        assert fire['Location'] == 'Test Creek'
        assert fire['Size'] == 25.0
        assert fire['Status'] == 'Out of Control'
        assert fire['StatusLevel'] == 1
        assert fire['Distance'] > 0
        assert fire['Direction']

    def test_nearby_status_filter_applies_to_realtime_fires(self):
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1], status='Out')
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'active', 'distance': 50, 'size': 0})
            fires = ff.nearby()

        assert fires == []

    def test_nearby_shows_new_small_fire_despite_size_minimum(self):
        """A fire discovered days ago bypasses the default 1 ha size filter."""
        two_days_ago_ms = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1],
                           size=0.01, ignition_date=two_days_ago_ms)
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50, 'size': 1})
            fires = ff.nearby()

        assert len(fires) == 1
        assert fires[0]['Fire'] == 'K1'

    def test_nearby_hides_old_small_fire(self):
        """An old fire below the size minimum stays filtered out."""
        last_month_ms = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1],
                           size=0.01, ignition_date=last_month_ms)
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50, 'size': 1})
            fires = ff.nearby()

        assert fires == []


class TestAllStatusDropsSizeFilter:
    """An explicit 'all' status shows every fire regardless of size."""

    def test_all_removes_default_size_filter(self):
        ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50})
        assert 'size' not in ff.filters

    def test_all_with_explicit_size_keeps_it(self):
        ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50, 'size': 5})
        assert ff.filters['size'] == 5

    def test_default_status_keeps_size_filter(self):
        ff = FindFires(BC_COORDS, filters={'distance': 50})
        assert ff.filters['size'] == ff.settings.fire_size

    def test_all_includes_fire_with_no_size(self):
        """With 'all', even a fire without a size estimate is shown."""
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1], size=None)
        with patch('app.fires.find.get_config', return_value=realtime_settings()), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50})
            fires = ff.nearby()

        assert len(fires) == 1
        assert 'Size' not in fires[0]


class TestFireKeys:
    """Key derivation trusts the frame schema and fails loudly without it."""

    def test_empty_frame_with_schema_yields_no_keys(self):
        frame = gpd.GeoDataFrame(columns=['FIRE_YEAR', 'FIRE_NUMBER', 'geometry'],
                                 geometry='geometry', crs='EPSG:4326')
        assert fire_keys(frame, ['FIRE_YEAR', 'FIRE_NUMBER']) == []

    def test_missing_key_columns_fail_loudly(self):
        """A frame without its key columns is a broken producer, not an
        empty result."""
        frame = gpd.GeoDataFrame(columns=['geometry'], geometry='geometry',
                                 crs='EPSG:4326')
        with pytest.raises(ValueError, match='FIRE_NUMBER'):
            fire_keys(frame, ['FIRE_YEAR', 'FIRE_NUMBER'])

    def test_keys_join_fields_in_order(self):
        frame = gpd.GeoDataFrame(
            {'FIRE_YEAR': [2026], 'FIRE_NUMBER': ['K1']},
            geometry=[Point(-120.0, 50.0)], crs='EPSG:4326')
        assert fire_keys(frame, ['FIRE_YEAR', 'FIRE_NUMBER']) == ['2026-K1']


class TestFallbackFreshness:
    """Stored fallback data younger than the staleness window reads the
    same as a live answer and warrants no freshness marker."""

    def _findfires(self):
        with patch('app.fires.find.get_config', return_value=realtime_settings()):
            return FindFires(BC_COORDS)

    def test_fresh_fallback_reports_nothing(self):
        ff = self._findfires()
        fetched = datetime.now(timezone.utc) - timedelta(hours=5)
        ff.fallback_fetches = {'BC': fetched.isoformat()}

        assert ff.fallback_fetched is None

    def test_stale_fallback_reports_fetch_time(self):
        ff = self._findfires()
        fetched = datetime.now(timezone.utc) - timedelta(hours=7)
        ff.fallback_fetches = {'BC': fetched.isoformat()}

        assert ff.fallback_fetched == fetched

    def test_no_fallbacks_reports_nothing(self):
        ff = self._findfires()

        assert ff.fallback_fetched is None


class TestFallbackMatchesRealtime:
    """Identical source data must produce identical responses from the
    realtime path and the database fallback path."""

    def test_paths_agree(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1], ignition_date=1782000000000)
        filters = {'status': 'all', 'distance': 50, 'size': 0}

        # Realtime path (records to the database as a side effect).
        with patch('app.fires.find.get_config', return_value=realtime_settings(database=db)), \
             patch('app.fires.find.fetch_fires', return_value=gdf):
            realtime_fires = FindFires(BC_COORDS, filters=filters).nearby()

        # Fallback path reads what the realtime path recorded.
        with patch('app.fires.find.get_config', return_value=realtime_settings(database=db)), \
             patch('app.fires.find.fetch_fires', return_value=None):
            fallback_fires = FindFires(BC_COORDS, filters=filters).nearby()

        assert len(realtime_fires) == len(fallback_fires) == 1
        realtime_fire, fallback_fire = realtime_fires[0], fallback_fires[0]
        # DataTime is internal to growth enrichment and only exists on the
        # fallback path (realtime data is current by definition).
        assert set(realtime_fire) == set(fallback_fire) - {'DataTime'}
        for key in realtime_fire:
            if key in ('Distance',):
                assert fallback_fire[key] == pytest.approx(realtime_fire[key], rel=1e-6)
            else:
                assert fallback_fire[key] == realtime_fire[key], key


class TestFindFireById:
    """find_fire() looks up a specific fire by identifier across all sources,
    served here from the fixture database (realtime disabled in tests)."""

    def test_lookup_by_bc_number_without_coords(self):
        fires = find_fire("C10784")
        assert len(fires) == 1
        assert fires[0]["Fire"] == "C10784"
        # No coordinates were supplied, so there is no distance or direction.
        assert "Distance" not in fires[0]
        assert "Direction" not in fires[0]

    def test_lookup_by_us_name_with_coords_adds_distance(self):
        fires = find_fire("Snake River", (43.5, -110.7))
        assert len(fires) == 1
        assert fires[0]["Fire"] == "Snake River"
        assert fires[0]["Distance"] > 0
        assert fires[0]["Direction"]

    def test_lookup_is_case_insensitive(self):
        assert [f["Fire"] for f in find_fire("snake river")] == ["Snake River"]

    def test_lookup_matches_substring(self):
        assert "Snake River" in [f["Fire"] for f in find_fire("Snake")]

    def test_lookup_searches_ca_source(self):
        assert [f["Fire"] for f in find_fire("QC-2026-001")] == ["QC-2026-001"]

    def test_unknown_identifier_returns_empty(self):
        assert find_fire("ZZZ-NO-SUCH-FIRE") == []
