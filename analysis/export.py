"""Write Tableau-ready CSV extracts and a data dictionary to exports/."""

from __future__ import annotations

import argparse
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import psycopg

from collector import db
from collector.config import Config
from collector.tz import EDMONTON

ROOT = Path(__file__).resolve().parent.parent
LOCAL = "'America/Edmonton'"
STORY_ROUTES = ("56", "127")

DAY_FIELDS = """
       to_char({d}, 'Dy') AS day_name,
       CASE WHEN extract(isodow FROM {d}) <= 5 THEN 'Weekday' ELSE 'Weekend' END AS day_type"""

ETS_ROUTES = """
ets_routes AS (
    SELECT DISTINCT ON (r.route_id) r.route_id, r.route_short_name, r.route_long_name,
           CASE WHEN r.route_short_name LIKE '%%X' THEN 'Express' ELSE 'Regular' END AS route_kind
    FROM gtfs.routes r
    JOIN gtfs.feeds f ON f.feed_id = r.feed_id
    JOIN analysis.route_agency ra ON ra.feed_id = r.feed_id AND ra.route_id = r.route_id
    WHERE ra.is_ets
    ORDER BY r.route_id, f.fetched_at DESC
)"""

OTP_COUNTS = """count(*) AS events,
           count(*) FILTER (WHERE status = 'on_time') AS n_on_time,
           count(*) FILTER (WHERE status = 'early') AS n_early,
           count(*) FILTER (WHERE status = 'late') AS n_late,
           round(100.0 * avg((status = 'on_time')::int), 1) AS pct_on_time,
           round(100.0 * avg((status = 'early')::int), 1) AS pct_early,
           round(100.0 * avg((status = 'late')::int), 1) AS pct_late,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY delay_s) AS median_delay_s"""

EWT = """round((sum(actual_headway_s::float8 ^ 2) / (2 * sum(actual_headway_s))
                  - sum(scheduled_headway_s::float8 ^ 2) / (2 * sum(scheduled_headway_s)))::numeric)"""

NETWORK_DAILY = f"""
WITH otp AS (
    SELECT coalesce(schedule_date, service_date) AS day, {OTP_COUNTS},
           to_char(min(observed_ts) AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI') AS first_event_local,
           to_char(max(observed_ts) AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI') AS last_event_local
    FROM analysis.stop_events
    WHERE is_ets AND status IN ('on_time', 'early', 'late')
      AND coalesce(schedule_date, service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
ets AS (
    SELECT coalesce(schedule_date, service_date) AS day,
           count(*) AS ets_departures,
           count(*) FILTER (WHERE ets_status = 'on_time') AS ets_n_on_time,
           count(*) FILTER (WHERE ets_status = 'early') AS ets_n_early,
           count(*) FILTER (WHERE on_time_allowing_30s) AS ets_n_on_time_allowing_30s
    FROM analysis.ets_standard_events
    WHERE is_ets AND event = 'departure'
    GROUP BY 1
),
bunch AS (
    SELECT schedule_date AS day, count(*) AS headway_events,
           count(*) FILTER (WHERE spacing = 'bunched') AS n_bunched,
           count(*) FILTER (WHERE spacing = 'gap') AS n_gap,
           {EWT} AS excess_wait_s
    FROM analysis.headway_events
    WHERE is_ets AND actual_headway_s > 0
    GROUP BY 1
),
delivery AS (
    SELECT s.schedule_date AS day,
           count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')) AS trips_judged,
           count(*) FILTER (WHERE s.status IN ('ran', 'partial')) AS trips_delivered,
           count(*) FILTER (WHERE s.status = 'missing') AS trips_missing,
           count(*) FILTER (WHERE s.status = 'cancelled') AS trips_cancelled
    FROM analysis.trip_status s
    JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
    JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = s.route_id
    WHERE ra.is_ets
    GROUP BY 1
)
SELECT otp.day AS service_date, {DAY_FIELDS.format(d='otp.day')},
       otp.first_event_local, otp.last_event_local,
       otp.events, otp.n_on_time, otp.n_early, otp.n_late, otp.pct_on_time, otp.pct_early, otp.pct_late,
       otp.median_delay_s,
       ets.ets_departures, ets.ets_n_on_time, ets.ets_n_early, ets.ets_n_on_time_allowing_30s,
       round(100.0 * ets.ets_n_on_time / nullif(ets.ets_departures, 0), 1) AS ets_pct_on_time,
       round(100.0 * ets.ets_n_on_time_allowing_30s / nullif(ets.ets_departures, 0), 1) AS ets_pct_on_time_allowing_30s,
       bunch.headway_events, bunch.n_bunched, bunch.n_gap,
       round(100.0 * bunch.n_bunched / nullif(bunch.headway_events, 0), 1) AS pct_bunched,
       round(100.0 * bunch.n_gap / nullif(bunch.headway_events, 0), 1) AS pct_gap,
       bunch.excess_wait_s,
       delivery.trips_judged, delivery.trips_delivered, delivery.trips_missing, delivery.trips_cancelled,
       round(100.0 * delivery.trips_missing / nullif(delivery.trips_judged, 0), 1) AS pct_missing
FROM otp
LEFT JOIN ets USING (day)
LEFT JOIN bunch USING (day)
LEFT JOIN delivery USING (day)
ORDER BY otp.day
"""

