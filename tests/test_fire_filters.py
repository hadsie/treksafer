import pytest
from app.fires import (
    should_include_fire,
    fire_status_level,
    STATUS_LEVELS,
)


def test_status_levels_constant():
    """Test that STATUS_LEVELS contains expected hierarchy."""
    assert STATUS_LEVELS['active'] == 4
    assert STATUS_LEVELS['managed'] == 3
    assert STATUS_LEVELS['controlled'] == 2
    assert STATUS_LEVELS['out'] == 1

    # Verify hierarchy order
    assert STATUS_LEVELS['active'] > STATUS_LEVELS['managed']
    assert STATUS_LEVELS['managed'] > STATUS_LEVELS['controlled']
    assert STATUS_LEVELS['controlled'] > STATUS_LEVELS['out']


def test_should_include_fire_basic():
    """Test basic fire inclusion logic."""
    # Active level should include all fires
    assert should_include_fire('active', 'active') == True
    assert should_include_fire('managed', 'active') == True
    assert should_include_fire('controlled', 'active') == True
    assert should_include_fire('out', 'active') == True

    # Managed level should include active and managed fires
    assert should_include_fire('active', 'managed') == True
    assert should_include_fire('managed', 'managed') == True
    assert should_include_fire('controlled', 'managed') == False
    assert should_include_fire('out', 'managed') == False

    # Controlled level should include active, managed, and controlled fires
    assert should_include_fire('active', 'controlled') == True
    assert should_include_fire('managed', 'controlled') == True
    assert should_include_fire('controlled', 'controlled') == True
    assert should_include_fire('out', 'controlled') == False

    # Out level should include all fires
    assert should_include_fire('active', 'out') == True
    assert should_include_fire('managed', 'out') == True
    assert should_include_fire('controlled', 'out') == True
    assert should_include_fire('out', 'out') == True


def test_should_include_fire_invalid_levels():
    """Test fire inclusion with invalid status levels."""
    # Unknown fire status should default to 0 priority (not included unless config is also unknown)
    assert should_include_fire('unknown', 'managed') == False
    assert should_include_fire('invalid', 'controlled') == False

    # Unknown configured level should default to 0 priority
    assert should_include_fire('active', 'invalid') == True
    assert should_include_fire('controlled', 'invalid') == True


def test_fire_status_level_with_mapping():
    """Test status level mapping with valid status_map."""
    status_map = {
        'active': ['ACTIVE', 'BURNING'],
        'managed': ['UNDER CONTROL', 'BEING HELD'],
        'controlled': ['CONTROLLED'],
        'out': ['OUT', 'EXTINGUISHED'],
    }

    assert fire_status_level('ACTIVE', status_map) == 'active'
    assert fire_status_level('UNDER CONTROL', status_map) == 'managed'
    assert fire_status_level('CONTROLLED', status_map) == 'controlled'
    assert fire_status_level('OUT', status_map) == 'out'
    assert fire_status_level('EXTINGUISHED', status_map) == 'out'


def test_fire_status_level_unknown_status():
    """Test status level mapping with unknown raw status."""
    status_map = {
        'active': ['ACTIVE'],
        'controlled': ['CONTROLLED'],
    }

    # Unknown status should default to 'active' (safety-first)
    assert fire_status_level('UNKNOWN_STATUS', status_map) == 'active'
    assert fire_status_level('NEW_STATUS', status_map) == 'active'
    assert fire_status_level('', status_map) == 'active'
    assert fire_status_level(None, status_map) == 'active'


def test_fire_status_level_no_mapping():
    """Test status level mapping with no status_map provided."""
    # Should default to 'active' when no mapping is provided
    assert fire_status_level('ACTIVE', None) == 'active'
    assert fire_status_level('CONTROLLED', None) == 'active'
    assert fire_status_level('ANY_STATUS', {}) == 'active'


