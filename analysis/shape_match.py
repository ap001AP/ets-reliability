"""Match vehicle GPS pings to route shapes and time when each bus crossed each stop."""

from __future__ import annotations

import io
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np
import psycopg

log = logging.getLogger("analysis.shape_match")

EARTH_R = 6_371_000.0
LAT0 = 53.55
DEPART_OFFSET_M = 30.0
MAX_OFFROUTE_M = 100.0
MAX_SPEED_MPS = 30.0
BACKTRACK_M = 50.0
TIE_TOL_M = 25.0
RESET_AFTER = 3
ARRIVE_OFFSET_M = 15.0
FIRST_STOP_WAIT_M = 300.0
STATIONARY_M = 20.0


def to_xy(lat, lon) -> np.ndarray:
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    x = EARTH_R * np.radians(lon) * math.cos(math.radians(LAT0))
    y = EARTH_R * np.radians(lat)
    return np.column_stack([x, y])


@dataclass
class Shape:
    a: np.ndarray
    ab: np.ndarray
    seg_len: np.ndarray
    cum: np.ndarray

    @property
    def length(self) -> float:
        return float(self.cum[-1] + self.seg_len[-1])


def build_shape(xy: np.ndarray) -> Shape | None:
    keep = np.ones(len(xy), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(xy, axis=0)) > 1e-6, axis=1)
    xy = xy[keep]
    if len(xy) < 2:
        return None
    a, b = xy[:-1], xy[1:]
    ab = b - a
    seg_len = np.hypot(ab[:, 0], ab[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg_len)[:-1]])
    return Shape(a=a, ab=ab, seg_len=seg_len, cum=cum)


