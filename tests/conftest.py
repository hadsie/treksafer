import os
import json
import pytest
import responses
from pathlib import Path
from app.config import get_config

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Default to 'test' if not already set
    os.environ.setdefault("TREKSAFER_ENV", "test")

@pytest.fixture
def mock_bc_fire_api():
    """Mock BC Wildfire API using test data from BC_perimeters.geojson"""

    # Get BC API config
    config = get_config()
    bc_config = next((d for d in config.data if d.location == "BC"), None)
    if not bc_config or not bc_config.mapping.get("api"):
        yield
        return

    api_config = bc_config.mapping["api"]
    url_template = api_config["url"]
    api_fields = api_config["fields"]

    # Load BC test data
    test_data_path = Path(__file__).parent / "data" / "BC_perimeters.geojson"
    with open(test_data_path) as f:
        geojson = json.load(f)

    # Start mocking
    responses.start()

    # Register mock for each fire in test data
    for feature in geojson["features"]:
        props = feature["properties"]

        # Build API URL from template
        url = url_template.format(
            FIRE_NUM=props["FIRE_NUM"],
            FIRE_YEAR=props["FIRE_YEAR"]
        )

        # Build API response using field mapping
        mock_response = {}
        for internal_name, api_field_name in api_fields.items():
            # Get value from GeoJSON properties
            mock_response[api_field_name] = props.get(api_field_name)

        # Register mock
        responses.add(
            responses.GET,
            url,
            json=mock_response,
            status=200
        )

    yield

    responses.stop()
    responses.reset()
