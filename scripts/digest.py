#!venv/bin/python
"""Email the operator a daily digest of request problems and volume.

Run from cron once a day, entirely from the request log database: parse
failures (a spike usually means a device format we don't handle yet),
likely wrong-coordinate re-requests, and volume and outcome counts for
the period.

Sends nothing when there were no failures and no re-request pairs. When
the email fails, the request-log watermark does not advance, so the same
entries are retried on the next run.
"""

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import request_log
from app.config import get_config
from app.notify import notify_email
from scripts.monitor import load_state, save_state

MAX_ENTRIES = 50

# Two location requests from one sender this close together usually mean
# the first reply resolved the wrong coordinates.
REREQUEST_WINDOW = timedelta(minutes=3)

def re_request_pairs(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Consecutive requests from one sender within the re-request window.

    Service keyword requests (help/usage/health) carry no location and
    never pair.
    """
    by_sender = {}
    for row in rows:
        if row['response_type'] in ('help', 'usage', 'health'):
            continue
        by_sender.setdefault(row['sender'], []).append(row)
    pairs = []
    for sender_rows in by_sender.values():
        for first, second in zip(sender_rows, sender_rows[1:]):
            delta = (datetime.fromisoformat(second['received_at'])
                     - datetime.fromisoformat(first['received_at']))
            if delta <= REREQUEST_WINDOW:
                pairs.append((first, second))
    return pairs


def _coords(row: dict) -> str:
    if row['lat'] is None:
        return 'no coordinates'
    return f"({row['lat']:.5f}, {row['lon']:.5f})"


def format_failures(failures: list[dict], total: int) -> str:
    lines = [f"{len(failures)} of {total} request(s) had no usable coordinates:", ""]
    for row in failures[:MAX_ENTRIES]:
        lines.append(f"{row['received_at']}  {row['sender']}")
        lines.append(f"  {row['message']}")
        lines.append("")
    if len(failures) > MAX_ENTRIES:
        lines.append(f"... and {len(failures) - MAX_ENTRIES} more")
    return "\n".join(lines)


def format_pairs(pairs: list[tuple[dict, dict]]) -> str:
    minutes = int(REREQUEST_WINDOW.total_seconds() // 60)
    lines = [f"{len(pairs)} re-request pair(s) within {minutes} min "
             "(likely wrong coordinates):", ""]
    for first, second in pairs[:MAX_ENTRIES]:
        lines.append(first['sender'])
        for row in (first, second):
            lines.append(f"  {row['received_at']}  {_coords(row)}")
            lines.append(f"    {row['message']}")
        lines.append("")
    if len(pairs) > MAX_ENTRIES:
        lines.append(f"... and {len(pairs) - MAX_ENTRIES} more")
    return "\n".join(lines)


def format_volume(rows: list[dict], since: datetime) -> str:
    counts = Counter(row['response_type'] for row in rows)
    breakdown = ", ".join(f"{kind} {n}" for kind, n in counts.most_common())
    return (f"{len(rows)} request(s) since {since:%b %d %H:%M} UTC"
            + (f": {breakdown}" if breakdown else ""))


def run(settings) -> int:
    monitoring = settings.monitoring
    state = load_state(monitoring.digest_state_file)

    now = datetime.now(timezone.utc)
    since = (datetime.fromisoformat(state["requests_since"])
             if state.get("requests_since") else now - timedelta(days=1))
    rows = request_log.requests_since(settings.request_database, since)
    failures = [r for r in rows if r['response_type'] == 'no_gps']
    pairs = re_request_pairs(rows)
    state["requests_since"] = now.isoformat()

    delivered = True
    if failures or pairs:
        problems = []
        sections = []
        if failures:
            problems.append(f"{len(failures)} request(s) with no usable coordinates")
            sections.append(format_failures(failures, len(rows)))
        if pairs:
            problems.append(f"{len(pairs)} possible wrong-coordinate re-request(s)")
            sections.append(format_pairs(pairs))
        sections.append(format_volume(rows, since))
        subject = "TrekSafer digest: " + "; ".join(problems)
        delivered = notify_email(subject, "\n\n".join(sections))
        if not delivered:
            # Keep the watermark so the next run retries these entries.
            state["requests_since"] = since.isoformat()
            print("Digest email failed; will retry next run.")
    save_state(monitoring.digest_state_file, state)

    print(f"{len(rows)} request(s), {len(failures)} parse failure(s), "
          f"{len(pairs)} re-request pair(s).")
    return 0 if delivered else 1


def main():
    return run(get_config())


if __name__ == "__main__":
    sys.exit(main())
