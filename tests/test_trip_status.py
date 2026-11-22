"""Tests for scheduled-trip delivery status and the collector-coverage guard."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from analysis import build
from gtfs_static import load
from tests.test_gtfs_static import make_zip
from collector.tz import EDMONTON

DAY = date(2026, 11, 21)
STARTS = {"A": "08:00", "B": "08:10", "C": "08:20", "D": "08:30", "E": "08:40", "F": "09:10", "G": "10:30"}


def local(hm: str, seconds: int = 0) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=EDMONTON) + timedelta(seconds=seconds)


def feed() -> dict[str, str]:
    lines = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    for trip, hm in STARTS.items():
        for k in range(4):
            h, m = map(int, hm.split(":"))
            t = f"{h:02d}:{m + k:02d}:00"
            lines.append(f"{trip},{t},{t},S{k + 1},{k + 1}")
    lines += [f"H,08:5{k}:00,08:5{k}:00,S{k + 1},{k + 1}" for k in range(4)]
    return {
        "routes.txt": "route_id,route_short_name,route_long_name,route_type\n9,9,Test Line,3\nL,L,Rail Line,0\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\n" + "".join(f"9,SAT,{t},0\n" for t in STARTS)
                     + "L,SAT,H,0\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n" + "".join(f"S{k},S{k},53.5,-113.5\n" for k in range(1, 5)),
        "stop_times.txt": "\n".join(lines) + "\n",
        "calendar_dates.txt": "service_id,date,exception_type\nSAT,20261121,1\n",
    }


@pytest.fixture
def world(pg, tmp_path):
    pg.execute("DROP SCHEMA IF EXISTS analysis CASCADE")
    pg.execute("DROP SCHEMA IF EXISTS gtfs CASCADE")
    load.db.apply_schema(pg, load.SCHEMA_PATH)
    feed_id, _ = load.load_feed(pg, make_zip(tmp_path / "gtfs_20261121_feed.zip", feed()))
    load.db.apply_schema(pg, build.SCHEMA_PATH)
    pg.execute(
        """
        INSERT INTO rt.poll_log (feed, polled_at, http_status)
        SELECT 'vehicle_positions', ts, 200
        FROM generate_series(%s::timestamptz, %s::timestamptz, interval '30 seconds') ts
        WHERE NOT (ts >= %s AND ts < %s)
        """,
        (local("07:30"), local("10:00"), local("09:00"), local("09:40")),
    )
    poll_id = pg.execute("SELECT min(poll_id) FROM rt.poll_log").fetchone()[0]
    pg.execute("INSERT INTO rt.vehicle_positions (poll_id, vehicle_id, vehicle_ts, trip_id, start_date) "
               "VALUES (%s, 'V1', %s, 'C', %s)", (poll_id, local("08:19"), DAY))
    pg.execute("INSERT INTO rt.trip_updates (poll_id, trip_id, start_date, trip_schedule_relationship, n_stop_updates) "
               "VALUES (%s, 'D', %s, 'CANCELED', 0)", (poll_id, DAY))
    for trip, n in (("A", 4), ("B", 1)):
        for seq in range(1, n + 1):
            pg.execute(
                "INSERT INTO analysis.stop_passages (service_date, trip_id, stop_sequence, feed_id, route_id, "
                "stop_id, observed_ts, window_s, stops_in_window, schedule_date) "
                "VALUES (%s, %s, %s, %s, '9', %s, %s, 30, 1, %s)",
                (DAY, trip, seq, feed_id, f"S{seq}", local(STARTS[trip], 60 * (seq - 1) + 20), DAY),
            )
    return pg


def statuses(conn) -> dict[str, str]:
    return dict(conn.execute("SELECT trip_id, status FROM analysis.trip_status").fetchall())


def test_every_status_assigned(world):
    assert build.build_trip_status(world, DAY) == 8
    assert statuses(world) == {
        "A": "ran", "B": "partial", "C": "tracked_not_matched", "D": "cancelled",
        "E": "missing", "F": "no_data", "G": "no_data", "H": "not_in_feed",
    }


def test_coverage_recorded(world):
    build.build_trip_status(world, DAY)
    cov = dict(world.execute("SELECT trip_id, feed_coverage FROM analysis.trip_status").fetchall())
    assert float(cov["E"]) == 1.0
    assert float(cov["F"]) < 0.8 and float(cov["G"]) == 0


def test_delivery_by_route_excludes_no_data(world):
    build.build_trip_status(world, DAY)
    judged, missing, pct_delivered, pct_missing = world.execute(
        "SELECT trips_judged, missing, pct_delivered, pct_missing FROM analysis.delivery_by_route WHERE route_id = '9'"
    ).fetchone()
    assert (judged, missing) == (5, 1)
    assert float(pct_delivered) == 40.0 and float(pct_missing) == 20.0


def test_rebuild_is_idempotent(world):
    build.build_trip_status(world, DAY)
    build.build_trip_status(world, DAY)
    assert world.execute("SELECT count(*) FROM analysis.trip_status").fetchone()[0] == 8


def test_check_report_includes_delivery(world, capsys):
    build.build_trip_status(world, DAY)
    build.check(world, DAY, DAY)
    out = capsys.readouterr().out
    assert "Trip delivery by schedule date" in out
    assert "Routes with the most missing trips" in out


def test_sql_steps_parse_in_order():
    names = [name for name, _ in build.sql_steps(build.TRIP_STATUS_PATH.read_text())]
    assert names[0] == "schedule" and names[-1] == "classify"
    assert all(sql and not sql.endswith(";") for _, sql in build.sql_steps(build.TRIP_STATUS_PATH.read_text()))


def test_route_absent_from_feed_is_not_judged(world):
    build.build_trip_status(world, DAY)
    judged, not_in_feed, pct_missing = world.execute(
        "SELECT trips_judged, not_in_feed, pct_missing FROM analysis.delivery_by_route WHERE route_id = 'L'"
    ).fetchone()
    assert (judged, not_in_feed, pct_missing) == (0, 1, None)
