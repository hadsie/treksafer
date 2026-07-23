"""Tests for fire report rendering (app/messaging/fire.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.messaging.fire import FireMessages
from app.weather import WindReport


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


class TestFireBlockRendering:
    """Test suite for FireMessages.fire() method."""

    def test_full_format_includes_all_fields(self):
        """Full format should include all fire details."""
        fire = mock_fire()
        message = FireMessages().fire(fire, size="full")

        assert "Fire: Little Creek (K72481)" in message
        assert "Location: 5 km NW of Town" in message
        assert "12km NW" in message  # 12345m = 12.345km → rounds to 12km (≥10km rule)
        assert "Size: 123 ha" in message
        assert "Status: Out of Control" in message
        assert message.count("\n") == 4  # 5 lines = 4 newlines

    def test_medium_format_excludes_location_and_status(self):
        """Medium format should omit location and status fields."""
        fire = mock_fire()
        message = FireMessages().fire(fire, size="medium")

        assert "Fire: Little Creek K72481" in message
        assert "12km NW" in message  # 12345m = 12.345km → rounds to 12km
        assert "Size: 123 ha" in message
        assert "Location" not in message
        assert "Status" not in message
        assert message.count("\n") == 2  # 3 lines = 2 newlines

    def test_short_format_minimal(self):
        """Short format should contain only fire code, distance, and size."""
        fire = mock_fire()
        message = FireMessages().fire(fire, size="short")

        assert "K72481" in message
        assert "12km NW" in message  # 12345m = 12.345km → rounds to 12km
        assert "123ha" in message
        assert "Fire:" not in message
        assert "Location" not in message
        assert "Status" not in message

    def test_fire_name_same_as_code_omits_duplicate(self):
        """When fire name equals code, don't show duplicate in full format."""
        fire = mock_fire(Name="K72481")
        message = FireMessages().fire(fire, size="full")

        # Should show "Fire: K72481", not "Fire: K72481 (K72481)"
        assert "Fire: K72481\n" in message
        assert "(K72481)" not in message

    def test_auto_shortens_when_exceeds_sms_limit(self):
        """Messages over 159 chars should auto-shorten."""
        long_name = "VeryLongFireNameThatWillPushTheMessageOverTheLimit"
        fire = mock_fire(Name=long_name * 4, Location="Very long location name" * 10)

        message = FireMessages().fire(fire, size="full")

        # Should be shortened to stay under SMS limit
        char_count = len(message.encode("utf_16_le")) // 2
        assert char_count <= 159, f"Message too long: {char_count} chars"

    def test_long_location_gets_truncated(self):
        """Very long location strings should be handled gracefully."""
        long_location = "Lorem ipsum dolor sit amet, consectetuer adipiscing elit. " * 3
        fire = mock_fire(Location=long_location)

        message = FireMessages().fire(fire, size="full")

        # Should auto-shorten to medium or short
        assert len(message) < 200, "Long location should trigger auto-shortening"

    def test_distance_formatting_below_10km(self):
        """Distances under 10km should show one decimal place."""
        fire = mock_fire(Distance=9444)  # 9.444 km
        message = FireMessages().fire(fire, size="short")
        assert "9.4km" in message

    def test_distance_formatting_above_10km(self):
        """Distances 10km and above should be whole numbers."""
        fire = mock_fire(Distance=43210)  # 43.21 km
        message = FireMessages().fire(fire, size="short")
        assert "43km" in message

    def test_distance_strips_trailing_zero(self):
        """Whole number distances should not show .0 decimal."""
        fire = mock_fire(Distance=1000)  # Exactly 1.0 km
        message = FireMessages().fire(fire, size="short")
        assert "1km" in message
        assert "1.0km" not in message

    def test_size_rounded_to_integer(self):
        """Fire size should be rounded to nearest hectare."""
        fire = mock_fire(Size=72999.2)
        message = FireMessages().fire(fire, size="full")
        assert "Size: 72999 ha" in message  # 72999.2 rounds to 72999

    def test_direction_included_with_distance(self):
        """Distance and direction should be combined."""
        fire = mock_fire(Distance=25567, Direction="ENE")
        message = FireMessages().fire(fire, size="short")
        assert "25.6km ENE" in message or "26km ENE" in message

    def test_no_fires_basic_message(self):
        """No fires message without filter returns simple message."""
        msg = FireMessages().no_fires(50, (50.12345, -122.54321))
        assert msg == 'No fires reported within 50km of your location (50.12345, -122.54321).'

        # 'all' filter also returns simple message
        msg_all = FireMessages().no_fires(50, (50.12345, -122.54321), 'all')
        assert msg_all == 'No fires reported within 50km of your location (50.12345, -122.54321).'

    def test_no_fires_with_active_filter(self):
        """No fires message with 'active' filter shows single status."""
        msg = FireMessages().no_fires(50, (50.12345, -122.54321), 'active')
        assert msg == 'No fires reported within 50km of your location (50.12345, -122.54321). (Showing: active)'

    def test_no_fires_with_controlled_filter(self):
        """No fires message with 'controlled' filter shows multiple statuses."""
        msg = FireMessages().no_fires(50, (50.12345, -122.54321), 'controlled')
        assert msg == 'No fires reported within 50km of your location (50.12345, -122.54321). (Showing: active, managed, controlled)'


