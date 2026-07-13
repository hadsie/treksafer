"""Tests for SMS message parsing functionality.

Tests parse_message() which extracts:
- Coordinates in various formats
- Fire filter keywords (status, distance, data type)
- Avalanche forecast filters
"""
import pytest
import responses

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

    def test_integer_coords_rejected(self):
        """Integer-only coordinates are rejected; real device coords always have decimals."""
        message = "Test basic message (52, -115) coordinates arbitrarily placed."
        assert parse_message(message) is None

    def test_zero_coordinates(self):
        """Null Island parses when given with decimals; the integer form is rejected."""
        result = parse_message("0.0000, 0.0000")
        assert result["coords"] == (0.0, 0.0)
        assert parse_message("0, 0") is None

    def test_decimal_coords_preferred_over_integer_pair(self):
        """An incidental integer pair is ignored in favour of real decimal coords."""
        message = "Valid (12, 99) valid (52.5092, -115.6182)."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_incidental_integer_pair_ignored(self):
        """An incidental integer pair before the coords is not read as coordinates."""
        result = parse_message("party of 2, 50.58, -122.09")
        assert result["coords"] == (50.58, -122.09)

    def test_out_of_range_pair_returns_none(self):
        """A pair whose first value cannot be a latitude returns None, not a corrupted value."""
        # 122.09 is not a valid latitude; must not return the fragment (9.0, 50.58).
        assert parse_message("122.09, 50.58") is None

    def test_leading_negative_sign_preserved(self):
        """A leading negative sign on the latitude is not dropped."""
        result = parse_message("-45.5, -120.5")
        assert result["coords"] == (-45.5, -120.5)

    def test_typed_pair_beats_appended_device_location(self):
        """The earliest coordinates win: a pair the user typed outranks the
        device location the inReach gateway appends at the end."""
        result = parse_message(
            "Fires (38.11111, -119.22222) inreachlink.com/ABC123  (-43.51234, 172.61234)")
        assert result["coords"] == (38.11111, -119.22222)

    def test_first_valid_pair_wins_even_over_end_bracketed_pair(self):
        """Earliest-match-wins is uniform: free text that parses as a valid
        coordinate pair beats a later bracketed pair. Accepted trade-off of
        the explicit-before-automatic rule; the decimal and bounds guards
        keep this rare."""
        result = parse_message("temp 12.5, 20.3 at (52.5092, -115.6182)")
        assert result["coords"] == (12.5, 20.3)

    def test_skip_invalid_find_valid(self):
        """Skip invalid pairs, find first valid one."""
        message = "Invalid (1234.0, 99.0) valid (52.5092, -115.6182)."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_invalid_coords_only(self):
        """Message with only invalid coordinates returns None."""
        message = "Message with invalid coords (1234.0, 99.0)"
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

    def test_typed_hemisphere_beats_appended_device_location(self):
        """A deliberately typed hemisphere pair outranks the trailing decimal
        pair the inReach gateway appends (the device's own location).

        Models a user abroad asking about North American fires.
        """
        message = ("Fires(N 33.11111° W 116.22222°) "
                   "inreachlink.com/ABC123XYZ  (-43.51234, 172.61234)")
        result = parse_message(message)
        assert result["coords"] == (33.11111, -116.22222)