ROUTES = f"""
WITH {ETS_ROUTES},
otp AS (
    SELECT route_id, {OTP_COUNTS},
           percentile_cont(0.9) WITHIN GROUP (ORDER BY delay_s) AS p90_delay_s
    FROM analysis.stop_events
    WHERE is_ets AND status IN ('on_time', 'early', 'late')
      AND coalesce(schedule_date, service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY route_id
),
ets AS (
    SELECT route_id, count(*) AS ets_departures,
           count(*) FILTER (WHERE ets_status = 'on_time') AS ets_n_on_time,
           count(*) FILTER (WHERE ets_status = 'early') AS ets_n_early,
           count(*) FILTER (WHERE on_time_allowing_30s) AS ets_n_on_time_allowing_30s
    FROM analysis.ets_standard_events
    WHERE is_ets AND event = 'departure'
      AND coalesce(schedule_date, service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY route_id
),
bunch AS (
    SELECT route_id, count(*) AS headway_events,
           count(*) FILTER (WHERE spacing = 'bunched') AS n_bunched,
           count(*) FILTER (WHERE spacing = 'gap') AS n_gap,
           {EWT} AS excess_wait_s
    FROM analysis.headway_events
    WHERE is_ets AND actual_headway_s > 0 AND schedule_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY route_id
),
delivery AS (
    SELECT route_id,
           count(*) FILTER (WHERE status NOT IN ('no_data', 'not_in_feed')) AS trips_judged,
           count(*) FILTER (WHERE status IN ('ran', 'partial')) AS trips_delivered,
           count(*) FILTER (WHERE status = 'missing') AS trips_missing,
           count(*) FILTER (WHERE status = 'cancelled') AS trips_cancelled
    FROM analysis.trip_status
    WHERE schedule_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY route_id
)
SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_kind,
       otp.events, otp.n_on_time, otp.n_early, otp.n_late, otp.pct_on_time, otp.pct_early, otp.pct_late,
       otp.median_delay_s, otp.p90_delay_s,
       ets.ets_departures, ets.ets_n_on_time, ets.ets_n_early, ets.ets_n_on_time_allowing_30s,
       round(100.0 * ets.ets_n_on_time / nullif(ets.ets_departures, 0), 1) AS ets_pct_on_time,
       round(100.0 * ets.ets_n_on_time_allowing_30s / nullif(ets.ets_departures, 0), 1) AS ets_pct_on_time_allowing_30s,
       bunch.headway_events, bunch.n_bunched, bunch.n_gap,
       round(100.0 * bunch.n_bunched / nullif(bunch.headway_events, 0), 1) AS pct_bunched,
       round(100.0 * bunch.n_gap / nullif(bunch.headway_events, 0), 1) AS pct_gap,
       bunch.excess_wait_s,
       delivery.trips_judged, delivery.trips_delivered, delivery.trips_missing, delivery.trips_cancelled,
       round(100.0 * delivery.trips_missing / nullif(delivery.trips_judged, 0), 1) AS pct_missing
FROM ets_routes r
LEFT JOIN otp USING (route_id)
LEFT JOIN ets USING (route_id)
LEFT JOIN bunch USING (route_id)
LEFT JOIN delivery USING (route_id)
WHERE otp.events IS NOT NULL OR delivery.trips_judged > 0
ORDER BY r.route_short_name
"""

