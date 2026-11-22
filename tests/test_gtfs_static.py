"""Tests for the static GTFS loader, service calendar and local-time helper."""

from __future__ import annotations

import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from gtfs_static import load
from collector.tz import EDMONTON


FILES = {
    "routes.txt": "﻿route_id,route_short_name,route_long_name,route_type,network_id\n"
                  "4,4,Lewis Farms - Capilano,3,ets\n",
    "trips.txt": "route_id,service_id,trip_id,direction_id,shape_id\n"
                 "4,WKDY,T1,0,SH1\n4,WKDY,T2,1,SH1\n4,SAT,T3,0,SH1\n",
    "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon\n"
                 "S1,1001,Stop One,53.5,-113.5\nS2,1002,Stop Two,53.51,-113.49\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
                      "T1,08:00:00,08:00:00,S1,1,1\nT1,08:07:30,08:08:00,S2,2,\n"
                      "T2,24:50:00,24:50:00,S2,1,1\nT2,,,S1,2,0\n"
                      "T3,9:15:00,9:15:00,S1,1,1\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                    "WKDY,1,1,1,1,1,0,0,20261116,20261130\nSAT,0,0,0,0,0,1,0,20261116,20261130\n",
    "calendar_dates.txt": "service_id,date,exception_type\n"
                          "WKDY,20261111,1\nWKDY,20261120,2\nSAT,20261120,1\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                  "SH1,53.5,-113.5,1\nSH1,53.51,-113.49,2\n",
    "feed_info.txt": "feed_publisher_name,feed_version,feed_start_date,feed_end_date\n"
                     "ETS,2026-fall,20261116,20261130\n",
}


def make_zip(path: Path, files: dict[str, str] = FILES) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path


@pytest.fixture
def gtfs(pg):
    pg.execute("DROP SCHEMA IF EXISTS gtfs CASCADE")
    load.db.apply_schema(pg, load.SCHEMA_PATH)
    return pg


def test_parse_gtfs_time():
    assert load.parse_gtfs_time("08:07:30") == 29250
    assert load.parse_gtfs_time("9:15:00") == 33300
    assert load.parse_gtfs_time("25:01:00") == 90060
    with pytest.raises(ValueError):
        load.parse_gtfs_time("8:7")


def test_load_feed_counts_and_types(gtfs, tmp_path):
    feed_id, loads = load.load_feed(gtfs, make_zip(tmp_path / "gtfs_20261120_abc.zip"))
    counts = {t.table: t.rows for t in loads}
    assert counts == {"routes": 1, "trips": 3, "stops": 2, "stop_times": 5,
                      "calendar": 2, "calendar_dates": 3, "shapes": 2}
    assert next(t for t in loads if t.table == "routes").ignored_columns == ["network_id"]
    assert gtfs.execute("SELECT route_id FROM gtfs.routes").fetchone()[0] == "4"

    rows = gtfs.execute(
        "SELECT trip_id, stop_sequence, arrival_s, departure_s, timepoint FROM gtfs.stop_times ORDER BY trip_id, stop_sequence"
    ).fetchall()
    assert rows == [("T1", 1, 28800, 28800, 1), ("T1", 2, 29250, 29280, None),
                    ("T2", 1, 89400, 89400, 1), ("T2", 2, None, None, 0), ("T3", 1, 33300, 33300, 1)]

    feed = gtfs.execute("SELECT feed_version, feed_start_date, fetched_at FROM gtfs.feeds").fetchone()
    assert feed == ("2026-fall", date(2026, 11, 16), datetime(2026, 11, 20, tzinfo=timezone.utc))


def test_reload_same_zip_is_skipped(gtfs, tmp_path):
    path = make_zip(tmp_path / "gtfs.zip")
    assert load.load_feed(gtfs, path) is not None
    assert load.load_feed(gtfs, path) is None
    assert gtfs.execute("SELECT count(*) FROM gtfs.trips").fetchone()[0] == 3


def test_service_days_apply_exceptions(gtfs, tmp_path):
    load.load_feed(gtfs, make_zip(tmp_path / "gtfs.zip"))

    def services(d):
        return {r[0] for r in gtfs.execute(
            "SELECT service_id FROM gtfs.service_days WHERE service_date = %s", (d,))}

    assert services(date(2026, 11, 19)) == {"WKDY"}
    assert services(date(2026, 11, 20)) == {"SAT"}
    assert services(date(2026, 11, 21)) == {"SAT"}
    assert services(date(2026, 11, 22)) == set()
    assert services(date(2026, 11, 11)) == {"WKDY"}


def test_service_ts_handles_after_midnight_and_dst(gtfs):
    def ts(d, s):
        return gtfs.execute("SELECT gtfs.service_ts(%s, %s)", (d, s)).fetchone()[0]

    assert ts(date(2026, 11, 20), 8 * 3600) == datetime(2026, 11, 20, 8, 0, tzinfo=EDMONTON)
    assert ts(date(2026, 11, 20), 89400) == datetime(2026, 11, 21, 0, 50, tzinfo=EDMONTON)
    assert ts(date(2026, 3, 8), 8 * 3600) == datetime(2026, 3, 8, 8, 0, tzinfo=EDMONTON)


def test_missing_required_file_rejected_without_partial_load(gtfs, tmp_path):
    files = {k: v for k, v in FILES.items() if k != "stop_times.txt"}
    with pytest.raises(load.FeedError, match="stop_times.txt"):
        load.load_feed(gtfs, make_zip(tmp_path / "bad.zip", files))
    assert gtfs.execute("SELECT count(*) FROM gtfs.feeds").fetchone()[0] == 0


def test_bad_value_rolls_back_whole_feed(gtfs, tmp_path):
    files = dict(FILES)
    files["stop_times.txt"] += "T3,9:99:00,9:99:00,S2,2,1\n"
    with pytest.raises(load.FeedError, match="line 7"):
        load.load_feed(gtfs, make_zip(tmp_path / "bad.zip", files))
    assert gtfs.execute("SELECT count(*) FROM gtfs.feeds").fetchone()[0] == 0
    assert gtfs.execute("SELECT count(*) FROM gtfs.routes").fetchone()[0] == 0


def test_timezone_check_agrees_with_python(gtfs, capsys):
    assert load.timezone_check(gtfs)
    assert "MISMATCH" not in capsys.readouterr().out


def test_pinned_time_zone_matches_alberta_rules():
    assert datetime(2026, 3, 1, 12, tzinfo=EDMONTON).utcoffset().total_seconds() == -7 * 3600
    assert datetime(2026, 11, 20, 12, tzinfo=EDMONTON).utcoffset().total_seconds() == -6 * 3600
    assert datetime(2027, 1, 15, 12, tzinfo=EDMONTON).utcoffset().total_seconds() == -6 * 3600
