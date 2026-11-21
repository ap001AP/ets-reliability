-- Inspect one route's delays. Run with: psql -v route=127 -f this file

\pset pager off

\echo
\echo 1. Trips on the route: when each was scheduled to start, and how late it ran
SELECT p.trip_id,
       to_char(min(p.scheduled_ts) AT TIME ZONE 'America/Edmonton', 'Dy HH24:MI') AS scheduled_start,
       count(*) AS events,
       min(p.delay_s) AS min_delay_s,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.delay_s) AS median_delay_s,
       max(p.delay_s) AS max_delay_s,
       (array_agg(p.delay_s ORDER BY p.stop_sequence))[1] AS first_stop_delay_s
FROM analysis.stop_passages p
JOIN gtfs.routes r ON r.feed_id = p.feed_id AND r.route_id = p.route_id
WHERE r.route_short_name = :'route' AND p.window_s <= 120
GROUP BY p.trip_id
ORDER BY min(p.scheduled_ts)
LIMIT 40;

\echo
\echo 2. Delay along the route, by stop (constant offset suggests a schedule mismatch; growing delay suggests traffic)
SELECT p.direction_id, p.stop_sequence, max(s.stop_name) AS stop_name, count(*) AS events,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.delay_s) AS median_delay_s
FROM analysis.stop_passages p
JOIN gtfs.routes r ON r.feed_id = p.feed_id AND r.route_id = p.route_id
JOIN gtfs.stops s ON s.feed_id = p.feed_id AND s.stop_id = p.stop_id
WHERE r.route_short_name = :'route' AND p.window_s <= 120
GROUP BY p.direction_id, p.stop_sequence
ORDER BY p.direction_id, p.stop_sequence;

\echo
\echo 3. Service alerts naming the route
SELECT a.effect, a.cause, left(a.header_text, 90) AS header,
       to_char(a.first_seen AT TIME ZONE 'America/Edmonton', 'Mon DD HH24:MI') AS first_seen
FROM rt.alerts a
JOIN gtfs.routes r ON r.route_id = ANY (a.route_ids)
WHERE r.route_short_name = :'route'
GROUP BY a.entity_id, a.content_hash, a.effect, a.cause, a.header_text, a.first_seen
ORDER BY a.first_seen;
