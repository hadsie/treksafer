import os
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Default to 'test' if not already set
    os.environ.setdefault("TREKSAFER_ENV", "test")
