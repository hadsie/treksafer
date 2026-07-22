"""Tests for the compliance-list manager (scripts/contacts.py).

All senders and message bodies are fabricated.
"""

import pytest

from app import optout
from app.config import get_config
from scripts import contacts

LOG = (
    "2026-07-12 08:00:01 sms INFO From: +15550000001\n"
    "> Fires (49.2, -123.1)\n"
    "2026-07-12 08:00:03 sms INFO Reply:\n"
    "> Fire: Test Fire (K10001)\n"
    "2026-07-12 09:10:11 sms INFO From: +15550000002\n"
    "> fires near the lake\n"
    "2026-07-12 09:10:12 sms INFO Reply:\n"
    "> No valid GPS coordinates found.\n"
    "2026-07-12 10:30:00 sms INFO From: +15550000001\n"
    "> health\n"
    "2026-07-12 10:30:01 sms INFO Reply:\n"
    "> TrekSafer OK. Data fetched (UTC):\n"
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / 'optouts.db')
    monkeypatch.setattr(get_config(), 'optout_database', path)
    return path


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / 'sms.log'
    path.write_text(LOG)
    return str(path)


class TestImportLogs:
    def test_records_each_sender_once(self, db, log_file):
        added = contacts.import_logs(db, [log_file])

        assert added == 2
        assert not optout.first_contact(db, '+15550000001')
        assert not optout.first_contact(db, '+15550000002')

    def test_reimport_adds_nothing(self, db, log_file):
        contacts.import_logs(db, [log_file])

        assert contacts.import_logs(db, [log_file]) == 0

    def test_missing_file_is_skipped_with_a_warning(self, db, log_file, tmp_path, capsys):
        missing = str(tmp_path / 'rotated.log')

        added = contacts.import_logs(db, [missing, log_file])

        assert added == 2
        assert f"Skipping {missing}" in capsys.readouterr().err

    def test_non_number_senders_are_skipped(self, db, tmp_path, capsys):
        log = tmp_path / 'sms.log'
        log.write_text("2026-07-12 08:00:01 sms INFO From: not-a-number\n"
                       "> hello\n"
                       "2026-07-12 08:00:02 sms INFO Reply:\n"
                       "> hi\n")

        assert contacts.import_logs(db, [str(log)]) == 0
        assert "unparseable sender" in capsys.readouterr().err


class TestManageCommands:
    def test_add_then_remove_round_trip(self, db, capsys):
        assert contacts.main(['add', '+15550000009']) == 0
        assert not optout.first_contact(db, '+15550000009')

        assert contacts.main(['remove', '+15550000009']) == 0
        assert optout.first_contact(db, '+15550000009')

    def test_remove_reports_an_unknown_number(self, db, capsys):
        assert contacts.main(['remove', '+15550000009']) == 0

        assert 'was not on the contact list' in capsys.readouterr().out

    @pytest.mark.parametrize('number', ['5550000009', '+1555abc', 'STOP', ''])
    def test_invalid_numbers_are_rejected(self, db, number, capsys):
        assert contacts.main(['add', number]) == 2

        assert 'Not a valid number' in capsys.readouterr().err

    def test_list_shows_contacts_and_opt_out_state(self, db, capsys):
        optout.first_contact(db, '+15550000001')
        optout.first_contact(db, '+15550000002')
        optout.opt_out(db, '+15550000002')
        optout.opt_out(db, '+15550000003')

        assert contacts.main(['list']) == 0

        out = capsys.readouterr().out
        assert '2 contact(s), 2 opted out:' in out
        assert '+15550000001' in out
        assert '+15550000002  first seen' in out and '[opted out]' in out
        assert '+15550000003  [opted out, no contact record]' in out

    def test_import_logs_defaults_to_configured_log(self, db, log_file, monkeypatch, capsys):
        monkeypatch.setattr(get_config().monitoring, 'sms_log_file', log_file)

        assert contacts.main(['import-logs']) == 0

        assert '2 new contact(s) recorded.' in capsys.readouterr().out
