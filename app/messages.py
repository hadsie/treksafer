"""Parses inbound text, locates fires, and generates the responses."""

import logging

from .config import get_config
from .helpers import parse_message, get_aqi
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
        return int(km_rounded) if km_rounded == int(km_rounded) else km_rounded

def handle_message(message):
    """
    Process an inbound message to locate nearby fires and generate appropriate responses.

    This function parses the incoming message to extract GPS coordinates and optional
    filters, searches for fires near those coordinates, and formats appropriate
    response messages based on the findings.

    :param str message: The inbound message containing location information
    :return: Formatted response message(s) about nearby fires or error messages
    :rtype: str
    """
    responses = Messages()
    parsed_data = parse_message(message)
    if not parsed_data:
        logging.warning('No GPS coords found in message.')
        logging.warning(message)
        return responses.no_gps()

    coords = parsed_data["coords"]
    user_filters = parsed_data["filters"]

    aqi_message = ""
    settings = get_config()
    if settings.include_aqi:
        aqi = get_aqi(coords)
        aqi_message = f"AQI: {aqi}\n\n"

    findfires = FindFires(coords)
    if findfires.out_of_range():
        return aqi_message + responses.outside_of_area()

    logging.info(message)
    fires = findfires.nearby(user_filters)
    if not fires:
        return aqi_message + responses.no_fires()

    fire_messages = []
    for fire in fires:
        fire_messages.append(responses.fire(fire))
    return aqi_message + "\n\n".join(fire_messages)
