from app.messages import Messages

class TestMessages:
    def test_standard_fire(self):
        fire = {
            "Fire": 'TEST1',
            "Name": 'Test One',
            "Location": 'The first test',
            "Size": 4000,
            "Distance": 1000,
            "Direction": 'NW',
            "Status": 'OUT_CNTRL',
        }
        message = Messages().fire(fire)
        assert len(message) == 86, f"Got {len(message)} characters:\n{message}"

    def test_really_long_location_fire(self):
        fire = {
            "Fire": 'TEST2',
            "Name": 'Long location test fire',
            "Location": 'Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Aenean commodo ligula eget dolor. Aenean massa. Cum sociis natoque penatibus et magnis dis parturient montes, nascetur rid',
            "Size": 72999.2,
            "Distance": 25567.5,
            "Direction": 'ENE',
            "Status": 'OUT_CNTRL',
        }
        message = Messages().fire(fire)
        assert len(message) == 59, f"Got {len(message)} characters:\n{message}"

    def test_short_fire(self):
        fire = {
            "Fire": 'TEST3',
            "Name": 'Long location test fire',
            "Location": 'Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Aenean commodo ligula eget dolor. Aenean massa. Cum sociis natoque penatibus et magnis dis parturient montes, nascetur rid',
            "Size": 72999.2,
            "Distance": 25567.5,
            "Direction": 'ENE',
            "Status": 'OUT_CNTRL',
        }
        message = Messages().fire(fire, "short")
        assert len(message) == 22, f"Got {len(message)} characters:\n{message}"


def mock_fire(**overrides):
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


def test_format_full_message():
    m = Messages().fire(mock_fire(), size="full")
    assert "Fire: Little Creek (K72481)" in m
    assert "Size: 123 ha" in m
    assert "Status:" in m
    # Ensure it's multiple lines.
    assert "\n" in m


def test_format_medium_truncates():
    m = Messages().fire(mock_fire(), size="medium")
    assert "Status" not in m
    assert m.count("\n") == 2


def test_format_auto_shortens_for_sms():
    big_name = "VeryLongFireNameThatWillPushTheMessageOverTheLimit"
    # Make it even longer.
    fire = mock_fire(Name=big_name * 4)
    m = Messages().fire(fire, size="full")
    assert len(m.encode("utf_16_le")) // 2 <= 159


def test_distance_rounding_rules():
    # < 10 km = 1 decimal
    assert Messages()._format_distance(9444) == 9.4
    # Rounding
    assert Messages()._format_distance(9950) == 10
    # Strips .0
    assert Messages()._format_distance(1000) == 1
    # ≥ 10 km = integer
    assert Messages()._format_distance(43210) == 43
    # Exactly 10km
    assert Messages()._format_distance(10000) == 10
