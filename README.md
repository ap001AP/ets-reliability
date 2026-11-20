# ETS Reliability

How reliable is Edmonton Transit, really? This project collects Edmonton Transit Service's live GTFS Realtime feeds, stores every bus position and arrival prediction in PostgreSQL, and measures on-time performance and bus bunching against the published schedule.

**Status:** collecting data (Stage 1 of 3). Analysis and the Tableau dashboard come next.

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

## Tests

```bash
pytest                                   # unit tests
docker compose exec db createdb -U ets ets_test
TEST_DATABASE_URL=postgresql://ets:ets@localhost:5432/ets_test pytest   # + database tests
```

Database tests drop and recreate schema `rt`, so never point `TEST_DATABASE_URL` at the `ets` database.

## Data source

ETS GTFS and GTFS Realtime feeds from City of Edmonton Open Data, used under the City's open data licence.
