"""Avalanche forecast integration with multi-provider support.

Usage:
    avalanche = AvalancheReport((49.25, -123.1))
    if avalanche.has_data():
        forecast = avalanche.get_forecast()
        print(forecast)
"""
from .base import AvalancheProvider
from .canada import AvalancheCanadaProvider
from .quebec import AvalancheQuebecProvider
from .report import AvalancheReport

# Provider registry
AVALANCHE_PROVIDERS = {
    'CA': AvalancheCanadaProvider,
    'QC': AvalancheQuebecProvider,
}

# Update the registry in report module
from . import report
report.AVALANCHE_PROVIDERS = AVALANCHE_PROVIDERS

__all__ = [
    'AvalancheProvider',
    'AvalancheCanadaProvider',
    'AvalancheQuebecProvider',
    'AvalancheReport',
    'AVALANCHE_PROVIDERS',
]
