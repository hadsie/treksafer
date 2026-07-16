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

Refresh the fire database: `python scripts/downloads.py` (runs daily via cron in production)

## Operator monitoring

The message `health` on any transport returns a data-freshness summary; the exact
lowercase form over the CLI port returns JSON. `scripts/monitor.py` (cron,
every 15-30 min) probes it and alerts via ntfy + email (`app/notify.py`,
configured under `monitoring:` in config.yaml) on app-down, stale fetches,
frozen upstream layers (ArcGIS metadata lastEditDate), and new ERROR log
lines; alerts fire on state changes only. `scripts/digest.py` (cron, daily)
emails a summary of requests whose coordinates could not be parsed, scraped
from sms.log. Both scripts ping healthchecks.io as a dead-man's switch
(monitor directly, downloads.py after a successful refresh). Responses served
from the database because a realtime fetch failed carry a "Data from <time>"
marker.

## Architecture

### Message flow

1. Transport receives SMS (SignalWire) or TCP message (CLI)
2. `helpers.parse_message()` extracts coords + filters
3. Auto-detects data type: avalanche if provider covers the location, otherwise fire
4. Routes to `handle_fire_request()` or `handle_avalanche_request()` in `messages.py`

A `fireid <id>` message carries a `fire_id` lookup (the single token after the keyword) instead of (or alongside) coordinates. It outranks data-type routing: `handle_message` resolves it via `fires.FireLookup` -- a single fire matched exactly across all sources, database first, with one targeted live re-query only when the stored match is stale -- and a miss reports not-found, never a radius search.

### Key modules

- `app/config.py` -- Pydantic settings loaded from `config.yaml` + `.env.<ENV>` + env vars
- `app/fires/` -- Wildfire package: `find.py` (FindFires search + normalization), `sources.py` (realtime fetching and point/perimeter merging), `db.py` (SQLite fire database: snapshot history + API-outage fallback)
- `app/arcgis.py` -- ArcGIS FeatureServer transport client
- `app/filters.py` -- Generic status/size filtering
- `app/helpers.py` -- Coord parsing (decimal, hemisphere, map URLs), AQI, compass bearing
- `app/messages.py` -- Response formatting with auto-downsize for SMS (160 char limit)
- `app/transport/` -- Pluggable transports (CLI TCP, SignalWire SMS). Abstract base in `base.py`
- `app/avalanche/` -- Provider pattern: `base.py` ABC, implementations for Avalanche Canada (`avcan.py`), US NAC (`us_nac.py`), Quebec (`quebec.py`). `report.py` aggregates/formats

### Data sources

All five fire sources (BC, AB, ON, CA, US) are pairs of realtime ArcGIS layers (incident points + perimeters), configured with field mappings and status maps in `config.yaml` under `data:`. BC/AB/ON/US join their layers on a fire-number field; CA's national hotspot perimeters carry no fire ID, so points join spatially, with fires that have no perimeter getting a circle of their reported size. ON's points are split across three status layers (New/Active/Out), listed together under `points_url` and concatenated, and its database key includes a synthesized season year (`year_field`) because Ontario fire numbers recycle annually and the layers carry no year field.

Every successful fetch is recorded to the fire database (`data/fires.db`): fire identities plus a snapshot history gated on the source's own update signal (per-fire timestamp where published, field comparison otherwise). When an API is unavailable, the source serves from the database at any age (logged); a source with no stored data returns "data unavailable", never "no fires". Set `TREKSAFER_{BC,AB,ON,CA,US}_REALTIME=false` to disable realtime per source.

Avalanche providers are configured in `config.yaml` under `avalanche.providers:` and selected dynamically based on location.

Boundary files in `boundaries/` determine which data sources are nearby.

### Caching

- `requests_cache` for the realtime ArcGIS fire queries (SQLite in `cache/`, 15m TTL); cache misses are what trigger database snapshot writes
- Each avalanche provider has its own `CachedSession` (1h default)

## Dependencies

Python 3.11+. Direct dependencies are listed in `requirements.txt` using a
floor-plus-major-cap convention (e.g. `pandas>=2.2,<3`), which allows minor and
patch updates while guarding against breaking major bumps.
Run `pytest` after any dependency change to verify nothing breaks.

## Testing

```bash
pytest                           # unit tests (default, excludes smoke/live)
pytest -m smoke                  # end-to-end with running transports
pytest -m live                   # hits real APIs
```

Tests live in `tests/` mirroring the source tree. Test data (GeoJSON, JSON samples) in `tests/data/`.

The `conftest.py` sets `TREKSAFER_ENV=test`, disables realtime, and builds a fixture fire database from the GeoJSONs in `tests/data/`. HTTP is mocked with the `responses` library.

## Configuration

Three-tier precedence: env vars > `.env.<ENV>` file > `config.yaml`.

All config placeholders use `${VAR:-default}` syntax. Pydantic validates everything in `app/config.py`.

Key env vars: `TREKSAFER_ENV`, `TREKSAFER_SW_ENABLED`, `TREKSAFER_SW_PROJECT`, `TREKSAFER_SW_TOKEN`, `TREKSAFER_SW_NUMBER`, `TREKSAFER_SW_CONTEXT`.

## Contributing

Branch names: `claude/` prefix for automated changes, `feature/` for new work, `fix/` for bugfixes.
PR descriptions should include what changed and why.
All PRs must pass `pytest` before merge.
