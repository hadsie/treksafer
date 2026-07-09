"""Unit tests for the fire data downloader (scripts/downloads.py).

The HTTP boundary is mocked so no network calls happen. fetch_CA is covered
by mocking the arcgis layer fetches it delegates to; the spatial merge logic
itself is tested in tests/test_arcgis.py.
"""

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from scripts import downloads


def _square(cx, cy, half=0.02):
    """A small square polygon centered on (cx, cy) in lon/lat degrees."""
    return Polygon([
        (cx - half, cy - half), (cx + half, cy - half),
        (cx + half, cy + half), (cx - half, cy + half),
    ])


class TestFetchCA:
    """fetch_CA saves the realtime CA source, merged the same way requests
    use it, as the recovery file for API outages."""

    def _points(self):
        return gpd.GeoDataFrame(
            {
                'Fire_Name': ['2026_SK_F1', '2026_ON_F2'],
                'Agency': ['SK', 'ON'],
                'Hectares__Ha_': [10.0, 250.0],
                'Stage_of_Control': ['OC', 'UC'],
                'Start_Date': [1782000000000, 1782000000000],
            },
            geometry=[Point(-105.0, 55.0), Point(-79.0, 48.0)],
            crs='EPSG:4326',
        )

    def _perimeters(self):
        # Covers F1 only; F2 must get a synthesized circle.
        return gpd.GeoDataFrame(
            {'UID': ['P1']}, geometry=[_square(-105.0, 55.0)], crs='EPSG:4326',
        )

    @pytest.fixture
    def captured(self, monkeypatch):
        calls = []

        def fake_fetch_layer(url, out_fields, where='1=1'):
            calls.append({'url': url, 'out_fields': out_fields, 'where': where})
            return self._perimeters() if 'Perimeter' in url else self._points()

        monkeypatch.setattr(downloads, 'fetch_layer', fake_fetch_layer)
        result = {}
        monkeypatch.setattr(downloads, 'write_shapefile',
                            lambda location, gdf: result.update(location=location, gdf=gdf))
        downloads.fetch_CA()
        result['calls'] = calls
        return result

    def test_writes_legacy_column_names(self, captured):
        assert list(captured['gdf'].columns) == [
            'firename', 'agency', 'hectares', 'stage_of_c', 'geometry']

    def test_excludes_bc_and_ab_server_side(self, captured):
        assert "NOT IN ('BC','AB')" in captured['calls'][0]['where']

    def test_matched_fire_gets_perimeter_polygon(self, captured):
        gdf = captured['gdf']
        f1 = gdf[gdf['firename'] == '2026_SK_F1'].iloc[0]
        assert f1.geometry.geom_type == 'Polygon'
        assert f1.geometry.area > 0

    def test_unmatched_fire_gets_size_circle(self, captured):
        gdf = captured['gdf']
        f2 = gdf[gdf['firename'] == '2026_ON_F2'].iloc[0]
        assert f2.geometry.geom_type == 'Polygon'

    def test_writes_wgs84(self, captured):
        assert captured['gdf'].crs.to_epsg() == 4326

    def test_raises_when_no_fires_returned(self, monkeypatch):
        empty = gpd.GeoDataFrame(columns=['Fire_Name', 'geometry'],
                                 geometry='geometry', crs='EPSG:4326')
        monkeypatch.setattr(downloads, 'fetch_layer',
                            lambda url, out_fields, where='1=1': empty)
        with pytest.raises(ValueError, match="No fires"):
            downloads.fetch_CA()


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


class TestFetchUS:
    """fetch_US saves the realtime WFIGS source as the recovery file, with
    sizes converted to hectares and the shapefile column names config reads."""

    def _points(self):
        return gpd.GeoDataFrame(
            {
                'IncidentName': ['Snake River', 'Lava Spot'],
                'IncidentShortDescription': ['10 Miles N from Jackson, WY', None],
                'IncidentSize': [5000.0, None],
                'PercentContained': [0.0, None],
                'IncidentTypeCategory': ['WF', 'RX'],
                'FireDiscoveryDateTime': [1782000000000, 1782000000000],
            },
            geometry=[Point(-110.0, 44.0), Point(-110.5, 44.2)],
            crs='EPSG:4326',
        )

    def _perimeters(self):
        # Covers Snake River only; Lava Spot must get a synthesized circle.
        return gpd.GeoDataFrame(
            {'attr_IrwinID': ['{AAA}']}, geometry=[_square(-110.0, 44.0)], crs='EPSG:4326',
        )

    @pytest.fixture
    def captured(self, monkeypatch):
        def fake_fetch_layer(url, out_fields, where='1=1'):
            return self._perimeters() if 'Perimeters' in url else self._points()

        monkeypatch.setattr(downloads, 'fetch_layer', fake_fetch_layer)
        result = {}
        monkeypatch.setattr(downloads, 'write_shapefile',
                            lambda location, gdf: result.update(location=location, gdf=gdf))
        downloads.fetch_US()
        return result

    def test_writes_config_column_names(self, captured):
        assert list(captured['gdf'].columns) == [
            'FIRE_NAME', 'LOCATION', 'SIZE_HA', 'PCT_CONT',
            'INCID_TYPE', 'DISCOVERED', 'geometry']

    def test_converts_acres_to_hectares(self, captured):
        gdf = captured['gdf']
        snake = gdf[gdf['FIRE_NAME'] == 'Snake River'].iloc[0]
        assert snake['SIZE_HA'] == 2023.43

    def test_all_geometry_is_polygonal(self, captured):
        assert set(captured['gdf'].geom_type) == {'Polygon'}

    def test_writes_wgs84(self, captured):
        assert captured['gdf'].crs.to_epsg() == 4326

    def test_raises_when_no_fires_returned(self, monkeypatch):
        empty = gpd.GeoDataFrame(columns=['IncidentName', 'geometry'],
                                 geometry='geometry', crs='EPSG:4326')
        monkeypatch.setattr(downloads, 'fetch_layer',
                            lambda url, out_fields, where='1=1': empty)
        with pytest.raises(ValueError, match="No fires"):
            downloads.fetch_US()
