-- Stop passages inferred from vehicle positions, and on-time performance views.

CREATE SCHEMA IF NOT EXISTS analysis;

CREATE OR REPLACE FUNCTION analysis.dist_m(lat1 float8, lon1 float8, lat2 float8, lon2 float8)
RETURNS float8
LANGUAGE sql IMMUTABLE
AS $$
    SELECT 6371000 * sqrt(power(radians(lat2 - lat1), 2)
                          + power(cos(radians((lat1 + lat2) / 2)) * radians(lon2 - lon1), 2))
$$;

CREATE OR REPLACE VIEW analysis.feed_by_date AS
WITH candidates AS (
    SELECT DISTINCT sd.service_date, sd.feed_id, f.fetched_at,
           f.fetched_at < ((sd.service_date + 1)::timestamp AT TIME ZONE 'America/Edmonton') AS fetched_by_day_end
    FROM gtfs.service_days sd
    JOIN gtfs.feeds f USING (feed_id)
)
SELECT DISTINCT ON (service_date) service_date, feed_id
FROM candidates
ORDER BY service_date,
         fetched_by_day_end DESC,
         CASE WHEN fetched_by_day_end THEN -extract(epoch FROM fetched_at) ELSE extract(epoch FROM fetched_at) END;

CREATE OR REPLACE VIEW analysis.route_agency AS
SELECT r.feed_id, r.route_id, r.agency_id, a.agency_name,
       CASE
           WHEN a.agency_name IS NOT NULL THEN a.agency_name ILIKE '%Edmonton Transit%'
           WHEN r.agency_id IS NOT NULL THEN r.agency_id = '1'
           ELSE true
       END AS is_ets
FROM gtfs.routes r
LEFT JOIN LATERAL (
    SELECT ag.agency_name FROM gtfs.agency ag
    WHERE ag.feed_id = r.feed_id AND ag.agency_id IS NOT DISTINCT FROM r.agency_id
    LIMIT 1
) a ON true;

CREATE TABLE IF NOT EXISTS analysis.stop_passages (
    service_date     date        NOT NULL,
    trip_id          text        NOT NULL,
    stop_sequence    integer     NOT NULL,
    feed_id          text        NOT NULL,
    vehicle_id       text,
    route_id         text,
    direction_id     smallint,
    stop_id          text,
    timepoint        smallint,
    observed_ts      timestamptz NOT NULL,
    window_s         integer     NOT NULL,
    stops_in_window  integer     NOT NULL,
    scheduled_ts     timestamptz,
    delay_s          integer,
    PRIMARY KEY (service_date, trip_id, stop_sequence)
);
ALTER TABLE analysis.stop_passages ADD COLUMN IF NOT EXISTS schedule_date date;
ALTER TABLE analysis.stop_passages ADD COLUMN IF NOT EXISTS event text NOT NULL DEFAULT 'departure';

CREATE INDEX IF NOT EXISTS stop_passages_schedule_idx ON analysis.stop_passages (schedule_date, trip_id, stop_sequence);
CREATE INDEX IF NOT EXISTS stop_passages_route_idx ON analysis.stop_passages (route_id, service_date);
CREATE INDEX IF NOT EXISTS stop_passages_stop_idx ON analysis.stop_passages (stop_id, observed_ts);

DROP VIEW IF EXISTS analysis.ets_standard_by_route;
DROP VIEW IF EXISTS analysis.ets_standard_events;
DROP VIEW IF EXISTS analysis.otp_by_route;
DROP VIEW IF EXISTS analysis.stop_events;

CREATE VIEW analysis.stop_events AS
SELECT p.*,
       (p.observed_ts AT TIME ZONE 'America/Edmonton')                       AS observed_local,
       extract(hour FROM p.observed_ts AT TIME ZONE 'America/Edmonton')::int AS local_hour,
       extract(isodow FROM coalesce(p.schedule_date, p.service_date))::int   AS iso_dow,
       CASE
           WHEN p.delay_s IS NULL                 THEN 'unscheduled'
           WHEN abs(p.delay_s) > 3 * 3600         THEN 'invalid'
           WHEN p.delay_s < -60                   THEN 'early'
           WHEN p.delay_s > 300                   THEN 'late'
           ELSE 'on_time'
       END AS status,
       coalesce(ra.is_ets, true) AS is_ets,
       coalesce(ra.agency_name, ra.agency_id) AS agency
FROM analysis.stop_passages p
LEFT JOIN analysis.route_agency ra ON ra.feed_id = p.feed_id AND ra.route_id = p.route_id
WHERE p.window_s <= 120
  AND p.event = 'departure';

