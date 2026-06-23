"""Unit tests for the Canada-wide fire downloader (scripts/downloads.py).

These cover the logic that broke when CWFIS retired the static activefires.csv
and replaced it with the GeoServer WFS feed: the column rename back to the
legacy schema, the agency exclusion, and point-to-polygon merging. The HTTP
boundary (pandas/geopandas readers) is mocked so no network calls happen.
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from scripts import downloads

CA_KEYS = {
    'fireId': 'firename',
    'fireArea': 'agency',
    'fireSize': 'hectares',
    'fireStage': 'stage_of_c',
    'perimId': 'UID',
}


def _square(cx, cy, half=0.02):
    """A small square polygon centered on (cx, cy) in lon/lat degrees."""
    return Polygon([
        (cx - half, cy - half), (cx + half, cy - half),
        (cx + half, cy + half), (cx - half, cy + half),
    ])


class TestFetchCASchemaAdaptation:
    """fetch_CA must adapt the new WFS schema to the legacy column names."""

    @pytest.fixture
    def wfs_csv(self):
        """A stand-in for the GeoServer WFS CSV, with new-schema columns."""
        return pd.DataFrame({
            'agency_code': ['QC', 'BC', 'AB', 'ON', 'bc'],
            'longitude': [-72.0, -123.0, -114.0, -79.0, -124.0],
            'latitude': [48.5, 49.0, 51.0, 45.0, 50.0],
            'agency_fire_id': ['QC-1', 'BC-9', 'AB-9', 'ON-1', 'BC-8'],
            'fire_size': [10.0, 5.0, 7.0, 20.0, 3.0],
            'stage_of_control_status': ['OC', 'OC', 'UC', 'BH', 'OC'],
        })

    @pytest.fixture
    def captured_meta(self, monkeypatch, wfs_csv):
        """Run fetch_CA with the HTTP boundary mocked, capturing the
        GeoDataFrame handed to the merge step (after rename + exclusion)."""
        captured = {}

        empty_perim = gpd.GeoDataFrame({'UID': []}, geometry=[], crs='EPSG:3978')
        monkeypatch.setattr(downloads.pd, 'read_csv', lambda url: wfs_csv.copy())
        monkeypatch.setattr(downloads.gpd, 'read_file', lambda url: empty_perim)

        def fake_merge(meta, perim, keys, **kwargs):
            captured['meta'] = meta
            captured['keys'] = keys
            return empty_perim

        monkeypatch.setattr(downloads, 'merge_fires_with_perimeters', fake_merge)
        monkeypatch.setattr(downloads, 'write_shapefile', lambda location, gdf: None)

        downloads.fetch_CA()
        return captured

    def test_renames_wfs_columns_to_legacy_names(self, captured_meta):
        meta = captured_meta['meta']
        assert {'agency', 'lon', 'lat', 'firename', 'hectares',
                'stage_of_c'} <= set(meta.columns)

    def test_drops_new_schema_column_names(self, captured_meta):
        meta = captured_meta['meta']
        for new_name in ('agency_code', 'longitude', 'latitude',
                         'agency_fire_id', 'fire_size', 'stage_of_control_status'):
            assert new_name not in meta.columns

    def test_excludes_bc_and_ab_case_insensitively(self, captured_meta):
        meta = captured_meta['meta']
        # QC and ON survive; both BC rows (upper and lower) and AB are removed.
        assert set(meta['agency']) == {'QC', 'ON'}
        assert not {'BC-9', 'BC-8', 'AB-9'} & set(meta['firename'])

    def test_passes_ca_field_mapping_to_merge(self, captured_meta):
        assert captured_meta['keys'] == CA_KEYS


class TestFetchABSchemaAdaptation:
    """fetch_AB must map the ArcGIS schema to the columns config.yaml reads
    and drop everything else, keeping field names within the 10-char limit."""

    def _response(self, features):
        class FakeResponse:
            def json(self_inner):
                return {"type": "FeatureCollection", "features": features}
        return FakeResponse()

    @pytest.fixture
    def captured_gdf(self, monkeypatch):
        feature = {
            "type": "Feature",
            "properties": {
                "FireNumber": "WWF-017-2026",
                "IncdtName": "Some Lake",
                "FIRE_COMPLEX_NAME": "Some Complex",
                "AREA_ESTIMATE": 51.5,
                "FIRE_STATUS": "Under Control",
                # Extra source fields that must not reach the shapefile.
                "FIRE_TYPE": "Wildfire",
                "GISFeatureLastUpdated": "2026-06-23",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-114.0, 55.0], [-113.9, 55.0],
                                 [-113.9, 55.1], [-114.0, 55.1], [-114.0, 55.0]]],
            },
        }
        monkeypatch.setattr(downloads.requests, 'get',
                            lambda url: self._response([feature]))
        captured = {}
        monkeypatch.setattr(downloads, 'write_shapefile',
                            lambda location, gdf: captured.update(location=location, gdf=gdf))
        downloads.fetch_AB()
        return captured

    def test_writes_only_config_columns(self, captured_gdf):
        assert list(captured_gdf['gdf'].columns) == [
            'FIRE_NUMBE', 'ALIAS', 'COMPLEX', 'AREA', 'STATUS', 'geometry']

    def test_maps_renamed_fields(self, captured_gdf):
        row = captured_gdf['gdf'].iloc[0]
        assert row['FIRE_NUMBE'] == 'WWF-017-2026'
        assert row['ALIAS'] == 'Some Lake'
        assert row['COMPLEX'] == 'Some Complex'
        assert row['AREA'] == 51.5
        assert row['STATUS'] == 'Under Control'

    def test_sets_wgs84_crs(self, captured_gdf):
        assert captured_gdf['gdf'].crs.to_epsg() == 4326

    def test_raises_when_no_features_returned(self, monkeypatch):
        monkeypatch.setattr(downloads.requests, 'get',
                            lambda url: self._response([]))
        with pytest.raises(ValueError, match="No features"):
            downloads.fetch_AB()


class TestFetchUSReplicaValidation:
    """fetch_US must surface a clear error instead of silently swallowing a
    failed or incomplete async replica export (the old bare-except behaviour)."""

    def _resp(self, payload):
        class FakeResponse:
            def json(self_inner):
                return payload
        return FakeResponse()

    def test_raises_when_no_status_url(self, monkeypatch):
        monkeypatch.setattr(downloads.requests, 'post',
                            lambda url, data=None: self._resp({}))
        with pytest.raises(ValueError, match="no statusUrl"):
            downloads.fetch_US()

    def test_raises_when_export_not_completed(self, monkeypatch):
        monkeypatch.setattr(downloads.requests, 'post',
                            lambda url, data=None: self._resp({'statusUrl': 'http://x/job'}))
        monkeypatch.setattr(downloads.requests, 'get',
                            lambda url, params=None: self._resp({'status': 'Failed'}))
        with pytest.raises(ValueError, match="did not complete"):
            downloads.fetch_US()


class TestMergeFiresWithPerimeters:
    """Point-to-polygon assignment, independent of any network access."""

    def _perimeters(self):
        # Built in 4326 then projected to 3978 (metres), which the merge and
        # its nearby-search helper require.
        return gpd.GeoDataFrame(
            {'UID': ['P1', 'P2']},
            geometry=[_square(-72.0, 48.5), _square(-71.0, 48.5)],
            crs='EPSG:4326',
        ).to_crs(3978)

    def test_each_fire_matches_its_covering_polygon(self):
        perim = self._perimeters()
        meta = gpd.GeoDataFrame(
            {'firename': ['F1', 'F2'], 'agency': ['QC', 'QC'],
             'hectares': [10.0, 20.0], 'stage_of_c': ['OC', 'UC']},
            geometry=[Point(-72.0, 48.5), Point(-71.0, 48.5)],
            crs='EPSG:4326',
        )

        merged = downloads.merge_fires_with_perimeters(meta, perim, CA_KEYS)

        assert len(merged) == 2
        assert dict(zip(merged['UID'], merged['firename'])) == {'P1': 'F1', 'P2': 'F2'}

    def test_fire_with_no_nearby_polygon_is_dropped(self):
        perim = self._perimeters()
        meta = gpd.GeoDataFrame(
            {'firename': ['F1', 'FAR'], 'agency': ['QC', 'QC'],
             'hectares': [10.0, 5.0], 'stage_of_c': ['OC', 'OC']},
            # FAR sits hundreds of km away, outside the 1km search radius.
            geometry=[Point(-72.0, 48.5), Point(-60.0, 45.0)],
            crs='EPSG:4326',
        )

        merged = downloads.merge_fires_with_perimeters(meta, perim, CA_KEYS)

        assert set(merged['firename']) == {'F1'}
