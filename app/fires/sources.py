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

from ..arcgis import envelope_filter, fetch_layer, query_layer, radius_filter
from ..config import RealtimeFireConfig

# A point within this many true meters of a polygon counts as the same fire.
_SPATIAL_JOIN_M = 1000
# A fire's hotspot perimeter can be several disconnected burn patches while
# the fire has a single point; an unclaimed polygon within this many true
# meters of a matched fire is treated as one of its patches.
_FRAGMENT_ASSOC_M = 20_000
# Circle radius in true meters for fires that report no size.
_MIN_CIRCLE_M = 100


def _size_circle(point_3857, hectares, latitude):
    """Build a circle around a point, sized to the fire's reported area.

    Fires with no reported size get a minimal circle, keeping results to a
    single geometry type. The radius is stretched by 1/cos(latitude) to
    counter EPSG:3857's stretch, so the circle's ground size is true once
    the search path reprojects it.
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
                  size_field: Optional[str], unmatched: str = 'buffer',
                  threshold_m: float = _SPATIAL_JOIN_M):
    """Give each point a polygon geometry, matching the two by location.

    A point takes the polygon it falls inside, or the nearest one within
    threshold_m meters; no shared identifier is needed. Unmatched points
    get a circle sized from the fire's reported area ('buffer') or are
    discarded ('drop'). Polygons left unclaimed are then treated as
    detached burn patches: each one within _FRAGMENT_ASSOC_M of a matched
    fire is unioned into that fire's geometry. Polygons never add rows of
    their own.

    Args:
        points: Fire points with attribute columns, in EPSG:4326 (lat/lon)
        perimeters: Polygons in EPSG:4326
        size_field: Points column holding the fire size in hectares
        unmatched: 'buffer' or 'drop'
        threshold_m: How close a point must be to a polygon to claim it

    Returns:
        Tuple of (points with their new geometry, in the meter-based
        EPSG:3857, and the index labels of the polygons used).
    """
    latitudes = points.geometry.y
    points_m = points.to_crs(epsg=3857)
    perimeters_m = perimeters.to_crs(epsg=3857)

    used: set = set()
    keep, geometries, kept_latitudes = [], [], []
    for (idx, row), latitude in zip(points_m.iterrows(), latitudes):
        threshold = threshold_m / math.cos(math.radians(latitude))
        match = None
        if not perimeters_m.empty:
            distances = perimeters_m.geometry.distance(row.geometry)
            nearest = distances.idxmin()
            if distances[nearest] <= threshold:
                match = nearest
        if match is not None:
            keep.append(idx)
            geometries.append(perimeters_m.geometry[match])
            kept_latitudes.append(latitude)
            used.add(match)
        elif unmatched == 'buffer':
            hectares = row.get(size_field) if size_field else None
            keep.append(idx)
            geometries.append(_size_circle(row.geometry, hectares, latitude))
            kept_latitudes.append(latitude)

    # Attach detached burn patches to the nearest matched fire.
    associated = 0
    if geometries:
        for perim_idx in perimeters_m.index:
            if perim_idx in used:
                continue
            patch = perimeters_m.geometry[perim_idx]
            distances = [geom.distance(patch) for geom in geometries]
            nearest = distances.index(min(distances))
            threshold = _FRAGMENT_ASSOC_M / math.cos(math.radians(kept_latitudes[nearest]))
            if distances[nearest] <= threshold:
                geometries[nearest] = geometries[nearest].union(patch)
                used.add(perim_idx)
                associated += 1
    if associated:
        logging.info(f"{associated} detached burn patch(es) merged into nearby fires.")

    merged = points_m.loc[keep].copy()
    merged.geometry = geometries
    return merged, used


def _points_fields(config: RealtimeFireConfig) -> list[str]:
    """Columns to request from the points layer: mapped fields plus the
    join, identity-key, and update-timestamp fields."""
    fields = list(config.mapping.values())
    extras = [config.join_field, *config.key_fields, config.updated_field]
    fields += [f for f in extras if f and f not in fields]
    return fields


def _expanded_bounds(gdf: gpd.GeoDataFrame, meters: float):
    """Grow a frame's lat/lon bounds by a true-meter margin on every side."""
    minx, miny, maxx, maxy = gdf.total_bounds
    dlat = meters / 111_000
    dlon = meters / (111_000 * math.cos(math.radians((miny + maxy) / 2)))
    return (minx - dlon, miny - dlat, maxx + dlon, maxy + dlat)


