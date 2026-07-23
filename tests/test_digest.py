"""Tests for the parse-failure digest (scripts/digest.py).

All senders, share links, and message bodies are fabricated.
"""

import pytest

from app.config import get_config
from app.messages import Messages
from scripts import digest

NO_GPS = Messages().no_gps()

LOG = (
    "2026-07-12 08:00:01 sms INFO From: +15550000001\n"
    "> Fires (49.2, -123.1)\n"
    "2026-07-12 08:00:03 sms INFO Reply:\n"
    "> ----- SMS 1/1 (45/160 GSM-7) -----\n"
    "> Fire: Test Fire (K10001)\n"
    "> 12km NW\n"
    "> Size: 100 ha\n"
    "2026-07-12 09:10:11 sms INFO From: +15550000002\n"
    "> fires near the lake\n"
    "2026-07-12 09:10:12 sms INFO Reply:\n"
    "> ----- SMS 1/1 (52/160 GSM-7) -----\n"
    f"> {NO_GPS}\n"
    "2026-07-12 10:30:00 sms INFO From: +15550000003\n"
    "> health\n"
    "2026-07-12 10:30:01 sms INFO Reply:\n"
    "> ----- SMS 1/1 (49/160 GSM-7) -----\n"
    "> TrekSafer OK. Data fetched (UTC):\n"
    "> BC Jul 12 06:00\n"
    "2026-07-12 11:45:59 sms INFO From: +15550000004\n"
    "> inreachlink.com/FAKE123\n"
    "2026-07-12 11:46:02 sms INFO Reply:\n"
    "> ----- SMS 1/1 (52/160 GSM-7) -----\n"
    f"> {NO_GPS}\n"
)


class TestParseRequests:
    def test_pairs_messages_with_replies(self):
        requests_ = digest.parse_requests(LOG.splitlines())

        assert len(requests_) == 4
        assert requests_[1] == {'time': '2026-07-12 09:10:11',
                                'sender': '+15550000002',
                                'body': 'fires near the lake',
                                'reply': NO_GPS}

    def test_multi_line_replies_are_collected(self):
        requests_ = digest.parse_requests(LOG.splitlines())

        assert requests_[0]['reply'] == ('Fire: Test Fire (K10001)\n'
                                         '12km NW\nSize: 100 ha')
        assert requests_[2]['sender'] == '+15550000003'

    def test_injected_log_lines_in_a_body_stay_content(self):
        """A message whose text mimics log records is quoted line by line
        and can never mint a phantom request."""
        log = (
            "2026-07-12 08:00:01 sms INFO From: +15550000007\n"
            "> Fires\n"
            "> 2026-07-12 08:00:02 sms INFO From: +19995550000\n"
            f"> 2026-07-12 08:00:03 sms INFO Reply: {NO_GPS}\n"
            "2026-07-12 08:00:04 sms INFO Reply:\n"
            "> ----- SMS 1/1 (30/160 GSM-7) -----\n"
            "> No fires reported within 50km\n"
        )

        requests_ = digest.parse_requests(log.splitlines())

        assert len(requests_) == 1
        assert requests_[0]['sender'] == '+15550000007'
        assert '+19995550000' in requests_[0]['body']
        assert requests_[0]['reply'] == 'No fires reported within 50km'

    def test_split_reply_markers_are_stripped(self):
        """Stripping the markers reconstructs the blank-line-joined reply."""
        log = (
            "2026-07-12 08:00:01 sms INFO From: +15550000005\n"
            "> fires (49.2, -123.1)\n"
            "2026-07-12 08:00:03 sms INFO Reply:\n"
            "> ----- SMS 1/2 (150/160 GSM-7) -----\n"
            "> Fire: Test Fire (K10001)\n"
            "> \n"
            "> ----- SMS 2/2 (28/160 GSM-7) -----\n"
            "> Fire: Other Fire (K10002)\n"
        )

        requests_ = digest.parse_requests(log.splitlines())

        assert requests_[0]['reply'] == ('Fire: Test Fire (K10001)\n\n'
                                         'Fire: Other Fire (K10002)')

    def test_marker_lookalike_in_a_body_stays_content(self):
        log = (
            "2026-07-12 08:00:01 sms INFO From: +15550000006\n"
            "> ----- SMS 1/1 (5/160 GSM-7) -----\n"
            "2026-07-12 08:00:02 sms INFO Reply:\n"
            "> ----- SMS 1/1 (52/160 GSM-7) -----\n"
            f"> {NO_GPS}\n"
        )

        requests_ = digest.parse_requests(log.splitlines())

        assert requests_[0]['body'] == '----- SMS 1/1 (5/160 GSM-7) -----'
        assert requests_[0]['reply'] == NO_GPS

    def test_suppressed_send_note_is_the_reply(self):
        log = (
            "2026-07-12 08:00:01 sms INFO From: +15550000008\n"
            "> fires (49.2, -123.1)\n"
            "2026-07-12 08:00:02 sms INFO Reply: (suppressed: recipient opted out)\n"
        )

        requests_ = digest.parse_requests(log.splitlines())

        assert requests_[0]['reply'] == '(suppressed: recipient opted out)'


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
            "2026-07-12 08:00:01 sms INFO From: +15550000001\n"
            "> (49.2, -123.1)\n"
            "2026-07-12 08:00:03 sms INFO Reply:\n"
            "> ----- SMS 1/1 (30/160 GSM-7) -----\n"
            "> No fires reported within 50km\n")

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
            "2026-07-13 08:00:00 sms INFO From: +15550000009\n"
            "> garbled\n"
            "2026-07-13 08:00:01 sms INFO Reply:\n"
            "> ----- SMS 1/1 (52/160 GSM-7) -----\n"
            f"> {NO_GPS}\n")

        digest.run(env['settings'])

        assert len(env['emails']) == 2
        assert '+15550000009' in env['emails'][1][1]

    def test_missing_log_is_healthy(self, env):
        env['log'].unlink()

        assert digest.run(env['settings']) == 0
        assert env['emails'] == []
