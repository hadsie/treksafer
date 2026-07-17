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
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Literal, List, Union, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.types import SecretStr
from pydantic_settings import BaseSettings


CONFIG_YAML = Path.cwd() / "config.yaml"

class SignalWireConfig(BaseModel):
    type: Literal["signalwire"]
    project_id: SecretStr | None = None
    api_token: SecretStr | None = None
    phone_number: str | None = None
    context: str = "treksafer"
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
    language: str = 'en'
    out_of_season: List[str] = []


class AvalancheConfig(BaseModel):
    """Avalanche forecast configuration."""
    providers: Dict[str, AvalancheProviderConfig]


# ---- Core settings model ---- #

class EnrichmentConfig(BaseModel):
    """Per-fire enrichment API for data the source's layers lack.

    Used only by single-fire lookups (one call per looked-up fire, cached).
    The url is a template whose placeholders name the source's key_fields
    (e.g. {FIRE_YEAR}, {FIRE_NUMBER}); updated_field is the JSON field
    carrying the fire's last-update time (epoch milliseconds).
    """
    url: str
    updated_field: str


class RealtimeFireConfig(BaseModel):
    """Realtime ArcGIS FeatureServer source for a fire data location."""
    enabled: bool = True
    points_url: List[str]
    perimeters_url: str
    cache_timeout: int = 900
    mapping: Dict[str, str]
    transforms: Dict[str, str] = {}
    status_map: Dict[str, List[str]]
    # How points and perimeters relate: 'field' joins on a shared fire-number
    # field; 'spatial' assigns each point the polygon it falls in (for
    # sources whose perimeters carry no fire ID).
    join: Literal["field", "spatial"] = "field"
    # Fire-number field on the perimeters layer; may differ from the points
    # layer's (e.g. Alberta uses FireNumber vs the points layer's LABEL).
    # Required for (and only used by) the 'field' join.
    perimeter_fire_field: Optional[str] = None
    # Points-layer field to join on; may differ from the displayed Fire
    # identifier (e.g. WFIGS joins on the IrwinID GUID). Required for (and
    # only used by) the 'field' join.
    join_field: Optional[str] = None
    # Points-layer fields whose combined values identify a fire across
    # seasons in the database (BC fire numbers recycle annually, so BC uses
    # [FIRE_YEAR, FIRE_NUMBER]).
    key_fields: List[str]
    # Name of a synthesized points column holding the fetch's UTC year,
    # for sources whose fire numbers recycle annually but whose layer has
    # no year field. Usable in key_fields; never requested from the layer.
    year_field: Optional[str] = None
    # Points-layer field holding the source's per-fire update timestamp,
    # where one exists; it gates snapshot writes. Sources without one gate
    # on field comparison instead.
    updated_field: Optional[str] = None
    # IANA zone for parsing zoneless local timestamp strings (e.g.
    # America/Edmonton for AB's FIRE_STATUS_DATE).
    timezone: Optional[str] = None
    # Attribute filter applied to points-layer queries, e.g. to exclude
    # agencies covered by a dedicated source.
    points_where: str = "1=1"
    # Attribute filter applied to perimeters-layer queries, e.g. to skip
    # fetching polygons whose fires points_where excludes.
    perimeters_where: str = "1=1"
    # The monitor alerts when the source's ArcGIS layers haven't been
    # republished (layer metadata lastEditDate) within this many hours.
    layer_stale_hours: int = 24
    # Per-fire enrichment API for data the layers lack (e.g. BC's last-update
    # time, which only its incident system publishes).
    enrichment: Optional[EnrichmentConfig] = None

    @field_validator("points_url", mode="before")
    @classmethod
    def single_url_is_a_list_of_one(cls, value):
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_join_key(self):
        if "Fire" not in self.mapping:
            raise ValueError(
                "realtime mapping must include 'Fire'; it identifies each "
                "fire from the points layer"
            )
        if self.join == "field":
            missing = [name for name in ("perimeter_fire_field", "join_field")
                       if not getattr(self, name)]
            if missing:
                raise ValueError(f"the 'field' join requires {' and '.join(missing)}")
        return self


