"""Tests for the inbound request log (app/request_log.py)."""
from datetime import datetime, timedelta, timezone

from app import request_log


def _db(tmp_path):
    return str(tmp_path / 'requests.db')


class TestRecord:
    def test_roundtrip(self, tmp_path):
        path = _db(tmp_path)
        request_log.record(path, '+15550000001', 'fires (49.1, -120.2)',
                           (49.1, -120.2), 'fires')

        rows = request_log.requests_since(
            path, datetime.now(timezone.utc) - timedelta(minutes=1))

        assert len(rows) == 1
        row = rows[0]
        assert row['sender'] == '+15550000001'
        assert row['message'] == 'fires (49.1, -120.2)'
        assert (row['lat'], row['lon']) == (49.1, -120.2)
        assert row['response_type'] == 'fires'

    def test_no_coordinates_stored_as_null(self, tmp_path):
        path = _db(tmp_path)
        request_log.record(path, '+15550000001', 'fires please', None,
                           'no_gps')

        row = request_log.requests_since(
            path, datetime.now(timezone.utc) - timedelta(minutes=1))[0]

        assert row['lat'] is None and row['lon'] is None

    def test_rows_returned_oldest_first(self, tmp_path):
        path = _db(tmp_path)
        for n in range(3):
            request_log.record(path, '+15550000001', f'message {n}', None,
                               'no_gps')

        rows = request_log.requests_since(
            path, datetime.now(timezone.utc) - timedelta(minutes=1))

        assert [r['message'] for r in rows] == ['message 0', 'message 1', 'message 2']

    def test_since_excludes_older_rows(self, tmp_path):
        path = _db(tmp_path)
        request_log.record(path, '+15550000001', 'old', None, 'no_gps')

        rows = request_log.requests_since(
            path, datetime.now(timezone.utc) + timedelta(minutes=1))

        assert rows == []