ROUTE_HOUR = f"""
WITH {ETS_ROUTES}
SELECT e.route_id, r.route_short_name, r.route_kind,
       e.iso_dow, {DAY_FIELDS.format(d="coalesce(e.schedule_date, e.service_date)")},
       e.local_hour,
       {OTP_COUNTS.replace("status", "e.status").replace("delay_s)", "e.delay_s)")}
FROM analysis.stop_events e
JOIN ets_routes r USING (route_id)
WHERE e.is_ets AND e.status IN ('on_time', 'early', 'late')
  AND coalesce(e.schedule_date, e.service_date) BETWEEN %(date_from)s AND %(date_to)s
GROUP BY e.route_id, r.route_short_name, r.route_kind, e.iso_dow, 5, 6, e.local_hour
ORDER BY r.route_short_name, e.iso_dow, e.local_hour
"""

STOP_ROUTE = f"""
WITH {ETS_ROUTES},
headsigns AS (
    SELECT DISTINCT ON (t.route_id, t.direction_id) t.route_id, t.direction_id, t.trip_headsign
    FROM analysis.stop_events e
    JOIN gtfs.trips t ON t.feed_id = e.feed_id AND t.trip_id = e.trip_id
    WHERE e.is_ets AND coalesce(e.schedule_date, e.service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY t.route_id, t.direction_id, t.trip_headsign
    ORDER BY t.route_id, t.direction_id, count(*) DESC
)
SELECT e.route_id, r.route_short_name, e.direction_id, h.trip_headsign AS headsign,
       e.stop_id, max(s.stop_name) AS stop_name, max(s.stop_lat) AS stop_lat, max(s.stop_lon) AS stop_lon,
       min(e.stop_sequence) AS stop_order, max(coalesce(e.timepoint, 0)) AS is_timepoint,
       {OTP_COUNTS.replace("status", "e.status").replace("delay_s)", "e.delay_s)")},
       round(avg(e.delay_s)) AS avg_delay_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY e.delay_s) AS p90_delay_s
FROM analysis.stop_events e
JOIN ets_routes r USING (route_id)
JOIN gtfs.stops s ON s.feed_id = e.feed_id AND s.stop_id = e.stop_id
LEFT JOIN headsigns h ON h.route_id = e.route_id AND h.direction_id IS NOT DISTINCT FROM e.direction_id
WHERE e.is_ets AND e.status IN ('on_time', 'early', 'late')
  AND coalesce(e.schedule_date, e.service_date) BETWEEN %(date_from)s AND %(date_to)s
GROUP BY e.route_id, r.route_short_name, e.direction_id, h.trip_headsign, e.stop_id
ORDER BY r.route_short_name, e.direction_id, stop_order
"""

ROUTE_SHAPES = f"""
WITH {ETS_ROUTES},
usage AS (
    SELECT t.feed_id, t.route_id, t.direction_id, t.shape_id, count(*) AS n
    FROM analysis.stop_passages p
    JOIN gtfs.trips t ON t.feed_id = p.feed_id AND t.trip_id = p.trip_id
    WHERE coalesce(p.schedule_date, p.service_date) BETWEEN %(date_from)s AND %(date_to)s
      AND t.shape_id IS NOT NULL
    GROUP BY t.feed_id, t.route_id, t.direction_id, t.shape_id
),
main_shape AS (
    SELECT DISTINCT ON (route_id, direction_id) feed_id, route_id, direction_id, shape_id
    FROM usage ORDER BY route_id, direction_id, n DESC
)
SELECT m.route_id, r.route_short_name, m.direction_id, m.shape_id,
       sh.shape_pt_sequence AS point_order, sh.shape_pt_lat AS lat, sh.shape_pt_lon AS lon
FROM main_shape m
JOIN ets_routes r USING (route_id)
JOIN gtfs.shapes sh ON sh.feed_id = m.feed_id AND sh.shape_id = m.shape_id
ORDER BY r.route_short_name, m.direction_id, sh.shape_pt_sequence
"""

