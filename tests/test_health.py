"""Tests for the operator health report (app/health.py)."""

from app.config import get_config
from app.health import health_report
from tests.conftest import FIXTURE_FETCHED_AT


class TestHealthReport:
    def test_reports_all_configured_sources(self):
        report = health_report()

        assert report['status'] == 'ok'
        assert set(report['sources']) == {'BC', 'AB', 'ON', 'CA', 'US'}
        for source in report['sources'].values():
            assert source['latest_fetch'] == FIXTURE_FETCHED_AT.isoformat()

    def test_never_fetched_source_reports_null_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_config(), 'database', str(tmp_path / 'empty.db'))

        report = health_report()

        assert report['status'] == 'ok'
        assert report['sources']['BC'] == {'latest_fetch': None}

    def test_unreadable_database_reports_error(self, tmp_path, monkeypatch):
        # A directory is not openable as a database file.
        monkeypatch.setattr(get_config(), 'database', str(tmp_path))

        report = health_report()

        assert report['status'] == 'error'
        assert report['error']
