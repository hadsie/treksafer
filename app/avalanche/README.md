# Avalanche Provider Implementation Guide

This guide documents the complete process for adding a new avalanche forecast provider to TrekSafer.

## Overview

Avalanche providers fetch forecast data from external APIs and normalize it into a common format for display. Each provider must implement three abstract methods and return data in a specific normalized structure.

## Quick Start Checklist

- [ ] Add provider configuration to `config.yaml`
- [ ] Add term abbreviations to `data/avalanche_terms.yaml`
- [ ] Create provider class file in `app/avalanche/`
- [ ] Register provider in `app/avalanche/report.py`
- [ ] Implement three required abstract methods
- [ ] Ensure `get_forecast()` returns normalized dict format

## Step 1: Configuration Files

### 1.1 config.yaml

Add your provider under the `avalanche.providers` section:

```yaml
avalanche:
  providers:
    ProviderName:
      class: ProviderNameProvider
      api_url: https://api.example.com/endpoint
      cache_timeout: 3600
      language: en
```

**Key naming rules:**
- **ProviderName**: This is the provider key used in YAML term mappings
  - Example: `AvalancheCanada`, `NationalAvalancheCenter`
- **class**: Must match the class name in your provider file
  - Pattern: `{ProviderName}Provider`
  - Example: `AvalancheCanadaProvider`

**Available config fields:**
- `class`: (required) Python class name
- `api_url`: (required) API endpoint with optional `{placeholders}`
- `cache_timeout`: (required) Cache duration in seconds
- `language`: (optional) Language code for API requests

### 1.2 data/avalanche_terms.yaml

Add term abbreviations for your provider:

```yaml
ProviderName:
  problem_type:
    "Storm Slab": "StormSlb"
    "Wind Slab": "WindSlb"
    "Persistent Slab": "PrsistSlb"
    "Deep Persistent Slab": "DeepSlb"
    "Wet Slab": "WetSlb"
    "Cornices": "Cornice"
    "Loose Wet": "LooseWet"
    "Loose Dry": "LooseDry"

  likelihood:
    "unlikely": "UnLkly"
    "possible": "Poss"
    "likely": "Lkly"
    "very likely": "VLkly"
    "certain": "Certain"

default:
  danger_rating:
    "Extreme": "E"
    "High": "H"
    "Considerable": "C"
    "Moderate": "M"
    "Low": "L"
    "Early Season": "ES"
    "No Rating": "N/A"
```

**Important:**
- The top-level key (`ProviderName`) must match the provider key in `config.yaml`
- Keys are exact strings from the API response
- Values are the abbreviations used in SMS output
- Danger ratings are usually in the `default` section since they're standardized
- Add provider-specific terms only if they differ from existing providers

## Step 2: Create Provider File

### 2.1 File Naming

Create a new file: `app/avalanche/{providername}.py`

**Naming convention:**
- Lowercase
- Example: `canada.py`, `nationalcenter.py`

### 2.2 Provider Class Structure

```python
"""Provider Name implementation."""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from requests import RequestException

from .base import AvalancheProvider
from ..config import AvalancheProviderConfig


class ProviderNameProvider(AvalancheProvider):
    """Provider Name API provider."""

    def __init__(self, config: AvalancheProviderConfig):
        super().__init__(config)
        # Load geospatial data using base class helper
        # self.regions_gdf is automatically used by base class distance_from_region()
        self.regions_gdf = self._load_geodata(
            lambda: gpd.read_file('boundaries/provider_regions.shp.zip')
        )

    def get_forecast(self, coords: tuple) -> Optional[Dict[str, Any]]:
        """Get forecast from Provider API.

        Args:
            coords: (latitude, longitude) tuple

        Returns:
            Normalized forecast dict or None if unavailable
        """
        try:
            # Build API URL
            url = f"{self.api_base}?lat={coords[0]}&lon={coords[1]}"

            # Make cached request (inherited from base class)
            response = self._request(url)

            if response.status_code == 200:
                return self._parse_forecast(response.json(), coords)
            else:
                logging.warning(
                    f"Provider API returned status {response.status_code} "
                    f"for coords {coords}"
                )
        except RequestException as e:
            logging.warning(f"Network error: {e}")

        return None

    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside forecast coverage area.

        Args:
            coords: (latitude, longitude) tuple

        Returns:
            True if out of range, False if within coverage
        """
        # Implement provider-specific range check
        # e.g., check against shapefile, bounding box, or API
        pass

    # NOTE: distance_from_region() is implemented in base class
    # It uses self.regions_gdf automatically - no need to override
    # unless you have provider-specific distance logic

    def _parse_forecast(self, data: Dict, coords: tuple) -> Optional[Dict]:
        """Parse API response into normalized format.

        Args:
            data: Raw API response
            coords: Original coordinates

        Returns:
            Normalized forecast dict (see format below)
        """
        # Implement API response parsing
        # MUST return the normalized dict format (see Step 3)
        pass
```

