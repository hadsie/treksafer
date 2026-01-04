#!/usr/bin/env python3
"""Extract region data from avcan canadian_subregions shapefile to CSV."""
import csv
import sys
import geopandas as gpd

# Read the shapefile directly from the zip file
gdf = gpd.read_file('zip://../boundaries/canadian_subregions.shp.zip')

# Field mappings
name_field = 'polygon_na'
id_field = 'id'
last_update_field = 'last_updat'

# Prepare data for CSV output
regions = []
for _, row in gdf.iterrows():
    name = row[name_field] if name_field in row and row[name_field] else ''
    region_id = row[id_field] if id_field in row and row[id_field] else ''

    # Calculate centroid
    centroid = row.geometry.centroid
    coords = f"{centroid.y:.6f},{centroid.x:.6f}"  # lat,lon format

    # Get last update, leave empty if None
    last_update = row[last_update_field] if last_update_field in row and row[last_update_field] else ''

    regions.append({
        'Name': name,
        'ID': region_id,
        'Coords': coords,
        'LastUpdate': last_update
    })

# Sort by name
regions.sort(key=lambda x: x['Name'])

# Output as CSV
writer = csv.DictWriter(sys.stdout, fieldnames=['Name', 'ID', 'Coords', 'LastUpdate'])
writer.writeheader()
writer.writerows(regions)
