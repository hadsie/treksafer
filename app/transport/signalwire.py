
import asyncio
import logging
from typing import Optional

from signalwire.relay.consumer import Consumer

from app.messages import handle_message
from app.config import SignalWireConfig
from .base import BaseTransport


class CustomConsumer(Consumer):

    def __init__(self, project: str, token: str, from_number: str, logger: logging.Logger):
        super().__init__(project=project, token=token, contexts=["treksafer"])
        self.from_number = from_number
        self.log = logger
        self.sms_log = self._setup_sms_logger()

    def _setup_sms_logger(self) -> logging.Logger:
        # separate SMS-only log
        sms_log = logging.getLogger("sms")
        if not sms_log.handlers:
            fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
            h = logging.FileHandler("logs/sms.log")
            h.setFormatter(logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S"))
            sms_log.setLevel(logging.DEBUG)
            sms_log.addHandler(h)
        return sms_log

    async def ready(self) -> None:
        self.log.info("SignalWire consumer ready (context: treksafer).")

    async def on_incoming_message(self, message):
        self.log.info("SignalWire SMS received incoming message from %s.", message.from_number)
        response = handle_message(message.body)

        self.sms_log.info("From: %s, Body: %s", message.from_number, message.body)

        result = await self.client.messaging.send(
            context="treksafer",
            from_number=self.from_number,
            to_number=message.from_number,
            body=response,
        )

        if result.successful:
            self.log.info("Replied to %s (msg id %s).", message.from_number, result.message_id)
        else:
            self.log.warning("Failed to reply to %s.", message.from_number)

        self.sms_log.info("Reply: %s", response)


class SignalWireTransport(BaseTransport):
    """Async transport adapter for SignalWire SMS."""

    def __init__(self, cfg: SignalWireConfig):
        self.cfg = cfg
        self._consumer: Optional[CustomConsumer] = None
        self._thread_loop: Optional[asyncio.AbstractEventLoop] = None
        self._future: Optional[asyncio.Future] = None

    async def listen(self) -> None:
        package_logger = logging.getLogger(__name__.split(".", 1)[0])

        self._consumer = CustomConsumer(
            project=self.cfg.project_id.get_secret_value(),
            token=self.cfg.api_token.get_secret_value(),
            from_number=self.cfg.phone_number,
            logger=package_logger,
        )

        # run consumer in a separate thread, keep the thread's loop reference so
        # we can shut it down later.
        self._future = asyncio.create_task(
            asyncio.to_thread(_run_consumer_in_thread, self._consumer, self)
        )

        await self._future

    async def stop(self) -> None:
        if self._consumer and self._thread_loop:
            coro = getattr(self._consumer, "end", None) or getattr(self._consumer, "disconnect")
            if asyncio.iscoroutinefunction(coro):
                fut = asyncio.run_coroutine_threadsafe(coro(), self._thread_loop)
                await asyncio.wrap_future(fut)
            else:
                self._thread_loop.call_soon_threadsafe(coro)

        # Wait for the background thread to finish; no explicit cancel needed
        if self._future and not self._future.done():
            await self._future

    def send(self, recipient: str, content: str):
        raise NotImplementedError("SignalWireTransport.send() is unused.")

def _run_consumer_in_thread(consumer: Consumer, parent: "SignalWireTransport"):
    """Runs SignalWire Consumer in its own thread-local event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    parent._thread_loop = loop          # give stop() access to this loop
    try:
        consumer.run()                  # blocks until end()/disconnect()
    finally:
        loop.close()
