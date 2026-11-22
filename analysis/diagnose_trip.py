"""Explain why trips on a route were tracked in the live feed but not matched to their route shape."""

from __future__ import annotations

import argparse
from datetime import date, datetime

import numpy as np

from analysis import shape_match as sm
from collector import db
from collector.config import Config
from collector.tz import EDMONTON



def fmt_time(epoch: float | None) -> str:
    if epoch is None or np.isnan(epoch):
        return "-"
    return datetime.fromtimestamp(epoch, EDMONTON).strftime("%H:%M")


def routes_summary(conn, day: date | None) -> None:
    rows = conn.execute(
        """
        SELECT r.route_short_name, left(r.route_long_name, 34),
               count(*) FILTER (WHERE s.status NOT IN ('no_data', 'not_in_feed')) AS judged,
               count(*) FILTER (WHERE s.status = 'tracked_not_matched') AS not_matched,
               count(*) FILTER (WHERE s.status = 'partial') AS partial
        FROM analysis.trip_status s
        JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
        JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = s.route_id
        WHERE %(day)s::date IS NULL OR s.schedule_date = %(day)s
        GROUP BY r.route_short_name, r.route_long_name
        HAVING count(*) FILTER (WHERE s.status = 'tracked_not_matched') > 0
        ORDER BY count(*) FILTER (WHERE s.status = 'tracked_not_matched') DESC
        LIMIT 12
        """, {"day": day}).fetchall()
    print("\nRoutes with the most tracked-but-not-matched trips")
    print(f"  {'route':<7}{'name':<36}{'judged':>7}{'not_matched':>13}{'partial':>9}")
    for r in rows:
        print(f"  {r[0]:<7}{r[1]:<36}{r[2]:>7}{r[3]:>13}{r[4]:>9}")


def diagnose(conn, route: str, day: date | None, statuses: list[str], limit: int) -> None:
    trips = conn.execute(
        """
        SELECT s.schedule_date, s.trip_id, s.status, s.stops_scheduled, s.stops_observed,
               extract(epoch FROM s.scheduled_start), extract(epoch FROM s.scheduled_end),
               f.feed_id, t.shape_id, t.trip_headsign
        FROM analysis.trip_status s
        JOIN analysis.feed_by_date f ON f.service_date = s.schedule_date
        JOIN gtfs.routes r ON r.feed_id = f.feed_id AND r.route_id = s.route_id
        JOIN gtfs.trips t ON t.feed_id = f.feed_id AND t.trip_id = s.trip_id
        WHERE ltrim(r.route_short_name, '0') = ltrim(%(route)s, '0')
          AND s.status = ANY(%(statuses)s)
          AND (%(day)s::date IS NULL OR s.schedule_date = %(day)s)
        ORDER BY s.schedule_date, s.scheduled_start
        LIMIT %(limit)s
        """, {"route": route, "statuses": statuses, "day": day, "limit": limit}).fetchall()

    print(f"\nRoute {route}: {len(trips)} trips with status {', '.join(statuses)}")
    if not trips:
        return
    print(f"  {'date':<11}{'trip':<11}{'status':<20}{'sched':<12}{'pings':>6}{'ping time':>14}"
          f"{'on route':>9}{'med m':>7}{'min m':>7}{'along m':>13}{'shape m':>9}{'stops':>9}  headsign")

    shape_cache: dict[tuple[str, str], sm.Shape | None] = {}
    for sched_date, trip_id, status, n_sched, n_obs, s_start, s_end, feed_id, shape_id, headsign in trips:
        key = (feed_id, shape_id)
        if key not in shape_cache:
            pts = conn.execute(
                "SELECT shape_pt_lat, shape_pt_lon FROM gtfs.shapes WHERE feed_id = %s AND shape_id = %s "
                "ORDER BY shape_pt_sequence", key).fetchall()
            shape_cache[key] = sm.build_shape(sm.to_xy(*zip(*pts))) if pts else None
        shape = shape_cache[key]

        pings = conn.execute(
            """
            SELECT extract(epoch FROM vehicle_ts)::float8, latitude, longitude
            FROM rt.vehicle_positions
            WHERE trip_id = %s AND start_date IN (%s, %s::date + 1)
              AND latitude IS NOT NULL AND vehicle_ts IS NOT NULL
            ORDER BY vehicle_ts
            """, (trip_id, sched_date, sched_date)).fetchall()

        sched = f"{fmt_time(float(s_start))}-{fmt_time(float(s_end))}"
        if not pings or shape is None:
            print(f"  {sched_date!s:<11}{trip_id:<11}{status:<20}{sched:<12}{len(pings):>6}"
                  f"{'-':>14}{'-':>9}{'-':>7}{'-':>7}{'-':>13}{'-':>9}{n_obs:>4}/{n_sched:<4}  "
                  f"{(headsign or '')[:24]}{'  (no shape)' if shape is None else ''}")
            continue

        t = np.array([p[0] for p in pings])
        xy = sm.to_xy([p[1] for p in pings], [p[2] for p in pings])
        _, dist = sm.project(shape, xy)
        nearest = dist.min(axis=1)
        along = sm.locate_pings(shape, xy, t)
        ok = ~np.isnan(along)
        span = f"{along[ok].min():.0f}-{along[ok].max():.0f}" if ok.any() else "-"
        ping_time = f"{fmt_time(t.min())}-{fmt_time(t.max())}"
        print(f"  {sched_date!s:<11}{trip_id:<11}{status:<20}{sched:<12}{len(pings):>6}{ping_time:>14}"
              f"{100 * ok.mean():>8.0f}%{np.median(nearest):>7.0f}{nearest.min():>7.0f}{span:>13}"
              f"{shape.length:>9.0f}{n_obs:>4}/{n_sched:<4}  {(headsign or '')[:24]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose trips that were tracked but not matched to their shape")
    parser.add_argument("--route", required=True, help="route short name, e.g. 517")
    parser.add_argument("--date", type=date.fromisoformat, help="schedule date (default: all)")
    parser.add_argument("--status", default="tracked_not_matched,partial",
                        help="comma-separated statuses to inspect")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    with db.connect(Config.from_env().database_url) as conn:
        routes_summary(conn, args.date)
        diagnose(conn, args.route, args.date, args.status.split(","), args.limit)
        print("\nColumns: pings = live pings tagged with the trip; ping time = when they were sent; "
              "on route = share placed on the shape;\n"
              "med m / min m = distance from pings to the nearest point of the shape; "
              "along m = range of progress along the shape; stops = observed/scheduled.")


if __name__ == "__main__":
    main()
