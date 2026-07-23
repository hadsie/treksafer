#!venv/bin/python
"""Email the operator a daily digest of request problems and volume.

Run from cron once a day. Two sources feed it: new sms.log entries since
the last run (requests whose reply was the "no valid GPS coordinates"
error -- a spike usually means a device format we don't handle yet), and
the request log database (likely wrong-coordinate re-requests, plus
volume and outcome counts for the period).

Sends nothing when there were no failures and no re-request pairs. When
the email fails, neither the log position nor the request-log watermark
advances, so the same entries are retried on the next run.
"""

import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import request_log
from app.config import get_config
from app.messages import Messages
from app.notify import notify_email
from scripts.monitor import load_state, save_state

MAX_ENTRIES = 50

# Two location requests from one sender this close together usually mean
# the first reply resolved the wrong coordinates.
REREQUEST_WINDOW = timedelta(minutes=3)

# "2026-07-11 14:02:11 sms INFO From: +16045551234" starts a record; the
# message content follows, one "> "-prefixed line each, so content can
# never read as a log record no matter what a sender texts.
_HEADER = re.compile(
    r'^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \S+ \w+ '
    r'(?P<kind>From|Reply):\s*(?P<rest>.*)$')
_CONTENT = re.compile(r'^> (?P<text>.*)$')
# Marker line the SignalWire transport writes above each reply message,
# e.g. "----- SMS 1/2 (148/160 GSM-7) -----": log metadata, not content.
_MARKER = re.compile(r'^----- SMS \d+/\d+ \([^)]*\) -----$')


def read_new_lines(log_path: str, state: dict) -> list[str]:
    """Lines added since the last run, tracked by a byte offset in state.
    A shrunken file (rotation) rescans from the start."""
    path = Path(log_path)
    if not path.exists():
        return []
    offset = state.get("log_offset", 0)
    if path.stat().st_size < offset:
        offset = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        lines = f.readlines()
        state["log_offset"] = f.tell()
    return lines


def parse_requests(lines: list[str]) -> list[dict]:
    """Pair each inbound message with the reply that followed it.

    A Reply header's own text is a metadata note (e.g. a suppressed
    send); actual message content arrives on the quoted lines below it.
    """
    requests_, record, field = [], None, None
    for raw in lines:
        line = raw.rstrip("\n")
        header = _HEADER.match(line)
        if header:
            if header["kind"] == "From":
                record = {"time": header["time"],
                          "sender": header["rest"].strip(), "body": ""}
                field = "body"
            elif record is not None and "reply" not in record:
                record["reply"] = header["rest"].strip()
                requests_.append(record)
                field = "reply"
            else:
                field = None
            continue
        content = _CONTENT.match(line)
        if content and record is not None and field:
            # Markers are stripped from replies only; a body line that
            # mimics one is sender content and stays.
            if field == "reply" and _MARKER.match(content["text"]):
                continue
            record[field] += ("\n" if record[field] else "") + content["text"]
    return requests_


def format_digest(failures: list[dict], total: int) -> str:
    lines = [f"{len(failures)} of {total} request(s) had no usable coordinates:", ""]
    for entry in failures[:MAX_ENTRIES]:
        lines.append(f"{entry['time']}  {entry['sender']}")
        lines.append(f"  {entry['body']}")
        lines.append("")
    if len(failures) > MAX_ENTRIES:
        lines.append(f"... and {len(failures) - MAX_ENTRIES} more")
    return "\n".join(lines)


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
    offset_before = state.get("log_offset", 0)

    requests_ = parse_requests(read_new_lines(monitoring.sms_log_file, state))
    no_gps = Messages().no_gps()
    failures = [r for r in requests_ if r["reply"] == no_gps]

    now = datetime.now(timezone.utc)
    since = (datetime.fromisoformat(state["requests_since"])
             if state.get("requests_since") else now - timedelta(days=1))
    rows = request_log.requests_since(settings.request_database, since)
    pairs = re_request_pairs(rows)
    state["requests_since"] = now.isoformat()

    delivered = True
    if failures or pairs:
        problems = []
        sections = []
        if failures:
            problems.append(f"{len(failures)} request(s) with no usable coordinates")
            sections.append(format_digest(failures, len(requests_)))
        if pairs:
            problems.append(f"{len(pairs)} possible wrong-coordinate re-request(s)")
            sections.append(format_pairs(pairs))
        sections.append(format_volume(rows, since))
        subject = "TrekSafer digest: " + "; ".join(problems)
        delivered = notify_email(subject, "\n\n".join(sections))
        if not delivered:
            # Keep both positions so the next run retries these entries.
            state["log_offset"] = offset_before
            state["requests_since"] = since.isoformat()
            print("Digest email failed; will retry next run.")
    save_state(monitoring.digest_state_file, state)

    print(f"{len(requests_)} request(s), {len(failures)} parse failure(s), "
          f"{len(pairs)} re-request pair(s).")
    return 0 if delivered else 1


def main():
    return run(get_config())


if __name__ == "__main__":
    sys.exit(main())
