# TrekSafer test suite

This directory contains all unit and integration tests for the project. Tests are written using [`pytest`](https://docs.pytest.org/).

## Running the test suite

From the project root:

```bash
pytest
```

This will run all unit tests and skip the transport smoke tests.

To explicitly run smoke tests (e.g., end-to-end transport tests or integration with live services):

```bash
pytest -m smoke
```

Combine markers and test names as needed:

```bash
pytest -m "not smoke" -k test_format_distance
```

Run a single test file with verbose output:

```bash
pytest tests/test_coords.py -v
```
