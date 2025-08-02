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
    assert parse_message(msg) == (49.1234, -123.9876)


def test_parse_message_anywhere():
    msg = "Fire at  49.1 , -123.9  about 2 km west."
    assert parse_message(msg) == (49.1, -123.9)


def test_parse_message_none():
    assert parse_message("hello world") is None
