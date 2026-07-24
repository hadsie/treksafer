from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
import requests

from app.messages import Messages, handle_fire_request, handle_message, in_fire_season
from app.weather import AqiReport, WindReport
from app.messaging.assembler import Block, as_text, fits_segment


class TestOutsideCoverage:
    """An out-of-coverage location must never read as 'no fires reported'."""

    @patch("app.messages.get_wind", return_value=None)
    @patch("app.messages.get_aqi", return_value=None)
    @patch("app.messages.FindFires")
    def test_out_of_range_returns_outside_area_message(self, mock_ff_cls, mock_aqi, mock_wind):
        ff = mock_ff_cls.return_value
        ff.out_of_range.return_value = True

        from app.messages import handle_fire_request
        message = as_text(handle_fire_request((48.8566, 2.3522), {}))

        assert 'outside of supported' in message
        assert '(48.85660, 2.35220)' in message
        assert 'No fires reported' not in message


class TestDataUnavailable:
    """An unavailable source must never read as 'no fires reported'."""

    @patch("app.messages.get_wind", return_value=None)
    @patch("app.messages.get_aqi", return_value=None)
    @patch("app.messages.FindFires")
    def test_unavailable_source_with_no_fires(self, mock_ff_cls, mock_aqi, mock_wind):
        ff = mock_ff_cls.return_value
        ff.out_of_range.return_value = False
        ff.nearby.return_value = []
        ff.unavailable_sources = ['BC']

        from app.messages import handle_fire_request
        message = as_text(handle_fire_request((50.0, -122.0), {}))

        assert 'temporarily unavailable' in message
        assert 'No fires reported' not in message

    @patch("app.messages.get_wind", return_value=None)
    @patch("app.messages.get_aqi", return_value=None)
    @patch("app.messages.FindFires")
    def test_all_sources_available_with_no_fires(self, mock_ff_cls, mock_aqi, mock_wind):
        ff = mock_ff_cls.return_value
        ff.out_of_range.return_value = False
        ff.nearby.return_value = []
        ff.unavailable_sources = []
        ff.fallback_fetched = None
        ff.filters = {'distance': 50}

        from app.messages import handle_fire_request
        message = as_text(handle_fire_request((50.0, -122.0), {}))

        assert 'No fires reported' in message


