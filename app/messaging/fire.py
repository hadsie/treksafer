"""Fire report rendering: per-fire messages, lookup enrichment lines,
and the no-result replies (no fires, out of coverage, data unavailable)."""

from datetime import datetime, timezone
from typing import Dict

from ..filters import STATUS_LEVELS
from .assembler import SMS_LIMIT, message_length


class FireMessages:

    @staticmethod
    def _location(coords: tuple) -> str:
        return f'({coords[0]:.5f}, {coords[1]:.5f})'

    def outside_of_area(self, coords: tuple) -> str:
        """Out-of-coverage error, echoing the searched coordinates so the
        user can verify what location their message parsed to."""
        return ('GPS coordinates outside of supported fire perimeter '
                f'area. No data available for your location {self._location(coords)}.')

    def data_unavailable(self) -> str:
        return 'Fire data is temporarily unavailable for your area. Try again later.'

    def fire_not_found(self, term: str) -> str:
        """A fireid lookup matched nothing. Informational, so it carries no
        TrekSafer branding."""
        return (f'No fire matching "{term}" was found. Check the fire number, '
                'or send "fires" with your location for nearby fires.')

    def fire_perimeter(self, perimeter: Dict) -> str:
        """One line of perimeter bounds, e.g.
        'Perim: 50.97-50.99N 89.44-89.28W'."""
        minlat, maxlat, minlon, maxlon = perimeter['bounds']
        ns = 'N' if (minlat + maxlat) >= 0 else 'S'
        ew = 'W' if (minlon + maxlon) < 0 else 'E'
        return (f"Perim: {abs(minlat):.2f}-{abs(maxlat):.2f}{ns} "
                f"{abs(minlon):.2f}-{abs(maxlon):.2f}{ew}")

    def fire_edge(self, edge: Dict) -> str:
        """One line of recent perimeter movement, e.g.
        'Edge: moved ~8km E in the last 26h, was 19km from you'."""
        moved = self._format_distance(edge['advance_m'])
        line = (f"Edge: moved ~{moved}km {edge['direction']} "
                f"in the last {self._ago(edge['since'])}")
        if edge.get('was_m') is not None:
            line += f", was {self._format_distance(edge['was_m'])}km from you"
        return line

    def no_fires(self, distance: float, coords: tuple, status_filter: str = None) -> str:
        """Generate no fires message with optional status filter context.

        The searched coordinates are echoed so the user can verify what
        location their message parsed to.

        Args:
            distance: Search radius in km
            coords: The (latitude, longitude) that was searched
            status_filter: Status filter applied ('active', 'managed', 'controlled', 'out', 'all', or None)

        Returns:
            Formatted message string
        """
        location = self._location(coords)
        base_msg = f'No fires reported within {distance}km of your location {location}.'

        # If no filter or 'all'/'out', return simple message
        if not status_filter or status_filter in ('all', 'out'):
            return base_msg

        # Get all statuses included in this filter level
        filter_level = STATUS_LEVELS.get(status_filter)
        if not filter_level:
            return base_msg

        # Include all statuses at or below this level
        included_statuses = [
            status for status, level in STATUS_LEVELS.items()
            if level <= filter_level
        ]
        included_statuses.sort(key=lambda s: STATUS_LEVELS[s])  # Sort by level

        # Format message with included statuses
        status_list = ', '.join(included_statuses)
        return f'No fires reported within {distance}km of your location {location}. (Showing: {status_list})'

    @staticmethod
    def _timestamp(dt: datetime) -> str:
        return f"{dt:%b} {dt.day} {dt:%H:%M}"

    @staticmethod
    def _span(hours: float) -> str:
        """A compact duration: '<1h', hours up to 48 ('26h'), days beyond ('4d')."""
        if hours < 1:
            return '<1h'
        return f"{round(hours)}h" if hours <= 48 else f"{round(hours / 24)}d"

    @staticmethod
    def _ago(moment: datetime) -> str:
        """How long ago an aware UTC datetime was, e.g. '3h'."""
        hours = (datetime.now(timezone.utc) - moment).total_seconds() / 3600
        return FireMessages._span(hours)

    @staticmethod
    def as_of(current: datetime) -> str:
        """How old the served fire's information is: the age of the agency's
        own update where known, otherwise of the feed read."""
        return f"As of {FireMessages._ago(current)} ago"

    @staticmethod
    def data_age(fetched: datetime) -> str:
        """Freshness marker shown when a response was built from stored
        data. fetched must already be in the user's local timezone."""
        return f"Data from {FireMessages._timestamp(fetched)}"

    def fires(self, fires: list[Dict]) -> list[str]:
        messages = []
        for fire in fires:
            messages.append(self.fire(fire))
        return messages

    def fire(self, fire: Dict, size: str = "full") -> str:
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

        # History annotations (growth.enrich) render as line suffixes and
        # must survive stringification and the downsizing recursion.
        change = fire.get('SizeChange')
        is_new = bool(fire.get('New'))

        # Strip all strings
        fire = {k: str(v).strip() for k, v in fire.items()
                if k not in ('SizeChange', 'New')}
        if change:
            fire['SizeChange'] = change
        if is_new:
            fire['New'] = True

        fire['FullName'] = fire['Fire']
        if 'Name' in fire and fire['Name'] != fire['Fire']:
            if size == "full":
                fire['FullName'] = f"{fire['Name']} ({fire['Fire']})"
            elif size == "medium":
                fire['FullName'] = f"{fire['Name']} {fire['Fire']}"

        # Distance/direction are present only when the request carried
        # coordinates (a bare id/name lookup has neither).
        if 'Distance' in fire:
            distance = self._format_distance(fire['Distance'])
            fire['DistDir'] = f"{distance}km {fire['Direction']}"
        # New fires may not have a size estimate yet; the line is omitted.
        if 'Size' in fire:
            fire['Size'] = self._format_size(fire['Size'])
        message = []

        for key, template in fields:
            value = fire.get(key)
            if not value:
                continue
            line = template.format(value)
            if is_new and key in ('FullName', 'Fire'):
                line += " (NEW)"
            # The delta rides the Size line; short is the last-resort
            # squeeze and shows the bare size.
            if change and key == 'Size' and size != 'short':
                line += f" ({self._size_change(change)})"
            message.append(line)

        message = "\n".join(message)
        msg_length = message_length(message)
        if msg_length > SMS_LIMIT and size != "short":
            new_size = "medium" if size == "full" else "short"
            message = self._fire(fire, new_size)

        return message

    @staticmethod
    def _size_change(change: Dict) -> str:
        """Render a growth.enrich size change, e.g. '+500 since 26h ago'."""
        return f"{change['delta']:+d} since {FireMessages._span(change['hours'])} ago"

    @staticmethod
    def _message_length(message: str) -> float:
        """Computes the byte length of a string including emojis."""
        return message_length(message)

    @staticmethod
    def _format_size(hectares: float | str) -> str:
        """Return a nicely formatted fire size in hectares.

        Rules
        -----
        1. 0 ha     → "" (no estimate yet; the Size line is omitted)
        2. < 0.1 ha → "<0.1" so tiny new fires still show a size
        3. < 10 ha  → round to 1 decimal place
        4. ≥ 10 ha  → round to nearest whole hectare
        5. Never show a trailing .0
        """
        ha = float(hectares)
        if ha == 0:
            return ""
        if ha < 0.1:
            return "<0.1"
        # Scale-up rounding for consistency; see _format_distance.
        rounded = round(ha * 10) / 10 if ha < 10 else round(ha)
        return str(int(rounded)) if rounded == int(rounded) else str(rounded)

    @staticmethod
    def _format_distance(meters: float) -> int | float:
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
