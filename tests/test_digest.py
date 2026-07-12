"""Tests for the parse-failure digest (scripts/digest.py).

All senders, share links, and message bodies are fabricated.
"""

import pytest

from app.config import get_config
from app.messages import Messages
from scripts import digest

NO_GPS = Messages().no_gps()

LOG = (
    "2026-07-12 08:00:01 sms INFO From: +15550000001, Body: Fires (49.2, -123.1)\n"
    "2026-07-12 08:00:03 sms INFO Reply: Fire: Test Fire (K10001)\n"
    "12km NW\n"
    "Size: 100 ha\n"
    f"2026-07-12 09:10:11 sms INFO From: +15550000002, Body: fires near the lake\n"
    f"2026-07-12 09:10:12 sms INFO Reply: {NO_GPS}\n"
    "2026-07-12 10:30:00 sms INFO From: +15550000003, Body: health\n"
    "2026-07-12 10:30:01 sms INFO Reply: TrekSafer OK. Data fetched (UTC):\n"
    "BC Jul 12 06:00\n"
    f"2026-07-12 11:45:59 sms INFO From: +15550000004, Body: inreachlink.com/FAKE123\n"
    f"2026-07-12 11:46:02 sms INFO Reply: {NO_GPS}\n"
)


class TestParseRequests:
    def test_pairs_messages_with_replies(self):
        requests_ = digest.parse_requests(LOG.splitlines())

        assert len(requests_) == 4
        assert requests_[1] == {'time': '2026-07-12 09:10:11',
                                'sender': '+15550000002',
                                'body': 'fires near the lake',
                                'reply': NO_GPS}

    def test_multi_line_replies_do_not_confuse_pairing(self):
        requests_ = digest.parse_requests(LOG.splitlines())

        assert requests_[0]['reply'] == 'Fire: Test Fire (K10001)'
        assert requests_[2]['sender'] == '+15550000003'


class TestRun:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        settings = get_config()
        log = tmp_path / 'sms.log'
        log.write_text(LOG)
        monkeypatch.setattr(settings.monitoring, 'sms_log_file', str(log))
        monkeypatch.setattr(settings.monitoring, 'digest_state_file',
                            str(tmp_path / 'digest_state.json'))
        emails = []
        monkeypatch.setattr(digest, 'notify_email',
                            lambda subject, body: emails.append((subject, body)) or True)
        return {'settings': settings, 'log': log, 'emails': emails,
                'monkeypatch': monkeypatch}

    def test_emails_only_the_parse_failures(self, env):
        assert digest.run(env['settings']) == 0

        (subject, body), = env['emails']
        assert '2 request(s) with no usable coordinates' in subject
        assert '2 of 4 request(s)' in body
        assert '+15550000002' in body and 'fires near the lake' in body
        assert '+15550000004' in body and 'inreachlink.com/FAKE123' in body
        assert '+15550000001' not in body

    def test_second_run_does_not_rereport(self, env):
        digest.run(env['settings'])
        digest.run(env['settings'])

        assert len(env['emails']) == 1

    def test_no_failures_sends_nothing(self, env):
        env['log'].write_text(
            "2026-07-12 08:00:01 sms INFO From: +15550000001, Body: (49.2, -123.1)\n"
            "2026-07-12 08:00:03 sms INFO Reply: No fires reported within 50km\n")

        assert digest.run(env['settings']) == 0
        assert env['emails'] == []

    def test_failed_email_retries_next_run(self, env):
        env['monkeypatch'].setattr(digest, 'notify_email', lambda *a: False)

        assert digest.run(env['settings']) == 1

        env['monkeypatch'].setattr(
            digest, 'notify_email',
            lambda subject, body: env['emails'].append((subject, body)) or True)
        digest.run(env['settings'])
        assert len(env['emails']) == 1

    def test_rotated_log_rescans_from_start(self, env):
        digest.run(env['settings'])
        env['log'].write_text(
            "2026-07-13 08:00:00 sms INFO From: +15550000009, Body: garbled\n"
            f"2026-07-13 08:00:01 sms INFO Reply: {NO_GPS}\n")

        digest.run(env['settings'])

        assert len(env['emails']) == 2
        assert '+15550000009' in env['emails'][1][1]

    def test_missing_log_is_healthy(self, env):
        env['log'].unlink()

        assert digest.run(env['settings']) == 0
        assert env['emails'] == []
