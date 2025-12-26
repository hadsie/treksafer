# TrekSafer test suite

This directory contains all unit and integration tests for the project. Tests are written using [`pytest`](https://docs.pytest.org/).

## Running the test suite

### Unit Tests (Default)

From the project root:

```bash
pytest
```

This runs all unit tests and automatically skips smoke tests. Unit tests use mocks and don't require running services.

### Smoke Tests

Smoke tests verify the transport layer works end-to-end with real configuration. They should be run after deployment to staging/production.

**Requirements:**
- CLI smoke test: App must be running (`python -m app`)
- SignalWire smoke test: Valid credentials in `config.yaml`

**Run smoke tests:**
```bash
# Run with verbose output and show print statements
pytest -m smoke -v -s

# Run in specific environment
TREKSAFER_ENV=prod pytest -m smoke -v -s
```

**What smoke tests do:**
- **CLI Transport:** Connects to running CLI server, sends test message, verifies response
- **SignalWire Transport:** Verifies initialization with real config, prints manual test instructions

**Expected behavior:**
- Tests will **skip** if transport is not enabled in config
- Tests will **skip** if transport service is not running
- Tests will **pass** if transport works correctly
- Tests will **fail** if transport errors occur

### Other pytest commands

Combine markers and test names as needed:

```bash
pytest -m "not smoke" -k test_format_distance
pytest -k test_avalanche
```

Run a single test file with verbose output:

```bash
pytest tests/test_coords.py -v
```


# Wildfire test data

We're using customized test data to target specific use-cases rather than the 3rd party shapefiles. This is for validating fire filtering, distance calculations, status filtering, and cross-border scenarios in the TrekSafer application.

The GeoJSON test data is in tests/data and are converted to shapefile ZIP archives (matching the production data format) using the `compile_test_data.py` script.

## Status Codes & Color Scheme

The status codes in the GeoJSON match the format defined in the upstream shapefiles, we have one geojson file to mirror each upstream data source. To view the perimeters add them to a tool like https://geojson.io.

### Polygon status color mapping

- Active - Red `#FF0000` - Fire is out of control
- Managed - Orange `#FFA500` - Fire is being held
- Controlled - Yellow `#FFFF00` - Fire is under control
- Out - Green `#00FF00` - Fire is extinguished
- Unkonwn - Gray `#888888` - Unknown status (edge case testing)


## Generating Shapefile Archives

To convert the GeoJSON source files to shapefile ZIP archives:

```bash
# Generate with today's date
python tests/compile_test_data.py

# Generate with specific date
python tests/compile_test_data.py --date 20251225

# Clean old shapefiles and regenerate
python tests/compile_test_data.py --clean
```

### Output

The script creates shapefile zip archives in `tests/shapefiles/{location}/` using the naming conventions from `config.yaml`:

### Modifying Existing Fires

1. Edit the GeoJSON files directly in `tests/data/`
2. Update fire properties (coordinates, size, status, etc.)
3. Regenerate shapefiles: `python tests/compile_test_data.py --clean`

### Adding New Fires

1. Add a new feature to the appropriate GeoJSON file
2. Include all required fields:
   - **BC**: `FIRE_NUM`, `FIRE_YEAR`, `NAME`, `FIRE_SZ_HA`, `STATUS`, `DESC`
   - **AB**: `FIRE_NUMBE`, `AREA`, `ALIAS`, `COMPLEX`, `STATUS`, `DESC`
   - **US**: `attr_Fir_6`, `attr_Inc_2`, `attr_Inc_4`, `attr_Incid`, `attr_Fir_2`, `DESC`
3. Add color properties for visualization:
   - `fill`: Color code based on status
   - `stroke`: Darker border color
   - `fill-opacity`: 0.6 for semi-transparency
4. Add geometry (polygon coordinates)
5. Regenerate shapefiles: `python tests/compile_test_data.py --clean`

# Avalanche test data

We use mocked API responses to test avalanche forecast parsing and provider selection. The test data is a copy of real API responses from Avalanche Canada and Avalanche Quebec.

## Test Data Files

 - `avcan_Brandywine-Garibaldi-Homathko-Spearhead-Tantalus_sample.json`: Simulates the Avalanche Canada API response structure for the Sea to Sky region in Dec 2025.
 - `avalanche_quebec_sample.json`: Simulates the Avalanche Quebec API response structure for the Chic-Chocs region.
