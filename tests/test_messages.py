import pytest
from app.messages import Messages


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
