# Data quality

Generated 2026-11-22 03:22 CST by `python -m analysis.quality` from the collected data. Rerun it to refresh every figure.

## Feed availability

Polls run every 30 s. Availability is successful polls over expected polls between the first and last poll; feed age is how old the feed's own timestamp was when polled.

| feed | first poll | last poll | expected polls | successful | failed | availability % | stale % | median feed age s | p90 feed age s |
|---|---|---|---|---|---|---|---|---|---|
| trip_updates | Fri Nov 20 14:51 | Sun Nov 22 03:21 | 4,381 | 4,355 | 25 | 99.4 | 0.0 | 16.3 | 27.2 |
| vehicle_positions | Fri Nov 20 14:51 | Sun Nov 22 03:21 | 4,381 | 4,374 | 5 | 99.8 | 0.0 | 16.1 | 27.1 |

### Gaps longer than 90 s

| feed | last good poll | next good poll | gap (min) | cause |
|---|---|---|---|---|
| trip_updates | Sat Nov 21 01:32 | Sat Nov 21 01:35 | 3.0 | HTTP 404, HTTP 500 |
| vehicle_positions | Sat Nov 21 12:14 | Sat Nov 21 12:16 | 1.5 | connection dropped |
| trip_updates | Sat Nov 21 16:36 | Sat Nov 21 16:42 | 6.0 | HTTP 404, HTTP 500 |

## Field completeness

### Vehicle positions

969,232 vehicle pings stored.

| field | % filled |
|---|---|
| trip_id | 100.0 |
| route_id | 100.0 |
| direction_id | 70.1 |
| start_date | 100.0 |
| latitude | 100.0 |
| bearing | 90.1 |
| speed_mps | 63.8 |
| current_stop_sequence | 100.0 |
| stop_id | 100.0 |
| current_status | 2.8 |
| occupancy_status | 37.7 |

### Trip updates and alerts

| measure | value |
|---|---|
| trip-update rows stored (changes only) | 1,258,687 |
| % rows with no trip_id | 12.9 |
| % rows with an arrival time | 3.4 |
| % rows with a departure time | 96.6 |
| % rows with a departure delay | 93.9 |
| trips announced as cancelled | 36 |
| skipped-stop predictions | 4,975 |
| distinct service alerts | 81 |
| alerts with no real end date (year 3000 placeholder) | 4 |
| alert effects seen | DETOUR, NO_SERVICE, STOP_MOVED |

## Schedule and live feed consistency

| check | distinct IDs seen | found in schedule | % |
|---|---|---|---|
| vehicle trip_ids in schedule | 12,064 | 12,048 | 99.9 |
| vehicle route_ids in schedule | 230 | 226 | 98.3 |
| vehicle stop_ids in schedule | 1,792 | 1,788 | 99.8 |
| trip-update trip_ids in schedule | 12,759 | 12,743 | 99.9 |

The schedule covers ETS and its regional partners. Headline figures in the analysis use ETS routes only; partner agencies are reported separately.

| agency | counted as ETS | routes |
|---|---|---|
| Edmonton Transit Service | yes | 185 |
| Strathcona County Transit | no | 25 |
| St. Albert Transit | no | 24 |
| Fort Saskatchewan Transit | no | 3 |
| Leduc Transit | no | 2 |
| Beaumont Transit | no | 1 |
| Spruce Grove Transit | no | 1 |

Scheduled routes that never appear in the live vehicle feed:

| route | name | scheduled trips excluded |
|---|---|---|
| Valley | Valley Line | 351 |
| Capital | Capital Line | 271 |
| Metro | Metro Line | 228 |
| 10 | Leduc - Nisku - Eia | 40 |
| 540 | Beaumont To Edmonton | 26 |
| 1 | Leduc - Nisku - Edmonton | 12 |
| 671 | Alex Janvier - Wem | 2 |
| 628 | Steele Heights - Cy Becker | 1 |

## Method validation

Inferred stop passage times compared with the departure times ETS reports for stops already passed (seconds, ours minus ETS):

| compared | median | p10 | p90 | % within 60 s |
|---|---|---|---|---|
| 187,712 | 6.4 | -61.9 | 90.2 | 72.4 |

The same comparison split by group. A group whose median sits far from the others would point to a bias in the method for that kind of stop or service:

