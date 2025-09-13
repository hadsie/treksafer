#!/usr/bin/env python3
"""
cli_connect.py — send a message to a running CLI transporter and print the response.

Defaults:
  host = 127.0.0.1
  port = 8888
  timeout = 30s
"""

import argparse
import socket
import sys

def send_message(host: str, port: int, message: str, timeout: float, append_newline: bool) -> bytes:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            payload = message.encode("utf-8")
            if append_newline:
                payload += b"\n"
            sock.sendall(payload)

            chunks = []
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    # No more data within timeout; assume server is done
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    except (ConnectionRefusedError, socket.timeout) as e:
        print(f"[error] Connection failed or timed out: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"[error] Socket error: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Send a test message to the CLI transporter and print the response.")
    parser.add_argument("message", nargs="+", help="Message to send (wrap in quotes if it has spaces).")
    parser.add_argument("--host", default="127.0.0.1", help="Host to connect to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8888, help="Port to connect to (default: 8888)")
    parser.add_argument("--timeout", type=float, default=30, help="Socket timeout in seconds (default: 30)")
    parser.add_argument("--append-newline", action="store_true",
                        help="Append a newline after the message (some servers expect line-terminated input).")
    args = parser.parse_args()

    msg = " ".join(args.message)
    raw = send_message(args.host, args.port, msg, args.timeout, args.append_newline)

    if not raw:
        print("[warn] No data received from server.", file=sys.stderr)
        sys.exit(2)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Fall back to a safe representation if response isn't valid UTF-8
        text = raw.decode("utf-8", errors="replace")

    print(text.strip())
    sys.exit(0)

if __name__ == "__main__":
    main()
