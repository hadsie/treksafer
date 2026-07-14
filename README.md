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

**Fire response example:**
```
AQI: 42

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
- Apple Maps and Google Maps share links
- inReach / ZOLEO share links, resolved to the device's location
- InReach automatic format: `fires (lat, lon)`

Coordinates you type take priority over the device location your messenger appends, so you can ask about somewhere you aren't.

### Look up a specific fire

Send **`fireid <number>`** to monitor a specific fire, e.g. `fireid K70597`. Include your coordinates and for the reply to includes the distance and direction from you to the fire.

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

Edit config.yaml for defaults (fire radius, data sources, etc.).

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