HEADWAYS = f"""
WITH {ETS_ROUTES}
SELECT h.schedule_date AS service_date, {DAY_FIELDS.format(d='h.schedule_date')},
       h.route_id, r.route_short_name, h.direction_id, t.trip_headsign AS headsign,
       h.stop_id, s.stop_name, s.stop_lat, s.stop_lon,
       to_char(h.observed_ts AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI:SS') AS observed_local,
       h.local_hour, h.trip_id, h.prev_trip_id,
       h.actual_headway_s, h.scheduled_headway_s, round(h.headway_ratio::numeric, 2) AS headway_ratio, h.spacing
FROM analysis.headway_events h
JOIN ets_routes r USING (route_id)
JOIN analysis.feed_by_date f ON f.service_date = h.schedule_date
JOIN gtfs.stops s ON s.feed_id = f.feed_id AND s.stop_id = h.stop_id
JOIN gtfs.trips t ON t.feed_id = f.feed_id AND t.trip_id = h.trip_id
JOIN gtfs.stop_times st ON st.feed_id = f.feed_id AND st.trip_id = h.trip_id AND st.stop_sequence = h.stop_sequence
WHERE h.is_ets AND h.actual_headway_s > 0 AND h.schedule_date BETWEEN %(date_from)s AND %(date_to)s
  AND (%(all_headway_stops)s OR st.timepoint = 1 OR s.stop_name ILIKE '%%Transit Centre%%')
ORDER BY h.schedule_date, r.route_short_name, h.direction_id, h.stop_id, h.observed_ts
"""

TRIPS = f"""
WITH {ETS_ROUTES}
SELECT s.schedule_date AS service_date, {DAY_FIELDS.format(d='s.schedule_date')},
       s.route_id, r.route_short_name, r.route_kind, s.direction_id, t.trip_headsign AS headsign, s.trip_id,
       to_char(s.scheduled_start AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI') AS scheduled_start_local,
       to_char(s.scheduled_end AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI') AS scheduled_end_local,
       extract(hour FROM s.scheduled_start AT TIME ZONE {LOCAL})::int AS start_hour,
       s.status, s.stops_scheduled, s.stops_observed
FROM analysis.trip_status s
JOIN ets_routes r USING (route_id)
JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
JOIN gtfs.trips t ON t.feed_id = f.feed_id AND t.trip_id = s.trip_id
WHERE s.schedule_date BETWEEN %(date_from)s AND %(date_to)s
ORDER BY s.schedule_date, r.route_short_name, s.scheduled_start
"""


NETWORK_SUMMARY = f"""
WITH otp AS (
    SELECT {OTP_COUNTS}
    FROM analysis.stop_events
    WHERE is_ets AND status IN ('on_time', 'early', 'late')
      AND coalesce(schedule_date, service_date) BETWEEN %(date_from)s AND %(date_to)s
),
ets AS (
    SELECT count(*) AS ets_departures,
           count(*) FILTER (WHERE ets_status = 'on_time') AS ets_n_on_time,
           count(*) FILTER (WHERE ets_status = 'early') AS ets_n_early,
           count(*) FILTER (WHERE on_time_allowing_30s) AS ets_n_on_time_allowing_30s
    FROM analysis.ets_standard_events
    WHERE is_ets AND event = 'departure'
      AND coalesce(schedule_date, service_date) BETWEEN %(date_from)s AND %(date_to)s
),
bunch AS (
    SELECT count(*) AS headway_events,
           count(*) FILTER (WHERE spacing = 'bunched') AS n_bunched,
           count(*) FILTER (WHERE spacing = 'gap') AS n_gap,
           {EWT} AS excess_wait_s
    FROM analysis.headway_events
    WHERE is_ets AND actual_headway_s > 0 AND schedule_date BETWEEN %(date_from)s AND %(date_to)s
),
delivery AS (
    SELECT count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')) AS trips_judged,
           count(*) FILTER (WHERE s.status IN ('ran', 'partial')) AS trips_delivered,
           count(*) FILTER (WHERE s.status = 'missing') AS trips_missing,
           count(*) FILTER (WHERE s.status = 'cancelled') AS trips_cancelled
    FROM analysis.trip_status s
    JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
    JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = s.route_id
    WHERE ra.is_ets AND s.schedule_date BETWEEN %(date_from)s AND %(date_to)s
)
SELECT %(date_from)s::date AS date_from, %(date_to)s::date AS date_to,
       otp.events, otp.n_on_time, otp.n_early, otp.n_late, otp.pct_on_time, otp.pct_early, otp.pct_late,
       otp.median_delay_s,
       ets.ets_departures, ets.ets_n_on_time, ets.ets_n_early, ets.ets_n_on_time_allowing_30s,
       round(100.0 * ets.ets_n_on_time / nullif(ets.ets_departures, 0), 1) AS ets_pct_on_time,
       round(100.0 * ets.ets_n_on_time_allowing_30s / nullif(ets.ets_departures, 0), 1) AS ets_pct_on_time_allowing_30s,
       bunch.headway_events, bunch.n_bunched, bunch.n_gap,
       round(100.0 * bunch.n_bunched / nullif(bunch.headway_events, 0), 1) AS pct_bunched,
       round(100.0 * bunch.n_gap / nullif(bunch.headway_events, 0), 1) AS pct_gap,
       bunch.excess_wait_s,
       delivery.trips_judged, delivery.trips_delivered, delivery.trips_missing, delivery.trips_cancelled,
       round(100.0 * delivery.trips_missing / nullif(delivery.trips_judged, 0), 1) AS pct_missing
FROM otp, ets, bunch, delivery
"""

