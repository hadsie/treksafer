"""Tests for SMS message parsing functionality.

Tests parse_message() which extracts:
- Coordinates in various formats
- Fire filter keywords (status, distance, data type)
- Avalanche forecast filters
"""
import pytest

from app.helpers import parse_message


class TestCoordinateParsing:
    """Test coordinate extraction from plain text."""

    def test_basic_inreach_format(self):
        """InReach devices append coordinates at end of message."""
        message = "Test basic message with, punctuation and coordinates. inreachlink.com/ABC1234  (52.5092, -115.6182)"
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_positive_negative_coords(self):
        """Coordinates with positive lat, negative lon."""
        message = "(52.5092, -115.6182)"
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_negative_positive_coords(self):
        """Coordinates with negative lat, positive lon."""
        message = "(-52.5092, 115.6182)"
        result = parse_message(message)
        assert result["coords"] == (-52.5092, 115.6182)

    def test_coords_arbitrary_placement(self):
        """Coordinates can appear anywhere in message."""
        message = "Test basic message   (52.5092, -115.6182) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_newline_before_coords(self):
        """Coordinates after newline."""
        message = "Test basic message  \n (52.5092, -115.6182) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_newline_within_coords(self):
        """Newline between lat and lon."""
        message = "Test basic message (52.5092,\n -115.6182) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_newline_and_spaces_in_coords(self):
        """Multiple whitespace types within coordinates."""
        message = "Here:\n( 52.5092 ,\n-115.6182 )"
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_coords_no_decimals(self):
        """Integer coordinates without decimal points."""
        message = "Test basic message (52, -115) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52, -115)

    def test_zero_coordinates(self):
        """Null Island (0, 0) is valid."""
        result = parse_message("0.0000, 0.0000")
        assert result["coords"] == (0.0, 0.0)
        result = parse_message("0, 0")
        assert result["coords"] == (0.0, 0.0)

    def test_first_valid_pair_wins(self):
        """When multiple pairs exist, first valid one is used."""
        message = "Valid (12, 99) valid (52.5092, -115.6182)."
        result = parse_message(message)
        assert result["coords"] == (12, 99)

    def test_skip_invalid_find_valid(self):
        """Skip invalid pairs, find first valid one."""
        message = "Invalid (1234, 99) valid (52.5092, -115.6182)."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_invalid_coords_only(self):
        """Message with only invalid coordinates returns None."""
        message = "Message with invalid coords (1234, 99)"
        assert parse_message(message) is None

    def test_no_coords_returns_none(self):
        """Message without coordinates returns None."""
        assert parse_message("Just a plain message") is None


class TestHemisphereCoordinates:
    """Test degree + hemisphere letter format (e.g., '50° N, 122° W')."""

    def test_degrees_north_west(self):
        """Standard N/W format with degrees symbol after number."""
        result = parse_message("coords: 50.58225° N, 122.09114° W")
        assert result["coords"] == (50.58225, -122.09114)

    def test_hemisphere_before_degrees(self):
        """Hemisphere letter before number."""
        result = parse_message("N 50.58225°, W 122.09114°")
        assert result["coords"] == (50.58225, -122.09114)

    def test_lowercase_south_east(self):
        """Lowercase hemisphere letters (s/e)."""
        result = parse_message("33.12345° s, 18.54321° e")
        assert result["coords"] == (-33.12345, 18.54321)

    def test_hemisphere_overrides_sign(self):
        """Hemisphere letter takes precedence over negative sign."""
        result = parse_message("-50° N, 122° W")
        assert result["coords"] == (50, -122)

    def test_positive_sign_with_south(self):
        """Hemisphere letter overrides positive sign."""
        result = parse_message("+50° S, 122° E")
        assert result["coords"] == (-50, 122)


