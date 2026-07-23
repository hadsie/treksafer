"""Tests for the daily digest (scripts/digest.py).

All senders, coordinates, and message bodies are fabricated.
"""
import pytest

from app import request_log
from app.config import get_config
from scripts import digest


class TestRun:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        settings = get_config()
        monkeypatch.setattr(settings.monitoring, 'digest_state_file',
                            str(tmp_path / 'digest_state.json'))
        # Requests recorded by other tests must not leak into this run.
        monkeypatch.setattr(settings, 'request_database',
                            str(tmp_path / 'requests.db'))
        emails = []
        monkeypatch.setattr(digest, 'notify_email',
                            lambda subject, body: emails.append((subject, body)) or True)
        return {'settings': settings, 'emails': emails,
                'monkeypatch': monkeypatch}

    def _seed(self, settings, sender, message, coords, response_type):
        request_log.record(settings.request_database, sender, message,
                           coords, response_type)

    def test_emails_the_parse_failures(self, env):
        self._seed(env['settings'], '+15550000001', 'fires broken', None, 'no_gps')
        self._seed(env['settings'], '+15550000002', 'fires (49.1, -120.2)',
                   (49.1, -120.2), 'fires')

        assert digest.run(env['settings']) == 0

        (subject, body), = env['emails']
        assert '1 request(s) with no usable coordinates' in subject
        assert '1 of 2 request(s)' in body
        assert 'fires broken' in body
        assert '+15550000001' in body

    def test_volume_counts_ride_along(self, env):
        self._seed(env['settings'], '+15550000001', 'fires broken', None, 'no_gps')

        digest.run(env['settings'])

        (_, body), = env['emails']
        assert 'no_gps 1' in body

    def test_re_request_pair_triggers_and_reports(self, env):
        self._seed(env['settings'], '+15550000003', 'fires (49.1, -120.2)',
                   (49.1, -120.2), 'fires')
        self._seed(env['settings'], '+15550000003', 'fires (49.5, -120.9)',
                   (49.5, -120.9), 'fires')

        digest.run(env['settings'])

        (subject, body), = env['emails']
        assert 'possible wrong-coordinate re-request' in subject
        assert '(49.10000, -120.20000)' in body
        assert '(49.50000, -120.90000)' in body

    def test_second_run_does_not_rereport(self, env):
        self._seed(env['settings'], '+15550000001', 'fires broken', None, 'no_gps')

        digest.run(env['settings'])
        digest.run(env['settings'])

        assert len(env['emails']) == 1

    def test_no_problems_sends_nothing(self, env):
        self._seed(env['settings'], '+15550000002', 'fires (49.1, -120.2)',
                   (49.1, -120.2), 'fires')

        assert digest.run(env['settings']) == 0
        assert env['emails'] == []

    def test_empty_log_is_healthy(self, env):
        assert digest.run(env['settings']) == 0
        assert env['emails'] == []

    def test_failed_email_retries_next_run(self, env):
        self._seed(env['settings'], '+15550000001', 'fires broken', None, 'no_gps')
        env['monkeypatch'].setattr(digest, 'notify_email', lambda s, b: False)

        assert digest.run(env['settings']) == 1

        env['monkeypatch'].setattr(
            digest, 'notify_email',
            lambda s, b: env['emails'].append((s, b)) or True)
        assert digest.run(env['settings']) == 0
        assert len(env['emails']) == 1


class TestReRequestPairs:
    """Quick follow-up requests from one sender pair up for the digest."""

    def _row(self, sender, at, response_type='fires', message='fires (49.1, -120.2)',
             lat=49.1, lon=-120.2):
        return {'received_at': at, 'sender': sender, 'message': message,
                'lat': lat, 'lon': lon, 'response_type': response_type}

    def test_pairs_within_window(self):
        rows = [self._row('+15550000001', '2026-07-22T18:00:00+00:00'),
                self._row('+15550000001', '2026-07-22T18:02:30+00:00')]
        assert len(digest.re_request_pairs(rows)) == 1

    def test_no_pair_outside_window(self):
        rows = [self._row('+15550000001', '2026-07-22T18:00:00+00:00'),
                self._row('+15550000001', '2026-07-22T18:05:00+00:00')]
        assert digest.re_request_pairs(rows) == []

    def test_different_senders_never_pair(self):
        rows = [self._row('+15550000001', '2026-07-22T18:00:00+00:00'),
                self._row('+15550000002', '2026-07-22T18:01:00+00:00')]
        assert digest.re_request_pairs(rows) == []

    def test_service_keywords_do_not_pair(self):
        rows = [self._row('+15550000001', '2026-07-22T18:00:00+00:00'),
                self._row('+15550000001', '2026-07-22T18:01:00+00:00',
                          response_type='usage', lat=None, lon=None)]
        assert digest.re_request_pairs(rows) == []

    def test_three_quick_requests_make_two_pairs(self):
        rows = [self._row('+15550000001', '2026-07-22T18:00:00+00:00'),
                self._row('+15550000001', '2026-07-22T18:01:00+00:00'),
                self._row('+15550000001', '2026-07-22T18:02:00+00:00')]
        assert len(digest.re_request_pairs(rows)) == 2

    def test_pair_formatting_shows_both_coordinate_sets(self):
        pair = (self._row('+15550000001', '2026-07-22T18:00:00+00:00'),
                self._row('+15550000001', '2026-07-22T18:02:00+00:00',
                          message='fires (49.5, -120.9)', lat=49.5, lon=-120.9))
        text = digest.format_pairs([pair])
        assert '(49.10000, -120.20000)' in text
        assert '(49.50000, -120.90000)' in text
        assert 'fires (49.5, -120.9)' in text


class TestVolumeSection:
    def test_counts_by_outcome(self):
        from datetime import datetime, timezone
        rows = [{'response_type': t} for t in ('fires', 'fires', 'no_gps')]
        text = digest.format_volume(
            rows, datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc))
        assert '3 request(s) since Jul 21 12:00 UTC' in text
        assert 'fires 2' in text and 'no_gps 1' in text