def project(shape: Shape, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ap = pts[:, None, :] - shape.a[None, :, :]
    t = np.clip((ap * shape.ab[None, :, :]).sum(axis=2) / (shape.seg_len ** 2)[None, :], 0.0, 1.0)
    proj = shape.a[None, :, :] + t[..., None] * shape.ab[None, :, :]
    dist = np.hypot(*(pts[:, None, :] - proj).transpose(2, 0, 1))
    along = shape.cum[None, :] + t * shape.seg_len[None, :]
    return along, dist


def locate_stops(shape: Shape, stop_xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    along, dist = project(shape, stop_xy)
    out = np.empty(len(stop_xy))
    offset = np.empty(len(stop_xy))
    prev = -np.inf
    for i in range(len(stop_xy)):
        mask = along[i] >= prev - 10.0
        if not mask.any():
            mask = np.ones_like(mask)
        dmin = dist[i][mask].min()
        cand = mask & (dist[i] <= dmin + TIE_TOL_M)
        j = np.flatnonzero(cand)[np.argmin(along[i][cand])]
        out[i], offset[i] = along[i, j], dist[i, j]
        prev = out[i]
    return out, offset


def locate_pings(shape: Shape, ping_xy: np.ndarray, t: np.ndarray, reset_after: int = RESET_AFTER) -> np.ndarray:
    along, dist = project(shape, ping_xy)
    n = len(ping_xy)
    out = np.full(n, np.nan)
    prev_a = prev_t = None
    placed: list[int] = []
    misses: list[int] = []
    i = 0
    while i < n:
        if prev_a is None:
            mask = np.ones(along.shape[1], dtype=bool)
        else:
            hi = prev_a + MAX_SPEED_MPS * max(t[i] - prev_t, 0.0) + 100.0
            mask = (along[i] >= prev_a - BACKTRACK_M) & (along[i] <= hi)
        if mask.any() and dist[i][mask].min() <= MAX_OFFROUTE_M:
            dmin = dist[i][mask].min()
            if prev_a is None:
                cand = mask & (dist[i] <= dmin + TIE_TOL_M)
                a = along[i][cand].min()
            else:
                a = max(along[i][mask][np.argmin(dist[i][mask])], prev_a)
            out[i] = a
            placed.append(i)
            misses = []
            prev_a, prev_t = a, t[i]
            i += 1
            continue
        if prev_a is not None and dist[i].min() <= MAX_OFFROUTE_M:
            misses.append(i)
            if len(misses) >= reset_after:
                restart = placed[0] + 1
                out[placed] = np.nan
                placed = []
                prev_a = prev_t = None
                i = restart
                misses = []
                continue
        i += 1
    return out


def _cross(t: np.ndarray, a: np.ndarray, target: float) -> tuple[float, float, float, float] | None:
    j = int(np.searchsorted(a, target, side="left"))
    if j == 0 or j >= len(a):
        return None
    i = j - 1
    span = a[j] - a[i]
    if span <= 0:
        return None
    when = t[i] + (target - a[i]) / span * (t[j] - t[i])
    return float(when), float(t[j] - t[i]), float(a[i]), float(a[j])


def crossings(ping_t: np.ndarray, ping_a: np.ndarray, stop_a: np.ndarray,
              offset_m: float = DEPART_OFFSET_M) -> list[tuple[int, float, float, int, str]]:
    ok = ~np.isnan(ping_a)
    t, a = ping_t[ok], ping_a[ok]
    if len(a) < 2 or len(stop_a) == 0:
        return []
    last = len(stop_a) - 1
    targets = np.empty(len(stop_a))
    events = []
    for k in range(len(stop_a)):
        if k == last and last > 0:
            targets[k] = stop_a[k] - ARRIVE_OFFSET_M
            events.append("arrival")
        else:
            targets[k] = stop_a[k] + offset_m
            events.append("departure")
    waiting_past_first = (a[0] >= targets[0] and a[0] - stop_a[0] <= FIRST_STOP_WAIT_M
                          and a[1] - a[0] <= STATIONARY_M)
    if waiting_past_first:
        targets[0] = a[0] + offset_m
    out = []
    for k, target in enumerate(targets):
        hit = _cross(t, a, target)
        if hit is None:
            continue
        when, window, lo, hi = hit
        n = int(((targets > lo) & (targets <= hi)).sum())
        out.append((k, when, window, n, events[k]))
    return out


TRIPS_SQL = """
SELECT DISTINCT v.trip_id, f.feed_id, t.shape_id
FROM rt.vehicle_positions v
JOIN analysis.feed_by_date f ON f.service_date = v.start_date
JOIN gtfs.trips t ON t.feed_id = f.feed_id AND t.trip_id = v.trip_id
WHERE v.start_date = %s AND t.shape_id IS NOT NULL
"""

STOPS_SQL = """
SELECT st.trip_id, st.stop_sequence, s.stop_lat, s.stop_lon
FROM gtfs.stop_times st
JOIN gtfs.stops s ON s.feed_id = st.feed_id AND s.stop_id = st.stop_id
WHERE st.feed_id = %s AND st.trip_id = ANY(%s)
ORDER BY st.trip_id, st.stop_sequence
"""

SHAPES_SQL = """
SELECT shape_id, shape_pt_lat, shape_pt_lon
FROM gtfs.shapes
WHERE feed_id = %s AND shape_id = ANY(%s)
ORDER BY shape_id, shape_pt_sequence
"""

PINGS_SQL = """
SELECT trip_id, vehicle_id, extract(epoch FROM vehicle_ts)::float8, latitude, longitude
FROM rt.vehicle_positions
WHERE start_date = %s AND trip_id IS NOT NULL AND vehicle_ts IS NOT NULL
  AND latitude IS NOT NULL AND longitude IS NOT NULL
ORDER BY trip_id, vehicle_id, vehicle_ts
"""

STAGE_SQL = """
CREATE TEMP TABLE passage_stage (
    service_date     date,
    trip_id          text,
    stop_sequence    integer,
    feed_id          text,
    vehicle_id       text,
    observed_epoch   float8,
    window_s         integer,
    stops_in_window  integer,
    event            text
) ON COMMIT DROP
"""

INSERT_SQL = """
WITH joined AS (
    SELECT s.*, t.route_id, t.direction_id, st.stop_id, st.timepoint,
           CASE WHEN s.event = 'arrival' THEN coalesce(st.arrival_s, st.departure_s)
                ELSE coalesce(st.departure_s, st.arrival_s) END AS scheduled_s
    FROM passage_stage s
    JOIN gtfs.trips t ON t.feed_id = s.feed_id AND t.trip_id = s.trip_id
    JOIN gtfs.stop_times st ON st.feed_id = s.feed_id AND st.trip_id = s.trip_id AND st.stop_sequence = s.stop_sequence
),
dated AS (
    SELECT j.*, d.schedule_date, gtfs.service_ts(d.schedule_date, j.scheduled_s) AS scheduled_ts
    FROM joined j
    CROSS JOIN LATERAL (
        SELECT x AS schedule_date
        FROM (VALUES (j.service_date), (j.service_date - 1)) AS v (x)
        ORDER BY abs(j.observed_epoch - extract(epoch FROM gtfs.service_ts(x, j.scheduled_s))) NULLS LAST
        LIMIT 1
    ) d
)
INSERT INTO analysis.stop_passages
    (service_date, trip_id, stop_sequence, feed_id, vehicle_id, route_id, direction_id, stop_id,
     timepoint, observed_ts, window_s, stops_in_window, event, schedule_date, scheduled_ts, delay_s)
SELECT DISTINCT ON (service_date, trip_id, stop_sequence)
       service_date, trip_id, stop_sequence, feed_id, vehicle_id, route_id, direction_id, stop_id,
       timepoint, to_timestamp(observed_epoch), window_s, stops_in_window, event, schedule_date, scheduled_ts,
       round(observed_epoch - extract(epoch FROM scheduled_ts))::int
FROM dated
ORDER BY service_date, trip_id, stop_sequence, window_s
"""


@dataclass
class DayStats:
    trips_seen: int = 0
    trips_matched: int = 0
    pings: int = 0
    pings_on_route: int = 0
    passages: int = 0
    stops_far_from_shape: int = 0


def build_day(conn: psycopg.Connection, day: date) -> DayStats:
    stats = DayStats()
    trips = conn.execute(TRIPS_SQL, (day,)).fetchall()
    if not trips:
        with conn.transaction():
            conn.execute("DELETE FROM analysis.stop_passages WHERE service_date = %s", (day,))
        return stats

    feed_id = trips[0][1]
    trip_shape = {trip_id: shape_id for trip_id, _, shape_id in trips}
    trip_ids = list(trip_shape)

    stops = defaultdict(lambda: ([], [], []))
    for trip_id, seq, lat, lon in conn.execute(STOPS_SQL, (feed_id, trip_ids)):
        bucket = stops[trip_id]
        bucket[0].append(seq)
        bucket[1].append(lat)
        bucket[2].append(lon)

    shape_pts = defaultdict(lambda: ([], []))
    for shape_id, lat, lon in conn.execute(SHAPES_SQL, (feed_id, list(set(trip_shape.values())))):
        shape_pts[shape_id][0].append(lat)
        shape_pts[shape_id][1].append(lon)
    shapes = {sid: build_shape(to_xy(lat, lon)) for sid, (lat, lon) in shape_pts.items()}

    stop_cache: dict[tuple, np.ndarray] = {}
    rows = io.StringIO()

    def flush_group(trip_id, vehicle_id, t, lat, lon):
        shape = shapes.get(trip_shape.get(trip_id))
        if shape is None or trip_id not in stops:
            return
        seqs, slat, slon = stops[trip_id]
        key = (trip_shape[trip_id], tuple(seqs), tuple(slat))
        if key not in stop_cache:
            stop_a, offset = locate_stops(shape, to_xy(slat, slon))
            stats.stops_far_from_shape += int((offset > MAX_OFFROUTE_M).sum())
            stop_cache[key] = stop_a
        stop_a = stop_cache[key]
        t_arr = np.asarray(t)
        ping_a = locate_pings(shape, to_xy(lat, lon), t_arr)
        stats.pings += len(t_arr)
        stats.pings_on_route += int((~np.isnan(ping_a)).sum())
        found = crossings(t_arr, ping_a, stop_a)
        if found:
            stats.trips_matched += 1
        for k, when, window, n, event in found:
            rows.write(f"{day}\t{trip_id}\t{seqs[k]}\t{feed_id}\t{vehicle_id}\t{when:.3f}\t{round(window)}\t{n}\t{event}\n")
            stats.passages += 1

    current = None
    t, lat, lon = [], [], []
    seen_trips = set()
    with conn.transaction(), conn.cursor(name="pings") as cur:
        cur.itersize = 50_000
        cur.execute(PINGS_SQL, (day,))
        for trip_id, vehicle_id, epoch, plat, plon in cur:
            key = (trip_id, vehicle_id)
            if key != current:
                if current is not None:
                    flush_group(*current, t, lat, lon)
                current, t, lat, lon = key, [], [], []
                seen_trips.add(trip_id)
            t.append(epoch)
            lat.append(plat)
            lon.append(plon)
        if current is not None:
            flush_group(*current, t, lat, lon)
    stats.trips_seen = len(seen_trips)

    rows.seek(0)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM analysis.stop_passages WHERE service_date = %s", (day,))
        cur.execute(STAGE_SQL)
        with cur.copy("COPY passage_stage FROM STDIN") as copy:
            while chunk := rows.read(1 << 20):
                copy.write(chunk)
        cur.execute(INSERT_SQL)
        cur.execute("ANALYZE analysis.stop_passages")
    return stats
