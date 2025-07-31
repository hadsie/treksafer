import logging
import os
import yaml

from pydantic import BaseModel, model_validator
from pydantic.types import SecretStr
from typing import Any, Dict, Literal, List, Union


class SignalWireConfig(BaseModel):
    type: Literal["signalwire"]
    project_id: SecretStr
    api_token: SecretStr
    phone_number: str
    enabled: bool

class CLIConfig(BaseModel):
    type: Literal["cli"]
    host: str = "localhost"
    port: int = 8888
    enabled: bool

class EmailConfig(BaseModel):
    type: Literal["email"]
    smtp_server: str
    smtp_user: str
    smtp_password: str
    enabled: bool


TransportConfig = Union[SignalWireConfig, CLIConfig, EmailConfig]


class DataFile(BaseModel):
    location: str
    filename: str
    mapping: Dict[str, Any]


class Settings(BaseModel):
    env: str = "prod"
    fire_radius: int = 100
    download_timeout: int = 600
    transports: List[TransportConfig] = []
    shapefiles: str = "shapefiles"
    data: List[DataFile]
    log_file: str | None = None
    log_level: int = logging.DEBUG
    request_cache_timeout: int = 14400

    @model_validator(mode="after")
    def set_default_log_file(self):
        if self.log_file is None:
            self.log_file = f"logs/{self.env}.log"
        return self


def _load_yml_config(env: str = "prod"):
    """Load settings from a YAML file matching the given environment."""
    try:
        file_path = f"config/{env}.yaml"
        with open(file_path, "r") as f:
            return yaml.safe_load(f)

    except FileNotFoundError as error:
        message = "Error: yml config file not found."
        logging.exception(message)
        raise FileNotFoundError(error, message) from error


environment = os.environ.get("TREKSAFER_ENV", "prod")
settings = Settings(**_load_yml_config(environment))


def get_config():
    """Return settings object.

    :return: Settings object.
    """
    return settings
