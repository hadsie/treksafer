"""Tests for FindFires source loading (realtime vs downloaded)."""

from unittest.mock import patch

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.config import get_config, RealtimeFireConfig
from app.fires import FindFires

BC_COORDS = (50.7021714, -121.9725246)

REALTIME = RealtimeFireConfig(
    enabled=True,
    points_url='https://example.test/points/query',
    perimeters_url='https://example.test/perims/query',
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


def realtime_settings(enabled=True):
    """Settings copy where BC is the only source and has realtime enabled."""
    settings = get_config().model_copy(deep=True)
    bc = next(df for df in settings.data if df.location == 'BC')
    bc.realtime = REALTIME.model_copy(update={'enabled': enabled})
    settings.data = [bc]
    return settings


def realtime_gdf(lat, lon, status='Out of Control'):
    """A single-fire GeoDataFrame as fetch_fires would return it."""
    return gpd.GeoDataFrame(
        {
            'FIRE_NUMBER': ['K1'],
            'INCIDENT_NAME': ['Test Fire'],
            'GEOGRAPHIC_DESCRIPTION': ['Test Creek'],
            'CURRENT_SIZE': [25.0],
            'FIRE_STATUS': [status],
        },
        geometry=gpd.GeoSeries([Point(lon, lat)], crs='EPSG:4326'),
    ).to_crs(epsg=3857)


class TestLoadSource:
    def test_realtime_success_uses_realtime_mapping(self):
        gdf = realtime_gdf(*BC_COORDS)
        with patch('app.fires.get_config', return_value=realtime_settings()), \
             patch('app.fires.fetch_fires', return_value=gdf) as mock_fetch:
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            data_file = ff.settings.data[0]
            fires_gdf, effective = ff._load_source(data_file, {})

        assert fires_gdf is gdf
        assert effective.mapping == {'fields': REALTIME.mapping}
        assert effective.status_map == REALTIME.status_map
        mock_fetch.assert_called_once_with(data_file.realtime, BC_COORDS, 20)

    def test_realtime_failure_falls_back_to_downloaded_file(self, caplog):
        with patch('app.fires.get_config', return_value=realtime_settings()), \
             patch('app.fires.fetch_fires', return_value=None):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            data_file = ff.settings.data[0]
            fires_gdf, effective = ff._load_source(data_file, ff.sources_map())

        assert fires_gdf is not None
        assert not fires_gdf.empty
        assert effective is data_file
        assert 'using downloaded data' in caplog.text

    def test_realtime_disabled_never_queries_api(self):
        with patch('app.fires.get_config', return_value=realtime_settings(enabled=False)), \
             patch('app.fires.fetch_fires') as mock_fetch:
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            ff._load_source(ff.settings.data[0], ff.sources_map())

        mock_fetch.assert_not_called()

    def test_no_realtime_and_no_file_returns_none(self):
        with patch('app.fires.get_config', return_value=realtime_settings(enabled=False)):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 20})
            fires_gdf, _ = ff._load_source(ff.settings.data[0], {})

        assert fires_gdf is None

    def test_radius_capped_at_max_radius(self):
        with patch('app.fires.get_config', return_value=realtime_settings()), \
             patch('app.fires.fetch_fires', return_value=realtime_gdf(*BC_COORDS)) as mock_fetch:
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 9999})
            ff._load_source(ff.settings.data[0], {})

        radius = mock_fetch.call_args.args[2]
        assert radius == ff.settings.max_radius


class TestNearbyRealtime:
    def test_nearby_returns_normalized_realtime_fire(self):
        """End to end: a realtime fire is normalized, statused, and sorted."""
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1])
        with patch('app.fires.get_config', return_value=realtime_settings()), \
             patch('app.fires.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'all', 'distance': 50, 'size': 0})
            fires = ff.nearby()

        assert len(fires) == 1
        fire = fires[0]
        assert fire['Fire'] == 'K1'
        assert fire['Name'] == 'Test Fire'
        assert fire['Location'] == 'Test Creek'
        assert fire['Size'] == 25.0
        assert fire['Status'] == 'Out of Control'
        assert fire['StatusLevel'] == 1
        assert fire['Distance'] > 0
        assert fire['Direction']

    def test_nearby_status_filter_applies_to_realtime_fires(self):
        gdf = realtime_gdf(BC_COORDS[0] + 0.05, BC_COORDS[1], status='Out')
        with patch('app.fires.get_config', return_value=realtime_settings()), \
             patch('app.fires.fetch_fires', return_value=gdf):
            ff = FindFires(BC_COORDS, filters={'status': 'active', 'distance': 50, 'size': 0})
            fires = ff.nearby()

        assert fires == []
