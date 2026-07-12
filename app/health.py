"""Health report. Verify that the database can be opened and fresh data is available."""

import sqlite3

from .config import get_config
from .fires import db as firedb


def health_report() -> dict:
    """App alive and per-source data freshness.

    The sources field checks ever configured source and returns the timestamp
    it was last successfully fetched.
    """
    settings = get_config()
    try:
        conn = firedb.connect(settings.database)
        try:
            fetches = firedb.latest_fetches(conn)
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as e:
        return {'status': 'error', 'error': str(e)}
    return {'status': 'ok',
            'sources': {df.location: {'latest_fetch': fetches.get(df.location)}
                        for df in settings.data}}
