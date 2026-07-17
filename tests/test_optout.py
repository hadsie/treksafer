"""Tests for the SMS opt-out store (app/optout.py)."""

from app import optout


class TestOptOutStore:
    def test_round_trip(self, tmp_path):
        """Each call opens a fresh connection, so this also covers
        persistence across connections."""
        db = str(tmp_path / 'optouts.db')
        assert not optout.is_opted_out(db, '+15551230001')

        optout.opt_out(db, '+15551230001')
        assert optout.is_opted_out(db, '+15551230001')
        assert not optout.is_opted_out(db, '+15551230002')

        optout.opt_in(db, '+15551230001')
        assert not optout.is_opted_out(db, '+15551230001')

    def test_opt_out_is_idempotent(self, tmp_path):
        db = str(tmp_path / 'optouts.db')
        optout.opt_out(db, '+15551230001')
        optout.opt_out(db, '+15551230001')

        assert optout.is_opted_out(db, '+15551230001')

    def test_opt_in_without_opt_out_is_a_no_op(self, tmp_path):
        db = str(tmp_path / 'optouts.db')
        optout.opt_in(db, '+15551230001')

        assert not optout.is_opted_out(db, '+15551230001')

    def test_creates_missing_directory(self, tmp_path):
        db = str(tmp_path / 'nested' / 'optouts.db')
        optout.opt_out(db, '+15551230001')

        assert optout.is_opted_out(db, '+15551230001')
