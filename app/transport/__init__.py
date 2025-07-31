
from app.config import get_config

#from .signal import SignalWireTransport
from .cli import CLITransport
#from .email import EmailTransport

TRANSPORT_FACTORIES = {
    #"signalwire": SignalWireTransport,
    "cli": CLITransport,
    #"email": EmailTransport,
}

def get_transport_config(type):
    settings = get_config()
    for cfg in settings.transports:
        if cfg.type == type:
            return cfg
    return False

def get_transports():
    settings = get_config()
    transport_instances = []

    for cfg in settings.transports:
        if not cfg.enabled:
            continue
        transport_class = TRANSPORT_FACTORIES[cfg.type]
        # If you used SecretStr, unwrap secrets here:
        transport = transport_class(cfg.dict())
        transport_instances.append(transport)

    return transport_instances
