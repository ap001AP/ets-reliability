"""Build stop passages by matching GPS pings to route shapes, and report validation checks."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

from analysis import shape_match
from collector import db
from collector.config import Config
from collector.tz import EDMONTON

log = logging.getLogger("analysis")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "sql" / "analysis_schema.sql"
GTFS_SCHEMA_PATH = ROOT / "sql" / "gtfs_schema.sql"
HEADWAYS_PATH = ROOT / "sql" / "headways.sql"
TRIP_STATUS_PATH = ROOT / "sql" / "trip_status.sql"


def build_headways(conn: psycopg.Connection, day: date) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM analysis.headways WHERE schedule_date = %s", (day,))
        cur.execute("SELECT 1 FROM analysis.stop_passages WHERE schedule_date = %s LIMIT 1", (day,))
        if cur.fetchone() is None:
            return 0
        cur.execute("SET LOCAL work_mem = '256MB'")
        cur.execute(HEADWAYS_PATH.read_text(), {"d": day})
        n = cur.rowcount
        cur.execute("ANALYZE analysis.headways")
        return n


def sql_steps(sql: str) -> list[tuple[str, str]]:
    steps = []
    for block in sql.split("-- step: ")[1:]:
        name, _, body = block.partition("\n")
        steps.append((name.strip(), body.strip().rstrip(";")))
    return steps


def build_trip_status(conn: psycopg.Connection, day: date) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM analysis.trip_status WHERE schedule_date = %s", (day,))
        cur.execute("SELECT 1 FROM analysis.stop_passages WHERE schedule_date = %s LIMIT 1", (day,))
        if cur.fetchone() is None:
            return 0
        cur.execute("SET LOCAL work_mem = '256MB'")
        n = 0
        for name, sql in sql_steps(TRIP_STATUS_PATH.read_text()):
            t0 = time.perf_counter()
            cur.execute(sql, {"d": day} if "%(d)s" in sql else None)
            n = cur.rowcount
            log.info("%s: trip status step '%s' %.1f s", day, name, time.perf_counter() - t0)
        cur.execute("ANALYZE analysis.trip_status")
        return n


CROSS_CHECK_BREAKDOWN_SQL = """
WITH ets AS (
    SELECT DISTINCT ON (start_date, trip_id, stop_sequence) start_date, trip_id, stop_sequence, departure_ts
    FROM rt.trip_updates
    WHERE is_past AND departure_ts IS NOT NULL AND start_date BETWEEN %(date_from)s AND %(date_to)s
    ORDER BY start_date, trip_id, stop_sequence, feed_ts DESC
),
trips AS (
    SELECT DISTINCT feed_id, trip_id FROM analysis.stop_passages
    WHERE service_date BETWEEN %(date_from)s AND %(date_to)s
),
firsts AS (
    SELECT st.feed_id, st.trip_id, min(st.stop_sequence) AS first_seq
    FROM gtfs.stop_times st JOIN trips t ON t.feed_id = st.feed_id AND t.trip_id = st.trip_id
    GROUP BY st.feed_id, st.trip_id
),
d AS (
    SELECT extract(epoch FROM s.observed_ts - e.departure_ts) AS diff,
           CASE WHEN s.stop_sequence = f.first_seq THEN 'first stop' ELSE 'other stops' END AS position,
           CASE WHEN s.timepoint = 1 THEN 'timepoint' ELSE 'not a timepoint' END AS tp,
           CASE WHEN r.route_short_name LIKE '%%X' THEN 'express' ELSE 'regular' END AS kind,
           CASE WHEN s.iso_dow <= 5 AND (s.local_hour BETWEEN 6 AND 8 OR s.local_hour BETWEEN 15 AND 17)
                THEN 'weekday peak' ELSE 'other times' END AS period
    FROM analysis.stop_events s
    JOIN ets e ON e.start_date = s.service_date AND e.trip_id = s.trip_id AND e.stop_sequence = s.stop_sequence
    JOIN firsts f ON f.feed_id = s.feed_id AND f.trip_id = s.trip_id
    JOIN gtfs.routes r ON r.feed_id = s.feed_id AND r.route_id = s.route_id
    WHERE s.service_date BETWEEN %(date_from)s AND %(date_to)s
)
SELECT dim, grp, count(*),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY diff),
       percentile_cont(0.1) WITHIN GROUP (ORDER BY diff),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY diff),
       round(100.0 * avg((abs(diff) <= 60)::int), 1)::float8