class TestConditionsThresholds:
    """Conditions lines appear only past their floors."""

    COORDS_WITH_FIRES = "fires all (49.06, -120.79)"
    COORDS_QUIET = "fires (54.5, -125.5)"

    @pytest.fixture(autouse=True)
    def thresholds(self, monkeypatch):
        """Pin the floors so the tests hold whatever the operator tunes."""
        from app.config import get_config
        t = get_config().thresholds
        monkeypatch.setattr(t, 'wind_floor', 20)
        monkeypatch.setattr(t, 'wind_trend_delta', 10)
        monkeypatch.setattr(t, 'aqi_floor', 75)
        monkeypatch.setattr(t, 'aqi_forecast_hours', 4)
        monkeypatch.setattr(t, 'aqi_trend_delta', 50)

    def _reply(self, message, aqi=None, wind=None):
        with patch('app.messages.get_aqi', return_value=aqi), \
             patch('app.messages.get_wind', return_value=wind):
            return handle_message(message)

    def test_aqi_at_the_floor_shows(self):
        reply = self._reply(self.COORDS_WITH_FIRES, aqi=AqiReport(75, 75))
        assert reply.startswith("AQI: 75\n")

    def test_aqi_below_the_floor_is_silent(self):
        reply = self._reply(self.COORDS_WITH_FIRES, aqi=AqiReport(74, 74))
        assert 'AQI' not in reply

    def test_rising_aqi_shows_future_tense_before_the_floor(self):
        reply = self._reply(self.COORDS_WITH_FIRES, aqi=AqiReport(30, 80))
        assert reply.startswith("AQI: 30 rising to 80\n")

    def test_rise_below_the_delta_stays_silent(self):
        reply = self._reply(self.COORDS_WITH_FIRES, aqi=AqiReport(30, 79))
        assert 'AQI' not in reply

    def test_high_and_rising_uses_the_rising_form(self):
        reply = self._reply(self.COORDS_WITH_FIRES, aqi=AqiReport(152, 210))
        assert reply.startswith("AQI: 152 rising to 210\n")

    def test_wind_at_the_floor_shows(self):
        wind = WindReport(speed=20, direction='SW', peak=20)
        reply = self._reply(self.COORDS_WITH_FIRES, wind=wind)
        assert 'Wind: 20km/h from SW' in reply

    def test_wind_below_the_floor_is_silent(self):
        wind = WindReport(speed=19, direction='SW', peak=19)
        reply = self._reply(self.COORDS_WITH_FIRES, wind=wind)
        assert 'Wind' not in reply

    def test_rising_wind_shows_future_tense_before_the_floor(self):
        """Calm now, windy later: the pattern that matters most."""
        wind = WindReport(speed=8, direction='SW', peak=30)
        reply = self._reply(self.COORDS_WITH_FIRES, wind=wind)
        assert 'Wind: 8km/h from SW rising to 30' in reply

    def test_rise_that_stays_under_the_floor_is_silent(self):
        wind = WindReport(speed=5, direction='SW', peak=17)
        reply = self._reply(self.COORDS_WITH_FIRES, wind=wind)
        assert 'Wind' not in reply

    def test_no_fires_means_no_wind_report(self):
        """Wind above every floor still stays out of a fireless reply."""
        wind = WindReport(speed=40, direction='SW', peak=80)
        reply = self._reply(self.COORDS_QUIET, wind=wind)
        assert 'No fires reported' in reply
        assert 'Wind' not in reply

    def test_rising_aqi_that_stays_under_the_floor_is_silent(self):
        """The rise rule is floor-anchored: a jump within clean air says
        nothing."""
        reply = self._reply(self.COORDS_WITH_FIRES, aqi=AqiReport(20, 70))
        assert 'AQI' not in reply

    def test_aqi_shows_without_fires(self):
        """Smoke travels: AQI is independent of fire content."""
        reply = self._reply(self.COORDS_QUIET, aqi=AqiReport(152, 152))
        assert reply.startswith("AQI: 152\n")
        assert 'No fires reported' in reply


class TestMessageSegments:
    """Replies split into single-SMS messages on paragraph boundaries."""

    @patch("app.messages.get_wind", return_value=None)
    @patch("app.messages.get_aqi", return_value=AqiReport(152, 152))
    def test_segments_fit_and_carry_whole_paragraphs(self, mock_aqi, mock_wind):
        from app.messages import handle_message_segments
        from app.messaging.assembler import fits_segment

        reply = handle_message("fires all (49.064646, -120.7919022)")
        segments = handle_message_segments("fires all (49.064646, -120.7919022)")

        assert len(segments) > 1
        paragraphs = reply.split("\n\n")
        for segment in segments:
            assert fits_segment(segment)
            for paragraph in segment.split("\n\n"):
                assert paragraph in paragraphs

    @patch("app.messages.get_wind", return_value=None)
    @patch("app.messages.get_aqi", return_value=AqiReport(152, 152))
    def test_joined_segments_reconstruct_the_reply(self, mock_aqi, mock_wind):
        from app.messages import handle_message_segments

        message = "fires all (49.064646, -120.7919022)"
        assert "\n\n".join(handle_message_segments(message)) == handle_message(message)

    def test_short_reply_is_a_single_segment(self):
        from app.messages import handle_message_segments
        assert handle_message_segments("help") == [Messages().help()]


