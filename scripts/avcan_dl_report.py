#!/usr/bin/env python3
"""Download raw AVCAN avalanche forecast JSON for testing.

Usage:
    python scripts/avcan_dl_report.py <latitude> <longitude>

Example:
    python scripts/avcan_dl_report.py 49.5 -123.0
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


def download_avcan_report(lat: float, lon: float) -> None:
    """Download AVCAN report for coordinates and save to tests/data.

    Args:
        lat: Latitude
        lon: Longitude
    """
    # Construct API URL
    api_url = f"https://api.avalanche.ca/forecasts/en/products/point?lat={lat}&long={lon}"

    print(f"Fetching report for coordinates: {lat}, {lon}")
    print(f"URL: {api_url}")

    # Make request
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()

    data = response.json()

    # Extract report title and date
    report = data.get('report', {})
    title = report.get('title', 'Unknown')
    # Sanitize title for filename - replace spaces and special chars with hyphens
    title = re.sub(r'[^\w-]+', '-', title)
    date_issued = report.get('dateIssued', '')

    # Parse date from ISO format
    if date_issued:
        date_obj = datetime.fromisoformat(date_issued.replace('Z', '+00:00'))
        date_str = date_obj.strftime('%Y%m%d')
    else:
        date_str = datetime.now().strftime('%Y%m%d')

    # Construct output filename
    output_dir = Path('tests/data')
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"avcan-{title}-{date_str}.json"
    output_path = output_dir / filename

    # Write JSON to file
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Report saved to: {output_path}")
    print(f"Region: {title}")
    print(f"Date issued: {date_issued}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    try:
        latitude = float(sys.argv[1])
        longitude = float(sys.argv[2])
        download_avcan_report(latitude, longitude)
    except ValueError as e:
        print(f"Error: Invalid coordinates - {e}")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Error: Failed to download report - {e}")
        sys.exit(1)
