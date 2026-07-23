# TrekSafer

TrekSafer keeps backcountry travelers informed about nearby wildfires and avalanche conditions.
Send the service a message from your satellite messenger with your GPS coordinates and get a summary of what's happening around you.

---

## How to use as a backpacker

See https://treksafer.com for more information.

1. Add TrekSafer’s number to your contacts.
1. From your inReach / satellite SMS device, open a new message to that number.
1. Type **`fires`** or **`avalanche`**, or just send any text. TrekSafer will auto-detect based on your location whether to return fire or avalanche data.
1. Ensure your device is set to **include location** with outgoing messages (most inReach presets add “(lat, lon)” automatically).
1. Hit *Send*. Within a few seconds you’ll get a reply.

**Note:** Reports with several fires can arrive as several messages, typically one fire per SMS. This is to prevent confusion with fires spanning messages and out-of-order SMSes.

**Fire response example:**
```
AQI: 42
Wind: 20km/h from SW, gusts 40 rising to 65

Fire: Lower Young Creek (K50911)
Location: 7 km NE of Lillooet
25km E
Size: 66 ha (+20 since 26h ago)
Status: Out of Control

Fire: K50920 (NEW)
Location: Fountain Valley
31km SE
Size: 4 ha
Status: Out of Control
```

Fires that grew or shrank recently show the change next to their size, and fires that where discovered since the last full data sync are labelled `(NEW)`.

Wind direction is where the wind blows *from* (a SW wind pushes fire toward the NE). The `rising to` figure is the strongest gust forecast in the next 12 hours, shown only when it's well above current gusts. Air quality and wind data by [Open-Meteo.com](https://open-meteo.com) (CC-BY 4.0).

**Avalanche response example:**
```
Sea to Sky
Mon: ALP:C TL:M BTL:L

Storm Slabs
ALP,TL Slp:N,NE,E
Lkly, Sz:1-2.5

Wind Slabs
ALP Slp:N,NE
Poss, Sz:1-2
```

Elevation bands are:
 - ALP (alpine)
 - TL (treeline)
 - BTL (below treeline)

Danger ratings are abbreviated (C = Considerable, M = Moderate, L = Low) as are problem likelihood and destructive size.

By default, fires within 50km of your GPS location are returned. If you’re in an area covered by an avalanche forecast provider (Avalanche Canada, Avalanche Quebec, US National Avalanche Center), you’ll get the current forecast instead.

---

## Filter Options

### Fire Filters

**Status:**
- **`active`** - Only active/out of control fires
- **`all`** - All fires including extinguished ones
- **Default** - Active, managed, and controlled fires (excludes extinguished)

**Distance:**
- **`25km`** or **`10mi`** - Custom search radius (max 150km)
- **Default** - 50km radius

### Avalanche Filters

- **`current`** - Today’s forecast only
- **`tomorrow`** - Tomorrow’s forecast only
- **`all`** - All available forecast days
- **Default** - Full current forecast

