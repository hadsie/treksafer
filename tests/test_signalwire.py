import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import SecretStr, ValidationError
from signalwire.relay import RelayError

from app import optout
from app.config import SignalWireConfig, get_config
from app.transport.signalwire import SignalWireTransport


@pytest.fixture(autouse=True)
def optout_db(tmp_path, monkeypatch):
    """Point the compliance store at a throwaway database and mark the
    default sender known, so replies carry no first-contact notice."""
    db = str(tmp_path / 'optouts.db')
    monkeypatch.setattr(get_config(), 'optout_database', db)
    optout.first_contact(db, '+15559876543')
    return db


@pytest.fixture
def signalwire_config():
    """Create a mock SignalWire configuration."""
    return SignalWireConfig(
        type="signalwire",
        enabled=True,
        project_id=SecretStr("test_project_id"),
        api_token=SecretStr("test_api_token"),
        phone_number="+15551234567",
    )


@pytest.fixture
def transport(signalwire_config):
    """A transport with a mocked RELAY client ready to receive messages."""
    t = SignalWireTransport(signalwire_config)
    t._client = Mock()
    sent = Mock()
    sent.message_id = "msg_12345"
    t._client.send_message = AsyncMock(return_value=sent)
    return t


def _incoming(from_number="+15559876543", body="FIRECHECK: (49.25, -123.10)"):
    msg = Mock()
    msg.from_number = from_number
    msg.body = body
    return msg


class TestOnMessage:
    """Test suite for inbound message handling."""

    @pytest.mark.asyncio
    async def test_processes_and_replies(self, transport, signalwire_config):
        """An incoming message is processed and the response is sent back."""
        with patch("app.transport.signalwire.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["No fires reported within 100km"]

            await transport._on_message(_incoming())

            mock_handle.assert_called_once_with("FIRECHECK: (49.25, -123.10)", "+15559876543", record=True)
            transport._client.send_message.assert_called_once_with(
                context="treksafer",
                from_number=signalwire_config.phone_number,
                to_number="+15559876543",
                body="No fires reported within 100km",
            )

    @pytest.mark.asyncio
    async def test_handles_send_failure(self, transport):
        """A RELAY send error is logged, not raised."""
        transport._client.send_message = AsyncMock(side_effect=RelayError(500, "boom"))

        with patch("app.transport.signalwire.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["Test response"]

            # Should not raise even though the reply fails.
            await transport._on_message(_incoming())

            transport._client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_configured_context(self, signalwire_config):
        """Replies are sent on the configured context, not a hardcoded one."""
        cfg = signalwire_config.model_copy(update={"context": "custom-ctx"})
        transport = SignalWireTransport(cfg)
        transport._client = Mock()
        transport._client.send_message = AsyncMock(return_value=Mock(message_id="m1"))

        with patch("app.transport.signalwire.safe_handle_message", return_value=["ok"]):
            await transport._on_message(_incoming())

        assert transport._client.send_message.call_args.kwargs["context"] == "custom-ctx"

    @pytest.mark.asyncio
    async def test_replies_with_fire_data(self, transport):
        """A fire-check request is routed through safe_handle_message and replied to."""
        with patch("app.transport.signalwire.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["Fire: Test Fire (K12345)\n12km NW\nSize: 100 ha"]

            await transport._on_message(_incoming(body="FIRECHECK: (51.398720, -116.491640)"))

            mock_handle.assert_called_once_with("FIRECHECK: (51.398720, -116.491640)", "+15559876543", record=True)
            assert transport._client.send_message.call_count == 1


class TestMultiSegmentSending:
    """Each segment of a reply goes out as its own SMS."""

    @pytest.mark.asyncio
    async def test_each_segment_sent_separately(self, transport):
        with patch("app.transport.signalwire.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["first", "second", "third"]

            await transport._on_message(_incoming())

            bodies = [c.kwargs["body"] for c in transport._client.send_message.call_args_list]
            assert bodies == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_failed_segment_does_not_stop_the_rest(self, transport):
        transport._client.send_message = AsyncMock(
            side_effect=[RelayError(500, "boom"), Mock(message_id="m2")])

        with patch("app.transport.signalwire.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["first", "second"]

            await transport._on_message(_incoming())

            assert transport._client.send_message.call_count == 2


class TestSignalWireTransport:
    """Test suite for SignalWireTransport lifecycle."""

    def test_transport_initialization(self, signalwire_config):
        """Transport should initialize with config and no client."""
        transport = SignalWireTransport(signalwire_config)

        assert transport.config == signalwire_config
        assert transport._client is None
        assert transport._stopping is False

    def test_send_not_implemented(self, signalwire_config):
        """send() method should raise NotImplementedError."""
        transport = SignalWireTransport(signalwire_config)

        with pytest.raises(NotImplementedError, match="SignalWireTransport.send"):
            transport.send("+15551234567", "test message")

    @pytest.mark.asyncio
    async def test_listen_uses_default_relay_gateway(self, signalwire_config):
        """No host is passed; the SDK connects to its default RELAY gateway."""
        transport = SignalWireTransport(signalwire_config)
        transport._stopping = True  # make listen() exit immediately

        with patch("app.transport.signalwire.RelayClient") as mock_client:
            await transport.listen()

        _, kwargs = mock_client.call_args
        assert "host" not in kwargs
        assert kwargs["contexts"] == ["treksafer"]

    @pytest.mark.asyncio
    async def test_stop_disconnects_client(self, transport):
        """stop() sets the stop flag and disconnects the client."""
        transport._client.disconnect = AsyncMock()

        await transport.stop()

        assert transport._stopping is True
        transport._client.disconnect.assert_awaited_once()


class TestSignalWireConfig:
    """Validation of required fields when the transport is enabled."""

    def test_phone_number_required_when_enabled(self):
        """phone_number is mandatory once the transport is enabled."""
        with pytest.raises(ValidationError, match="phone_number"):
            SignalWireConfig(
                type="signalwire",
                enabled=True,
                project_id=SecretStr("p"),
                api_token=SecretStr("t"),
            )

    def test_fields_optional_when_disabled(self):
        """No fields are required when the transport is disabled."""
        cfg = SignalWireConfig(type="signalwire", enabled=False)
        assert cfg.phone_number is None

    def test_context_defaults_to_treksafer(self):
        """context is optional and defaults to 'treksafer'."""
        cfg = SignalWireConfig(type="signalwire", enabled=False)
        assert cfg.context == "treksafer"
