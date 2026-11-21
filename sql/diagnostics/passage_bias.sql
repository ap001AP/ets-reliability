-- Diagnose when ETS advances current_stop_sequence. Run with: psql -v day=YYYY-MM-DD -f this file

\pset pager off

CREATE FUNCTION pg_temp.dist_m(lat1 float8, lon1 float8, lat2 float8, lon2 float8) RETURNS float8
LANGUAGE sql IMMUTABLE AS $$
    SELECT 6371000 * sqrt(power(radians(lat2 - lat1), 2)
                          + power(cos(radians((lat1 + lat2) / 2)) * radians(lon2 - lon1), 2))
$$;

\echo
\echo 1. Ping freshness: seconds between the vehicle GPS fix and the feed header
SELECT count(*) AS pings,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM feed_ts - vehicle_ts)) AS median_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM feed_ts - vehicle_ts)) AS p90_s,
       max(extract(epoch FROM feed_ts - vehicle_ts)) AS max_s
FROM rt.vehicle_positions
WHERE start_date = :'day';

CREATE TEMP TABLE transitions AS
WITH f AS (SELECT service_date, feed_id FROM analysis.feed_by_date WHERE service_date = :'day'),
p AS (
    SELECT v.start_date, v.trip_id, v.vehicle_id, v.vehicle_ts AS ts, v.current_stop_sequence AS seq,
           v.latitude AS lat, v.longitude AS lon, v.speed_mps,
           lag(v.current_stop_sequence) OVER w AS pseq,
           lag(v.latitude) OVER w AS plat,
           lag(v.longitude) OVER w AS plon,
           lag(v.vehicle_ts) OVER w AS pts
    FROM rt.vehicle_positions v
    WHERE v.start_date = :'day' AND v.trip_id IS NOT NULL AND v.current_stop_sequence IS NOT NULL
    WINDOW w AS (PARTITION BY v.start_date, v.trip_id, v.vehicle_id ORDER BY v.vehicle_ts)
)
SELECT p.*, f.feed_id,
       sa.stop_id AS a_id, a.stop_lat AS alat, a.stop_lon AS alon,
       sb.stop_id AS b_id, b.stop_lat AS blat, b.stop_lon AS blon,
       sa.stop_sequence = min(st0.stop_sequence) AS a_is_first_stop
FROM p
JOIN f ON f.service_date = p.start_date
JOIN gtfs.stop_times sa ON sa.feed_id = f.feed_id AND sa.trip_id = p.trip_id AND sa.stop_sequence = p.pseq
JOIN gtfs.stop_times sb ON sb.feed_id = f.feed_id AND sb.trip_id = p.trip_id AND sb.stop_sequence = p.seq
JOIN gtfs.stops a ON a.feed_id = f.feed_id AND a.stop_id = sa.stop_id
JOIN gtfs.stops b ON b.feed_id = f.feed_id AND b.stop_id = sb.stop_id
JOIN gtfs.stop_times st0 ON st0.feed_id = f.feed_id AND st0.trip_id = p.trip_id
WHERE p.seq > p.pseq
  AND p.ts - p.pts <= interval '60 seconds'
  AND NOT EXISTS (
      SELECT 1 FROM gtfs.stop_times x
      WHERE x.feed_id = f.feed_id AND x.trip_id = p.trip_id
        AND x.stop_sequence > p.pseq AND x.stop_sequence < p.seq
  )
GROUP BY p.start_date, p.trip_id, p.vehicle_id, p.ts, p.seq, p.lat, p.lon, p.speed_mps, p.pseq, p.plat, p.plon, p.pts,
         f.feed_id, sa.stop_id, a.stop_lat, a.stop_lon, sb.stop_id, b.stop_lat, b.stop_lon, sa.stop_sequence;

\echo
\echo 2. Where is the bus when the stop number changes from stop A to the next stop B?
\echo    If the number changes after the bus leaves A, the new ping should be past A (closer to B than A is).
SELECT a_is_first_stop,
       count(*) AS transitions,
       round(100.0 * avg((pg_temp.dist_m(lat, lon, blat, blon) < pg_temp.dist_m(alat, alon, blat, blon))::int), 1) AS pct_new_ping_past_a,
       round(100.0 * avg((pg_temp.dist_m(plat, plon, blat, blon) > pg_temp.dist_m(alat, alon, blat, blon))::int), 1) AS pct_prev_ping_before_a,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pg_temp.dist_m(plat, plon, alat, alon))::numeric) AS med_m_prev_ping_to_a,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pg_temp.dist_m(lat, lon, alat, alon))::numeric) AS med_m_new_ping_to_a,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pg_temp.dist_m(alat, alon, blat, blon))::numeric) AS med_m_a_to_b
FROM transitions
GROUP BY a_is_first_stop
ORDER BY a_is_first_stop;

\echo
\echo 3. Delay by position in the trip (from analysis.stop_passages)
WITH firsts AS (
    SELECT feed_id, trip_id, min(stop_sequence) AS first_seq, max(stop_sequence) AS last_seq
    FROM gtfs.stop_times GROUP BY feed_id, trip_id
)
SELECT CASE WHEN e.stop_sequence = f.first_seq THEN '1 first stop'
            WHEN e.stop_sequence >= f.last_seq - 2 THEN '3 last few stops'
            ELSE '2 middle stops' END AS position,
       count(*) AS events,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.delay_s) AS median_delay_s,
       round(100.0 * avg((e.status = 'early')::int), 1) AS pct_early
FROM analysis.stop_events e
JOIN firsts f ON f.feed_id = e.feed_id AND f.trip_id = e.trip_id
WHERE e.service_date = :'day' AND e.status IN ('on_time', 'early', 'late')
GROUP BY 1 ORDER BY 1;

\echo
\echo 4. One sample trip on an express route, ping by ping
WITH pick AS (
    SELECT t.trip_id FROM transitions t
    JOIN gtfs.trips g ON g.feed_id = t.feed_id AND g.trip_id = t.trip_id
    JOIN gtfs.routes r ON r.feed_id = g.feed_id AND r.route_id = g.route_id
    WHERE r.route_short_name LIKE '%X'
    GROUP BY t.trip_id ORDER BY count(*) DESC LIMIT 1
)
SELECT to_char(v.vehicle_ts AT TIME ZONE 'America/Edmonton', 'HH24:MI:SS') AS ping,
       v.current_stop_sequence AS seq, v.stop_id,
       to_char(gtfs.service_ts(v.start_date, st.departure_s) AT TIME ZONE 'America/Edmonton', 'HH24:MI:SS') AS sched_dep_of_that_stop,
       round(pg_temp.dist_m(v.latitude, v.longitude, s.stop_lat, s.stop_lon)::numeric) AS m_to_that_stop,
       round(v.speed_mps::numeric, 1) AS speed_mps
FROM rt.vehicle_positions v
JOIN pick USING (trip_id)
JOIN analysis.feed_by_date f ON f.service_date = v.start_date
JOIN gtfs.stop_times st ON st.feed_id = f.feed_id AND st.trip_id = v.trip_id AND st.stop_sequence = v.current_stop_sequence
JOIN gtfs.stops s ON s.feed_id = f.feed_id AND s.stop_id = st.stop_id
WHERE v.start_date = :'day'
ORDER BY v.vehicle_ts
LIMIT 60;
