-- Actual vs scheduled headway for every observed bus at every stop on one schedule date.

WITH feed AS (
    SELECT feed_id FROM analysis.feed_by_date WHERE service_date = %(d)s
),
sched AS (
    SELECT t.route_id, t.direction_id, st.stop_id, st.trip_id, st.stop_sequence,
           gtfs.service_ts(%(d)s, coalesce(st.departure_s, st.arrival_s)) AS scheduled_ts,
           coalesce(st.departure_s, st.arrival_s) AS scheduled_s
    FROM feed f
    JOIN gtfs.service_days sd ON sd.feed_id = f.feed_id AND sd.service_date = %(d)s
    JOIN gtfs.trips t ON t.feed_id = f.feed_id AND t.service_id = sd.service_id
    JOIN gtfs.stop_times st ON st.feed_id = f.feed_id AND st.trip_id = t.trip_id
    WHERE coalesce(st.departure_s, st.arrival_s) IS NOT NULL
),
first_departure AS (
    SELECT trip_id, min(scheduled_s) AS first_s FROM sched GROUP BY trip_id
),
seen AS (
    SELECT DISTINCT start_date, trip_id
    FROM rt.vehicle_positions
    WHERE start_date IN (%(d)s, %(d)s::date + 1) AND trip_id IS NOT NULL
),
tracked AS (
    SELECT DISTINCT s.trip_id
    FROM seen s
    JOIN first_departure fd ON fd.trip_id = s.trip_id
    WHERE s.start_date = %(d)s OR fd.first_s >= 86400
),
ordered AS (
    SELECT s.*, p.observed_ts, (tr.trip_id IS NOT NULL) AS is_tracked,
           row_number() OVER sw AS pos,
           sum(CASE WHEN tr.trip_id IS NOT NULL AND p.observed_ts IS NULL THEN 1 ELSE 0 END) OVER sw AS cum_missing,
           s.scheduled_ts - lag(s.scheduled_ts) OVER sw AS scheduled_headway
    FROM sched s
    LEFT JOIN analysis.stop_passages p
      ON p.schedule_date = %(d)s AND p.trip_id = s.trip_id AND p.stop_sequence = s.stop_sequence
     AND p.window_s <= 120
    LEFT JOIN tracked tr ON tr.trip_id = s.trip_id
    WINDOW sw AS (PARTITION BY s.route_id, s.direction_id, s.stop_id ORDER BY s.scheduled_ts, s.trip_id)
),
observed AS (
    SELECT o.*,
           lag(o.observed_ts) OVER ow AS prev_observed_ts,
           lag(o.trip_id) OVER ow AS prev_trip_id,
           lag(o.cum_missing) OVER ow AS prev_cum_missing
    FROM ordered o
    WHERE o.observed_ts IS NOT NULL
    WINDOW ow AS (PARTITION BY o.route_id, o.direction_id, o.stop_id ORDER BY o.observed_ts)
)
INSERT INTO analysis.headways
    (schedule_date, route_id, direction_id, stop_id, trip_id, stop_sequence, prev_trip_id, observed_ts,
     actual_headway_s, scheduled_headway_s, data_gap)
SELECT %(d)s, route_id, direction_id, stop_id, trip_id, stop_sequence, prev_trip_id, observed_ts,
       round(extract(epoch FROM observed_ts - prev_observed_ts))::int,
       round(extract(epoch FROM scheduled_headway))::int,
       abs(cum_missing - prev_cum_missing) > 0
FROM observed
WHERE prev_observed_ts IS NOT NULL;
