"""Database tests for shape-matched stop passages, schedule delay and the validation report."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from analysis import build, shape_match
from gtfs_static import load
from tests.test_gtfs_static import make_zip

EDMONTON = ZoneInfo("America/Edmonton")
DAY = date(2026, 11, 20)
M_PER_DEG_LAT = shape_match.EARTH_R * 3.141592653589793 / 180
LON = -113.5


def lat(y_m: float) -> float:
    return 53.5 + y_m / M_PER_DEG_LAT


STOP_Y = [0.0, 0.002 * M_PER_DEG_LAT, 0.004 * M_PER_DEG_LAT, 0.006 * M_PER_DEG_LAT]

FEED = {
    "routes.txt": "route_id,route_short_name,route_long_name,route_type\n4,4,Lewis Farms - Capilano,3\n",
    "trips.txt": "route_id,service_id,trip_id,direction_id,shape_id\n4,WKDY,T1,0,SH\n4,WKDY,T2,0,SH\n4,WKDY,T3,0,SH\n4,SAT,T9,0,SH\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                 "S1,One,53.500,-113.5\nS2,Two,53.502,-113.5\nS3,Three,53.504,-113.5\nS4,Four,53.506,-113.5\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
                      "T1,08:00:00,08:00:00,S1,1,1\nT1,08:01:00,08:01:00,S2,2,0\n"
                      "T1,08:02:00,08:02:00,S3,3,0\nT1,08:03:00,08:03:00,S4,4,1\n"
                      "T2,09:00:00,09:00:00,S1,1,1\nT2,09:01:00,09:01:00,S2,2,0\n"
                      "T2,09:02:00,09:02:00,S3,3,0\nT2,09:03:00,09:03:00,S4,4,1\n"
                      "T3,24:30:00,24:30:00,S1,1,1\nT3,24:31:00,24:31:00,S2,2,0\n"
                      "T3,24:32:00,24:32:00,S3,3,0\nT3,24:33:00,24:33:00,S4,4,1\n"
                      "T9,08:00:00,08:00:00,S1,1,1\nT9,08:03:00,08:03:00,S4,2,1\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                  "SH,53.500,-113.5,1\nSH,53.503,-113.5,2\nSH,53.507,-113.5,3\n",
    "calendar_dates.txt": "service_id,date,exception_type\nWKDY,20261120,1\nSAT,20261121,1\n",
}

T1_PINGS = [
    ("07:59:30", 0, 0, 1), ("08:00:00", 0, 0, 1), ("08:00:30", 0, 0, 4), ("08:01:00", 240, 0, 2),
    ("08:01:15", 250, 0.01, 4), ("08:01:30", 480, 0, 3), ("08:02:00", 720, 0, 4),
]
T2_PINGS = [("09:01:30", 300, 0, 2), ("09:02:00", 540, 0, 4)]
NEXT_DAY = DAY + timedelta(days=1)
T3_PINGS = [("00:30:00", 0, 0, 1), ("00:30:30", 0, 0, 1), ("00:31:00", 240, 0, 2)]


def local(hms: str, day: date = DAY) -> datetime:
    h, m, s = map(int, hms.split(":"))
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=EDMONTON)


@pytest.fixture
def world(pg, tmp_path):
    pg.execute("DROP SCHEMA IF EXISTS analysis CASCADE")
    pg.execute("DROP SCHEMA IF EXISTS gtfs CASCADE")
    load.db.apply_schema(pg, load.SCHEMA_PATH)
    load.load_feed(pg, make_zip(tmp_path / "gtfs_20261120_feed.zip", FEED))
    load.db.apply_schema(pg, build.SCHEMA_PATH)
    pg.execute("INSERT INTO rt.poll_log (poll_id, feed, polled_at) VALUES (1, 'vehicle_positions', now())")
    for trip, vehicle, pings, day in (("T1", "4501", T1_PINGS, DAY), ("T2", "4502", T2_PINGS, DAY),
                                      ("T3", "4503", T3_PINGS, NEXT_DAY)):
        for hms, y, dlon, seq in pings:
            pg.execute(
                "INSERT INTO rt.vehicle_positions (poll_id, vehicle_id, vehicle_ts, trip_id, route_id, start_date, "
                "latitude, longitude, current_stop_sequence) VALUES (1, %s, %s, %s, '4', %s, %s, %s, %s)",
                (vehicle, local(hms, day), trip, day, lat(y), LON + dlon, seq),
            )
    return pg


def expected_time(t0: str, y0: float, y1: float, stop_y: float) -> datetime:
    frac = (stop_y + shape_match.DEPART_OFFSET_M - y0) / (y1 - y0)
    return local(t0) + timedelta(seconds=30 * frac)


def test_feed_by_date_picks_loaded_feed(world):
    assert world.execute("SELECT feed_id FROM analysis.feed_by_date WHERE service_date = %s", (DAY,)).fetchone()


def test_build_day_times_each_stop_from_gps(world):
    stats = shape_match.build_day(world, DAY)
    assert stats.trips_seen == 2 and stats.passages == 5
    assert stats.pings == 9 and stats.pings_on_route == 8
    rows = world.execute(
        "SELECT stop_sequence, observed_ts, window_s, stops_in_window, delay_s, stop_id "
        "FROM analysis.stop_passages WHERE trip_id = 'T1' ORDER BY stop_sequence"
    ).fetchall()
    expected = [
        expected_time("08:00:30", 0, 240, STOP_Y[0]),
        expected_time("08:01:00", 240, 480, STOP_Y[1]),
        expected_time("08:01:00", 240, 480, STOP_Y[2]),
        expected_time("08:01:30", 480, 720, STOP_Y[3]),
    ]
    assert [r[0] for r in rows] == [1, 2, 3, 4]
    for (seq, observed, window, n, delay, stop_id), exp in zip(rows, expected):
        assert abs((observed - exp).total_seconds()) < 0.5
        assert window == 30
        assert stop_id == f"S{seq}"
    assert [r[3] for r in rows] == [1, 2, 2, 1]
    scheduled = [local(f"08:0{i}:00") for i in range(4)]
    for (seq, observed, _, _, delay, _), sched in zip(rows, scheduled):
        assert abs(delay - (observed - sched).total_seconds()) <= 1


def test_stop_numbers_are_ignored(world):
    shape_match.build_day(world, DAY)
    assert world.execute("SELECT count(*) FROM analysis.stop_passages WHERE trip_id = 'T1'").fetchone()[0] == 4


def test_trip_first_seen_mid_route_skips_earlier_stops(world):
    shape_match.build_day(world, DAY)
    rows = world.execute("SELECT stop_sequence FROM analysis.stop_passages WHERE trip_id = 'T2'").fetchall()
    assert rows == [(3,)]


def test_rebuild_is_idempotent(world):
    shape_match.build_day(world, DAY)
    shape_match.build_day(world, DAY)
    assert world.execute("SELECT count(*) FROM analysis.stop_passages").fetchone()[0] == 5


def test_day_without_pings_clears_rows(world):
    shape_match.build_day(world, DAY)
    world.execute("DELETE FROM rt.vehicle_positions")
    shape_match.build_day(world, DAY)
    assert world.execute("SELECT count(*) FROM analysis.stop_passages").fetchone()[0] == 0


def test_stop_events_classify(world):
    shape_match.build_day(world, DAY)
    rows = world.execute(
        "SELECT trip_id, stop_sequence, status FROM analysis.stop_events ORDER BY trip_id, stop_sequence"
    ).fetchall()
    assert rows == [("T1", 1, "on_time"), ("T1", 2, "on_time"), ("T1", 3, "on_time"),
                    ("T1", 4, "early"), ("T2", 3, "on_time")]


def test_check_report_runs(world, capsys):
    shape_match.build_day(world, DAY)
    build.check(world, DAY, DAY)
    out = capsys.readouterr().out
    assert "Network on-time performance" in out
    assert "Cross-check against ETS" in out


def test_after_midnight_trip_labelled_next_day_uses_previous_schedule_day(world):
    stats = shape_match.build_day(world, NEXT_DAY)
    assert stats.passages == 1
    service_date, schedule_date, delay = world.execute(
        "SELECT service_date, schedule_date, delay_s FROM analysis.stop_passages WHERE trip_id = 'T3'"
    ).fetchone()
    assert service_date == NEXT_DAY and schedule_date == DAY
    assert 0 < delay < 60
    status = world.execute("SELECT status FROM analysis.stop_events WHERE trip_id = 'T3'").fetchone()[0]
    assert status == "on_time"


def test_same_day_trips_keep_their_own_schedule_date(world):
    shape_match.build_day(world, DAY)
    assert world.execute("SELECT DISTINCT schedule_date FROM analysis.stop_passages").fetchall() == [(DAY,)]
