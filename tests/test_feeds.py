"""Tests for feed parsing and row flattening."""

from __future__ import annotations

from datetime import date, datetime, timezone

from google.transit import gtfs_realtime_pb2 as pb

from collector import feeds
from tests.conftest import HEADER_TS, add_alert, add_trip_update, add_vehicle, new_feed


def test_service_date_parsing():
    assert feeds.service_date("20261120") == date(2026, 11, 20)
    assert feeds.service_date("") is None
    assert feeds.service_date("2026-11-20") is None
    assert feeds.service_date("20261340") is None


def test_to_ts_is_utc_and_handles_zero():
    assert feeds.to_ts(0) is None
    assert feeds.to_ts(HEADER_TS) == datetime.fromtimestamp(HEADER_TS, tz=timezone.utc)


def test_vehicle_rows_fields():
    msg = new_feed()
    add_vehicle(msg, "4501", HEADER_TS - 10, status=pb.VehiclePosition.STOPPED_AT)
    (row,) = feeds.vehicle_rows(msg)
    assert row["vehicle_id"] == "4501"
    assert row["route_id"] == "4"
    assert row["start_date"] == date(2026, 11, 20)
    assert row["current_status"] == "STOPPED_AT"
    assert row["current_stop_sequence"] == 5
    assert abs(row["latitude"] - 53.5461) < 1e-4
    assert row["vehicle_ts"] == feeds.to_ts(HEADER_TS - 10)


def test_vehicle_rows_unset_optionals_are_none():
    msg = new_feed()
    add_vehicle(msg, "4502", HEADER_TS, stop_seq=None)
    (row,) = feeds.vehicle_rows(msg)
    assert row["current_stop_sequence"] is None
    assert row["current_status"] is None
    assert row["bearing"] is None
    assert row["direction_id"] is None


def test_trip_update_keeps_last_passed_and_next_stop():
    msg = new_feed()
    add_trip_update(msg, "T1", [
        (1, "S1", HEADER_TS - 300, 60),
        (2, "S2", HEADER_TS - 60, 90),
        (3, "S3", HEADER_TS + 120, 120),
        (4, "S4", HEADER_TS + 400, 150),
    ])
    rows = feeds.trip_update_rows(msg, upcoming_stops=1)
    assert [(r["stop_id"], r["is_past"]) for r in rows] == [("S2", True), ("S3", False)]
    assert rows[1]["arrival_delay_s"] == 120
    assert all(r["n_stop_updates"] == 4 for r in rows)


def test_trip_update_upcoming_stops_setting():
    msg = new_feed()
    add_trip_update(msg, "T1", [(s, f"S{s}", HEADER_TS + 60 * s, 0) for s in range(1, 6)])
    rows = feeds.trip_update_rows(msg, upcoming_stops=3)
    assert [r["stop_sequence"] for r in rows] == [1, 2, 3]
    assert not any(r["is_past"] for r in rows)


def test_canceled_trip_without_stops_still_recorded():
    msg = new_feed()
    add_trip_update(msg, "T9", [], canceled=True)
    (row,) = feeds.trip_update_rows(msg)
    assert row["trip_schedule_relationship"] == "CANCELED"
    assert row["stop_id"] is None
    assert row["n_stop_updates"] == 0


def test_alert_rows_and_stable_hash():
    msg = new_feed()
    add_alert(msg, "A1", "Route 4 detour", ["4", "8", "4"])
    (row,) = feeds.alert_rows(msg)
    assert row["effect"] == "DETOUR"
    assert row["cause"] == "CONSTRUCTION"
    assert row["route_ids"] == ["4", "8"]
    assert row["header_text"] == "Route 4 detour"
    again = new_feed(HEADER_TS + 30)
    add_alert(again, "A1", "Route 4 detour", ["4", "8", "4"])
    assert feeds.alert_rows(again)[0]["content_hash"] == row["content_hash"]


def test_parse_roundtrip():
    msg = new_feed()
    add_vehicle(msg, "4501", HEADER_TS)
    assert len(feeds.parse(msg.SerializeToString()).entity) == 1
