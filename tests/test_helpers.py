import math
from shapely.geometry import Point

from app.helpers import (
    acres_to_hectares,
    compass_direction,
    parse_message,
)

def test_acres_to_hectares():
    assert acres_to_hectares(0) == 0
    # 1 acre → 0.4 ha (rounded)
    assert acres_to_hectares(1) == 0.4
    # exactly 1 ha
    assert acres_to_hectares(2.4710538147) == 1


def test_compass_direction():
    # Point B due east of A.
    A = Point(0, 0)             # Web-Mercator metres
    B = Point(1000, 0)          # 1 km east
    assert compass_direction(A, B) == "E"

    # Point B ≈ NW of A
    C = Point(-500, 1000)
    assert compass_direction(A, C) in {"NW", "NNW", "WNW"}


def test_parse_message_inreach_brackets():
    msg = "Fire test (49.1234, -123.9876)"
    coords, fire_override = parse_message(msg)
    assert coords == (49.1234, -123.9876)
    assert fire_override is None


def test_parse_message_anywhere():
    msg = "Fire at  49.1 , -123.9  about 2 km west."
    coords, fire_override = parse_message(msg)
    assert coords == (49.1, -123.9)
    assert fire_override is None


def test_parse_message_none():
    assert parse_message("hello world") is None


def test_parse_message_with_active_command():
    """Test that 'active' command is correctly parsed."""
    msg = "(49.123, -123.456) active"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'active'

    msg = "active (49.123, -123.456)"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'active'

    msg = "(49.123, -123.456) ACTIVE"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'active'


def test_parse_message_with_all_command():
    """Test that 'all' command is correctly parsed."""
    msg = "(49.123, -123.456) all"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'out'  # 'all' maps to 'out' level

    msg = "all (49.123, -123.456)"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'out'

    msg = "(49.123, -123.456) ALL"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'out'


def test_parse_message_no_false_matches():
    """Test that fire commands aren't matched in unrelated words."""
    # Should not match "reactive" as "active"
    msg = "(49.123, -123.456) reactive"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override is None

    # Should not match "ball" as "all"
    msg = "(49.123, -123.456) ball game"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override is None


def test_parse_message_mixed_content():
    """Test parsing with various other content."""
    msg = "Emergency evacuation needed at (49.123, -123.456) show me active fires only"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'active'

    msg = "Planning trip to (49.123, -123.456) need all fire info"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override == 'out'


def test_parse_message_coordinates_only():
    """Test that messages with coordinates but no fire commands work as before."""
    msg = "(49.123, -123.456)"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override is None

    msg = "Check fire status at 49.123, -123.456 please"
    coords, fire_override = parse_message(msg)
    assert coords == (49.123, -123.456)
    assert fire_override is None