class TestConditionsHeader:
    """The AQI/wind conditions header on fire responses."""

    @pytest.fixture(autouse=True)
    def thresholds(self, monkeypatch):
        """Pin the floors so the tests hold whatever the operator tunes."""
        from app.config import get_config
        t = get_config().thresholds
        monkeypatch.setattr(t, 'wind_floor', 20)
        monkeypatch.setattr(t, 'wind_trend_delta', 10)
        monkeypatch.setattr(t, 'aqi_floor', 75)
        monkeypatch.setattr(t, 'aqi_forecast_hours', 4)
        monkeypatch.setattr(t, 'aqi_trend_delta', 50)

    @patch("app.messages.get_wind")
    @patch("app.messages.get_aqi", return_value=AqiReport(152, 152))
    def test_fire_response_leads_with_conditions_header(self, mock_aqi, mock_wind):
        mock_wind.return_value = WindReport(speed=30, direction="NW", peak=30)
        message = handle_message("fires all (49.06, -120.79)")
        assert message.startswith("AQI: 152\nWind: 30km/h from NW\n\n")

    @patch("app.messages.get_wind")
    @patch("app.messages.get_aqi", return_value=None)
    def test_wind_line_stands_alone_without_aqi(self, mock_aqi, mock_wind):
        mock_wind.return_value = WindReport(speed=30, direction="NW", peak=30)
        message = handle_message("fires all (49.06, -120.79)")
        assert message.startswith("Wind: 30km/h from NW\n\n")

    @patch("app.messages.get_wind")
    @patch("app.messages.get_aqi", return_value=AqiReport(152, 152))
    @patch("app.messages.FindFires")
    def test_header_dropped_when_it_cannot_share_the_first_sms(
            self, mock_ff_cls, mock_aqi, mock_wind):
        """The conditions header is a bonus: when it will not fit alongside
        the first fire, the reply proceeds without it rather than spending
        an SMS on conditions alone."""
        from app.messages import handle_message_segments
        mock_wind.return_value = WindReport(speed=25, direction="SW", peak=60)
        ff = mock_ff_cls.return_value
        ff.out_of_range.return_value = False
        ff.unavailable_sources = []
        ff.fallback_fetched = None
        ff.nearby.return_value = [{
            'Fire': 'K50911', 'Name': 'Very Long Fire Name For The Test',
            'Location': 'Distant Valley Behind The Long Ridge, Northern Sector',
            'Distance': 25000, 'Direction': 'E', 'Size': 6641,
            'Status': 'Out of Control',
        }]

        with patch('app.messages.parse_message', return_value={
                'coords': (50.0, -122.0), 'fire_filters': {}}):
            segments = handle_message_segments('fires (50.0, -122.0)')

        assert len(segments) == 1
        assert segments[0].startswith('Fire:')
        assert 'AQI' not in segments[0] and 'Wind' not in segments[0]

    @patch("app.messages.get_wind", return_value=None)
    @patch("app.messages.get_aqi", return_value=None)
    def test_no_conditions_header_when_both_unavailable(self, mock_aqi, mock_wind):
        message = handle_message("fires all (49.06, -120.79)")
        assert "Wind:" not in message
        assert not message.startswith("\n")


