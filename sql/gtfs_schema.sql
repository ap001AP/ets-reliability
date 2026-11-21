-- Static GTFS schedule, versioned by feed snapshot.

CREATE SCHEMA IF NOT EXISTS gtfs;

CREATE TABLE IF NOT EXISTS gtfs.feeds (
    feed_id          text PRIMARY KEY,
    sha256           text        NOT NULL UNIQUE,
    fetched_at       timestamptz NOT NULL,
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    source_path      text        NOT NULL,
    feed_version     text,
    feed_start_date  date,
    feed_end_date    date
);

CREATE TABLE IF NOT EXISTS gtfs.routes (
    feed_id           text NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    route_id          text NOT NULL,
    agency_id         text,
    route_short_name  text,
    route_long_name   text,
    route_desc        text,
    route_type        integer,
    route_color       text,
    route_text_color  text,
    route_sort_order  integer,
    PRIMARY KEY (feed_id, route_id)
);

CREATE TABLE IF NOT EXISTS gtfs.trips (
    feed_id                text NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    trip_id                text NOT NULL,
    route_id               text NOT NULL,
    service_id             text NOT NULL,
    trip_headsign          text,
    trip_short_name        text,
    direction_id           smallint,
    block_id               text,
    shape_id               text,
    wheelchair_accessible  smallint,
    bikes_allowed          smallint,
    PRIMARY KEY (feed_id, trip_id)
);
CREATE INDEX IF NOT EXISTS trips_route_idx ON gtfs.trips (feed_id, route_id);

CREATE TABLE IF NOT EXISTS gtfs.stops (
    feed_id              text NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    stop_id              text NOT NULL,
    stop_code            text,
    stop_name            text,
    stop_desc            text,
    stop_lat             double precision,
    stop_lon             double precision,
    zone_id              text,
    location_type        smallint,
    parent_station       text,
    wheelchair_boarding  smallint,
    platform_code        text,
    PRIMARY KEY (feed_id, stop_id)
);

CREATE TABLE IF NOT EXISTS gtfs.stop_times (
    feed_id              text    NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    trip_id              text    NOT NULL,
    stop_sequence        integer NOT NULL,
    stop_id              text    NOT NULL,
    arrival_time         text,
    departure_time       text,
    arrival_s            integer,
    departure_s          integer,
    stop_headsign        text,
    pickup_type          smallint,
    drop_off_type        smallint,
    shape_dist_traveled  double precision,
    timepoint            smallint,
    PRIMARY KEY (feed_id, trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS stop_times_stop_idx ON gtfs.stop_times (feed_id, stop_id);

CREATE TABLE IF NOT EXISTS gtfs.calendar (
    feed_id     text     NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    service_id  text     NOT NULL,
    monday      smallint NOT NULL,
    tuesday     smallint NOT NULL,
    wednesday   smallint NOT NULL,
    thursday    smallint NOT NULL,
    friday      smallint NOT NULL,
    saturday    smallint NOT NULL,
    sunday      smallint NOT NULL,
    start_date  date     NOT NULL,
    end_date    date     NOT NULL,
    PRIMARY KEY (feed_id, service_id)
);

CREATE TABLE IF NOT EXISTS gtfs.calendar_dates (
    feed_id         text     NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    service_id      text     NOT NULL,
    date            date     NOT NULL,
    exception_type  smallint NOT NULL,
    PRIMARY KEY (feed_id, service_id, date)
);

CREATE TABLE IF NOT EXISTS gtfs.shapes (
    feed_id              text             NOT NULL REFERENCES gtfs.feeds (feed_id) ON DELETE CASCADE,
    shape_id             text             NOT NULL,
    shape_pt_sequence    integer          NOT NULL,
    shape_pt_lat         double precision NOT NULL,
    shape_pt_lon         double precision NOT NULL,
    shape_dist_traveled  double precision,
    PRIMARY KEY (feed_id, shape_id, shape_pt_sequence)
);

CREATE OR REPLACE FUNCTION gtfs.service_ts(service_date date, seconds integer)
RETURNS timestamptz
LANGUAGE sql IMMUTABLE
AS $$
    SELECT ((service_date + time '12:00') AT TIME ZONE 'America/Edmonton')
           - interval '12 hours'
           + make_interval(secs => seconds)
$$;

CREATE OR REPLACE VIEW gtfs.service_days AS
WITH regular AS (
    SELECT c.feed_id, c.service_id, d::date AS service_date
    FROM gtfs.calendar c
    CROSS JOIN LATERAL generate_series(c.start_date, c.end_date, interval '1 day') AS d
    WHERE CASE extract(isodow FROM d)
              WHEN 1 THEN c.monday    WHEN 2 THEN c.tuesday  WHEN 3 THEN c.wednesday
              WHEN 4 THEN c.thursday  WHEN 5 THEN c.friday   WHEN 6 THEN c.saturday
              ELSE c.sunday
          END = 1
)
SELECT r.feed_id, r.service_id, r.service_date
FROM regular r
WHERE NOT EXISTS (
    SELECT 1 FROM gtfs.calendar_dates x
    WHERE x.feed_id = r.feed_id AND x.service_id = r.service_id
      AND x.date = r.service_date AND x.exception_type = 2
)
UNION
SELECT feed_id, service_id, date
FROM gtfs.calendar_dates
WHERE exception_type = 1;
