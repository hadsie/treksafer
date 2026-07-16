"""Tests for the fire lookup order (app/fires/lookup.py).

The database answers existence cheaply; only a stale match triggers one live
re-query of that single source.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import geopandas as gpd
import pytest
import requests
import responses
from shapely.geometry import Point, box

from app.config import EnrichmentConfig, get_config
from app.fires import db as firedb
from app.fires.lookup import FireLookup
from app.helpers import local_crs

FIRE_COORDS = (50.7, -121.9)


def lookup_settings(db, enabled=True):
    """Settings with BC as the only source, realtime toggleable.

    Enrichment is stripped so no test can reach the real enrichment API;
    tests exercising it configure their own mocked endpoint.
    """
    settings = get_config().model_copy(deep=True)
    bc = next(df for df in settings.data if df.location == 'BC')
    bc.realtime = bc.realtime.model_copy(update={'enabled': enabled, 'enrichment': None})
    settings.data = [bc]
    settings.database = db
    return settings


def record_stored(db, fetched_at, fire='K1', updated=None, key=None, size=10.0):
    """Record one stored BC fire fetched at fetched_at."""
    frame = gpd.GeoDataFrame(
        {
            'Fire': [fire], 'Name': ['Stored Name'], 'Location': ['Stored Creek'],
            'Type': [None], 'Discovered': [None], 'Updated': [updated],
            'Size': [size], 'Status': ['Under Control'], 'StatusLevel': [3],
            'latitude': [FIRE_COORDS[0]], 'longitude': [FIRE_COORDS[1]],
            'fire_key': [key or f'2026-{fire}'],
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
        fetched = now() - timedelta(minutes=5)
        record_stored(db, fetched)
        with patch('app.fires.lookup.fetch_fire') as mock_fetch:
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Stored Name'
        assert lookup.as_of == fetched
        mock_fetch.assert_not_called()

    def test_stale_match_refreshed_live(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        test_start = now()
        record_stored(db, now() - timedelta(minutes=30))
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire()) as mock_fetch:
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Live Name'
        assert lookup.as_of >= test_start
        mock_fetch.assert_called_once()

    def test_live_failure_serves_stored_with_stored_time(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        fetched = now() - timedelta(hours=7)
        record_stored(db, fetched)
        with patch('app.fires.lookup.fetch_fire',
                   side_effect=requests.ConnectionError('boom')):
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Stored Name'
        assert lookup.as_of == fetched

    def test_live_but_fire_absent_serves_stored_with_stored_time(self, tmp_path):
        """A fire dropped from the feed is served from storage, timestamped
        with the stored fetch time so the reply never reads as current."""
        db = str(tmp_path / 'fires.db')
        fetched = now() - timedelta(minutes=30)
        record_stored(db, fetched)
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire().iloc[0:0]):
            lookup = self._lookup(lookup_settings(db))
            fire = lookup.result()

        assert fire['Name'] == 'Stored Name'
        assert lookup.as_of == fetched

    def test_no_database_match_queries_realtime(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        test_start = now()
        record_stored(db, now() - timedelta(minutes=5), fire='K99')  # different fire
        with patch('app.fires.lookup.fetch_fire', return_value=live_fire('K1')) as mock_fetch:
            lookup = self._lookup(lookup_settings(db), term='K1')
            fire = lookup.result()

        assert fire['Name'] == 'Live Name'
        assert lookup.as_of >= test_start
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


class TestSeasonPreference:
    """A fire number that recycles annually resolves to the current
    season's fire; a number with no current fire serves the most recent
    previous season, honestly aged."""

    def _lookup(self, settings, term='K1', coords=None):
        with patch('app.fires.lookup.get_config', return_value=settings):
            lookup = FireLookup(term, coords)
            return lookup, lookup.result()

    def test_current_season_outranks_prior(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(days=300), key='2025-K1', size=99.0)
        record_stored(db, now() - timedelta(minutes=5), key='2026-K1', size=10.0)

        lookup, fire = self._lookup(lookup_settings(db, enabled=False))

        assert fire['Size'] == 10.0

    def test_prior_season_serves_when_no_current_match(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        last_seen = now() - timedelta(days=300)
        record_stored(db, last_seen, key='2025-K1', size=99.0)

        lookup, fire = self._lookup(lookup_settings(db, enabled=False))

        assert fire['Size'] == 99.0
        assert lookup.as_of == last_seen

    def test_prior_season_match_tries_live_before_serving_stored(self, tmp_path):
        """A long-unseen match is stale by its own age, so the lookup
        re-queries live; the feed no longer has it, and the stored record
        serves with its honest age."""
        db = str(tmp_path / 'fires.db')
        last_seen = now() - timedelta(days=300)
        record_stored(db, last_seen, key='2025-K1')
        with patch('app.fires.lookup.fetch_fire',
                   return_value=live_fire().iloc[0:0]) as mock_fetch:
            lookup, fire = self._lookup(lookup_settings(db))

        mock_fetch.assert_called_once()
        assert fire['Name'] == 'Stored Name'
        assert lookup.as_of == last_seen


class TestAsOf:
    """The as_of time is the agency's own per-fire update time where one
    exists, otherwise when the served data was fetched."""

    def _lookup(self, settings, term='K1', coords=None):
        with patch('app.fires.lookup.get_config', return_value=settings):
            lookup = FireLookup(term, coords)
            lookup.result()
            return lookup

    def test_stored_agency_time_wins(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        agency = now() - timedelta(hours=9)
        record_stored(db, now() - timedelta(minutes=5), updated=agency)

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.as_of == agency

    def test_no_agency_time_falls_back_to_fetch_time(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        fetched = now() - timedelta(minutes=5)
        record_stored(db, fetched)

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.as_of == fetched

    def test_fetch_time_is_the_fires_own_last_seen(self, tmp_path):
        """A later fetch that did not include the fire (e.g. a radius query
        elsewhere) must not make its data read fresher."""
        db = str(tmp_path / 'fires.db')
        seen = now() - timedelta(hours=3)
        record_stored(db, seen)
        record_stored(db, now() - timedelta(minutes=5), fire='K99')

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.as_of == seen

    @responses.activate
    def test_enrichment_supplies_agency_time(self, tmp_path, monkeypatch):
        """BC-style: the layer has no per-fire time; the enrichment API does.
        The url template resolves from the fire's key fields."""
        import requests as requests_lib
        from app.fires import lookup as lookup_mod
        monkeypatch.setattr(lookup_mod, '_enrichment_session',
                            lambda: requests_lib.Session())
        responses.get(
            'https://enrich.test/incident/K1?fireYear=2026',
            json={'updateDate': 1783960569140})

        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5))
        settings = lookup_settings(db)
        settings.data[0].realtime = settings.data[0].realtime.model_copy(update={
            'enrichment': EnrichmentConfig(
                url='https://enrich.test/incident/{FIRE_NUMBER}?fireYear={FIRE_YEAR}',
                updated_field='updateDate')})

        lookup = self._lookup(settings)

        assert lookup.as_of == datetime.fromtimestamp(1783960569.140, tz=timezone.utc)

    @responses.activate
    def test_enrichment_failure_falls_back_to_fetch_time(self, tmp_path, monkeypatch):
        import requests as requests_lib
        from app.fires import lookup as lookup_mod
        monkeypatch.setattr(lookup_mod, '_enrichment_session',
                            lambda: requests_lib.Session())
        responses.get('https://enrich.test/incident/K1?fireYear=2026', status=503)

        db = str(tmp_path / 'fires.db')
        fetched = now() - timedelta(minutes=5)
        record_stored(db, fetched)
        settings = lookup_settings(db)
        settings.data[0].realtime = settings.data[0].realtime.model_copy(update={
            'enrichment': EnrichmentConfig(
                url='https://enrich.test/incident/{FIRE_NUMBER}?fireYear={FIRE_YEAR}',
                updated_field='updateDate')})

        lookup = self._lookup(settings)

        assert lookup.as_of == fetched

    @responses.activate
    def test_enrichment_backfills_snapshot(self, tmp_path, monkeypatch):
        """An explicit lookup writes the fetched update time onto the
        fire's newest snapshot, where it serves later lookups (e.g. with
        realtime disabled) as a fallback."""
        import requests as requests_lib
        from app.fires import lookup as lookup_mod
        monkeypatch.setattr(lookup_mod, '_enrichment_session',
                            lambda: requests_lib.Session())
        responses.get(
            'https://enrich.test/incident/K1?fireYear=2026',
            json={'updateDate': 1783960569140})

        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5))
        settings = lookup_settings(db)
        settings.data[0].realtime = settings.data[0].realtime.model_copy(update={
            'enrichment': EnrichmentConfig(
                url='https://enrich.test/incident/{FIRE_NUMBER}?fireYear={FIRE_YEAR}',
                updated_field='updateDate')})
        self._lookup(settings)

        agency = datetime.fromtimestamp(1783960569.140, tz=timezone.utc)
        conn = firedb.connect(db)
        try:
            stored = conn.execute('SELECT source_updated FROM snapshots').fetchone()[0]
        finally:
            conn.close()
        assert stored == agency.isoformat()

        # The back-fill now serves as_of without any live call.
        offline = self._lookup(lookup_settings(db, enabled=False))
        assert offline.as_of == agency

    @responses.activate
    def test_enrichment_failure_leaves_snapshot_unfilled(self, tmp_path, monkeypatch):
        import requests as requests_lib
        from app.fires import lookup as lookup_mod
        monkeypatch.setattr(lookup_mod, '_enrichment_session',
                            lambda: requests_lib.Session())
        responses.get('https://enrich.test/incident/K1?fireYear=2026', status=503)

        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(minutes=5))
        settings = lookup_settings(db)
        settings.data[0].realtime = settings.data[0].realtime.model_copy(update={
            'enrichment': EnrichmentConfig(
                url='https://enrich.test/incident/{FIRE_NUMBER}?fireYear={FIRE_YEAR}',
                updated_field='updateDate')})
        self._lookup(settings)

        conn = firedb.connect(db)
        try:
            assert conn.execute('SELECT source_updated FROM snapshots').fetchone()[0] is None
        finally:
            conn.close()

    def test_enrichment_skipped_when_realtime_disabled(self, tmp_path):
        """Disabled realtime means no live upstream calls of any kind; a
        configured enrichment API is not consulted."""
        db = str(tmp_path / 'fires.db')
        fetched = now() - timedelta(minutes=5)
        record_stored(db, fetched)
        settings = lookup_settings(db, enabled=False)
        settings.data[0].realtime = settings.data[0].realtime.model_copy(update={
            'enrichment': EnrichmentConfig(
                url='https://enrich.test/never-called/{FIRE_NUMBER}?y={FIRE_YEAR}',
                updated_field='updateDate')})

        # No responses mock is registered: any request would raise.
        lookup = self._lookup(settings)

        assert lookup.as_of == fetched


