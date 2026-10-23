import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from app.config import get_config
from app.filters import STATUS_LEVELS

# Relative to the run, so the fixture database never ages out of time windows.
FIXTURE_FETCHED_AT = (datetime.now(timezone.utc).replace(microsecond=0)
                      - timedelta(days=30))

LOOKUP_FETCHED_DAYS_AGO = 3

_STAGE_LEVELS = {
    'OUT_CNTRL': ('Out of Control', 'active'), 'OC': ('OC', 'active'),
    'HOLDING': ('Being Held', 'managed'), 'BH': ('BH', 'managed'),
    'UNDR_CNTRL': ('Under Control', 'controlled'), 'UC': ('UC', 'controlled'),
    'OUT': ('Out', 'out'), 'Out': ('Out', 'out'),
    'Out of Control': ('Out of Control', 'active'),
    'Not Under Control': ('Not Under Control', 'active'),
    'Being Observed': ('Being Observed', 'active'),
    'Being Held': ('Being Held', 'managed'),
    'Under Control': ('Under Control', 'controlled'),
}


def _us_status(pct, incident_type):
    if incident_type == 'RX':
        return 'Prescribed', 'controlled'
    if pct is None:
        return 'Active', 'active'
    if pct >= 100:
        return 'Contained', 'controlled'
    if pct <= 0:
        return 'Uncontained', 'active'
    return f'{round(pct)}% contained', 'active'


def _normalize_fixture(location, props):
    """Translate a fixture GeoJSON's legacy properties to database fields."""
    if location == 'BC':
        # A null stage mirrors the unmapped-status treatment: shown, active.
        status, level = _STAGE_LEVELS.get(props['stageOfControlCode'], ('Unknown', 'active'))
        return {'Fire': props['FIRE_NUM'], 'Name': props.get('incidentName'),
                'Location': props.get('incidentLocation'), 'Type': None,
                'Size': props['FIRE_SZ_HA'], 'Status': status, 'level': level,
                'fire_key': f"{props.get('FIRE_YEAR', 2026)}-{props['FIRE_NUM']}"}
    if location == 'AB':
        status, level = _STAGE_LEVELS[props['STATUS']]
        return {'Fire': props['FIRE_NUMBE'], 'Name': props.get('ALIAS'),
                'Location': props.get('COMPLEX'), 'Type': None,
                'Size': props['AREA'], 'Status': status, 'level': level,
                'fire_key': props['FIRE_NUMBE']}
    if location == 'ON':
        status, level = _STAGE_LEVELS[props['CONDITION_DESCRIPTION']]
        return {'Fire': props['FIRE_NAME'], 'Name': None,
                'Location': props.get('DISTRICT_NAME'), 'Type': None,
                'Size': props['CURRENT_SIZE'], 'Status': status,
                'level': level,
                'fire_key': f"{props['FIRE_YEAR']}-{props['FIRE_NAME']}"}
    if location == 'CA':
        status, level = _STAGE_LEVELS[props['stage_of_c']]
        return {'Fire': props['firename'], 'Name': None,
                'Location': props.get('agency'), 'Type': None,
                'Size': props['hectares'], 'Status': status, 'level': level,
                'fire_key': props['firename']}
    if location == 'US':
        status, level = _us_status(props.get('PCT_CONT'), props.get('INCID_TYPE'))
        discovered = props.get('DISCOVERED')
        return {'Fire': props['FIRE_NAME'], 'Name': None,
                'Location': props.get('LOCATION') or None,
                'Type': props.get('INCID_TYPE'),
                'Size': props.get('SIZE_HA'), 'Status': status, 'level': level,
                'fire_key': props['FIRE_NAME'],
                'Discovered': (datetime.fromtimestamp(discovered / 1000, timezone.utc)
                               if discovered else None)}
    raise ValueError(f"No fixture normalizer for {location}")


def build_fixture_db(path, fetched_at=FIXTURE_FETCHED_AT):
    """Build a fire database from the fixture GeoJSONs."""
    from app.fires import db as firedb

    data_dir = Path(__file__).parent / 'data'
    conn = firedb.connect(str(path))
    try:
        for location in ('BC', 'AB', 'ON', 'CA', 'US'):
            geojson = json.load(open(data_dir / f'{location}_perimeters.geojson'))
            records = []
            for feature in geojson['features']:
                row = _normalize_fixture(location, feature['properties'])
                row['StatusLevel'] = STATUS_LEVELS[row.pop('level')]
                records.append(row)
            geometry = gpd.GeoDataFrame.from_features(
                geojson['features'], crs='EPSG:4326').geometry
            fires = gpd.GeoDataFrame(records, geometry=geometry, crs='EPSG:4326')
            firedb.record_fires(conn, location, fires, fetched_at)
    finally:
        conn.close()


@pytest.fixture
def lookup_fire_db(tmp_path, monkeypatch):
    """Serve fire data from a database holding only BC fire C10784, last
    fetched LOOKUP_FETCHED_DAYS_AGO days ago.

    fireid lookups ignore inactive fires last seen more than
    lookup_history_months ago, so the fetch date is set relative to now.
    """
    from app.fires import db as firedb

    fire = gpd.GeoDataFrame(
        {'Fire': ['C10784'], 'Name': ['Kamloops_Base'], 'Location': ['Kamloops'],
         'Type': [None], 'Size': [50.0], 'Status': ['Being Held'],
         'StatusLevel': [STATUS_LEVELS['managed']], 'fire_key': ['2026-C10784']},
        geometry=[box(-120.505, 50.4968, -120.495, 50.5032)], crs='EPSG:4326')
    path = str(tmp_path / 'fires.db')
    conn = firedb.connect(path)
    try:
        firedb.record_fires(conn, 'BC', fire, datetime.now(timezone.utc)
                            - timedelta(days=LOOKUP_FETCHED_DAYS_AGO))
    finally:
        conn.close()
    monkeypatch.setattr(get_config(), 'database', path)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Default to 'test' if not already set
    os.environ.setdefault("TREKSAFER_ENV", "test")
    # Unit tests never hit the realtime APIs
    os.environ.setdefault("TREKSAFER_BC_REALTIME", "false")
    os.environ.setdefault("TREKSAFER_AB_REALTIME", "false")
    os.environ.setdefault("TREKSAFER_ON_REALTIME", "false")
    os.environ.setdefault("TREKSAFER_CA_REALTIME", "false")
    os.environ.setdefault("TREKSAFER_US_REALTIME", "false")
    # Serve fire data from a fixture database built from tests/data/
    if "TREKSAFER_DATABASE" not in os.environ:
        db_path = Path(tempfile.mkdtemp(prefix="treksafer-tests-")) / "fires.db"
        build_fixture_db(db_path)
        os.environ["TREKSAFER_DATABASE"] = str(db_path)
