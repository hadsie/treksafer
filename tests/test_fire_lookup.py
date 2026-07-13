"""Tests for the fire lookup order (app/fires/lookup.py).

The database answers existence cheaply; only a stale match triggers one live
re-query of that single source.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import geopandas as gpd
import pytest
import requests
from shapely.geometry import Point

from app.config import get_config
from app.fires import db as firedb
from app.fires.lookup import FireLookup

FIRE_COORDS = (50.7, -121.9)


def lookup_settings(db, enabled=True):
    """Settings with BC as the only source, realtime toggleable."""
    settings = get_config().model_copy(deep=True)
    bc = next(df for df in settings.data if df.location == 'BC')
    bc.realtime = bc.realtime.model_copy(update={'enabled': enabled})
    settings.data = [bc]
    settings.database = db
    return settings


def record_stored(db, fetched_at, fire='K1'):
    """Record one stored BC fire fetched at fetched_at."""
    frame = gpd.GeoDataFrame(
        {
            'Fire': [fire], 'Name': ['Stored Name'], 'Location': ['Stored Creek'],
            'Type': [None], 'Discovered': [None], 'Updated': [None],
            'Size': [10.0], 'Status': ['Under Control'], 'StatusLevel': [3],
            'latitude': [FIRE_COORDS[0]], 'longitude': [FIRE_COORDS[1]],
            'fire_key': [f'2026-{fire}'],
        },
        geometry=gpd.GeoSeries([Point(FIRE_COORDS[1], FIRE_COORDS[0])], crs='EPSG:4326'),
    )
    conn = firedb.connect(db)
    try:
        firedb.record_fires(conn, 'BC', frame, fetched_at)
    finally:
        conn.close()


def live_fire(number='K1', status='Out of Control', size=25.0):
    """A single-fire realtime frame as fetch_fire would return it."""
    return gpd.GeoDataFrame(
        {
            'FIRE_NUMBER': [number], 'INCIDENT_NAME': ['Live Name'],
            'GEOGRAPHIC_DESCRIPTION': ['Live Creek'], 'CURRENT_SIZE': [size],
            'FIRE_STATUS': [status], 'IGNITION_DATE': [None],
        },
        geometry=gpd.GeoSeries([Point(FIRE_COORDS[1], FIRE_COORDS[0])], crs='EPSG:4326'),
    ).to_crs(epsg=3857)


def now():
    return datetime.now(timezone.utc)


class TestLookupOrder:
    def _lookup(self, settings, term='K1', coords=None):
        with patch('app.fires.lookup.get_config', return_value=settings):
            return FireLookup(term, coords)

    def test_fresh_database_hit_makes_no_network_call(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5))
        with patch('app.fires.lookup.fetch_fire') as mock_fetch:
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Stored Name'
        assert lookup.marker_fetched is None
        mock_fetch.assert_not_called()

    def test_stale_match_refreshed_live(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=30))
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire()) as mock_fetch:
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Live Name'
        assert lookup.marker_fetched is None
        mock_fetch.assert_called_once()

    def test_live_failure_serves_stored_with_marker(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(hours=7))
        with patch('app.fires.lookup.fetch_fire',
                   side_effect=requests.ConnectionError('boom')):
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Stored Name'
        assert lookup.marker_fetched is not None

    def test_live_but_fire_absent_serves_stored_with_marker(self, tmp_path):
        """A fire dropped from the feed is served from storage, ALWAYS marked
        stale -- even when the stored fetch is younger than the marker window."""
        db = str(tmp_path / 'fires.db')
        fetched = now() - timedelta(minutes=30)  # inside the 6h marker window
        record_stored(db, fetched)
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire().iloc[0:0]):
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Stored Name'
        assert lookup.marker_fetched is not None

    def test_no_database_match_queries_realtime(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5), fire='K99')  # different fire
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire('K1')) as mock_fetch:
            lookup = self._lookup(lookup_settings(db), term='K1')
            fire = lookup.result()

        assert fire['Name'] == 'Live Name'
        assert lookup.marker_fetched is None
        mock_fetch.assert_called_once()

    def test_nothing_matched_returns_none(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5), fire='K99')
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire().iloc[0:0]):
            lookup = self._lookup(lookup_settings(db), term='K1')
            assert lookup.result() is None

    def test_realtime_fetches_are_not_recorded(self, tmp_path):
        """A filtered single-fire slice must never be written to the database."""
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5), fire='K99')
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire('K1')):
            self._lookup(lookup_settings(db), term='K1').result()

        conn = firedb.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM fires WHERE fire = 'K1'").fetchone()[0] == 0
        finally:
            conn.close()


class TestMarkerCoordinates:
    """The marker's timezone comes from the requester's coordinates when
    present, otherwise the matched fire's own location."""

    def test_requester_coords_localize_when_present(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(hours=7))
        with patch('app.fires.lookup.get_config', return_value=lookup_settings(db)), \
             patch('app.fires.lookup.fetch_fire', side_effect=requests.ConnectionError('x')):
            lookup = FireLookup('K1', (49.0, -120.0))
            lookup.result()

        assert lookup.marker_coords == (49.0, -120.0)

    def test_fire_location_localizes_without_coords(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(hours=7))
        with patch('app.fires.lookup.get_config', return_value=lookup_settings(db)), \
             patch('app.fires.lookup.fetch_fire', side_effect=requests.ConnectionError('x')):
            lookup = FireLookup('K1', None)
            lookup.result()

        assert lookup.marker_coords[0] == pytest.approx(FIRE_COORDS[0], abs=1e-3)
        assert lookup.marker_coords[1] == pytest.approx(FIRE_COORDS[1], abs=1e-3)
