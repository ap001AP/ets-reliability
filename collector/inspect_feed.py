"""Report field coverage for the live ETS realtime feeds."""

from __future__ import annotations

import time
from collections import Counter

import requests

from collector import feeds
from collector.config import Config


def coverage(rows: list[dict]) -> list[tuple[str, float, object]]:
    if not rows:
        return []
    out = []
    for key in rows[0]:
        filled = [r[key] for r in rows if r[key] is not None]
        sample = filled[0] if filled else None
        out.append((key, 100 * len(filled) / len(rows), sample))
    return out


def report(name: str, url: str, cfg: Config, session: requests.Session) -> None:
    print(f"\n=== {name} ===\n{url}")
    res = feeds.fetch(session, name, url, cfg.http_timeout_s)
    if res.error:
        print(f"  FAILED: {res.error}")
        return
    msg = feeds.parse(res.content)
    lag = int(time.time()) - msg.header.timestamp if msg.header.timestamp else None
    print(f"  {len(res.content):,} bytes, {res.latency_ms} ms, {len(msg.entity)} entities, header lag {lag}s")

    if name == "vehicle_positions":
        rows = feeds.vehicle_rows(msg)
    elif name == "trip_updates":
        rows = feeds.trip_update_rows(msg, upcoming_stops=cfg.upcoming_stops)
        stus = [len(e.trip_update.stop_time_update) for e in msg.entity if e.HasField("trip_update")]
        if stus:
            print(f"  stop_time_updates per trip: min {min(stus)}, max {max(stus)}, avg {sum(stus) / len(stus):.1f}")
        print(f"  past-stop rows kept: {sum(1 for r in rows if r['is_past'])} of {len(rows)}")
    else:
        rows = feeds.alert_rows(msg)

    print(f"  {'field':<28}{'filled':>8}  sample")
    for key, pct, sample in coverage(rows):
        print(f"  {key:<28}{pct:>7.0f}%  {str(sample)[:50]}")

    for key in ("current_status", "trip_schedule_relationship", "stop_schedule_relationship", "effect"):
        values = Counter(r.get(key) for r in rows if key in r)
        if values and any(values):
            print(f"  {key}: {dict(values)}")


def main() -> None:
    cfg = Config.from_env()
    session = feeds.make_session()
    report("vehicle_positions", cfg.vehicle_positions_url, cfg, session)
    report("trip_updates", cfg.trip_updates_url, cfg, session)
    report("alerts", cfg.alerts_url, cfg, session)


if __name__ == "__main__":
    main()
