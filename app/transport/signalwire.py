import logging

from signalwire.relay.consumer import Consumer
from signalwire.relay.client import Client

from app.messages import handle_message
from .base import Transport

class CustomConsumer(Consumer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = None
        self.responses = None
        self.sms_log = None
        self.number = None

    def setup(self):
        self.contexts = ['treksafer']
        #self.client = Client(project=self.project, token=self.token)
        self.responses = Messages()

    def setupLogging(self):
        self.sms_log = logging.getLogger('sms')
        formatter = logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
        log_handler = logging.FileHandler('logs/sms.log')
        log_handler.setFormatter(formatter)
        self.sms_log.setLevel(logging.DEBUG)
        self.sms_log.addHandler(log_handler)

    async def ready(self):
        print('SignalWire consumer is ready...')
        self.logger.info('SignalWire Consumer is listening for messages.')

    async def on_incoming_message(self, message):
        self.logger.info(f'SignalWire Consumer received incoming message from {message.from_number}.')
        outgoing_message = handle_message(message.body)
        return self.send_message(message.from_number, outgoing_message)

    async def send_message(self, number, message):
        result = await self.client.messaging.send(context='treksafer', from_number=self.number, to_number=number, body=message)
        if result.successful:
            self.logger.info(f'Sent SMS to {number} with message ID {result.message_id}.')
        else:
            self.logger.warning(f'Failed to send SMS response to {number} with message ID {result.message_id}.')
        self.sms_log.info(message)

        return result.successful


class SignalWireTransport(Transport):
    def __init__(self, settings: dict):
        self.settings = settings
        self.server_socket = None

    def send(self, recipient, content):
        raise NotImplementedError("CLITransport.send_message is not used.")

    def listen(self):
        consumer = CustomConsumer()
        consumer.run()

        package_name = __name__.split('.', 1)[0]
        logger = logging.getLogger(package_name)

        try:
            consumer = CustomConsumer(
                project=self.settings.project_id,
                token=self.settings.api_token
            )
            consumer.number = self.settings.phone_number
            consumer.logger = logger
            consumer.run()
        except Exception as err:
            logger.error('Exception caught in signalwire consumer: %s' % str(err))
            print(f"Unexpected {err=}, {type(err)=}")
            raise

    def on_incoming_message(self, message):
        response = handle_message(message)
        return response
