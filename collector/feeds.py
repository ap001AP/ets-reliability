"""GTFS Realtime fetching and row flattening."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests
from google.transit import gtfs_realtime_pb2 as pb


@dataclass
class FetchResult:
    feed: str
    polled_at: datetime
    http_status: int | None
    latency_ms: int | None
    content: bytes | None
    error: str | None


def fetch(session: requests.Session, feed: str, url: str, timeout: float = 15.0) -> FetchResult:
    polled_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        latency = int((time.perf_counter() - t0) * 1000)
        return FetchResult(feed, polled_at, None, latency, None, f"{type(exc).__name__}: {exc}")
    latency = int((time.perf_counter() - t0) * 1000)
    if resp.status_code != 200:
        return FetchResult(feed, polled_at, resp.status_code, latency, None, f"HTTP {resp.status_code}")
    return FetchResult(feed, polled_at, 200, latency, resp.content, None)


def parse(content: bytes) -> pb.FeedMessage:
    msg = pb.FeedMessage()
    msg.ParseFromString(content)
    return msg


def to_ts(epoch: int | None) -> datetime | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def service_date(value: str) -> date | None:
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        return None


def _opt(msg, field: str):
    return getattr(msg, field) if msg.HasField(field) else None


def _enum(enum_type, msg, field: str) -> str | None:
    return enum_type.Name(getattr(msg, field)) if msg.HasField(field) else None


def _trip_fields(trip: pb.TripDescriptor) -> dict:
    return {
        "trip_id": trip.trip_id or None,
        "route_id": trip.route_id or None,
        "direction_id": _opt(trip, "direction_id"),
        "start_date": service_date(trip.start_date),
        "start_time": trip.start_time or None,
        "trip_schedule_relationship": _enum(pb.TripDescriptor.ScheduleRelationship, trip, "schedule_relationship"),
    }


def vehicle_rows(msg: pb.FeedMessage) -> list[dict]:
    feed_ts = to_ts(msg.header.timestamp)
    rows = []
    for entity in msg.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        pos = v.position if v.HasField("position") else None
        rows.append({
            "feed_ts": feed_ts,
            "vehicle_ts": to_ts(_opt(v, "timestamp")),
            "vehicle_id": v.vehicle.id or v.vehicle.label or entity.id,
            "vehicle_label": v.vehicle.label or None,
            **_trip_fields(v.trip),
            "latitude": pos.latitude if pos else None,
            "longitude": pos.longitude if pos else None,
            "bearing": _opt(pos, "bearing") if pos else None,
            "speed_mps": _opt(pos, "speed") if pos else None,
            "current_stop_sequence": _opt(v, "current_stop_sequence"),
            "stop_id": v.stop_id or None,
            "current_status": _enum(pb.VehiclePosition.VehicleStopStatus, v, "current_status"),
            "occupancy_status": _enum(pb.VehiclePosition.OccupancyStatus, v, "occupancy_status"),
        })
    return rows


def _event_time(stu: pb.TripUpdate.StopTimeUpdate) -> int | None:
    if stu.HasField("arrival") and stu.arrival.HasField("time"):
        return stu.arrival.time
    if stu.HasField("departure") and stu.departure.HasField("time"):
        return stu.departure.time
    return None


_EMPTY_STOP = {
    "is_past": None,
    "stop_sequence": None,
    "stop_id": None,
    "arrival_ts": None,
    "arrival_delay_s": None,
    "departure_ts": None,
    "departure_delay_s": None,
    "stop_schedule_relationship": None,
}


def _stop_fields(stu: pb.TripUpdate.StopTimeUpdate, is_past: bool) -> dict:
    arr = stu.arrival if stu.HasField("arrival") else None
    dep = stu.departure if stu.HasField("departure") else None
    return {
        "is_past": is_past,
        "stop_sequence": _opt(stu, "stop_sequence"),
        "stop_id": stu.stop_id or None,
        "arrival_ts": to_ts(_opt(arr, "time")) if arr else None,
        "arrival_delay_s": _opt(arr, "delay") if arr else None,
        "departure_ts": to_ts(_opt(dep, "time")) if dep else None,
        "departure_delay_s": _opt(dep, "delay") if dep else None,
        "stop_schedule_relationship": _enum(pb.TripUpdate.StopTimeUpdate.ScheduleRelationship, stu, "schedule_relationship"),
    }


def trip_update_rows(msg: pb.FeedMessage, upcoming_stops: int = 1, now_epoch: int | None = None) -> list[dict]:
    feed_ts = to_ts(msg.header.timestamp)
    ref = msg.header.timestamp or now_epoch or int(time.time())
    rows = []
    for entity in msg.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        stus = list(tu.stop_time_update)
        base = {
            "feed_ts": feed_ts,
            "update_ts": to_ts(_opt(tu, "timestamp")),
            **_trip_fields(tu.trip),
            "vehicle_id": (tu.vehicle.id or tu.vehicle.label or None) if tu.HasField("vehicle") else None,
            "trip_delay_s": _opt(tu, "delay"),
            "n_stop_updates": len(stus),
        }
        if not stus:
            rows.append({**base, **_EMPTY_STOP})
            continue
        passed = [(t := _event_time(s)) is not None and t < ref for s in stus]
        past = [s for s, p in zip(stus, passed) if p]
        future = [s for s, p in zip(stus, passed) if not p]
        selected = ([(past[-1], True)] if past else []) + [(s, False) for s in future[:upcoming_stops]]
        for stu, is_past in selected:
            rows.append({**base, **_stop_fields(stu, is_past)})
    return rows


def _text(translated: pb.TranslatedString) -> str | None:
    if not translated.translation:
        return None
    for tr in translated.translation:
        if tr.language.lower().startswith("en"):
            return tr.text
    return translated.translation[0].text


def alert_rows(msg: pb.FeedMessage) -> list[dict]:
    rows = []
    for entity in msg.entity:
        if not entity.HasField("alert"):
            continue
        a = entity.alert
        starts = [p.start for p in a.active_period if p.HasField("start")]
        ends = [p.end for p in a.active_period if p.HasField("end")]
        rows.append({
            "entity_id": entity.id,
            "content_hash": hashlib.sha1(a.SerializeToString(deterministic=True)).hexdigest(),
            "cause": _enum(pb.Alert.Cause, a, "cause"),
            "effect": _enum(pb.Alert.Effect, a, "effect"),
            "header_text": _text(a.header_text),
            "description_text": _text(a.description_text),
            "route_ids": sorted({ie.route_id for ie in a.informed_entity if ie.route_id}) or None,
            "stop_ids": sorted({ie.stop_id for ie in a.informed_entity if ie.stop_id}) or None,
            "active_start": to_ts(min(starts)) if starts else None,
            "active_end": to_ts(max(ends)) if ends else None,
        })
    return rows
