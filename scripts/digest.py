#!venv/bin/python
"""Email the operator a summary of requests with unusable coordinates.

Run from cron once a day. Scans new sms.log entries since the last run,
collects the requests whose reply was the "no valid GPS coordinates"
error, and emails them (sender, time, raw message) so parse failures
surface instead of sitting in the log: a spike usually means a device
format we don't handle yet, not user error.

Sends nothing when there were no failures. When the email fails, the
log position is not advanced, so the same entries are retried on the
next run.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_config
from app.messages import Messages
from app.notify import notify_email
from scripts.monitor import load_state, save_state

MAX_ENTRIES = 50

# "2026-07-11 14:02:11 sms INFO From: +16045551234, Body: Fires ..."
# Lines that don't match are continuations of a multi-line reply.
_LINE = re.compile(
    r'^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \S+ \w+ '
    r'(?P<kind>From|Reply): (?P<rest>.*)$')


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
    """Pair each inbound message with the reply that followed it."""
    requests_, pending = [], None
    for line in lines:
        match = _LINE.match(line)
        if not match:
            continue
        if match["kind"] == "From":
            sender, _, body = match["rest"].partition(", Body: ")
            pending = {"time": match["time"], "sender": sender, "body": body}
        elif pending:
            pending["reply"] = match["rest"]
            requests_.append(pending)
            pending = None
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


def run(settings) -> int:
    monitoring = settings.monitoring
    state = load_state(monitoring.digest_state_file)
    offset_before = state.get("log_offset", 0)

    requests_ = parse_requests(read_new_lines(monitoring.sms_log_file, state))
    no_gps = Messages().no_gps()
    failures = [r for r in requests_ if r["reply"] == no_gps]

    delivered = True
    if failures:
        subject = f"TrekSafer digest: {len(failures)} request(s) with no usable coordinates"
        delivered = notify_email(subject, format_digest(failures, len(requests_)))
        if not delivered:
            # Keep the log position so the next run retries these entries.
            state["log_offset"] = offset_before
            print("Digest email failed; will retry next run.")
    save_state(monitoring.digest_state_file, state)

    print(f"{len(requests_)} request(s), {len(failures)} parse failure(s).")
    return 0 if delivered else 1


def main():
    return run(get_config())


if __name__ == "__main__":
    sys.exit(main())
