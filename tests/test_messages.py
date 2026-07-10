from datetime import date
from unittest.mock import patch

import pytest

from app.messages import Messages, handle_message, in_fire_season


def mock_fire(**overrides):
    """Create a mock fire dictionary with sensible defaults."""
    base = {
        "Fire": "K72481",
        "Name": "Little Creek",
        "Location": "5 km NW of Town",
        "Distance": 12345,
        "Direction": "NW",
        "Size": 123.4,
        "Status": "Out of Control",
    }
    base.update(overrides)
    return base


class TestMessages:
    """Test suite for Messages.fire() method."""

    def test_full_format_includes_all_fields(self):
        """Full format should include all fire details."""
        fire = mock_fire()
        message = Messages().fire(fire, size="full")

        assert "Fire: Little Creek (K72481)" in message
        assert "Location: 5 km NW of Town" in message
        assert "12km NW" in message  # 12345m = 12.345km → rounds to 12km (≥10km rule)
        assert "Size: 123 ha" in message
        assert "Status: Out of Control" in message
        assert message.count("\n") == 4  # 5 lines = 4 newlines

    def test_medium_format_excludes_location_and_status(self):
        """Medium format should omit location and status fields."""
        fire = mock_fire()
        message = Messages().fire(fire, size="medium")

        assert "Fire: Little Creek K72481" in message
        assert "12km NW" in message  # 12345m = 12.345km → rounds to 12km
        assert "Size: 123 ha" in message
        assert "Location" not in message
        assert "Status" not in message
        assert message.count("\n") == 2  # 3 lines = 2 newlines

    def test_short_format_minimal(self):
        """Short format should contain only fire code, distance, and size."""
        fire = mock_fire()
        message = Messages().fire(fire, size="short")

        assert "K72481" in message
        assert "12km NW" in message  # 12345m = 12.345km → rounds to 12km
        assert "123ha" in message
        assert "Fire:" not in message
        assert "Location" not in message
        assert "Status" not in message

    def test_fire_name_same_as_code_omits_duplicate(self):
        """When fire name equals code, don't show duplicate in full format."""
        fire = mock_fire(Name="K72481")
        message = Messages().fire(fire, size="full")

        # Should show "Fire: K72481", not "Fire: K72481 (K72481)"
        assert "Fire: K72481\n" in message
        assert "(K72481)" not in message

    def test_auto_shortens_when_exceeds_sms_limit(self):
        """Messages over 159 chars should auto-shorten."""
        long_name = "VeryLongFireNameThatWillPushTheMessageOverTheLimit"
        fire = mock_fire(Name=long_name * 4, Location="Very long location name" * 10)

        message = Messages().fire(fire, size="full")

        # Should be shortened to stay under SMS limit
        char_count = len(message.encode("utf_16_le")) // 2
        assert char_count <= 159, f"Message too long: {char_count} chars"

    def test_long_location_gets_truncated(self):
        """Very long location strings should be handled gracefully."""
        long_location = "Lorem ipsum dolor sit amet, consectetuer adipiscing elit. " * 3
        fire = mock_fire(Location=long_location)

        message = Messages().fire(fire, size="full")

        # Should auto-shorten to medium or short
        assert len(message) < 200, "Long location should trigger auto-shortening"

    def test_distance_formatting_below_10km(self):
        """Distances under 10km should show one decimal place."""
        fire = mock_fire(Distance=9444)  # 9.444 km
        message = Messages().fire(fire, size="short")
        assert "9.4km" in message

    def test_distance_formatting_above_10km(self):
        """Distances 10km and above should be whole numbers."""
        fire = mock_fire(Distance=43210)  # 43.21 km
        message = Messages().fire(fire, size="short")
        assert "43km" in message

    def test_distance_strips_trailing_zero(self):
        """Whole number distances should not show .0 decimal."""
        fire = mock_fire(Distance=1000)  # Exactly 1.0 km
        message = Messages().fire(fire, size="short")
        assert "1km" in message
        assert "1.0km" not in message

    def test_size_rounded_to_integer(self):
        """Fire size should be rounded to nearest hectare."""
        fire = mock_fire(Size=72999.2)
        message = Messages().fire(fire, size="full")
        assert "Size: 72999 ha" in message  # 72999.2 rounds to 72999

    def test_direction_included_with_distance(self):
        """Distance and direction should be combined."""
        fire = mock_fire(Distance=25567, Direction="ENE")
        message = Messages().fire(fire, size="short")
        assert "25.6km ENE" in message or "26km ENE" in message

    def test_no_fires_basic_message(self):
        """No fires message without filter returns simple message."""
        msg = Messages().no_fires(50, (50.12345, -122.54321))
        assert msg == 'No fires reported within 50km of your location (50.12345, -122.54321).'

        # 'all' filter also returns simple message
        msg_all = Messages().no_fires(50, (50.12345, -122.54321), 'all')
        assert msg_all == 'No fires reported within 50km of your location (50.12345, -122.54321).'

    def test_no_fires_with_active_filter(self):
        """No fires message with 'active' filter shows single status."""
        msg = Messages().no_fires(50, (50.12345, -122.54321), 'active')
        assert msg == 'No fires reported within 50km of your location (50.12345, -122.54321). (Showing: active)'

    def test_no_fires_with_controlled_filter(self):
        """No fires message with 'controlled' filter shows multiple statuses."""
        msg = Messages().no_fires(50, (50.12345, -122.54321), 'controlled')
        assert msg == 'No fires reported within 50km of your location (50.12345, -122.54321). (Showing: active, managed, controlled)'


