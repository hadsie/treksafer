import pytest
from pydantic import ValidationError

from app.config import RealtimeFireConfig, Settings


class TestFireSeasonValidation:
    """Settings validates fire season dates as MM-DD strings."""

    def test_valid_dates_accepted(self):
        settings = Settings(fire_season_start="05-15", fire_season_end="08-15")
        assert settings.fire_season_start == "05-15"
        assert settings.fire_season_end == "08-15"

    @pytest.mark.parametrize("value", ["15-05", "2026-05-15", "May 15", "", "13-01", "05-32"])
    def test_malformed_dates_rejected(self, value):
        with pytest.raises(ValidationError):
            Settings(fire_season_start=value)


class TestRealtimeFireConfig:
    """RealtimeFireConfig validates the realtime source block."""

    def test_defaults(self):
        config = RealtimeFireConfig(
            points_url="https://example.test/points/query",
            perimeters_url="https://example.test/perims/query",
            perimeter_fire_field="FIRE_NUMBER",
            mapping={"Fire": "FIRE_NUMBER"},
            status_map={"active": ["Out of Control"]},
        )
        assert config.enabled is True
        assert config.cache_timeout == 900

    def test_mapping_without_fire_key_rejected(self):
        with pytest.raises(ValidationError, match="Fire"):
            RealtimeFireConfig(
                points_url="https://example.test/points/query",
                perimeters_url="https://example.test/perims/query",
                perimeter_fire_field="FIRE_NUMBER",
                mapping={"Name": "INCIDENT_NAME"},
                status_map={"active": ["Out of Control"]},
            )

    def test_missing_perimeter_fire_field_rejected(self):
        with pytest.raises(ValidationError, match="perimeter_fire_field"):
            RealtimeFireConfig(
                points_url="https://example.test/points/query",
                perimeters_url="https://example.test/perims/query",
                mapping={"Fire": "FIRE_NUMBER"},
                status_map={"active": ["Out of Control"]},
            )
