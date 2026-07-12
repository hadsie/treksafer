"""Tests for response-time history enrichment (size change and NEW flag)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.fires import db as firedb
from app.fires import growth
from app.fires.growth import enrich

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def record(conn, size, fetched_at, source='BC', fire_key='2026-T1',
           status='Out of Control', updated=None):
    """Record one fire snapshot at a controlled time."""
    frame = gpd.GeoDataFrame(
        {
            'Fire': [fire_key.split('-', 1)[1]],
            'Name': [None], 'Location': [None], 'Type': [None],
            'Discovered': [None], 'Size': [size], 'Status': [status],
            'StatusLevel': [1], 'Updated': [updated], 'fire_key': [fire_key],
            'latitude': [50.0], 'longitude': [-120.0],
        },
        geometry=[Point(-120.0, 50.0)], crs='EPSG:4326',
    )
    firedb.record_fires(conn, source, frame, fetched_at)


def make_fire(**overrides):
    fire = {
        'Fire': 'T1', 'Source': 'BC', 'FireKey': '2026-T1',
        'Size': 500.0, 'Status': 'Out of Control', 'StatusLevel': 1,
        'Distance': 12000, 'Direction': 'NW',
    }
    fire.update(overrides)
    return fire


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'fires.db')
    conn = firedb.connect(path)
    yield path, conn
    conn.close()


class TestSizeChange:
    def test_growth_against_day_old_snapshot(self, db):
        path, conn = db
        record(conn, 200.0, NOW - timedelta(hours=26))
        fire = make_fire(Size=500.0)

        enrich([fire], path, now=NOW)

        assert fire['SizeChange']['delta'] == 300
        assert fire['SizeChange']['hours'] == pytest.approx(26, abs=0.1)

    def test_shrinkage_renders_negative(self, db):
        path, conn = db
        record(conn, 800.0, NOW - timedelta(hours=30))
        fire = make_fire(Size=500.0)

        enrich([fire], path, now=NOW)

        assert fire['SizeChange']['delta'] == -300

    def test_delta_below_both_thresholds_suppressed(self, db):
        path, conn = db
        record(conn, 498.0, NOW - timedelta(hours=26))
        fire = make_fire(Size=500.0)

        enrich([fire], path, now=NOW)

        assert 'SizeChange' not in fire

    def test_small_absolute_delta_shown_when_relative_is_large(self, db):
        path, conn = db
        record(conn, 92.0, NOW - timedelta(hours=26))
        fire = make_fire(Size=100.0)

        enrich([fire], path, now=NOW)

        # 8 ha is under the 10 ha floor but 8% of the fire's size.
        assert fire['SizeChange']['delta'] == 8

    def test_large_absolute_delta_shown_when_relative_is_small(self, db):
        path, conn = db
        record(conn, 14300.0, NOW - timedelta(hours=26))
        fire = make_fire(Size=14333.0)

        enrich([fire], path, now=NOW)

        # 33 ha is 0.2% of the fire but clears the 10 ha floor.
        assert fire['SizeChange']['delta'] == 33

    def test_new_fire_shows_no_delta(self, db):
        """A NEW fire's delta is its whole size; the label carries it."""
        path, conn = db
        record(conn, 50.0, NOW - timedelta(hours=30), fire_key='2026-OLD')
        record(conn, 100.0, NOW - timedelta(hours=6))
        fire = make_fire(Size=400.0)

        enrich([fire], path, now=NOW)

        assert fire['New'] is True
        assert 'SizeChange' not in fire

    def test_unlabeled_young_fire_anchors_to_oldest_snapshot(self, db):
        """A young fire that escapes the NEW label (fresh source history)
        still reports growth from its oldest snapshot."""
        path, conn = db
        record(conn, 100.0, NOW - timedelta(hours=6))
        fire = make_fire(Size=400.0)

        enrich([fire], path, now=NOW)

        assert 'New' not in fire
        assert fire['SizeChange']['delta'] == 300
        assert fire['SizeChange']['hours'] == pytest.approx(6, abs=0.1)

    def test_under_one_hour_of_history_shows_nothing(self, db):
        path, conn = db
        record(conn, 100.0, NOW - timedelta(minutes=30))
        fire = make_fire(Size=400.0)

        enrich([fire], path, now=NOW)

        assert 'SizeChange' not in fire

    def test_non_active_fire_shows_no_delta(self, db):
        path, conn = db
        record(conn, 200.0, NOW - timedelta(hours=26),
               status='Under Control')
        fire = make_fire(Size=500.0, Status='Under Control', StatusLevel=3)

        enrich([fire], path, now=NOW)

        assert 'SizeChange' not in fire

    def test_unknown_fire_left_untouched(self, db):
        path, conn = db
        fire = make_fire(FireKey='2026-NOPE')

        enrich([fire], path, now=NOW)

        assert 'SizeChange' not in fire
        assert 'New' not in fire

    def test_fallback_data_time_anchors_the_window(self, db):
        """Day-old fallback data must anchor against its own timeline, not
        now, or the current snapshot would qualify as its own anchor."""
        path, conn = db
        record(conn, 100.0, NOW - timedelta(days=5))
        record(conn, 600.0, NOW - timedelta(days=3))
        fire = make_fire(Size=600.0,
                         DataTime=(NOW - timedelta(days=3)).isoformat())

        enrich([fire], path, now=NOW)

        assert fire['SizeChange']['delta'] == 500
        # The displayed span runs from the anchor to now (reading time).
        assert fire['SizeChange']['hours'] == pytest.approx(120, abs=0.1)

    def test_unchanged_fire_shows_nothing(self, db):
        """A fire whose only snapshots predate the window by days has a
        zero delta, suppressed by the noise floor."""
        path, conn = db
        record(conn, 500.0, NOW - timedelta(days=4))
        fire = make_fire(Size=500.0)

        enrich([fire], path, now=NOW)

        assert 'SizeChange' not in fire


