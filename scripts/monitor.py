#!venv/bin/python
"""Check TrekSafer health and alert the operator on failures.

Run from cron every 15-30 minutes. Checks:

1. The app answers its health command (CLI transport, localhost).
2. Every source has a recent successful fetch.
3. Every source's upstream ArcGIS layers are still being republished
   (lastEditDate in the layer metadata; agencies republish continuously,
   so a stale layer means their pipeline is frozen).
4. New ERROR lines in the app log.

Alerts go to every configured channel (ntfy + email, see MonitoringConfig)
once when a condition trips and once when it recovers; state is kept in a
JSON file. When no channel delivers, state is not advanced, so the alert
is retried on the next run. Each successful run pings the healthchecks
URL so an external service notices if the monitor itself stops running.
"""

import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from app.config import get_config
from app.notify import notify

PROBE_TIMEOUT_S = 10
MAX_ERROR_LINES = 20


def probe_health(host: str, port: int) -> dict:
    """Send the health command to the app; a failed probe is an error report."""
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_S) as sock:
            sock.sendall(b"health\n")
            chunks = []
            while chunk := sock.recv(4096):
                chunks.append(chunk)
        return json.loads(b"".join(chunks))
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "error": f"health probe failed: {e}"}


def layer_conditions(data_files, now: datetime) -> dict:
    """Check each source's upstream layers via their metadata endpoints.

    Returns {condition name: problem or None}. An unreachable metadata
    endpoint is itself a problem: it means the upstream API is down.
    """
    conditions = {}
    for data_file in data_files:
        realtime = data_file.realtime
        if not (realtime and realtime.enabled):
            continue
        for kind in ("points", "perimeters"):
            name = f"layer:{data_file.location}:{kind}"
            url = getattr(realtime, f"{kind}_url").removesuffix("/query")
            conditions[name] = _check_layer(name, url, realtime.layer_stale_hours, now)
    return conditions


def _check_layer(name: str, url: str, stale_hours: int, now: datetime):
    try:
        resp = requests.get(url, params={"f": "json"}, timeout=30)
        resp.raise_for_status()
        edited_ms = resp.json().get("editingInfo", {}).get("lastEditDate")
    except (requests.RequestException, ValueError) as e:
        return f"{name}: metadata query failed: {e}"
    if edited_ms is None:
        return f"{name}: no lastEditDate in layer metadata"
    age_h = (now - datetime.fromtimestamp(edited_ms / 1000, timezone.utc)).total_seconds() / 3600
    if age_h > stale_hours:
        return f"{name}: not republished for {age_h:.1f}h (threshold {stale_hours}h)"
    return None


def fetch_conditions(report: dict, stale_hours: int, now: datetime) -> dict:
    """Evaluate app and per-source fetch freshness from a health report.

    A failed probe reports only the app condition; fetch conditions are
    left out entirely (unknown, not recovered).
    """
    if report.get("status") != "ok":
        return {"app": f"app health: {report.get('error', 'no status')}"}
    conditions = {"app": None}
    for source, info in report["sources"].items():
        fetched = info["latest_fetch"]
        name = f"fetch:{source}"
        if fetched is None:
            conditions[name] = f"{name}: never fetched"
            continue
        age_h = (now - datetime.fromisoformat(fetched)).total_seconds() / 3600
        conditions[name] = (f"{name}: last fetched {age_h:.1f}h ago "
                            f"(threshold {stale_hours}h)") if age_h > stale_hours else None
    return conditions


def scan_log_errors(log_path: str, state: dict) -> list[str]:
    """New ERROR lines since the last run, tracked by a byte offset in
    state. A shrunken file (rotation) rescans from the start."""
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
    return [line.rstrip() for line in lines if " ERROR " in line]


def transitions(conditions: dict, previous: dict) -> tuple[list, list]:
    """Compare against the last run's state: (new problems, recoveries)."""
    trips = [problem for name, problem in conditions.items()
             if problem and not previous.get(name)]
    recoveries = [f"{name} recovered" for name, problem in conditions.items()
                  if not problem and previous.get(name)]
    return trips, recoveries


def load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2))


def run(settings, now: datetime) -> int:
    monitoring = settings.monitoring
    state = load_state(monitoring.state_file)
    previous = state.get("conditions", {})

    cli = next(t for t in settings.transports if t.type == "cli")
    report = probe_health(cli.host, cli.port)
    conditions = fetch_conditions(report, monitoring.fetch_stale_hours, now)
    conditions.update(layer_conditions(settings.data, now))

    trips, recoveries = transitions(conditions, previous)
    log_offset_before = state.get("log_offset", 0)
    errors = scan_log_errors(settings.log_file, state)

    delivered = True
    if trips:
        delivered = notify("TrekSafer ALERT", "\n".join(trips)) and delivered
    if recoveries:
        delivered = notify("TrekSafer recovered", "\n".join(recoveries)) and delivered
    if errors:
        body = "\n".join(errors[:MAX_ERROR_LINES])
        if len(errors) > MAX_ERROR_LINES:
            body += f"\n... and {len(errors) - MAX_ERROR_LINES} more"
        delivered = notify("TrekSafer log errors", body) and delivered

    if delivered:
        state["conditions"] = {name: problem for name, problem in
                               {**previous, **conditions}.items() if problem}
    else:
        # Nothing was delivered; keep the old state and log offset so the
        # next run raises the same alerts again.
        state["log_offset"] = log_offset_before
        print("Alert delivery failed on every channel; will retry next run.")
    save_state(monitoring.state_file, state)

    for line in trips + recoveries:
        print(line)
    if errors:
        print(f"{len(errors)} new ERROR line(s) in {settings.log_file}")
    if not (trips or recoveries or errors):
        print("All healthy.")

    if monitoring.healthcheck_url:
        try:
            requests.get(monitoring.healthcheck_url, timeout=10)
        except requests.RequestException as e:
            print(f"healthcheck ping failed: {e}")
    return 0 if delivered else 1


def main():
    return run(get_config(), datetime.now(timezone.utc))


if __name__ == "__main__":
    sys.exit(main())
