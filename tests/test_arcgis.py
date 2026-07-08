"""Tests for the realtime ArcGIS fire data client."""

import logging
from datetime import timedelta

import pytest
import requests
import responses

from app.arcgis import fetch_fires, _session
from app.config import RealtimeFireConfig

POINTS_URL = 'https://example.test/points/FeatureServer/0/query'
PERIMS_URL = 'https://example.test/perims/FeatureServer/0/query'

CONFIG = RealtimeFireConfig(
    points_url=POINTS_URL,
    perimeters_url=PERIMS_URL,
    perimeter_fire_field='FIRE_NUMBER',
    mapping={
        'Fire': 'FIRE_NUMBER',
        'Name': 'INCIDENT_NAME',
        'Location': 'GEOGRAPHIC_DESCRIPTION',
        'Size': 'CURRENT_SIZE',
        'Status': 'FIRE_STATUS',
    },
    status_map={
        'active': ['Out of Control', 'Fire of Note'],
        'managed': ['Being Held'],
        'controlled': ['Under Control'],
        'out': ['Out'],
    },
)

COORDS = (50.6, -120.3)


def point_feature(number, status='Out of Control', lon=-120.35, lat=50.65):
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
        'properties': {
            'FIRE_NUMBER': number,
            'INCIDENT_NAME': number,
            'GEOGRAPHIC_DESCRIPTION': 'Test Creek',
            'CURRENT_SIZE': 12.5,
            'FIRE_STATUS': status,
        },
    }


def perimeter_feature(number, lon=-120.35, lat=50.65, fire_field='FIRE_NUMBER'):
    ring = [(lon, lat), (lon + 0.01, lat), (lon + 0.01, lat + 0.01), (lon, lat + 0.01), (lon, lat)]
    return {
        'type': 'Feature',
        'geometry': {'type': 'Polygon', 'coordinates': [ring]},
        'properties': {fire_field: number},
    }


def collection(features):
    return {'type': 'FeatureCollection', 'features': features}


@pytest.fixture
def plain_session(monkeypatch):
    """Bypass the on-disk response cache so tests stay independent."""
    monkeypatch.setattr('app.arcgis._session', lambda timeout: requests.Session())


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.mark.usefixtures('plain_session')
class TestFetchFires:
    def test_merges_perimeter_geometry_over_point(self, mocked_responses):
        """A fire in both layers uses its perimeter polygon; point-only fires keep their point."""
        mocked_responses.get(POINTS_URL, json=collection([
            point_feature('K1'), point_feature('K2'),
        ]))
        mocked_responses.get(PERIMS_URL, json=collection([perimeter_feature('K1')]))

        gdf = fetch_fires(CONFIG, COORDS, 50)

        assert str(gdf.crs) == 'EPSG:3857'
        assert set(gdf['FIRE_NUMBER']) == {'K1', 'K2'}
        geoms = dict(zip(gdf['FIRE_NUMBER'], gdf.geometry))
        assert geoms['K1'].geom_type == 'Polygon'
        assert geoms['K2'].geom_type == 'Point'

    def test_perimeter_fire_field_joins_differently_named_layers(self, mocked_responses):
        """AB-style layers where the perimeter fire-number field differs from the points layer."""
        config = CONFIG.model_copy(update={'perimeter_fire_field': 'FireNumber'})
        mocked_responses.get(POINTS_URL, json=collection([point_feature('HWF096')]))
        mocked_responses.get(PERIMS_URL, json=collection([
            perimeter_feature('HWF096', fire_field='FireNumber'),
        ]))

        gdf = fetch_fires(config, COORDS, 50)

        assert set(gdf['FIRE_NUMBER']) == {'HWF096'}
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'
        params = mocked_responses.calls[1].request.params
        assert params['outFields'] == 'FireNumber'

    def test_empty_results_return_empty_dataframe(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([]))

        gdf = fetch_fires(CONFIG, COORDS, 50)

        assert gdf is not None
        assert gdf.empty

    def test_network_error_returns_none(self, mocked_responses, caplog):
        mocked_responses.get(POINTS_URL, body=requests.ConnectionError('boom'))

        with caplog.at_level(logging.WARNING):
            assert fetch_fires(CONFIG, COORDS, 50) is None
        assert 'Realtime fire query failed' in caplog.text

    def test_http_error_returns_none(self, mocked_responses, caplog):
        mocked_responses.get(POINTS_URL, status=503)

        with caplog.at_level(logging.WARNING):
            assert fetch_fires(CONFIG, COORDS, 50) is None
        assert 'Realtime fire query failed' in caplog.text

    def test_arcgis_error_body_returns_none(self, mocked_responses, caplog):
        """ArcGIS reports failures as HTTP 200 with an error body."""
        mocked_responses.get(POINTS_URL, json={'error': {'code': 400, 'message': 'Invalid query'}})

        with caplog.at_level(logging.WARNING):
            assert fetch_fires(CONFIG, COORDS, 50) is None
        assert 'Invalid query' in caplog.text

    def test_exceeded_transfer_limit_returns_none(self, mocked_responses, caplog):
        """Truncated results must never be reported as complete fire data."""
        payload = collection([point_feature('K1')])
        payload['exceededTransferLimit'] = True
        mocked_responses.get(POINTS_URL, json=payload)

        with caplog.at_level(logging.WARNING):
            assert fetch_fires(CONFIG, COORDS, 50) is None
        assert 'transfer limit' in caplog.text

    def test_unmatched_perimeter_logged_and_excluded(self, mocked_responses, caplog):
        mocked_responses.get(POINTS_URL, json=collection([point_feature('K1')]))
        mocked_responses.get(PERIMS_URL, json=collection([
            perimeter_feature('K1'), perimeter_feature('K9'),
        ]))

        with caplog.at_level(logging.WARNING):
            gdf = fetch_fires(CONFIG, COORDS, 50)

        assert set(gdf['FIRE_NUMBER']) == {'K1'}
        assert 'K9' in caplog.text

    def test_query_parameters(self, mocked_responses):
        """The spatial filter sends lon,lat in WGS84 with a km radius."""
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([]))

        fetch_fires(CONFIG, COORDS, 75)

        params = mocked_responses.calls[0].request.params
        assert params['geometry'] == '-120.3,50.6'
        assert params['distance'] == '75'
        assert params['units'] == 'esriSRUnit_Kilometer'
        assert params['inSR'] == '4326'
        assert params['f'] == 'geojson'


class TestSession:
    def test_session_is_cached_with_configured_ttl(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        _session.cache_clear()

        session = _session(900)

        assert session.settings.expire_after == timedelta(seconds=900)
        assert session.settings.stale_if_error is True
        assert _session(900) is session
        _session.cache_clear()


@pytest.mark.live
class TestLiveEndpoint:
    def test_bcws_layers_respond(self):
        """The real BCWS layers answer a point-radius query near Kamloops."""
        from app.config import get_config
        bc = next(d for d in get_config().data if d.location == 'BC')
        gdf = fetch_fires(bc.realtime, (50.67, -120.34), 100)

        assert gdf is not None
        assert str(gdf.crs) == 'EPSG:3857'
        assert 'FIRE_NUMBER' in gdf.columns

    def test_alberta_layers_respond(self):
        """The real Alberta Wildfire layers answer a point-radius query near Edmonton."""
        from app.config import get_config
        ab = next(d for d in get_config().data if d.location == 'AB')
        gdf = fetch_fires(ab.realtime, (53.55, -113.49), 150)

        assert gdf is not None
        assert str(gdf.crs) == 'EPSG:3857'
        assert 'LABEL' in gdf.columns
