"""Avalanche forecast rendering: danger ratings, problems, and the
abbreviated SMS format."""

import logging
from functools import cache
from typing import Dict, Any

import yaml


@cache
def _get_abbrev_cache() -> Dict[str, Dict[str, str]]:
    """Build and cache flattened, case-insensitive lookup cache.

    Returns:
        Dict with keys like "AvalancheCanada:problem_type" mapping to
        uppercase term -> abbreviation dicts
    """
    with open('data/avalanche_terms.yaml', 'r') as f:
        terms = yaml.safe_load(f)

    result = {}
    for provider, term_types in terms.items():
        for term_type, mappings in term_types.items():
            cache_key = f"{provider}:{term_type}"
            result[cache_key] = {k.upper(): v for k, v in mappings.items()}
    return result


class AvalancheMessages:
    """Renders avalanche forecast content. Expects self.provider (the
    selected AvalancheProvider) from the class mixing this in."""

    def _get_abbreviation(self, term_type: str, value: str) -> str:
        """Get abbreviation for a term (case-insensitive).

        Args:
            term_type: 'problem_type', 'likelihood', or 'danger_rating'
            value: Raw value from API

        Returns:
            Abbreviated string, or original value if not found
        """
        cache = _get_abbrev_cache()
        provider_key = self.provider.__class__.__name__.replace('Provider', '')
        value_upper = value.upper()

        # Try provider-specific first
        provider_cache_key = f"{provider_key}:{term_type}"
        result = cache.get(provider_cache_key, {}).get(value_upper)
        if result:
            return result

        # Fall back to default
        default_cache_key = f"default:{term_type}"
        result = cache.get(default_cache_key, {}).get(value_upper)
        if result:
            return result

        # Not found - log and return original
        logging.error(f"Avalanche API error - Unknown {term_type}: {value}")
        return value

    def _format_problems_full(self, problems: list, indent: str = "  ") -> list:
        """Format avalanche problems (full version)."""
        if not problems:
            return []

        parts = ["Problems:"]
        for problem in problems:
            parts.append(f"{indent}• {problem['type']}")

            if problem.get('elevations'):
                parts.append(f"{indent}  Elevations: {', '.join(problem['elevations'])}")

            if problem.get('aspects'):
                parts.append(f"{indent}  Aspects: {', '.join(problem['aspects'])}")

            if problem.get('likelihood') and problem.get('size_min'):
                size_range = f"{problem['size_min']}-{problem['size_max']}"
                parts.append(f"{indent}  {problem['likelihood']}, Size {size_range}")

        return parts

    def _format_forecast_full(self, forecast_data: Dict, dates: list) -> str:
        """Format forecast for any number of dates (full version).

        Args:
            forecast_data: Full forecast data
            dates: List of day name strings to include in forecast

        Returns:
            Formatted forecast string
        """
        parts = [f"Avalanche Forecast: {forecast_data['region']}"]

        # Header: show specific day for single, or "Issued" for multiple
        if len(dates) == 1:
            parts.append(f"Date: {dates[0]}")
        else:
            parts.append(f"Issued: {forecast_data['date_issued']}")

        parts.append("")

        # Format each day
        for day_name in dates:
            if day_name not in forecast_data['forecasts']:
                continue

            # For multiple dates, label each day
            if len(dates) > 1:
                parts.append(f"Date: {day_name}")

            parts.append("Danger Ratings:")
            # Indent more for multi-date to distinguish dates
            indent = "  " if len(dates) == 1 else "    "
            day_forecast = forecast_data['forecasts'][day_name]
            ratings = [
                f"{indent}Alpine: {day_forecast['alpine_rating']}",
                f"{indent}Treeline: {day_forecast['treeline_rating']}",
                f"{indent}Below Treeline: {day_forecast['below_treeline_rating']}"
            ]
            parts.extend(ratings)
            parts.append("")

        # Problems shown once at end
        if forecast_data.get('problems'):
            parts.extend(self._format_problems_full(forecast_data['problems']))

        if forecast_data.get('url'):
            parts.append("")
            parts.append(forecast_data['url'])

        return "\n".join(parts)

    def _format_forecast_abbrev(self, forecast_data: Dict[str, Any], dates: list) -> str:
        """Format forecast in abbreviated form for SMS.

        Args:
            forecast_data: Full forecast data
            dates: List of day name strings to include in forecast

        Returns:
            Abbreviated forecast string
        """
        parts = [forecast_data['region']]

        # Format each day
        for day_name in dates:
            if day_name not in forecast_data['forecasts']:
                continue

            day_forecast = forecast_data['forecasts'][day_name]

            # Abbreviated day (3-letter abbreviation)
            day_abbrev = day_name[:3]

            # Abbreviated danger ratings
            alp = self._get_abbreviation('danger_rating', day_forecast['alpine_rating'])
            tl = self._get_abbreviation('danger_rating', day_forecast['treeline_rating'])
            btl = self._get_abbreviation('danger_rating', day_forecast['below_treeline_rating'])

            parts.append(f"{day_abbrev}: ALP:{alp} TL:{tl} BTL:{btl}")

        # Problems shown after danger ratings
        if forecast_data.get('problems'):
            parts.append("")  # Empty line before problems
            parts.extend(self._format_problems_abbrev(forecast_data['problems']))

        return "\n".join(parts)

    def _format_problems_abbrev(self, problems: list) -> list:
        """Format avalanche problems in abbreviated form."""
        if not problems:
            return []

        parts = []
        for i, problem in enumerate(problems, 1):
            if i > 1:
                parts.append("")  # Empty line between problems

            # Problem type (use modest abbreviations)
            prob_type = self._get_abbreviation('problem_type', problem['type'])
            parts.append(prob_type)

            # Elevations - abbreviate or use "All"
            elevations = problem.get('elevations', [])
            if len(elevations) == 3 or not elevations:
                elev_str = "AllElev"
            else:
                elev_str = self._abbrev_elevations(elevations)

            # Aspects - explicit list or "All"
            aspects = problem.get('aspects', [])
            if len(aspects) >= 7 or not aspects:
                aspect_str = "All"
            else:
                aspect_str = self._abbrev_aspects(aspects)

            parts.append(f"{elev_str} Slp:{aspect_str}")

            # Likelihood and size
            likelihood = problem.get('likelihood', '')
            likelihood = self._get_abbreviation('likelihood', likelihood)
            line = likelihood
            size_min = problem.get('size_min', '')
            size_max = problem.get('size_max', '')
            if size_min and size_max:
                # Format size range, removing trailing .0
                size_min_fmt = size_min.rstrip('0').rstrip('.') if '.' in size_min else size_min
                size_max_fmt = size_max.rstrip('0').rstrip('.') if '.' in size_max else size_max
                size_str = f"{size_min_fmt}-{size_max_fmt}"
                line = f"{line}, Sz:{size_str}"
            if line:
                parts.append(line)

        return parts

    def _abbrev_danger_rating(self, rating: str) -> str:
        """Abbreviate danger rating to single letter."""
        return self._get_abbreviation('danger_rating', rating)

    def _abbrev_elevations(self, elevations: list) -> str:
        """Abbreviate and order elevation bands.

        Always returns elevations in order: ALP, TL, BTL.
        Logs error for unknown elevation values.

        Args:
            elevations: List of elevation strings

        Returns:
            Comma-separated abbreviated elevations in order
        """
        # Ordered mapping: keys are in display order (ALP, TL, BTL)
        ELEV_MAP = {
            'ALPINE': 'ALP',
            'TREELINE': 'TL',
            'BELOW TREELINE': 'BTL',
        }

        result = []
        elevations_upper = [e.upper() for e in elevations]

        for elev_key, abbrev in ELEV_MAP.items():
            if elev_key in elevations_upper:
                result.append(abbrev)

        for elevation in elevations_upper:
            if elevation not in ELEV_MAP.keys():
                logging.error(f"Avalanche API error - Unknown elevation value: {elevation}")

        return ','.join(result)

    def _abbrev_aspects(self, aspects: list) -> str:
        """Order aspects in clockwise order from N.

        Args:
            aspects: List of aspect strings (e.g., ['e', 'nw', 'n', 'ne'])

        Returns:
            Comma-separated aspects in clockwise order from N
        """
        # Clockwise order starting from N
        ASPECT_ORDER = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

        aspects_upper = [a.upper() for a in aspects]
        result = [a for a in ASPECT_ORDER if a in aspects_upper]

        return ','.join(result)

    def no_provider_msg(self) -> str:
        return 'Avalanche forecasts not available for this location.'

    def no_forecast_msg(self) -> str:
        return 'No avalanche forecast available for this location.'

    def outside_of_area_msg(self):
        return 'GPS coordinates outside of supported avalanche forecast area. No data available.'

    def broken_forecast_msg(self, reason: str) -> str:
        """Return error message when forecast data is malformed/missing.

        Args:
            reason: Type of forecast error ('date', 'data', etc.)

        Returns:
            Error message string
        """
        logging.error(f"Avalanche API error - {reason}: No forecast dates available")
        return 'TrekSafer ERROR: Unable to retrieve avalanche forecast data. Please try again later.'
