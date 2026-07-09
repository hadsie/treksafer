"""Transport client for ArcGIS FeatureServer layers.

Knows the ArcGIS query protocol and its quirks -- spatial filters, errors
delivered inside HTTP 200 responses, the server-side cap on features per
response -- and returns results as GeoDataFrames (tables with a geometry
column). It knows nothing about fires; that logic lives in fire_sources.py.
"""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import requests
import requests_cache

_REQUEST_TIMEOUT = 30
# Upper bound on pages per fetch_layer call, guarding against servers whose
# truncation flag never clears (e.g. resultOffset silently ignored).
_MAX_PAGES = 100


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


def radius_filter(coords: tuple, radius_km: float) -> dict:
    """Query params selecting features within a radius of a lat/lon point."""
    return {
        'geometry': f'{coords[1]},{coords[0]}',
        'geometryType': 'esriGeometryPoint',
        'inSR': 4326,
        'distance': radius_km,
        'units': 'esriSRUnit_Kilometer',
    }


def envelope_filter(bounds) -> dict:
    """Query params selecting features within a lat/lon bounding box.

    Bounds are (min lon, min lat, max lon, max lat), the order GeoDataFrame
    total_bounds returns.
    """
    return {
        'geometry': ','.join(str(round(float(b), 6)) for b in bounds),
        'geometryType': 'esriGeometryEnvelope',
        'inSR': 4326,
    }


def _get_payload(session, url: str, params: dict) -> dict:
    """Run a query and return the payload, raising on any ArcGIS failure.

    ArcGIS reports failures as HTTP 200 responses carrying an error body, so
    the body must be checked as well as the status code.

    Args:
        session: requests.Session (or CachedSession) to make the request with
        url: Full URL to request
        params: Query-string parameters
    """
    response = session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if 'error' in payload:
        raise ValueError(f"ArcGIS error from {url}: {payload['error']}")
    return payload


def _truncated(payload: dict) -> bool:
    return bool(payload.get('exceededTransferLimit')
                or payload.get('properties', {}).get('exceededTransferLimit'))


def _to_gdf(features: list, out_fields: list[str]) -> gpd.GeoDataFrame:
    if not features:
        return gpd.GeoDataFrame(columns=[*out_fields, 'geometry'], geometry='geometry', crs='EPSG:4326')
    return gpd.GeoDataFrame.from_features(features, crs='EPSG:4326')


def query_layer(url: str, spatial_filter: dict, out_fields: list[str],
                cache_timeout: int, where: str = '1=1') -> gpd.GeoDataFrame:
    """Run a spatially filtered query against a FeatureServer layer.

    Raises on any failure. That includes a truncated response (the server
    capped the number of features returned): silently passing along an
    incomplete result must never happen, since the caller would treat it
    as the full picture.

    Args:
        url: The layer's /query endpoint
        spatial_filter: Geometry params from radius_filter()/envelope_filter(),
            or {} for no geographic restriction
        out_fields: Attribute columns to return; [] returns only the object ID
        cache_timeout: Seconds to cache the response for
        where: SQL-style attribute filter, e.g. "Agency NOT IN ('BC','AB')"
    """
    params = {
        **spatial_filter,
        'where': where,
        'outSR': 4326,
        'f': 'geojson',
    }
    if out_fields:
        params['outFields'] = ','.join(out_fields)
    payload = _get_payload(_session(cache_timeout), url, params)
    if _truncated(payload):
        raise ValueError(f"ArcGIS query to {url} exceeded the transfer limit; results are truncated")
    return _to_gdf(payload.get('features', []), out_fields)


def _object_id_field(session, query_url: str) -> str:
    """Return the layer's object ID field name from its metadata.

    Every ArcGIS layer has a unique-ID column, but its name varies by
    layer. Paginated fetches sort by it because ArcGIS does not promise a
    stable order otherwise, and unordered pages could repeat or skip
    features across page boundaries.
    """
    layer_url = query_url.rsplit('/query', 1)[0]
    payload = _get_payload(session, layer_url, {'f': 'json'})
    oid_field = payload.get('objectIdField') or next(
        (f['name'] for f in payload.get('fields', [])
         if f.get('type') == 'esriFieldTypeOID'),
        None,
    )
    if not oid_field:
        raise ValueError(f"Could not determine the object ID field for {layer_url}")
    return oid_field


def fetch_layer(url: str, out_fields: list[str], where: str = '1=1') -> gpd.GeoDataFrame:
    """Fetch every feature from a layer.

    A single response is capped by the server, so results are requested
    page by page, sorted by the layer's object ID to keep the pages stable.
    Meant for full downloads (e.g. the daily recovery file) rather than
    request-time queries, so responses are not cached.

    Args:
        url: The layer's /query endpoint
        out_fields: Attribute columns to return; [] returns only the object ID
        where: SQL-style attribute filter, e.g. "Agency NOT IN ('BC','AB')"
    """
    session = requests.Session()
    oid_field = _object_id_field(session, url)
    features: list = []
    for _ in range(_MAX_PAGES):
        params = {
            'where': where,
            'outSR': 4326,
            'f': 'geojson',
            'orderByFields': oid_field,
            'resultOffset': len(features),
        }
        if out_fields:
            params['outFields'] = ','.join(out_fields)
        payload = _get_payload(session, url, params)
        page = payload.get('features', [])
        features += page
        if not _truncated(payload):
            return _to_gdf(features, out_fields)
        if not page:
            raise ValueError(f"{url} reports more features but returned an empty page")
    raise ValueError(f"{url} still reports more features after {_MAX_PAGES} pages")