STORY_PASSAGES = f"""
WITH {ETS_ROUTES}
SELECT coalesce(p.schedule_date, p.service_date) AS service_date,
       {DAY_FIELDS.format(d='coalesce(p.schedule_date, p.service_date)')},
       p.route_id, r.route_short_name, p.direction_id, t.trip_headsign AS headsign, p.trip_id, p.vehicle_id,
       p.stop_sequence, p.stop_id, s.stop_name, coalesce(p.timepoint, 0) AS is_timepoint, p.event,
       to_char(p.scheduled_ts AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI:SS') AS scheduled_local,
       to_char(p.observed_ts AT TIME ZONE {LOCAL}, 'YYYY-MM-DD HH24:MI:SS') AS observed_local,
       p.delay_s, p.window_s
FROM analysis.stop_passages p
JOIN ets_routes r USING (route_id)
JOIN gtfs.trips t ON t.feed_id = p.feed_id AND t.trip_id = p.trip_id
JOIN gtfs.stops s ON s.feed_id = p.feed_id AND s.stop_id = p.stop_id
WHERE ltrim(r.route_short_name, '0') = ANY(%(story_routes)s)
  AND p.window_s <= 120 AND p.delay_s IS NOT NULL
  AND coalesce(p.schedule_date, p.service_date) BETWEEN %(date_from)s AND %(date_to)s
ORDER BY r.route_short_name, service_date, p.scheduled_ts, p.trip_id, p.stop_sequence
"""

MAP_LAYERS = f"""
WITH {ETS_ROUTES},
usage AS (
    SELECT t.feed_id, t.route_id, t.direction_id, t.shape_id, count(*) AS n
    FROM analysis.stop_passages p
    JOIN gtfs.trips t ON t.feed_id = p.feed_id AND t.trip_id = p.trip_id
    WHERE coalesce(p.schedule_date, p.service_date) BETWEEN %(date_from)s AND %(date_to)s
      AND t.shape_id IS NOT NULL
    GROUP BY t.feed_id, t.route_id, t.direction_id, t.shape_id
),
main_shape AS (
    SELECT DISTINCT ON (route_id, direction_id) feed_id, route_id, direction_id, shape_id
    FROM usage ORDER BY route_id, direction_id, n DESC
),
headsigns AS (
    SELECT DISTINCT ON (t.route_id, t.direction_id) t.route_id, t.direction_id, t.trip_headsign
    FROM analysis.stop_events e
    JOIN gtfs.trips t ON t.feed_id = e.feed_id AND t.trip_id = e.trip_id
    WHERE e.is_ets AND coalesce(e.schedule_date, e.service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY t.route_id, t.direction_id, t.trip_headsign
    ORDER BY t.route_id, t.direction_id, count(*) DESC
),
stops AS (
    SELECT 'stop' AS layer, e.route_id, r.route_short_name, e.direction_id, h.trip_headsign AS headsign,
           NULL::integer AS point_order, max(s.stop_lat) AS lat, max(s.stop_lon) AS lon,
           e.stop_id, max(s.stop_name) AS stop_name, min(e.stop_sequence) AS stop_order,
           max(coalesce(e.timepoint, 0)) AS is_timepoint,
           count(*) AS events,
           count(*) FILTER (WHERE e.status = 'on_time') AS n_on_time,
           count(*) FILTER (WHERE e.status = 'early') AS n_early,
           count(*) FILTER (WHERE e.status = 'late') AS n_late,
           round(avg(e.delay_s)) AS avg_delay_s,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY e.delay_s) AS median_delay_s
    FROM analysis.stop_events e
    JOIN ets_routes r USING (route_id)
    JOIN gtfs.stops s ON s.feed_id = e.feed_id AND s.stop_id = e.stop_id
    LEFT JOIN headsigns h ON h.route_id = e.route_id AND h.direction_id IS NOT DISTINCT FROM e.direction_id
    WHERE e.is_ets AND e.status IN ('on_time', 'early', 'late')
      AND coalesce(e.schedule_date, e.service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY e.route_id, r.route_short_name, e.direction_id, h.trip_headsign, e.stop_id
),
shapes AS (
    SELECT 'shape' AS layer, m.route_id, r.route_short_name, m.direction_id, h.trip_headsign AS headsign,
           sh.shape_pt_sequence AS point_order, sh.shape_pt_lat AS lat, sh.shape_pt_lon AS lon,
           NULL::text AS stop_id, NULL::text AS stop_name, NULL::integer AS stop_order, NULL::integer AS is_timepoint,
           NULL::bigint AS events, NULL::bigint AS n_on_time, NULL::bigint AS n_early, NULL::bigint AS n_late,
           NULL::numeric AS avg_delay_s, NULL::float8 AS median_delay_s
    FROM main_shape m
    JOIN ets_routes r USING (route_id)
    JOIN gtfs.shapes sh ON sh.feed_id = m.feed_id AND sh.shape_id = m.shape_id
    LEFT JOIN headsigns h ON h.route_id = m.route_id AND h.direction_id IS NOT DISTINCT FROM m.direction_id
)
SELECT * FROM shapes
UNION ALL
SELECT * FROM stops
ORDER BY route_short_name, direction_id, layer, point_order, stop_order
"""

