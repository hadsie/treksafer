
import asyncio
import logging
from pathlib import Path
from typing import Optional

import websockets
from signalwire.relay import RelayClient, RelayError
from signalwire.relay.event import MessageReceiveEvent

from app.messages import safe_handle_message
from app.config import SignalWireConfig
from .base import BaseTransport

# Time to wait before reconnecting after a dropped connection (in seconds)
RECONNECT_DELAY = 5


class SignalWireTransport(BaseTransport):
    """Async transport adapter for SignalWire SMS via the RELAY realtime client."""

    def __init__(self, config: SignalWireConfig):
        self.config = config
        self.log = logging.getLogger(__name__.split(".", 1)[0])
        self.sms_log = self._setup_sms_logger()
        self._client: Optional[RelayClient] = None
        self._stopping = False

    @staticmethod
    def _setup_sms_logger() -> logging.Logger:
        # separate SMS-only log
        sms_log = logging.getLogger("sms")
        if not sms_log.handlers:
            fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
            Path("logs").mkdir(exist_ok=True)
            h = logging.FileHandler("logs/sms.log")
            h.setFormatter(logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S"))
            sms_log.setLevel(logging.DEBUG)
            sms_log.addHandler(h)
        return sms_log

    async def listen(self) -> None:
        # RELAY authenticates with project + token against the SDK's default
        # gateway (relay.signalwire.com); the space domain is REST-only.
        self._client = RelayClient(
            project=self.config.project_id.get_secret_value(),
            token=self.config.api_token.get_secret_value(),
            contexts=[self.config.context],
        )
        self._client.on_message(self._on_message)

        # Handle connect/reconnect; the SDK's blocking run() would own the event
        # loop and clash with the shared loop transports run in.
        while not self._stopping:
            try:
                await self._client.connect()
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
                self.log.warning("SignalWire RELAY connection failed: %s", e)
            else:
                self.log.info("SignalWire RELAY connected (context: %s).", self.config.context)
                await self._await_disconnect()

            if self._stopping:
                break
            self.log.info("Reconnecting to SignalWire RELAY in %ss.", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)

    async def _await_disconnect(self) -> None:
        """Block until the RELAY receive loop ends (connection lost or stop())."""
        recv = self._client._recv_task
        if recv is None:
            return
        try:
            await recv
        except asyncio.CancelledError:
            pass

    async def _on_message(self, message: MessageReceiveEvent) -> None:
        self.log.info("SignalWire SMS received incoming message from %s.", message.from_number)
        self.sms_log.info("From: %s, Body: %s", message.from_number, message.body)

        response = safe_handle_message(message.body)

        try:
            result = await self._client.send_message(
                context=self.config.context,
                from_number=self.config.phone_number,
                to_number=message.from_number,
                body=response,
            )
        except RelayError as e:
            self.log.warning("Failed to reply to %s: %s", message.from_number, e)
        else:
            self.log.info("Replied to %s (msg id %s).", message.from_number, result.message_id)

        self.sms_log.info("Reply: %s", response)

    async def stop(self) -> None:
        self._stopping = True
        if self._client is not None:
            await self._client.disconnect()

    def send(self, recipient: str, content: str):
        raise NotImplementedError("SignalWireTransport.send() is unused.")
