"""Database tests for the polling loop."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector import feeds, run
from collector.config import Config
from tests.conftest import HEADER_TS, add_alert, add_trip_update, add_vehicle, new_feed


def make_cfg(tmp_path: Path) -> Config:
    return Config(
        database_url="", vehicle_positions_url="vp", trip_updates_url="tu", alerts_url="al",
        static_url="st", realtime_interval_s=30, alerts_interval_s=300, static_interval_s=3600,
        upcoming_stops=1, raw_archive_every=1, archive_dir=tmp_path, http_timeout_s=5,
    )


@pytest.fixture
def serve(monkeypatch):
    queue: list = []

    def fake_fetch(session, feed, url, timeout=15.0):
        item = queue.pop(0)
        now = datetime.now(timezone.utc)
        if isinstance(item, str):
            return feeds.FetchResult(feed, now, None, 5, None, item)
        return feeds.FetchResult(feed, now, 200, 5, item.SerializeToString(), None)

    monkeypatch.setattr(feeds, "fetch", fake_fetch)
    return queue


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_vehicle_positions_insert_dedupe_and_stale(pg, serve, tmp_path):
    cfg = make_cfg(tmp_path)
    job = run.Job("vehicle_positions", "vp", 30)

    first = new_feed(HEADER_TS)
    add_vehicle(first, "4501", HEADER_TS - 5)
    add_vehicle(first, "4502", HEADER_TS - 8)
    second = new_feed(HEADER_TS + 30)
    add_vehicle(second, "4501", HEADER_TS - 5)
    add_vehicle(second, "4502", HEADER_TS + 25)
    serve.extend([first, second, second])

    for _ in range(3):
        run.poll_feed(pg, None, job, cfg)

    assert count(pg, "SELECT count(*) FROM rt.vehicle_positions") == 3
    logs = pg.execute("SELECT rows_written, is_stale FROM rt.poll_log ORDER BY poll_id").fetchall()
    assert logs == [(2, False), (1, False), (0, True)]
    assert any((tmp_path / "raw").rglob("*.pb.gz"))


def test_fetch_failure_is_logged_not_raised(pg, serve, tmp_path):
    serve.append("ConnectionError: boom")
    run.poll_feed(pg, None, run.Job("trip_updates", "tu", 30), make_cfg(tmp_path))
    row = pg.execute("SELECT http_status, error, rows_written FROM rt.poll_log").fetchone()
    assert row == (None, "ConnectionError: boom", 0)


def test_trip_updates_written(pg, serve, tmp_path):
    msg = new_feed()
    add_trip_update(msg, "T1", [(1, "S1", HEADER_TS - 60, 30), (2, "S2", HEADER_TS + 60, 45)])
    add_trip_update(msg, "T2", [], canceled=True)
    serve.append(msg)
    run.poll_feed(pg, None, run.Job("trip_updates", "tu", 30), make_cfg(tmp_path))
    rows = pg.execute(
        "SELECT trip_id, stop_id, is_past, arrival_delay_s, trip_schedule_relationship "
        "FROM rt.trip_updates ORDER BY trip_id, stop_sequence NULLS LAST"
    ).fetchall()
    assert rows == [("T1", "S1", True, 30, None), ("T1", "S2", False, 45, None),
                    ("T2", None, None, None, "CANCELED")]


def test_unchanged_trip_predictions_are_not_repeated(pg, serve, tmp_path):
    cfg = make_cfg(tmp_path)
    job = run.Job("trip_updates", "tu", 30)
    for offset, delay in ((0, 45), (30, 45), (60, 90)):
        msg = new_feed(HEADER_TS + offset)
        add_trip_update(msg, "T1", [(2, "S2", HEADER_TS + 600 + delay, delay)])
        serve.append(msg)
        run.poll_feed(pg, None, job, cfg)
    delays = [r[0] for r in pg.execute("SELECT arrival_delay_s FROM rt.trip_updates ORDER BY poll_id")]
    assert delays == [45, 90]
    written = [r[0] for r in pg.execute("SELECT rows_written FROM rt.poll_log ORDER BY poll_id")]
    assert written == [1, 0, 1]


def test_migration_dedupes_existing_rows(pg):
    pg.execute("DROP INDEX rt.trip_updates_dedupe_idx")
    pg.execute("CREATE INDEX trip_updates_trip_stop_idx ON rt.trip_updates (start_date, trip_id, stop_sequence)")
    pg.execute("INSERT INTO rt.poll_log (poll_id, feed, polled_at) VALUES (1, 'trip_updates', now()), (2, 'trip_updates', now())")
    for poll_id in (1, 2):
        pg.execute("INSERT INTO rt.trip_updates (poll_id, trip_id, stop_sequence, n_stop_updates, departure_delay_s) "
                   "VALUES (%s, 'T1', 3, 5, 60), (%s, NULL, 1, 1, NULL)", (poll_id, poll_id))
    migration = Path(__file__).resolve().parent.parent / "sql" / "migrations" / "001_trip_updates_dedupe.sql"
    pg.execute(migration.read_text())
    assert pg.execute("SELECT count(*), min(poll_id), max(poll_id) FROM rt.trip_updates").fetchone() == (2, 1, 1)


def test_alerts_upsert_last_seen(pg, serve, tmp_path):
    cfg = make_cfg(tmp_path)
    job = run.Job("alerts", "al", 300)
    for offset in (0, 300):
        msg = new_feed(HEADER_TS + offset)
        add_alert(msg, "A1", "Route 4 detour", ["4"])
        serve.append(msg)
        run.poll_feed(pg, None, job, cfg)
    n, first, last = pg.execute("SELECT count(*), min(first_seen), max(last_seen) FROM rt.alerts").fetchone()
    assert n == 1
    assert last > first