class TestCoordinateValidation:
    """Test coordinate boundary validation."""

    def test_max_valid_latitude(self):
        """North pole (90) is valid."""
        result = parse_message("(90.0, 0.0)")
        assert result["coords"] == (90, 0)

    def test_min_valid_latitude(self):
        """South pole (-90) is valid."""
        result = parse_message("(-90.0, 0.0)")
        assert result["coords"] == (-90, 0)

    def test_max_valid_longitude(self):
        """International date line east (180) is valid."""
        result = parse_message("(0.0, 180.0)")
        assert result["coords"] == (0, 180)

    def test_min_valid_longitude(self):
        """International date line west (-180) is valid."""
        result = parse_message("(0.0, -180.0)")
        assert result["coords"] == (0, -180)

    def test_latitude_too_high(self):
        """Latitude > 90 is invalid."""
        assert parse_message("(91.0, 0.0)") is None

    def test_latitude_too_low(self):
        """Latitude < -90 is invalid."""
        assert parse_message("(-91.0, 0.0)") is None

    def test_longitude_too_high(self):
        """Longitude > 180 is invalid."""
        assert parse_message("(0.0, 181.0)") is None

    def test_longitude_too_low(self):
        """Longitude < -180 is invalid."""
        assert parse_message("(0.0, -181.0)") is None


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
        assert result["fire_filters"]["status"] == "active"

    def test_all_status_filter(self):
        """'all' keyword sets status filter."""
        result = parse_message("(49.25, -123.01) all")
        assert result["fire_filters"]["status"] == "all"

    def test_status_case_insensitive(self):
        """Status filters are case-insensitive."""
        result = parse_message("(49.25, -123.01) ACTIVE")
        assert result["fire_filters"]["status"] == "active"

    def test_distance_filter_kilometers(self):
        """Distance with 'km' unit."""
        result = parse_message("(49.25, -123.01) 25km")
        assert result["fire_filters"]["distance"] == 25

    def test_distance_filter_miles(self):
        """Distance with 'mi' unit converts to km."""
        result = parse_message("(49.25, -123.01) 10mi")
        assert result["fire_filters"]["distance"] == pytest.approx(16.09344)

    def test_distance_filter_with_spaces(self):
        """Distance filter handles spacing variations."""
        result = parse_message("(49.25, -123.01)  50km  ")
        assert result["fire_filters"]["distance"] == 50

    def test_distance_filter_space_between_number_and_unit(self):
        """Distance filter matches with a space between the number and unit."""
        result = parse_message("(49.25, -123.01) 25 km")
        assert result["fire_filters"]["distance"] == 25

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
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 25
        assert result["data_type"] == "fire"


class TestReturnValueStructure:
    """Test structure of returned dictionary."""

    def test_has_all_required_keys(self):
        """Result dict contains all expected keys."""
        result = parse_message("(49.25, -123.01)")
        assert "coords" in result
        assert "fire_filters" in result
        assert "data_type" in result
        assert "avalanche_filters" in result

    def test_filters_is_dict(self):
        """Filters are returned as dict."""
        result = parse_message("(49.25, -123.01)")
        assert isinstance(result["fire_filters"], dict)

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
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 50

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
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 25
        assert result["data_type"] == "fire"

    def test_complex_natural_language(self):
        """Natural language message with embedded coordinates."""
        message = "Hi, I'm at (49.25, -123.01) and want to know about active fires within 30km"
        result = parse_message(message)
        assert result["coords"] == (49.25, -123.01)
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 30
        assert result["data_type"] == "fire"

    def test_avalanche_forecast_request(self):
        """Avalanche forecast query with time filter."""
        message = "Avalanche forecast for tomorrow at 49.25, -123.01"
        result = parse_message(message)
        assert result["coords"] == (49.25, -123.01)
        assert result["data_type"] == "avalanche"
        assert result["avalanche_filters"]["forecast"] == "tomorrow"


