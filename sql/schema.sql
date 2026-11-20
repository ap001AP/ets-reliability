-- Schema for ETS GTFS Realtime snapshots.

CREATE SCHEMA IF NOT EXISTS rt;

CREATE TABLE IF NOT EXISTS rt.poll_log (
    poll_id       bigserial PRIMARY KEY,
    feed          text        NOT NULL CHECK (feed IN ('vehicle_positions', 'trip_updates', 'alerts')),
    polled_at     timestamptz NOT NULL,
    http_status   integer,
    latency_ms    integer,
    bytes         integer,
    header_ts     timestamptz,
    entity_count  integer,
    rows_written  integer     NOT NULL DEFAULT 0,
    is_stale      boolean     NOT NULL DEFAULT false,
    error         text
);
CREATE INDEX IF NOT EXISTS poll_log_feed_time_idx ON rt.poll_log (feed, polled_at);

CREATE TABLE IF NOT EXISTS rt.vehicle_positions (
    poll_id                     bigint      NOT NULL REFERENCES rt.poll_log (poll_id),
    feed_ts                     timestamptz,
    vehicle_ts                  timestamptz,
    vehicle_id                  text,
    vehicle_label               text,
    trip_id                     text,
    route_id                    text,
    direction_id                smallint,
    start_date                  date,
    start_time                  text,
    trip_schedule_relationship  text,
    latitude                    double precision,
    longitude                   double precision,
    bearing                     real,
    speed_mps                   real,
    current_stop_sequence       integer,
    stop_id                     text,
    current_status              text,
    occupancy_status            text
);
CREATE UNIQUE INDEX IF NOT EXISTS vehicle_positions_dedupe_idx
    ON rt.vehicle_positions (vehicle_id, vehicle_ts);
CREATE INDEX IF NOT EXISTS vehicle_positions_trip_idx
    ON rt.vehicle_positions (start_date, trip_id, vehicle_ts);
CREATE INDEX IF NOT EXISTS vehicle_positions_route_idx
    ON rt.vehicle_positions (route_id, vehicle_ts);

CREATE TABLE IF NOT EXISTS rt.trip_updates (
    poll_id                     bigint      NOT NULL REFERENCES rt.poll_log (poll_id),
    feed_ts                     timestamptz,
    update_ts                   timestamptz,
    trip_id                     text,
    route_id                    text,
    direction_id                smallint,
    start_date                  date,
    start_time                  text,
    trip_schedule_relationship  text,
    vehicle_id                  text,
    trip_delay_s                integer,
    n_stop_updates              integer     NOT NULL,
    is_past                     boolean,
    stop_sequence               integer,
    stop_id                     text,
    arrival_ts                  timestamptz,
    arrival_delay_s             integer,
    departure_ts                timestamptz,
    departure_delay_s           integer,
    stop_schedule_relationship  text
);
CREATE UNIQUE INDEX IF NOT EXISTS trip_updates_dedupe_idx
    ON rt.trip_updates (start_date, trip_id, stop_sequence, vehicle_id, arrival_ts, departure_ts,
                        trip_schedule_relationship, stop_schedule_relationship) NULLS NOT DISTINCT;
CREATE INDEX IF NOT EXISTS trip_updates_route_idx
    ON rt.trip_updates (route_id, feed_ts);

CREATE TABLE IF NOT EXISTS rt.alerts (
    entity_id         text        NOT NULL,
    content_hash      text        NOT NULL,
    first_seen        timestamptz NOT NULL,
    last_seen         timestamptz NOT NULL,
    cause             text,
    effect            text,
    header_text       text,
    description_text  text,
    route_ids         text[],
    stop_ids          text[],
    active_start      timestamptz,
    active_end        timestamptz,
    PRIMARY KEY (entity_id, content_hash)
);

CREATE TABLE IF NOT EXISTS rt.static_snapshots (
    sha256      text PRIMARY KEY,
    fetched_at  timestamptz NOT NULL,
    bytes       integer     NOT NULL,
    path        text        NOT NULL
);
