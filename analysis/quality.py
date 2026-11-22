"""Generate docs/data_quality.md from the collected data."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import psycopg

from analysis import build
from collector import db
from collector.config import Config
from collector.tz import EDMONTON

ROOT = Path(__file__).resolve().parent.parent
POLL_INTERVAL_S = 30
GAP_THRESHOLD_S = 90

ISSUES = [
    ("Stop-sequence field jumps ahead",
     "While a bus is between stops, `current_stop_sequence` often reports the trip's final stop, then snaps back "
     "at the next real stop. A method built on that field had a median bias of -287 s against ETS's own "
     "departure times.",
     "Stop passages are timed from GPS positions matched to each trip's route shape; the field is no longer used."),
    ("After-midnight trips labelled with the next day",
     "GTFS keeps late-night trips on the previous service day (times like 24:30:00), but the live feed labels them "
     "with the calendar date, so they were compared with a schedule 24 hours away.",
     "Each passage is compared with whichever of the labelled date or the day before fits the schedule "
     "(`schedule_date`)."),
    ("Outdated time zone data in the database image",
     "The stock Postgres image placed Edmonton at UTC-7 while the live data and the host were at UTC-6, which would "
     "have shifted every schedule comparison by an hour.",
     "The database image now installs current tzdata, and the schedule loader checks Postgres against Python on "
     "every run. Python environments differ too (a fresh environment still placed Edmonton at UTC-7), so Python "
     "code and tests load the zone from the pinned `tzdata` package instead of the host's database."),
    ("Server closing idle connections",
     "ETS's server occasionally closes kept-alive connections; the next request failed instantly on both feeds.",
     "The collector retries connection errors (not HTTP errors) on a fresh connection."),
    ("Trip-update volume",
     "The trip-update feed repeats about 2,200 trips' predictions every 30 s, including trips that have not "
     "started, which projected to over 20 GB in two weeks.",
     "A prediction is stored only when it changes (unique index with `NULLS NOT DISTINCT`)."),
    ("Loop routes locked to the end of the route",
     "On loop routes the bus lays over at the bay where the route shape ends. The first ping matched the end of the "
     "shape, and because matching only moves forward, every later ping was rejected. About 400 trips on routes "
     "such as 517, 509A/B, 512, 514 and 109 were tracked but never matched.",
     "If three consecutive pings sit on the route but behind the current position, the match is treated as a "
     "wrong lock: those placements are discarded and matching restarts from the next ping."),
    ("First and last stops not timed",
     "Departures are timed when a bus passes 30 m beyond a stop, so a bus that waits at a bay further past its "
     "first stop never registered a terminal departure, and final stops were never timed at all. Two-stop express "
     "trips such as 700X showed as tracked but not matched.",
     "A bus first seen standing within 300 m past its first stop is timed from when it pulls away, and the final "
     "stop records an arrival against the scheduled arrival. On-time performance still uses departures only."),
    ("Routes absent from the live feed",
     "LRT lines and some regional routes are in the static schedule but never appear in the live vehicle feed.",
     "Their trips are classified `not_in_feed` and excluded from delivery figures; the analysis covers bus service."),
    ("Queries that stalled at full scale",
     "Two analysis queries ran in seconds on test data but stalled on the real database because of stale table "
     "statistics and missing indexes.",
     "Tables are analyzed after each rebuild, join columns are indexed, and the trip-status query runs in timed, "
     "analyzed stages."),
]


def md_table(cols: list[str], rows: list[tuple]) -> str:
    def cell(v) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.1f}"
        if isinstance(v, int):
            return f"{v:,}"
        if isinstance(v, datetime):
            return v.astimezone(EDMONTON).strftime("%a %b %d %H:%M")
        return str(v).replace("|", "/")

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def availability(conn: psycopg.Connection) -> tuple[str, list[tuple]]:
    rows = conn.execute(
        """
        SELECT feed,
               min(polled_at) AS first_poll,
               max(polled_at) AS last_poll,
               count(*) FILTER (WHERE error IS NULL) AS ok,
               count(*) FILTER (WHERE error IS NOT NULL) AS failed,
               round(100.0 * avg(is_stale::int) FILTER (WHERE error IS NULL), 1)::float8 AS pct_stale,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM polled_at - header_ts))
                   FILTER (WHERE error IS NULL) AS median_age_s,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM polled_at - header_ts))
                   FILTER (WHERE error IS NULL) AS p90_age_s
        FROM rt.poll_log
        WHERE feed IN ('vehicle_positions', 'trip_updates')
        GROUP BY feed ORDER BY feed
        """).fetchall()
    out = []
    for feed, first, last, ok, failed, stale, med_age, p90_age in rows:
        expected = max(1, int((last - first).total_seconds() // POLL_INTERVAL_S) + 1)
        out.append((feed, first, last, expected, ok, failed, round(min(100.0, 100.0 * ok / expected), 2),
                    stale, float(med_age or 0), float(p90_age or 0)))
    return md_table(
        ["feed", "first poll", "last poll", "expected polls", "successful", "failed", "availability %",
         "stale %", "median feed age s", "p90 feed age s"], out), out


def outages(conn: psycopg.Connection) -> tuple[str, list[tuple]]:
    rows = conn.execute(
        """
        WITH ok AS (
            SELECT feed, polled_at,
                   lag(polled_at) OVER (PARTITION BY feed ORDER BY polled_at) AS prev
            FROM rt.poll_log
            WHERE error IS NULL AND feed IN ('vehicle_positions', 'trip_updates')
        )
        SELECT ok.feed, ok.prev, ok.polled_at,
               round(extract(epoch FROM ok.polled_at - ok.prev) / 60.0, 1)::float8 AS minutes,
               (SELECT string_agg(DISTINCT CASE
                           WHEN e.http_status IS NOT NULL THEN 'HTTP ' || e.http_status
                           WHEN e.error LIKE 'ConnectionError%%' THEN 'connection dropped'
                           ELSE split_part(e.error, ':', 1) END, ', ')
                FROM rt.poll_log e
                WHERE e.feed = ok.feed AND e.error IS NOT NULL
                  AND e.polled_at > ok.prev AND e.polled_at < ok.polled_at) AS causes
        FROM ok
        WHERE ok.prev IS NOT NULL AND ok.polled_at - ok.prev > make_interval(secs => %s)
        ORDER BY ok.prev
        """, (GAP_THRESHOLD_S,)).fetchall()
    out = [(feed, start, end, minutes, causes or "no polls logged (collector restarting or offline)")
           for feed, start, end, minutes, causes in rows]
    return md_table(["feed", "last good poll", "next good poll", "gap (min)", "cause"], out), out


def vehicle_fields(conn: psycopg.Connection) -> str:
    cols = ["trip_id", "route_id", "direction_id", "start_date", "latitude", "bearing", "speed_mps",
            "current_stop_sequence", "stop_id", "current_status", "occupancy_status"]
    sel = ", ".join(f"round(100.0 * avg(({c} IS NOT NULL)::int), 1)::float8" for c in cols)
    row = conn.execute(f"SELECT count(*), {sel} FROM rt.vehicle_positions").fetchone()
    return f"{row[0]:,} vehicle pings stored.\n\n" + md_table(["field", "% filled"], list(zip(cols, row[1:])))


def trip_update_fields(conn: psycopg.Connection) -> str:
    row = conn.execute(
        """
        SELECT count(*),
               round(100.0 * avg((trip_id IS NULL)::int), 1)::float8,
               round(100.0 * avg((arrival_ts IS NOT NULL)::int), 1)::float8,
               round(100.0 * avg((departure_ts IS NOT NULL)::int), 1)::float8,
               round(100.0 * avg((departure_delay_s IS NOT NULL)::int), 1)::float8,
               count(DISTINCT (start_date, trip_id)) FILTER (WHERE trip_schedule_relationship = 'CANCELED'),
               count(*) FILTER (WHERE stop_schedule_relationship = 'SKIPPED')
        FROM rt.trip_updates
        """).fetchone()
    alerts = conn.execute(
        """
        SELECT count(DISTINCT entity_id),
               count(DISTINCT entity_id) FILTER (WHERE active_end >= '2999-01-01'),
               string_agg(DISTINCT effect, ', ')
        FROM rt.alerts
        """).fetchone()
    return md_table(
        ["measure", "value"],
        [("trip-update rows stored (changes only)", row[0]),
         ("% rows with no trip_id", row[1]),
         ("% rows with an arrival time", row[2]),
         ("% rows with a departure time", row[3]),
         ("% rows with a departure delay", row[4]),
         ("trips announced as cancelled", row[5]),
         ("skipped-stop predictions", row[6]),
         ("distinct service alerts", alerts[0]),
         ("alerts with no real end date (year 3000 placeholder)", alerts[1]),
         ("alert effects seen", alerts[2])])


def consistency(conn: psycopg.Connection) -> str:
    feed = conn.execute("SELECT feed_id FROM gtfs.feeds ORDER BY fetched_at DESC LIMIT 1").fetchone()
    if not feed:
        return "No schedule loaded."
    checks = [
        ("vehicle trip_ids in schedule", "rt.vehicle_positions", "trip_id", "trips"),
        ("vehicle route_ids in schedule", "rt.vehicle_positions", "route_id", "routes"),
        ("vehicle stop_ids in schedule", "rt.vehicle_positions", "stop_id", "stops"),
        ("trip-update trip_ids in schedule", "rt.trip_updates", "trip_id", "trips"),
    ]
    rows = []
    for label, source, col, table in checks:
        seen, matched = conn.execute(
            f"""
            SELECT count(DISTINCT x.{col}), count(DISTINCT x.{col}) FILTER (WHERE g.{col} IS NOT NULL)
            FROM {source} x LEFT JOIN gtfs.{table} g ON g.feed_id = %s AND g.{col} = x.{col}
            WHERE x.{col} IS NOT NULL
            """, feed).fetchone()
        rows.append((label, seen, matched, round(100.0 * matched / seen, 1) if seen else None))
    text = md_table(["check", "distinct IDs seen", "found in schedule", "%"], rows)

    if conn.execute("SELECT to_regclass('analysis.route_agency')").fetchone()[0]:
        agencies = conn.execute(
            """
            SELECT coalesce(agency_name, agency_id, '(none)'), CASE WHEN is_ets THEN 'yes' ELSE 'no' END, count(*)
            FROM analysis.route_agency WHERE feed_id = %s
            GROUP BY 1, 2 ORDER BY count(*) DESC
            """, feed).fetchall()
        text += ("\n\nThe schedule covers ETS and its regional partners. Headline figures in the analysis use ETS "
                 "routes only; partner agencies are reported separately.\n\n")
        text += md_table(["agency", "counted as ETS", "routes"], agencies)

    absent = conn.execute(
        """
        SELECT r.route_short_name, r.route_long_name, count(*)
        FROM analysis.trip_status s
        JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
        JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = s.route_id
        WHERE s.status = 'not_in_feed'
        GROUP BY r.route_short_name, r.route_long_name
        ORDER BY count(*) DESC
        """).fetchall() if conn.execute("SELECT to_regclass('analysis.trip_status')").fetchone()[0] else []
    if absent:
        text += "\n\nScheduled routes that never appear in the live vehicle feed:\n\n"
        text += md_table(["route", "name", "scheduled trips excluded"], absent)
    return text


def validation(conn: psycopg.Connection) -> str:
    if not conn.execute("SELECT to_regclass('analysis.stop_passages')").fetchone()[0]:
        return "Analysis tables not built yet."
    cross = conn.execute(
        """
        WITH ets AS (
            SELECT DISTINCT ON (start_date, trip_id, stop_sequence) start_date, trip_id, stop_sequence, departure_ts
            FROM rt.trip_updates
            WHERE is_past AND departure_ts IS NOT NULL
            ORDER BY start_date, trip_id, stop_sequence, feed_ts DESC
        ),
        d AS (
            SELECT extract(epoch FROM s.observed_ts - e.departure_ts) AS diff
            FROM analysis.stop_events s
            JOIN ets e ON e.start_date = s.service_date AND e.trip_id = s.trip_id AND e.stop_sequence = s.stop_sequence
        )
        SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY diff),
               percentile_cont(0.1) WITHIN GROUP (ORDER BY diff), percentile_cont(0.9) WITHIN GROUP (ORDER BY diff),
               round(100.0 * avg((abs(diff) <= 60)::int), 1)::float8
        FROM d
        """).fetchone()
    status = conn.execute(
        """
        SELECT status, count(*) FROM analysis.trip_status GROUP BY status ORDER BY count(*) DESC
        """).fetchall() if conn.execute("SELECT to_regclass('analysis.trip_status')").fetchone()[0] else []
    window = conn.execute(
        "SELECT count(*), round(100.0 * avg((window_s <= 120)::int), 1)::float8 FROM analysis.stop_passages"
    ).fetchone()
    text = ("Inferred stop passage times compared with the departure times ETS reports for stops already "
            "passed (seconds, ours minus ETS):\n\n")
    text += md_table(["compared", "median", "p10", "p90", "% within 60 s"],
                     [(cross[0], float(cross[1] or 0), float(cross[2] or 0), float(cross[3] or 0), cross[4])])
    span = conn.execute("SELECT min(service_date), max(service_date) FROM analysis.stop_passages").fetchone()
    if span[0] is not None:
        params = {"date_from": span[0], "date_to": span[1]}
        text += ("\n\nThe same comparison split by group. A group whose median sits far from the others "
                 "would point to a bias in the method for that kind of stop or service:\n\n")
        text += md_table(build.CROSS_CHECK_COLS[:2] + ["compared", "median", "p10", "p90", "% within 60 s"],
                         [(r[0], r[1], r[2], float(r[3]), float(r[4]), float(r[5]), r[6])
                          for r in conn.execute(build.CROSS_CHECK_BREAKDOWN_SQL, params).fetchall()])
        text += ("\n\nOn-time performance of ETS routes under Edmonton's 2009 Transit Service Standards (policy C539): departures "
                 "from timepoints 0 to 3 minutes late with no early departures, and arrivals 5 minutes early to 1 "
                 "minute late, each against a 90% target. The current policy (C539A) does not define on-time "
                 "performance. Because inferred times carry measurement error, the last column allows 30 s either "
                 "side.\n\n")
        text += md_table(["measure", "events", "% on time", "% early", "% late", "% on time allowing 30 s"],
                         conn.execute(build.ETS_STANDARD_SQL, params).fetchall())
    text += (f"\n\n{window[0]:,} stop passages inferred; {window[1]}% fall within a crossing window of 120 s "
             "or less and count toward on-time performance.")
    if status:
        text += "\n\nScheduled trips by delivery status:\n\n" + md_table(["status", "trips"], status)
    return text


def report(conn: psycopg.Connection) -> str:
    generated = datetime.now(EDMONTON).strftime("%Y-%m-%d %H:%M %Z")
    avail_md, avail = availability(conn)
    outage_md, gaps = outages(conn)
    lines = [
        "# Data quality",
        "",
        f"Generated {generated} by `python -m analysis.quality` from the collected data. "
        "Rerun it to refresh every figure.",
        "",
        "## Feed availability",
        "",
        f"Polls run every {POLL_INTERVAL_S} s. Availability is successful polls over expected polls between the "
        "first and last poll; feed age is how old the feed's own timestamp was when polled.",
        "",
        avail_md,
        "",
        f"### Gaps longer than {GAP_THRESHOLD_S} s",
        "",
        outage_md if gaps else "None.",
        "",
        "## Field completeness",
        "",
        "### Vehicle positions",
        "",
        vehicle_fields(conn),
        "",
        "### Trip updates and alerts",
        "",
        trip_update_fields(conn),
        "",
        "## Schedule and live feed consistency",
        "",
        consistency(conn),
        "",
        "## Method validation",
        "",
        validation(conn),
        "",
        "## Issues found and how they were handled",
        "",
    ]
    for title, problem, fix in ISSUES:
        lines += [f"**{title}.** {problem} *Handled:* {fix}", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the data-quality report")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "data_quality.md")
    args = parser.parse_args()
    with db.connect(Config.from_env().database_url) as conn:
        text = report(conn)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
