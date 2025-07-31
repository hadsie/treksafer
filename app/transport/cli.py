import socket

from app.messages import handle_message
from .base import Transport


class CLITransport(Transport):
    def __init__(self, params: dict):
        self.host = params['host']
        self.port = params['port']
        self.server_socket = None

    def send(self, recipient, content):
        raise NotImplementedError("CLITransport.send_message is not used.")

    def listen(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)

        print(f"[CLITransport] Listening on {self.host}:{self.port}")

        while True:
            client_socket, addr = self.server_socket.accept()
            print(f"[CLITransport] Connection from {addr}")
            data = client_socket.recv(4096)
            if not data:
                client_socket.close()
                continue

            message = data.decode('utf-8').strip()
            print(f"[CLITransport] Received: {message}")

            # Process the message
            response = self.on_incoming_message(message)

            # Send back the response
            client_socket.sendall(response.encode('utf-8'))
            client_socket.close()

    def on_incoming_message(self, message):
        response = handle_message(message)
        return response
