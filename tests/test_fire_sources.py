"""Tests for the realtime fire source layer (joins, geometry synthesis)."""

import logging
import math

import pytest
import requests
import responses
from urllib.parse import unquote_plus

from app.fires.sources import fetch_fires, fetch_fires_by_id
from app.config import RealtimeFireConfig

POINTS_URL = 'https://example.test/points/FeatureServer/0/query'
PERIMS_URL = 'https://example.test/perims/FeatureServer/0/query'

CONFIG = RealtimeFireConfig(
    points_url=POINTS_URL,
    perimeters_url=PERIMS_URL,
    join_field='FIRE_NUMBER',
    perimeter_fire_field='FIRE_NUMBER',
    key_fields=['FIRE_NUMBER'],
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

    def test_join_field_joins_on_stable_key(self, mocked_responses):
        """US-style layers join on a GUID while displaying a human-readable name."""
        config = CONFIG.model_copy(update={
            'join_field': 'IRWIN',
            'perimeter_fire_field': 'attr_IRWIN',
        })
        point = point_feature('K1')
        point['properties']['IRWIN'] = '{GUID-1}'
        mocked_responses.get(POINTS_URL, json=collection([point]))
        mocked_responses.get(PERIMS_URL, json=collection([
            perimeter_feature('{GUID-1}', fire_field='attr_IRWIN'),
        ]))

        gdf = fetch_fires(config, COORDS, 50)

        assert list(gdf['FIRE_NUMBER']) == ['K1']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'
        assert 'IRWIN' in mocked_responses.calls[0].request.params['outFields']

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

    def test_stale_perimeter_logged_and_excluded(self, mocked_responses, caplog):
        """A perimeter whose fire number is nowhere in the points layer is dropped."""
        mocked_responses.get(POINTS_URL, json=collection([point_feature('K1')]))
        mocked_responses.get(PERIMS_URL, json=collection([
            perimeter_feature('K1'), perimeter_feature('K9'),
        ]))
        mocked_responses.get(POINTS_URL, json=collection([]))

        with caplog.at_level(logging.WARNING):
            gdf = fetch_fires(CONFIG, COORDS, 50)

        assert set(gdf['FIRE_NUMBER']) == {'K1'}
        assert 'K9' in caplog.text
        recovery_call = mocked_responses.calls[2].request.params
        assert "FIRE_NUMBER IN ('K9')" in recovery_call['where']

    def test_perimeter_with_distant_point_recovered_by_fire_number(self, mocked_responses):
        """A fire whose point sits outside the radius is fetched by number."""
        mocked_responses.get(POINTS_URL, json=collection([point_feature('K1')]))
        mocked_responses.get(PERIMS_URL, json=collection([
            perimeter_feature('K1'), perimeter_feature('K9'),
        ]))
        mocked_responses.get(POINTS_URL, json=collection([
            point_feature('K9', lon=-121.5, lat=51.2),
        ]))

        gdf = fetch_fires(CONFIG, COORDS, 50)

        assert set(gdf['FIRE_NUMBER']) == {'K1', 'K9'}
        geoms = dict(zip(gdf['FIRE_NUMBER'], gdf.geometry))
        assert geoms['K9'].geom_type == 'Polygon'

    def test_recovery_runs_when_radius_has_no_points(self, mocked_responses):
        """A lone boundary-straddling fire is still reported."""
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([perimeter_feature('K9')]))
        mocked_responses.get(POINTS_URL, json=collection([
            point_feature('K9', lon=-121.5, lat=51.2),
        ]))

        gdf = fetch_fires(CONFIG, COORDS, 50)

        assert list(gdf['FIRE_NUMBER']) == ['K9']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'

    def test_failed_recovery_keeps_radius_results(self, mocked_responses, caplog):
        mocked_responses.get(POINTS_URL, json=collection([point_feature('K1')]))
        mocked_responses.get(PERIMS_URL, json=collection([
            perimeter_feature('K1'), perimeter_feature('K9'),
        ]))
        mocked_responses.get(POINTS_URL, status=503)

        with caplog.at_level(logging.WARNING):
            gdf = fetch_fires(CONFIG, COORDS, 50)

        assert set(gdf['FIRE_NUMBER']) == {'K1'}
        assert 'Recovery query' in caplog.text

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


SPATIAL_CONFIG = RealtimeFireConfig(
    points_url=POINTS_URL,
    perimeters_url=PERIMS_URL,
    join='spatial',
    key_fields=['Fire_Name'],
    points_where="Agency NOT IN ('BC','AB')",
    mapping={
        'Fire': 'Fire_Name',
        'Location': 'Agency',
        'Size': 'Hectares__Ha_',
        'Status': 'Stage_of_Control',
    },
    status_map={'active': ['OC'], 'managed': ['BH'], 'controlled': ['UC'], 'out': []},
)

