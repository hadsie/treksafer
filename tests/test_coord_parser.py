import pytest

from app.helpers import parse_message

class TestParseMessage:
    def test_basic_inreach(self):
        message = "Test basic message with, punctuation and coordinates. inreachlink.com/ABC1234  (52.5092, -115.6182)"
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_coords_only_pos_neg(self):
        message = "(52.5092, -115.6182)"
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_coords_only_neg_pos(self):
        message = "(-52.5092, 115.6182)"
        result = parse_message(message)
        assert result["coords"] == (-52.5092, 115.6182)

    def test_coords_arbitrary_placement(self):
        message = "Test basic message   (52.5092, -115.6182) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_newline(self):
        message = "Test basic message  \n (52.5092, -115.6182) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_newline_in_coords(self):
        message = "Test basic message (52.5092,\n -115.6182) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_newline_and_spaces_in_coords(self):
        message = "Here:\n( 52.5092 ,\n-115.6182 )"
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182), f"Got {message}"

    def test_coords_no_decimal(self):
        message = "Test basic message (52, -115) coordinates arbitrarily placed."
        result = parse_message(message)
        assert result["coords"] == (52, -115)

    def test_invalid_multiple_pairs(self):
        message = "Invalid (1234, 99) valid (52.5092, -115.6182)."
        result = parse_message(message)
        assert result["coords"] == (52.5092, -115.6182)

    def test_valid_multiple_pairs(self):
        message = "Valid (12, 99) valid (52.5092, -115.6182)."
        result = parse_message(message)
        assert result["coords"] == (12, 99)

    def test_invalid_coords(self):
        message = "Message with invalid coords (1234, 99)"
        assert parse_message(message) == None

    def test_more_coords(self):
        result1 = parse_message("(49.253491, -123.017063)")
        assert result1["coords"] == (49.253491, -123.017063)

        result2 = parse_message("coords: 50.58225° N, 122.09114° W")
        assert result2["coords"] == (50.58225, -122.09114)

        result3 = parse_message("N 50.58225°, W 122.09114°")
        assert result3["coords"] == (50.58225, -122.09114)

        result4 = parse_message("33.12345° s, 18.54321° e")
        assert result4["coords"] == (-33.12345, 18.54321)

        result5 = parse_message("0.0000, 0.0000")
        assert result5["coords"] == (0.0, 0.0)


# --- Apple Maps share links --- #

APPLE_CASES = [
    (
        "Check this out: "
        "https://maps.apple.com/place?coordinate=49.253491,-123.017063"
        "&name=Dropped%20Pin&span=0.004591,0.014026",
        (49.253491, -123.017063),
    ),
    (
        # Multiple query-string params shuffled - still works.
        "https://maps.apple.com/place?name=Pin&span=0.01,0.01"
        "&coordinate=-12.345678,98.765432",
        (-12.345678, 98.765432),
    ),
]

# --- Google Maps share links --- #

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

# --- Links that should NOT yield coords --- #

NEGATIVE_CASES = [
    # Google link with an address, not lat/lon.
    "https://www.google.com/maps/search/?api=1&query=123+Creekside+Pl++Burnaby++BC",
    # Apple link with no coordinate param.
    "https://maps.apple.com/place?name=Vancouver",
]


@pytest.mark.parametrize("msg,expected", APPLE_CASES + GOOGLE_CASES)
def test_parse_message_success(msg, expected):
    result = parse_message(msg)
    assert result["coords"] == pytest.approx(expected)


@pytest.mark.parametrize("msg", NEGATIVE_CASES)
def test_parse_message_failure(msg):
    assert parse_message(msg) is None
