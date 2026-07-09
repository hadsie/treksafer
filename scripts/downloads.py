#!venv/bin/python

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geopandas as gpd
import requests
import tempfile
import time
import zipfile

from datetime import date
from pathlib import Path

from app.arcgis import fetch_layer
from app.config import get_config
from app.fire_sources import spatial_merge

max_wait_time = 600

def fetch_US():
    settings = get_config()
    today = date.today().strftime("%Y%m%d")

    data_obj = None
    for data_file in settings.data:
        if data_file.location == 'US':
            data_obj = data_file
    if not data_obj:
        print("No data settings found for US.")
        return

    filename = data_obj.filename.format(DATE=today)
    target_dir = Path(settings.shapefiles) / data_obj.location
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    # Found at https://data-nifc.opendata.arcgis.com/datasets/d1c32af3212341869b3c810f1a215824_0/explore
    fire_perimeters_url = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/createReplica"

    start_time = time.time()
    response = requests.post(fire_perimeters_url,
                             data = {
                                 'f': 'json',
                                 'layers': '0',
                                 'layerQueries': '{"0":{"queryOption":"all"}}',
                                 'returnAttachments': 'false',
                                 'async': 'true',
                                 'syncModel': 'none',
                                 'targetType': 'client',
                                 'syncDirection': 'bidirectional',
                                 'dataFormat': 'filegdb',
                             }
                            )
    status_url = response.json().get('statusUrl')
    if not status_url:
        raise ValueError(f"US replica request returned no statusUrl: {response.json()}")

    job = requests.get(status_url, params={'f': 'json'}).json()
    while job.get('status') in ('Pending', 'ExportingData') and time.time() - start_time < max_wait_time:
        time.sleep(10)
        job = requests.get(status_url, params={'f': 'json'}).json()

    if job.get('status') != 'Completed' or not job.get('resultUrl'):
        raise ValueError(f"US replica export did not complete: {job}")

    file = requests.get(job['resultUrl'])
    target_path.write_bytes(file.content)

def fetch_AB():
    fire_perimeters_url = 'https://services.arcgis.com/Eb8P5h4CJk8utIBz/arcgis/rest/services/Wildfire_Perimeter_Active_(PROD)/FeatureServer/3/query?where=1=1&outFields=*&f=geojson'

    # Convert the ArcGIS geojson to a shapefile.
    print(f"Downloading AB Fire Perimeters")
    geojson_response = requests.get(fire_perimeters_url)
    data = geojson_response.json()
    if "features" not in data or not data["features"]:
        raise ValueError("No features returned by query.")
    gdf = gpd.GeoDataFrame.from_features(data["features"])
    # Rename to the names config.yaml reads, then keep only those columns so the
    # shapefile carries nothing the app doesn't use (and no >10-char field names).
    gdf = gdf.rename(columns={
        "FireNumber": "FIRE_NUMBE",
        "IncdtName": "ALIAS",
        "FIRE_COMPLEX_NAME": "COMPLEX",
        "AREA_ESTIMATE": "AREA",
        "FIRE_STATUS": "STATUS",
    })
    gdf = gdf[["FIRE_NUMBE", "ALIAS", "COMPLEX", "AREA", "STATUS", "geometry"]]
    gdf = gdf.set_crs("EPSG:4326")

    write_shapefile("AB", gdf)

def fetch_BC():
    settings = get_config()
    fire_perimeters_url = 'https://pub.data.gov.bc.ca/datasets/cdfc2d7b-c046-4bf0-90ac-4897232619e1/prot_current_fire_polys.zip'
    today = date.today().strftime("%Y%m%d")

    data_obj = None
    for data_file in settings.data:
        if data_file.location == 'BC':
            data_obj = data_file
    if not data_obj:
        print("No data settings found for BC.")
        return

    filename = data_obj.filename.format(DATE=today)
    target_dir = Path(settings.shapefiles) / data_obj.location
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    print(f"Downloading BC Fire Perimeters to {target_path}")

    file = requests.get(fire_perimeters_url)
    open(target_path, 'wb').write(file.content)

def fetch_CA():
    """Download the national ArcGIS layers and save the merged result.

    Uses the exact source and merge the realtime CA path uses, so the saved
    file is a recovery mode for when the API is unavailable at request time.
    Because the whole country is fetched, every fire report is already
    present and no follow-up lookups are needed; leftover perimeters have
    no active fire record (stale data, or an agency covered by its own
    dedicated source).
    """
    settings = get_config()
    data_obj = next((d for d in settings.data if d.location == 'CA'), None)
    if not data_obj or not data_obj.realtime:
        print("No realtime CA data settings found.")
        return
    realtime = data_obj.realtime

    points = fetch_layer(realtime.points_url, list(realtime.mapping.values()),
                         realtime.points_where)
    if points.empty:
        raise ValueError("No fires returned by the CA points layer.")
    perimeters = fetch_layer(realtime.perimeters_url, [])
    print(f"Loaded {len(points)} fires and {len(perimeters)} perimeters.")

    merged, used = spatial_merge(points, perimeters, realtime.mapping.get('Size'))
    print(f"{len(perimeters) - len(used)} perimeters had no fire record.")

    # Shapefile field names are capped at 10 chars; use the legacy names the
    # CA fallback mapping in config.yaml reads.
    merged = merged.rename(columns={
        'Fire_Name': 'firename',
        'Agency': 'agency',
        'Hectares__Ha_': 'hectares',
        'Stage_of_Control': 'stage_of_c',
    })
    merged = merged[['firename', 'agency', 'hectares', 'stage_of_c', 'geometry']]
    write_shapefile("CA", merged.to_crs(epsg=4326))

def write_shapefile(location, gdf):
    settings = get_config()
    today = date.today().strftime("%Y%m%d")
    data_obj = None
    for data_file in settings.data:
        if data_file.location == location:
            data_obj = data_file
    if not data_obj:
        print(f"No data settings found for {location}.")
        return
    filename = data_obj.filename.format(DATE=today)
    target_dir = Path(settings.shapefiles) / data_obj.location
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    print(f"Writing shapefiles to {target_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        shapefile_base = Path(tmpdir) / "data.shp"
        # Callers trim to the columns config.yaml reads, all within the
        # shapefile 10-char field-name limit, so nothing gets truncated here.
        gdf.to_file(shapefile_base, driver="ESRI Shapefile")

        # Create ZIP archive
        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in Path(tmpdir).glob("data.*"):
                zf.write(file, arcname=file.name)


def main():
    fetchers = [fetch_CA, fetch_BC, fetch_AB, fetch_US]
    failures = []
    for fetch in fetchers:
        name = fetch.__name__
        try:
            fetch()
        except (requests.RequestException, urllib.error.URLError, ValueError) as e:
            failures.append(name)
            print(f"{name} failed: {e}")

    if failures:
        print(f"\n{len(failures)} of {len(fetchers)} sources failed: "
              f"{', '.join(failures)}")
        return 1
    print(f"\nAll {len(fetchers)} sources downloaded successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