CA_COORDS = (55.1, -105.3)


def ca_point_feature(name, size=10.0, lon=-105.35, lat=55.15):
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
        'properties': {
            'Fire_Name': name,
            'Agency': 'SK',
            'Hectares__Ha_': size,
            'Stage_of_Control': 'OC',
        },
    }


def ca_perimeter_feature(lon=-105.35, lat=55.15, half=0.02):
    ring = [(lon - half, lat - half), (lon + half, lat - half),
            (lon + half, lat + half), (lon - half, lat + half), (lon - half, lat - half)]
    return {
        'type': 'Feature',
        'geometry': {'type': 'Polygon', 'coordinates': [ring]},
        'properties': {'OBJECTID': 1},
    }


@pytest.mark.usefixtures('plain_session')
class TestFetchFiresSpatial:
    """Spatially joined sources (perimeters carry no fire ID)."""

    def test_point_inside_polygon_gets_it(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([ca_point_feature('F1')]))
        mocked_responses.get(PERIMS_URL, json=collection([ca_perimeter_feature()]))

        gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert list(gdf['Fire_Name']) == ['F1']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'
        assert len(mocked_responses.calls) == 2

    def test_unmatched_sized_point_gets_circle_of_reported_area(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([ca_point_feature('F1', size=100.0)]))
        mocked_responses.get(PERIMS_URL, json=collection([]))

        gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        geom = gdf.geometry.iloc[0]
        assert geom.geom_type == 'Polygon'
        # Planar area in EPSG:3857 is inflated by 1/cos^2(lat).
        expected = 100.0 * 10_000 / math.cos(math.radians(55.15)) ** 2
        assert abs(geom.area - expected) / expected < 0.05

    def test_unmatched_sizeless_point_gets_minimal_circle(self, mocked_responses):
        """No reported size still yields a polygon, so geometry stays uniform."""
        mocked_responses.get(POINTS_URL, json=collection([ca_point_feature('F1', size=None)]))
        mocked_responses.get(PERIMS_URL, json=collection([]))

        gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        geom = gdf.geometry.iloc[0]
        assert geom.geom_type == 'Polygon'
        # Minimal 100m true-radius circle, planar-inflated by 1/cos(lat).
        expected = math.pi * (100 / math.cos(math.radians(55.15))) ** 2
        assert abs(geom.area - expected) / expected < 0.05

    def test_agency_filter_sent_with_points_query(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([]))

        fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert mocked_responses.calls[0].request.params['where'] == "Agency NOT IN ('BC','AB')"

    def test_province_filter_sent_with_perimeters_query(self, mocked_responses):
        config = SPATIAL_CONFIG.model_copy(
            update={'perimeters_where': "Province NOT IN ('British Columbia','Alberta')"})
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([]))

        fetch_fires(config, CA_COORDS, 100)

        perimeter_call = mocked_responses.calls[1].request.params
        assert perimeter_call['where'] == "Province NOT IN ('British Columbia','Alberta')"

    def test_perimeter_with_distant_fire_point_is_recovered(self, mocked_responses):
        """A megafire's polygon reaches the radius while its report point sits outside it."""
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([ca_perimeter_feature()]))
        mocked_responses.get(POINTS_URL, json=collection([
            ca_point_feature('MEGA', size=200000.0, lon=-105.34, lat=55.16),
        ]))

        gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert list(gdf['Fire_Name']) == ['MEGA']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'
        envelope_call = mocked_responses.calls[2].request.params
        assert envelope_call['geometryType'] == 'esriGeometryEnvelope'
        assert envelope_call['where'] == "Agency NOT IN ('BC','AB')"

    def test_detached_patch_unions_into_nearby_fire(self, mocked_responses):
        """A fire's disconnected burn patches merge into one geometry."""
        mocked_responses.get(POINTS_URL, json=collection([ca_point_feature('F1')]))
        mocked_responses.get(PERIMS_URL, json=collection([
            ca_perimeter_feature(),                        # contains F1's point
            ca_perimeter_feature(lon=-105.25, lat=55.15),  # detached patch ~6km east
        ]))

        gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert len(gdf) == 1
        assert gdf.geometry.iloc[0].geom_type == 'MultiPolygon'
        assert len(mocked_responses.calls) == 2  # both patches claimed, no recovery

    def test_in_radius_patch_with_far_point_reports_the_fire(self, mocked_responses):
        """The field case: only a detached patch is in radius; the fire's
        point is beyond it. The fire must still be reported."""
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([ca_perimeter_feature()]))
        mocked_responses.get(POINTS_URL, json=collection([
            ca_point_feature('FAR', size=40000.0, lon=-105.6, lat=55.25),  # ~18km away
        ]))

        gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert list(gdf['Fire_Name']) == ['FAR']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'
        # Recovery envelope grew past the patch's own bounding box.
        envelope = mocked_responses.calls[2].request.params['geometry']
        minx, miny, maxx, maxy = (float(v) for v in envelope.split(','))
        assert maxx - minx > 0.3  # the patch alone is 0.04 degrees wide

    def test_orphaned_perimeter_dropped_with_warning(self, mocked_responses, caplog):
        mocked_responses.get(POINTS_URL, json=collection([]))
        mocked_responses.get(PERIMS_URL, json=collection([ca_perimeter_feature()]))
        mocked_responses.get(POINTS_URL, json=collection([]))

        with caplog.at_level(logging.WARNING):
            gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert gdf.empty
        assert 'no active fire record' in caplog.text

    def test_failed_recovery_query_degrades_to_radius_results(self, mocked_responses, caplog):
        """A recovery failure must not throw away the fires already in hand."""
        mocked_responses.get(POINTS_URL, json=collection([
            ca_point_feature('F1', lon=-105.6, lat=55.4),
        ]))
        mocked_responses.get(PERIMS_URL, json=collection([ca_perimeter_feature()]))
        mocked_responses.get(POINTS_URL, status=503)

        with caplog.at_level(logging.WARNING):
            gdf = fetch_fires(SPATIAL_CONFIG, CA_COORDS, 100)

        assert gdf is not None
        assert list(gdf['Fire_Name']) == ['F1']
        assert 'Recovery query' in caplog.text


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

    def test_national_layers_respond(self):
        """The real Esri Canada national layers answer a query near La Ronge SK."""
        from app.config import get_config
        ca = next(d for d in get_config().data if d.location == 'CA')
        gdf = fetch_fires(ca.realtime, (55.1, -105.3), 100)

        assert gdf is not None
        assert str(gdf.crs) == 'EPSG:3857'
        assert 'Fire_Name' in gdf.columns
        assert not (gdf['Agency'].isin(['BC', 'AB'])).any()

    def test_wfigs_layers_respond(self):
        """The real NIFC WFIGS layers answer a query near Boise ID."""
        from app.config import get_config
        us = next(d for d in get_config().data if d.location == 'US')
        gdf = fetch_fires(us.realtime, (43.6, -116.2), 150)

        assert gdf is not None
        assert str(gdf.crs) == 'EPSG:3857'
        assert 'IncidentName' in gdf.columns


