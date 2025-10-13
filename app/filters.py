"""
Generic filtering system for TrekSafer data sources.

This module provides a generic, extensible filtering framework that can be used
across different data types (fires, avalanches, etc.). It supports multiple
filter types with a consistent interface.
"""
from __future__ import annotations

# Filter priority levels (lower numbers = more urgent/restrictive)
STATUS_LEVELS = {
    'active': 1,      # Active/out of control
    'managed': 2,     # Being held/managed
    'controlled': 3,  # Under control
    'out': 4          # Out/extinguished
}


def get_allowed_statuses(status_map, filter_level):
    """
    Extract allowed status codes using STATUS_LEVELS ordering.

    Args:
        status_map (dict): Status mapping for the specific data source
        filter_level (str): Filter level ('active', 'controlled', etc.)

    Returns:
        set: Set of allowed status codes for the filter level
    """
    max_level = STATUS_LEVELS.get(filter_level)
    if not max_level:
        # Invalid filter_level - return empty set (exclude all)
        return set()

    # Include all categories up to the specified level
    allowed = []
    for category, level in STATUS_LEVELS.items():
        if level <= max_level:
            allowed.extend(status_map.get(category, []))

    return set(allowed)


def apply_status_filter(items, status_filter, data_file, **kwargs):
    """Apply status filtering to items."""
    if status_filter == 'all':
        return items

    if not data_file or not hasattr(data_file, 'status_map'):
        return items

    allowed_statuses = get_allowed_statuses(data_file.status_map, status_filter)
    return [item for item in items if item.get('Status') in allowed_statuses]


def apply_distance_filter(items, distance_km, data_file, **kwargs):
    """Apply distance filtering to items."""
    location = kwargs.get('location')
    settings = kwargs.get('settings')

    if not settings:
        return items

    # Cap distance at max_radius
    max_distance_km = min(distance_km, settings.max_radius)
    distance_limit = max_distance_km * 1000  # Convert to meters

    filtered_items = []
    for item in items:
        # Note: item already has Distance in meters from normalization
        if item.get('Distance', float('inf')) <= distance_limit:
            filtered_items.append(item)

    return filtered_items


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


# Generic filter handler registry
FILTER_HANDLERS = {
    'status': apply_status_filter,
    'distance': apply_distance_filter,
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


# Factory functions for creating domain-specific filter configurations
def create_fire_filters(settings):
    """Create default filter configuration for fires."""
    return {
        'status': settings.fire_status,
        'size': settings.fire_size
    }


def create_avalanche_filters(settings):
    """Create default filter configuration for avalanches (future use)."""
    # Placeholder for future avalanche filtering
    return {}
