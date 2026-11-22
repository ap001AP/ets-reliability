"""Tests for separating ETS from partner agencies and backfilling the agency table."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from analysis import build, quality
from collector.tz import EDMONTON
from gtfs_static import load
from tests.test_gtfs_static import make_zip

DAY = date(2026, 11, 21)

FEED = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                  "1,Edmonton Transit Service,https://www.edmonton.ca/ets,America/Edmonton\n"
                  "4,Strathcona County Transit,https://www.strathcona.ca,America/Edmonton\n",
    "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                  "9,1,009,ETS Line,3\n433,4,433,Bethel - Clarkdale,3\n",
    "trips.txt": "route_id,service_id,trip_id,direction_id\n9,SAT,A,0\n433,SAT,P,0\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nS1,One,53.5,-113.5\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
                      "A,08:00:00,08:00:00,S1,1,1\nP,08:00:00,08:00:00,S1,1,1\n",
    "calendar_dates.txt": "service_id,date,exception_type\nSAT,20261121,1\n",
}


@pytest.fixture
def world(pg, tmp_path):
    pg.execute("DROP SCHEMA IF EXISTS analysis CASCADE")
    pg.execute("DROP SCHEMA IF EXISTS gtfs CASCADE")
    load.db.apply_schema(pg, load.SCHEMA_PATH)
    path = make_zip(tmp_path / "gtfs_20261121_feed.zip", FEED)
    feed_id, _ = load.load_feed(pg, path)
    load.db.apply_schema(pg, build.SCHEMA_PATH)
    base = datetime(DAY.year, DAY.month, DAY.day, 8, 0, tzinfo=EDMONTON)
    for trip, route, delay in (("A", "9", 20), ("P", "433", 900)):
        pg.execute(
            "INSERT INTO analysis.stop_passages (service_date, trip_id, stop_sequence, feed_id, route_id, stop_id, "
            "timepoint, observed_ts, window_s, stops_in_window, schedule_date, scheduled_ts, delay_s, event) "
            "VALUES (%s, %s, 1, %s, %s, 'S1', 1, %s, 30, 1, %s, %s, %s, 'departure')",
            (DAY, trip, feed_id, route, base + timedelta(seconds=delay), DAY, base, delay),
        )
    pg.path = path
    return pg


def test_route_agency_flags_ets(world):
    rows = dict(world.execute("SELECT route_id, is_ets FROM analysis.route_agency").fetchall())
    assert rows == {"9": True, "433": False}


def test_stop_events_carry_agency(world):
    rows = world.execute("SELECT trip_id, agency, is_ets FROM analysis.stop_events ORDER BY trip_id").fetchall()
    assert rows == [("A", "Edmonton Transit Service", True), ("P", "Strathcona County Transit", False)]


def test_ets_standard_excludes_partners(world):
    summary = world.execute(build.ETS_STANDARD_SQL, {"date_from": DAY, "date_to": DAY}).fetchall()
    assert summary[0][1] == 1 and summary[0][2] == 100.0


def test_agency_summary_lists_both(world):
    rows = world.execute(build.AGENCY_SUMMARY_SQL, {"date_from": DAY, "date_to": DAY}).fetchall()
    by_name = {r[0]: r for r in rows}
    assert by_name["Edmonton Transit Service"][1:4] == ("yes", 1, 100.0)
    assert by_name["Strathcona County Transit"][1:4] == ("no", 1, 0.0)


def test_agency_backfilled_for_already_loaded_feed(world):
    world.execute("DELETE FROM gtfs.agency")
    assert load.load_feed(world, world.path) is None
    assert world.execute("SELECT count(*) FROM gtfs.agency").fetchone()[0] == 2


def test_quality_report_lists_agencies(world):
    assert "Strathcona County Transit" in quality.consistency(world)
