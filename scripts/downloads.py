#!venv/bin/python

import geopandas as gpd
import pandas as pd
import networkx as nx
import requests
import tempfile
import time
import warnings
import zipfile

from datetime import date
from pathlib import Path
from shapely.geometry import Point

from app.config import get_config

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
                                 'returnAttachments': 'true',
                                 'returnAttachmentsDataByUrl': 'true',
                                 'async': 'true',
                                 'syncModel': 'none',
                                 'targetType': 'client',
                                 'syncDirection': 'bidirectional',
                                 'attachmentsSyncDirection': 'bidirectional',
                                 'dataFormat': 'shapefile',
                             }
                            )
    status_url = response.json()['statusUrl']
    try:
        json_response = requests.get(status_url, params="f=json").json()
        while json_response['status'] in ['Pending', 'ExportingData'] and time.time() - start_time < max_wait_time:
            time.sleep(10)
            json_response = requests.get(status_url, params="f=json").json()

        file = requests.get(json_response['resultUrl'])
        open(target_path, 'wb').write(file.content)
    except:
        print('Failed to download')

def fetch_AB():
    fire_perimeters_url = 'https://services.arcgis.com/Eb8P5h4CJk8utIBz/arcgis/rest/services/wildfire_perimeter_active/FeatureServer/1/query?where=1=1&outFields=*&f=geojson'

    # Convert the ArcGIS geojson to a shapefile.
    print(f"Downloading AB Fire Perimeters")
    geojson_response = requests.get(fire_perimeters_url)
    data = geojson_response.json()
    if "features" not in data or not data["features"]:
        raise ValueError("No features returned by query.")
    gdf = gpd.GeoDataFrame.from_features(data["features"])
    gdf = gdf.rename(columns={
        "FIRE_STATUS": "STATUS",
        "FIRE_COMPLEX_NAME": "COMPLEX",
        "AREA_ESTIMATE": "AREA"
    })
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
    """Download Canadian active-fire CSV + perimeter shapefile and return a
     GeoDataFrame to merge active fire names into the geometry polygons."""

    # Load the hotspot perimeter shapefile
    perim_url = "https://cwfis.cfs.nrcan.gc.ca/downloads/hotspots/perimeters.shp"
    gdf_perim = gpd.read_file(perim_url)  # EPSG:3978
    print(f"Loaded {len(gdf_perim)} perimeters.")

    # Load activefires metadata CSV.
    active_fires_url = "https://cwfis.cfs.nrcan.gc.ca/downloads/activefires/activefires.csv"
    df_meta = pd.read_csv(active_fires_url)
    df_meta.columns = df_meta.columns.str.strip()
    df_meta = df_meta.applymap(
        lambda x: x.strip() if isinstance(x, str) else x
    )
    # Remove all excluded agencies from the active fires list.
    excluded_agencies = ["conus", "bc", "ab"]
    original_count = len(df_meta)
    df_meta = df_meta[~df_meta["agency"].str.strip().isin(excluded_agencies)]
    print(f"Filtered out {original_count - len(df_meta)} fires.")

    # Convert fire lat/lon to Point geometries
    df_meta["geometry"] = [Point(xy) for xy in zip(df_meta["lon"], df_meta["lat"])]
    gdf_meta = gpd.GeoDataFrame(df_meta, geometry="geometry", crs="EPSG:4326")
    print(f"Loaded {len(gdf_meta)} ignition points.")

    merged = merge_fires_with_perimeters(gdf_meta, gdf_perim, {
        'fireId': 'firename',
        'fireArea': 'agency',
        'fireSize': 'hectares',
        'fireStage': 'stage_of_control',
        'perimId': 'UID',
    })

    write_shapefile("CA", merged)

