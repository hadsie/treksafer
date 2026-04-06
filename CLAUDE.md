# TrekSafer

Backcountry safety notification system. Users send SMS from satellite messengers (inReach/Garmin) with GPS coordinates and receive nearby wildfire reports or avalanche forecasts.

## Running

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp dotenv.example .env.dev   # then fill in values
python -m app                # starts CLI transport on localhost:8888
```

Connect via `python scripts/cli_connect.py` for local testing.

Download fire perimeter data: `python scripts/downloads.py`

## Architecture

### Message flow

1. Transport receives SMS (SignalWire) or TCP message (CLI)
2. `helpers.parse_message()` extracts coords + filters
3. Auto-detects data type: avalanche if provider covers the location, otherwise fire
4. Routes to `handle_fire_request()` or `handle_avalanche_request()` in `messages.py`

### Key modules

- `app/config.py` -- Pydantic settings loaded from `config.yaml` + `.env.<ENV>` + env vars
- `app/fires.py` -- `FindFires` loads shapefiles, searches by radius, calls BC API for enrichment
- `app/filters.py` -- Generic status/size filtering
- `app/helpers.py` -- Coord parsing (decimal, hemisphere, map URLs), AQI, compass bearing
- `app/messages.py` -- Response formatting with auto-downsize for SMS (160 char limit)
- `app/transport/` -- Pluggable transports (CLI TCP, SignalWire SMS). Abstract base in `base.py`
- `app/avalanche/` -- Provider pattern: `base.py` ABC, implementations for Avalanche Canada (`avcan.py`), US NAC (`us_nac.py`), Quebec (`quebec.py`). `report.py` aggregates/formats

### Data sources

Fire perimeter shapefiles in `shapefiles/{BC,AB,CA,US}/`. Each source has field mappings and status maps in `config.yaml` under `data:`. BC fires are enriched via a REST API call (cached 4h).

Avalanche providers are configured in `config.yaml` under `avalanche.providers:` and selected dynamically based on location.

Boundary files in `boundaries/` determine which data sources are nearby.

### Caching

- `requests_cache` for BC fire API (SQLite in `cache/`, 4h TTL)
- Each avalanche provider has its own `CachedSession` (1h default)
- Shapefile loading is memoized via `@lru_cache`

## Dependencies

Python 3.11+. All direct dependencies are pinned in `requirements.txt`.
When updating dependencies, pin to exact versions (e.g. `requests==2.32.3`).
Run `pytest` after any dependency change to verify nothing breaks.

## Testing

```bash
pytest                           # unit tests (default, excludes smoke/live)
pytest -m smoke                  # end-to-end with running transports
pytest -m live                   # hits real APIs
```

Tests live in `tests/` mirroring the source tree. Test data (GeoJSON, JSON samples, compiled shapefiles) in `tests/data/` and `tests/shapefiles/`.

The `conftest.py` sets `TREKSAFER_ENV=test` and provides a `mock_bc_fire_api` fixture using the `responses` library.

## Configuration

Three-tier precedence: env vars > `.env.<ENV>` file > `config.yaml`.

All config placeholders use `${VAR:-default}` syntax. Pydantic validates everything in `app/config.py`.

Key env vars: `TREKSAFER_ENV`, `TREKSAFER_SW_ENABLED`, `TREKSAFER_SW_PROJECT`, `TREKSAFER_SW_TOKEN`, `TREKSAFER_SW_NUMBER`.

## Contributing

Branch names: `claude/` prefix for automated changes, `feature/` for new work, `fix/` for bugfixes.
PR descriptions should include what changed and why.
All PRs must pass `pytest` before merge.
