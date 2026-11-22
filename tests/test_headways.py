"""Tests for headway pairing, bunching classification and the data-gap guard."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from analysis import build
from gtfs_static import load
from tests.test_gtfs_static import make_zip
from collector.tz import EDMONTON

DAY = date(2026, 11, 21)
TRIPS = ["A", "B", "C", "D", "E", "F", "G"]
OBSERVED = {"A": "08:00:30", "B": "08:13:00", "C": "08:14:00", "E": "08:40:10", "G": "09:00:20"}
TRACKED = {"A", "B", "C", "D", "E", "G"}


def local(hms: str) -> datetime:
    h, m, s = map(int, hms.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, h, m, s, tzinfo=EDMONTON)


def feed() -> dict[str, str]:
    stop_times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    for i, trip in enumerate(TRIPS):
        t = f"08:{i * 10:02d}:00" if i < 6 else "09:00:00"
        stop_times.append(f"{trip},{t},{t},S1,1")
    return {
        "routes.txt": "route_id,route_short_name,route_long_name,route_type\n9,9,Frequent Line,3\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\n" + "".join(f"9,SAT,{t},0\n" for t in TRIPS),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nS1,One,53.5,-113.5\n",
        "stop_times.txt": "\n".join(stop_times) + "\n",
        "calendar_dates.txt": "service_id,date,exception_type\nSAT,20261121,1\n",
    }


@pytest.fixture
def world(pg, tmp_path):
    pg.execute("DROP SCHEMA IF EXISTS analysis CASCADE")
    pg.execute("DROP SCHEMA IF EXISTS gtfs CASCADE")
    load.db.apply_schema(pg, load.SCHEMA_PATH)
    feed_id, _ = load.load_feed(pg, make_zip(tmp_path / "gtfs_20261121_feed.zip", feed()))
    load.db.apply_schema(pg, build.SCHEMA_PATH)
    pg.execute("INSERT INTO rt.poll_log (poll_id, feed, polled_at) VALUES (1, 'vehicle_positions', now())")
    for i, trip in enumerate(sorted(TRACKED)):
        pg.execute(
            "INSERT INTO rt.vehicle_positions (poll_id, vehicle_id, vehicle_ts, trip_id, start_date) "
            "VALUES (1, %s, %s, %s, %s)", (f"V{i}", local("07:50:00"), trip, DAY),
        )
    for trip, hms in OBSERVED.items():
        pg.execute(
            "INSERT INTO analysis.stop_passages (service_date, trip_id, stop_sequence, feed_id, route_id, direction_id, "
            "stop_id, observed_ts, window_s, stops_in_window, schedule_date) "
            "VALUES (%s, %s, 1, %s, '9', 0, 'S1', %s, 30, 1, %s)",
            (DAY, trip, feed_id, local(hms), DAY),
        )
    return pg


def headways(conn):
    return {
        r[0]: r[1:]
        for r in conn.execute(
            "SELECT trip_id, prev_trip_id, actual_headway_s, scheduled_headway_s, data_gap FROM analysis.headways"
        )
    }


def test_pairs_follow_observed_order(world):
    assert build.build_headways(world, DAY) == 4
    rows = headways(world)
    assert rows["B"] == ("A", 750, 600, False)
    assert rows["C"] == ("B", 60, 600, False)
    assert rows["G"] == ("E", 1210, 600, False)


def test_tracked_bus_missing_at_stop_flags_data_gap(world):
    build.build_headways(world, DAY)
    assert headways(world)["E"][0] == "C" and headways(world)["E"][3] is True


def test_spacing_classification_excludes_data_gaps(world):
    build.build_headways(world, DAY)
    rows = world.execute("SELECT trip_id, spacing FROM analysis.headway_events ORDER BY trip_id").fetchall()
    assert rows == [("B", "regular"), ("C", "bunched"), ("G", "gap")]


def test_bunching_by_route_metrics(world):
    build.build_headways(world, DAY)
    events, pct_bunched, pct_gap, ewt = world.execute(
        "SELECT events, pct_bunched, pct_gap, excess_wait_s FROM analysis.bunching_by_route"
    ).fetchone()
    assert events == 3
    assert float(pct_bunched) == pytest.approx(33.3) and float(pct_gap) == pytest.approx(33.3)
    actual = [750, 60, 1210]
    expected_ewt = sum(a * a for a in actual) / (2 * sum(actual)) - 600 / 2
    assert float(ewt) == pytest.approx(round(expected_ewt))


def test_rebuild_is_idempotent(world):
    build.build_headways(world, DAY)
    build.build_headways(world, DAY)
    assert world.execute("SELECT count(*) FROM analysis.headways").fetchone()[0] == 4


def test_check_report_includes_bunching(world, capsys):
    build.build_headways(world, DAY)
    build.check(world, DAY, DAY)
    assert "Bunching on frequent ETS service" in capsys.readouterr().out
