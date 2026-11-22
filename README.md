# ETS Reliability

How reliable is Edmonton Transit, really? This project collects Edmonton Transit Service's live GTFS Realtime feeds, stores every bus position and arrival prediction in PostgreSQL, and measures on-time performance and bus bunching against the published schedule.

**Status:** collecting data; Stage 2 (analysis) in progress. The Tableau dashboard comes next.

## Stage 1 — Collector

A Python poller reads three ETS feeds and archives the static schedule:

| Feed | Interval | Stored as |
|---|---|---|
| Vehicle positions | 30 s | `rt.vehicle_positions` (one row per new vehicle ping) |
| Trip updates | 30 s | `rt.trip_updates` (per trip: last passed stop + next stop) |
| Service alerts | 5 min | `rt.alerts` (first and last seen) |
| Static GTFS zip | 6 h | `archive/static/` (only when the file changes) |

Every poll, including failures and unchanged ("stale") feeds, is written to `rt.poll_log`, so data gaps can be measured, not guessed.

## Run it

```bash
docker compose up -d --build
docker compose logs -f collector
```

Check what the live feed actually populates:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m collector.inspect_feed
```

## Stage 2 — Schedule

The static GTFS zip archived by the collector is loaded into a versioned `gtfs` schema (one `feed_id` per snapshot, so schedule changes during the collection window are kept apart). `gtfs.service_days` expands the service calendar with holiday exceptions, and `gtfs.service_ts()` turns GTFS times (including times past 24:00) into real timestamps.

```bash
python -m gtfs_static.load --all --check
```

`--check` prints row counts, the service-date range, and how many realtime trip, route and stop IDs match the schedule.

### Stop passages

Stop passage times come from GPS positions matched to each trip's route shape, not from the feed's stop-sequence field. That field proved unreliable: while a bus is between stops, ETS often reports the trip's final stop as the next stop, then snaps back at the next real stop (see `sql/diagnostics/passage_bias.sql`).

1. Shapes and stops are projected to metres. Each stop is placed on the shape in trip order, so loop routes that pass the same corner twice stay in sequence.
2. Each ping is placed on the shape within a window set by the previous ping and a 30 m/s speed limit. Pings more than 100 m from the route (deadheading, detours) are dropped, and small backwards GPS jitter is clamped. If three pings in a row are on the route but behind the current position (a bus laying over at the end of a loop before starting its trip), the match restarts.
3. A stop's departure time is when the bus crosses 30 m past the stop, interpolated between the two pings on either side. A bus first seen waiting up to 300 m past its first stop departs when it pulls away. The final stop records an arrival instead, used for trip delivery and spacing but not for on-time performance.

Passages are joined to the schedule to get delay. Only crossings with pings 120 s apart or less count toward on-time performance (on time = 1 min early to 5 min late).

```bash
python -m analysis.build --check
```

`--check` reports the share of pings matched to the route, and compares inferred passage times with the departure times ETS itself reports for stops already passed.

### Bus bunching

For every observed bus at every stop, `analysis.headways` compares the actual gap to the previous bus (same route, direction and stop) with that trip's scheduled gap. On frequent service (scheduled every 15 minutes or better), a bus is **bunched** if it arrives within 25% of the scheduled gap and leaves a **gap** if it arrives more than 150% of it later. Routes are also scored on headway variability and **excess wait time**: the extra average wait riders face because buses are unevenly spaced.

If a tracked bus has no recorded passage at a stop, the pair around it is dropped rather than counted as a false gap. Trips that never appeared at all stay in, because riders did wait through those gaps.

### Ghost buses

`analysis.trip_status` gives every scheduled trip one status: **ran** (observed at half its stops or more), **partial**, **cancelled** (announced in the trip-update feed), **tracked_not_matched** (in the vehicle feed but never matched to stops on its route), **missing** (scheduled, never seen, never announced), or **no_data**. A trip is only judged if the collector received at least 80% of expected vehicle polls during its scheduled time, so collector downtime and ETS feed outages are never counted as ghost buses.

## Data quality

[`docs/data_quality.md`](docs/data_quality.md) covers feed availability and every outage, field completeness, schedule consistency, validation of the passage method against ETS's own departure times, and each data issue found with how it was handled. Regenerate it with:

```bash
python -m analysis.quality
```

## Tests

```bash
pytest                                   # unit tests
docker compose exec db createdb -U ets ets_test
TEST_DATABASE_URL=postgresql://ets:ets@localhost:5432/ets_test pytest   # + database tests
```

Database tests drop and recreate schema `rt`, so never point `TEST_DATABASE_URL` at the `ets` database.

## Data source

ETS GTFS and GTFS Realtime feeds from City of Edmonton Open Data, used under the City's open data licence.
