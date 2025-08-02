from abc import ABC, abstractmethod

class BaseTransport(ABC):
    @abstractmethod
    def send(self, recipient, content):
        pass

    @abstractmethod
    async def listen(self) -> None:
        """Run the listener. Should not return until asked to stop."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Shut down resources opened in listen()."""
        pass