## Step 3: Normalized Return Format

The `get_forecast()` method **MUST** return a dict with this exact structure:

```python
{
    'region': str,              # Region name for display
                                # e.g., "Spearhead", "Mount Washington"

    'date_issued': str,         # Date forecast was issued
                                # e.g., "2025-01-15", "Friday, January 15"

    'timezone': str,            # IANA timezone identifier
                                # e.g., "America/Vancouver", "America/Denver"

    'forecasts': {              # Dict keyed by day name (day of week)
        'Friday': {
            'alpine_rating': str,           # e.g., "Considerable", "3 - Considerable"
            'treeline_rating': str,         # e.g., "Moderate", "2 - Moderate"
            'below_treeline_rating': str    # e.g., "Low", "1 - Low"
        },
        'Saturday': {
            'alpine_rating': str,
            'treeline_rating': str,
            'below_treeline_rating': str
        },
        # ... more days as available
    },

    'problems': [               # List of avalanche problems
        {
            'type': str,        # Problem type from API
                                # e.g., "Storm slab", "Wind Slab"
                                # Must match keys in avalanche_terms.yaml

            'elevations': list, # Elevation bands affected
                                # e.g., ["Alpine", "Treeline"]
                                # Standard values: "Alpine", "Treeline", "Below Treeline"

            'aspects': list,    # Slope aspects affected
                                # e.g., ["N", "NE", "E", "SE"]
                                # Standard values: N, NE, E, SE, S, SW, W, NW

            'likelihood': str,  # Likelihood from API
                                # e.g., "likely", "possible_unlikely"
                                # Must match keys in avalanche_terms.yaml

            'size_min': str,    # Minimum avalanche size
                                # e.g., "1.0", "1.5"

            'size_max': str     # Maximum avalanche size
                                # e.g., "2.5", "3.0"
        },
        # ... more problems
    ],

    'url': str                  # Optional: URL to full forecast
                                # e.g., "https://avalanche.ca/forecasts/..."
}
```

### Important Notes on Return Format

1. **Day Names**: Use full day names (Monday, Tuesday, etc.) as keys in `forecasts` dict
2. **Danger Ratings**: Return raw API values - abbreviation happens automatically via `avalanche_terms.yaml`
3. **Problem Types**: Return exact API strings - they'll be matched in `avalanche_terms.yaml`
4. **Elevations**: Use standardized names: "Alpine", "Treeline", "Below Treeline"
5. **Aspects**: Use uppercase cardinal directions: N, NE, E, SE, S, SW, W, NW
6. **Timezone**: Use IANA timezone identifier (critical for date filtering)

## Step 4: Register Provider

Edit `app/avalanche/report.py` and add your provider to the `_get_provider_class()` function:

```python
def _get_provider_class(class_name: str):
    """Dynamically get avalanche provider class by name."""
    # Import here to avoid circular dependency
    from .canada import AvalancheCanadaProvider
    from .quebec import AvalancheQuebecProvider
    from .newprovider import NewProviderProvider  # <-- Add import

    providers = {
        'AvalancheCanadaProvider': AvalancheCanadaProvider,
        'AvalancheQuebecProvider': AvalancheQuebecProvider,
        'NewProviderProvider': NewProviderProvider,  # <-- Add to dict
    }

    provider_class = providers.get(class_name)
    if not provider_class:
        raise ValueError(f"Unknown avalanche provider class: {class_name}")
    return provider_class
```

## Step 5: Implementation Guidelines

### 5.1 HTTP Requests

Use the inherited `_request()` method for all API calls:

```python
response = self._request(url)
```

**Benefits:**
- Automatic caching (respects `cache_timeout` from config)
- Consistent timeout handling
- Error logging

### 5.2 Region/Distance Checking

The base class provides `distance_from_region()` implementation that works automatically if you set `self.regions_gdf`. You only need to implement `out_of_range()`.

**Option 1: Shapefile-based (recommended)**
```python
import geopandas as gpd

def __init__(self, config):
    super().__init__(config)
    # Load geodata using base class helper (handles errors automatically)
    self.regions_gdf = self._load_geodata(
        lambda: gpd.read_file('boundaries/provider_regions.shp.zip')
    )

def out_of_range(self, coords: tuple) -> bool:
    """Check if coordinates are outside coverage area."""
    # Use your own region lookup method
    return self._get_region(coords) is None

def _get_region(self, coords: tuple) -> Optional[str]:
    """Get region name from coordinates (helper method)."""
    if self.regions_gdf is None:
        return None

    from shapely.geometry import Point
    point = Point(coords[1], coords[0])  # lon, lat

    matches = self.regions_gdf[self.regions_gdf.contains(point)]
    if not matches.empty:
        return matches.iloc[0]['region_name_field']

    return None

# distance_from_region() is inherited from base class
# It automatically:
#  - Checks exact match against self.regions_gdf
#  - Calculates distance to nearest region
#  - Applies buffer limit from config
#  - Returns None (exact match), float (km), or inf (out of range)
```

