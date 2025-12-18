# Test Fire Data Documentation

This directory contains test fire perimeter data used to validate fire filtering, distance calculations, status filtering, and cross-border scenarios in the TrekSafer application.

## Overview

The test data consists of GeoJSON files that serve as the **single source of truth** for test fire perimeters. These files are converted to shapefile ZIP archives (matching the production data format) using the `compile_test_data.py` script.

### File Structure

```
tests/data/
├── README.md                  ← This file
├── BC_perimeters.geojson      ← British Columbia test fires (20 fires)
├── AB_perimeters.geojson      ← Alberta test fires (8 fires)
└── US_perimeters.geojson      ← US (WA/MT) test fires (8 fires)

tests/shapefiles/              ← Generated shapefile archives
├── BC/
│   └── BC_Fire_Perimeters_{DATE}.zip
├── AB/
│   └── AB_Fire_Perimeters_{DATE}.zip
└── US/
    └── WFIGS_Interagency_Perimeters_{DATE}.zip
```

## Status Codes & Color Scheme

The GeoJSON files include visual properties for easy identification in viewers like geojson.io:

### BC Status Codes
- **OUT_CNTRL** (Active) - Red `#FF0000` - Fire is out of control
- **HOLDING** (Managed) - Orange `#FFA500` - Fire is being held
- **UNDR_CNTRL** (Controlled) - Yellow `#FFFF00` - Fire is under control
- **OUT** - Green `#00FF00` - Fire is extinguished
- **null** - Gray `#888888` - Unknown status (edge case testing)

### AB Status Codes
- **Out of Control** (Active) - Red `#FF0000`
- **Being Held** (Managed) - Orange `#FFA500`
- **Under Control** (Controlled) - Yellow `#FFFF00`

### US Status Codes
- **OUT_CNTRL** (Active) - Red `#FF0000`
- **HOLDING** / **Flanking** (Managed) - Orange `#FFA500`
- **UC** (Controlled) - Yellow `#FFFF00`
- **OUT** - Green `#00FF00`

## Test Reference Coordinates

Most BC fires are positioned relative to test center: **(49.0, -121.0)** (Hope, BC area)

## BC Fires (20 total)

### Distance Testing

| Fire ID | Name | Distance | Size | Status | Test Purpose |
|---------|------|----------|------|--------|--------------|
| C20145 | Hope_VeryClose | ~3km | 0.5ha | OUT | Very close fire + size filter (below 1ha threshold) |
| C10801 | Manning_West | ~5km | 120ha | OUT_CNTRL | Close fire within default radius |
| C10802 | Manning_East | ~10-15km | 90ha | UNDR_CNTRL | Medium distance fire |
| C20146 | Skagit_Close | ~12km | 1.5ha | HOLDING | Close fire + small size (just above 1ha threshold) |
| C20147 | Fraser_Medium | ~30km | 25ha | UNDR_CNTRL | Medium distance fire |
| C20148 | Chilliwack_Large | ~45km | 250ha | OUT_CNTRL | Far fire within default radius + large size |
| C10784 | Kamloops_Base | ~50km | 50ha | HOLDING | At default radius boundary (50km) |
| C20149 | Abbotsford_Tiny | ~60km | 0.3ha | OUT | Beyond default radius + below size threshold |
| C20150 | Harrison_FarMedium | ~70km | 75ha | HOLDING | Beyond default radius |
| C20151 | Lillooet_VeryFar | ~145km | 500ha | UNDR_CNTRL | Near max_radius boundary (150km) |
| C20152 | PrinceGeorge_OutOfRange | ~700km | 300ha | HOLDING | Far beyond max_radius (150km) |

### Border & Cross-Border Testing

| Fire ID | Name | Size | Status | Test Purpose |
|---------|------|------|--------|--------------|
| C53001 | Mount_Robson_Border | 280ha | OUT_CNTRL | BC/AB border - Mt Robson Provincial Park |
| S49001 | Akamina_Kishinena_Border | 190ha | OUT_CNTRL | BC/AB/MT tri-border area |
| S40234 | SE_Border_Near | 140ha | HOLDING | Edge within ~10km of SE BC border |
| C52789 | Jasper_BC_Side | 350ha | OUT_CNTRL | 3-border scenario: BC side near Jasper |
| N49123 | Okanagan_Border_BC | 175ha | OUT_CNTRL | 3-border scenario: BC corner near WA/AB |

### Overlapping Fires (Cluster Testing)

| Fire ID | Name | Size | Status | Test Purpose |
|---------|------|------|--------|--------------|
| N60784 | NE_Overlap_1 | 300ha | OUT_CNTRL | NE BC cluster - overlaps with #2/#3/#4 |
| N60785 | NE_Overlap_2 | 260ha | UNDR_CNTRL | NE BC cluster - overlaps with #1/#3/#4 |
| N60786 | NE_Overlap_3 | 200ha | UNDR_CNTRL | NE BC cluster - overlaps with #1/#2/#4 |
| N60787 | NE_Overlap_4 | 180ha | null | NE BC cluster - overlaps with #1/#2/#3, **null status edge case** |

