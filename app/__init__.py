import logging
import requests_cache

from .config import get_config
from .transport import get_transports

def main():
    settings = get_config()

    formatter = logging.Formatter(
        '%(asctime)s %(name)s %(levelname)s : %(message)s',
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.FileHandler(f"logs/{settings.env}.log")
    file_handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.setLevel(settings.log_level)
    logger.addHandler(file_handler)

    requests_cache.install_cache("bc_fire_api_cache", expire_after=settings.request_cache_timeout)

    for transport_instance in get_transports():
        transport_instance.listen()