class TestDistanceFormatting:
    """Test suite for Messages._format_distance() method."""

    def test_distance_under_10km_one_decimal(self):
        """< 10 km should round to 1 decimal place."""
        assert Messages()._format_distance(9444) == 9.4

    def test_distance_rounds_up_to_10(self):
        """9.95 km should round to 10, not 9.9."""
        assert Messages()._format_distance(9950) == 10

    def test_distance_exactly_1km(self):
        """1000m = 1km, not 1.0km."""
        assert Messages()._format_distance(1000) == 1

    def test_distance_over_10km_integer(self):
        """≥ 10 km should be whole numbers."""
        assert Messages()._format_distance(43210) == 43

    def test_distance_exactly_10km(self):
        """10000m = 10km (boundary case)."""
        assert Messages()._format_distance(10000) == 10


class TestFormatSize:
    """_format_size() renders hectares for SMS."""

    @pytest.mark.parametrize("hectares,expected", [
        (0.009, "<0.1"),
        (0.05, "<0.1"),
        (0.1, "0.1"),
        (0.5, "0.5"),
        (2.69, "2.7"),
        (9.95, "10"),
        (10.4, "10"),
        (123.4, "123"),
        (808.1, "808"),
    ])
    def test_size_formatting(self, hectares, expected):
        assert Messages._format_size(hectares) == expected

    def test_zero_size_renders_empty_and_omits_line(self):
        """A 0 ha size means no estimate yet; the Size line is dropped."""
        assert Messages._format_size(0) == ""
        message = Messages().fire(mock_fire(Size=0.0))
        assert 'Size' not in message

    def test_tiny_fire_message_shows_size(self):
        message = Messages().fire(mock_fire(Size=0.009))
        assert 'Size: <0.1 ha' in message


class TestFireMessageWithoutSize:
    """New fires may have no size estimate; the Size line is omitted."""

    def test_full_format_omits_size_line(self):
        fire = mock_fire()
        del fire['Size']
        message = Messages().fire(fire)
        assert 'Size' not in message
        assert 'K72481' in message

    def test_short_format_omits_size(self):
        fire = mock_fire()
        del fire['Size']
        message = Messages().fire(fire, size='short')
        assert 'ha' not in message


