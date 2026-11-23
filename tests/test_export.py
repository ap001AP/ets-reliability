"""Tests for the Tableau CSV extracts and generated data dictionary."""

from __future__ import annotations

import csv

import pytest

from analysis import build, export, shape_match
from tests.test_stop_passages import DAY, world  # noqa: F401


@pytest.fixture
def exported(world, tmp_path):
    shape_match.build_day(world, DAY)
    build.build_headways(world, DAY)
    build.build_trip_status(world, DAY)
    results = export.export(world, tmp_path, DAY, DAY)
    return tmp_path, {ex.name: (header, rows) for ex, header, rows in results}


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_every_extract_written(exported):
    out, results = exported
    for ex in export.EXTRACTS:
        assert (out / ex.name).exists(), ex.name
        assert results[ex.name][0], f"{ex.name} has no header"


def test_every_column_documented(exported):
    _, results = exported
    missing = {name: [c for c in header if c not in export.COLUMN_DOCS] for name, (header, _) in results.items()}
    assert {k: v for k, v in missing.items() if v} == {}


def test_network_daily_counts_match_stop_events(exported, world):
    out, _ = exported
    (row,) = read(out / "network_daily.csv")
    events = world.execute(
        "SELECT count(*) FROM analysis.stop_events WHERE status IN ('on_time', 'early', 'late')").fetchone()[0]
    assert int(row["events"]) == events
    assert int(row["n_on_time"]) + int(row["n_early"]) + int(row["n_late"]) == events
    assert row["day_type"] == "Weekday" and row["day_name"] == "Fri"


def test_routes_and_stop_route_have_coordinates(exported):
    out, _ = exported
    (route,) = read(out / "routes.csv")
    assert route["route_short_name"] == "4" and route["route_kind"] == "Regular"
    stops = read(out / "stop_route.csv")
    assert stops and all(s["stop_lat"] and s["stop_lon"] for s in stops)
    assert [int(s["stop_order"]) for s in stops] == sorted(int(s["stop_order"]) for s in stops)


def test_route_shapes_are_ordered_paths(exported):
    out, _ = exported
    pts = read(out / "route_shapes.csv")
    assert len(pts) == 3
    assert [int(p["point_order"]) for p in pts] == [1, 2, 3]


def test_trips_cover_every_scheduled_trip(exported, world):
    out, _ = exported
    trips = read(out / "trips.csv")
    assert len(trips) == world.execute("SELECT count(*) FROM analysis.trip_status").fetchone()[0]
    assert {t["status"] for t in trips} <= {"ran", "partial", "cancelled", "tracked_not_matched", "missing",
                                             "not_in_feed", "no_data"}


def test_dictionary_lists_files_and_rows(exported):
    out, results = exported
    text = (out / "README.md").read_text()
    for name, (_, rows) in results.items():
        assert f"## {name}" in text
        assert f"{rows:,} rows." in text
    assert "SUM(n_on_time) / SUM(events)" in text


def test_all_headway_stops_option_runs(world, tmp_path):
    shape_match.build_day(world, DAY)
    build.build_headways(world, DAY)
    build.build_trip_status(world, DAY)
    results = export.export(world, tmp_path, DAY, DAY, all_headway_stops=True)
    assert any(ex.name == "headways.csv" for ex, _, _ in results)


def test_network_summary_matches_daily_totals(exported):
    out, _ = exported
    (summary,) = read(out / "network_summary.csv")
    daily = read(out / "network_daily.csv")
    assert int(summary["events"]) == sum(int(d["events"]) for d in daily)
    assert int(summary["trips_judged"]) == sum(int(d["trips_judged"]) for d in daily)
    assert summary["date_from"] == str(DAY) == summary["date_to"]


def test_map_layers_hold_lines_and_stops(exported):
    out, _ = exported
    rows = read(out / "map_layers.csv")
    shapes = [r for r in rows if r["layer"] == "shape"]
    stops = [r for r in rows if r["layer"] == "stop"]
    assert len(shapes) == 3 and all(r["point_order"] and not r["stop_id"] for r in shapes)
    assert stops and all(r["stop_id"] and r["events"] and not r["point_order"] for r in stops)
    assert all(r["lat"] and r["lon"] for r in rows)


def test_story_passages_limited_to_story_routes(world, tmp_path):
    shape_match.build_day(world, DAY)
    build.build_headways(world, DAY)
    build.build_trip_status(world, DAY)
    export.export(world, tmp_path / "none", DAY, DAY)
    assert read(tmp_path / "none" / "story_passages.csv") == []
    export.export(world, tmp_path / "four", DAY, DAY, story_routes=("004",))
    rows = read(tmp_path / "four" / "story_passages.csv")
    assert rows and {r["route_short_name"] for r in rows} == {"4"}
    assert {r["event"] for r in rows} <= {"departure", "arrival"}
    assert all(r["scheduled_local"] and r["observed_local"] for r in rows)


def test_agencies_carry_counts(exported):
    out, _ = exported
    (row,) = read(out / "agencies.csv")
    assert row["is_ets"] == "yes"
    assert round(100 * int(row["n_on_time"]) / int(row["events"]), 1) == float(row["pct_on_time"])
