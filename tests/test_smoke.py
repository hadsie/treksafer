"""
Smoke tests for TrekSafer transports.

These tests verify that the transport layer works end-to-end with real configuration.
They should be run after deployment to staging/production to ensure the system is working.

Run with: pytest -m smoke -v -s

NOTE: These tests require:
- CLI transport: The app must be running (python -m app)
- SignalWire: Valid credentials in config.yaml
"""

import socket
import pytest

from app.config import get_config
from app.transport import get_transport_config
from app.transport.signalwire import SignalWireTransport


def _send_cli_message(host: str, port: int, message: str, timeout: float = 5.0) -> str:
    """Send a message to the CLI transport and return the response."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(message.encode("utf-8"))
        response = sock.recv(4096).decode("utf-8")
        return response.strip()


@pytest.mark.smoke
def test_cli_transport_smoke():
    """Verify CLI transport works end-to-end with real config."""
    # Load real config
    settings = get_config()
    cli_config = get_transport_config(settings, 'cli')

    # Check if CLI transport is enabled
    if not cli_config or not cli_config.enabled:
        pytest.skip("CLI transport not enabled in config.yaml")

    # Try to connect and send test message
    try:
        response = _send_cli_message(
            host=cli_config.host,
            port=cli_config.port,
            message="FIRECHECK: (49.25,-123.10)",  # Vancouver coordinates (no fires)
        )
    except ConnectionRefusedError:
        pytest.skip(
            f"CLI transport not running on {cli_config.host}:{cli_config.port}. "
            f"Start the app with: python -m app"
        )
    except socket.timeout:
        pytest.fail(f"CLI transport timed out on {cli_config.host}:{cli_config.port}")

    # Verify we got a response
    assert response, "CLI transport returned empty response"

    # Response should mention fires or error
    response_lower = response.lower()
    assert "fire" in response_lower or "error" in response_lower, \
        f"Unexpected response format: {response}"

    # In test environment, verify exact content based on config
    if settings.env == "test":
        expected_radius = min(settings.fire_radius, settings.max_radius)
        assert f"No fires reported within a {expected_radius}km radius" in response, \
            f"Expected message about {expected_radius}km radius, got: {response}"

    print(f"\n✅ CLI transport smoke test passed")
    print(f"   Response: {response[:100]}...")


@pytest.mark.smoke
def test_signalwire_transport_smoke():
    """Verify SignalWire transport initializes correctly with real config."""
    # Load real config
    settings = get_config()
    sw_config = get_transport_config(settings, 'signalwire')

    # Check if SignalWire transport is enabled
    if not sw_config or not sw_config.enabled:
        pytest.skip("SignalWire transport not enabled in config.yaml")

    # Verify initialization works with real config
    try:
        transport = SignalWireTransport(sw_config)
        assert transport.cfg.phone_number is not None, "SignalWire phone number not configured"
        assert transport.cfg.project_id is not None, "SignalWire project_id not configured"
        assert transport.cfg.api_token is not None, "SignalWire api_token not configured"
    except ValueError as e:
        pytest.fail(f"SignalWire transport failed validation: {e}")
    except Exception as e:
        pytest.fail(f"SignalWire transport failed to initialize: {e}")

    # Print manual test instructions
    print("\n" + "="*70)
    print("✅ SignalWire transport initialization successful")
    print("="*70)
    print("\nMANUAL SIGNALWIRE TEST:")
    print(f"1. Send this SMS to {sw_config.phone_number}:")
    print("   FIRECHECK: (49.25,-123.10)")
    print("\n2. Expected response should contain:")
    print(f"   'No fires reported within a {settings.fire_radius}km radius'")
    print("\n3. Or try a location with known fires:")
    print("   FIRECHECK: (51.398720, -116.491640)")
    print("   (This should return fire information)")
    print("="*70)
