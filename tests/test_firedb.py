"""Tests for the fire database (app/fires/db.py)."""

from datetime import datetime, timezone

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.fires import db as firedb

T1 = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 7, 3, 6, 0, tzinfo=timezone.utc)


def fires_gdf(rows):
    """Build a normalized frame from simplified row dicts."""
    defaults = {
        'Name': None, 'Location': None, 'Type': None, 'Discovered': None,
        'Updated': None, 'Size': 10.0, 'Status': 'Out of Control',
        'StatusLevel': 1, 'latitude': 50.6, 'longitude': -120.3,
    }
    records = [{**defaults, **row} for row in rows]
    geometry = [r.pop('geometry', Point(-120.3, 50.6)) for r in records]
    return gpd.GeoDataFrame(records, geometry=geometry, crs='EPSG:4326')


@pytest.fixture
def conn(tmp_path):
    conn = firedb.connect(str(tmp_path / 'fires.db'))
    yield conn
    conn.close()


class TestRecordFires:
    def test_round_trip(self, conn):
        fires = fires_gdf([{'fire_key': '2026-K1', 'Fire': 'K1', 'Name': 'Test Fire',
                            'Location': 'Test Creek', 'Size': 25.0}])
        written = firedb.record_fires(conn, 'BC', fires, T1)

        assert written == 1
        loaded = firedb.load_source(conn, 'BC')
        assert str(loaded.crs) == 'EPSG:3857'
        row = loaded.iloc[0]
        assert (row['Fire'], row['Name'], row['Location']) == ('K1', 'Test Fire', 'Test Creek')
        assert (row['Size'], row['Status'], row['StatusLevel']) == (25.0, 'Out of Control', 1)

    def test_unchanged_fire_not_resnapshotted(self, conn):
        fires = fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}])
        firedb.record_fires(conn, 'BC', fires, T1)

        written = firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}]), T2)

        assert written == 0

    @pytest.mark.parametrize('change', [
        {'Size': 99.0},
        {'Status': 'Out', 'StatusLevel': 4},
        {'geometry': Point(-121.0, 51.0)},
    ])
    def test_field_change_snapshots(self, conn, change):
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}]), T1)

        written = firedb.record_fires(
            conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1', **change}]), T2)

        assert written == 1

    def test_source_timestamp_gates_snapshots(self, conn):
        """With an update timestamp, identical fields still snapshot when it
        advances, and changed fields don't when it hasn't."""
        base = {'fire_key': 'F1', 'Fire': 'F1', 'Updated': '2026-07-01T00:00:00+00:00'}
        firedb.record_fires(conn, 'US', fires_gdf([base]), T1)

        same_stamp_new_size = firedb.record_fires(
            conn, 'US', fires_gdf([{**base, 'Size': 99.0}]), T2)
        new_stamp_same_fields = firedb.record_fires(
            conn, 'US', fires_gdf([{**base, 'Updated': '2026-07-02T00:00:00+00:00'}]), T3)

        assert same_stamp_new_size == 0
        assert new_stamp_same_fields == 1

    def test_identity_updates_in_place(self, conn):
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1',
                                                    'Name': 'Old Name'}]), T1)
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1',
                                                    'Name': 'New Name', 'Size': 50.0}]), T2)

        names = conn.execute("SELECT name FROM fires").fetchall()
        assert names == [('New Name',)]

    def test_recycled_fire_number_is_a_new_fire(self, conn):
        """A season-qualified key keeps a recycled BC number's history separate."""
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': '2026-K1', 'Fire': 'K1'}]), T1)
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': '2027-K1', 'Fire': 'K1'}]), T2)

        assert conn.execute("SELECT COUNT(*) FROM fires").fetchone()[0] == 2

    def test_every_fetch_logged(self, conn):
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}]), T1)
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}]), T2)

        assert conn.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 2
        assert firedb.latest_fetch(conn, 'BC') == T2.isoformat()


class TestLoadSource:
    def test_no_data_returns_none(self, conn):
        assert firedb.load_source(conn, 'BC') is None

    def test_empty_fetch_returns_empty_frame(self, conn):
        """A recorded fetch with zero fires is real data, not unavailability."""
        firedb.record_fires(conn, 'BC', fires_gdf([]), T1)

        loaded = firedb.load_source(conn, 'BC')
        assert loaded is not None
        assert loaded.empty

    def test_delisted_fire_drops_out_but_history_remains(self, conn):
        firedb.record_fires(conn, 'BC', fires_gdf([
            {'fire_key': 'K1', 'Fire': 'K1'}, {'fire_key': 'K2', 'Fire': 'K2'},
        ]), T1)
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}]), T2)

        loaded = firedb.load_source(conn, 'BC')
        assert list(loaded['Fire']) == ['K1']
        assert conn.execute("SELECT COUNT(*) FROM fires").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM snapshots s JOIN fires f ON f.id = s.fire_id "
            "WHERE f.fire_key = 'K2'").fetchone()[0] == 1

    def test_sources_are_independent(self, conn):
        firedb.record_fires(conn, 'BC', fires_gdf([{'fire_key': 'K1', 'Fire': 'K1'}]), T1)

        assert firedb.load_source(conn, 'AB') is None
