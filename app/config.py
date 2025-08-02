"""TrekSafer configuration management.

Order of precedence:
1. Environment variables
2. .env.<ENV> file
3. YAML file in config/<ENV>.yaml
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Literal, List, Union

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic.types import SecretStr
from pydantic_settings import BaseSettings


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


# ---- Core settings model ---- #

class DataFile(BaseModel):
    location: str
    filename: str
    mapping: Dict[str, Any]


class Settings(BaseSettings):
    """App-wide settings loaded from YAML + env vars."""
    env: str = Field("dev", description="Environment ID: test, dev, or prod")
    fire_radius: int = 100
    download_timeout: int = 600
    request_cache_timeout: int = 14400  # 4 hours.

    shapefiles: str = "shapefiles"
    data: List[DataFile] = []

    transports: List[TransportConfig] = []

    log_level: int = logging.INFO
    log_file: str | None = None

    model_config = {"env_prefix": "TREKSAFER_"}

    @model_validator(mode="after")
    def set_default_log_file(self):
        if self.log_file is None:
            self.log_file = f"logs/{self.env}.log"
        return self

# ---- Loader helpers ---- #

def _load_yaml_defaults(env: str) -> dict[str, Any]:
    """Load settings from a YAML file matching the given environment."""
    file_path = Path("config") / f"{env}.yaml"
    try:
        with open(file_path, "r") as fh:
            return yaml.safe_load(fh) or {}

    except FileNotFoundError as err:
        raise FileNotFoundError(
            f"YAML config '{file_path}' not found."
        ) from err


def _load_dotenv(env: str) -> None:
    """Populate os.environ from `.env.<env>` if it exists."""
    dotenv_path = Path.cwd() / f".env.{env}"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)


def get_config() -> Settings:
    """Returns the settings object that will be used by the rest of the app."""
    env = os.getenv("TREKSAFER_ENV", "dev")
    _load_dotenv(env)
    yaml_defaults = _load_yaml_defaults(env)
    return Settings(**yaml_defaults)


# Instantiate once so callers can use from app.config import settings.
settings = get_config()