**Helper methods available from base class:**
```python
# Load geodata with consistent error handling
self.regions_gdf = self._load_geodata(loader_fn)

# Calculate distances to all regions (used by distance_from_region)
gdf_with_distances = self._calculate_distances(coords)
```

**Option 2: Bounding box**
```python
def out_of_range(self, coords: tuple) -> bool:
    lat, lon = coords
    return not (
        min_lat <= lat <= max_lat and
        min_lon <= lon <= max_lon
    )
```

**Option 3: API-based**
```python
def out_of_range(self, coords: tuple) -> bool:
    try:
        forecast = self.get_forecast(coords)
        return forecast is None
    except:
        return True
```

### 5.3 Error Handling

Follow these patterns:

```python
# API errors
try:
    response = self._request(url)
    if response.status_code == 200:
        return self._parse_forecast(response.json())
    else:
        logging.warning(f"API returned status {response.status_code}")
        return None
except RequestException as e:
    logging.warning(f"Network error: {e}")
    return None

# Parsing errors
if not data or 'required_field' not in data:
    logging.warning(f"Invalid API response for coords {coords}")
    return None
```

## Step 6: Testing

Create test fixtures in `tests/conftest.py`:

```python
@pytest.fixture
def newprovider_config():
    return AvalancheProviderConfig(
        class_name='NewProviderProvider',
        api_url='https://api.example.com/forecast',
        cache_timeout=3600,
        language='en'
    )

@pytest.fixture
def newprovider_sample_response():
    # Return sample API response dict
    return {...}
```

Add tests in `tests/test_avalanche.py`:

```python
def test_newprovider_parsing(newprovider_config, newprovider_sample_response):
    provider = NewProviderProvider(newprovider_config)
    result = provider._parse_forecast(newprovider_sample_response, (49.0, -123.0))

    assert result is not None
    assert 'region' in result
    assert 'forecasts' in result
    assert 'Friday' in result['forecasts']
    # ... more assertions
```

## Common Pitfalls

1. **Mismatched provider names**
   - `config.yaml` key must match `avalanche_terms.yaml` top-level key
   - Class name must be `{ProviderName}Provider`

2. **Wrong return format**
   - Day names must be full weekday names (not dates)
   - Danger ratings must be strings (not numbers)
   - All fields must be present even if empty

3. **Timezone issues**
   - Always return IANA timezone identifier
   - Used for filtering "tomorrow" forecasts

4. **Term mapping failures**
   - API strings must exactly match keys in `avalanche_terms.yaml`
   - Check logs for "Unknown {term_type}: {value}" errors

5. **Import errors in report.py**
   - Remember to add both import and dict entry to `_get_provider_class()`

## Example: Minimal Provider

```python
"""Minimal provider example."""
import geopandas as gpd
from typing import Optional, Dict, Any
from shapely.geometry import Point
from .base import AvalancheProvider

class MinimalProvider(AvalancheProvider):
    def __init__(self, config):
        super().__init__(config)
        # Load regions using base class helper
        self.regions_gdf = self._load_geodata(
            lambda: gpd.read_file('boundaries/provider_regions.shp.zip')
        )

    def get_forecast(self, coords: tuple) -> Optional[Dict[str, Any]]:
        url = f"{self.api_base}?lat={coords[0]}&lon={coords[1]}"
        response = self._request(url)

        if response.status_code != 200:
            return None

        data = response.json()

        return {
            'region': data['region_name'],
            'date_issued': data['issued_date'],
            'timezone': 'America/Vancouver',
            'forecasts': {
                'Friday': {
                    'alpine_rating': data['ratings']['alpine'],
                    'treeline_rating': data['ratings']['treeline'],
                    'below_treeline_rating': data['ratings']['below_treeline']
                }
            },
            'problems': [],
            'url': data.get('forecast_url', '')
        }

    def out_of_range(self, coords: tuple) -> bool:
        """Check if coordinates are outside coverage."""
        if self.regions_gdf is None:
            return True

        point = Point(coords[1], coords[0])  # lon, lat
        return not self.regions_gdf.contains(point).any()

    # distance_from_region() inherited from base class
```

## Reference

- See `app/avalanche/canada.py` for a complete implementation example
- See `app/avalanche/base.py` for base class and abstract methods
- See `app/avalanche/report.py` for how providers are selected and used