class TestFallbackMarker:
    """Responses built from stored data after a realtime failure carry a
    freshness marker; stored data as the configured mode does not."""

    # Fixture data was fetched 2026-07-01 12:00 UTC; the test coordinates
    # are in America/Vancouver (PDT, UTC-7).
    MARKER = "Data from Jul 1 05:00"
    COORDS = (49.06, -120.79)

    @pytest.fixture
    def bc_realtime_failing(self, monkeypatch):
        """Enable BC realtime and make its fetch fail, forcing DB fallback."""
        from app.config import get_config
        bc = next(d for d in get_config().data if d.location == 'BC')
        monkeypatch.setattr(bc.realtime, 'enabled', True)
        with patch('app.fires.find.fetch_fires', return_value=None), \
             patch('app.messages.get_aqi', return_value=None), \
             patch('app.messages.get_wind', return_value=None):
            yield

    def test_marker_appended_after_realtime_failure(self, bc_realtime_failing):
        from app.messages import handle_fire_request
        message = as_text(handle_fire_request(self.COORDS, {'status': 'all'}))

        assert message.endswith(self.MARKER)

    def test_marker_on_no_fires_response(self, bc_realtime_failing):
        from app.messages import handle_fire_request
        # (49.5, -120.9) is 40+ km from the nearest fixture fire.
        message = as_text(handle_fire_request((49.5, -120.9), {'status': 'all', 'distance': 1}))

        assert 'No fires reported' in message
        assert message.endswith(self.MARKER)

    def test_no_marker_when_realtime_disabled_by_config(self):
        from app.messages import handle_fire_request
        message = as_text(handle_fire_request(self.COORDS, {'status': 'all'}))

        assert 'Data from' not in message

    def test_data_age_format(self):
        from datetime import datetime
        assert Messages.data_age(datetime(2026, 7, 10, 14, 30)) == "Data from Jul 10 14:30"


class TestFireLookupResponse:
    """A "fireid <id>" request returns that one fire (from the fixture
    database, realtime disabled) or the not-found reply -- never a radius
    search."""

    def test_distance_and_direction_only_with_coords(self):
        with_coords = handle_message('fireid C10784 (50.5, -121.0)')
        without_coords = handle_message('fireid C10784')

        assert 'C10784' in with_coords and 'km ' in with_coords
        assert 'C10784' in without_coords and 'km ' not in without_coords

    def test_not_found_reply_exact_wording(self):
        message = handle_message('fireid NOPE999')

        assert message == ('No fire matching "NOPE999" was found. Check the fire '
                           'number, or send "fires" with your location for nearby fires.')

    def test_miss_with_coords_never_falls_back_to_radius_search(self):
        """An explicit lookup gets a direct answer; coordinates in the
        message do not turn a miss into a nearby-fires report."""
        message = handle_message('fireid NOPE (50.5, -121.0)')

        assert 'No fire matching "NOPE"' in message
        assert 'No fires reported' not in message

    def test_lookup_outranks_avalanche_keyword(self):
        """A fireid request is answered even when the message also contains
        the avalanche keyword."""
        message = handle_message('avalanche fireid NOPE999')

        assert 'No fire matching' in message

    def test_reply_carries_perimeter_and_as_of_lines(self):
        message = handle_message('fireid C10784')

        assert 'Perim: ' in message
        assert 'As of ' in message

    def test_ontario_fire_resolves_with_perimeter(self):
        """ON joins perimeters by fire number, so its fixture fire's mapped
        polygon is served with perimeter bounds and the as-of age."""
        message = handle_message('fireid NIP991')

        assert 'Fire: NIP991' in message
        assert 'Location: Nipigon' in message
        assert 'Status: Not Under Control' in message
        assert 'Perim: ' in message
        assert 'As of ' in message

    def test_ontario_lookup_is_case_insensitive(self):
        assert 'Fire: NIP991' in handle_message('fireid nip991')

    def test_as_of_relative_to_stored_fetch(self, monkeypatch):
        """A stale hit whose live refresh fails is served from storage with
        an As-of age measured from the stored fetch time."""
        from app.config import get_config
        from tests.conftest import FIXTURE_FETCHED_AT
        bc = next(d for d in get_config().data if d.location == 'BC')
        monkeypatch.setattr(bc.realtime, 'enabled', True)
        monkeypatch.setattr(bc.realtime, 'enrichment', None)
        with patch('app.fires.lookup.fetch_fire', side_effect=requests.ConnectionError('x')), \
             patch('app.messages.get_aqi', return_value=None), \
             patch('app.messages.get_wind', return_value=None):
            message = handle_message('fireid C10784 (49.06, -120.79)')

        days = round((datetime.now(timezone.utc) - FIXTURE_FETCHED_AT).total_seconds() / 86400)
        assert message.endswith(f'As of {days}d ago')


