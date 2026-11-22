-- Bunching at one stop on one route, and whether lead buses in a bunch are more crowded.
-- Run with: psql -v route=56 -v stop='Century Park' -f this file

\pset pager off

CREATE TEMP VIEW route_headways AS
SELECT h.*,
       s.stop_name,
       t.trip_headsign,
       extract(hour FROM h.observed_ts AT TIME ZONE 'America/Edmonton')::int AS local_hour,
       CASE
           WHEN h.actual_headway_s < 0.25 * h.scheduled_headway_s THEN 'bunched'
           WHEN h.actual_headway_s > 1.5 * h.scheduled_headway_s  THEN 'gap'
           ELSE 'regular'
       END AS spacing
FROM analysis.headways h
JOIN analysis.feed_by_date f ON f.service_date = h.schedule_date
JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = h.route_id
JOIN gtfs.stops s ON s.feed_id = f.feed_id AND s.stop_id = h.stop_id
JOIN gtfs.trips t ON t.feed_id = f.feed_id AND t.trip_id = h.trip_id
WHERE ltrim(r.route_short_name, '0') = ltrim(:'route', '0')
  AND s.stop_name ILIKE '%' || :'stop' || '%'
  AND NOT h.data_gap
  AND h.scheduled_headway_s BETWEEN 60 AND 1800
  AND h.actual_headway_s > 0;

\echo
\echo 1. Spacing at the stop by day, direction and hour
SELECT to_char(schedule_date, 'Dy Mon DD') AS day,
       left(max(trip_headsign), 28) AS toward,
       local_hour AS hour,
       count(*) AS buses,
       round(avg(scheduled_headway_s) / 60.0, 1) AS sched_gap_min,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY actual_headway_s) / 60.0)::numeric, 1) AS median_gap_min,
       round(100.0 * avg((spacing = 'bunched')::int)) AS pct_bunched,
       round(100.0 * avg((spacing = 'gap')::int)) AS pct_gap
FROM route_headways
GROUP BY schedule_date, direction_id, local_hour
ORDER BY schedule_date, direction_id, local_hour;

\echo
\echo 2. Buses arriving together: two or more within 3 minutes of each other
WITH clustered AS (
    SELECT rh.*,
           sum((actual_headway_s > 180)::int) OVER (
               PARTITION BY schedule_date, direction_id, stop_id ORDER BY observed_ts
           ) AS cluster_id
    FROM route_headways rh
),
clusters AS (
    SELECT schedule_date, direction_id, stop_id, cluster_id,
           min(observed_ts) FILTER (WHERE actual_headway_s > 180) AS first_ts,
           max(observed_ts) AS last_ts,
           count(*) FILTER (WHERE actual_headway_s <= 180) AS followers,
           max(trip_headsign) AS toward,
           max(stop_name) AS stop_name,
           round(avg(scheduled_headway_s) / 60.0) AS sched_gap_min
    FROM clustered
    GROUP BY schedule_date, direction_id, stop_id, cluster_id
)
SELECT to_char(first_ts AT TIME ZONE 'America/Edmonton', 'Dy HH24:MI') AS first_bus,
       followers + 1 AS buses_together,
       round(extract(epoch FROM last_ts - first_ts) / 60.0, 1) AS within_min,
       sched_gap_min,
       left(toward, 28) AS toward,
       left(stop_name, 30) AS stop
FROM clusters
WHERE followers >= 1 AND first_ts IS NOT NULL
ORDER BY first_ts
LIMIT 60;