def _stash_report_point(points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Preserve each fire's report location as columns before merging can
    replace the point geometry with a perimeter polygon."""
    points = points.copy()
    points['latitude'] = points.geometry.y
    points['longitude'] = points.geometry.x
    return points


def _points_by_fire_number(fire_numbers, config: RealtimeFireConfig) -> Optional[gpd.GeoDataFrame]:
    """Fetch points-layer records for specific fire numbers.

    Returns None when the query fails.
    """
    quoted = "','".join(str(n).replace("'", "''") for n in fire_numbers)
    where = f"({config.points_where}) AND {config.join_field} IN ('{quoted}')"
    try:
        return _stash_report_point(
            query_layer(config.points_url, {}, _points_fields(config),
                        config.cache_timeout, where))
    except (RequestException, ValueError) as e:
        logging.warning(
            f"Recovery query for {len(fire_numbers)} unmatched fire perimeter(s) "
            f"failed; reporting radius results only: {e}"
        )
        return None


def _merge_by_field(points: gpd.GeoDataFrame, perimeters: gpd.GeoDataFrame,
                    config: RealtimeFireConfig, recover: bool = True) -> gpd.GeoDataFrame:
    """Join points and perimeters on a shared fire-number field."""
    fire_key = config.join_field
    perimeters = perimeters.rename(columns={config.perimeter_fire_field: fire_key})
    perimeters = perimeters.drop_duplicates(subset=fire_key)

    # Perimeters with no matching point belong to fires whose report point
    # lies outside the queried radius; fetch those records by fire number.
    # Full-layer callers pass recover=False: every point is already present,
    # so unmatched perimeters are stale by definition.
    missing = perimeters.loc[~perimeters[fire_key].isin(points[fire_key]), fire_key]
    if not missing.empty and not recover:
        logging.warning(
            f"Fire perimeters with no incident record (excluded): {', '.join(missing.astype(str))}"
        )
    elif not missing.empty:
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
    have no active fire record (stale data) and are logged and dropped.
    If the follow-up query fails, the radius results are returned as-is.
    """
    size_field = config.mapping.get('Size')
    fire_key = config.mapping['Fire']
    merged, used = spatial_merge(points, perimeters, size_field)

    unused = perimeters.loc[~perimeters.index.isin(used)]
    if unused.empty:
        return merged

    # The report point of a detached burn patch can sit well outside the
    # patch itself, so the recovery envelope grows by the association
    # distance and recovered points may claim a patch from that far away.
    try:
        extra = _stash_report_point(
            query_layer(config.points_url,
                        envelope_filter(_expanded_bounds(unused, _FRAGMENT_ASSOC_M)),
                        _points_fields(config), config.cache_timeout,
                        config.points_where))
    except (RequestException, ValueError) as e:
        logging.warning(
            f"Recovery query for {len(unused)} unmatched fire perimeter(s) failed; "
            f"reporting radius results only: {e}"
        )
        return merged

    if not extra.empty and not points.empty:
        extra = extra[~extra[fire_key].isin(points[fire_key])]

    recovered, recovered_used = spatial_merge(extra, unused, size_field, unmatched='drop',
                                              threshold_m=_FRAGMENT_ASSOC_M)
    leftover = len(unused) - len(recovered_used)
    if leftover:
        logging.warning(
            f"{leftover} fire perimeter(s) in range have no active fire record; dropped."
        )
    if recovered.empty:
        return merged
    if merged.empty:
        return recovered
    return gpd.GeoDataFrame(pd.concat([merged, recovered]), crs=merged.crs)


def fetch_fires_by_id(config: RealtimeFireConfig, term: str) -> Optional[gpd.GeoDataFrame]:
    """Fetch fires whose displayed identifier matches term, ignoring location.

    Matches term as a case-insensitive substring of the points layer's
    displayed-fire field (mapping['Fire']) across the whole source, then
    gives each match its perimeter geometry the same way the radius query
    does. Unlike the radius path, no location recovery is attempted: only
    the fires the identifier selected are returned, never extra rows.

    Returned in EPSG:3857, or None when the source is unavailable.
    """
    fire_field = config.mapping['Fire']
    safe = term.replace("'", "''")
    where = f"({config.points_where}) AND UPPER({fire_field}) LIKE UPPER('%{safe}%')"
    try:
        points = _stash_report_point(
            query_layer(config.points_url, {}, _points_fields(config),
                        config.cache_timeout, where))
    except (RequestException, ValueError) as e:
        logging.warning(f"Fire-ID query for {config.mapping['Fire']} failed: {e}")
        return None

    if points.empty:
        return points.to_crs(epsg=3857)

    try:
        if config.join == 'field':
            keys = points[config.join_field].dropna().unique()
            quoted = "','".join(str(k).replace("'", "''") for k in keys)
            pwhere = (f"({config.perimeters_where}) AND "
                      f"{config.perimeter_fire_field} IN ('{quoted}')")
            perimeters = query_layer(config.perimeters_url, {},
                                     [config.perimeter_fire_field],
                                     config.cache_timeout, pwhere)
            return _merge_by_field(points, perimeters, config, recover=False)
        # Spatial sources: pull the perimeters near the matched points and
        # join by location, buffering any point with no perimeter.
        perimeters = query_layer(config.perimeters_url,
                                 envelope_filter(_expanded_bounds(points, _SPATIAL_JOIN_M)),
                                 [], config.cache_timeout, config.perimeters_where)
        merged, _ = spatial_merge(points, perimeters, config.mapping.get('Size'))
        return merged
    except (RequestException, ValueError) as e:
        logging.warning(f"Fire-ID perimeter query for {config.mapping['Fire']} failed: {e}")
        return None


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
        points = _stash_report_point(points)
        perimeters = query_layer(config.perimeters_url, spatial_filter,
                                 perimeter_fields, config.cache_timeout,
                                 config.perimeters_where)
        if config.join == 'field':
            return _merge_by_field(points, perimeters, config)
        return _merge_spatial(points, perimeters, config)
    except (RequestException, ValueError) as e:
        logging.warning(f"Realtime fire query failed: {e}")
        return None


def fetch_all_fires(config: RealtimeFireConfig) -> gpd.GeoDataFrame:
    """Fetch a source's complete fire set (no spatial filter), merged.

    Used by the daily database refresh. The full points layer is present,
    so no recovery queries are needed; unmatched perimeters are stale and
    dropped with a warning. Raises on any query failure.
    """
    points = fetch_layer(config.points_url, _points_fields(config), config.points_where)
    points = _stash_report_point(points)
    perimeter_fields = [config.perimeter_fire_field] if config.join == 'field' else []
    perimeters = fetch_layer(config.perimeters_url, perimeter_fields, config.perimeters_where)
    if config.join == 'field':
        return _merge_by_field(points, perimeters, config, recover=False)
    merged, used = spatial_merge(points, perimeters, config.mapping.get('Size'))
    leftover = len(perimeters) - len(used)
    if leftover:
        logging.warning(f"{leftover} fire perimeter(s) have no active fire record; dropped.")
    return merged
