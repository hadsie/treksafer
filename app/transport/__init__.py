"""
Transport package

A transport is a pluggable message gateway, allows us to simultaneously receive / send from
different sources (SMS, email, etc).
"""

from typing import List, Optional

from app.config import Settings
from .base import BaseTransport
from .signalwire import SignalWireTransport
from .cli import CLITransport
#from .email import EmailTransport

TRANSPORT_FACTORIES = {
    "signalwire": SignalWireTransport,
    "cli": CLITransport,
    #"email": EmailTransport,
}

def get_transport_config(settings: Settings, transport_type: str):
    """
    Return the Pydantic config object for the requested transport type,
    or None if not found.
    """
    for cfg in settings.transports:
        if cfg.type == transport_type:
            return cfg
    return None

def get_transports(settings: Settings) -> List[BaseTransport]:
    instances: list[BaseTransport] = []

    for cfg in settings.transports:
        if not cfg.enabled:
            continue
        factory = TRANSPORT_FACTORIES[cfg.type]
        if factory is None:
            raise ValueError(f"Unsupported transport type '{cfg.type}' in config.")
        instances.append(factory(cfg))

    return instances
