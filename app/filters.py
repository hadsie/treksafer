"""
Generic filtering system for TrekSafer data sources.

This module provides a generic, extensible filtering framework that can be used
across different data types (fires, avalanches, etc.). It supports multiple
filter types with a consistent interface.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

# Filter priority levels (lower numbers = more urgent/restrictive)
STATUS_LEVELS = {
    'active': 1,      # Active/out of control
    'managed': 2,     # Being held/managed
    'controlled': 3,  # Under control
    'out': 4          # Out/extinguished
}


def apply_status_filter(items, status_filter, **kwargs):
    """Apply status filtering to items."""
    if status_filter == 'all':
        return items

    # Get max level for the filter
    max_level = STATUS_LEVELS.get(status_filter)
    if not max_level:
        logging.error(f"Invalid status filter '{status_filter}'. Valid filters: {', '.join(STATUS_LEVELS.keys())}")
        return items

    filtered_items = []
    for item in items:
        status_level = item.get('StatusLevel')
        if status_level is None:
            # Log warning for missing status level (indicates potential normalization bug)
            # Use highest level (currently 4='out') so missing status fires appear with 'out' filter
            fire_id = item.get('Fire', item.get('Name', 'unknown'))
            logging.warning(f"Fire {fire_id} is missing StatusLevel field - using max level as default")
            status_level = max(STATUS_LEVELS.values())

        if status_level <= max_level:
            filtered_items.append(item)

    return filtered_items


def _within_new_fire_window(item, settings):
    """True when the item was discovered within the new-fire age window."""
    discovered = item.get('Discovered')
    if discovered is None or settings is None:
        return False
    return datetime.now(timezone.utc) - discovered < timedelta(days=settings.new_fire_age_days)


def apply_size_filter(items, min_size_ha, settings=None, **kwargs):
    """Apply size filtering to items.

    Fires discovered within settings.new_fire_age_days bypass the size
    filter: a brand new fire is the most safety-relevant kind and often has
    no size estimate yet.
    """
    filtered_items = []
    for item in items:
        if _within_new_fire_window(item, settings):
            filtered_items.append(item)
            continue
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


def apply_filters(items, filters, settings):
    """
    Apply multiple filters to items generically.

    Args:
        items (list): List of data item dictionaries
        filters (dict): Dictionary of filter_type: filter_value pairs
        settings: Application settings

    Returns:
        list: Filtered list of items
    """
    for filter_type, filter_value in filters.items():
        if filter_type in FILTER_HANDLERS:
            items = FILTER_HANDLERS[filter_type](items, filter_value, settings=settings)

    return items