AGENCIES = """
WITH otp AS (
    SELECT coalesce(agency, '(unknown)') AS k, bool_or(is_ets) AS is_ets, count(*) AS events,
           count(*) FILTER (WHERE status = 'on_time') AS n_on_time
    FROM analysis.stop_events
    WHERE status IN ('on_time', 'early', 'late')
      AND coalesce(schedule_date, service_date) BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
bunch AS (
    SELECT coalesce(agency, '(unknown)') AS k, count(*) AS headway_events,
           count(*) FILTER (WHERE spacing = 'bunched') AS n_bunched
    FROM analysis.headway_events
    WHERE actual_headway_s > 0 AND schedule_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
delivery AS (
    SELECT coalesce(ra.agency_name, ra.agency_id, '(unknown)') AS k, bool_or(coalesce(ra.is_ets, true)) AS is_ets,
           count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')) AS trips_judged,
           count(*) FILTER (WHERE s.status = 'missing') AS trips_missing
    FROM analysis.trip_status s
    JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
    LEFT JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = s.route_id
    WHERE s.schedule_date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY 1
),
keys AS (SELECT k FROM otp UNION SELECT k FROM delivery)
SELECT keys.k AS agency, CASE WHEN coalesce(o.is_ets, d.is_ets) THEN 'yes' ELSE 'no' END AS is_ets,
       o.events, o.n_on_time, round(100.0 * o.n_on_time / nullif(o.events, 0), 1) AS pct_on_time,
       b.headway_events, b.n_bunched, round(100.0 * b.n_bunched / nullif(b.headway_events, 0), 1) AS pct_bunched,
       d.trips_judged, d.trips_missing, round(100.0 * d.trips_missing / nullif(d.trips_judged, 0), 1) AS pct_missing
FROM keys
LEFT JOIN otp o ON o.k = keys.k
LEFT JOIN delivery d ON d.k = keys.k
LEFT JOIN bunch b ON b.k = keys.k
ORDER BY o.events DESC NULLS LAST, keys.k
"""


@dataclass(frozen=True)
class Extract:
    name: str
    description: str
    sql: str


EXTRACTS = [
    Extract("network_summary.csv", "One row for the whole export period for ETS; feeds the headline cards.",
            NETWORK_SUMMARY),
    Extract("network_daily.csv", "One row per service day for ETS: on-time, ETS-standard, bunching and trip delivery.",
            NETWORK_DAILY),
    Extract("routes.csv", "One row per ETS route over the export period, with every metric.", ROUTES),
    Extract("route_hour.csv", "On-time counts by ETS route, day of week and hour, for heatmaps.", ROUTE_HOUR),
    Extract("stop_route.csv", "On-time counts and delay by ETS route, direction and stop, with coordinates.", STOP_ROUTE),
    Extract("route_shapes.csv", "Most-used route line per ETS route and direction, as ordered points.", ROUTE_SHAPES),
    Extract("headways.csv", "Observed gaps between consecutive buses on frequent ETS service, at timepoints and "
                            "transit centres (every stop with --all-headway-stops).", HEADWAYS),
    Extract("map_layers.csv", "Route lines and stop delays for ETS routes in one table, for a two-layer map.",
            MAP_LAYERS),
    Extract("trips.csv", "Delivery status of every scheduled ETS trip.", TRIPS),
    Extract("story_passages.csv", "Every timed stop for the story routes (default 56 and 127).", STORY_PASSAGES),
    Extract("agencies.csv", "Summary for ETS and each regional partner agency in the feed.", AGENCIES),
]

