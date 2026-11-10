"""Golden tests: every response shape, pinned byte-for-byte.

Each scenario's full response is stored under tests/data/golden/ and
compared exactly, so any change to response output, intended or not,
fails here first. The one exception is volatile time spans ("26h ago",
"3d ago"), which are normalized to "T ago" because they depend on
wall-clock time.

Regenerate after an INTENDED output change:

    TREKSAFER_UPDATE_GOLDENS=1 pytest tests/test_golden.py

then review the golden diff like code.
"""
import json
import os
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.avalanche.avcan import AvalancheCanadaProvider
from app.config import get_config
from app.messages import handle_message, handle_message_segments
from app.weather import AqiReport, WindReport

GOLDEN_DIR = Path(__file__).parent / 'data' / 'golden'

_TIME_SPANS = re.compile(r'\b(?:<1h|\d+(?:\.\d+)?[hd]) ago\b')


def _normalize(text) -> str:
    return _TIME_SPANS.sub('T ago', text if text is not None else '<None>')


def _check(name: str, actual: str) -> None:
    path = GOLDEN_DIR / f'{name}.txt'
    normalized = _normalize(actual)
    if os.environ.get('TREKSAFER_UPDATE_GOLDENS'):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized)
    assert path.exists(), f'No golden for {name}; run with TREKSAFER_UPDATE_GOLDENS=1'
    assert normalized == path.read_text(), f'Golden mismatch for {name}'


# (golden name, message). Coordinates and fires come from the fixture
# database tests/conftest.py builds. Every scenario names its data type
# explicitly; auto-detection is season-dependent and tested elsewhere.
SCENARIOS = [
    ('fires_manning_all', 'fires all (49.064646, -120.7919022)'),
    ('fires_manning_default', 'fires (49.06, -120.79)'),
    ('fires_manning_active_15km', 'fires active 15km (49.06, -120.79)'),
    ('fires_lillooet_20km_all', 'fires all 20km (50.7021714, -121.9725246)'),
    ('fires_none_nearby', 'fires (54.5, -125.5)'),
    ('fires_outside_coverage', 'fires (64.9, -18.5)'),
    ('fires_no_coordinates', 'fires please'),
    ('fires_manning_all_full', 'fires all !full (49.064646, -120.7919022)'),
    ('fireid_not_found', 'fireid NOPE999'),
    ('keyword_help', 'help'),
]


class TestGoldenResponses:
    """Every user-visible response shape, pinned."""

    @pytest.fixture(autouse=True)
    def fire_report_limits(self, monkeypatch):
        """Pin the fire report limits the goldens were recorded with."""
        monkeypatch.setattr(get_config().thresholds, 'fire_report_segments', 4)
        monkeypatch.setattr(get_config().thresholds, 'fire_count_cap', 8)

    @pytest.mark.parametrize('name,message', SCENARIOS)
    def test_fire_and_service_responses(self, name, message):
        with patch('app.messages.get_aqi', return_value=AqiReport(42, 42)), \
             patch('app.messages.get_wind', return_value=None):
            _check(name, handle_message(message))

    def test_segment_view_of_a_multi_sms_reply(self):
        """The multi-fire scenario as delivered: one chunk per SMS."""
        with patch('app.messages.get_aqi', return_value=AqiReport(152, 152)), \
             patch('app.messages.get_wind', return_value=None):
            segments = handle_message_segments('fires all (49.064646, -120.7919022)')
        _check('fires_manning_all_segments', '\n\n-- next SMS --\n\n'.join(segments))

    def test_conditions_header_with_wind(self, monkeypatch):
        from app.config import get_config
        t = get_config().thresholds
        monkeypatch.setattr(t, 'wind_floor', 20)
        monkeypatch.setattr(t, 'wind_trend_delta', 10)
        monkeypatch.setattr(t, 'aqi_floor', 75)
        monkeypatch.setattr(t, 'aqi_trend_delta', 50)
        report = WindReport(speed=25, direction='SW', peak=60)
        with patch('app.messages.get_aqi', return_value=AqiReport(152, 152)), \
             patch('app.messages.get_wind', return_value=report):
            _check('fires_manning_all_with_wind',
                   handle_message('fires all (49.064646, -120.7919022)'))

    def test_conditions_header_with_rising_aqi(self, monkeypatch):
        from app.config import get_config
        t = get_config().thresholds
        monkeypatch.setattr(t, 'aqi_floor', 75)
        monkeypatch.setattr(t, 'aqi_trend_delta', 50)
        with patch('app.messages.get_aqi', return_value=AqiReport(35, 160)), \
             patch('app.messages.get_wind', return_value=None):
            _check('fires_manning_all_rising_aqi',
                   handle_message('fires all (49.064646, -120.7919022)'))

    @pytest.mark.usefixtures('lookup_fire_db')
    def test_fireid_found(self):
        with patch('app.messages.get_aqi', return_value=AqiReport(42, 42)), \
             patch('app.messages.get_wind', return_value=None):
            _check('fireid_found', handle_message('fireid C10784 (49.06, -120.79)'))

    def test_aqi_absent_when_unavailable(self):
        with patch('app.messages.get_aqi', return_value=None), \
             patch('app.messages.get_wind', return_value=None):
            _check('fires_manning_all_no_aqi',
                   handle_message('fires all (49.064646, -120.7919022)'))

    @pytest.mark.parametrize('name,message', [
        ('avalanche_whistler', 'avalanche (50.1163, -122.9574)'),
        ('avalanche_whistler_all', 'avalanche all (50.1163, -122.9574)'),
    ])
    def test_avalanche_responses(self, name, message):
        sample_path = Path(__file__).parent / 'data' / \
            'avcan_Brandywine-Garibaldi-Homathko-Spearhead-Tantalus_sample.json'
        mock_response = Mock(status_code=200)
        mock_response.json.return_value = json.loads(sample_path.read_text())
        with patch.object(AvalancheCanadaProvider, '_request',
                          return_value=mock_response):
            _check(name, handle_message(message))