## AB Fires (8 total)

| Fire ID | Name | Size | Status | Test Purpose |
|---------|------|------|--------|--------------|
| HWF-089-2025 | Banff_Small | 75ha | Under Control | Small controlled fire in Banff National Park |
| HWF-090-2025 | Kananaskis_Medium | 220ha | Being Held | Medium fire in Kananaskis |
| LWF-134-2025 | Calgary_Tiny | 0.8ha | Under Control | Below 1ha threshold - size filter testing |
| PWF-156-2025 | Grande_Prairie_Large | 520ha | Out of Control | Large active fire in northern AB |
| LWF-178-2025 | Medicine_Hat_Small | 2.5ha | Under Control | Small fire in southern AB |
| CWF-201-2025 | Hinton_Cross_Border | 155ha | Being Held | **Cross-border fire** (AB/BC boundary) |
| PWF-205-2025 | Jasper_NE_Town | 340ha | Out of Control | NE of Jasper, **bottom-right corner touches Jasper townsite** |
| SWF-212-2025 | Waterton_East | 210ha | Being Held | Just east of Waterton National Park |

## US Fires (8 total)

All US fires are located in Washington (WA) or Montana (MT).

| Fire ID | Name | State | Size | Status | Test Purpose |
|---------|------|-------|------|--------|--------------|
| WA-OKS-000234 | Okanogan_Border_WA | WA | 432 acres | OUT_CNTRL | **3-border scenario**: NE WA corner within 10km of BC/AB border |
| WA-OKS-000189 | Pasayten_Small | WA | 185 acres | HOLDING | Small fire in Pasayten Wilderness |
| WA-CTL-000567 | Chelan_Medium | WA | 618 acres | Flanking | Medium fire with Flanking status |
| WA-SNO-000423 | Snoqualmie_Tiny | WA | 1.2 acres | UC | **Below 1ha threshold** (0.49ha) - size filter testing |
| WA-YAK-000789 | Yakima_Large | WA | 1235 acres | OUT_CNTRL | Large active fire (500ha) |
| WA-SPO-000156 | Spokane_Out | WA | 247 acres | OUT | Extinguished fire - status filter testing |
| ID-CDA-000234 | Coeur_dAlene_Border | ID | 395 acres | HOLDING | Near WA/ID border |
| MT-GLA-000345 | Glacier_North_Border | MT | 520 acres | OUT_CNTRL | **Just north of Glacier National Park** |

*Note: US fire sizes are in acres; the system converts to hectares (1 acre = 0.404686 hectares)*

## Test Coverage

The test data is designed to comprehensively test:

### 1. Distance Filtering
- Fires at various distances: 3km, 5km, 12km, 30km, 45km, 50km, 70km, 145km, 700km
- Tests default radius (50km), max_radius boundary (150km), and beyond max_radius
- **Tests**: `test_fire_filtering.py::TestGenericFilterSystem::test_apply_distance_filter`

### 2. Status Filtering
- Distribution across all status levels:
  - **Active**: 7 BC fires, 2 AB fires, 3 US fires
  - **Managed**: 5 BC fires, 3 AB fires, 3 US fires
  - **Controlled**: 5 BC fires, 3 AB fires, 1 US fire
  - **Out**: 2 BC fires, 0 AB fires, 1 US fire
  - **null**: 1 BC fire (edge case)
- **Tests**: `test_fire_filtering.py::TestGetAllowedStatuses`, `TestFireFilteringIntegration`

### 3. Size Filtering
- Fires below 1ha threshold: 0.3ha, 0.5ha, 0.8ha, 0.49ha (US)
- Fires just above 1ha: 1.5ha, 2.5ha
- Medium fires: 25ha, 50ha, 75ha, 90ha, 120ha
- Large fires: 220ha, 250ha, 280ha, 300ha, 340ha, 500ha, 520ha
- **Tests**: `test_fire_filtering.py::TestGenericFilterSystem::test_apply_size_filter`

### 4. Cross-Border Scenarios
- **BC/AB border**: Mount_Robson_Border, Akamina_Kishinena_Border, Hinton_Cross_Border
- **3-border areas**: Okanagan_Border_BC (BC/WA/AB), Okanogan_Border_WA (WA/BC/AB)
- **Near borders**: SE_Border_Near, Jasper fires, Waterton_East, Glacier_North_Border
- **Purpose**: Validates that fires near borders are correctly attributed to their jurisdiction

### 5. Overlapping Fires
- NE BC cluster (N60784-N60787): 4 fires with overlapping perimeters
- **Purpose**: Tests handling of multiple fires in same area

### 6. Edge Cases
- **Null status**: N60787 (tests handling of missing status data)
- **Below threshold**: Multiple fires <1ha (tests size filtering)
- **Out of range**: PrinceGeorge_OutOfRange at 700km (tests distance limits)
- **Invalid data**: Tests handle missing/invalid Size values

