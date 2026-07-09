"""Tests for the ArcGIS transport client."""

from datetime import timedelta

import pytest
import responses

from app.arcgis import fetch_layer, _session

LAYER_URL = 'https://example.test/points/FeatureServer/0'
QUERY_URL = f'{LAYER_URL}/query'


def feature(name):
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [-105.35, 55.15]},
        'properties': {'Fire_Name': name},
    }


def collection(features):
    return {'type': 'FeatureCollection', 'features': features}


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


class TestFetchLayer:
    """Full-layer fetches paginate past the ArcGIS transfer limit."""

    def test_paginates_in_object_id_order(self, mocked_responses):
        mocked_responses.get(LAYER_URL, json={'objectIdField': 'ObjectId'})
        page1 = collection([feature('F1')])
        page1['exceededTransferLimit'] = True
        mocked_responses.get(QUERY_URL, json=page1)
        mocked_responses.get(QUERY_URL, json=collection([feature('F2')]))

        gdf = fetch_layer(QUERY_URL, ['Fire_Name'])

        assert list(gdf['Fire_Name']) == ['F1', 'F2']
        page2_params = mocked_responses.calls[2].request.params
        assert page2_params['resultOffset'] == '1'
        assert page2_params['orderByFields'] == 'ObjectId'

    def test_object_id_field_from_fields_list(self, mocked_responses):
        """Layers without objectIdField metadata fall back to the OID-typed field."""
        mocked_responses.get(LAYER_URL, json={
            'fields': [
                {'name': 'Fire_Name', 'type': 'esriFieldTypeString'},
                {'name': 'OBJECTID', 'type': 'esriFieldTypeOID'},
            ],
        })
        mocked_responses.get(QUERY_URL, json=collection([feature('F1')]))

        gdf = fetch_layer(QUERY_URL, ['Fire_Name'])

        assert mocked_responses.calls[1].request.params['orderByFields'] == 'OBJECTID'
        assert list(gdf['Fire_Name']) == ['F1']

    def test_raises_on_truncated_empty_page(self, mocked_responses):
        """A server that reports more features but returns none must not loop forever."""
        mocked_responses.get(LAYER_URL, json={'objectIdField': 'ObjectId'})
        page = collection([])
        page['exceededTransferLimit'] = True
        mocked_responses.get(QUERY_URL, json=page)

        with pytest.raises(ValueError, match="empty page"):
            fetch_layer(QUERY_URL, ['Fire_Name'])

    def test_raises_when_pagination_never_completes(self, mocked_responses):
        """A server that ignores resultOffset serves the same full page forever."""
        mocked_responses.get(LAYER_URL, json={'objectIdField': 'ObjectId'})
        page = collection([feature('F1')])
        page['exceededTransferLimit'] = True
        mocked_responses.get(QUERY_URL, json=page)

        with pytest.raises(ValueError, match="pages"):
            fetch_layer(QUERY_URL, ['Fire_Name'])

    def test_raises_when_object_id_field_unknown(self, mocked_responses):
        """Pagination without a stable order can skip fires; fail loudly instead."""
        mocked_responses.get(LAYER_URL, json={'fields': []})

        with pytest.raises(ValueError, match="object ID field"):
            fetch_layer(QUERY_URL, ['Fire_Name'])


class TestSession:
    def test_session_is_cached_with_configured_ttl(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        _session.cache_clear()

        session = _session(900)

        assert session.settings.expire_after == timedelta(seconds=900)
        assert session.settings.stale_if_error is True
        assert _session(900) is session
        _session.cache_clear()
