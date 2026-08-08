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

    @responses.activate
    def test_null_stale_hours_skips_the_source_entirely(self):
        """A source whose server publishes no lastEditDate (ON) opts out
        via layer_stale_hours: null. No mocked responses are registered,
        so any metadata request here would fail the test."""
        from app.config import get_config
        on = next(d for d in get_config().data if d.location == 'ON')
        on = on.model_copy(deep=True)
        on.realtime.enabled = True

        assert monitor.layer_conditions([on], NOW) == {}


class TestTransitions:
    def test_new_problem_trips_once(self):
        trips, recoveries, held = monitor.transitions({'app': 'down'}, {}, {})

        assert trips == ['down']
        assert recoveries == []
        assert held == {}

    def test_persisting_problem_does_not_realert(self):
        trips, recoveries, held = monitor.transitions(
            {'app': 'down'}, {'app': 'down'}, {})

        assert trips == []
        assert recoveries == []

    def test_recovery_alerts_once(self):
        trips, recoveries, held = monitor.transitions(
            {'app': None}, {'app': 'down'}, {})

        assert trips == []
        assert recoveries == ['app recovered']

    def test_first_layer_problem_is_held_not_tripped(self):
        trips, recoveries, held = monitor.transitions(
            {'layer:BC:points': 'dns blip'}, {}, {})

        assert trips == []
        assert held == {'layer:BC:points': 'dns blip'}

    def test_layer_problem_on_second_consecutive_run_trips(self):
        trips, recoveries, held = monitor.transitions(
            {'layer:BC:points': 'still down'}, {},
            {'layer:BC:points': 'dns blip'})

        assert trips == ['still down']
        assert held == {}

    def test_held_layer_problem_that_clears_is_dropped_silently(self):
        trips, recoveries, held = monitor.transitions(
            {'layer:BC:points': None}, {}, {'layer:BC:points': 'dns blip'})

        assert trips == []
        assert recoveries == []
        assert held == {}

    def test_alerted_layer_problem_still_recovers(self):
        trips, recoveries, held = monitor.transitions(
            {'layer:BC:points': None}, {'layer:BC:points': 'down'}, {})

        assert recoveries == ['layer:BC:points recovered']


class TestLayerCheckDue:
    def test_due_when_never_checked(self):
        assert monitor.layer_check_due({}, 12, NOW)

    def test_not_due_within_interval(self):
        state = {'last_layer_check': '2026-07-12T10:00:00+00:00'}   # 8h ago

        assert not monitor.layer_check_due(state, 12, NOW)

    def test_due_after_interval(self):
        state = {'last_layer_check': '2026-07-12T05:00:00+00:00'}   # 13h ago

        assert monitor.layer_check_due(state, 12, NOW)

    def test_pending_layer_problem_forces_check(self):
        state = {'last_layer_check': '2026-07-12T17:45:00+00:00',
                 'pending': {'layer:BC:points': 'dns blip'}}

        assert monitor.layer_check_due(state, 12, NOW)

    def test_tripped_layer_problem_forces_check(self):
        state = {'last_layer_check': '2026-07-12T17:45:00+00:00',
                 'conditions': {'layer:BC:points': 'down'}}

        assert monitor.layer_check_due(state, 12, NOW)

    def test_non_layer_conditions_do_not_force_check(self):
        state = {'last_layer_check': '2026-07-12T17:45:00+00:00',
                 'conditions': {'fetch:BC': 'stale'}}

        assert not monitor.layer_check_due(state, 12, NOW)


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

    def test_failed_delivery_retries_next_run_and_skips_ping(self, env, monkeypatch):
        monkeypatch.setattr(monitor, 'probe_health',
                            lambda *a: ok_report(BC={'latest_fetch': STALE}))
        monkeypatch.setattr(env['settings'].monitoring, 'healthcheck_url',
                            'https://hc.test/ping')
        pinged = []
        monkeypatch.setattr(monitor.requests, 'get',
                            lambda url, timeout: pinged.append(url))
        failed = []
        monkeypatch.setattr(monitor, 'notify',
                            lambda title, body: failed.append(title) and False)

        assert monitor.run(env['settings'], NOW) == 1
        assert pinged == []
        monkeypatch.setattr(monitor, 'notify',
                            lambda title, body: env['sent'].append((title, body)) or True)
        monitor.run(env['settings'], NOW)

        assert len(failed) == 1
        assert [title for title, _ in env['sent']] == ['TrekSafer ALERT']

    def test_layer_blip_for_one_run_stays_silent(self, env, monkeypatch):
        monkeypatch.setattr(monitor, 'layer_conditions',
                            lambda *a: {'layer:BC:points': 'dns blip'})
        monitor.run(env['settings'], NOW)

        monkeypatch.setattr(monitor, 'layer_conditions', lambda *a: {'layer:BC:points': None})
        monitor.run(env['settings'], NOW)

        assert env['sent'] == []

    def test_healthy_layers_are_checked_on_the_interval_not_every_run(self, env, monkeypatch):
        calls = []
        monkeypatch.setattr(monitor, 'layer_conditions',
                            lambda *a: calls.append(1) or {'layer:BC:points': None})
        monitor.run(env['settings'], NOW)   # never checked: runs
        monitor.run(env['settings'], NOW)   # within interval: skipped
        monitor.run(env['settings'], NOW)

        assert len(calls) == 1

    def test_layer_problem_persisting_two_runs_alerts_then_recovers(self, env, monkeypatch):
        monkeypatch.setattr(monitor, 'layer_conditions',
                            lambda *a: {'layer:BC:points': 'metadata query failed'})
        monitor.run(env['settings'], NOW)   # held, no alert
        monitor.run(env['settings'], NOW)   # confirmed: alert
        monitor.run(env['settings'], NOW)   # ongoing: no re-alert

        monkeypatch.setattr(monitor, 'layer_conditions', lambda *a: {'layer:BC:points': None})
        monitor.run(env['settings'], NOW)   # recovery

        titles = [title for title, _ in env['sent']]
        assert titles == ['TrekSafer ALERT', 'TrekSafer recovered']

    def test_new_log_errors_are_reported(self, env):
        with open(env['settings'].log_file, 'w') as f:
            f.write('2026-07-12 10:00:00 app ERROR : Unmapped BC fire status\n')

        monitor.run(env['settings'], NOW)
        monitor.run(env['settings'], NOW)   # same lines: not re-reported

        titles = [title for title, _ in env['sent']]
        assert titles == ['TrekSafer log errors']
        assert 'Unmapped BC fire status' in env['sent'][0][1]
