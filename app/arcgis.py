"""Realtime fire data from ArcGIS FeatureServer layers.

Fires are queried at request time with a point-radius spatial filter against
two layers: an incident points layer (attribute source of truth, includes new
fires that have no mapped perimeter yet) and a perimeters layer (polygon
geometry for accurate distance/bearing). Caching is self-contained; callers
just call fetch_fires().
"""
from __future__ import annotations

import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

import geopandas as gpd
import requests_cache
from requests import RequestException

from .config import RealtimeFireConfig

_REQUEST_TIMEOUT = 30


@lru_cache(maxsize=None)
def _session(cache_timeout: int) -> requests_cache.CachedSession:
    cache_dir = Path('cache')
    cache_dir.mkdir(exist_ok=True)
    return requests_cache.CachedSession(
        cache_name=str(cache_dir / 'arcgis_fires'),
        expire_after=timedelta(seconds=cache_timeout),
        allowable_methods=['GET'],
        stale_if_error=True,
    )


def _query(url: str, coords: tuple, radius_km: float, out_fields: list[str],
           cache_timeout: int) -> gpd.GeoDataFrame:
    """Run a point-radius query against a FeatureServer layer.

    Raises on any failure, including HTTP 200 responses carrying an ArcGIS
    error body or a truncation flag: partial fire data must never pass
    silently.
    """
    params = {
        'geometry': f'{coords[1]},{coords[0]}',
        'geometryType': 'esriGeometryPoint',
        'inSR': 4326,
        'distance': radius_km,
        'units': 'esriSRUnit_Kilometer',
        'where': '1=1',
        'outFields': ','.join(out_fields),
        'outSR': 4326,
        'f': 'geojson',
    }
    response = _session(cache_timeout).get(url, params=params, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if 'error' in payload:
        raise ValueError(f"ArcGIS error from {url}: {payload['error']}")
    if payload.get('exceededTransferLimit') or payload.get('properties', {}).get('exceededTransferLimit'):
        raise ValueError(f"ArcGIS query to {url} exceeded the transfer limit; results are truncated")

    features = payload.get('features', [])
    if not features:
        return gpd.GeoDataFrame(columns=[*out_fields, 'geometry'], geometry='geometry', crs='EPSG:4326')
    return gpd.GeoDataFrame.from_features(features, crs='EPSG:4326')


def fetch_fires(config: RealtimeFireConfig, coords: tuple,
                radius_km: float) -> Optional[gpd.GeoDataFrame]:
    """Fetch fires within radius_km of coords from the realtime layers.

    Attributes come from the points layer; geometry is the perimeter polygon
    where one exists and the incident point otherwise. Returned in EPSG:3857
    to match the search pipeline.

    :param RealtimeFireConfig config: Realtime source configuration
    :param tuple coords: (latitude, longitude) in WGS84
    :param float radius_km: Search radius in kilometers
    :return: GeoDataFrame of fires, or None when the source is unavailable so
        the caller can fall back to downloaded data
    """
    fire_key = config.mapping['Fire']
    perimeter_key = config.perimeter_fire_field
    try:
        points = _query(config.points_url, coords, radius_km,
                        list(config.mapping.values()), config.cache_timeout)
        perimeters = _query(config.perimeters_url, coords, radius_km,
                            [perimeter_key], config.cache_timeout)
    except (RequestException, ValueError) as e:
        logging.warning(f"Realtime fire query failed: {e}")
        return None

    perimeters = perimeters.rename(columns={perimeter_key: fire_key})

    if points.empty:
        return points.to_crs(epsg=3857)

    unmatched = perimeters.loc[~perimeters[fire_key].isin(points[fire_key]), fire_key]
    if not unmatched.empty:
        logging.warning(
            f"Fire perimeters with no incident record (excluded): {', '.join(unmatched.astype(str))}"
        )

    perimeters = perimeters.drop_duplicates(subset=fire_key)
    merged = points.merge(perimeters[[fire_key, 'geometry']], on=fire_key,
                          how='left', suffixes=('', '_perimeter'))
    geometry = merged['geometry_perimeter'].where(merged['geometry_perimeter'].notna(),
                                                  merged['geometry'])
    merged = merged.drop(columns=['geometry', 'geometry_perimeter'])
    gdf = gpd.GeoDataFrame(merged, geometry=gpd.GeoSeries(geometry, crs='EPSG:4326'))
    return gdf.to_crs(epsg=3857)
