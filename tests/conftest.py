import os
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    env = os.environ.get("TREKSAFER_ENV")
    if env != "test":
        pytest.exit(
            f"Tests must be run with TREKSAFER_ENV=test (got: {env!r})",
            returncode=1
        )