class DataFile(BaseModel):
    location: str
    mapping: Dict[str, Any] = {}
    status_map: Dict[str, List[str]] = {}
    realtime: Optional[RealtimeFireConfig] = None


class MonitoringConfig(BaseModel):
    """Operator alerting used by scripts/monitor.py and app/notify.py.

    Each delivery channel is enabled by configuring it: ntfy by a topic,
    email by an SMTP host + alert address. Unconfigured channels are
    skipped with a log line.
    """
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    # From address; must be a sender the SMTP service accepts (e.g. an
    # SES-verified address). Defaults to smtp_user, then alert_email.
    smtp_from: str = ""
    alert_email: str = ""
    # Dead-man's switch pings (e.g. healthchecks.io check URLs): one for
    # the monitor, one for the daily database refresh.
    healthcheck_url: str = ""
    refresh_healthcheck_url: str = ""
    # Alert when a source's newest fetch is older than this.
    fetch_stale_hours: int = 12
    # Last-known condition state, for alerting on changes only.
    state_file: str = "data/monitor_state.json"
    # Daily digest of requests with unusable coordinates (scripts/digest.py).
    # Defaults to sms.log in the app's log_dir.
    sms_log_file: str = ""
    digest_state_file: str = "data/digest_state.json"

    @model_validator(mode="after")
    def check_email_pair(self):
        if bool(self.smtp_host) != bool(self.alert_email):
            raise ValueError(
                "email alerts need both smtp_host and alert_email; only one is set")
        return self


class Settings(BaseSettings):
    model_config = {"env_prefix": "TREKSAFER_"}

    """App-wide settings loaded from YAML + env vars."""
    env: str = os.getenv("TREKSAFER_ENV", "dev")
    fire_radius: int = 100
    max_radius: int = 150
    fire_status: str = "controlled"
    fire_size: int = 1
    # Fire database: snapshot history and the fallback for API outages.
    database: str = "data/fires.db"
    # Numbers that opted out of SMS replies (STOP).
    optout_database: str
    # Stored fallback data older than this (hours) carries a freshness marker.
    stale_data_hours: int
    # Fires discovered within this many days bypass the minimum size filter.
    new_fire_age_days: int = 7
    # Auto-detected requests default to fire data within this window (MM-DD, inclusive).
    fire_season_start: str = "05-15"
    fire_season_end: str = "08-15"
    include_aqi: bool = True

    # Avalanche forecast configuration
    avalanche: Optional[AvalancheConfig] = None
    avalanche_distance_buffer: int = 5  # Distance buffer for avalanche provider selection (km)

    data: List[DataFile] = []

    transports: List[TransportConfig] = []

    monitoring: MonitoringConfig = MonitoringConfig()

    # Directory for runtime logs; log_file and monitoring.sms_log_file
    # derive from it unless set explicitly.
    log_dir: str = "logs"
    log_file: str | None = None
    log_level: int = logging.INFO

    @field_validator("fire_season_start", "fire_season_end")
    @classmethod
    def validate_month_day(cls, value: str) -> str:
        # Parsed against a leap year so "02-29" is accepted.
        datetime.strptime(f"2000-{value}", "%Y-%m-%d")
        return value

    def model_post_init(self, __context):
        if self.log_file is None:
            self.log_file = f"{self.log_dir}/{self.env}.log"
        if not self.monitoring.sms_log_file:
            self.monitoring.sms_log_file = f"{self.log_dir}/sms.log"


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

@lru_cache(maxsize=1)
def get_config() -> Settings:
    """Returns the settings object (cached after first call)."""
    _load_dotenv()
    return Settings(**_yaml_defaults())
