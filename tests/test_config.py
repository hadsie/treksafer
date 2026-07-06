import pytest
from pydantic import ValidationError

from app.config import Settings


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
