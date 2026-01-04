"""National Avalanche Center (US) provider implementation."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import geopandas as gpd
import pytz
from requests import RequestException
from shapely.geometry import Point

from .base import AvalancheProvider
from ..config import AvalancheProviderConfig, get_config
from ..helpers import coords_to_point_meters


# Danger level mapping (numeric to string)
DANGER_LEVELS = {
    -1: 'No Rating',
    0: 'No Rating',
    1: 'Low',
    2: 'Moderate',
    3: 'Considerable',
    4: 'High',
    5: 'Extreme'
}

# Aspect mapping (text to abbreviation)
ASPECT_MAP = {
    'north': 'N',
    'northeast': 'NE',
    'east': 'E',
    'southeast': 'SE',
    'south': 'S',
    'southwest': 'SW',
    'west': 'W',
    'northwest': 'NW'
}

# Elevation mapping
ELEVATION_MAP = {
    'upper': 'Alpine',
    'middle': 'Treeline',
    'lower': 'Below Treeline'
}


class NationalAvalancheProvider(AvalancheProvider):
    """National Avalanche Center API provider."""

    def __init__(self, config: AvalancheProviderConfig):
        super().__init__(config)
        self.regions_gdf = self._load_geodata(self._load_zones)

    def _load_zones(self):
        """Load zone polygons from GeoJSON file.

        Manually parses GeoJSON to preserve feature-level IDs as zone_id property.
        """
        with open('boundaries/us_nac_boundaries.geojson') as f:
            data = json.load(f)

        # Inject feature-level ID into properties
        for feature in data['features']:
            feature['properties']['zone_id'] = feature['id']

        gdf = gpd.GeoDataFrame.from_features(data['features'])
        gdf.set_crs(epsg=4326, inplace=True)
        return gdf

    def _find_zone(self, coords: tuple) -> Optional[Dict[str, Any]]:
        """Find zone containing coordinates.

        Args:
            coords: (latitude, longitude)

        Returns:
            Dict with zone properties (id, center_id, timezone, name) or None
        """
        if self.regions_gdf is None:
            return None

        point = Point(coords[1], coords[0])  # lon, lat
        matches = self.regions_gdf[self.regions_gdf.contains(point)]

        if matches.empty:
            return None

        # Return first match
        zone = matches.iloc[0]
        return {
            'id': int(zone['zone_id']),
            'center_id': zone['center_id'],
            'timezone': zone['timezone'],
            'name': zone['name']
        }

    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside NAC coverage area."""
        return self._find_zone(coords) is None

    def get_forecast(self, coords: tuple) -> Optional[Dict[str, Any]]:
        """Get forecast from NAC API."""
        zone_info = self._find_zone(coords)
        if not zone_info:
            logging.warning(f"No NAC zone found for coords {coords}")
            return None

        try:
            # Build API URL with center_id and zone_id
            url = self.api_base.format(
                center=zone_info['center_id'],
                zone=zone_info['id']
            )
            response = self._request(url)

            if response.status_code == 200:
                result = self._parse_forecast(response.json(), zone_info)
                if result is None:
                    logging.warning(f"Invalid or empty JSON response from NAC API for coords {coords}")
                return result
            else:
                logging.warning(f"NAC API returned status {response.status_code} for coords {coords}")

        except RequestException as e:
            logging.warning(f"Network error checking NAC avalanche data: {e}")

        return None

    def _parse_forecast(self, data: Dict, zone_info: Dict) -> Optional[Dict]:
        """Parse NAC API response into normalized format.

        Args:
            data: API response data
            zone_info: Zone information from _find_zone()

        Returns:
            Normalized forecast dict
        """
        if not data or 'danger' not in data:
            return None

        # Extract timezone from zone info
        timezone = zone_info.get('timezone', 'America/Denver')

        # Parse published_time to get day of week for "current"
        published_time = data.get('published_time', '')
        try:
            tz = pytz.timezone(timezone)
            pub_dt = datetime.fromisoformat(published_time.replace('Z', '+00:00'))
            pub_dt_local = pub_dt.astimezone(tz)
            current_day = pub_dt_local.strftime('%A')

            # Tomorrow is next day
            from datetime import timedelta
            tomorrow_dt = pub_dt_local + timedelta(days=1)
            tomorrow_day = tomorrow_dt.strftime('%A')
        except (ValueError, TypeError) as e:
            logging.warning(f"Failed to parse published_time: {e}")
            # Fallback to current time
            current_day = datetime.now(pytz.timezone(timezone)).strftime('%A')
            tomorrow_day = (datetime.now(pytz.timezone(timezone)) + timedelta(days=1)).strftime('%A')

        # Parse danger ratings
        forecasts_by_date = {}
        for danger_entry in data.get('danger', []):
            valid_day = danger_entry.get('valid_day')

            # Map valid_day to actual day name
            if valid_day == 'current':
                day_name = current_day
            elif valid_day == 'tomorrow':
                day_name = tomorrow_day
            else:
                logging.warning(f"Avalanche API: Unknown NAC valid_day: {valid_day}")
                continue

            # Convert numeric ratings to strings
            upper = DANGER_LEVELS.get(danger_entry.get('upper', -1), 'No Rating')
            middle = DANGER_LEVELS.get(danger_entry.get('middle', -1), 'No Rating')
            lower = DANGER_LEVELS.get(danger_entry.get('lower', -1), 'No Rating')

            forecasts_by_date[day_name] = {
                'alpine_rating': upper,
                'treeline_rating': middle,
                'below_treeline_rating': lower
            }

        # Parse avalanche problems
        problems = []
        for problem in data.get('forecast_avalanche_problems', []):
            prob_type = problem.get('name', 'Unknown')

            # Parse location into elevations and aspects
            problem_locations = problem.get('location', [])
            elevations = set()
            aspects = set()

            # Problem locations are of the form like "southwest upper" or "west lower".
            for loc in problem_locations:
                try:
                    slope, elevation = loc.lower().rsplit(' ', 1)
                    elevations.add(ELEVATION_MAP[elevation])
                    aspects.add(ASPECT_MAP[slope])
                except (ValueError, KeyError) as e:
                    logging.warning(f"Avalanche API: Invalid NAC problem location '{loc}': {e}")

            # Extract size (min/max from array)
            sizes = problem.get('size', [])
            size_min = sizes[0] if len(sizes) > 0 else ''
            size_max = sizes[-1] if len(sizes) > 0 else size_min

            problems.append({
                'type': prob_type,
                'elevations': sorted(elevations),
                'aspects': sorted(aspects),
                'likelihood': problem.get('likelihood', ''),
                'size_min': str(size_min),
                'size_max': str(size_max)
            })

        # Get forecast URL
        forecast_zones = data.get('forecast_zone', [{}])
        url = forecast_zones[0].get('url', '')

        return {
            'region': zone_info.get('name', 'Unknown'),
            'date_issued': published_time,
            'timezone': timezone,
            'forecasts': forecasts_by_date,
            'problems': problems,
            'url': url
        }
