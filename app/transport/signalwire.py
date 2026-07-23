
import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

import websockets
from signalwire.relay import RelayClient, RelayError
from signalwire.relay.event import MessageReceiveEvent

from app import optout
from app.helpers import quoted
from app.messages import Messages, safe_handle_message
from app.messaging.assembler import segment_cost
from app.config import SignalWireConfig, get_config
from .base import BaseTransport

# Time to wait before reconnecting after a dropped connection (in seconds)
RECONNECT_DELAY = 5

# Carrier-standard opt-out/opt-in keywords, honored only when they are the
# whole message so "stop" inside a real request never opts anyone out.
_OPT_OUT_PATTERN = re.compile(
    r'\s*(stop|stopall|unsubscribe|cancel|end|quit)\s*', re.IGNORECASE)
_OPT_IN_PATTERN = re.compile(r'\s*(start|unstop)\s*', re.IGNORECASE)


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
        # Separate SMS-only log. scripts/digest.py scrapes it, so the path
        # is shared config.
        sms_log = logging.getLogger("sms")
        # Don't push SMS records to the app log.
        sms_log.propagate = False
        if not sms_log.handlers:
            fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
            path = Path(get_config().monitoring.sms_log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            h = logging.FileHandler(path)
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

    def _route(self, number: str, body: str) -> Optional[list[str]]:
        """Resolve the reply as a list of single-SMS messages, applying
        opt-out compliance around the message pipeline.

        STOP records the number and gets only the confirmation; START
        clears it. An opted-out number gets no reply at all -- the check
        sits here, the last place before a send, so suppression holds no
        matter what the pipeline produces. Returns None for no reply.
        """
        db = get_config().optout_database
        opting_out = _OPT_OUT_PATTERN.fullmatch(body or '') is not None
        opting_in = _OPT_IN_PATTERN.fullmatch(body or '') is not None
        try:
            if opting_out:
                optout.opt_out(db, number)
                self.log.info("Opt-out recorded for %s.", number)
                return [Messages().opt_out_confirmed()]
            if opting_in:
                optout.opt_in(db, number)
                self.log.info("Opt-out cleared for %s.", number)
                return [Messages().opt_in_confirmed()]
            suppressed = optout.is_opted_out(db, number)
        except (sqlite3.Error, OSError) as e:
            self.log.error("Opt-out store unavailable: %s", e)
            if opting_out or opting_in:
                # An opt-out that cannot be recorded must not be confirmed.
                return [Messages().system_error()]
            # A failed check must not block safety information; the error
            # above alerts the operator.
            suppressed = False
        if suppressed:
            self.log.info("No reply to %s: recipient opted out.", number)
            return None
        return safe_handle_message(body)

    async def _on_message(self, message: MessageReceiveEvent) -> None:
        self.log.info("SignalWire SMS received incoming message from %s.", message.from_number)
        self.sms_log.info("From: %s\n%s", message.from_number, quoted(message.body))

        segments = self._route(message.from_number, message.body)
        if segments is None:
            self.sms_log.info("Reply: (suppressed: recipient opted out)")
            return

        # Each segment is a self-contained message, so a failed send does
        # not stop the rest: deliver whatever can be delivered.
        for i, segment in enumerate(segments, 1):
            try:
                result = await self._client.send_message(
                    context=self.config.context,
                    from_number=self.config.phone_number,
                    to_number=message.from_number,
                    body=segment,
                )
            except RelayError as e:
                self.log.error("Failed to reply to %s (message %d/%d): %s",
                               message.from_number, i, len(segments), e)
            else:
                self.log.info("Replied to %s (msg id %s, message %d/%d).",
                              message.from_number, result.message_id, i, len(segments))

        # One record per exchange with split marks on each message
        # annotated with its position and segment cost.
        logged = "\n\n".join(
            f"----- SMS {i}/{len(segments)} ({segment_cost(s)}) -----\n{s}"
            for i, s in enumerate(segments, 1))
        self.sms_log.info("Reply:\n%s", quoted(logged))

    async def stop(self) -> None:
        self._stopping = True
        if self._client is not None:
            await self._client.disconnect()

    def send(self, recipient: str, content: str):
        raise NotImplementedError("SignalWireTransport.send() is unused.")
