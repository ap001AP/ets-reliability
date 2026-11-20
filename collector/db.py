"""PostgreSQL writes for the realtime collector."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import psycopg

from collector.feeds import FetchResult

VP_COLUMNS = [
    "feed_ts", "vehicle_ts", "vehicle_id", "vehicle_label", "trip_id", "route_id",
    "direction_id", "start_date", "start_time", "trip_schedule_relationship",
    "latitude", "longitude", "bearing", "speed_mps", "current_stop_sequence",
    "stop_id", "current_status", "occupancy_status",
]

TU_COLUMNS = [
    "feed_ts", "update_ts", "trip_id", "route_id", "direction_id", "start_date",
    "start_time", "trip_schedule_relationship", "vehicle_id", "trip_delay_s",
    "n_stop_updates", "is_past", "stop_sequence", "stop_id", "arrival_ts",
    "arrival_delay_s", "departure_ts", "departure_delay_s", "stop_schedule_relationship",
]

ALERT_COLUMNS = [
    "entity_id", "content_hash", "cause", "effect", "header_text", "description_text",
    "route_ids", "stop_ids", "active_start", "active_end",
]


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def apply_schema(conn: psycopg.Connection, schema_path: Path) -> None:
    conn.execute(schema_path.read_text())


def log_poll(cur: psycopg.Cursor, result: FetchResult, header_ts: datetime | None,
             entity_count: int | None, is_stale: bool, error: str | None) -> int:
    cur.execute(
        """
        INSERT INTO rt.poll_log
            (feed, polled_at, http_status, latency_ms, bytes, header_ts, entity_count, is_stale, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING poll_id
        """,
        (result.feed, result.polled_at, result.http_status, result.latency_ms,
         len(result.content) if result.content is not None else None,
         header_ts, entity_count, is_stale, error),
    )
    return cur.fetchone()[0]


def set_rows_written(cur: psycopg.Cursor, poll_id: int, n: int) -> None:
    cur.execute("UPDATE rt.poll_log SET rows_written = %s WHERE poll_id = %s", (n, poll_id))


def _insert_many(cur: psycopg.Cursor, table: str, columns: list[str], poll_id: int,
                 rows: list[dict], conflict: str = "") -> int:
    if not rows:
        return 0
    cols = ["poll_id", *columns]
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) {conflict}"
    cur.executemany(sql, [(poll_id, *(r[c] for c in columns)) for r in rows])
    return max(cur.rowcount, 0)


def insert_vehicle_positions(cur: psycopg.Cursor, poll_id: int, rows: list[dict], polled_at: datetime) -> int:
    return _insert_many(cur, "rt.vehicle_positions", VP_COLUMNS, poll_id, rows,
                        "ON CONFLICT (vehicle_id, vehicle_ts) DO NOTHING")


def insert_trip_updates(cur: psycopg.Cursor, poll_id: int, rows: list[dict], polled_at: datetime) -> int:
    return _insert_many(cur, "rt.trip_updates", TU_COLUMNS, poll_id, rows,
                        "ON CONFLICT (start_date, trip_id, stop_sequence, vehicle_id, arrival_ts, departure_ts, "
                        "trip_schedule_relationship, stop_schedule_relationship) DO NOTHING")


def upsert_alerts(cur: psycopg.Cursor, poll_id: int, rows: list[dict], polled_at: datetime) -> int:
    if not rows:
        return 0
    cols = [*ALERT_COLUMNS, "first_seen", "last_seen"]
    placeholders = ", ".join(["%s"] * len(cols))
    cur.executemany(
        f"""
        INSERT INTO rt.alerts ({', '.join(cols)}) VALUES ({placeholders})
        ON CONFLICT (entity_id, content_hash) DO UPDATE SET last_seen = EXCLUDED.last_seen
        """,
        [(*(r[c] for c in ALERT_COLUMNS), polled_at, polled_at) for r in rows],
    )
    return len(rows)


def static_snapshot_exists(cur: psycopg.Cursor, sha256: str) -> bool:
    cur.execute("SELECT 1 FROM rt.static_snapshots WHERE sha256 = %s", (sha256,))
    return cur.fetchone() is not None


def record_static_snapshot(cur: psycopg.Cursor, sha256: str, fetched_at: datetime,
                           size: int, path: str) -> None:
    cur.execute(
        "INSERT INTO rt.static_snapshots (sha256, fetched_at, bytes, path) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (sha256) DO NOTHING",
        (sha256, fetched_at, size, path),
    )


WRITERS = {
    "vehicle_positions": insert_vehicle_positions,
    "trip_updates": insert_trip_updates,
    "alerts": upsert_alerts,
}
