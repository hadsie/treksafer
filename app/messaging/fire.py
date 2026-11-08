"""Fire report rendering: per-fire messages, lookup enrichment lines,
and the no-result replies (no fires, out of coverage, data unavailable)."""

from datetime import datetime, timezone
from functools import cache
from typing import Dict, Iterable

import yaml

from ..config import get_config
from ..filters import STATUS_LEVELS
from ..weather import AqiReport, WindReport
from .assembler import Block, assemble, fits_segment


@cache
def _status_terms() -> Dict[str, str]:
    """Status wording -> SMS abbreviation."""
    with open('data/fire_terms.yaml') as f:
        return yaml.safe_load(f)['status']


@cache
def _status_words() -> Dict[str, str]:
    """Feed status code -> full wording."""
    return {abbreviation: word for word, abbreviation in _status_terms().items()}


_TIERS = ('full', 'medium', 'compact')


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

    @staticmethod
    def aqi(report: AqiReport) -> str:
        """The AQI line, or None when the air quality is fine. If AQI is
        forecast to increase past the trend delta in the forecast window
        include its "rising to" form."""
        thresholds = get_config().thresholds
        if (report.peak >= thresholds.aqi_floor
                and report.peak - report.current >= thresholds.aqi_trend_delta):
            return f"AQI: {report.current} rising to {report.peak}"
        if report.current >= thresholds.aqi_floor:
            return f"AQI: {report.current}"
        return None

    @staticmethod
    def wind(report: WindReport) -> str:
        """The wind line, or None when the wind is mild enough. Mirrors
        the AQI logic."""
        thresholds = get_config().thresholds
        if (report.peak is not None
                and report.peak >= thresholds.wind_floor
                and report.peak - report.speed >= thresholds.wind_trend_delta):
            return f"Wind: {report.speed}km/h from {report.direction} rising to {report.peak}"
        if report.speed >= thresholds.wind_floor:
            return f"Wind: {report.speed}km/h from {report.direction}"
        return None

    def fires(self, fires: list[Dict]) -> list[str]:
        messages = []
        for fire in fires:
            messages.append(self.fire(fire))
        return messages

    def fire(self, fire: Dict, size: str = "full") -> str:
        message = self._fire(fire, size)
        return message

    def fire_ladder(self, fire: Dict) -> list[str]:
        """Every size of this fire's message, largest first."""
        return [self._render(fire, size) for size in _TIERS]

    def fire_report(self, fires: list[Dict],
                    header: Iterable[Block] = (),
                    trailer: Iterable[Block] = ()) -> list[Block]:
        """Fit ranked fires into the segment budget, shrinking detail down
        the list and summarizing the rest on a '+N more' line."""
        thresholds = get_config().thresholds
        budget = thresholds.fire_report_segments
        header, trailer = list(header), list(trailer)
        kept = []
        tier = 0
        for fire in fires[:thresholds.fire_count_cap]:
            while tier < len(_TIERS):
                block = Block(self.fire_ladder(fire)[tier:], kind='fires')
                if len(assemble([*header, *kept, block, *trailer])) <= budget:
                    kept.append(block)
                    break
                tier += 1
            if tier == len(_TIERS):
                break
        # Make room for the '+N more' line by shrinking, then dropping, the
        # last kept fire.
        while True:
            blocks = [*header, *kept]
            dropped = fires[len(kept):]
            if dropped:
                blocks.append(Block(self.more_fires_ladder(dropped), kind='fires'))
            blocks.extend(trailer)
            if not kept or len(assemble(blocks)) <= budget:
                return blocks
            if len(kept[-1].ladder) > 1:
                kept[-1] = Block(kept[-1].ladder[1:], kind='fires')
            else:
                kept.pop()

    def more_fires_ladder(self, dropped: list[Dict]) -> list[str]:
        """The '+N more' line: the dropped ids, falling back to a bare count."""
        distances = sorted(self._format_distance(fire['Distance']) for fire in dropped)
        span = (f"{distances[0]}-{distances[-1]}km" if distances[0] != distances[-1]
                else f"{distances[0]}km")
        ids = ' '.join(fire['Fire'] for fire in dropped)
        return [f"+{len(dropped)} {span}: {ids}", f"+{len(dropped)} more {span}"]

    def _fire(self, fire, size = "full"):
        """Render at size, stepping full -> medium -> compact until the
        message fits one SMS. Each retry re-renders from the same fire
        dict, so _render must leave it unmodified."""
        message = self._render(fire, size)
        if not fits_segment(message) and size != "compact":
            return self._fire(fire, "medium" if size == "full" else "compact")
        return message

    def _render(self, fire, size):
        """
        Format the message for this specific fire.

        :param dict fire: The fire data dictionary.
        :param str size: The message size, one of full, medium, compact.
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
            ]
        }

        # Strip all strings
        fire = {k: v.strip() if isinstance(v, str) else v
                for k, v in fire.items()}

        # Feeds that publish status codes (CA: OC/BH/UC) display as words.
        if 'Status' in fire:
            fire['Status'] = _status_words().get(fire['Status'], fire['Status'])
            fire['StatusCode'] = _status_terms().get(fire['Status'], fire['Status'])

        # Skip a Location that just repeats the fire's name or id.
        if fire.get('Location') in (fire.get('Name'), fire['Fire']):
            fire.pop('Location', None)

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
        # New fires may not have a size estimate yet.
        if 'Size' in fire:
            fire['Size'] = self._format_size(fire['Size'])

        if size == 'compact':
            return self._compact(fire)

        fields = level_fields[size]
        message = []

        change = fire.get('SizeChange')
        is_new = bool(fire.get('New'))
        for key, template in fields:
            value = fire.get(key)
            if not value:
                if key == 'Size' and size == 'full':
                    message.append("Size: Unknown")
                continue
            line = template.format(value)
            if is_new and key == 'FullName':
                line += " (NEW)"
            # Medium keeps the status as its code on the distance line.
            if size == 'medium' and key == 'DistDir' and fire.get('StatusCode'):
                line += f" {fire['StatusCode']}"
            # The delta and its timeframe ride the Size line.
            if change and key == 'Size':
                line += f" ({self._size_change(change)})"
            message.append(line)

        return "\n".join(message)

    def _compact(self, fire: Dict) -> str:
        """One-line rendering, e.g. 'K72481 423ha(+300) 12km NW OC'."""
        parts = [fire['Fire'] + (' (NEW)' if fire.get('New') else '')]
        if fire.get('Size'):
            change = fire.get('SizeChange')
            delta = f"({change['delta']:+d})" if change else ''
            parts.append(f"{fire['Size']}ha{delta}")
        if fire.get('DistDir'):
            parts.append(fire['DistDir'])
        if fire.get('StatusCode'):
            parts.append(fire['StatusCode'])
        return ' '.join(parts)

    @staticmethod
    def _size_change(change: Dict) -> str:
        """Render a growth.enrich size change, e.g. '+500 since 26h ago'."""
        return f"{change['delta']:+d} since {FireMessages._span(change['hours'])} ago"

    @staticmethod
    def _format_size(hectares: float | str) -> str:
        """Return a nicely formatted fire size in hectares.

        Rules
        -----
        1. 0 ha     → "" (no estimate yet; rendered as unknown or omitted)
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
        """Return a nicely formatted distance in km, as an int when whole.

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
