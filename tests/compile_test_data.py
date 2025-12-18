#!/usr/bin/env python3
"""
Compile test GeoJSON data into shapefile ZIP archives.

This script reads GeoJSON perimeter files from tests/data/ and converts them
to shapefile ZIP archives in tests/shapefiles/{location}/, using the naming
conventions defined in config.yaml.

Usage:
    python tests/compile_test_data.py [--date YYYYMMDD] [--clean]
"""

import argparse
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import yaml


def load_config(config_path: Path) -> dict:
    """Load and parse the config.yaml file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def convert_geojson_to_shapefile(
    geojson_path: Path,
    output_dir: Path,
    location: str,
    filename_template: str,
    date_str: str
) -> bool:
    """
    Convert a GeoJSON file to a zipped shapefile.

    Args:
        geojson_path: Path to source GeoJSON file
        output_dir: Directory to save the shapefile ZIP
        location: Location code (BC, AB, US, etc.)
        filename_template: Filename template with {DATE} placeholder
        date_str: Date string to replace {DATE} placeholder

    Returns:
        True if successful, False otherwise
    """
    if not geojson_path.exists():
        print(f"✗ {geojson_path.name} not found")
        return False

    # Read GeoJSON
    gdf = gpd.read_file(geojson_path)
    feature_count = len(gdf)

    # Create output filename
    output_filename = filename_template.replace('{DATE}', date_str)
    output_zip_path = output_dir / output_filename

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create temporary directory for shapefile components
    temp_dir = output_dir / f"temp_{location}"
    temp_dir.mkdir(exist_ok=True)

    try:
        # Write shapefile (creates .shp, .shx, .dbf, .prj, .cpg)
        shapefile_base = temp_dir / f"{location}_perimeters"
        gdf.to_file(shapefile_base.with_suffix('.shp'), driver='ESRI Shapefile')

        # Create ZIP archive with all shapefile components
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg']:
                component_file = shapefile_base.with_suffix(ext)
                if component_file.exists():
                    zipf.write(component_file, component_file.name)

        print(f"✓ {geojson_path.name} → {output_dir.name}/{output_filename} ({feature_count} features)")
        return True

    except Exception as e:
        print(f"✗ Error converting {geojson_path.name}: {e}")
        return False

    finally:
        # Clean up temporary directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def main():
    parser = argparse.ArgumentParser(
        description='Convert test GeoJSON files to shapefile ZIP archives'
    )
    parser.add_argument(
        '--date',
        default=datetime.now().strftime('%Y%m%d'),
        help='Date string for filename (YYYYMMDD format, default: today)'
    )
    parser.add_argument(
        '--clean',
        action='store_true',
        help='Remove existing shapefiles before generating new ones'
    )

    args = parser.parse_args()

    # Determine project root (parent of tests directory)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # Paths
    config_path = project_root / 'config.yaml'
    data_dir = script_dir / 'data'
    shapefiles_base = script_dir / 'shapefiles'

    # Load config
    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"Error loading config.yaml: {e}")
        sys.exit(1)

    # Mapping of location codes to GeoJSON filenames
    geojson_files = {
        'BC': 'BC_perimeters.geojson',
        'AB': 'AB_perimeters.geojson',
        'US': 'US_perimeters.geojson',
    }

    if args.clean:
        print("Cleaning existing shapefiles...")
        if shapefiles_base.exists():
            shutil.rmtree(shapefiles_base)
        print()

    print(f"Converting GeoJSON to shapefiles (date: {args.date})...\n")

    success_count = 0
    total_count = 0

    # Process each data source from config
    for data_config in config.get('data', []):
        location = data_config['location']
        filename_template = data_config['filename']

        # Only process locations we have test data for
        if location not in geojson_files:
            continue

        total_count += 1
        geojson_filename = geojson_files[location]
        geojson_path = data_dir / geojson_filename
        output_dir = shapefiles_base / location

        if convert_geojson_to_shapefile(
            geojson_path,
            output_dir,
            location,
            filename_template,
            args.date
        ):
            success_count += 1

    print(f"\nGenerated {success_count}/{total_count} shapefile archives.")

    if success_count > 0:
        print(f"Shapefiles saved to: {shapefiles_base}/")

    sys.exit(0 if success_count == total_count else 1)


if __name__ == '__main__':
    main()