def _square(lon_min, lon_max, lat_min=50.69, lat_max=50.71):
    """A lat/lon rectangle for perimeter-history fixtures."""
    return box(lon_min, lat_min, lon_max, lat_max)


def record_perimeter(db, fetched_at, geometry, fire='K1', size=100.0):
    """Record one stored BC fire snapshot with an explicit perimeter."""
    frame = gpd.GeoDataFrame(
        {
            'Fire': [fire], 'Name': ['Stored Name'], 'Location': ['Stored Creek'],
            'Type': [None], 'Discovered': [None], 'Updated': [None],
            'Size': [size], 'Status': ['Out of Control'], 'StatusLevel': [1],
            'latitude': [FIRE_COORDS[0]], 'longitude': [FIRE_COORDS[1]],
            'fire_key': [f'2026-{fire}'],
        },
        geometry=gpd.GeoSeries([geometry], crs='EPSG:4326'),
    )
    conn = firedb.connect(db)
    try:
        firedb.record_fires(conn, 'BC', frame, fetched_at)
    finally:
        conn.close()


class TestEnrichment:
    """A looked-up fire carries perimeter bounds/area and the edge movement
    derived from its snapshot geometry history."""

    def _lookup(self, settings, term='K1', coords=None):
        with patch('app.fires.lookup.get_config', return_value=settings):
            lookup = FireLookup(term, coords)
            lookup.result()
            return lookup

    def test_perimeter_bounds(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.92))

        lookup = self._lookup(lookup_settings(db, enabled=False))

        minlat, maxlat, minlon, maxlon = lookup.perimeter['bounds']
        assert (minlat, maxlat) == pytest.approx((50.69, 50.71))
        assert (minlon, maxlon) == pytest.approx((-121.95, -121.92))

    def test_synthetic_circle_gets_no_geometry_enrichment(self, tmp_path):
        """A fire whose geometry is the generated size circle (no mapped
        perimeter exists) reports no perimeter or edge lines."""
        db = str(tmp_path / 'fires.db')
        # Ground-true circle, as sources._size_circle produces (a degree-space
        # buffer would be a ground ellipse and correctly fail the detector).
        circle = gpd.GeoSeries([Point(0, 0).buffer(500)],
                               crs=local_crs(FIRE_COORDS)).to_crs('EPSG:4326').iloc[0]
        record_perimeter(db, now() - timedelta(minutes=5), circle)

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.perimeter is None
        assert lookup.edge is None

    def test_spatially_joined_source_gets_no_geometry_enrichment(self, tmp_path):
        """CA-style sources attach perimeter patches by our own proximity
        guesswork; per-fire geometry claims built on it are suppressed."""
        db = str(tmp_path / 'fires.db')
        record_perimeter(db, now() - timedelta(hours=3), _square(-121.95, -121.92))
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.87))
        settings = lookup_settings(db, enabled=False)
        settings.data[0].realtime = settings.data[0].realtime.model_copy(
            update={'join': 'spatial'})

        lookup = self._lookup(settings)

        assert lookup.perimeter is None
        assert lookup.edge is None

    def test_edge_movement_direction_and_magnitude(self, tmp_path):
        """The perimeter grew ~3.5km east between snapshots."""
        db = str(tmp_path / 'fires.db')
        earlier = now() - timedelta(hours=3)
        record_perimeter(db, earlier, _square(-121.95, -121.92))
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.87))

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.edge is not None
        assert lookup.edge['direction'] == 'E'
        assert lookup.edge['advance_m'] == pytest.approx(3500, rel=0.15)
        assert lookup.edge['since'] == earlier
        assert lookup.edge['was_m'] is None

    def test_edge_movement_includes_requester_distance(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_perimeter(db, now() - timedelta(hours=3), _square(-121.95, -121.92))
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.87))

        # Requester due east of the fire: the old edge was farther away.
        lookup = self._lookup(lookup_settings(db, enabled=False), coords=(50.70, -121.60))

        assert lookup.edge['was_m'] is not None
        current_distance = 22_000  # -121.87 to -121.60 is ~19km; old edge ~22.5km
        assert lookup.edge['was_m'] > current_distance

    def test_single_snapshot_has_no_edge_movement(self, tmp_path):
        db = str(tmp_path / 'fires.db')
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.92))

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.edge is None
        assert lookup.perimeter is not None

    def test_invalid_geometry_never_breaks_the_lookup(self, tmp_path):
        """Agency perimeters are often topologically invalid; edge movement
        degrades rather than failing the reply."""
        from shapely.geometry import Polygon
        db = str(tmp_path / 'fires.db')
        bowtie = Polygon([(-121.95, 50.69), (-121.92, 50.71),
                          (-121.92, 50.69), (-121.95, 50.71)])
        record_perimeter(db, now() - timedelta(hours=3), bowtie)
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.87))

        lookup = self._lookup(lookup_settings(db, enabled=False))

        # Must not raise; movement is either computed or absent.
        assert lookup.perimeter is not None

    def test_geometry_errors_skip_the_snapshot(self, tmp_path, monkeypatch):
        """A GEOS failure on one prior snapshot degrades to no edge line."""
        from app.fires import lookup as lookup_mod
        from shapely.errors import GEOSException
        db = str(tmp_path / 'fires.db')
        record_perimeter(db, now() - timedelta(hours=3), _square(-121.95, -121.92))
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.87))
        def explode(current_m, prior_m):
            raise GEOSException('TopologyException: unable to assign free hole')
        monkeypatch.setattr(lookup_mod, '_edge_advance', explode)

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.edge is None
        assert lookup.perimeter is not None

    def test_point_prior_reports_no_movement(self, tmp_path):
        """A perimeter appearing where only a report point existed is the
        mapping catching up, not fire movement; nothing is reported."""
        db = str(tmp_path / 'fires.db')
        record_stored(db, now() - timedelta(hours=3))  # bare point geometry
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.87))

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.edge is None
        assert lookup.perimeter is not None

    def test_unchanged_snapshots_report_no_movement(self, tmp_path):
        """Repeated identical geometry (status-only changes) is not movement."""
        db = str(tmp_path / 'fires.db')
        record_perimeter(db, now() - timedelta(hours=3), _square(-121.95, -121.92), size=99.0)
        record_perimeter(db, now() - timedelta(minutes=5), _square(-121.95, -121.92))

        lookup = self._lookup(lookup_settings(db, enabled=False))

        assert lookup.edge is None