| split | group | compared | median | p10 | p90 | % within 60 s |
|---|---|---|---|---|---|---|
| period | other times | 145,088 | 5.9 | -57.4 | 82.8 | 74.9 |
| period | weekday peak | 42,624 | 8.7 | -81.5 | 117.3 | 63.6 |
| position | first stop | 5,007 | -26.1 | -96.2 | 7.7 | 74.3 |
| position | other stops | 182,705 | 7.4 | -60.6 | 91.2 | 72.3 |
| route type | express | 3,297 | -5.2 | -214.5 | 144.3 | 45.9 |
| route type | regular | 184,415 | 6.5 | -60.4 | 89.3 | 72.8 |
| timepoint | not a timepoint | 176,103 | 7.1 | -61.2 | 90.4 | 72.4 |
| timepoint | timepoint | 11,609 | 1.0 | -73.0 | 85.8 | 72.3 |

On-time performance of ETS routes under Edmonton's 2009 Transit Service Standards (policy C539): departures from timepoints 0 to 3 minutes late with no early departures, and arrivals 5 minutes early to 1 minute late, each against a 90% target. The current policy (C539A) does not define on-time performance. Because inferred times carry measurement error, the last column allows 30 s either side.

| measure | events | % on time | % early | % late | % on time allowing 30 s |
|---|---|---|---|---|---|
| all timepoint departures (0 to +3 min) | 27,350 | 50.2 | 24.9 | 24.9 | 67.3 |
| first-stop departures (0 to +3 min) | 9,519 | 60.0 | 23.8 | 16.2 | 78.0 |
| other timepoint departures (0 to +3 min) | 17,831 | 45.0 | 25.5 | 29.5 | 61.6 |
| final-stop arrivals (-5 to +1 min) | 1,350 | 41.9 | 4.1 | 54.1 | 47.3 |

381,675 stop passages inferred; 98.3% fall within a crossing window of 120 s or less and count toward on-time performance.

Scheduled trips by delivery status:

| status | trips |
|---|---|
| ran | 11,370 |
| no_data | 7,040 |
| not_in_feed | 931 |
| partial | 327 |
| missing | 175 |
| tracked_not_matched | 145 |
| cancelled | 25 |

## Issues found and how they were handled

**Stop-sequence field jumps ahead.** While a bus is between stops, `current_stop_sequence` often reports the trip's final stop, then snaps back at the next real stop. A method built on that field had a median bias of -287 s against ETS's own departure times. *Handled:* Stop passages are timed from GPS positions matched to each trip's route shape; the field is no longer used.

**After-midnight trips labelled with the next day.** GTFS keeps late-night trips on the previous service day (times like 24:30:00), but the live feed labels them with the calendar date, so they were compared with a schedule 24 hours away. *Handled:* Each passage is compared with whichever of the labelled date or the day before fits the schedule (`schedule_date`).

**Outdated time zone data in the database image.** The stock Postgres image placed Edmonton at UTC-7 while the live data and the host were at UTC-6, which would have shifted every schedule comparison by an hour. *Handled:* The database image now installs current tzdata, and the schedule loader checks Postgres against Python on every run. Python environments differ too (a fresh environment still placed Edmonton at UTC-7), so Python code and tests load the zone from the pinned `tzdata` package instead of the host's database.

**Server closing idle connections.** ETS's server occasionally closes kept-alive connections; the next request failed instantly on both feeds. *Handled:* The collector retries connection errors (not HTTP errors) on a fresh connection.

**Trip-update volume.** The trip-update feed repeats about 2,200 trips' predictions every 30 s, including trips that have not started, which projected to over 20 GB in two weeks. *Handled:* A prediction is stored only when it changes (unique index with `NULLS NOT DISTINCT`).

**Loop routes locked to the end of the route.** On loop routes the bus lays over at the bay where the route shape ends. The first ping matched the end of the shape, and because matching only moves forward, every later ping was rejected. About 400 trips on routes such as 517, 509A/B, 512, 514 and 109 were tracked but never matched. *Handled:* If three consecutive pings sit on the route but behind the current position, the match is treated as a wrong lock: those placements are discarded and matching restarts from the next ping.

**First and last stops not timed.** Departures are timed when a bus passes 30 m beyond a stop, so a bus that waits at a bay further past its first stop never registered a terminal departure, and final stops were never timed at all. Two-stop express trips such as 700X showed as tracked but not matched. *Handled:* A bus first seen standing within 300 m past its first stop is timed from when it pulls away, and the final stop records an arrival against the scheduled arrival. On-time performance still uses departures only.

**Routes absent from the live feed.** LRT lines and some regional routes are in the static schedule but never appear in the live vehicle feed. *Handled:* Their trips are classified `not_in_feed` and excluded from delivery figures; the analysis covers bus service.

**Queries that stalled at full scale.** Two analysis queries ran in seconds on test data but stalled on the real database because of stale table statistics and missing indexes. *Handled:* Tables are analyzed after each rebuild, join columns are indexed, and the trip-status query runs in timed, analyzed stages.