class TestDistanceFormatting:
    """Test suite for FireMessages._format_distance() method."""

    def test_distance_under_10km_one_decimal(self):
        """< 10 km should round to 1 decimal place."""
        assert FireMessages()._format_distance(9444) == 9.4

    def test_distance_rounds_up_to_10(self):
        """9.95 km should round to 10, not 9.9."""
        assert FireMessages()._format_distance(9950) == 10

    def test_distance_exactly_1km(self):
        """1000m = 1km, not 1.0km."""
        assert FireMessages()._format_distance(1000) == 1

    def test_distance_over_10km_integer(self):
        """≥ 10 km should be whole numbers."""
        assert FireMessages()._format_distance(43210) == 43

    def test_distance_exactly_10km(self):
        """10000m = 10km (boundary case)."""
        assert FireMessages()._format_distance(10000) == 10


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
        assert FireMessages._format_size(hectares) == expected

    def test_zero_size_renders_empty_and_omits_line(self):
        """A 0 ha size means no estimate yet; the Size line is dropped."""
        assert FireMessages._format_size(0) == ""
        message = FireMessages().fire(mock_fire(Size=0.0))
        assert 'Size' not in message

    def test_tiny_fire_message_shows_size(self):
        message = FireMessages().fire(mock_fire(Size=0.009))
        assert 'Size: <0.1 ha' in message


class TestFireMessageWithoutSize:
    """New fires may have no size estimate; the Size line is omitted."""

    def test_full_format_omits_size_line(self):
        fire = mock_fire()
        del fire['Size']
        message = FireMessages().fire(fire)
        assert 'Size' not in message
        assert 'K72481' in message

    def test_short_format_omits_size(self):
        fire = mock_fire()
        del fire['Size']
        message = FireMessages().fire(fire, size='short')
        assert 'ha' not in message


class TestSizeChangeRendering:
    """growth.enrich annotations render as suffixes on the Size line."""

    def test_growth_appended_to_size_line(self):
        fire = mock_fire(Size=14333, SizeChange={'delta': 300, 'hours': 26.2})
        message = FireMessages().fire(fire)
        assert 'Size: 14333 ha (+300 since 26h ago)' in message

    def test_shrinkage_renders_signed(self):
        fire = mock_fire(SizeChange={'delta': -150, 'hours': 30.0})
        message = FireMessages().fire(fire)
        assert '(-150 since 30h ago)' in message

    def test_long_spans_render_in_days(self):
        fire = mock_fire(SizeChange={'delta': 2000, 'hours': 100.0})
        message = FireMessages().fire(fire)
        assert '(+2000 since 4d ago)' in message

    def test_short_format_drops_the_delta(self):
        fire = mock_fire(SizeChange={'delta': 300, 'hours': 26.0})
        message = FireMessages().fire(fire, size='short')
        assert 'since' not in message
        assert 'ha' in message

    def test_no_annotation_renders_plain_size(self):
        message = FireMessages().fire(mock_fire(Size=14333))
        assert 'Size: 14333 ha' in message
        assert 'since' not in message