### 7. Multi-Filter Scenarios
- Combinations of status + distance + size filters
- **Tests**: `test_fire_filtering.py::TestGenericFilterSystem::test_apply_filters_multiple`

## Generating Shapefile Archives

To convert the GeoJSON source files to shapefile ZIP archives:

```bash
# Generate with today's date
python tests/compile_test_data.py

# Generate with specific date
python tests/compile_test_data.py --date 20251225

# Clean old shapefiles and regenerate
python tests/compile_test_data.py --clean
```

### Output

The script creates shapefile ZIP archives in `tests/shapefiles/{location}/` using the naming conventions from `config.yaml`:

- `tests/shapefiles/BC/BC_Fire_Perimeters_{DATE}.zip`
- `tests/shapefiles/AB/AB_Fire_Perimeters_{DATE}.zip`
- `tests/shapefiles/US/WFIGS_Interagency_Perimeters_{DATE}.zip`

Each ZIP contains the standard shapefile components: `.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`

## Updating Test Data

### Modifying Existing Fires

1. Edit the GeoJSON files directly in `tests/data/`
2. Update fire properties (coordinates, size, status, etc.)
3. Regenerate shapefiles: `python tests/compile_test_data.py --clean`

### Adding New Fires

1. Add a new feature to the appropriate GeoJSON file
2. Include all required fields:
   - **BC**: `FIRE_NUM`, `FIRE_YEAR`, `NAME`, `FIRE_SZ_HA`, `STATUS`, `DESC`
   - **AB**: `FIRE_NUMBE`, `AREA`, `ALIAS`, `COMPLEX`, `STATUS`, `DESC`
   - **US**: `attr_Fir_6`, `attr_Inc_2`, `attr_Inc_4`, `attr_Incid`, `attr_Fir_2`, `DESC`
3. Add color properties for visualization:
   - `fill`: Color code based on status
   - `stroke`: Darker border color
   - `fill-opacity`: 0.6 for semi-transparency
4. Add geometry (polygon coordinates)
5. Regenerate shapefiles: `python tests/compile_test_data.py --clean`
6. Update this README with the new fire's test purpose

### Best Practices

- **Keep GeoJSON as source of truth**: Always edit GeoJSON files, then regenerate shapefiles
- **Maintain test coverage**: Ensure new fires serve a specific test purpose
- **Document changes**: Update this README when adding/modifying fires
- **Use realistic coordinates**: Fires should be in appropriate geographic regions
- **Follow field conventions**: Match field names to production data sources
- **Test your changes**: Run `pytest tests/test_fire_filtering.py` after updates

## Visualization

To visualize the test data:

1. Open https://geojson.io in your browser
2. Drag and drop any `.geojson` file from `tests/data/`
3. Fires will be color-coded by status:
   - Red = Active/Out of Control
   - Orange = Managed/Being Held
   - Yellow = Controlled/Under Control
   - Green = Extinguished/OUT
   - Gray = Unknown/null status

## Field Reference

### BC Fire Fields
- `FIRE_NUM`: Unique fire identifier (e.g., "C10784")
- `FIRE_YEAR`: Year of fire (2025)
- `NAME`: Fire name/alias
- `FIRE_SZ_HA`: Fire size in hectares
- `STATUS`: Status code (OUT_CNTRL, HOLDING, UNDR_CNTRL, OUT, null)
- `DESC`: Human-readable description

### AB Fire Fields
- `FIRE_NUMBE`: Unique fire identifier (e.g., "HWF-089-2025")
- `AREA`: Fire size in hectares
- `ALIAS`: Fire name
- `COMPLEX`: Fire complex/location
- `STATUS`: Status text ("Out of Control", "Being Held", "Under Control")
- `DESC`: Human-readable description

### US Fire Fields
- `attr_Fir_6`: Unique fire identifier (e.g., "WA-OKS-000234")
- `attr_Inc_2`: Fire/incident name
- `attr_Inc_4`: Location description
- `attr_Incid`: Fire size in **acres** (converted to hectares by system)
- `attr_Fir_2`: Status code (OUT_CNTRL, HOLDING, Flanking, UC, OUT)
- `DESC`: Human-readable description

### Visualization Fields (Not used by TrekSafer)
- `fill`: Polygon fill color (hex code)
- `stroke`: Polygon border color (hex code)
- `fill-opacity`: Transparency (0.0-1.0)

The TrekSafer code handles shapefiles with extra fields gracefully - these visualization properties are simply ignored during fire data processing.

## Related Files

- `tests/compile_test_data.py` - Conversion script
- `tests/test_fire_filtering.py` - Fire filtering tests
- `config.yaml` - Data source configuration (field mappings, status maps, filenames)
- `app/filters.py` - Filtering implementation
- `app/fires.py` - Fire data processing

---

*Last updated: 2025-12-18*