COLUMN_DOCS = {
    "service_date": "Service day the trip belongs to (after-midnight trips count toward the previous day).",
    "day_name": "Day of week.",
    "day_type": "Weekday or Weekend.",
    "first_event_local": "First observed departure that day (Edmonton time); shows partial collection days.",
    "last_event_local": "Last observed departure that day (Edmonton time).",
    "events": "Departures counted toward on-time performance.",
    "n_on_time": "Departures 1 minute early to 5 minutes late.",
    "n_early": "Departures more than 1 minute early.",
    "n_late": "Departures more than 5 minutes late.",
    "pct_on_time": "n_on_time / events, as a percentage. Recompute from counts when aggregating.",
    "pct_early": "n_early / events.",
    "pct_late": "n_late / events.",
    "median_delay_s": "Median departure delay in seconds (negative = early).",
    "p90_delay_s": "90th percentile departure delay in seconds.",
    "avg_delay_s": "Mean departure delay in seconds.",
    "ets_departures": "Timepoint departures judged under ETS's 2009 standard.",
    "ets_n_on_time": "Timepoint departures 0 to 3 minutes late (2009 standard).",
    "ets_n_early": "Timepoint departures before the scheduled time.",
    "ets_n_on_time_allowing_30s": "Timepoint departures 30 s early to 3.5 minutes late (allows for measurement error).",
    "ets_pct_on_time": "ets_n_on_time / ets_departures; the 2009 target was 90%.",
    "ets_pct_on_time_allowing_30s": "ets_n_on_time_allowing_30s / ets_departures.",
    "headway_events": "Observed gaps between consecutive buses on frequent service (scheduled every 15 min or better).",
    "n_bunched": "Gaps under 25% of the scheduled gap.",
    "n_gap": "Gaps over 150% of the scheduled gap.",
    "pct_bunched": "n_bunched / headway_events.",
    "pct_gap": "n_gap / headway_events.",
    "excess_wait_s": "Extra average wait in seconds caused by uneven spacing (actual minus scheduled expected wait).",
    "trips_judged": "Scheduled trips the collector could judge (excludes collector outages and routes not in the feed).",
    "trips_delivered": "Trips observed at one or more stops.",
    "trips_missing": "Trips never seen in the live feed and never announced as cancelled.",
    "trips_cancelled": "Trips announced as cancelled in the trip-update feed.",
    "pct_missing": "trips_missing / trips_judged.",
    "route_id": "GTFS route ID.",
    "route_short_name": "Public route number.",
    "route_long_name": "Route name.",
    "route_kind": "Express (route number ends in X) or Regular.",
    "iso_dow": "Day of week number, Monday = 1.",
    "local_hour": "Hour of the event in Edmonton time.",
    "direction_id": "GTFS direction (0 or 1).",
    "headsign": "Destination sign for the direction.",
    "stop_id": "GTFS stop ID.",
    "stop_name": "Stop name.",
    "stop_lat": "Stop latitude.",
    "stop_lon": "Stop longitude.",
    "stop_order": "Position of the stop along the route (lowest stop sequence seen).",
    "is_timepoint": "1 if the stop is a scheduled timepoint.",
    "shape_id": "GTFS shape ID of the most-used route line.",
    "point_order": "Order of the point along the route line; use as the Path in Tableau.",
    "lat": "Point latitude.",
    "lon": "Point longitude.",
    "observed_local": "When the bus departed the stop (Edmonton time).",
    "trip_id": "GTFS trip ID.",
    "prev_trip_id": "Trip ID of the bus before it at the same stop.",
    "actual_headway_s": "Seconds since the previous bus departed the stop.",
    "scheduled_headway_s": "Scheduled seconds between this trip and the one before it.",
    "headway_ratio": "actual_headway_s / scheduled_headway_s.",
    "spacing": "bunched, gap or regular.",
    "scheduled_start_local": "Scheduled first departure (Edmonton time).",
    "scheduled_end_local": "Scheduled last arrival (Edmonton time).",
    "start_hour": "Hour of the scheduled first departure.",
    "status": "ran, partial, cancelled, tracked_not_matched, missing, not_in_feed or no_data.",
    "stops_scheduled": "Stops on the trip.",
    "stops_observed": "Stops where the bus was timed.",
    "date_from": "First service date in the export.",
    "date_to": "Last service date in the export.",
    "layer": "shape for route-line points, stop for stops.",
    "vehicle_id": "Bus that ran the trip.",
    "stop_sequence": "Position of the stop in the trip.",
    "event": "departure, or arrival at the final stop.",
    "scheduled_local": "Scheduled time at the stop (Edmonton time).",
    "delay_s": "Observed minus scheduled time, seconds (negative = early).",
    "window_s": "Seconds between the two GPS pings either side of the stop; smaller is more precise.",
    "agency": "Transit agency.",
    "is_ets": "yes for Edmonton Transit Service.",
}