class TestCoordinateValidation:
    """Test coordinate boundary validation."""

    def test_max_valid_latitude(self):
        """North pole (90) is valid."""
        result = parse_message("(90, 0)")
        assert result["coords"] == (90, 0)

    def test_min_valid_latitude(self):
        """South pole (-90) is valid."""
        result = parse_message("(-90, 0)")
        assert result["coords"] == (-90, 0)

    def test_max_valid_longitude(self):
        """International date line east (180) is valid."""
        result = parse_message("(0, 180)")
        assert result["coords"] == (0, 180)

    def test_min_valid_longitude(self):
        """International date line west (-180) is valid."""
        result = parse_message("(0, -180)")
        assert result["coords"] == (0, -180)

    def test_latitude_too_high(self):
        """Latitude > 90 is invalid."""
        assert parse_message("(91, 0)") is None

    def test_latitude_too_low(self):
        """Latitude < -90 is invalid."""
        assert parse_message("(-91, 0)") is None

    def test_longitude_too_high(self):
        """Longitude > 180 is invalid."""
        assert parse_message("(0, 181)") is None

    def test_longitude_too_low(self):
        """Longitude < -180 is invalid."""
        assert parse_message("(0, -181)") is None


class TestMapLinkParsing:
    """Test extraction of coordinates from map sharing links."""

    APPLE_CASES = [
        (
            "Check this out: "
            "https://maps.apple.com/place?coordinate=49.253491,-123.017063"
            "&name=Dropped%20Pin&span=0.004591,0.014026",
            (49.253491, -123.017063),
        ),
        (
            # Multiple query-string params shuffled - still works
            "https://maps.apple.com/place?name=Pin&span=0.01,0.01"
            "&coordinate=-12.345678,98.765432",
            (-12.345678, 98.765432),
        ),
    ]

    GOOGLE_CASES = [
        (
            # /maps/search/?api=1&query=lat,lon
            "https://www.google.com/maps/search/?api=1&query=49.253491,-123.017063",
            (49.253491, -123.017063),
        ),
        (
            # /maps?q=lat,lon
            "Totally random text https://www.google.com/maps?q=-12.345678,98.765432 yay",
            (-12.345678, 98.765432),
        ),
        (
            # /maps/@lat,lon,zoom
            "https://www.google.com/maps/@49.253491,-123.017063,17z",
            (49.253491, -123.017063),
        ),
    ]

    NEGATIVE_CASES = [
        # Google link with an address, not lat/lon
        "https://www.google.com/maps/search/?api=1&query=123+Creekside+Pl++Burnaby++BC",
        # Apple link with no coordinate param
        "https://maps.apple.com/place?name=Vancouver",
    ]

    @pytest.mark.parametrize("msg,expected", APPLE_CASES + GOOGLE_CASES)
    def test_map_link_success(self, msg, expected):
        """Map sharing links are parsed correctly."""
        result = parse_message(msg)
        assert result["coords"] == pytest.approx(expected)

    @pytest.mark.parametrize("msg", NEGATIVE_CASES)
    def test_map_link_failure(self, msg):
        """Map links without coordinates return None."""
        assert parse_message(msg) is None


