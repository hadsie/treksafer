"""Parses inbound text, locates fires, and generates the responses."""

import logging

from .config import get_config
from .helpers import parse_message
from .fires import FindFires

_SMS_LIMIT = 159

class Messages:
    def no_gps(self):
        return 'TrekSafer ERROR: No GPS location found. Ensure device is setup to include location in sent message or manually include coordinates with "(lat, long)".'

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
        if msg_length > _SMS_LIMIT and size != "short":
            new_size = "medium" if size == "full" else "short"
            message = self._fire(fire, new_size)

        return message

    @staticmethod
    def _message_length(message):
        """Computes the byte length of a string including emojis."""
        return len(message.encode(encoding='utf_16_le'))/2

    @staticmethod
    def _format_distance(meters):
        """Return a nicely formatted distance string in km.

        Rules
        -----
        1. < 10 km  → round to 1 decimal place
        2. ≥ 10 km  → round to nearest whole km
        3. Never show a trailing .0
        """
        km = float(meters)/1000
        if km < 10:
            # scale up, round to nearest integer, scale back
            # otherwise we have inconsistent rounding on round(x.95, 1)
            # See round(7.95, 1) vs round(8.95, 1)
            km_rounded = round(km * 10) / 10
        else:
            km_rounded = round(km)

        # Strip the trailing “.0” if the number is an integer
        return int(km_rounded) if km_rounded.is_integer() else km_rounded

def handle_message(message):
    responses = Messages()
    coords = parse_message(message)
    if not coords:
        logging.warning('No GPS coords found in message.')
        logging.warning(message)
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
