"""Synthetic GTFS Realtime builders and PostgreSQL fixture."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2 as pb

from collector import db

HEADER_TS = 1_795_000_000
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def new_feed(header_ts: int = HEADER_TS) -> pb.FeedMessage:
    msg = pb.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = header_ts
    return msg


def add_vehicle(msg: pb.FeedMessage, vehicle_id: str, ts: int, trip_id: str = "T1",
                route_id: str = "4", lat: float = 53.5461, lon: float = -113.4938,
                stop_seq: int | None = 5, status: int | None = None) -> None:
    e = msg.entity.add()
    e.id = f"v-{vehicle_id}"
    v = e.vehicle
    v.vehicle.id = vehicle_id
    v.timestamp = ts
    v.trip.trip_id = trip_id
    v.trip.route_id = route_id
    v.trip.start_date = "20261120"
    v.position.latitude = lat
    v.position.longitude = lon
    if stop_seq is not None:
        v.current_stop_sequence = stop_seq
    if status is not None:
        v.current_status = status


def add_trip_update(msg: pb.FeedMessage, trip_id: str, stops: list[tuple[int, str, int, int]],
                    canceled: bool = False) -> None:
    e = msg.entity.add()
    e.id = f"tu-{trip_id}"
    tu = e.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.route_id = "4"
    tu.trip.start_date = "20261120"
    if canceled:
        tu.trip.schedule_relationship = pb.TripDescriptor.CANCELED
    tu.vehicle.id = "4501"
    for seq, stop_id, arr_time, delay in stops:
        stu = tu.stop_time_update.add()
        stu.stop_sequence = seq
        stu.stop_id = stop_id
        stu.arrival.time = arr_time
        stu.arrival.delay = delay


def add_alert(msg: pb.FeedMessage, alert_id: str, text: str, routes: list[str]) -> None:
    e = msg.entity.add()
    e.id = alert_id
    a = e.alert
    a.effect = pb.Alert.DETOUR
    a.cause = pb.Alert.CONSTRUCTION
    tr = a.header_text.translation.add()
    tr.text = text
    tr.language = "en"
    for r in routes:
        a.informed_entity.add().route_id = r
    period = a.active_period.add()
    period.start = HEADER_TS - 3600
    period.end = HEADER_TS + 3600


@pytest.fixture
def pg():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = db.connect(dsn)
    conn.execute("DROP SCHEMA IF EXISTS rt CASCADE")
    db.apply_schema(conn, SCHEMA_PATH)
    yield conn
    conn.close()