def render(conn: psycopg.Connection, sql: str, params: dict) -> str:
    return psycopg.ClientCursor(conn).mogrify(sql, params)


def write_csv(conn: psycopg.Connection, sql: str, params: dict, path: Path) -> tuple[list[str], int]:
    query = render(conn, sql, params).strip().rstrip(";")
    buf = io.BytesIO()
    with conn.cursor() as cur:
        with cur.copy(f"COPY ({query}) TO STDOUT WITH (FORMAT csv, HEADER)") as copy:
            for chunk in copy:
                buf.write(chunk)
    data = buf.getvalue()
    path.write_bytes(data)
    lines = data.decode().splitlines()
    header = next(csv.reader(lines[:1])) if lines else []
    return header, max(len(lines) - 1, 0)


def export(conn: psycopg.Connection, out_dir: Path, date_from: date, date_to: date,
           all_headway_stops: bool = False,
           story_routes: tuple[str, ...] = STORY_ROUTES) -> list[tuple[Extract, list[str], int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    params = {"date_from": date_from, "date_to": date_to, "all_headway_stops": all_headway_stops,
              "story_routes": [r.lstrip("0") or "0" for r in story_routes]}
    conn.execute("SET work_mem = '256MB'")
    results = []
    for ex in EXTRACTS:
        header, rows = write_csv(conn, ex.sql, params, out_dir / ex.name)
        results.append((ex, header, rows))
    (out_dir / "README.md").write_text(dictionary(results, date_from, date_to))
    return results


def dictionary(results, date_from: date, date_to: date) -> str:
    generated = datetime.now(EDMONTON).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Tableau extracts",
        "",
        f"Generated {generated} by `python -m analysis.export` for service dates {date_from} to {date_to}. "
        "Figures cover ETS buses only unless the file says otherwise. Percentages are included for convenience; "
        "when combining rows in Tableau, sum the counts and divide (for example SUM(n_on_time) / SUM(events)) "
        "so busy routes and quiet routes are weighted correctly.",
        "",
    ]
    for ex, header, rows in results:
        lines += [f"## {ex.name}", "", f"{ex.description} {rows:,} rows.", "", "| column | meaning |", "|---|---|"]
        lines += [f"| {c} | {COLUMN_DOCS.get(c, '')} |" for c in header]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write Tableau-ready CSV extracts")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat)
    parser.add_argument("--out", type=Path, default=ROOT / "exports")
    parser.add_argument("--story-routes", default=",".join(STORY_ROUTES),
                        help="comma-separated route numbers for story_passages.csv")
    parser.add_argument("--all-headway-stops", action="store_true",
                        help="export headways at every stop, not just timepoints and transit centres")
    args = parser.parse_args()
    with db.connect(Config.from_env().database_url) as conn:
        span = conn.execute(
            "SELECT min(coalesce(schedule_date, service_date)), max(coalesce(schedule_date, service_date)) "
            "FROM analysis.stop_passages").fetchone()
        date_from = args.date_from or span[0]
        date_to = args.date_to or span[1]
        if date_from is None:
            parser.error("no stop passages built yet; run python -m analysis.build first")
        routes = tuple(r.strip() for r in args.story_routes.split(",") if r.strip())
        for ex, header, rows in export(conn, args.out, date_from, date_to, args.all_headway_stops, routes):
            print(f"{ex.name:<20}{rows:>10,} rows  {len(header)} columns")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
