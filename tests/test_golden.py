"""Golden tests: pin response output across the messaging refactor.

These exist to protect the app/messaging extraction (HAD-221a): the
refactor must keep every response byte-identical. Each scenario's full
response is stored under tests/data/golden/ and compared exactly, except
volatile time spans ("26h ago", "3d ago"), which are normalized to
"T ago" because they grow with wall-clock time against the fixed-date
fixture database.

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
from app.messages import handle_message

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
    ('fireid_found', 'fireid C10784 (49.06, -120.79)'),
    ('fireid_not_found', 'fireid NOPE999'),
    ('keyword_help', 'help'),
    ('keyword_usage', 'usage'),
    ('keyword_usage_with_location', 'usage (49.2, -123.1)'),
]


class TestGoldenResponses:
    """Every user-visible response shape, pinned."""

    @pytest.mark.parametrize('name,message', SCENARIOS)
    def test_fire_and_service_responses(self, name, message):
        with patch('app.messages.get_aqi', return_value=42):
            _check(name, handle_message(message))

    def test_aqi_absent_when_unavailable(self):
        with patch('app.messages.get_aqi', return_value=None):
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
