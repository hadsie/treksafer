#!venv/bin/python
"""Refresh the fire database from every configured source.

Fetches each source's complete fire set from its realtime layers (the same
source and merge the request path uses) and records it, so the database is
a warm recovery mode when an API is unavailable at request time.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from app.config import get_config
from app.fires import db as firedb
from app.fires import normalize_for_db
from app.fires.sources import fetch_all_fires


def refresh_source(conn, data_file) -> int:
    """Fetch, normalize, and record one source. Returns snapshots written."""
    fires = fetch_all_fires(data_file.realtime)
    normalized = normalize_for_db(fires, data_file.location, data_file.realtime)
    return firedb.record_fires(conn, data_file.location, normalized,
                               datetime.now(timezone.utc))


def main():
    settings = get_config()
    conn = firedb.connect(settings.database)
    failures = []
    try:
        for data_file in settings.data:
            if not data_file.realtime:
                continue
            try:
                written = refresh_source(conn, data_file)
                print(f"{data_file.location}: {written} snapshot(s) written.")
            except (requests.RequestException, ValueError, KeyError) as e:
                failures.append(data_file.location)
                print(f"{data_file.location} failed: {e}")
    finally:
        conn.close()

    if failures:
        print(f"\n{len(failures)} source(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll sources refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