class TestDataUnavailable:
    """An unavailable source must never read as 'no fires reported'."""

    @patch("app.messages.get_aqi", return_value=None)
    @patch("app.messages.FindFires")
    def test_unavailable_source_with_no_fires(self, mock_ff_cls, mock_aqi):
        ff = mock_ff_cls.return_value
        ff.out_of_range.return_value = False
        ff.nearby.return_value = []
        ff.unavailable_sources = ['BC']

        from app.messages import handle_fire_request
        message = handle_fire_request((50.0, -122.0), {})

        assert 'temporarily unavailable' in message
        assert 'No fires reported' not in message

    @patch("app.messages.get_aqi", return_value=None)
    @patch("app.messages.FindFires")
    def test_all_sources_available_with_no_fires(self, mock_ff_cls, mock_aqi):
        ff = mock_ff_cls.return_value
        ff.out_of_range.return_value = False
        ff.nearby.return_value = []
        ff.unavailable_sources = []
        ff.filters = {'distance': 50}

        from app.messages import handle_fire_request
        message = handle_fire_request((50.0, -122.0), {})

        assert 'No fires reported' in message


class TestAutoDetectRouting:
    """Bare coordinates auto-detect between avalanche and fire."""

    @patch("app.messages.in_fire_season", return_value=False)
    @patch("app.messages.handle_fire_request", return_value="FIRE")
    @patch("app.messages.handle_avalanche_request", return_value="AVY")
    @patch("app.messages.AvalancheReport")
    def test_out_of_season_routes_to_fire(self, mock_report_cls, mock_avy, mock_fire, mock_season):
        report = mock_report_cls.return_value
        report.has_data.return_value = True
        report.out_of_season.return_value = True

        assert handle_message("(50.12,-122.90)") == "FIRE"
        mock_avy.assert_not_called()

    @patch("app.messages.in_fire_season", return_value=False)
    @patch("app.messages.handle_fire_request", return_value="FIRE")
    @patch("app.messages.handle_avalanche_request", return_value="AVY")
    @patch("app.messages.AvalancheReport")
    def test_in_season_routes_to_avalanche(self, mock_report_cls, mock_avy, mock_fire, mock_season):
        report = mock_report_cls.return_value
        report.has_data.return_value = True
        report.out_of_season.return_value = False

        assert handle_message("(50.12,-122.90)") == "AVY"
        mock_fire.assert_not_called()

    @patch("app.messages.in_fire_season", return_value=True)
    @patch("app.messages.handle_fire_request", return_value="FIRE")
    @patch("app.messages.handle_avalanche_request", return_value="AVY")
    @patch("app.messages.AvalancheReport")
    def test_fire_season_routes_to_fire_without_avalanche_lookup(
        self, mock_report_cls, mock_avy, mock_fire, mock_season
    ):
        assert handle_message("(50.12,-122.90)") == "FIRE"
        mock_report_cls.assert_not_called()
        mock_avy.assert_not_called()

    @patch("app.messages.in_fire_season", return_value=True)
    @patch("app.messages.handle_fire_request", return_value="FIRE")
    @patch("app.messages.handle_avalanche_request", return_value="AVY")
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


class TestSafeHandleMessage:
    """The transport boundary: a crash anywhere must still produce a reply."""

    @patch("app.messages.handle_message", return_value="normal reply")
    def test_passes_through_normally(self, mock_handle):
        from app.messages import safe_handle_message
        assert safe_handle_message("(50.1, -122.1)") == "normal reply"

    @patch("app.messages.handle_message", side_effect=KeyError("Fire"))
    def test_crash_produces_error_reply_and_loud_log(self, mock_handle, caplog):
        from app.messages import safe_handle_message
        import logging as _logging

        with caplog.at_level(_logging.ERROR):
            reply = safe_handle_message("(50.1, -122.1) crash bait")

        assert 'Something went wrong' in reply
        assert 'Do NOT rely on this service' in reply
        record = next(r for r in caplog.records if 'handle_message crashed' in r.message)
        assert record.levelname == 'ERROR'
        assert record.exc_info is not None          # full traceback captured
        assert 'crash bait' in record.message        # the repro case is in the log

    def test_error_reply_fits_in_one_sms(self):
        assert len(Messages().system_error()) <= 160
