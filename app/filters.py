"""
Generic filtering system for TrekSafer data sources.

This module provides a generic, extensible filtering framework that can be used
across different data types (fires, avalanches, etc.). It supports multiple
filter types with a consistent interface.
"""
from __future__ import annotations

import logging

# Filter priority levels (lower numbers = more urgent/restrictive)
STATUS_LEVELS = {
    'active': 1,      # Active/out of control
    'managed': 2,     # Being held/managed
    'controlled': 3,  # Under control
    'out': 4          # Out/extinguished
}


def apply_status_filter(items, status_filter, data_file, **kwargs):
    """Apply status filtering to items."""
    if status_filter == 'all':
        return items

    # Get max level for the filter
    max_level = STATUS_LEVELS.get(status_filter)
    if not max_level:
        logging.error(f"Invalid status filter '{status_filter}'. Valid filters: {', '.join(STATUS_LEVELS.keys())}")
        return items

    return [item for item in items if item.get('Status', float('inf')) <= max_level]


def apply_size_filter(items, min_size_ha, data_file, **kwargs):
    """Apply size filtering to items."""
    filtered_items = []
    for item in items:
        item_size = item.get('Size')
        if item_size is not None:
            try:
                if float(item_size) >= min_size_ha:
                    filtered_items.append(item)
            except (ValueError, TypeError):
                # If size can't be converted to float, exclude item
                continue
        # If no size info, exclude item (safer default)

    return filtered_items


# Filter handler registry (defined after functions to avoid NameError)
FILTER_HANDLERS = {
    'status': apply_status_filter,
    'size': apply_size_filter
}


def apply_filters(items, filters, data_file, location, settings):
    """
    Apply multiple filters to items generically.

    Args:
        items (list): List of data item dictionaries
        filters (dict): Dictionary of filter_type: filter_value pairs
        data_file: Data file configuration for this source
        location: Point location for distance calculations
        settings: Application settings

    Returns:
        list: Filtered list of items
    """
    for filter_type, filter_value in filters.items():
        if filter_type in FILTER_HANDLERS:
            items = FILTER_HANDLERS[filter_type](
                items, filter_value, data_file, location=location, settings=settings
            )

    return items

def create_avalanche_filters(settings):
    """Create default filter configuration for avalanches (future use)."""
    # Placeholder for future avalanche filtering
    return {}