class TestServiceKeywords:
    """HELP/INFO and USAGE/EXAMPLES answer only as the whole message."""

    @pytest.mark.parametrize('message', ['help', 'HELP', ' Info ', '\nhelp\n'])
    def test_help_keyword_returns_help(self, message):
        assert handle_message(message) == Messages().help()

    def test_help_copy_matches_campaign_registration(self):
        """The declared help copy and the app's must never drift apart.
        Carriers require an alternate contact method in the HELP reply."""
        assert Messages().help() == (
            'TrekSafer: Wildfire & avalanche info. Text GPS coordinates '
            '(e.g. fires (49.2, -123.1)) to get a report. '
            'Contact info@treksafer.com. Reply STOP to opt out.')

    def test_opt_in_notice_matches_campaign_registration(self):
        """The declared opt-in copy and the app's must never drift apart.
        Carriers require company name, message frequency, Msg&Data rates,
        and HELP/STOP language in the opt-in confirmation."""
        assert Messages().opt_in_notice() == (
            'Welcome to TrekSafer wildfire & avalanche reports. '
            'Message frequency varies. Msg&Data rates may apply. '
            'Reply HELP for help or STOP to opt out.')

    def test_help_fits_one_sms_segment(self):
        assert fits_segment(Messages().help())

    @pytest.mark.parametrize('message', ['usage', 'EXAMPLES', ' Usage '])
    def test_usage_keyword_returns_guide(self, message):
        assert handle_message(message) == Messages().usage()

    @pytest.mark.parametrize('message', [
        'usage (49.2, -123.1)',
        'Usage inreachlink.com/FAKE123',
        ' usage\n(49.2, -123.1)',
    ])
    def test_usage_at_start_wins_over_appended_location(self, message):
        """Satellite messengers append their location to every message, so
        usage matches at the start of the message, unlike the other keywords."""
        assert handle_message(message) == Messages().usage()

    def test_usage_elsewhere_in_a_request_is_not_a_keyword(self):
        with patch('app.messages.get_aqi', return_value=None), \
             patch('app.messages.get_wind', return_value=None):
            response = handle_message('fires usage (50.5, -121.0)')

        assert response != Messages().usage()

    def test_keyword_replies_fit_one_sms_segment(self):
        for text in (Messages().help(), Messages().usage(),
                     Messages().opt_out_confirmed(), Messages().opt_in_confirmed(),
                     Messages().opt_in_notice()):
            assert fits_segment(text)

    def test_keyword_inside_request_is_not_hijacked(self):
        with patch('app.messages.get_aqi', return_value=None), \
             patch('app.messages.get_wind', return_value=None):
            response = handle_message('help fires (50.5, -121.0)')

        assert response != Messages().help()
        assert 'fires' in response.lower() or 'Fire' in response

    @pytest.mark.parametrize('message', ['helpful', 'information', 'usages'])
    def test_longer_words_are_not_keywords(self, message):
        response = handle_message(message)

        assert response not in (Messages().help(), Messages().usage())


class TestHealthMessage:
    """The message "health" (any case, surrounding whitespace, nothing else)
    returns a health summary on any transport."""

    @pytest.mark.parametrize('message', ['health', 'HEALTH', ' Health ', '\nhealth\n'])
    def test_health_request_returns_summary(self, message):
        response = handle_message(message)

        assert response.startswith('TrekSafer OK')
        for source in ('BC', 'AB', 'ON', 'CA', 'US'):
            assert source in response

    @pytest.mark.parametrize('message', [
        'health check', 'healthy', 'is health ok', 'health (49.2, -123.1)'])
    def test_other_text_is_not_a_health_request(self, message):
        response = handle_message(message)

        assert 'TrekSafer OK' not in response

    def test_summary_fits_one_sms(self):
        assert fits_segment(handle_message('health'))

    def test_unreadable_database_reports_error(self, tmp_path, monkeypatch):
        from app.config import get_config
        monkeypatch.setattr(get_config(), 'database', str(tmp_path))

        response = handle_message('health')

        assert response.startswith('TrekSafer health ERROR')


