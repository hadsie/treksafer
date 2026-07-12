"""Tests for the health monitor (scripts/monitor.py)."""

from datetime import datetime, timezone

import pytest
import responses

from scripts import monitor

NOW = datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc)
FRESH = '2026-07-12T16:00:00+00:00'   # 2h old
STALE = '2026-07-11T12:00:00+00:00'   # 30h old


def ok_report(**overrides):
    sources = {s: {'latest_fetch': FRESH} for s in ('BC', 'AB', 'CA', 'US')}
    sources.update(overrides)
    return {'status': 'ok', 'sources': sources}


class TestFetchConditions:
    def test_fresh_sources_are_healthy(self):
        conditions = monitor.fetch_conditions(ok_report(), 12, NOW)

        assert all(problem is None for problem in conditions.values())
        assert set(conditions) == {'app', 'fetch:BC', 'fetch:AB', 'fetch:CA', 'fetch:US'}

    def test_stale_fetch_is_a_problem(self):
        report = ok_report(BC={'latest_fetch': STALE})

        conditions = monitor.fetch_conditions(report, 12, NOW)

        assert '30.0h ago' in conditions['fetch:BC']
        assert conditions['fetch:AB'] is None

    def test_never_fetched_is_a_problem(self):
        report = ok_report(BC={'latest_fetch': None})

        conditions = monitor.fetch_conditions(report, 12, NOW)

        assert 'never fetched' in conditions['fetch:BC']

    def test_failed_probe_reports_only_the_app_condition(self):
        conditions = monitor.fetch_conditions(
            {'status': 'error', 'error': 'boom'}, 12, NOW)

        assert set(conditions) == {'app'}
        assert 'boom' in conditions['app']


class TestLayerConditions:
    URL = 'https://example.test/FeatureServer/0'

    def check(self, stale_hours=24):
        return monitor._check_layer('layer:BC:points', self.URL, stale_hours, NOW)

    @responses.activate
    def test_recently_edited_layer_is_healthy(self):
        edited = int((NOW.timestamp() - 3600) * 1000)
        responses.add(responses.GET, self.URL,
                      json={'editingInfo': {'lastEditDate': edited}})

        assert self.check() is None

    @responses.activate
    def test_stale_layer_is_a_problem(self):
        edited = int((NOW.timestamp() - 30 * 3600) * 1000)
        responses.add(responses.GET, self.URL,
                      json={'editingInfo': {'lastEditDate': edited}})

        assert 'not republished for 30.0h' in self.check()

    @responses.activate
    def test_unreachable_metadata_is_a_problem(self):
        responses.add(responses.GET, self.URL, status=503)

        assert 'metadata query failed' in self.check()

    @responses.activate
    def test_missing_edit_date_is_a_problem(self):
        responses.add(responses.GET, self.URL, json={})

        assert 'no lastEditDate' in self.check()


class TestTransitions:
    def test_new_problem_trips_once(self):
        trips, recoveries = monitor.transitions({'app': 'down'}, {})

        assert trips == ['down']
        assert recoveries == []

    def test_persisting_problem_does_not_realert(self):
        trips, recoveries = monitor.transitions({'app': 'down'}, {'app': 'down'})

        assert trips == []
        assert recoveries == []

    def test_recovery_alerts_once(self):
        trips, recoveries = monitor.transitions({'app': None}, {'app': 'down'})

        assert trips == []
        assert recoveries == ['app recovered']


class TestScanLogErrors:
    def test_reports_only_new_error_lines(self, tmp_path):
        log = tmp_path / 'app.log'
        log.write_text('2026-07-12 10:00:00 app INFO : fine\n'
                       '2026-07-12 10:01:00 app ERROR : first\n')
        state = {}

        first = monitor.scan_log_errors(str(log), state)
        with open(log, 'a') as f:
            f.write('2026-07-12 10:02:00 app ERROR : second\n')
        second = monitor.scan_log_errors(str(log), state)

        assert [line.split(' : ')[1] for line in first] == ['first']
        assert [line.split(' : ')[1] for line in second] == ['second']

    def test_rotated_log_rescans_from_start(self, tmp_path):
        log = tmp_path / 'app.log'
        log.write_text('2026-07-12 10:00:00 app ERROR : old\n' * 5)
        state = {}
        monitor.scan_log_errors(str(log), state)

        log.write_text('2026-07-12 11:00:00 app ERROR : fresh\n')

        assert len(monitor.scan_log_errors(str(log), state)) == 1

    def test_missing_log_is_empty(self, tmp_path):
        assert monitor.scan_log_errors(str(tmp_path / 'nope.log'), {}) == []


class TestRun:
    """End-to-end through run() with the probe and delivery mocked."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        """Isolated state file, quiet log, healthy probe, capturing notify."""
        from app.config import get_config
        settings = get_config()
        monkeypatch.setattr(settings.monitoring, 'state_file',
                            str(tmp_path / 'state.json'))
        monkeypatch.setattr(settings, 'log_file', str(tmp_path / 'app.log'))
        sent = []
        monkeypatch.setattr(monitor, 'notify',
                            lambda title, body: sent.append((title, body)) or True)
        monkeypatch.setattr(monitor, 'layer_conditions', lambda *a: {})
        monkeypatch.setattr(monitor, 'probe_health', lambda *a: ok_report())
        return {'settings': settings, 'sent': sent, 'monkeypatch': monkeypatch}

    def test_healthy_run_sends_nothing_and_pings(self, env, monkeypatch):
        pinged = []
        monkeypatch.setattr(env['settings'].monitoring, 'healthcheck_url',
                            'https://hc.test/ping')
        monkeypatch.setattr(monitor.requests, 'get',
                            lambda url, timeout: pinged.append(url))

        assert monitor.run(env['settings'], NOW) == 0
        assert env['sent'] == []
        assert pinged == ['https://hc.test/ping']

    def test_trip_alerts_once_then_recovery(self, env, monkeypatch):
        monkeypatch.setattr(monitor, 'probe_health',
                            lambda *a: ok_report(BC={'latest_fetch': STALE}))
        monitor.run(env['settings'], NOW)
        monitor.run(env['settings'], NOW)   # unchanged: no re-alert

        monkeypatch.setattr(monitor, 'probe_health', lambda *a: ok_report())
        monitor.run(env['settings'], NOW)   # recovery

        titles = [title for title, _ in env['sent']]
        assert titles == ['TrekSafer ALERT', 'TrekSafer recovered']

    def test_failed_delivery_retries_next_run(self, env, monkeypatch):
        monkeypatch.setattr(monitor, 'probe_health',
                            lambda *a: ok_report(BC={'latest_fetch': STALE}))
        failed = []
        monkeypatch.setattr(monitor, 'notify',
                            lambda title, body: failed.append(title) and False)

        assert monitor.run(env['settings'], NOW) == 1
        monkeypatch.setattr(monitor, 'notify',
                            lambda title, body: env['sent'].append((title, body)) or True)
        monitor.run(env['settings'], NOW)

        assert len(failed) == 1
        assert [title for title, _ in env['sent']] == ['TrekSafer ALERT']

    def test_new_log_errors_are_reported(self, env):
        with open(env['settings'].log_file, 'w') as f:
            f.write('2026-07-12 10:00:00 app ERROR : Unmapped BC fire status\n')

        monitor.run(env['settings'], NOW)
        monitor.run(env['settings'], NOW)   # same lines: not re-reported

        titles = [title for title, _ in env['sent']]
        assert titles == ['TrekSafer log errors']
        assert 'Unmapped BC fire status' in env['sent'][0][1]
