import pytest
from unittest.mock import Mock, AsyncMock, patch
import asyncio

from app.transport.cli import CLITransport
from app.config import CLIConfig


@pytest.fixture
def cli_config():
    """Create a mock CLI transport configuration."""
    return CLIConfig(
        type="cli",
        enabled=True,
        host="localhost",
        port=8888,
    )


class TestCLITransport:
    """Test suite for CLI transport message handling."""

    def test_transport_initialization(self, cli_config):
        """Transport should initialize with config."""
        transport = CLITransport(cli_config)

        assert transport.host == "localhost"
        assert transport.port == 8888
        assert transport._server is None

    def test_send_not_implemented(self, cli_config):
        """send() method should raise NotImplementedError."""
        transport = CLITransport(cli_config)

        with pytest.raises(NotImplementedError, match="CLITransport.send"):
            transport.send("recipient", "test message")

    @pytest.mark.asyncio
    async def test_handle_client_processes_message(self, cli_config):
        """Client handler should process message and send response."""
        transport = CLITransport(cli_config)

        # Create mock StreamReader and StreamWriter
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = Mock(spec=asyncio.StreamWriter)
        mock_writer.write = Mock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = Mock()
        mock_writer.wait_closed = AsyncMock()

        # Simulate receiving a message
        test_message = "FIRECHECK: (49.25,-123.10)"
        mock_reader.read = AsyncMock(return_value=test_message.encode("utf-8"))

        # Mock handle_message to return a response
        with patch("app.transport.cli.handle_message") as mock_handle:
            mock_handle.return_value = "No fires reported within 100km"

            # Process the message
            await transport._handle_client(mock_reader, mock_writer)

            # Verify handle_message was called with correct input
            mock_handle.assert_called_once_with(test_message)

            # Verify response was written
            expected_response = "No fires reported within 100km\n"
            mock_writer.write.assert_called_once_with(expected_response.encode("utf-8"))
            mock_writer.drain.assert_called_once()

            # Verify connection was closed
            mock_writer.close.assert_called_once()
            mock_writer.wait_closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_client_strips_whitespace(self, cli_config):
        """Client handler should strip whitespace from messages."""
        transport = CLITransport(cli_config)

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = Mock(spec=asyncio.StreamWriter)
        mock_writer.write = Mock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = Mock()
        mock_writer.wait_closed = AsyncMock()

        # Message with leading/trailing whitespace
        test_message = "  FIRECHECK: (49.25,-123.10)  \n"
        mock_reader.read = AsyncMock(return_value=test_message.encode("utf-8"))

        with patch("app.transport.cli.handle_message") as mock_handle:
            mock_handle.return_value = "Test response"

            await transport._handle_client(mock_reader, mock_writer)

            # Verify whitespace was stripped
            mock_handle.assert_called_once_with("FIRECHECK: (49.25,-123.10)")

    @pytest.mark.asyncio
    async def test_handle_client_with_fire_found_response(self, cli_config):
        """Client handler should correctly handle fire found responses."""
        transport = CLITransport(cli_config)

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = Mock(spec=asyncio.StreamWriter)
        mock_writer.write = Mock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = Mock()
        mock_writer.wait_closed = AsyncMock()

        test_message = "FIRECHECK: (51.398720, -116.491640)"
        mock_reader.read = AsyncMock(return_value=test_message.encode("utf-8"))

        with patch("app.transport.cli.handle_message") as mock_handle:
            # Simulate multiple fires found
            fire_response = "Fire: Test Fire (K12345)\n12km NW\nSize: 100 ha\n\nFire: Another Fire (K67890)\n15km SE\nSize: 50 ha"
            mock_handle.return_value = fire_response

            await transport._handle_client(mock_reader, mock_writer)

            # Verify correct response written
            expected_response = fire_response + "\n"
            mock_writer.write.assert_called_once_with(expected_response.encode("utf-8"))

    @pytest.mark.asyncio
    async def test_handle_client_with_no_gps_error(self, cli_config):
        """Client handler should handle no GPS error message."""
        transport = CLITransport(cli_config)

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = Mock(spec=asyncio.StreamWriter)
        mock_writer.write = Mock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = Mock()
        mock_writer.wait_closed = AsyncMock()

        test_message = "FIRECHECK: no coordinates here"
        mock_reader.read = AsyncMock(return_value=test_message.encode("utf-8"))

        with patch("app.transport.cli.handle_message") as mock_handle:
            mock_handle.return_value = "TrekSafer ERROR: No GPS location found."

            await transport._handle_client(mock_reader, mock_writer)

            # Verify error message was written
            expected_response = "TrekSafer ERROR: No GPS location found.\n"
            mock_writer.write.assert_called_once_with(expected_response.encode("utf-8"))

    @pytest.mark.asyncio
    async def test_handle_client_encodes_utf8(self, cli_config):
        """Client handler should properly encode UTF-8 characters."""
        transport = CLITransport(cli_config)

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = Mock(spec=asyncio.StreamWriter)
        mock_writer.write = Mock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = Mock()
        mock_writer.wait_closed = AsyncMock()

        # Message with UTF-8 characters
        test_message = "FIRECHECK: (49.25,-123.10) Près de Montréal"
        mock_reader.read = AsyncMock(return_value=test_message.encode("utf-8"))

        with patch("app.transport.cli.handle_message") as mock_handle:
            mock_handle.return_value = "Résultat: Aucun feu"

            await transport._handle_client(mock_reader, mock_writer)

            # Verify UTF-8 encoding
            expected_response = "Résultat: Aucun feu\n"
            mock_writer.write.assert_called_once_with(expected_response.encode("utf-8"))