\echo
\echo 3. In a bunched pair, is the lead bus more crowded than the bus right behind it?
\echo    Crowding from the vehicle feed (only some buses report it), nearest ping within 90 s of each passage.
WITH bunched AS (
    SELECT h.schedule_date, h.route_id, h.stop_id, h.trip_id, h.prev_trip_id, h.observed_ts,
           ltrim(r.route_short_name, '0') = ltrim(:'route', '0') AS is_route
    FROM analysis.headways h
    JOIN analysis.feed_by_date f ON f.service_date = h.schedule_date
    JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = h.route_id
    WHERE NOT h.data_gap
      AND h.scheduled_headway_s BETWEEN 60 AND 1800
      AND h.actual_headway_s > 0
      AND h.actual_headway_s < 0.25 * h.scheduled_headway_s
),
pairs AS (
    SELECT b.*, fp.service_date AS f_date, lp.service_date AS l_date, lp.observed_ts AS l_ts
    FROM bunched b
    JOIN analysis.stop_passages fp
      ON fp.schedule_date = b.schedule_date AND fp.trip_id = b.trip_id
     AND fp.stop_id = b.stop_id AND fp.observed_ts = b.observed_ts
    JOIN LATERAL (
        SELECT lp.service_date, lp.observed_ts
        FROM analysis.stop_passages lp
        WHERE lp.schedule_date = b.schedule_date AND lp.trip_id = b.prev_trip_id
          AND lp.stop_id = b.stop_id AND lp.observed_ts <= b.observed_ts
        ORDER BY lp.observed_ts DESC
        LIMIT 1
    ) lp ON true
),
occ AS (
    SELECT p.*,
           (SELECT v.occupancy_status FROM rt.vehicle_positions v
            WHERE v.start_date = p.f_date AND v.trip_id = p.trip_id AND v.occupancy_status IS NOT NULL
              AND v.vehicle_ts BETWEEN p.observed_ts - interval '90 seconds' AND p.observed_ts + interval '90 seconds'
            ORDER BY abs(extract(epoch FROM v.vehicle_ts - p.observed_ts)) LIMIT 1) AS follower_occ,
           (SELECT v.occupancy_status FROM rt.vehicle_positions v
            WHERE v.start_date = p.l_date AND v.trip_id = p.prev_trip_id AND v.occupancy_status IS NOT NULL
              AND v.vehicle_ts BETWEEN p.l_ts - interval '90 seconds' AND p.l_ts + interval '90 seconds'
            ORDER BY abs(extract(epoch FROM v.vehicle_ts - p.l_ts)) LIMIT 1) AS leader_occ
    FROM pairs p
),
levels AS (
    SELECT o.*,
           array_position(ARRAY['EMPTY', 'MANY_SEATS_AVAILABLE', 'FEW_SEATS_AVAILABLE', 'STANDING_ROOM_ONLY',
                                'CRUSHED_STANDING_ROOM_ONLY', 'FULL', 'NOT_ACCEPTING_PASSENGERS'], o.leader_occ) - 1 AS lead_lvl,
           array_position(ARRAY['EMPTY', 'MANY_SEATS_AVAILABLE', 'FEW_SEATS_AVAILABLE', 'STANDING_ROOM_ONLY',
                                'CRUSHED_STANDING_ROOM_ONLY', 'FULL', 'NOT_ACCEPTING_PASSENGERS'], o.follower_occ) - 1 AS follow_lvl
    FROM occ o
),
scoped AS (
    SELECT 'all routes' AS scope, * FROM levels
    UNION ALL
    SELECT 'route ' || :'route', * FROM levels WHERE is_route
)
SELECT scope,
       count(*) AS bunched_pairs,
       count(*) FILTER (WHERE lead_lvl IS NOT NULL AND follow_lvl IS NOT NULL) AS pairs_with_crowding,
       round(100.0 * avg((lead_lvl > follow_lvl)::int) FILTER (WHERE lead_lvl IS NOT NULL AND follow_lvl IS NOT NULL)) AS pct_lead_fuller,
       round(100.0 * avg((lead_lvl = follow_lvl)::int) FILTER (WHERE lead_lvl IS NOT NULL AND follow_lvl IS NOT NULL)) AS pct_same,
       round(100.0 * avg((lead_lvl < follow_lvl)::int) FILTER (WHERE lead_lvl IS NOT NULL AND follow_lvl IS NOT NULL)) AS pct_follower_fuller,
       round(100.0 * avg((lead_lvl >= 2)::int) FILTER (WHERE lead_lvl IS NOT NULL AND follow_lvl IS NOT NULL)) AS pct_lead_few_seats_or_worse,
       round(100.0 * avg((follow_lvl >= 2)::int) FILTER (WHERE lead_lvl IS NOT NULL AND follow_lvl IS NOT NULL)) AS pct_follower_few_seats_or_worse
FROM scoped
GROUP BY scope
ORDER BY scope;
