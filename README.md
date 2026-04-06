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
Location: Lower Young Creek 25km E
Size: 66 ha
Status: Out of Control
```

**Avalanche response example:**
```
Avalanche Forecast
Rating: Considerable (Alp), Moderate (Tln), Low (Btl)
Problems: Storm Slabs (N-E-SE, Alp-Tln), Wind Slabs (N-NE-E, Alp)
Valid: Apr 6-7
```

By default, all active fires within 50km of your GPS location are returned. If you’re in an area covered by an avalanche forecast provider (Avalanche Canada, US National Avalanche Center), you’ll get the current forecast instead.

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
- InReach automatic format: `fires (lat, lon)`

### Examples
```
Basic: fires
With status filter: fires active
With distance: fires 25km
Combined filters: fires active 10mi
With coordinates: (49.2827, -123.1207) active 25km
Avalanche: avalanche
Avalanche tomorrow: avalanche tomorrow
```

---

## Todo

1. Wildfire data is pulled in manually/periodically, not at the time of the request. Pulling live data at request time is a priority for fires of note.
2. Data sources from outside of the US/Canada, and more fine-grained data support for specific US states that offer it.
3. Detect message send failures and auto-retry.

---

## Running TrekSafer locally

### Clone and set up

```bash
git clone https://github.com/your-org/treksafer.git
cd treksafer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Edit config.yaml for defaults (fire radius, shapefile paths, etc.).

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

#### dev mode (CLI + cached shapefiles)
python -m app

#### production (SignalWire + CLI)
TREKSAFER_ENV=prod treksafer

### Downloading shapefile data

From within the venv environment run `python scripts/downloads.py`. This will download public fire perimeter data from BC, AB, CA, and US sources. Still on the todo list to get data downloading in realtime.