### Coordinate Formats Supported
- Decimal degrees: `(49.123, -123.456)`
- Hemisphere notation: `50.58225° N, 122.09114° W`
- Degrees minutes seconds: `49°12'35.0"N 121°04'45.8"W` (Google Maps copy-paste, straight or curly quote marks) or bare `50 34 56 N, 122 05 28 W`
- Degrees decimal minutes: `49°12.467' N, 123°6.317' W` (Garmin's on-screen format) or bare `50 34.935 N, 122 05.468 W`
- Labelled decimals: `Lat 50.123456 Lon -89.654321` (inReach email format; `latitude`/`longitude` and `long` also work)
- Apple Maps and Google Maps share links
- inReach / ZOLEO share links, resolved to the device's location
- InReach automatic format: `fires (lat, lon)`

Coordinates you type take priority over the device location your messenger appends, so you can ask about somewhere you aren't.

### Look up a specific fire

Send **`fireid <number>`** to monitor a specific fire, e.g. `fireid K70597`. Include your coordinates and for the reply to includes the distance and direction from you to the fire.

### Service keywords

- **`help`** or **`info`** - What the service is and how to use it.
- **`usage`** or **`examples`** - Advanced usage: filters, fire tracking, avalanche options, coordinate formats.
- **`stop`** / **`start`** - Opt out of, or back in to, SMS replies (SMS only). An opted-out number receives nothing until it opts back in.

Keywords only apply when they are the entire message, so they never override a real request, except `usage`, which will always return usage information if it's the first word in the message.

### Examples
```
Basic: fires
With status filter: fires active
With distance: fires 25km
Combined filters: fires active 10mi
With coordinates: (49.2827, -123.1207) active 25km
Look up a fire: fireid K70597
Look up with distance: fireid K70597 (49.2, -123.1)
Avalanche: avalanche
Avalanche tomorrow: avalanche tomorrow
```

---

## Todo

1. Data sources from outside of the US/Canada, and more fine-grained data support for specific US states that offer it.
2. Detect message send failures and auto-retry.

---

## Running TrekSafer locally

### Clone and set up

```bash
git clone https://github.com/hadsie/treksafer.git
cd treksafer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Edit config.yaml for defaults (fire radius, data sources, etc.). Tunable
message thresholds (e.g. minimum AQI level to include in the message) live
in thresholds.yaml.

Create an env file for your environment (eg. `.env.dev`):

```
TREKSAFER_ENV=dev

# SignalWire (only needed in prod / integration tests)
TREKSAFER_SW_ENABLED=false
TREKSAFER_SW_PROJECT=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
TREKSAFER_SW_TOKEN=PTXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
TREKSAFER_SW_NUMBER=+15555551234
```

### Running the app

#### dev mode (CLI transport on localhost:8888)
```bash
python -m app
python scripts/cli_connect.py  "Test message (lat,lon)" # in another terminal, to send test messages
```

#### production (SignalWire + CLI)
```bash
TREKSAFER_ENV=prod python -m app
```

### Fire database

Fire data is fetched live from the sources' ArcGIS layers at request time, with a 15-minute cache. Every fetch is also recorded to a local  database (`data/fires.db`), which provides the history used in the  size-change reporting and also serves as the fallback when a source's API is unavailable.

Run `python scripts/downloads.py` to refresh the database from every source (production runs this daily via cron at 6:30am Pacific time).

### Adding a fire data source

Fire sources are defined in `config.yaml` under `data:`. The idea is that no code changes will be needed to add a new source, but unless a new source has a very similar structure to one of the existing ones, there will likely need to be small code changes made still.

Each source is a pair of realtime ArcGIS layers: incident points (one row per fire, carrying its attributes) and mapped perimeter polygons. The layers must answer standard `/query` requests (`f=geojson`, geometry filters, `resultOffset` pagination); test with `curl` before configuring.

1. **Pick the location code.** It must match a polygon the boundary files know: an ISO country code (`boundaries/countries.zip`) or a Canadian province postal code (`boundaries/canada_provinces.zip`). Quote codes YAML would read as booleans (`"ON"`). If the region is already covered by a broader source (like the CA national feed), exclude it there via that source's `points_where`/`perimeters_where`.
2. **Choose the join.** `field` (default) when the perimeters layer carries the fire number (`join_field` on points, `perimeter_fire_field` on perimeters); `spatial` when it doesn't, which assigns polygons by location and disables perimeter/edge reporting on `fireid` lookups.
3. **Map the fields.** `mapping` translates the layer's column names to response fields: `Fire` (required, the displayed identifier users look up), `Name`, `Location`, `Type`, `Size` (hectares), `Status`, `Discovered`. `transforms` converts values by mapping key: `epoch_ms` and `iso_datetime` for dates, `acres_to_hectares` for size, `wfigs_status` to derive status from percent contained.
4. **Classify statuses.** `status_map` sorts the feed's raw status values into `active` / `managed` / `controlled` / `out`. Unmapped values are logged and shown as active. Feeds that publish status codes display as full words via the table in `data/fire_terms.yaml`.
5. **Give fires a stable identity.** `key_fields` are the points columns whose combined values identify a fire across the season in the database. If the agency recycles fire numbers annually but publishes no year column, name a `year_field`: a synthesized column holding the fetch's UTC year, usable in `key_fields`.
6. **Wire the kill switch.** Follow the `enabled: ${TREKSAFER_XX_REALTIME:-true}` convention and add the variable to `dotenv.example`.

All options on a source's `realtime:` block:

| Option | Required | Purpose |
|---|---|---|
| `points_url` | yes | Points layer `/query` endpoint; a list when the feed is split across layers (ex: Ontario's New/Active/Out), concatenated and deduplicated |
| `perimeters_url` | yes | Perimeter polygons `/query` endpoint |
| `mapping` | yes | Layer columns to response fields; `Fire` is mandatory |
| `status_map` | yes | Raw status values to `active`/`managed`/`controlled`/`out` |
| `key_fields` | yes | Columns forming the fire's database identity |
| `join` | no | `field` (default) or `spatial` |
| `join_field` / `perimeter_fire_field` | field join | Fire-number columns on each layer (they may be named differently) |
| `transforms` | no | Value conversions by mapping key (`epoch_ms`, `iso_datetime`, `acres_to_hectares`, `wfigs_status`) |
| `year_field` | no | Synthesized fetch-year column for annually recycled fire numbers |
| `updated_field` | no | Per-fire update timestamp column |
| `timezone` | no | IANA zone for parsing zoneless local timestamp strings (AB) |
| `points_where` / `perimeters_where` | no | Attribute filters, e.g. excluding regions served by a dedicated source |
| `cache_timeout` | no | Realtime response cache in seconds (default 900) |
| `layer_stale_hours` | no | Monitor alert threshold for a frozen upstream layer (default 24) | `null` disables the check for servers that don't publish lastEditDate |
| `enrichment` | no | Per-fire API (`url` template over `key_fields` placeholders + `updated_field`) for data the layers lack, used on `fireid` lookups (BC's update time) |
| `enabled` | no | Realtime toggle; when false the source serves from the database |

For tests, add a fixture GeoJSON (`tests/data/<LOC>_perimeters.geojson`, fabricated fires with the real column names), a normalizer branch in `tests/conftest.py`, the location in its fixture-build loop and realtime-off env vars, and a live-marked endpoint test in `tests/test_fire_sources.py`. Update the expected source sets in the health tests.
