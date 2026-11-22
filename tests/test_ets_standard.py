"""Tests for ETS 2009 standard on-time classification and the cross-check breakdown."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from analysis import build, quality
from collector.tz import EDMONTON
from gtfs_static import load
from tests.test_gtfs_static import make_zip
from tests.test_headways import DAY, feed

DEPARTURE_DELAYS = {"A": -45, "B": -10, "C": 0, "D": 120, "E": 200, "F": 400}
ARRIVAL_DELAYS = {"A": -200, "B": 90}


@pytest.fixture
def world(pg, tmp_path):
    pg.execute("DROP SCHEMA IF EXISTS analysis CASCADE")
    pg.execute("DROP SCHEMA IF EXISTS gtfs CASCADE")
    load.db.apply_schema(pg, load.SCHEMA_PATH)
    feed_id, _ = load.load_feed(pg, make_zip(tmp_path / "gtfs_20261121_feed.zip", feed()))
    load.db.apply_schema(pg, build.SCHEMA_PATH)
    base = datetime(DAY.year, DAY.month, DAY.day, 8, 0, tzinfo=EDMONTON)
    rows = [(trip, 1, "departure", 1, d) for trip, d in DEPARTURE_DELAYS.items()]
    rows += [(trip, 2, "arrival", 1, d) for trip, d in ARRIVAL_DELAYS.items()]
    rows += [("G", 1, "departure", 0, 500)]
    for trip, seq, event, timepoint, delay in rows:
        pg.execute(
            "INSERT INTO analysis.stop_passages (service_date, trip_id, stop_sequence, feed_id, route_id, stop_id, "
            "timepoint, observed_ts, window_s, stops_in_window, schedule_date, scheduled_ts, delay_s, event) "
            "VALUES (%s, %s, %s, %s, '9', 'S1', %s, %s, 30, 1, %s, %s, %s, %s)",
            (DAY, trip, seq, feed_id, timepoint, base + timedelta(seconds=delay), DAY, base, delay, event),
        )
    return pg


def test_departures_need_timepoint_and_follow_zero_to_three_minutes(world):
    rows = dict(world.execute(
        "SELECT trip_id, ets_status FROM analysis.ets_standard_events WHERE event = 'departure'").fetchall())
    assert rows == {"A": "early", "B": "early", "C": "on_time", "D": "on_time", "E": "late", "F": "late"}


def test_tolerance_column_allows_thirty_seconds(world):
    rows = dict(world.execute(
        "SELECT trip_id, on_time_allowing_30s FROM analysis.ets_standard_events WHERE event = 'departure'").fetchall())
    assert rows == {"A": False, "B": True, "C": True, "D": True, "E": True, "F": False}


def test_final_arrivals_follow_five_early_to_one_late(world):
    rows = dict(world.execute(
        "SELECT trip_id, ets_status FROM analysis.ets_standard_events WHERE event = 'arrival'").fetchall())
    assert rows == {"A": "on_time", "B": "late"}


def test_route_view_and_network_summary(world):
    pct_on, pct_early, pct_late, pct_tol = world.execute(
        "SELECT pct_on_time, pct_early, pct_late, pct_on_time_allowing_30s FROM analysis.ets_standard_by_route"
    ).fetchone()
    assert [float(x) for x in (pct_on, pct_early, pct_late, pct_tol)] == [33.3, 33.3, 33.3, 66.7]
    summary = world.execute(build.ETS_STANDARD_SQL, {"date_from": DAY, "date_to": DAY}).fetchall()
    assert [r[:3] for r in summary] == [
        ("all timepoint departures (0 to +3 min)", 6, 33.3),
        ("first-stop departures (0 to +3 min)", 6, 33.3),
        ("final-stop arrivals (-5 to +1 min)", 2, 50.0),
    ]


def test_cross_check_breakdown_groups(world):
    world.execute("INSERT INTO rt.poll_log (poll_id, feed, polled_at) VALUES (1, 'trip_updates', now())")
    base = datetime(DAY.year, DAY.month, DAY.day, 8, 0, tzinfo=EDMONTON)
    for trip, offset in (("C", 10), ("D", 130)):
        world.execute(
            "INSERT INTO rt.trip_updates (poll_id, feed_ts, trip_id, start_date, stop_sequence, n_stop_updates, "
            "is_past, departure_ts) VALUES (1, now(), %s, %s, 1, 1, true, %s)",
            (trip, DAY, base + timedelta(seconds=offset)),
        )
    rows = world.execute(build.CROSS_CHECK_BREAKDOWN_SQL, {"date_from": DAY, "date_to": DAY}).fetchall()
    groups = {(r[0], r[1]): r[2] for r in rows}
    assert groups[("position", "first stop")] == 2
    assert groups[("route type", "regular")] == 2
    assert ("timepoint", "timepoint") in groups


def test_quality_report_includes_ets_standard(world):
    text = quality.validation(world)
    assert "2009 Transit Service Standards" in text
    assert "first-stop departures (0 to +3 min)" in text