class TestFilterExtraction:
    """Test extraction of filter keywords from messages."""

    def test_active_status_filter(self):
        """'active' keyword sets status filter."""
        result = parse_message("(49.25, -123.01) active")
        assert result["filters"]["status"] == "active"

    def test_all_status_filter(self):
        """'all' keyword sets status filter."""
        result = parse_message("(49.25, -123.01) all")
        assert result["filters"]["status"] == "all"

    def test_status_case_insensitive(self):
        """Status filters are case-insensitive."""
        result = parse_message("(49.25, -123.01) ACTIVE")
        assert result["filters"]["status"] == "active"

    def test_distance_filter_kilometers(self):
        """Distance with 'km' unit."""
        result = parse_message("(49.25, -123.01) 25km")
        assert result["filters"]["distance"] == 25

    def test_distance_filter_miles(self):
        """Distance with 'mi' unit converts to km."""
        result = parse_message("(49.25, -123.01) 10mi")
        assert result["filters"]["distance"] == pytest.approx(16.09344)

    def test_distance_filter_with_spaces(self):
        """Distance filter handles spacing variations."""
        result = parse_message("(49.25, -123.01)  50km  ")
        assert result["filters"]["distance"] == 50

    def test_data_type_fire(self):
        """'fire' keyword sets data type."""
        result = parse_message("(49.25, -123.01) fire")
        assert result["data_type"] == "fire"

    def test_data_type_fires_plural(self):
        """'fires' (plural) also matches."""
        result = parse_message("(49.25, -123.01) fires")
        assert result["data_type"] == "fire"

    def test_data_type_avalanche(self):
        """'avalanche' keyword sets data type."""
        result = parse_message("(49.25, -123.01) avalanche")
        assert result["data_type"] == "avalanche"

    def test_data_type_avalanches_plural(self):
        """'avalanches' (plural) also matches."""
        result = parse_message("(49.25, -123.01) avalanches")
        assert result["data_type"] == "avalanche"

    def test_data_type_default_auto(self):
        """Default data type is 'auto'."""
        result = parse_message("(49.25, -123.01)")
        assert result["data_type"] == "auto"

    def test_avalanche_forecast_current(self):
        """'current' sets avalanche forecast filter."""
        result = parse_message("(49.25, -123.01) current")
        assert result["avalanche_filters"]["forecast"] == "current"

    def test_avalanche_forecast_today(self):
        """'today' sets avalanche forecast filter."""
        result = parse_message("(49.25, -123.01) today")
        assert result["avalanche_filters"]["forecast"] == "today"

    def test_avalanche_forecast_tomorrow(self):
        """'tomorrow' sets avalanche forecast filter."""
        result = parse_message("(49.25, -123.01) tomorrow")
        assert result["avalanche_filters"]["forecast"] == "tomorrow"

    def test_avalanche_forecast_all(self):
        """'all' can set avalanche forecast filter."""
        result = parse_message("(49.25, -123.01) all")
        assert result["avalanche_filters"]["forecast"] == "all"

    def test_multiple_filters_combined(self):
        """Multiple filters in one message."""
        result = parse_message("(49.25, -123.01) active 25km fire")
        assert result["filters"]["status"] == "active"
        assert result["filters"]["distance"] == 25
        assert result["data_type"] == "fire"


class TestReturnValueStructure:
    """Test structure of returned dictionary."""

    def test_has_all_required_keys(self):
        """Result dict contains all expected keys."""
        result = parse_message("(49.25, -123.01)")
        assert "coords" in result
        assert "filters" in result
        assert "data_type" in result
        assert "avalanche_filters" in result

    def test_filters_is_dict(self):
        """Filters are returned as dict."""
        result = parse_message("(49.25, -123.01)")
        assert isinstance(result["filters"], dict)

    def test_avalanche_filters_is_dict(self):
        """Avalanche filters are returned as dict."""
        result = parse_message("(49.25, -123.01)")
        assert isinstance(result["avalanche_filters"], dict)


class TestIntegration:
    """Test complex real-world message scenarios."""

    def test_inreach_with_filters(self):
        """InReach message with status and distance filters."""
        message = "Emergency! Active fires near me. inreachlink.com/ABC (49.25, -123.01) 50km"
        result = parse_message(message)
        assert result["coords"] == (49.25, -123.01)
        assert result["filters"]["status"] == "active"
        assert result["filters"]["distance"] == 50

    def test_map_link_with_data_type(self):
        """Map link with data type keyword."""
        message = "Check avalanche conditions https://www.google.com/maps/@49.25,-123.01,15z"
        result = parse_message(message)
        assert result["coords"] == (49.25, -123.01)
        assert result["data_type"] == "avalanche"

    def test_hemisphere_coords_with_filters(self):
        """Hemisphere format with multiple filters."""
        message = "50.58° N, 122.09° W active fire 25km"
        result = parse_message(message)
        assert result["coords"] == (50.58, -122.09)
        assert result["filters"]["status"] == "active"
        assert result["filters"]["distance"] == 25
        assert result["data_type"] == "fire"

    def test_complex_natural_language(self):
        """Natural language message with embedded coordinates."""
        message = "Hi, I'm at (49.25, -123.01) and want to know about active fires within 30km"
        result = parse_message(message)
        assert result["coords"] == (49.25, -123.01)
        assert result["filters"]["status"] == "active"
        assert result["filters"]["distance"] == 30
        assert result["data_type"] == "fire"

    def test_avalanche_forecast_request(self):
        """Avalanche forecast query with time filter."""
        message = "Avalanche forecast for tomorrow at 49.25, -123.01"
        result = parse_message(message)
        assert result["coords"] == (49.25, -123.01)
        assert result["data_type"] == "avalanche"
        assert result["avalanche_filters"]["forecast"] == "tomorrow"