class TestInreachLinkResolution:
    """A message with only an inReach share link resolves coordinates by
    fetching the share page (the one network call in parsing)."""

    LINK_PAGE = '{"messages":[{"Latitude":44.11111,"Longitude":-73.22222}]}'

    @responses.activate
    def test_link_only_message_resolves(self):
        responses.get('https://inreachlink.com/FAKE123', body=self.LINK_PAGE)

        result = parse_message('Fires inreachlink.com/FAKE123')
        assert result["coords"] == (44.11111, -73.22222)

    @responses.activate
    def test_hyphenated_link_code_resolves(self):
        """inReach link codes are base64url-style and can contain hyphens."""
        responses.get('https://inreachlink.com/FAKE-1a2B3c-xY', body=self.LINK_PAGE)

        result = parse_message('Fires inreachlink.com/FAKE-1a2B3c-xY')
        assert result["coords"] == (44.11111, -73.22222)

    @responses.activate
    def test_typed_coordinates_skip_the_fetch(self):
        """No HTTP when the message already has coordinates."""
        # No mock registered: any request would raise a ConnectionError.
        result = parse_message('Fires (52.11111, -115.22222) inreachlink.com/FAKE123')
        assert result["coords"] == (52.11111, -115.22222)

    @responses.activate
    def test_fetch_failure_returns_no_coordinates(self):
        responses.get('https://inreachlink.com/FAKE123', status=503)

        assert parse_message('Fires inreachlink.com/FAKE123') is None

    @responses.activate
    def test_page_without_coordinates_returns_none(self):
        responses.get('https://inreachlink.com/FAKE123', body='<html>no location</html>')

        assert parse_message('Fires inreachlink.com/FAKE123') is None


class TestAppleShortLinks:
    """Apple's maps.apple short domain redirects to a full maps.apple.com URL."""

    TARGET = ('https://maps.apple.com/place'
              '?coordinate=49.11111,-123.22222&name=Pin&map=h')

    @responses.activate
    def test_short_link_resolves(self):
        responses.get('https://maps.apple/p/FAKE.abc', status=301,
                      headers={'Location': self.TARGET})
        responses.get(self.TARGET, body='')

        result = parse_message('https://maps.apple/p/FAKE.abc')
        assert result["coords"] == (49.11111, -123.22222)

    @responses.activate
    def test_short_link_beats_appended_device_location(self):
        """A shared pin is deliberate; it outranks the device tail."""
        responses.get('https://maps.apple/p/FAKE.abc', status=301,
                      headers={'Location': self.TARGET})
        responses.get(self.TARGET, body='')

        result = parse_message('https://maps.apple/p/FAKE.abc (-43.51234, 172.61234)')
        assert result["coords"] == (49.11111, -123.22222)

    @responses.activate
    def test_failed_expansion_falls_back_to_device_tail(self):
        responses.get('https://maps.apple/p/FAKE.abc', status=503)

        result = parse_message('https://maps.apple/p/FAKE.abc (-43.51234, 172.61234)')
        assert result["coords"] == (-43.51234, 172.61234)


class TestGoogleShortLinks:
    """Google's maps.app.goo.gl short domain redirects to a full maps URL
    whose coordinates live in the path and the !3d/!4d pin blob."""

    TARGET = ('https://www.google.com/maps/place/49.11111,-123.22222/'
              'data=!4m6!3m5!1s0!7e2!8m2!3d49.1111199!4d-123.2222299!18m1!1e1')

    @responses.activate
    def test_short_link_resolves_to_pin_precision(self):
        responses.get('https://maps.app.goo.gl/FAKE123', status=302,
                      headers={'Location': self.TARGET})
        responses.get(self.TARGET, body='')

        result = parse_message('https://maps.app.goo.gl/FAKE123?g_st=ac'.replace('?g_st=ac', ''))
        assert result["coords"] == (49.1111199, -123.2222299)

    def test_expanded_place_url_parses_directly(self):
        """The full URL form parses without any network, preferring the
        !3d/!4d pin over the lower-precision path pair."""
        result = parse_message(self.TARGET)
        assert result["coords"] == (49.1111199, -123.2222299)

    @responses.activate
    def test_failed_expansion_falls_back_to_device_tail(self):
        responses.get('https://maps.app.goo.gl/FAKE123', status=503)

        result = parse_message('https://maps.app.goo.gl/FAKE123 (-43.51234, 172.61234)')
        assert result["coords"] == (-43.51234, 172.61234)


