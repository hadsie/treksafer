"""
CLI interface for used for local smoke-testing of TrekSafer.

To test, when the app is running, send a message to the configured
port (default 8888) like so:

$ echo 'Fire test: (54.783803, -125.466560)' | nc localhost 8888
"""

import asyncio
from typing import Optional

from app.messages import handle_message
from .base import BaseTransport


class CLITransport(BaseTransport):
    def __init__(self, params: dict):
        self.host = params.host
        self.port = params.port
        self._server: Optional[asyncio.AbstractServer] = None

    def send(self, recipient, content):
        raise NotImplementedError("CLITransport.send_message is not used.")

    async def listen(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        print(f"[CLITransport] Listening on {addrs}")
        await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            print("[CLITransport] Server closed")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(4096)
        message = data.decode("utf-8").strip()
        print(f"[CLITransport] Received: {message}")

        response = handle_message(message)
        writer.write((response + "\n").encode("utf-8"))
        await writer.drain()

        writer.close()
        await writer.wait_closed()
