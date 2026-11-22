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

CREATE INDEX IF NOT EXISTS stop_passages_schedule_idx ON analysis.stop_passages (schedule_date, trip_id, stop_sequence);
CREATE INDEX IF NOT EXISTS stop_passages_route_idx ON analysis.stop_passages (route_id, service_date);
CREATE INDEX IF NOT EXISTS stop_passages_stop_idx ON analysis.stop_passages (stop_id, observed_ts);

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
       END AS status
FROM analysis.stop_passages p
WHERE p.window_s <= 120;

CREATE VIEW analysis.otp_by_route AS
SELECT e.route_id,
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
       h.actual_headway_s::float8 / h.scheduled_headway_s                     AS headway_ratio,
       extract(hour FROM h.observed_ts AT TIME ZONE 'America/Edmonton')::int  AS local_hour,
       extract(isodow FROM h.schedule_date)::int                              AS iso_dow,
       CASE
           WHEN h.actual_headway_s < 0.25 * h.scheduled_headway_s THEN 'bunched'
           WHEN h.actual_headway_s > 1.5 * h.scheduled_headway_s  THEN 'gap'
           ELSE 'regular'
       END AS spacing
FROM analysis.headways h
WHERE NOT h.data_gap
  AND h.scheduled_headway_s BETWEEN 60 AND 900;

CREATE VIEW analysis.bunching_by_route AS
SELECT e.route_id,
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