def test_fire_status_level_case_sensitivity():
    """Test that status mapping is case sensitive as expected."""
    status_map = {
        'active': ['Active'],
        'controlled': ['CONTROLLED'],
    }

    # Should match exact case
    assert fire_status_level('Active', status_map) == 'active'
    assert fire_status_level('CONTROLLED', status_map) == 'controlled'

    # Different case should default to active (safety-first)
    assert fire_status_level('ACTIVE', status_map) == 'active'  # uppercase vs Active
    assert fire_status_level('controlled', status_map) == 'active'  # lowercase vs CONTROLLED


def test_integration_filtering_scenarios():
    """Test realistic integration scenarios."""
    status_map = {
        'active': ['ACTIVE', 'BURNING'],
        'managed': ['BEING HELD', 'UNDER CONTROL'],
        'controlled': ['CONTROLLED'],
        'out': ['OUT', 'EXTINGUISHED'],
    }

    # Scenario 1: Default config (managed level) - should show active and managed fires
    config_level = 'managed'
    assert should_include_fire(fire_status_level('ACTIVE', status_map), config_level) == True
    assert should_include_fire(fire_status_level('BEING HELD', status_map), config_level) == True
    assert should_include_fire(fire_status_level('CONTROLLED', status_map), config_level) == False
    assert should_include_fire(fire_status_level('OUT', status_map), config_level) == False

    # Scenario 2: Conservative config (active level) - should show only active fires
    config_level = 'active'
    assert should_include_fire(fire_status_level('ACTIVE', status_map), config_level) == True
    assert should_include_fire(fire_status_level('BEING HELD', status_map), config_level) == False
    assert should_include_fire(fire_status_level('CONTROLLED', status_map), config_level) == False

    # Scenario 3: Permissive config (controlled level) - should show active, managed, controlled
    config_level = 'controlled'
    assert should_include_fire(fire_status_level('ACTIVE', status_map), config_level) == True
    assert should_include_fire(fire_status_level('BEING HELD', status_map), config_level) == True
    assert should_include_fire(fire_status_level('CONTROLLED', status_map), config_level) == True
    assert should_include_fire(fire_status_level('OUT', status_map), config_level) == False


def test_safety_first_behavior():
    """Test that safety-first behavior works correctly for unknown statuses."""
    status_map = {
        'controlled': ['CONTROLLED'],
        'out': ['OUT'],
    }

    # Unknown fire status should always default to 'active' (safety-first)
    unknown_level = fire_status_level('UNKNOWN_STATUS', status_map)
    assert unknown_level == 'active'

    # Should be included when config allows active fires
    assert should_include_fire(unknown_level, 'active') == True
    assert should_include_fire(unknown_level, 'managed') == True
    assert should_include_fire(unknown_level, 'controlled') == True
    assert should_include_fire(unknown_level, 'out') == True


def test_logging_level_behavior():
    """Test that the filtering behaves like logging levels."""
    # Higher or equal priority levels should be included

    # Active fires (priority 4) should be included in all levels
    assert should_include_fire('active', 'active') == True
    assert should_include_fire('active', 'managed') == True
    assert should_include_fire('active', 'controlled') == True
    assert should_include_fire('active', 'out') == True

    # Managed fires (priority 3) should be included in managed, controlled, and out levels
    assert should_include_fire('managed', 'active') == False
    assert should_include_fire('managed', 'managed') == True
    assert should_include_fire('managed', 'controlled') == True
    assert should_include_fire('managed', 'out') == True

    # Controlled fires (priority 2) should be included in controlled and out levels only
    assert should_include_fire('controlled', 'active') == False
    assert should_include_fire('controlled', 'managed') == False
    assert should_include_fire('controlled', 'controlled') == True
    assert should_include_fire('controlled', 'out') == True

    # Out fires (priority 1) should only be included in out level
    assert should_include_fire('out', 'active') == False
    assert should_include_fire('out', 'managed') == False
    assert should_include_fire('out', 'controlled') == False
    assert should_include_fire('out', 'out') == True