def merge_fires_with_perimeters(
        gdf_meta: gpd.GeoDataFrame,
        gdf_perim: gpd.GeoDataFrame,
        keys: dict[str, str],
        *,
        search_radius_m: float = 1_000
    ) -> gpd.GeoDataFrame:
    """
    Match every fire point (4326) to at most one perimeter polygon (3978).

    Required keys
    -------------
    keys["fireId"]   : column in *gdf_meta* that uniquely IDs each point
    keys["perimId"]  : column in *gdf_perim* that uniquely IDs each polygon
                      (MUST be different from fireId)

    Optional keys (if present in *keys* and in the dataframe, they’re kept)
    ----------------------------------------------------------------------
    keys["fireArea"] , keys["fireSize"] , keys["fireStage"]

    Returns
    -------
    GeoDataFrame (EPSG:3978) with one row per *matched polygon* and the
    chosen fire-attribute columns appended.
    """
    # ---- sanity on required keys ---------------------------------------
    fire_id  = keys["fireId"]
    perim_id = keys["perimId"]
    if fire_id == perim_id:
        raise ValueError("fireId and perimId must refer to different columns")

    # ---- 1. build point➜polygon assignment -----------------------------
    assignments: dict[int, str] = {}
    collisions : dict[int, set[str]] = {}
    free_ids   = set(gdf_perim[perim_id])

    for idx, fire in gdf_meta.iterrows():
        # project point to metres (3978) once
        point_m = (
            gpd.GeoSeries([fire.geometry], crs="EPSG:4326")
            .to_crs(gdf_perim.crs)
            .iloc[0]
        )

        candidates = gdf_perim[gdf_perim.covers(point_m)]
        if candidates.empty:
            candidates = _get_nearby_perimeters(gdf_perim, point_m, search_radius_m)
            if candidates.empty:
                print(f"No perimeter found for fire '{fire[fire_id]}')")
                continue

        id_set = set(candidates[perim_id]) & free_ids
        if id_set:
            if len(id_set) == 1:
                chosen = id_set.pop()
                assignments[idx] = chosen
                free_ids.remove(chosen)
            else:
                collisions[idx] = id_set
        else:
            largest = (
                candidates.assign(_area=candidates.geometry.area)
                          .sort_values("_area", ascending=False)
                          .iloc[0][perim_id]
            )
            assignments[idx] = largest
            print(
                f"All candidate polygons already assigned for fire "
                f"'{fire[fire_id]}'; reused largest perimeter {largest}."
            )

    if collisions:
        extra = _assign_by_matching(collisions, free_ids)
        assignments.update(extra)
        unmatched = set(collisions) - set(extra)
        if unmatched:
            print(f"{len(unmatched)} fire(s) still have no unique polygon.")

    # ---- 2. tag fires that got a polygon -------------------------------
    gdf_meta[perim_id] = gdf_meta.index.map(assignments)       # NaN if none

    # columns to keep (only if they exist in the dataframe)
    keep_cols = [perim_id]
    for k in ("fireArea", "fireSize", "fireStage"):
        col = keys.get(k)
        if col and col in gdf_meta.columns and col not in keep_cols:
            keep_cols.append(col)
    if fire_id not in keep_cols:
        keep_cols.append(fire_id)

    fire_attrs = gdf_meta.loc[gdf_meta[perim_id].notna(), keep_cols]

    # Notify for any dropped fires or polygons before writing the data.
    unmatched_fires   = gdf_meta[gdf_meta[perim_id].isna()]
    unmatched_polys   = gdf_perim[~gdf_perim[perim_id].isin(fire_attrs[perim_id])]
    print(f"{len(unmatched_fires)} fires had no polygon.")
    print(f"{len(unmatched_polys)} polygons are not associated with any active fire.")

    # ---- 3. inner-join → only matched polygons survive -----------------
    merged = (
        gdf_perim.merge(fire_attrs, on=perim_id, how="inner")
        .reset_index(drop=True)
    )
    return merged

# ----------------------------------------------------------------------
# helper: Turns the “many-candidates” dictionary (fire => polygons)
#         into a bipartite graph and runs maximum matching
# ----------------------------------------------------------------------
def _assign_by_matching(collision_dict, free_uids):
    """
    Parameters
    ----------
    collision_dict : dict[int, set[str]]
        fire_row_idx -> {UID, UID, …} (polygons still viable for that fire)
    free_ids : set[str]
        polygons that are not yet taken by a unique match pass

    Returns
    -------
    dict[int, str]
        mapping fire_row_idx -> chosen UID (only for rows in collision_dict)
    """
    # --- make labels that can't collide ------------------------------
    fire2label = {idx: f"F_{i}" for i, idx in enumerate(collision_dict)}
    poly2label = {uid: f"P_{uid}" for uid in free_uids}

    G = nx.Graph()
    G.add_nodes_from(fire2label.values(), bipartite=0)
    G.add_nodes_from(poly2label.values(), bipartite=1)

    for idx, uid_set in collision_dict.items():
        f_lab = fire2label[idx]
        for uid in uid_set:
            if uid in free_uids:
                G.add_edge(f_lab, poly2label[uid])

    # --- run Hopcroft–Karp with the explicit left set ----------------
    raw = nx.algorithms.bipartite.matching.maximum_matching(
        G, top_nodes=fire2label.values()
    )

    # --- convert labels back to originals ----------------------------
    resolved = {}
    for f_lab, p_lab in raw.items():
        if f_lab.startswith("F_"):            # keep only fire→poly pairs
            # reverse lookup
            idx = next(k for k, v in fire2label.items() if v == f_lab)
            uid = next(k for k, v in poly2label.items() if v == p_lab)
            resolved[idx] = uid
    return resolved

def _get_nearby_perimeters(perim3978: gpd.GeoDataFrame,
                          point3978: Point,
                          distance_m: float = 1_000) -> gpd.GeoDataFrame:
    """
    Return polygons whose true Euclidean distance to `point3978`
    is <= `distance_m`.

    Parameters
    ----------
    perim3978  : GeoDataFrame
        Polygon layer *already* in EPSG:3978 (units = metres).
    point3978  : shapely.geometry.Point
        Point *already* in EPSG:3978.
    distance_m : float
        Search radius in metres (default 1 km).

    Returns
    -------
    GeoDataFrame
        Subset of `perim3978` (still in EPSG:3978) that lie within `distance_m`.
    """

    # 0. quick sanity (optional but catches accidental CRS mix-ups)
    if perim3978.crs is None or perim3978.crs.to_epsg() != 3978:
        raise ValueError("perim3978 must be in EPSG:3978")

    # 1. bounding-box pre-filter using the spatial index
    buffer_geom = point3978.buffer(distance_m)          # metre-based buffer
    #bbox = buffer_geom.bounds                           # (minx, miny, maxx, maxy)
    cand_idx = perim3978.sindex.query(buffer_geom, predicate="intersects")
    if len(cand_idx) == 0:
        return perim3978.iloc[[]]                       # empty GDF, same CRS

    candidates = perim3978.iloc[cand_idx].copy()

    # 2. precise distance filter
    within = candidates.geometry.distance(point3978) <= distance_m
    return candidates.loc[within].copy()

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
        # Suppress long column warnings.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Column names longer than 10 characters will be truncated",
                category=UserWarning
            )
            gdf.to_file(shapefile_base, driver="ESRI Shapefile")

        # Create ZIP archive
        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in Path(tmpdir).glob("data.*"):
                zf.write(file, arcname=file.name)


fetch_CA()
fetch_BC()
fetch_AB()
fetch_US()
