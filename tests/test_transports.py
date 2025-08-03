import socket
import pytest

from app.transport import get_transport_config
from app.config import settings

def _cli_transport(message):
    config = get_transport_config(settings, 'cli')
    try:
        with socket.create_connection((config.host, config.port)) as sock:
            print(f"Connected to CLI transport at {config.host}:{config.port}")
            sock.sendall(message.encode("utf-8"))

            response = sock.recv(4096).decode("utf-8")
            print(f"Received response: {response.strip()}")
            return response
    except (ConnectionRefusedError, socket.timeout) as e:
        print(f"Failed to connect or timed out: {e}")
    except AssertionError as e:
        print(f"CLI transport test failed: {e}")

# Run with pytest -m smoke

@pytest.mark.smoke
def test_cli_transport_no_fires():
    message = "FIRECHECK: (49.25,-123.10)"
    response = _cli_transport(message)
    assert response.startswith("No fires reported within")

@pytest.mark.smoke
def test_cli_transport_fires_found():
    message = "FIRECHECK: (51.398720, -116.491640)"
    response = _cli_transport(message)
    assert response.count("Fire") == 4

