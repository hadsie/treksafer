# TrekSafer test suite

This directory contains all unit and integration tests for the project. Tests are written using [`pytest`](https://docs.pytest.org/).

## Running the test suite

From the project root:

```bash
pytest
```

This will run all unit tests and skip the transport smoke tests.

To explicitly run smoke tests (e.g., end-to-end transport tests or integration with live services):

```bash
pytest -m smoke
```

Combine markers and test names as needed:

```bash
pytest -m "not smoke" -k test_format_distance
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
