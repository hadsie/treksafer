# Realtime BC Fire Data

## Problem

BC fire responses are built from a daily zip
(`pub.data.gov.bc.ca/.../prot_current_fire_polys.zip`) that must be manually
downloaded, plus a per-fire enrichment API. Investigation on 2026-07-05 found:

- The zip is regenerated per-request from a warehouse table that only refreshes
  daily (max `LOAD_DATE` lagged 2 days behind). No `Last-Modified` header; zip
  entry timestamps are stamped at request time and are meaningless.
- The zip only contains fires with mapped perimeters (107 fires). New ignitions
  without a perimeter yet, which are the most safety-relevant, are absent.

## Solution

Query the BC Wildfire Service public ArcGIS FeatureServer layers at request
time with a point-radius spatial filter. Verified facts (2026-07-05):

| | Points layer | Perimeters layer |
|---|---|---|
| Service | `BCWS_ActiveFires_PublicView/FeatureServer/0` | `BCWS_FirePerimeters_PublicView/FeatureServer/0` |
| Host | `services6.arcgis.com/ubm4tcTYICKBpist/ArcGIS/rest/services` | same |
| Records | 419 (all current incidents) | 107 (matches the zip) |
| Freshness | `lastEditDate` minutes old, stable across requests (not request-stamped) | same |
| Key fields | `FIRE_NUMBER`, `INCIDENT_NAME`, `GEOGRAPHIC_DESCRIPTION`, `CURRENT_SIZE` (ha), `FIRE_STATUS` | `FIRE_NUMBER`, `FIRE_SIZE_HECTARES`, `FIRE_STATUS`, polygon |
| `maxRecordCount` | 1000 | 1000 |

`FIRE_STATUS` distinct values (both layers): `Out of Control`, `Fire of Note`,
`Being Held`, `Under Control`, `Out`.

### Verified query shape

```
GET {layer}/query
    geometry=<lon>,<lat>
    geometryType=esriGeometryPoint
    inSR=4326
    distance=<km>
    units=esriSRUnit_Kilometer
    where=1=1
    outFields=<comma-separated>
    outSR=4326
    f=geojson
```

Returns a GeoJSON FeatureCollection in EPSG:4326 that loads directly with
geopandas. Failures can arrive as HTTP 200 with an `{"error": ...}` JSON body,
so the body must be checked. `exceededTransferLimit: true` means truncated
results and must be treated as a failure (never silently report partial fire
data).

## Design

New module `app/arcgis.py`, self-contained per [ARCH-01] (own `CachedSession`,
callers just call `fetch_fires()`).

```
fetch_fires(config, coords, radius_km) -> Optional[GeoDataFrame]  # EPSG:3857
```

1. Query the points layer (attribute source of truth, includes point-only new
   fires) and the perimeters layer (polygon geometry) with the same
   point-radius filter.
2. Join on `FIRE_NUMBER` (the configured `Fire` mapping value): use the
   perimeter polygon where one exists, the incident point otherwise, so
   distance/bearing math against polygons stays exact.
3. Return `None` on any failure (network, error body, transfer limit) after
   logging a warning; the caller falls back to the newest downloaded file.

`FindFires.nearby()` gains a `_load_source()` step: realtime-enabled sources
fetch from the API and use the realtime field mapping (synthesized into a
`DataFile`, so `search()`/normalization/filtering are unchanged); on failure or
when disabled, the existing shapefile path runs as before. The realtime path
does not call the per-fire enrichment API (name/location/status all come from
the points layer), eliminating N sequential HTTP calls per request.

### Config

`DataFile` gains an optional `realtime` block (Pydantic `RealtimeFireConfig`):

```yaml
- location: BC
  filename: BC_Fire_Perimeters_{DATE}.zip   # fallback
  realtime:
    enabled: ${TREKSAFER_BC_REALTIME:-true}
    points_url: https://services6.arcgis.com/ubm4tcTYICKBpist/ArcGIS/rest/services/BCWS_ActiveFires_PublicView/FeatureServer/0/query
    perimeters_url: https://services6.arcgis.com/ubm4tcTYICKBpist/ArcGIS/rest/services/BCWS_FirePerimeters_PublicView/FeatureServer/0/query
    cache_timeout: 900
    mapping:
      Fire: FIRE_NUMBER
      Name: INCIDENT_NAME
      Location: GEOGRAPHIC_DESCRIPTION
      Size: CURRENT_SIZE
      Status: FIRE_STATUS
    status_map:
      active: ['Out of Control', 'Fire of Note']
      managed: ['Being Held']
      controlled: ['Under Control']
      out: ['Out']
  mapping: ...       # existing zip mapping, used only as fallback
  status_map: ...    # existing, used only as fallback
```

`mapping` must include `Fire` (join key); validated at config load. Cache TTL
900s: fresh enough for safety, protects the public endpoint from repeat
queries at the same coords.

`TREKSAFER_BC_REALTIME=false` in the test environment keeps unit tests on the
bundled shapefiles ([TEST-03]: no real network calls).

## Acceptance criteria

1. A BC request with realtime enabled returns fires from the ArcGIS layers,
   including point-only fires that have no perimeter polygon.
2. A fire present in both layers is reported with distance/bearing measured to
   its perimeter polygon, not its incident point.
3. All five `FIRE_STATUS` values map to the correct status level; unmapped
   values fall through to the existing log-and-treat-as-active behavior.
4. When the API is unreachable, returns an error body, or reports
   `exceededTransferLimit`, a warning is logged and the response is built from
   the newest downloaded BC file (identical to pre-feature behavior).
5. With `enabled: false` the realtime path is never attempted.
6. Responses are cached; a repeat query at the same coords within the TTL does
   not hit the network.

## Edge cases and decisions

- **Perimeter without an incident record**: logged loudly and excluded (join
  is points-driven). Not expected in practice; the points layer is the
  authoritative incident list.
- **Fires with no `CURRENT_SIZE`**: brand-new ignitions may lack a size
  estimate. The existing size filter excludes items without a size, so these
  are hidden under the default 1 ha minimum. UNRESOLVED: arguably a new
  Out of Control fire with unknown size should always be shown. Left as-is to
  match current filter semantics; revisit as its own change.
- **`INCIDENT_NAME` equals `FIRE_NUMBER`** for unnamed fires;
  `Messages._fire()` already collapses this case.
- **Empty result** (no fires in radius): valid, returns the normal "no fires"
  message.
- **US WFIGS** has equivalent NIFC ArcGIS layers; the realtime block is
  designed per-source so US can be migrated later without new code.

## Testing

- `tests/test_arcgis.py`: happy path (merge, polygon preference, CRS),
  point-only fires, empty results, network error, error body, transfer limit,
  unmatched perimeter logging. HTTP mocked with `responses`. A `live`-marked
  test hits the real endpoint.
- `tests/test_fires.py`: `_load_source()` realtime path, fallback on `None`,
  disabled flag; end-to-end `nearby()` with a patched `fetch_fires`.
- `tests/test_config.py`: `RealtimeFireConfig` validation (missing `Fire`
  mapping rejected, defaults).
