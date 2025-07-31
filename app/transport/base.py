from abc import ABC, abstractmethod

class Transport(ABC):
    @abstractmethod
    def send(self, recipient, content):
        pass

    @abstractmethod
    def listen(self):
        pass

    @abstractmethod
    def on_incoming_message(self, message):
        pass
