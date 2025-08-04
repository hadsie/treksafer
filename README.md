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
