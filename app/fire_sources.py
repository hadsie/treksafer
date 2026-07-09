"""Realtime fire data sources.

A fire source is a pair of ArcGIS layers: incident points (one row per
fire, carrying its attributes) and perimeters (mapped fire outlines). The
points decide which fires exist; a matching perimeter replaces the point
so distance is measured to the fire's edge. The layers are joined on a
shared fire-number field or, when the perimeters carry no such field, by
location. Callers just call fetch_fires(); HTTP lives in arcgis.py.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import geopandas as gpd
import pandas as pd
from requests import RequestException

from .arcgis import envelope_filter, query_layer, radius_filter
from .config import RealtimeFireConfig

# A point within this many true meters of a polygon counts as the same fire.
_SPATIAL_JOIN_M = 1000
# Circle radius in true meters for fires that report no size.
_MIN_CIRCLE_M = 100


def _size_circle(point_3857, hectares, latitude):
    """Build a circle around a point, sized to the fire's reported area.

    Fires with no reported size get a minimal circle, keeping results to a
    single geometry type. The radius is stretched by 1/cos(latitude) to
    match how EPSG:3857 stretches distances.
    """
    try:
        hectares = float(hectares)
    except (TypeError, ValueError):
        hectares = 0
    if math.isnan(hectares):
        hectares = 0
    radius = math.sqrt(hectares * 10_000 / math.pi) if hectares > 0 else _MIN_CIRCLE_M
    return point_3857.buffer(radius / math.cos(math.radians(latitude)))


def spatial_merge(points: gpd.GeoDataFrame, perimeters: gpd.GeoDataFrame,
                  size_field: Optional[str], unmatched: str = 'buffer'):
    """Give each point a polygon geometry, matching the two by location.

    A point takes the polygon it falls inside, or the nearest one within
    _SPATIAL_JOIN_M meters; no shared identifier is needed. Unmatched
    points get a circle sized from the fire's reported area ('buffer') or
    are discarded ('drop'). Polygons never add rows of their own.

    Args:
        points: Fire points with attribute columns, in EPSG:4326 (lat/lon)
        perimeters: Polygons in EPSG:4326
        size_field: Points column holding the fire size in hectares
        unmatched: 'buffer' or 'drop'

    Returns:
        Tuple of (points with their new geometry, in the meter-based
        EPSG:3857, and the index labels of the matched polygons).
    """
    latitudes = points.geometry.y
    points_m = points.to_crs(epsg=3857)
    perimeters_m = perimeters.to_crs(epsg=3857)

    used: set = set()
    keep, geometries = [], []
    for (idx, row), latitude in zip(points_m.iterrows(), latitudes):
        threshold = _SPATIAL_JOIN_M / math.cos(math.radians(latitude))
        match = None
        if not perimeters_m.empty:
            distances = perimeters_m.geometry.distance(row.geometry)
            nearest = distances.idxmin()
            if distances[nearest] <= threshold:
                match = nearest
        if match is not None:
            keep.append(idx)
            geometries.append(perimeters_m.geometry[match])
            used.add(match)
        elif unmatched == 'buffer':
            hectares = row.get(size_field) if size_field else None
            keep.append(idx)
            geometries.append(_size_circle(row.geometry, hectares, latitude))

    merged = points_m.loc[keep].copy()
    merged.geometry = geometries
    return merged, used


def _points_fields(config: RealtimeFireConfig) -> list[str]:
    """Columns to request from the points layer: mapped fields plus the join field."""
    fields = list(config.mapping.values())
    if config.join_field and config.join_field not in fields:
        fields.append(config.join_field)
    return fields


def _points_by_fire_number(fire_numbers, config: RealtimeFireConfig) -> Optional[gpd.GeoDataFrame]:
    """Fetch points-layer records for specific fire numbers.

    Returns None when the query fails.
    """
    quoted = "','".join(str(n).replace("'", "''") for n in fire_numbers)
    where = f"({config.points_where}) AND {config.join_field} IN ('{quoted}')"
    try:
        return query_layer(config.points_url, {}, _points_fields(config),
                           config.cache_timeout, where)
    except (RequestException, ValueError) as e:
        logging.warning(
            f"Recovery query for {len(fire_numbers)} unmatched fire perimeter(s) "
            f"failed; reporting radius results only: {e}"
        )
        return None


def _merge_by_field(points: gpd.GeoDataFrame, perimeters: gpd.GeoDataFrame,
                    config: RealtimeFireConfig) -> gpd.GeoDataFrame:
    """Join points and perimeters on a shared fire-number field."""
    fire_key = config.join_field
    perimeters = perimeters.rename(columns={config.perimeter_fire_field: fire_key})
    perimeters = perimeters.drop_duplicates(subset=fire_key)

    # Perimeters with no matching point belong to fires whose report point
    # lies outside the queried radius; fetch those records by fire number.
    missing = perimeters.loc[~perimeters[fire_key].isin(points[fire_key]), fire_key]
    if not missing.empty:
        # Fetch the fire records for the fire numbers we're missing.
        recovered = _points_by_fire_number(missing, config)
        if recovered is not None:
            if points.empty:
                points = recovered
            elif not recovered.empty:
                points = gpd.GeoDataFrame(pd.concat([points, recovered]), crs=points.crs)
            # Fire numbers still absent are not in the points layer at all
            # (stale perimeters); the join below drops them.
            stale = missing[~missing.isin(points[fire_key])]
            if not stale.empty:
                logging.warning(
                    f"Fire perimeters with no incident record (excluded): {', '.join(stale.astype(str))}"
                )

    if points.empty:
        return points.to_crs(epsg=3857)

    # Left join: every point survives, taking its perimeter's polygon when
    # one matches and keeping its own point geometry otherwise.
    merged = points.merge(perimeters[[fire_key, 'geometry']], on=fire_key,
                          how='left', suffixes=('', '_perimeter'))
    geometry = merged['geometry_perimeter'].where(merged['geometry_perimeter'].notna(),
                                                  merged['geometry'])
    merged = merged.drop(columns=['geometry', 'geometry_perimeter'])
    gdf = gpd.GeoDataFrame(merged, geometry=gpd.GeoSeries(geometry, crs='EPSG:4326'))
    return gdf.to_crs(epsg=3857)


def _merge_spatial(points: gpd.GeoDataFrame, perimeters: gpd.GeoDataFrame,
                   config: RealtimeFireConfig) -> gpd.GeoDataFrame:
    """Join points and perimeters by location, recovering distant fire reports.

    A large fire's polygon can reach into the query radius while its point
    sits outside it, so unmatched polygons get one follow-up query for
    points within their bounding box. Polygons that still match nothing
    have no active fire record (stale data, or an agency excluded by
    points_where) and are logged and dropped. If the follow-up query fails,
    the radius results are returned as-is.
    """
    size_field = config.mapping.get('Size')
    fire_key = config.mapping['Fire']
    merged, used = spatial_merge(points, perimeters, size_field)

    unused = perimeters.loc[~perimeters.index.isin(used)]
    if unused.empty:
        return merged

    try:
        extra = query_layer(config.points_url, envelope_filter(unused.total_bounds),
                            list(config.mapping.values()), config.cache_timeout,
                            config.points_where)
    except (RequestException, ValueError) as e:
        logging.warning(
            f"Recovery query for {len(unused)} unmatched fire perimeter(s) failed; "
            f"reporting radius results only: {e}"
        )
        return merged

    if not extra.empty and not points.empty:
        extra = extra[~extra[fire_key].isin(points[fire_key])]

    recovered, recovered_used = spatial_merge(extra, unused, size_field, unmatched='drop')
    leftover = len(unused) - len(recovered_used)
    if leftover:
        logging.warning(
            f"{leftover} fire perimeter(s) in range have no active fire record "
            f"(stale or excluded agency); dropped."
        )
    if recovered.empty:
        return merged
    if merged.empty:
        return recovered
    return gpd.GeoDataFrame(pd.concat([merged, recovered]), crs=merged.crs)


def fetch_fires(config: RealtimeFireConfig, coords: tuple,
                radius_km: float) -> Optional[gpd.GeoDataFrame]:
    """Fetch fires within radius_km of coords from the realtime layers.

    Attributes come from the points layer. Geometry is the mapped perimeter
    where one exists; otherwise a circle of the reported size (location-
    joined sources) or the incident point itself (field-joined sources).
    Returned in the meter-based EPSG:3857 the search pipeline uses.

    :param RealtimeFireConfig config: Realtime source configuration
    :param tuple coords: (latitude, longitude) in WGS84
    :param float radius_km: Search radius in kilometers
    :return: GeoDataFrame of fires, or None when the source is unavailable so
        the caller can fall back to downloaded data
    """
    spatial_filter = radius_filter(coords, radius_km)
    perimeter_fields = [config.perimeter_fire_field] if config.join == 'field' else []
    try:
        points = query_layer(config.points_url, spatial_filter,
                             _points_fields(config), config.cache_timeout,
                             config.points_where)
        perimeters = query_layer(config.perimeters_url, spatial_filter,
                                 perimeter_fields, config.cache_timeout)
        if config.join == 'field':
            return _merge_by_field(points, perimeters, config)
        return _merge_spatial(points, perimeters, config)
    except (RequestException, ValueError) as e:
        logging.warning(f"Realtime fire query failed: {e}")
        return None
