from app.helpers import parse_message
from app.messages import Messages

class TestParseMessage:
    def test_basic_inreach(self):
        message = "Test basic message with, punctuation and coordinates. inreachlink.com/ABC1234  (52.5092, -115.6182)"
        assert(parse_message(message) == (52.5092, -115.6182))

    def test_coords_only_pos_neg(self):
        message = "(52.5092, -115.6182)"
        assert(parse_message(message) == (52.5092, -115.6182))

    def test_coords_only_neg_pos(self):
        message = "(-52.5092, 115.6182)"
        assert(parse_message(message) == (-52.5092, 115.6182))

    def test_coords_arbitrary_placement(self):
        message = "Test basic message   (52.5092, -115.6182) coordinates arbitrarily placed."
        assert(parse_message(message) == (52.5092, -115.6182))

    def test_newline(self):
        message = "Test basic message  \n (52.5092, -115.6182) coordinates arbitrarily placed."
        assert(parse_message(message) == (52.5092, -115.6182))

    def test_newline_in_coords(self):
        message = "Test basic message (52.5092,\n -115.6182) coordinates arbitrarily placed."
        assert(parse_message(message) == (52.5092, -115.6182))

    def test_newline_and_spaces_in_coords(self):
        message = "Here:\n( 52.5092 ,\n-115.6182 )"
        assert parse_message(message) == (52.5092, -115.6182), f"Got {message}"

    def test_coords_no_decimal(self):
        message = "Test basic message (52, -115) coordinates arbitrarily placed."
        assert(parse_message(message) == (52, -115))

    def test_invalid_multiple_pairs(self):
        message = "Invalid (1234, 99) valid (52.5092, -115.6182)."
        assert(parse_message(message) == (52.5092, -115.6182))

    def test_valid_multiple_pairs(self):
        message = "Valid (12, 99) valid (52.5092, -115.6182)."
        assert(parse_message(message) == (12, 99))

    def test_invalid_coords(self):
        message = "Message with invalid coords (1234, 99)"
        assert(parse_message(message) == False)

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
