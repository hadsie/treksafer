"""TrekSafer configuration management.

Order of precedence:
1. Environment variables
2. .env.<ENV> file
3. config.yaml
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Literal, List, Union, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.types import SecretStr
from pydantic_settings import BaseSettings


CONFIG_YAML = Path.cwd() / "config.yaml"

class SignalWireConfig(BaseModel):
    type: Literal["signalwire"]
    project_id: SecretStr | None = None
    api_token: SecretStr | None = None
    phone_number: str | None = None
    enabled: bool = False

    # require secrets only if enabled
    @model_validator(mode="after")
    def check_required_when_enabled(self):
        if self.enabled:
            missing = [
                name for name in ("project_id", "api_token", "phone_number")
                if getattr(self, name) in (None, "", "REPLACE WITH...")
            ]
            if missing:
                raise ValueError(
                    f"SignalWire transport enabled but missing: {', '.join(missing)}"
                )
        return self

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


#TransportConfig = Union[SignalWireConfig, CLIConfig, EmailConfig]
TransportConfig = Union[SignalWireConfig, CLIConfig]


# ---- Avalanche configuration models ---- #

class AvalancheProviderConfig(BaseModel):
    """Configuration for a single avalanche forecast provider."""
    model_config = ConfigDict(populate_by_name=True)

    class_name: str = Field(alias='class')
    api_url: str
    cache_timeout: int = 3600
    forecast_cutoff_hour: int = 16
    language: str = 'en'


class AvalancheConfig(BaseModel):
    """Avalanche forecast configuration."""
    providers: Dict[str, AvalancheProviderConfig]


# ---- Core settings model ---- #

class DataFile(BaseModel):
    location: str
    filename: str
    mapping: Dict[str, Any]
    status_map: Dict[str, List[str]]


class Settings(BaseSettings):
    model_config = {"env_prefix": "TREKSAFER_"}

    """App-wide settings loaded from YAML + env vars."""
    env: str = os.getenv("TREKSAFER_ENV", "dev")
    fire_radius: int = 100
    max_radius: int = 150
    fire_status: str = "controlled"
    fire_size: int = 1
    download_timeout: int = 600
    include_aqi: bool = True

    request_cache_timeout: int = 14400  # 4 hours.

    # Avalanche forecast configuration
    avalanche: Optional[AvalancheConfig] = None
    avalanche_distance_buffer: int = 20  # Distance buffer for avalanche provider selection (km)

    shapefiles: str = "shapefiles"
    data: List[DataFile] = []

    transports: List[TransportConfig] = []

    log_file: str | None = None
    log_level: int = logging.INFO

    def model_post_init(self, __context):
        if self.log_file is None:
            self.log_file = f"logs/{self.env}.log"


# ---- Loader helpers ---- #
_PLACEHOLDER_RE = re.compile(r"\${([A-Z0-9_]+)(?::-(.*?))?}")

def _expand_placeholders(text: str) -> str:
    """
    Replace ${VAR}            → value from env  (or '')
            ${VAR:-default}   → value or fallback
    """
    def repl(match: re.Match):
        var, default = match.group(1), match.group(2)
        return os.getenv(var, default or "")

    return _PLACEHOLDER_RE.sub(repl, text)

def _yaml_defaults() -> dict[str, Any]:
    """Return dict from config.yaml with ${VAR} placeholders expanded."""
    raw = CONFIG_YAML.read_text()
    raw = _expand_placeholders(raw)
    return yaml.safe_load(raw) or {}

def _load_dotenv() -> None:
    """Populate os.environ from .env.<env> if it exists."""
    env = os.getenv("TREKSAFER_ENV", "dev")
    load_dotenv(f".env.{env}", override=False)
    load_dotenv(".env", override=False)

def get_config() -> Settings:
    """Returns the settings object that will be used by the rest of the app."""
    _load_dotenv()
    return Settings(**_yaml_defaults())


# Instantiate once so callers can use from app.config import settings.
settings = get_config()