class TestAutoDetectRouting:
    """Bare coordinates auto-detect between avalanche and fire."""

    @patch("app.messages.in_fire_season", return_value=False)
    @patch("app.messages.handle_fire_request", return_value=[Block(["FIRE"])])
    @patch("app.messages.handle_avalanche_request", return_value=[Block(["AVY"])])
    @patch("app.messages.AvalancheReport")
    def test_out_of_season_routes_to_fire(self, mock_report_cls, mock_avy, mock_fire, mock_season):
        report = mock_report_cls.return_value
        report.has_data.return_value = True
        report.out_of_season.return_value = True

        assert handle_message("(50.12,-122.90)") == "FIRE"
        mock_avy.assert_not_called()

    @patch("app.messages.in_fire_season", return_value=False)
    @patch("app.messages.handle_fire_request", return_value=[Block(["FIRE"])])
    @patch("app.messages.handle_avalanche_request", return_value=[Block(["AVY"])])
    @patch("app.messages.AvalancheReport")
    def test_in_season_routes_to_avalanche(self, mock_report_cls, mock_avy, mock_fire, mock_season):
        report = mock_report_cls.return_value
        report.has_data.return_value = True
        report.out_of_season.return_value = False

        assert handle_message("(50.12,-122.90)") == "AVY"
        mock_fire.assert_not_called()

    @patch("app.messages.in_fire_season", return_value=True)
    @patch("app.messages.handle_fire_request", return_value=[Block(["FIRE"])])
    @patch("app.messages.handle_avalanche_request", return_value=[Block(["AVY"])])
    @patch("app.messages.AvalancheReport")
    def test_fire_season_routes_to_fire_without_avalanche_lookup(
        self, mock_report_cls, mock_avy, mock_fire, mock_season
    ):
        assert handle_message("(50.12,-122.90)") == "FIRE"
        mock_report_cls.assert_not_called()
        mock_avy.assert_not_called()

    @patch("app.messages.in_fire_season", return_value=True)
    @patch("app.messages.handle_fire_request", return_value=[Block(["FIRE"])])
    @patch("app.messages.handle_avalanche_request", return_value=[Block(["AVY"])])
    @patch("app.messages.AvalancheReport")
    def test_explicit_avalanche_request_bypasses_fire_season(
        self, mock_report_cls, mock_avy, mock_fire, mock_season
    ):
        assert handle_message("avalanche (50.12,-122.90)") == "AVY"
        mock_fire.assert_not_called()


class TestInFireSeason:
    """in_fire_season() checks dates against the configured MM-DD window."""

    @pytest.mark.parametrize("today,expected", [
        (date(2026, 5, 14), False),
        (date(2026, 5, 15), True),
        (date(2026, 7, 5), True),
        (date(2026, 8, 15), True),
        (date(2026, 8, 16), False),
        (date(2026, 1, 1), False),
        (date(2026, 12, 31), False),
    ])
    def test_default_window_boundaries(self, today, expected):
        assert in_fire_season(today) is expected

    def test_window_wrapping_year_boundary(self):
        settings = type("S", (), {"fire_season_start": "11-01", "fire_season_end": "03-31"})()
        with patch("app.messages.get_config", return_value=settings):
            assert in_fire_season(date(2026, 12, 25)) is True
            assert in_fire_season(date(2026, 2, 10)) is True
            assert in_fire_season(date(2026, 7, 5)) is False


