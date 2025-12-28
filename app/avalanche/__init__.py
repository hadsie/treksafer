"""Avalanche forecast integration with multi-provider support.

Usage:
    avalanche = AvalancheReport((49.25, -123.1))
    if avalanche.has_data():
        forecast = avalanche.get_forecast()
        print(forecast)
"""
from .base import AvalancheProvider
from .avcan import AvalancheCanadaProvider
from .quebec import AvalancheQuebecProvider
from .report import AvalancheReport


__all__ = [
    'AvalancheProvider',
    'AvalancheCanadaProvider',
    'AvalancheQuebecProvider',
    'AvalancheReport',
]
