-- Delivery status for every scheduled trip on one schedule date, built in analyzed stages.

-- step: schedule
CREATE TEMP TABLE ts_sched ON COMMIT DROP AS
WITH feed AS (
    SELECT feed_id FROM analysis.feed_by_date WHERE service_date = %(d)s
),
active AS (
    SELECT DISTINCT sd.feed_id, sd.service_id
    FROM gtfs.service_days sd
    JOIN feed f ON f.feed_id = sd.feed_id
    WHERE sd.service_date = %(d)s
)
SELECT t.trip_id, t.route_id, t.direction_id,
       min(coalesce(st.departure_s, st.arrival_s)) AS first_s,
       max(coalesce(st.arrival_s, st.departure_s)) AS last_s,
       count(*) AS stops_scheduled
FROM active a
JOIN gtfs.trips t ON t.feed_id = a.feed_id AND t.service_id = a.service_id
JOIN gtfs.stop_times st ON st.feed_id = t.feed_id AND st.trip_id = t.trip_id
GROUP BY t.trip_id, t.route_id, t.direction_id
HAVING min(coalesce(st.departure_s, st.arrival_s)) IS NOT NULL;

-- step: schedule stats
ANALYZE ts_sched;

-- step: tracked trips
CREATE TEMP TABLE ts_tracked ON COMMIT DROP AS
SELECT DISTINCT s.trip_id
FROM (
    SELECT DISTINCT start_date, trip_id
    FROM rt.vehicle_positions
    WHERE start_date IN (%(d)s, %(d)s::date + 1) AND trip_id IS NOT NULL
) s
JOIN ts_sched sc ON sc.trip_id = s.trip_id
WHERE s.start_date = %(d)s OR sc.first_s >= 86400;

-- step: cancelled trips
CREATE TEMP TABLE ts_cancelled ON COMMIT DROP AS
SELECT DISTINCT c.trip_id
FROM (
    SELECT DISTINCT start_date, trip_id
    FROM rt.trip_updates
    WHERE start_date IN (%(d)s, %(d)s::date + 1)
      AND trip_schedule_relationship = 'CANCELED'
) c
JOIN ts_sched sc ON sc.trip_id = c.trip_id
WHERE c.start_date = %(d)s OR sc.first_s >= 86400;

-- step: observed trips
CREATE TEMP TABLE ts_observed ON COMMIT DROP AS
SELECT trip_id, count(DISTINCT stop_sequence) AS stops_observed
FROM analysis.stop_passages
WHERE schedule_date = %(d)s
GROUP BY trip_id;

-- step: routes in feed
CREATE TEMP TABLE ts_routes_in_feed ON COMMIT DROP AS
SELECT DISTINCT sc.route_id
FROM ts_sched sc
JOIN ts_tracked tr ON tr.trip_id = sc.trip_id;

-- step: polls
CREATE TEMP TABLE ts_polls ON COMMIT DROP AS
SELECT polled_at
FROM rt.poll_log
WHERE feed = 'vehicle_positions' AND error IS NULL
  AND polled_at BETWEEN gtfs.service_ts(%(d)s, 0) - interval '1 hour'
                    AND gtfs.service_ts(%(d)s, 0) + interval '2 days';

-- step: polls index
CREATE INDEX ON ts_polls (polled_at);

-- step: stage stats
ANALYZE ts_tracked, ts_cancelled, ts_observed, ts_routes_in_feed, ts_polls;

-- step: classify
WITH windows AS (
    SELECT sc.*,
           gtfs.service_ts(%(d)s, sc.first_s) AS scheduled_start,
           gtfs.service_ts(%(d)s, sc.last_s) AS scheduled_end
    FROM ts_sched sc
),
coverage AS (
    SELECT w.*,
           (SELECT count(*) FROM ts_polls p
            WHERE p.polled_at BETWEEN w.scheduled_start - interval '5 minutes'
                                  AND w.scheduled_end + interval '5 minutes')::float8
           / greatest(1, extract(epoch FROM (w.scheduled_end - w.scheduled_start) + interval '10 minutes') / 30.0)
           AS feed_coverage
    FROM windows w
)
INSERT INTO analysis.trip_status
    (schedule_date, trip_id, route_id, direction_id, scheduled_start, scheduled_end, stops_scheduled,
     stops_observed, tracked, cancelled, feed_coverage, status)
SELECT %(d)s, c.trip_id, c.route_id, c.direction_id, c.scheduled_start, c.scheduled_end, c.stops_scheduled,
       coalesce(o.stops_observed, 0), tr.trip_id IS NOT NULL, ca.trip_id IS NOT NULL,
       round(least(c.feed_coverage, 1)::numeric, 3),
       CASE
           WHEN coalesce(o.stops_observed, 0) >= 0.5 * c.stops_scheduled THEN 'ran'
           WHEN coalesce(o.stops_observed, 0) > 0                        THEN 'partial'
           WHEN c.feed_coverage < 0.8                                     THEN 'no_data'
           WHEN ca.trip_id IS NOT NULL                                    THEN 'cancelled'
           WHEN tr.trip_id IS NOT NULL                                    THEN 'tracked_not_matched'
           WHEN rf.route_id IS NULL                                       THEN 'not_in_feed'
           ELSE 'missing'
       END
FROM coverage c
LEFT JOIN ts_observed o ON o.trip_id = c.trip_id
LEFT JOIN ts_tracked tr ON tr.trip_id = c.trip_id
LEFT JOIN ts_cancelled ca ON ca.trip_id = c.trip_id
LEFT JOIN ts_routes_in_feed rf ON rf.route_id = c.route_id;