FROM (
    SELECT 'position' AS dim, position AS grp, diff FROM d
    UNION ALL SELECT 'timepoint', tp, diff FROM d
    UNION ALL SELECT 'route type', kind, diff FROM d
    UNION ALL SELECT 'period', period, diff FROM d
) x
GROUP BY dim, grp
ORDER BY dim, grp
"""

ETS_STANDARD_SQL = """
WITH e AS (
    SELECT * FROM analysis.ets_standard_events
    WHERE service_date BETWEEN %(date_from)s AND %(date_to)s AND is_ets
),
trips AS (
    SELECT DISTINCT feed_id, trip_id FROM e
),
firsts AS (
    SELECT st.feed_id, st.trip_id, min(st.stop_sequence) AS first_seq
    FROM gtfs.stop_times st JOIN trips t ON t.feed_id = st.feed_id AND t.trip_id = st.trip_id
    GROUP BY st.feed_id, st.trip_id
),
labelled AS (
    SELECT e.*,
           CASE WHEN e.event = 'arrival' THEN 'final-stop arrivals (-5 to +1 min)'
                WHEN e.stop_sequence = f.first_seq THEN 'first-stop departures (0 to +3 min)'
                ELSE 'other timepoint departures (0 to +3 min)' END AS measure,
           CASE WHEN e.event = 'arrival' THEN 4 WHEN e.stop_sequence = f.first_seq THEN 2 ELSE 3 END AS sort_key
    FROM e JOIN firsts f ON f.feed_id = e.feed_id AND f.trip_id = e.trip_id
),
grouped AS (
    SELECT measure, sort_key, ets_status, on_time_allowing_30s FROM labelled
    UNION ALL
    SELECT 'all timepoint departures (0 to +3 min)', 1, ets_status, on_time_allowing_30s
    FROM labelled WHERE event = 'departure'
)
SELECT measure, count(*),
       round(100.0 * avg((ets_status = 'on_time')::int), 1)::float8,
       round(100.0 * avg((ets_status = 'early')::int), 1)::float8,
       round(100.0 * avg((ets_status = 'late')::int), 1)::float8,
       round(100.0 * avg(on_time_allowing_30s::int), 1)::float8
