"""Build stop passages by matching GPS pings to route shapes, and report validation checks."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from analysis import shape_match
from collector import db
from collector.config import Config

log = logging.getLogger("analysis")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "sql" / "analysis_schema.sql"
EDMONTON = ZoneInfo("America/Edmonton")


def _print_rows(title: str, cols: list[str], rows: list[tuple]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  (no rows)")
        return
    widths = [max(len(str(c)), *(len(_fmt(r[i])) for r in rows)) for i, c in enumerate(cols)]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  " + "  ".join(_fmt(v).ljust(w) for v, w in zip(r, widths)))


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return "" if v is None else str(v)


def check(conn: psycopg.Connection, date_from: date, date_to: date) -> None:
    p = {"date_from": date_from, "date_to": date_to}

    _print_rows(
        "Vehicle stop_sequence vs schedule (does the ping's stop_id match the scheduled stop at that sequence?)",
        ["pings", "matched", "pct"],
        conn.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE st.stop_id = v.stop_id),
                   round(100.0 * count(*) FILTER (WHERE st.stop_id = v.stop_id) / nullif(count(*), 0), 1)::float8
            FROM rt.vehicle_positions v
            JOIN analysis.feed_by_date f ON f.service_date = v.start_date
            JOIN gtfs.stop_times st ON st.feed_id = f.feed_id AND st.trip_id = v.trip_id
                                   AND st.stop_sequence = v.current_stop_sequence
            WHERE v.start_date BETWEEN %(date_from)s AND %(date_to)s AND v.stop_id IS NOT NULL
            """, p).fetchall(),
    )

    _print_rows(
        "Stop passages by service date",
        ["service_date", "passages", "trips", "median_window_s", "pct_window_le_120", "pct_scheduled"],
        conn.execute(
            """
            SELECT service_date, count(*), count(DISTINCT trip_id),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY window_s),
                   round(100.0 * avg((window_s <= 120)::int), 1)::float8,
                   round(100.0 * avg((scheduled_ts IS NOT NULL)::int), 1)::float8
            FROM analysis.stop_passages
            WHERE service_date BETWEEN %(date_from)s AND %(date_to)s
            GROUP BY service_date ORDER BY service_date
            """, p).fetchall(),
    )

    _print_rows(
        "Cross-check against ETS trip-update departures for stops already passed (seconds, ours minus ETS)",
        ["compared", "median_diff_s", "p10_diff_s", "p90_diff_s", "pct_within_60s"],
        conn.execute(
            """
            WITH ets AS (
                SELECT DISTINCT ON (start_date, trip_id, stop_sequence)
                       start_date, trip_id, stop_sequence, departure_ts
                FROM rt.trip_updates
                WHERE is_past AND departure_ts IS NOT NULL
                  AND start_date BETWEEN %(date_from)s AND %(date_to)s
                ORDER BY start_date, trip_id, stop_sequence, feed_ts DESC
            ),
            diffs AS (
                SELECT extract(epoch FROM s.observed_ts - e.departure_ts) AS d
                FROM analysis.stop_events s
                JOIN ets e ON e.start_date = s.service_date AND e.trip_id = s.trip_id
                          AND e.stop_sequence = s.stop_sequence
            )
            SELECT count(*),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY d),
                   percentile_cont(0.1) WITHIN GROUP (ORDER BY d),
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY d),
                   round(100.0 * avg((abs(d) <= 60)::int), 1)::float8
            FROM diffs
            """, p).fetchall(),
    )

    _print_rows(
        "Network on-time performance (1 min early to 5 min late, crossing window <= 120 s)",
        ["events", "pct_on_time", "pct_early", "pct_late", "median_delay_s", "pct_timepoint_events"],
        conn.execute(
            """
            SELECT count(*),
                   round(100.0 * avg((status = 'on_time')::int), 1)::float8,
                   round(100.0 * avg((status = 'early')::int), 1)::float8,
                   round(100.0 * avg((status = 'late')::int), 1)::float8,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY delay_s),
                   round(100.0 * avg((timepoint = 1)::int), 1)::float8
            FROM analysis.stop_events
            WHERE status IN ('on_time', 'early', 'late')
              AND service_date BETWEEN %(date_from)s AND %(date_to)s
            """, p).fetchall(),
    )

    route_cols = ["route", "name", "events", "pct_on_time", "pct_late", "median_delay_s"]
    route_sql = """
        SELECT route_short_name, left(route_long_name, 40), events, pct_on_time::float8, pct_late::float8, median_delay_s
        FROM analysis.otp_by_route WHERE events >= 200 ORDER BY pct_on_time {order} LIMIT 5
    """
    _print_rows("Least on-time routes (>= 200 events, all dates)", route_cols,
                conn.execute(route_sql.format(order="ASC")).fetchall())
    _print_rows("Most on-time routes (>= 200 events, all dates)", route_cols,
                conn.execute(route_sql.format(order="DESC")).fetchall())

    _print_rows(
        "Excluded events",
        ["status", "events"],
        conn.execute(
            """
            SELECT CASE WHEN window_s > 120 THEN 'crossing window > 120 s' ELSE 'other' END, count(*)
            FROM analysis.stop_passages
            WHERE window_s > 120 AND service_date BETWEEN %(date_from)s AND %(date_to)s
            GROUP BY 1
            UNION ALL
            SELECT status, count(*) FROM analysis.stop_events
            WHERE status IN ('unscheduled', 'invalid') AND service_date BETWEEN %(date_from)s AND %(date_to)s
            GROUP BY status
            """, p).fetchall(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build stop passages and on-time views")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, help="first service date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, help="last service date (YYYY-MM-DD)")
    parser.add_argument("--check", action="store_true", help="print validation checks after building")
    parser.add_argument("--check-only", action="store_true", help="print checks without rebuilding")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()

    with db.connect(cfg.database_url) as conn:
        db.apply_schema(conn, SCHEMA_PATH)
        span = conn.execute("SELECT min(start_date), max(start_date) FROM rt.vehicle_positions").fetchone()
        date_from = args.date_from or span[0]
        date_to = args.date_to or span[1] or datetime.now(EDMONTON).date()
        if date_from is None:
            parser.error("no vehicle positions collected yet")

        if not args.check_only:
            day = date_from
            while day <= date_to:
                t0 = time.perf_counter()
                st = shape_match.build_day(conn, day)
                log.info(
                    "%s: %s passages from %s/%s trips, %.1f%% of %s pings on route, %s stops >100 m from shape (%.1f s)",
                    day, f"{st.passages:,}", f"{st.trips_matched:,}", f"{st.trips_seen:,}",
                    100 * st.pings_on_route / st.pings if st.pings else 0, f"{st.pings:,}",
                    st.stops_far_from_shape, time.perf_counter() - t0,
                )
                day += timedelta(days=1)

        if args.check or args.check_only:
            check(conn, date_from, date_to)


if __name__ == "__main__":
    main()
