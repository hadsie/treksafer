# TrekSafer

TrekSafer keeps backcountry travelers in the loop about nearby wild-fires.
Send the service a message from your satellite messenger with your GPS coordinates and get a summary of any active fires around you.

---

## How to use as a backpacker

See https://treksafer.com for more information.

1. Add TrekSafer’s number to your contacts.
1. From your inReach / satellite SMS device, open a new message to that number.
1. Type **`fires`** — that’s it. *(Any text is fine; `fires` is easy to remember.)*
1. Make sure your device is set to **include location** with outgoing messages (most inReach presets add “(lat, lon)” automatically).
1. Hit *Send*. Within a few seconds you’ll get a reply like:

```
Fire: Lower Young Creek (K50911)
Location: Lower Young Creek 25km E
Size: 66 ha
Status: Out of Control
```

Currently all active fires within a 50km radius of your GPS location are returned.

---

## Filter Options

You can customize your fire search with optional filters in your message:

### Status Filters
- **`active`** - Only active/out of control fires
- **`all`** - All fires including extinguished ones
- **Default** - Active, managed, and controlled fires (excludes extinguished)

### Distance Filters
- **`25km`** or **`10mi`** - Custom search radius (max 150km)
- **Default** - 50km radius

### Coordinate Formats Supported
- Decimal degrees: `(49.123, -123.456)`
- Hemisphere notation: `50.58225° N, 122.09114° W`
- Apple Maps and Google Maps share links
- InReach automatic format: `fires (lat, lon)`

### Example Usage
```
Basic: fires
With status filter: fires active
With distance: fires 25km
Combined filters: fires active 10mi
With coordinates: (49.2827, -123.1207) active 25km
```

---

## Todo

1. Currently wildfire data is pulled in manually / periodically, not at the time of the request. Pulling closer live data at the time of the request is a must have for fires of note.
2. Pulling in data sources from outside of the US/Canada and more fine grained data support for specific US states that offer it.
3. Add options to the SMS call, such as specifying the fire radius.
4. Detect message send failures and auto-retry.
5. Add current AQI ratings at the coordinates.

---

## Running TrekSafer locally

### Clone & set up

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

From within the venv environment run `python ./download.py`. This will download public data sources. Still on the todo list to get the data downloading in realtime.
