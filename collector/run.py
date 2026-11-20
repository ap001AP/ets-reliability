"""ETS GTFS Realtime polling loop."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
import requests
from google.protobuf.message import DecodeError

from collector import archive, db, feeds
from collector.config import Config

log = logging.getLogger("collector")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


@dataclass
class Job:
    name: str
    url: str
    every_s: float
    next_run: float = 0.0
    polls: int = 0
    last_header_epoch: int | None = None


def rows_for(feed: str, msg, cfg: Config) -> list[dict]:
    if feed == "vehicle_positions":
        return feeds.vehicle_rows(msg)
    if feed == "trip_updates":
        return feeds.trip_update_rows(msg, upcoming_stops=cfg.upcoming_stops)
    return feeds.alert_rows(msg)


def poll_feed(conn: psycopg.Connection, session: requests.Session, job: Job, cfg: Config) -> None:
    result = feeds.fetch(session, job.name, job.url, cfg.http_timeout_s)
    header_ts = entity_count = None
    header_epoch = None
    rows: list[dict] = []
    stale = False
    error = result.error

    if result.content is not None:
        try:
            msg = feeds.parse(result.content)
            header_epoch = msg.header.timestamp or None
            header_ts = feeds.to_ts(header_epoch)
            entity_count = len(msg.entity)
            stale = header_epoch is not None and header_epoch == job.last_header_epoch
            if not stale:
                rows = rows_for(job.name, msg, cfg)
        except DecodeError as exc:
            error = f"DecodeError: {exc}"

    with conn.transaction(), conn.cursor() as cur:
        poll_id = db.log_poll(cur, result, header_ts, entity_count, stale, error)
        written = db.WRITERS[job.name](cur, poll_id, rows, result.polled_at)
        db.set_rows_written(cur, poll_id, written)

    if not stale and header_epoch is not None:
        job.last_header_epoch = header_epoch
    job.polls += 1

    if result.content is not None and cfg.raw_archive_every > 0 and (job.polls - 1) % cfg.raw_archive_every == 0:
        archive.save_raw(cfg.archive_dir, job.name, result.polled_at, result.content)

    if error:
        log.warning("%s: %s (%s ms)", job.name, error, result.latency_ms)
    else:
        log.info("%s: %s entities, %s rows%s, %s ms", job.name, entity_count, written,
                 " [stale]" if stale else "", result.latency_ms)


def poll_static(conn: psycopg.Connection, session: requests.Session, cfg: Config) -> None:
    try:
        content, sha, fetched_at = archive.fetch_static(session, cfg.static_url, cfg.http_timeout_s)
    except requests.RequestException as exc:
        log.warning("static: %s: %s", type(exc).__name__, exc)
        return
    with conn.transaction(), conn.cursor() as cur:
        if db.static_snapshot_exists(cur, sha):
            log.info("static: unchanged (%s)", sha[:10])
            return
        path = archive.save_static(cfg.archive_dir, content, sha, fetched_at)
        db.record_static_snapshot(cur, sha, fetched_at, len(content), str(path))
    log.info("static: new snapshot saved to %s", path)


def ensure_connection(conn: psycopg.Connection | None, dsn: str) -> psycopg.Connection:
    if conn is not None and not conn.closed and not conn.broken:
        return conn
    log.info("connecting to database")
    return db.connect(dsn)


def run(cfg: Config, once: bool = False) -> None:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    session = requests.Session()
    session.headers["User-Agent"] = "ets-reliability-collector/1.0"

    jobs = [
        Job("vehicle_positions", cfg.vehicle_positions_url, cfg.realtime_interval_s),
        Job("trip_updates", cfg.trip_updates_url, cfg.realtime_interval_s),
        Job("alerts", cfg.alerts_url, cfg.alerts_interval_s),
        Job("static", cfg.static_url, cfg.static_interval_s),
    ]
    conn: psycopg.Connection | None = None

    while not stop.is_set():
        now = time.monotonic()
        for job in jobs:
            if now < job.next_run:
                continue
            job.next_run = now + job.every_s
            try:
                conn = ensure_connection(conn, cfg.database_url)
                if job.name == "static":
                    poll_static(conn, session, cfg)
                else:
                    poll_feed(conn, session, job, cfg)
            except psycopg.OperationalError as exc:
                log.error("database unavailable: %s", exc)
                conn = None
            except Exception:
                log.exception("%s: unexpected error", job.name)
        if once:
            break
        wait = min(j.next_run for j in jobs) - time.monotonic()
        stop.wait(max(wait, 0.5))

    if conn is not None:
        conn.close()
    log.info("collector stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="ETS GTFS Realtime collector")
    parser.add_argument("--once", action="store_true", help="poll every feed once and exit")
    parser.add_argument("--init-db", action="store_true", help="apply sql/schema.sql before starting")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()

    if args.init_db:
        with db.connect(cfg.database_url) as conn:
            db.apply_schema(conn, SCHEMA_PATH)
        log.info("schema applied")

    run(cfg, once=args.once)


if __name__ == "__main__":
    main()