FROM grouped
GROUP BY measure, sort_key
ORDER BY sort_key
"""

AGENCY_SUMMARY_SQL = """
WITH otp AS (
    SELECT coalesce(agency, '(unknown)') AS k, bool_or(is_ets) AS is_ets, count(*) AS events,
           round(100.0 * avg((status = 'on_time')::int), 1)::float8 AS pct_on_time
    FROM analysis.stop_events
    WHERE status IN ('on_time', 'early', 'late') AND service_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
bunch AS (
    SELECT coalesce(agency, '(unknown)') AS k, round(100.0 * avg((spacing = 'bunched')::int), 1)::float8 AS pct_bunched
    FROM analysis.headway_events
    WHERE actual_headway_s > 0 AND schedule_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
delivery AS (
    SELECT coalesce(ra.agency_name, ra.agency_id, '(unknown)') AS k,
           bool_or(coalesce(ra.is_ets, true)) AS is_ets,
           count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')) AS judged,
           round(100.0 * count(*) FILTER (WHERE s.status = 'missing')
                 / nullif(count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')), 0), 1)::float8 AS pct_missing
    FROM analysis.trip_status s
    JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
    LEFT JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = s.route_id
    WHERE s.schedule_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
keys AS (
    SELECT k FROM otp UNION SELECT k FROM delivery
)
SELECT keys.k, CASE WHEN coalesce(o.is_ets, d.is_ets) THEN 'yes' ELSE 'no' END,
       o.events, o.pct_on_time, b.pct_bunched, d.judged, d.pct_missing
FROM keys
LEFT JOIN otp o ON o.k = keys.k
LEFT JOIN delivery d ON d.k = keys.k
LEFT JOIN bunch b ON b.k = keys.k
ORDER BY o.events DESC NULLS LAST, keys.k
"""
AGENCY_SUMMARY_COLS = ["agency", "ets", "events", "pct_on_time", "pct_bunched", "trips_judged", "pct_missing"]

ETS_STANDARD_COLS = ["measure", "events", "pct_on_time", "pct_early", "pct_late", "pct_on_time_allowing_30s"]
CROSS_CHECK_COLS = ["split", "group", "compared", "median_diff_s", "p10_diff_s", "p90_diff_s", "pct_within_60s"]


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
        "Cross-check split by group (look for a group whose median is far from the others)",
        CROSS_CHECK_COLS, conn.execute(CROSS_CHECK_BREAKDOWN_SQL, p).fetchall(),
    )

    _print_rows(
        "By agency (the schedule and live feed include ETS's regional partners)",
        AGENCY_SUMMARY_COLS, conn.execute(AGENCY_SUMMARY_SQL, p).fetchall(),
    )

    _print_rows(
        "ETS on-time performance (1 min early to 5 min late, crossing window <= 120 s)",
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
            WHERE status IN ('on_time', 'early', 'late') AND is_ets
              AND service_date BETWEEN %(date_from)s AND %(date_to)s
            """, p).fetchall(),
    )

    _print_rows(
        "ETS 2009 standard, ETS routes (target 90%; strict, and allowing 30 s for measurement error)",
        ETS_STANDARD_COLS, conn.execute(ETS_STANDARD_SQL, p).fetchall(),
    )

    _print_rows(
        "Least on-time ETS routes by ETS standard (timepoint departures, >= 50, all dates)",
        ["route", "name", "departures", "pct_on_time", "pct_early", "pct_late", "pct_on_time_allowing_30s"],
        conn.execute(
            """
            SELECT route_short_name, left(route_long_name, 40), departures, pct_on_time::float8, pct_early::float8,
                   pct_late::float8, pct_on_time_allowing_30s::float8
            FROM analysis.ets_standard_by_route WHERE departures >= 50 AND is_ets ORDER BY pct_on_time LIMIT 5
            """).fetchall(),
    )

    route_cols = ["route", "name", "events", "pct_on_time", "pct_late", "median_delay_s"]
    route_sql = """
        SELECT route_short_name, left(route_long_name, 40), events, pct_on_time::float8, pct_late::float8, median_delay_s
        FROM analysis.otp_by_route WHERE events >= 200 AND is_ets ORDER BY pct_on_time {order} LIMIT 5
    """
    _print_rows("Least on-time ETS routes (>= 200 events, all dates)", route_cols,
                conn.execute(route_sql.format(order="ASC")).fetchall())
    _print_rows("Most on-time ETS routes (>= 200 events, all dates)", route_cols,
                conn.execute(route_sql.format(order="DESC")).fetchall())

    _print_rows(
        "Bunching on frequent ETS service (scheduled headway <= 15 min)",
        ["events", "pct_bunched", "pct_gap", "headway_cv", "excess_wait_s", "pct_pairs_dropped_for_data_gaps"],
        conn.execute(
            """
            WITH e AS (
                SELECT * FROM analysis.headway_events
                WHERE schedule_date BETWEEN %(date_from)s AND %(date_to)s AND actual_headway_s > 0 AND is_ets
            )
            SELECT count(*),
                   round(100.0 * avg((spacing = 'bunched')::int), 1)::float8,
                   round(100.0 * avg((spacing = 'gap')::int), 1)::float8,
                   round((stddev_samp(actual_headway_s - scheduled_headway_s) / nullif(avg(scheduled_headway_s), 0))::numeric, 2)::float8,
                   round((sum(actual_headway_s::float8 ^ 2) / (2 * sum(actual_headway_s))
                          - sum(scheduled_headway_s::float8 ^ 2) / (2 * sum(scheduled_headway_s)))::numeric)::float8,
                   (SELECT round(100.0 * avg(data_gap::int), 1)::float8 FROM analysis.headways
                    WHERE schedule_date BETWEEN %(date_from)s AND %(date_to)s AND scheduled_headway_s BETWEEN 60 AND 900)
            FROM e
            """, p).fetchall(),
    )

    _print_rows(
        "Most bunched frequent ETS routes (>= 200 events, all dates)",
        ["route", "name", "events", "pct_bunched", "pct_gap", "headway_cv", "excess_wait_s"],
        conn.execute(
            """
            SELECT route_short_name, left(route_long_name, 40), events, pct_bunched::float8, pct_gap::float8,
                   headway_cv::float8, excess_wait_s::float8
            FROM analysis.bunching_by_route WHERE events >= 200 AND is_ets ORDER BY pct_bunched DESC LIMIT 5
            """).fetchall(),
    )

    _print_rows(
        "ETS trip delivery by schedule date (no_data = collector offline; not_in_feed = route never appears in the live feed)",
        ["schedule_date", "scheduled", "no_data", "not_in_feed", "judged", "ran", "partial", "cancelled",
         "tracked_not_matched", "missing", "pct_delivered", "pct_missing"],
        conn.execute(
            """
            SELECT schedule_date, count(*),
                   count(*) FILTER (WHERE status = 'no_data'),
                   count(*) FILTER (WHERE status = 'not_in_feed'),
                   count(*) FILTER (WHERE status NOT IN ('no_data', 'not_in_feed')),
                   count(*) FILTER (WHERE status = 'ran'),
                   count(*) FILTER (WHERE status = 'partial'),
                   count(*) FILTER (WHERE status = 'cancelled'),
                   count(*) FILTER (WHERE status = 'tracked_not_matched'),
                   count(*) FILTER (WHERE status = 'missing'),
                   round(100.0 * count(*) FILTER (WHERE status IN ('ran', 'partial'))
                         / nullif(count(*) FILTER (WHERE status NOT IN ('no_data', 'not_in_feed')), 0), 1)::float8,
                   round(100.0 * count(*) FILTER (WHERE status = 'missing')
                         / nullif(count(*) FILTER (WHERE status NOT IN ('no_data', 'not_in_feed')), 0), 1)::float8
            FROM analysis.trip_status s
            JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
            LEFT JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = s.route_id
            WHERE schedule_date BETWEEN %(date_from)s AND %(date_to)s AND coalesce(ra.is_ets, true)
            GROUP BY schedule_date ORDER BY schedule_date
            """, p).fetchall(),
    )

    _print_rows(
        "ETS routes with the most missing trips (>= 20 trips judged, all dates)",
        ["route", "name", "judged", "missing", "cancelled", "pct_missing", "pct_delivered"],
        conn.execute(
            """
            SELECT route_short_name, left(route_long_name, 40), trips_judged, missing, cancelled,
                   pct_missing::float8, pct_delivered::float8
            FROM analysis.delivery_by_route WHERE trips_judged >= 20 AND is_ets
            ORDER BY pct_missing DESC, missing DESC LIMIT 8
            """).fetchall(),
    )

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
        db.apply_schema(conn, GTFS_SCHEMA_PATH)
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

            day = date_from - timedelta(days=1)
            while day <= date_to:
                t0 = time.perf_counter()
                n = build_headways(conn, day)
                if n:
                    log.info("%s: %s headways (%.1f s)", day, f"{n:,}", time.perf_counter() - t0)
                t0 = time.perf_counter()
                n = build_trip_status(conn, day)
                if n:
                    log.info("%s: %s scheduled trips classified (%.1f s)", day, f"{n:,}", time.perf_counter() - t0)
                day += timedelta(days=1)

        if args.check or args.check_only:
            check(conn, date_from, date_to)


if __name__ == "__main__":
    main()
