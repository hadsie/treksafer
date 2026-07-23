import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch

import pytest

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

        # Mock safe_handle_message to return a response
        with patch("app.transport.cli.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["No fires reported within 100km"]

            # Process the message
            await transport._handle_client(mock_reader, mock_writer)

            # Verify safe_handle_message was called with correct input
            mock_handle.assert_called_once_with(test_message, "cli", record=True)

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

        with patch("app.transport.cli.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["Test response"]

            await transport._handle_client(mock_reader, mock_writer)

            # Verify whitespace was stripped
            mock_handle.assert_called_once_with("FIRECHECK: (49.25,-123.10)", "cli", record=True)

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

        with patch("app.transport.cli.safe_handle_message") as mock_handle:
            # Simulate multiple fires found
            fire_response = "Fire: Test Fire (K12345)\n12km NW\nSize: 100 ha\n\nFire: Another Fire (K67890)\n15km SE\nSize: 50 ha"
            mock_handle.return_value = fire_response.split("\n\n")

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

        with patch("app.transport.cli.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["No valid GPS coordinates found."]

            await transport._handle_client(mock_reader, mock_writer)

            # Verify error message was written
            expected_response = "No valid GPS coordinates found.\n"
            mock_writer.write.assert_called_once_with(expected_response.encode("utf-8"))

    @pytest.mark.asyncio
    async def test_handle_client_health_command(self, cli_config):
        """The health command returns the JSON health report and bypasses
        message parsing entirely."""
        transport = CLITransport(cli_config)

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = Mock(spec=asyncio.StreamWriter)
        mock_writer.write = Mock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = Mock()
        mock_writer.wait_closed = AsyncMock()

        mock_reader.read = AsyncMock(return_value=b"health\n")

        with patch("app.transport.cli.safe_handle_message") as mock_handle:
            await transport._handle_client(mock_reader, mock_writer)
            mock_handle.assert_not_called()

        report = json.loads(mock_writer.write.call_args[0][0].decode("utf-8"))
        assert report["status"] == "ok"
        assert set(report["sources"]) == {"BC", "AB", "ON", "CA", "US"}
        assert report["sources"]["BC"]["latest_fetch"] is not None

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

        with patch("app.transport.cli.safe_handle_message") as mock_handle:
            mock_handle.return_value = ["Résultat: Aucun feu"]

            await transport._handle_client(mock_reader, mock_writer)

            # Verify UTF-8 encoding
            expected_response = "Résultat: Aucun feu\n"
            mock_writer.write.assert_called_once_with(expected_response.encode("utf-8"))


@pytest.fixture
def sw_transport(tmp_path, monkeypatch):
    """A SignalWire transport wired to a throwaway opt-out database."""
    from app.config import SignalWireConfig, get_config
    from app.transport.signalwire import SignalWireTransport
    monkeypatch.setattr(get_config(), 'optout_database',
                        str(tmp_path / 'optouts.db'))
    return SignalWireTransport(SignalWireConfig(type='signalwire', enabled=False))


class TestSignalWireOptOut:
    """STOP/START compliance: recorded persistently, suppression enforced in
    the last place before a send."""

    NUMBER = '+15551230001'

    def _request(self, sw_transport, body='fires (50.5, -121.0)'):
        """Route a request with the pipeline stubbed and the sender already
        known, so replies carry no opt-in notice."""
        from app import optout
        from app.config import get_config
        optout.first_contact(get_config().optout_database, self.NUMBER)
        with patch('app.transport.signalwire.safe_handle_message',
                   return_value=['OK']):
            return sw_transport._route(self.NUMBER, body)

    def test_stop_records_and_returns_only_the_confirmation(self, sw_transport):
        from app.messages import Messages

        response = sw_transport._route(self.NUMBER, 'STOP')

        assert response == [Messages().opt_out_confirmed()]
        assert sw_transport._route(self.NUMBER, 'fires (50.5, -121.0)') == []

    @pytest.mark.parametrize('body', ['stop', ' Unsubscribe ', 'QUIT',
                                      'stopall', 'End', 'cancel'])
    def test_every_opt_out_variant_suppresses(self, sw_transport, body):
        sw_transport._route(self.NUMBER, body)

        assert sw_transport._route(self.NUMBER, 'anything at all') == []

    def test_start_clears_the_opt_out(self, sw_transport):
        from app.messages import Messages
        sw_transport._route(self.NUMBER, 'STOP')

        response = sw_transport._route(self.NUMBER, 'START')

        assert response == [Messages().opt_in_confirmed()]
        assert self._request(sw_transport) == ['OK']

    def test_stop_inside_a_request_is_not_an_opt_out(self, sw_transport):
        assert self._request(
            sw_transport, 'please stop the fires (50.5, -121.0)') == ['OK']
        assert self._request(sw_transport) == ['OK']

    def test_suppression_bypasses_the_pipeline_entirely(self, sw_transport):
        sw_transport._route(self.NUMBER, 'STOP')

        with patch('app.transport.signalwire.safe_handle_message') as pipeline:
            assert sw_transport._route(self.NUMBER, 'fires (50.5, -121.0)') == []

        pipeline.assert_not_called()

    def test_unrecordable_opt_out_is_not_confirmed(self, sw_transport, monkeypatch):
        import sqlite3
        from app.messages import Messages
        monkeypatch.setattr('app.transport.signalwire.optout.opt_out',
                            Mock(side_effect=sqlite3.Error('locked')))

        response = sw_transport._route(self.NUMBER, 'STOP')

        assert response == [Messages().system_error()]

    def test_failed_check_does_not_block_information(self, sw_transport, monkeypatch, caplog):
        import logging
        import sqlite3
        monkeypatch.setattr('app.transport.signalwire.optout.is_opted_out',
                            Mock(side_effect=sqlite3.Error('locked')))

        with caplog.at_level(logging.ERROR):
            assert self._request(sw_transport) == ['OK']

        assert 'Opt-out store unavailable' in caplog.text

    @pytest.mark.asyncio
    async def test_opted_out_number_gets_no_send(self, sw_transport):
        sw_transport._route(self.NUMBER, 'STOP')
        sw_transport._client = AsyncMock()
        event = Mock(from_number=self.NUMBER, body='fires (50.5, -121.0)')

        await sw_transport._on_message(event)

        sw_transport._client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_confirmation_is_sent(self, sw_transport):
        from app.messages import Messages
        sw_transport._client = AsyncMock()
        event = Mock(from_number=self.NUMBER, body='STOP')

        await sw_transport._on_message(event)

        kwargs = sw_transport._client.send_message.call_args.kwargs
        assert kwargs['body'] == Messages().opt_out_confirmed()
        assert kwargs['to_number'] == self.NUMBER


class TestSignalWireOptInNotice:
    """A number's first message earns the one-time opt-in confirmation,
    sent ahead of the reply itself."""

    NUMBER = '+15551230001'

    def test_first_message_gets_notice_then_reply(self, sw_transport):
        from app.messages import Messages

        with patch('app.transport.signalwire.safe_handle_message',
                   return_value=['OK']):
            response = sw_transport._route(self.NUMBER, 'fires (50.5, -121.0)')

        assert response == [Messages().opt_in_notice(), 'OK']

    def test_second_message_gets_only_the_reply(self, sw_transport):
        with patch('app.transport.signalwire.safe_handle_message',
                   return_value=['OK']):
            sw_transport._route(self.NUMBER, 'fires (50.5, -121.0)')
            response = sw_transport._route(self.NUMBER, 'fires (50.5, -121.0)')

        assert response == ['OK']

    def test_stop_as_first_message_gets_no_notice(self, sw_transport):
        from app.messages import Messages

        response = sw_transport._route(self.NUMBER, 'STOP')

        assert response == [Messages().opt_out_confirmed()]

    def test_failed_record_does_not_block_information(self, sw_transport, monkeypatch, caplog):
        import logging
        import sqlite3
        monkeypatch.setattr('app.transport.signalwire.optout.first_contact',
                            Mock(side_effect=sqlite3.Error('locked')))

        with patch('app.transport.signalwire.safe_handle_message',
                   return_value=['OK']), caplog.at_level(logging.ERROR):
            response = sw_transport._route(self.NUMBER, 'fires (50.5, -121.0)')

        assert response == ['OK']
        assert 'Compliance store unavailable' in caplog.text

    @pytest.mark.asyncio
    async def test_both_messages_are_sent_in_order(self, sw_transport):
        from app.messages import Messages
        sw_transport._client = AsyncMock()
        event = Mock(from_number=self.NUMBER, body='fires (50.5, -121.0)')

        with patch('app.transport.signalwire.safe_handle_message',
                   return_value=['OK']):
            await sw_transport._on_message(event)

        bodies = [c.kwargs['body']
                  for c in sw_transport._client.send_message.call_args_list]
        assert bodies == [Messages().opt_in_notice(), 'OK']


class TestSmsLogFraming:
    """Message content in sms.log is '> '-quoted line by line, so a sender
    can never forge a log record."""

    def test_sms_records_stay_out_of_the_app_log(self, sw_transport):
        import logging

        assert logging.getLogger('sms').propagate is False

    @pytest.mark.asyncio
    async def test_on_message_logs_quoted_body_and_reply(self, sw_transport, caplog):
        import logging
        # The sms logger does not propagate, so capture on it directly.
        sms_logger = logging.getLogger('sms')
        sms_logger.addHandler(caplog.handler)
        sw_transport._client = AsyncMock()
        event = Mock(from_number='+15551230002', body='Fires\n(50.5, -121.0)')
        # Register the sender so the first-contact notice stays out of the
        # framing under test.
        from app import optout
        from app.config import get_config
        optout.first_contact(get_config().optout_database, '+15551230002')

        try:
            with patch('app.transport.signalwire.safe_handle_message',
                       return_value=['line1\nline2']):
                await sw_transport._on_message(event)
        finally:
            sms_logger.removeHandler(caplog.handler)

        messages = [r.getMessage() for r in caplog.records if r.name == 'sms']
        assert 'From: +15551230002\n> Fires\n> (50.5, -121.0)' in messages
        assert ('Reply:\n'
                '> ----- SMS 1/1 (11/160 GSM-7) -----\n'
                '> line1\n'
                '> line2') in messages

    @pytest.mark.asyncio
    async def test_split_reply_marks_each_message_with_cost(self, sw_transport, caplog):
        import logging
        sms_logger = logging.getLogger('sms')
        sms_logger.addHandler(caplog.handler)
        sw_transport._client = AsyncMock()
        event = Mock(from_number='+15551230002', body='Fires\n(50.5, -121.0)')
        # Register the sender so the first-contact notice stays out of the
        # framing under test.
        from app import optout
        from app.config import get_config
        optout.first_contact(get_config().optout_database, '+15551230002')

        try:
            with patch('app.transport.signalwire.safe_handle_message',
                       return_value=['first fire', 'second\n\nfire']):
                await sw_transport._on_message(event)
        finally:
            sms_logger.removeHandler(caplog.handler)

        messages = [r.getMessage() for r in caplog.records if r.name == 'sms']
        assert ('Reply:\n'
                '> ----- SMS 1/2 (10/160 GSM-7) -----\n'
                '> first fire\n'
                '> \n'
                '> ----- SMS 2/2 (12/160 GSM-7) -----\n'
                '> second\n'
                '> \n'
                '> fire') in messages
