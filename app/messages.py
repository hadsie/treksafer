import logging

from .config import get_config
from .helpers import parse_message
from .fires import FindFires

class Messages:
    def no_gps(self):
        return 'TrekSafer ERROR: No GPS location found. Ensure device is setup to include location in sent message or manually include coordinates with "(lat, long)".',

    def outside_of_area(self):
        return 'TrekSafer ERROR: GPS coordinates outside of supported fire perimeter area. No data available.'

    def no_fires(self):
        # @todo - Pull the 100 value from settings.
        settings = get_config()
        radius = settings.fire_radius
        return f'No fires reported within a {radius}km radius of your location.'

    def fires(self, fires):
        messages = []
        for fire in fires:
            messages.append(self.fire(fire))
        return messages

    def fire(self, fire, size = "full"):
        message = self._fire(fire, size)
        return message

    def _fire(self, fire, size = "full"):
        """
        Format the message for this specific fire.

        :param dict fire: The fire data dictionary.
        :param str size: The message size, one of full, medium, short.
        :return: The formatted message.
        :rtype: str
        """
        level_fields = {
            "full": [
                ("FullName", "Fire: {}"),
                ("Location", "Location: {}"),
                ("DistDir", "{}"),
                ("Size", "Size: {} ha"),
                ("Status", "Status: {}")
            ],
            "medium": [
                ("FullName", "Fire: {}"),
                ("DistDir", "{}"),
                ("Size", "Size: {} ha")
            ],
            "short": [
                ("Fire", "{}"),
                ("DistDir", "{}"),
                ("Size", "{}ha")
            ]
        }
        fields = level_fields[size]

        # Strip all strings
        fire = {k:str(v).strip() for k,v in fire.items()}

        fire['FullName'] = fire['Fire']
        if 'Name' in fire and fire['Name'] != fire['Fire']:
            if size == "full":
                fire['FullName'] = f"{fire['Name']} ({fire['Fire']})"
            elif size == "medium":
                fire['FullName'] = f"{fire['Name']} {fire['Fire']}"

        distance = self._format_distance(fire['Distance'])
        fire['DistDir'] = f"{distance}km {fire['Direction']}"
        fire['Size'] = round(float(fire['Size']))
        message = []

        for key, template in fields:
            value = fire.get(key)
            if not value:
                continue
            message.append(template.format(value))

        message = "\n".join(message)
        msg_length = self._message_length(message)
        if msg_length > 159 and size != "small":
            new_size = "medium" if size == "full" else "small"
            message = self._fire(fire, new_size)

        return message
        # location_line = -1
        # if not fire['Name'] or fire['Fire'] == fire['Name']:
        #     if fire['Fire']:
        #         lines.append(fire['Fire'])
        # else:
        #     lines.append(f"{fire['Name']} {fire['Fire']}")

        # if fire['Location']:
        #     lines.append(fire['Location'])
        #     location_line = len(lines) - 1

        # if fire['Size']:
        #     if format:
        #         lines.append(f"Size: {fire['Size']} ha")
        #     else:
        #         lines.append(f"{fire['Size']} ha")

        # distance = self._format_distance(fire['Distance'])
        # distance_line = str(distance)
        # if format:
        #     distance_line += 'km'
        # if fire['Direction']:
        #     distance_line += ' ' + fire['Direction']
        # lines.append(distance_line)
        # if fire['Status']:
        #     lines.append(fire['Status'])
        #
        # message = "\n".join(lines)
        # msg_length = self._message_length(message)
        # if msg_length > 159 and location_line != -1:
        #     # Remove special characters.
        #     special = re.compile("["
        #                          u"\U0001F600-\U0001F64F"  # emoticons
        #                          u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        #                          u"\U0001F680-\U0001F6FF"  # transport & map symbols
        #                          u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        #                          "]+", flags=re.UNICODE)
        #     lines[location_line] = special.sub(r'', lines[location_line])
        #     msg_length = self._message_length(message)
        #
        # if msg_length > 159 and location_line != -1:
        #     overage = msg_length - 159
        #     if overage >= len(lines[location_line]):
        #         # Remove the location line altogether if we're already maxing out the size.
        #         fire['Location'] = lines[location_line]
        #     else:
        #         diff = lines[location_line] - overage
        #         lines[location_line] = lines[location_line][:diff]
        #         msg_length = self._message_length(message)
        #
        # if msg_length > 159:
        #     fire['Location'] = ''
        #     fire['Name'] = ''
        #     fire['Status'] = ''
        #     lines = self._fire(fire, False)
        #
        # return lines

    def _message_length(self, message):
        """Computes the byte length of a string including emojis."""
        return len(message.encode(encoding='utf_16_le'))/2

    def _format_distance(self, meters):
        """Return a nicely formatted distance string in km.

        Rules
        -----
        1. < 10 km  → round to 1 decimal place
        2. ≥ 10 km  → round to nearest whole km
        3. Never show a trailing .0
        """
        km = float(meters)/1000
        km_rounded = round(km, 1 if km < 10 else 0)

        # Strip the trailing “.0” if the number is an integer
        return int(km_rounded) if km_rounded.is_integer() else km_rounded

def handle_message(message):
    responses = Messages()
    coords = parse_message(message)
    if not coords:
        logging.warning('No GPS coords found in message.')
        logging.warning(message.body)
        return responses.no_gps()
    findfires = FindFires(coords)
    if findfires.out_of_range():
        return responses.outside_of_area()

    logging.info(message)
    fires = findfires.nearby()
    if not fires:
        return responses.no_fires()

    fire_messages = []
    for fire in fires:
        fire_messages.append(responses.fire(fire))
    return "\n".join(fire_messages)
