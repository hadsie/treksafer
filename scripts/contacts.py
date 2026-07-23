#!venv/bin/python
"""Manage the SMS compliance lists (first-contact records and opt-outs).

The contacts table decides who receives the one-time opt-in notice: a
number's first message triggers it. `import-logs` backfills the table
from sms.log so senders who used the service before the notice existed
are not welcomed as new; `add`/`remove` put a test number through the
first-contact flow on demand. Opt-outs are listed for reference but only
managed by STOP/START messages, so the list always reflects real
carrier-visible consent.

Usage:
    python scripts/contacts.py list
    python scripts/contacts.py add +15551234567
    python scripts/contacts.py remove +15551234567
    python scripts/contacts.py import-logs [logfile ...]

import-logs defaults to the configured sms.log; pass rotated files as
extra arguments to scan them too.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import optout
from app.config import get_config

_NUMBER = re.compile(r'\+\d{7,15}')

# "2026-07-11 14:02:11 sms INFO From: +16045551234" opens a record;
# message content below it is '> '-quoted line by line, so a crafted
# message can never counterfeit a From record.
_FROM = re.compile(
    r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+ \w+ From:\s*(?P<sender>.*)$')


def import_logs(db: str, paths: list[str]) -> int:
    """Record every sender found in the logs; returns how many were new."""
    added = 0
    for path in paths:
        if not Path(path).exists():
            print(f"Skipping {path}: no such file.", file=sys.stderr)
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            senders = {match['sender'].strip()
                       for line in f if (match := _FROM.match(line))}
        valid = {s for s in senders if _NUMBER.fullmatch(s)}
        for skipped in sorted(senders - valid):
            print(f"Skipping unparseable sender {skipped!r} in {path}.",
                  file=sys.stderr)
        added += sum(optout.first_contact(db, number) for number in sorted(valid))
    return added


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage the SMS compliance lists.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show known contacts and opt-outs")
    for command in ("add", "remove"):
        sub.add_parser(command, help=f"{command} a first-contact record") \
           .add_argument("number", help="phone number, e.g. +15551234567")
    sub.add_parser("import-logs", help="record every sender in the logs") \
       .add_argument("paths", nargs="*", help="log files (default: sms.log)")
    args = parser.parse_args(argv)

    settings = get_config()
    db = settings.optout_database

    if args.command == "list":
        opted_out = {number for number, _ in optout.optouts(db)}
        known = optout.contacts(db)
        print(f"{len(known)} contact(s), {len(opted_out)} opted out:")
        for number, first_seen in known:
            marker = "  [opted out]" if number in opted_out else ""
            print(f"  {number}  first seen {first_seen}{marker}")
        for number in sorted(opted_out - {n for n, _ in known}):
            print(f"  {number}  [opted out, no contact record]")
        return 0

    if args.command in ("add", "remove"):
        if not _NUMBER.fullmatch(args.number):
            print(f"Not a valid number: {args.number!r} "
                  "(expected E.164, e.g. +15551234567).", file=sys.stderr)
            return 2
        if args.command == "add":
            new = optout.first_contact(db, args.number)
            print(f"{args.number} {'added' if new else 'was already known'}.")
        else:
            removed = optout.forget_contact(db, args.number)
            print(f"{args.number} {'removed: its next message gets the opt-in notice' if removed else 'was not on the contact list'}.")
        return 0

    paths = args.paths or [settings.monitoring.sms_log_file]
    added = import_logs(db, paths)
    print(f"{added} new contact(s) recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
