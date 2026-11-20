-- Keep one row per distinct trip-update prediction.

BEGIN;

DELETE FROM rt.trip_updates
WHERE ctid IN (
    SELECT ctid FROM (
        SELECT ctid,
               row_number() OVER (
                   PARTITION BY start_date, trip_id, stop_sequence, vehicle_id, arrival_ts, departure_ts,
                                trip_schedule_relationship, stop_schedule_relationship
                   ORDER BY poll_id
               ) AS rn
        FROM rt.trip_updates
    ) ranked
    WHERE rn > 1
);

DROP INDEX IF EXISTS rt.trip_updates_trip_stop_idx;

CREATE UNIQUE INDEX IF NOT EXISTS trip_updates_dedupe_idx
    ON rt.trip_updates (start_date, trip_id, stop_sequence, vehicle_id, arrival_ts, departure_ts,
                        trip_schedule_relationship, stop_schedule_relationship) NULLS NOT DISTINCT;

COMMIT;
