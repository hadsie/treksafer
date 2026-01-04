#!/usr/bin/env python3
"""Compare full vs abbreviated avalanche forecast reports.

Usage:
    python compare_avalanche_reports.py --coords 50.1163,-122.9574
    python compare_avalanche_reports.py --coords 50.1163,-122.9574 --filter tomorrow
    python compare_avalanche_reports.py --all-regions
    python compare_avalanche_reports.py --all-regions --filter all
"""
import argparse
import sys
import re
import requests
from typing import List, Tuple

from app.avalanche import AvalancheReport


def get_all_forecast_regions() -> List[Tuple[float, float, str]]:
    """Fetch all active forecast regions from Avalanche Canada API.

    Returns:
        List of tuples: (lat, lon, region_name)
    """
    try:
        url = "https://api.avalanche.ca/forecasts/en/areas"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        regions = []
        for area in data.get('features', []):
            props = area.get('properties', {})
            geom = area.get('geometry', {})

            # Get region ID
            region_id = props.get('id', 'Unknown')

            # Get centroid or first coordinate
            coords = props.get('centroid', [])
            if len(coords) == 2:
                lon, lat = coords[0], coords[1]
                regions.append((lat, lon, region_id))

        return regions
    except requests.RequestException as e:
        print(f"Error fetching forecast areas: {e}", file=sys.stderr)
        return []


def _make_url_clickable(text: str) -> tuple:
    """Convert URLs in text to clickable OSC 8 hyperlinks.

    Args:
        text: Text that may contain URLs

    Returns:
        Tuple of (converted_text, original_url or None)
    """
    # Pattern to detect URLs starting with http:// or https://
    url_pattern = r'(https?://[^\s]+)'

    match = re.search(url_pattern, text)
    if not match:
        return (text, None)

    url = match.group(1)

    # OSC 8 format: \e]8;;URL\e\\TEXT\e]8;;\e\\
    # Use a function to build the replacement to avoid escape sequence issues
    def make_link(m):
        return '\033]8;;' + m.group(1) + '\033\\Full Report: [link]\033]8;;\033\\'

    converted = re.sub(url_pattern, make_link, text)

    return (converted, url)


def _visible_width(text: str) -> int:
    """Calculate visible width of text, excluding ANSI/OSC 8 escape sequences.

    Args:
        text: Text that may contain escape sequences

    Returns:
        Visible character count
    """
    # Remove OSC 8 hyperlink sequences: \033]8;;URL\033\\ and \033]8;;\033\\
    clean = re.sub(r'\033]8;;[^\033]*\033\\', '', text)
    # Remove other ANSI escape sequences
    clean = re.sub(r'\033\[[0-9;]*m', '', clean)
    return len(clean)


def _pad_line(text: str, width: int) -> str:
    """Pad text to width, accounting for invisible escape sequences.

    Args:
        text: Text to pad (may contain escape sequences)
        width: Target width

    Returns:
        Padded text
    """
    visible = _visible_width(text)
    if visible >= width:
        return text
    padding = ' ' * (width - visible)
    return text + padding


def _wrap_line(line: str, width: int) -> list:
    """Wrap a line to fit within width.

    Args:
        line: Line to wrap
        width: Maximum width

    Returns:
        List of wrapped line segments
    """
    # If line contains OSC 8 escape sequences, don't wrap it
    # (it's already been converted to short clickable text)
    if '\033]8;;' in line:
        return [line]

    if len(line) <= width:
        return [line]

    # Split line into chunks of width
    chunks = []
    while line:
        chunks.append(line[:width])
        line = line[width:]

    return chunks


