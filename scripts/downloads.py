#!venv/bin/python
"""Refresh the fire database from every configured source.

Fetches each source's complete fire set from its realtime layers (the same
source and merge the request path uses) and records it, so the database is
a warm recovery mode when an API is unavailable at request time.
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from app.config import get_config
from app.fires import db as firedb
from app.fires import normalize_for_db
from app.fires.sources import fetch_all_fires

# The shared public quotas on the upstream layers say "retry after 60 sec"
# when exceeded; retry rounds after this delay ride out a throttle.
RETRY_DELAY_S = 75
MAX_RETRIES = 5


def refresh_source(conn, data_file) -> int:
    """Fetch, normalize, and record one source. Returns snapshots written."""
    fires = fetch_all_fires(data_file.realtime)
    normalized = normalize_for_db(fires, data_file.location, data_file.realtime)
    return firedb.record_fires(conn, data_file.location, normalized,
                               datetime.now(timezone.utc))


def _refresh(conn, data_files) -> list:
    """Refresh each source, returning the ones that failed."""
    failures = []
    for data_file in data_files:
        try:
            written = refresh_source(conn, data_file)
            print(f"{data_file.location}: {written} snapshot(s) written.")
        except (requests.RequestException, ValueError, KeyError) as e:
            failures.append(data_file)
            print(f"{data_file.location} failed: {e}")
    return failures


def main():
    settings = get_config()
    conn = firedb.connect(settings.database)
    try:
        failures = _refresh(conn, [d for d in settings.data if d.realtime])
        for attempt in range(MAX_RETRIES):
            if not failures:
                break
            print(f"\nRetrying {len(failures)} failed source(s) in {RETRY_DELAY_S}s "
                  f"(attempt {attempt + 1} of {MAX_RETRIES})...")
            time.sleep(RETRY_DELAY_S)
            failures = _refresh(conn, failures)
    finally:
        conn.close()

    if failures:
        print(f"\n{len(failures)} source(s) failed: "
              f"{', '.join(d.location for d in failures)}")
        return 1
    print("\nAll sources refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