CREATE VIEW analysis.otp_by_route AS
SELECT e.route_id,
       bool_or(e.is_ets)                                                          AS is_ets,
       max(e.agency)                                                              AS agency,
       max(r.route_short_name)                                                    AS route_short_name,
       max(r.route_long_name)                                                     AS route_long_name,
       count(*)                                                                   AS events,
       round(100.0 * avg((e.status = 'on_time')::int), 1)                         AS pct_on_time,
       round(100.0 * avg((e.status = 'early')::int), 1)                           AS pct_early,
       round(100.0 * avg((e.status = 'late')::int), 1)                            AS pct_late,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.delay_s)                     AS median_delay_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY e.delay_s)                     AS p90_delay_s,
       count(*) FILTER (WHERE e.timepoint = 1)                                    AS timepoint_events,
       round(100.0 * avg((e.status = 'on_time')::int) FILTER (WHERE e.timepoint = 1), 1) AS pct_on_time_timepoints
FROM analysis.stop_events e
LEFT JOIN gtfs.routes r ON r.feed_id = e.feed_id AND r.route_id = e.route_id
WHERE e.status IN ('on_time', 'early', 'late')
GROUP BY e.route_id;

CREATE TABLE IF NOT EXISTS analysis.headways (
    schedule_date        date        NOT NULL,
    route_id             text        NOT NULL,
    direction_id         smallint,
    stop_id              text        NOT NULL,
    trip_id              text        NOT NULL,
    stop_sequence        integer     NOT NULL,
    prev_trip_id         text        NOT NULL,
    observed_ts          timestamptz NOT NULL,
    actual_headway_s     integer     NOT NULL,
    scheduled_headway_s  integer,
    data_gap             boolean     NOT NULL,
    PRIMARY KEY (schedule_date, trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS headways_route_idx ON analysis.headways (route_id, schedule_date);

DROP VIEW IF EXISTS analysis.bunching_by_route;
DROP VIEW IF EXISTS analysis.headway_events;

CREATE VIEW analysis.headway_events AS
SELECT h.*,
       coalesce(ra.is_ets, true)                                              AS is_ets,
       coalesce(ra.agency_name, ra.agency_id)                                 AS agency,
       h.actual_headway_s::float8 / h.scheduled_headway_s                     AS headway_ratio,
       extract(hour FROM h.observed_ts AT TIME ZONE 'America/Edmonton')::int  AS local_hour,
       extract(isodow FROM h.schedule_date)::int                              AS iso_dow,
       CASE
           WHEN h.actual_headway_s < 0.25 * h.scheduled_headway_s THEN 'bunched'
           WHEN h.actual_headway_s > 1.5 * h.scheduled_headway_s  THEN 'gap'
           ELSE 'regular'
       END AS spacing
FROM analysis.headways h
LEFT JOIN analysis.feed_by_date f ON f.service_date = h.schedule_date
LEFT JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = h.route_id
WHERE NOT h.data_gap
  AND h.scheduled_headway_s BETWEEN 60 AND 900;

CREATE VIEW analysis.bunching_by_route AS
SELECT e.route_id,
       bool_or(e.is_ets)                                                  AS is_ets,
       max(e.agency)                                                      AS agency,
       max(r.route_short_name)                                            AS route_short_name,
       max(r.route_long_name)                                             AS route_long_name,
       count(*)                                                           AS events,
       round(100.0 * avg((e.spacing = 'bunched')::int), 1)                AS pct_bunched,
       round(100.0 * avg((e.spacing = 'gap')::int), 1)                    AS pct_gap,
       round((stddev_samp(e.actual_headway_s - e.scheduled_headway_s)
              / nullif(avg(e.scheduled_headway_s), 0))::numeric, 2)       AS headway_cv,
       round((sum(e.actual_headway_s::float8 ^ 2) / (2 * sum(e.actual_headway_s))
              - sum(e.scheduled_headway_s::float8 ^ 2) / (2 * sum(e.scheduled_headway_s)))::numeric) AS excess_wait_s
FROM analysis.headway_events e
LEFT JOIN analysis.feed_by_date f ON f.service_date = e.schedule_date
LEFT JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = e.route_id
WHERE e.actual_headway_s > 0
GROUP BY e.route_id;

CREATE TABLE IF NOT EXISTS analysis.trip_status (
    schedule_date    date        NOT NULL,
    trip_id          text        NOT NULL,
    route_id         text        NOT NULL,
    direction_id     smallint,
    scheduled_start  timestamptz NOT NULL,
    scheduled_end    timestamptz NOT NULL,
    stops_scheduled  integer     NOT NULL,
    stops_observed   integer     NOT NULL,
    tracked          boolean     NOT NULL,
    cancelled        boolean     NOT NULL,
    feed_coverage    numeric     NOT NULL,
    status           text        NOT NULL,
    PRIMARY KEY (schedule_date, trip_id)
);
CREATE INDEX IF NOT EXISTS trip_status_route_idx ON analysis.trip_status (route_id, schedule_date);
ALTER TABLE analysis.trip_status DROP CONSTRAINT IF EXISTS trip_status_status_check;
ALTER TABLE analysis.trip_status ADD CONSTRAINT trip_status_status_check
    CHECK (status IN ('ran', 'partial', 'cancelled', 'tracked_not_matched', 'missing', 'not_in_feed', 'no_data'));

DROP VIEW IF EXISTS analysis.delivery_by_route;

CREATE VIEW analysis.delivery_by_route AS
SELECT s.route_id,
       bool_or(coalesce(ra.is_ets, true))                                        AS is_ets,
       max(coalesce(ra.agency_name, ra.agency_id))                               AS agency,
       max(r.route_short_name)                                                   AS route_short_name,
       max(r.route_long_name)                                                    AS route_long_name,
       count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed'))        AS trips_judged,
       count(*) FILTER (WHERE s.status = 'ran')                                  AS ran,
       count(*) FILTER (WHERE s.status = 'partial')                              AS partial,
       count(*) FILTER (WHERE s.status = 'cancelled')                            AS cancelled,
       count(*) FILTER (WHERE s.status = 'tracked_not_matched')                  AS tracked_not_matched,
       count(*) FILTER (WHERE s.status = 'missing')                              AS missing,
       count(*) FILTER (WHERE s.status = 'not_in_feed')                          AS not_in_feed,
       round(100.0 * count(*) FILTER (WHERE s.status IN ('ran', 'partial'))
             / nullif(count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')), 0), 1) AS pct_delivered,
       round(100.0 * count(*) FILTER (WHERE s.status = 'missing')
             / nullif(count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')), 0), 1) AS pct_missing
FROM analysis.trip_status s
LEFT JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
LEFT JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = s.route_id
LEFT JOIN analysis.route_agency ra ON ra.feed_id = f.feed_id AND ra.route_id = s.route_id
GROUP BY s.route_id;

DROP VIEW IF EXISTS analysis.ets_standard_by_route;
DROP VIEW IF EXISTS analysis.ets_standard_events;

CREATE VIEW analysis.ets_standard_events AS
SELECT p.*,
       CASE
           WHEN p.event = 'departure' THEN
               CASE WHEN p.delay_s < 0 THEN 'early' WHEN p.delay_s > 180 THEN 'late' ELSE 'on_time' END
           ELSE
               CASE WHEN p.delay_s < -300 THEN 'early' WHEN p.delay_s > 60 THEN 'late' ELSE 'on_time' END
       END AS ets_status,
       CASE
           WHEN p.event = 'departure' THEN p.delay_s BETWEEN -30 AND 210
           ELSE p.delay_s BETWEEN -330 AND 90
       END AS on_time_allowing_30s,
       coalesce(ra.is_ets, true) AS is_ets,
       coalesce(ra.agency_name, ra.agency_id) AS agency
FROM analysis.stop_passages p
LEFT JOIN analysis.route_agency ra ON ra.feed_id = p.feed_id AND ra.route_id = p.route_id
WHERE p.window_s <= 120
  AND p.delay_s IS NOT NULL
  AND abs(p.delay_s) <= 3 * 3600
  AND ((p.event = 'departure' AND p.timepoint = 1) OR p.event = 'arrival');

CREATE VIEW analysis.ets_standard_by_route AS
SELECT e.route_id,
       bool_or(e.is_ets)                                                              AS is_ets,
       max(e.agency)                                                                  AS agency,
       max(r.route_short_name)                                                        AS route_short_name,
       max(r.route_long_name)                                                         AS route_long_name,
       count(*)                                                                       AS departures,
       round(100.0 * avg((e.ets_status = 'on_time')::int), 1)                         AS pct_on_time,
       round(100.0 * avg((e.ets_status = 'early')::int), 1)                           AS pct_early,
       round(100.0 * avg((e.ets_status = 'late')::int), 1)                            AS pct_late,
       round(100.0 * avg(e.on_time_allowing_30s::int), 1)                             AS pct_on_time_allowing_30s
FROM analysis.ets_standard_events e
LEFT JOIN gtfs.routes r ON r.feed_id = e.feed_id AND r.route_id = e.route_id
WHERE e.event = 'departure'
GROUP BY e.route_id;