@pytest.mark.usefixtures('plain_session')
class TestFetchFiresById:
    """fetch_fires_by_id() looks up fires by their displayed identifier,
    ignoring location."""

    def test_field_join_matches_and_merges_perimeter(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([point_feature('K1')]))
        mocked_responses.get(PERIMS_URL, json=collection([perimeter_feature('K1')]))

        gdf = fetch_fires_by_id(CONFIG, 'K1')

        assert list(gdf['FIRE_NUMBER']) == ['K1']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'
        assert str(gdf.crs) == 'EPSG:3857'

    def test_points_query_uses_case_insensitive_like(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([]))

        fetch_fires_by_id(CONFIG, "K1's")

        where = unquote_plus(mocked_responses.calls[0].request.url)
        assert "UPPER(FIRE_NUMBER) LIKE UPPER('%K1''s%')" in where

    def test_no_match_returns_empty_without_perimeter_query(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([]))

        gdf = fetch_fires_by_id(CONFIG, 'NOPE')

        assert gdf.empty
        assert str(gdf.crs) == 'EPSG:3857'
        assert len(mocked_responses.calls) == 1  # no perimeter fetch

    def test_spatial_join_matches_and_merges_perimeter(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json=collection([ca_point_feature('F1')]))
        mocked_responses.get(PERIMS_URL, json=collection([ca_perimeter_feature()]))

        gdf = fetch_fires_by_id(SPATIAL_CONFIG, 'F1')

        assert list(gdf['Fire_Name']) == ['F1']
        assert gdf.geometry.iloc[0].geom_type == 'Polygon'

    def test_source_unavailable_returns_none(self, mocked_responses):
        mocked_responses.get(POINTS_URL, json={'error': {'code': 500}})

        assert fetch_fires_by_id(CONFIG, 'K1') is None