class TestNewFlag:
    def test_recent_first_seen_flags_new(self, db):
        path, conn = db
        # Older fire establishes >=24h of source history.
        record(conn, 50.0, NOW - timedelta(hours=30), fire_key='2026-OLD')
        record(conn, 100.0, NOW - timedelta(hours=6))
        fire = make_fire()

        enrich([fire], path, now=NOW)

        assert fire['New'] is True
        assert 'SizeChange' not in fire

    def test_old_first_seen_not_flagged(self, db):
        path, conn = db
        record(conn, 100.0, NOW - timedelta(days=3))
        fire = make_fire()

        enrich([fire], path, now=NOW)

        assert 'New' not in fire

    def test_fresh_source_history_suppresses_new(self, db):
        """A source whose own history is younger than the window has
        nothing to be new against (fresh database, new source)."""
        path, conn = db
        record(conn, 100.0, NOW - timedelta(hours=6))
        fire = make_fire()

        enrich([fire], path, now=NOW)

        assert 'New' not in fire

    def test_new_applies_to_non_active_fires(self, db):
        path, conn = db
        record(conn, 50.0, NOW - timedelta(hours=30), fire_key='2026-OLD')
        record(conn, 100.0, NOW - timedelta(hours=6), status='Being Held')
        fire = make_fire(Status='Being Held', StatusLevel=2)

        enrich([fire], path, now=NOW)

        assert fire['New'] is True
        assert 'SizeChange' not in fire


class TestResilience:
    def test_read_failure_names_the_fire_and_spares_the_rest(self, db, caplog, monkeypatch):
        path, conn = db
        record(conn, 50.0, NOW - timedelta(hours=30), fire_key='2026-OLD')
        record(conn, 200.0, NOW - timedelta(hours=26), fire_key='2026-T2')
        broken, healthy = make_fire(FireKey='2026-T1'), make_fire(FireKey='2026-T2')

        real = growth.firedb.fire_first_seen
        def explode_on_t1(conn, source, fire_key):
            if fire_key == '2026-T1':
                raise sqlite3.OperationalError('boom')
            return real(conn, source, fire_key)
        monkeypatch.setattr(growth.firedb, 'fire_first_seen', explode_on_t1)

        enrich([broken, healthy], path, now=NOW)

        assert 'BC 2026-T1' in caplog.text
        assert healthy['SizeChange']['delta'] == 300

    def test_unreadable_database_degrades_quietly(self, caplog):
        fire = make_fire()

        enrich([fire], '/dev/null/nope/fires.db', now=NOW)

        assert 'SizeChange' not in fire
        assert 'skipping enrichment' in caplog.text

    def test_empty_fire_list_is_a_no_op(self, tmp_path):
        enrich([], str(tmp_path / 'unused.db'), now=NOW)