class TestZoleoLinks:
    """ZOLEO share links are the device's own location (like inReach):
    resolved only as a last resort, through an intermediate shortener to
    a Google Maps q= URL."""

    @responses.activate
    def test_zoleo_link_resolves_through_the_chain(self):
        responses.get('https://sms2zoleo.com/FAKE1', status=302,
                      headers={'Location': 'https://d.example.ms/FAKE1'})
        responses.get('https://d.example.ms/FAKE1', status=302,
                      headers={'Location': 'https://www.google.com/maps?q=49.11111,-123.22222'})
        responses.get('https://www.google.com/maps?q=49.11111,-123.22222', body='')

        result = parse_message('Fires? http://sms2zoleo.com/FAKE1')
        assert result["coords"] == (49.11111, -123.22222)

    @responses.activate
    def test_typed_coordinates_skip_the_fetch(self):
        """A device link never outranks typed coordinates; no HTTP happens."""
        # No mock registered: any request would raise a ConnectionError.
        result = parse_message('Fires (52.11111, -115.22222) http://sms2zoleo.com/FAKE1')
        assert result["coords"] == (52.11111, -115.22222)

    @responses.activate
    def test_failed_zoleo_expansion_returns_none(self):
        responses.get('https://sms2zoleo.com/FAKE1', status=503)

        assert parse_message('Fires? http://sms2zoleo.com/FAKE1') is None


class TestFireLookupParsing:
    """Test extraction of a "fire <id-or-name>" specific-fire lookup."""

    def test_fire_number_without_coords(self):
        """A bare fire number after the keyword is the lookup term."""
        result = parse_message("fire K70597")
        assert result["fire_id"] == "K70597"
        assert result["coords"] is None

    def test_fires_plural_keyword(self):
        """The plural 'fires' keyword also triggers a lookup."""
        result = parse_message("fires HWF-096-2026")
        assert result["fire_id"] == "HWF-096-2026"

    def test_keyword_case_insensitive(self):
        """The keyword matches regardless of case."""
        result = parse_message("FIRE V10758")
        assert result["fire_id"] == "V10758"

    def test_multiword_name(self):
        """A multi-word fire name is captured intact."""
        result = parse_message("fire Kullagh Creek")
        assert result["fire_id"] == "Kullagh Creek"

    def test_hyphenated_identifier_preserved(self):
        """Integers and hyphens survive (e.g. QC-2026-001)."""
        result = parse_message("fire QC-2026-001")
        assert result["fire_id"] == "QC-2026-001"

    def test_id_with_appended_device_coords(self):
        """A device's trailing coordinates are stripped from the term but
        still parsed as coords, so distance can be shown."""
        result = parse_message("fire V10758 (50.5, -122.1)")
        assert result["fire_id"] == "V10758"
        assert result["coords"] == (50.5, -122.1)

    def test_id_with_device_link_and_coords(self):
        """A device share link between the id and coords is stripped."""
        result = parse_message("fire K70597 inreachlink.com/ABC123 (50.5, -122.1)")
        assert result["fire_id"] == "K70597"
        assert result["coords"] == (50.5, -122.1)

    def test_plain_fire_keyword_with_coords_is_not_a_lookup(self):
        """'fire' with only coordinates is the normal nearby request."""
        result = parse_message("fire (49.25, -123.01)")
        assert result["fire_id"] is None
        assert result["coords"] == (49.25, -123.01)

    def test_filter_words_are_not_a_lookup_term(self):
        """Filter keywords after 'fire' do not become a lookup term."""
        result = parse_message("fire active 25km (49.25, -123.01)")
        assert result["fire_id"] is None
        assert result["fire_filters"]["status"] == "active"
        assert result["fire_filters"]["distance"] == 25

    def test_no_keyword_no_lookup(self):
        """Coordinates without the fire keyword produce no lookup term."""
        result = parse_message("(49.25, -123.01)")
        assert result["fire_id"] is None

    def test_lookup_without_coords_still_returns_dict(self):
        """A lookup with no coordinates is a valid request, not None."""
        assert parse_message("fire K70597") is not None

    def test_bare_keyword_returns_none(self):
        """The keyword alone, with nothing usable, is not a request."""
        assert parse_message("fire") is None