def format_side_by_side(full: str, abbrev: str, width: int = 60) -> str:
    """Format two reports side by side.

    Args:
        full: Full format report
        abbrev: Abbreviated format report
        width: Width of each column

    Returns:
        Side-by-side formatted string
    """
    full_lines = full.split('\n')
    abbrev_lines = abbrev.split('\n')

    max_lines = max(len(full_lines), len(abbrev_lines))

    # Pad shorter list
    full_lines.extend([''] * (max_lines - len(full_lines)))
    abbrev_lines.extend([''] * (max_lines - len(abbrev_lines)))

    result = []
    result.append("=" * (width * 2 + 3))
    result.append(f"{'FULL FORMAT':<{width}} | {'ABBREVIATED FORMAT':<{width}}")
    result.append("=" * (width * 2 + 3))

    # Track URLs for display after the table
    found_url = None

    for full_line, abbrev_line in zip(full_lines, abbrev_lines):
        # Convert URLs to clickable hyperlinks
        full_line, full_url = _make_url_clickable(full_line)
        abbrev_line, abbrev_url = _make_url_clickable(abbrev_line)

        # Save URL for later display (prefer full format URL)
        if full_url and not found_url:
            found_url = full_url
        elif abbrev_url and not found_url:
            found_url = abbrev_url

        # Wrap lines if too long
        full_wrapped = _wrap_line(full_line, width)
        abbrev_wrapped = _wrap_line(abbrev_line, width)

        # Display all wrapped lines
        max_wrapped = max(len(full_wrapped), len(abbrev_wrapped))
        full_wrapped.extend([''] * (max_wrapped - len(full_wrapped)))
        abbrev_wrapped.extend([''] * (max_wrapped - len(abbrev_wrapped)))

        for f_part, a_part in zip(full_wrapped, abbrev_wrapped):
            # Use custom padding to handle escape sequences
            f_padded = _pad_line(f_part, width)
            a_padded = _pad_line(a_part, width)
            result.append(f"{f_padded} | {a_padded}")

    result.append("=" * (width * 2 + 3))

    # Add URL below the table if present
    if found_url:
        result.append(found_url)

    # Add character count for abbreviated version
    abbrev_char_count = len(abbrev)
    result.append(f"TOTAL CHARACTERS: {abbrev_char_count}")
    result.append("")

    return '\n'.join(result)


def compare_reports(coords: Tuple, forecast_filter: str = 'current'):
    """Compare full and abbreviated reports for a location.

    Args:
        coords: (latitude, longitude)
        forecast_filter: 'current', 'tomorrow', or 'all'
    """
    report = AvalancheReport(coords)

    if report.out_of_range():
        print(f"Coordinates {coords} are out of range for avalanche forecasts.")
        return

    # Get both formats
    filters = {'forecast': forecast_filter}
    full = report.get_forecast(filters, format='full')
    abbrev = report.get_forecast(filters, format='abbrev')

    if not full or not abbrev:
        print(f"No forecast available for coordinates {coords}")
        return

    print(format_side_by_side(full, abbrev, 65))
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Compare full vs abbreviated avalanche forecast reports'
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--coords',
        type=str,
        help='Coordinates in format: LAT,LONG (e.g., 50.1163,-122.9574)'
    )
    group.add_argument(
        '--all-regions',
        action='store_true',
        help='Compare reports for all active forecast regions'
    )

    parser.add_argument(
        '--filter',
        choices=['current', 'tomorrow', 'all'],
        default='current',
        help='Date filter (default: current)'
    )

    args = parser.parse_args()

    if args.coords:
        # Parse coordinates
        try:
            lat_str, lon_str = args.coords.split(',')
            coords = (float(lat_str.strip()), float(lon_str.strip()))
        except ValueError:
            print("Error: Coordinates must be in format LAT,LONG", file=sys.stderr)
            sys.exit(1)

        compare_reports(coords, args.filter)

    elif args.all_regions:
        regions = get_all_forecast_regions()

        if not regions:
            print("No forecast regions found.", file=sys.stderr)
            sys.exit(1)

        print(f"Comparing {len(regions)} forecast regions...\n")

        for i, (lat, lon, id) in enumerate(regions, 1):
            print(f"Region {i}/{len(regions)}: {id} ({lat:.4f}, {lon:.4f})")
            compare_reports((lat, lon), args.filter)


if __name__ == '__main__':
    main()