class TestNewLabelRendering:
    def test_new_label_after_fire_name(self):
        fire = mock_fire(New=True)
        message = FireMessages().fire(fire)
        assert '(K72481) (NEW)' in message

    def test_new_label_survives_short_format(self):
        fire = mock_fire(New=True)
        message = FireMessages().fire(fire, size='short')
        assert 'K72481 (NEW)' in message

    def test_no_label_without_flag(self):
        message = FireMessages().fire(mock_fire())
        assert '(NEW)' not in message


class TestLookupEnrichmentRendering:
    """The fireid reply's perimeter and edge lines."""

    PERIMETER = {'bounds': (50.97, 50.99, -89.44, -89.28)}

    def test_perimeter_line_renders_bounds(self):
        line = FireMessages().fire_perimeter(self.PERIMETER)
        assert line == 'Perim: 50.97-50.99N 89.44-89.28W'

    def test_edge_line_with_requester_distance(self):
        edge = {'advance_m': 8000.0, 'direction': 'E', 'was_m': 19000.0,
                'since': datetime.now(timezone.utc) - timedelta(hours=26)}
        line = FireMessages().fire_edge(edge)
        assert line == 'Edge: moved ~8km E in the last 26h, was 19km from you'

    def test_edge_line_without_requester_distance(self):
        edge = {'advance_m': 8000.0, 'direction': 'E', 'was_m': None,
                'since': datetime.now(timezone.utc) - timedelta(hours=80)}
        line = FireMessages().fire_edge(edge)
        assert line == 'Edge: moved ~8km E in the last 3d'


class TestWindLine:
    """Test the wind conditions line."""

    @pytest.fixture(autouse=True)
    def margin(self, monkeypatch):
        """Pin the peak-gust margin so the tests hold whatever the operator
        tunes thresholds.yaml to."""
        from app.config import get_config
        monkeypatch.setattr(get_config().thresholds, 'wind_peak_gust_margin', 15)

    def test_current_conditions_when_peak_is_similar(self):
        report = WindReport(speed=20, gusts=40, direction="SW", peak_gust=50)
        assert FireMessages.wind(report) == "Wind: 20km/h from SW, gusts 40"

    def test_peak_gust_appended_when_meaningfully_worse(self):
        report = WindReport(speed=20, gusts=40, direction="SW", peak_gust=65)
        assert FireMessages.wind(report) == "Wind: 20km/h from SW, gusts 40 rising to 65"

    def test_peak_threshold_boundary(self):
        """The peak shows at 15km/h over current gusts, not below."""
        at = WindReport(speed=10, gusts=20, direction="N", peak_gust=35)
        below = WindReport(speed=10, gusts=20, direction="N", peak_gust=34)
        assert "rising to 35" in FireMessages.wind(at)
        assert "rising" not in FireMessages.wind(below)


class TestDownsizeUsesRealSmsMath:
    """Downsizing and packing judge fit with the same segment math."""

    def test_non_gsm_character_triggers_downsizing(self):
        """One character outside the GSM alphabet caps an SMS at 70 units,
        so a fire block that passes the old 160-character check must still
        step down until it fits."""
        from app.messaging.assembler import fits_segment
        fire = mock_fire(Name="Tsile’os Park Fire")
        message = FireMessages().fire(fire)
        assert fits_segment(message)
        # The full format is under 160 characters, so only the segment
        # math can have forced the step down that dropped Location.
        assert "Location" not in message


class TestNonStringFieldValues:
    """The value cleanup strips strings only; other types pass through."""

    def test_none_field_renders_no_line(self):
        """A None value is falsy, so its line is skipped -- never printed
        as the text 'None'."""
        message = FireMessages().fire(mock_fire(Location=None))
        assert 'None' not in message
        assert 'Location' not in message

    def test_numeric_size_still_formats(self):
        message = FireMessages().fire(mock_fire(Size=66.0))
        assert 'Size: 66 ha' in message

    def test_whitespace_stripped_from_strings(self):
        message = FireMessages().fire(mock_fire(Name='  Padded Name  '))
        assert 'Padded Name (' in message
