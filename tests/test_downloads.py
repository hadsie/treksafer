"""Tests for the fire database refresh script (scripts/downloads.py)."""

import geopandas as gpd
import requests
from shapely.geometry import Point

from app.fires import db as firedb
from app.config import get_config
from scripts import downloads


def bc_fires_gdf():
    """A realtime-shaped BC frame as fetch_all_fires would return it."""
    return gpd.GeoDataFrame(
        {
            'FIRE_NUMBER': ['K1', 'K2'],
            'FIRE_YEAR': [2026, 2026],
            'INCIDENT_NAME': ['Test Fire', 'K2'],
            'GEOGRAPHIC_DESCRIPTION': ['Test Creek', 'Other Creek'],
            'CURRENT_SIZE': [25.0, 3.1],
            'FIRE_STATUS': ['Out of Control', 'Under Control'],
            'IGNITION_DATE': [1782000000000, None],
            'latitude': [50.6, 50.7],
            'longitude': [-120.3, -120.4],
        },
        geometry=[Point(-120.3, 50.6), Point(-120.4, 50.7)],
        crs='EPSG:4326',
    )


def bc_data_file():
    return next(d for d in get_config().data if d.location == 'BC')


class TestRefreshSource:
    def test_records_normalized_snapshots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloads, 'fetch_all_fires', lambda config: bc_fires_gdf())
        conn = firedb.connect(str(tmp_path / 'fires.db'))

        written = downloads.refresh_source(conn, bc_data_file())

        assert written == 2
        rows = conn.execute(
            "SELECT fire_key, fire, name, location FROM fires ORDER BY fire"
        ).fetchall()
        assert rows == [
            ('2026-K1', 'K1', 'Test Fire', 'Test Creek'),
            ('2026-K2', 'K2', 'K2', 'Other Creek'),
        ]
        snapshot = conn.execute(
            "SELECT size_ha, status, status_level FROM snapshots ORDER BY id"
        ).fetchone()
        assert snapshot == (25.0, 'Out of Control', 1)
        conn.close()

    def test_unchanged_refetch_writes_no_snapshots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloads, 'fetch_all_fires', lambda config: bc_fires_gdf())
        conn = firedb.connect(str(tmp_path / 'fires.db'))

        downloads.refresh_source(conn, bc_data_file())
        written = downloads.refresh_source(conn, bc_data_file())

        assert written == 0
        conn.close()


class TestMain:
    def _empty(self):
        return gpd.GeoDataFrame(columns=['FIRE_NUMBER', 'geometry'],
                                geometry='geometry', crs='EPSG:4326')

    def _settings(self, tmp_path):
        settings = get_config().model_copy(deep=True)
        settings.database = str(tmp_path / 'fires.db')
        return settings

    def test_transient_failure_recovered_by_retry(self, tmp_path, monkeypatch, capsys):
        """A rate-limited source succeeds on the retry round; exit code 0."""
        calls = []

        def fake_fetch(config):
            calls.append(config)
            if len(calls) == 1:
                raise requests.ConnectionError('429 too many requests')
            return self._empty()

        sleeps = []
        monkeypatch.setattr(downloads, 'get_config', lambda: self._settings(tmp_path))
        monkeypatch.setattr(downloads, 'fetch_all_fires', fake_fetch)
        monkeypatch.setattr(downloads.time, 'sleep', sleeps.append)

        exit_code = downloads.main()

        # Recovered on the first retry round; no further attempts.
        assert exit_code == 0
        assert sleeps == [downloads.RETRY_DELAY_S]
        assert 'Retrying 1 failed source(s)' in capsys.readouterr().out

    def test_persistent_failure_reported_after_retry(self, tmp_path, monkeypatch, capsys):
        """A source that fails both rounds is reported; the others complete."""
        def fake_fetch(config):
            if config.points_where.startswith('Agency'):  # the CA source
                raise requests.ConnectionError('boom')
            return self._empty()

        sleeps = []
        monkeypatch.setattr(downloads, 'get_config', lambda: self._settings(tmp_path))
        monkeypatch.setattr(downloads, 'fetch_all_fires', fake_fetch)
        monkeypatch.setattr(downloads.time, 'sleep', sleeps.append)

        exit_code = downloads.main()

        assert exit_code == 1
        assert len(sleeps) == downloads.MAX_RETRIES
        out = capsys.readouterr().out
        assert '1 source(s) failed: CA' in out