class TestResponseType:
    """Every reply's blocks carry its outcome tag."""

    @pytest.mark.parametrize('message,expected', [
        ('help', 'help'),
        ('usage', 'usage'),
        ('fires please', 'no_gps'),
        ('fires (64.9, -18.5)', 'outside_of_area'),
        ('fires (54.5, -125.5)', 'no_fires'),
        ('fires all (49.06, -120.79)', 'fires'),
        ('fireid NOPE999', 'fire_not_found'),
        ('fireid C10784', 'fires'),
        ('health', 'health'),
    ])
    def test_classification(self, message, expected):
        from app.messages import _reply_kind, handle_message_blocks
        with patch('app.messages.get_aqi', return_value=AqiReport(42, 42)), \
             patch('app.messages.get_wind', return_value=None):
            assert _reply_kind(handle_message_blocks(message)) == expected


class TestRequestRecording:
    """Every request through the transport boundary lands in the log."""

    @pytest.fixture
    def request_db(self, tmp_path, monkeypatch):
        from app.config import get_config
        path = str(tmp_path / 'requests.db')
        monkeypatch.setattr(get_config(), 'request_database', path)
        return path

    def _rows(self, path):
        from datetime import datetime, timedelta, timezone
        from app import request_log
        return request_log.requests_since(
            path, datetime.now(timezone.utc) - timedelta(minutes=1))

    def test_request_recorded_with_sender_coords_and_type(self, request_db):
        from app.messages import safe_handle_message
        with patch('app.messages.get_aqi', return_value=None), \
             patch('app.messages.get_wind', return_value=None):
            safe_handle_message('fires all (49.06, -120.79)', '+15550001111')

        rows = self._rows(request_db)
        assert len(rows) == 1
        assert rows[0]['sender'] == '+15550001111'
        assert rows[0]['response_type'] == 'fires'
        assert rows[0]['lat'] == 49.06 and rows[0]['lon'] == -120.79

    @patch("app.messages.handle_message_blocks", side_effect=KeyError("Fire"))
    def test_crash_recorded_as_system_error(self, mock_blocks, request_db):
        from app.messages import safe_handle_message
        reply = safe_handle_message('fires (50.1, -122.1)', '+15550001111')

        assert 'Something went wrong' in reply[0]
        rows = self._rows(request_db)
        assert rows[0]['response_type'] == 'system_error'
        assert rows[0]['lat'] == 50.1

    def test_record_false_skips_the_log(self, request_db):
        """A transport with log_requests off leaves no row."""
        from app.messages import safe_handle_message
        safe_handle_message('help', 'cli', record=False)

        assert self._rows(request_db) == []

    def test_logging_failure_never_blocks_the_reply(self, request_db, caplog):
        import logging as _logging
        import sqlite3
        from app.messages import safe_handle_message
        with patch('app.messages.request_log.record',
                   side_effect=sqlite3.Error('locked')), \
             caplog.at_level(_logging.ERROR):
            reply = safe_handle_message('help', '+15550001111')

        assert reply == [Messages().help()]
        assert 'Request log unavailable' in caplog.text


class TestSafeHandleMessage:
    """The transport boundary: a crash anywhere must still produce a reply."""

    @patch("app.messages.handle_message_blocks", return_value=[Block(["normal reply"])])
    def test_passes_through_normally(self, mock_handle):
        from app.messages import safe_handle_message
        assert safe_handle_message("(50.1, -122.1)") == ["normal reply"]

    @patch("app.messages.handle_message_blocks", side_effect=KeyError("Fire"))
    def test_crash_produces_error_reply_and_loud_log(self, mock_handle, caplog):
        from app.messages import safe_handle_message
        import logging as _logging

        with caplog.at_level(_logging.ERROR):
            reply = safe_handle_message("(50.1, -122.1) crash bait")

        assert len(reply) == 1 and 'Something went wrong' in reply[0]
        assert 'logged and reported' in reply[0]
        record = next(r for r in caplog.records if 'handle_message crashed' in r.message)
        assert record.levelname == 'ERROR'
        assert record.exc_info is not None          # full traceback captured
        assert 'crash bait' in record.message        # the repro case is in the log

    def test_error_reply_fits_in_one_sms(self):
        assert len(Messages().system_error()) <= 160
