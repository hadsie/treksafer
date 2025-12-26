import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import logging

from app.transport.signalwire import SignalWireTransport, CustomConsumer
from app.config import SignalWireConfig
from pydantic import SecretStr


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
def mock_consumer():
    """Create a mock CustomConsumer."""
    consumer = Mock(spec=CustomConsumer)
    consumer.client = Mock()
    consumer.client.messaging = Mock()
    consumer.log = logging.getLogger("test")
    consumer.sms_log = logging.getLogger("test.sms")
    return consumer


class TestCustomConsumer:
    """Test suite for CustomConsumer message handling."""

    @pytest.mark.asyncio
    async def test_on_incoming_message_processes_and_replies(self, signalwire_config):
        """Consumer should process incoming message and send response."""
        # Create consumer
        consumer = CustomConsumer(
            project=signalwire_config.project_id.get_secret_value(),
            token=signalwire_config.api_token.get_secret_value(),
            from_number=signalwire_config.phone_number,
            logger=logging.getLogger("test"),
        )

        # Mock the messaging client
        consumer.client = Mock()
        consumer.client.messaging = Mock()
        mock_send_result = Mock()
        mock_send_result.successful = True
        mock_send_result.message_id = "msg_12345"
        consumer.client.messaging.send = AsyncMock(return_value=mock_send_result)

        # Create mock incoming message
        mock_message = Mock()
        mock_message.from_number = "+15559876543"
        mock_message.body = "FIRECHECK: (49.25, -123.10)"

        # Mock handle_message to return a response
        with patch("app.transport.signalwire.handle_message") as mock_handle:
            mock_handle.return_value = "No fires reported within 100km"

            # Process the message
            await consumer.on_incoming_message(mock_message)

            # Verify handle_message was called with message body
            mock_handle.assert_called_once_with("FIRECHECK: (49.25, -123.10)")

            # Verify response was sent via SignalWire
            consumer.client.messaging.send.assert_called_once_with(
                context="treksafer",
                from_number=signalwire_config.phone_number,
                to_number="+15559876543",
                body="No fires reported within 100km",
            )

    @pytest.mark.asyncio
    async def test_on_incoming_message_handles_send_failure(self, signalwire_config):
        """Consumer should handle failed message send gracefully."""
        consumer = CustomConsumer(
            project=signalwire_config.project_id.get_secret_value(),
            token=signalwire_config.api_token.get_secret_value(),
            from_number=signalwire_config.phone_number,
            logger=logging.getLogger("test"),
        )

        # Mock failed send
        consumer.client = Mock()
        consumer.client.messaging = Mock()
        mock_send_result = Mock()
        mock_send_result.successful = False
        consumer.client.messaging.send = AsyncMock(return_value=mock_send_result)

        mock_message = Mock()
        mock_message.from_number = "+15559876543"
        mock_message.body = "FIRECHECK: (49.25, -123.10)"

        with patch("app.transport.signalwire.handle_message") as mock_handle:
            mock_handle.return_value = "Test response"

            # Should not raise exception even if send fails
            await consumer.on_incoming_message(mock_message)

            # Verify send was attempted
            consumer.client.messaging.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_incoming_message_with_fire_data(self, signalwire_config):
        """Consumer should correctly process fire check requests."""
        consumer = CustomConsumer(
            project=signalwire_config.project_id.get_secret_value(),
            token=signalwire_config.api_token.get_secret_value(),
            from_number=signalwire_config.phone_number,
            logger=logging.getLogger("test"),
        )

        consumer.client = Mock()
        consumer.client.messaging = Mock()
        mock_send_result = Mock()
        mock_send_result.successful = True
        mock_send_result.message_id = "msg_67890"
        consumer.client.messaging.send = AsyncMock(return_value=mock_send_result)

        mock_message = Mock()
        mock_message.from_number = "+15559876543"
        mock_message.body = "FIRECHECK: (51.398720, -116.491640)"

        with patch("app.transport.signalwire.handle_message") as mock_handle:
            # Simulate a fire found response
            mock_handle.return_value = "Fire: Test Fire (K12345)\n12km NW\nSize: 100 ha"

            await consumer.on_incoming_message(mock_message)

            # Verify correct message processing
            mock_handle.assert_called_once_with("FIRECHECK: (51.398720, -116.491640)")
            assert consumer.client.messaging.send.call_count == 1


class TestSignalWireTransport:
    """Test suite for SignalWireTransport."""

    def test_transport_initialization(self, signalwire_config):
        """Transport should initialize with config."""
        transport = SignalWireTransport(signalwire_config)

        assert transport.cfg == signalwire_config
        assert transport._consumer is None
        assert transport._thread_loop is None
        assert transport._future is None

    def test_send_not_implemented(self, signalwire_config):
        """send() method should raise NotImplementedError."""
        transport = SignalWireTransport(signalwire_config)

        with pytest.raises(NotImplementedError, match="SignalWireTransport.send"):
            transport.send("+15551234567", "test message")
