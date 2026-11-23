# Tableau extracts

Generated 2026-11-23 00:37 by `python -m analysis.export` for service dates 2026-11-20 to 2026-11-22. Figures cover ETS buses only unless the file says otherwise. Percentages are included for convenience; when combining rows in Tableau, sum the counts and divide (for example SUM(n_on_time) / SUM(events)) so busy routes and quiet routes are weighted correctly.

## network_summary.csv

One row for the whole export period for ETS; feeds the headline cards. 1 rows.

| column | meaning |
|---|---|
| date_from | First service date in the export. |
| date_to | Last service date in the export. |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| n_early | Departures more than 1 minute early. |
| n_late | Departures more than 5 minutes late. |
| pct_on_time | n_on_time / events, as a percentage. Recompute from counts when aggregating. |
| pct_early | n_early / events. |
| pct_late | n_late / events. |
| median_delay_s | Median departure delay in seconds (negative = early). |
| ets_departures | Timepoint departures judged under ETS's 2009 standard. |
| ets_n_on_time | Timepoint departures 0 to 3 minutes late (2009 standard). |
| ets_n_early | Timepoint departures before the scheduled time. |
| ets_n_on_time_allowing_30s | Timepoint departures 30 s early to 3.5 minutes late (allows for measurement error). |
| ets_pct_on_time | ets_n_on_time / ets_departures; the 2009 target was 90%. |
| ets_pct_on_time_allowing_30s | ets_n_on_time_allowing_30s / ets_departures. |
| headway_events | Observed gaps between consecutive buses on frequent service (scheduled every 15 min or better). |
| n_bunched | Gaps under 25% of the scheduled gap. |
| n_gap | Gaps over 150% of the scheduled gap. |
| pct_bunched | n_bunched / headway_events. |
| pct_gap | n_gap / headway_events. |
| excess_wait_s | Extra average wait in seconds caused by uneven spacing (actual minus scheduled expected wait). |
| trips_judged | Scheduled trips the collector could judge (excludes collector outages and routes not in the feed). |
| trips_delivered | Trips observed at one or more stops. |
| trips_missing | Trips never seen in the live feed and never announced as cancelled. |
| trips_cancelled | Trips announced as cancelled in the trip-update feed. |
| pct_missing | trips_missing / trips_judged. |

## network_daily.csv

One row per service day for ETS: on-time, ETS-standard, bunching and trip delivery. 3 rows.

| column | meaning |
|---|---|
| service_date | Service day the trip belongs to (after-midnight trips count toward the previous day). |
| day_name | Day of week. |
| day_type | Weekday or Weekend. |
| first_event_local | First observed departure that day (Edmonton time); shows partial collection days. |
| last_event_local | Last observed departure that day (Edmonton time). |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| n_early | Departures more than 1 minute early. |
| n_late | Departures more than 5 minutes late. |
| pct_on_time | n_on_time / events, as a percentage. Recompute from counts when aggregating. |
| pct_early | n_early / events. |
| pct_late | n_late / events. |
| median_delay_s | Median departure delay in seconds (negative = early). |
| ets_departures | Timepoint departures judged under ETS's 2009 standard. |
| ets_n_on_time | Timepoint departures 0 to 3 minutes late (2009 standard). |
| ets_n_early | Timepoint departures before the scheduled time. |
| ets_n_on_time_allowing_30s | Timepoint departures 30 s early to 3.5 minutes late (allows for measurement error). |
| ets_pct_on_time | ets_n_on_time / ets_departures; the 2009 target was 90%. |
| ets_pct_on_time_allowing_30s | ets_n_on_time_allowing_30s / ets_departures. |
| headway_events | Observed gaps between consecutive buses on frequent service (scheduled every 15 min or better). |
| n_bunched | Gaps under 25% of the scheduled gap. |
| n_gap | Gaps over 150% of the scheduled gap. |
| pct_bunched | n_bunched / headway_events. |
| pct_gap | n_gap / headway_events. |
| excess_wait_s | Extra average wait in seconds caused by uneven spacing (actual minus scheduled expected wait). |
| trips_judged | Scheduled trips the collector could judge (excludes collector outages and routes not in the feed). |
| trips_delivered | Trips observed at one or more stops. |
| trips_missing | Trips never seen in the live feed and never announced as cancelled. |
| trips_cancelled | Trips announced as cancelled in the trip-update feed. |
| pct_missing | trips_missing / trips_judged. |

## routes.csv

One row per ETS route over the export period, with every metric. 176 rows.

| column | meaning |
|---|---|
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| route_long_name | Route name. |
| route_kind | Express (route number ends in X) or Regular. |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| n_early | Departures more than 1 minute early. |
| n_late | Departures more than 5 minutes late. |
| pct_on_time | n_on_time / events, as a percentage. Recompute from counts when aggregating. |
| pct_early | n_early / events. |
| pct_late | n_late / events. |
| median_delay_s | Median departure delay in seconds (negative = early). |
| p90_delay_s | 90th percentile departure delay in seconds. |
| ets_departures | Timepoint departures judged under ETS's 2009 standard. |
| ets_n_on_time | Timepoint departures 0 to 3 minutes late (2009 standard). |
| ets_n_early | Timepoint departures before the scheduled time. |
| ets_n_on_time_allowing_30s | Timepoint departures 30 s early to 3.5 minutes late (allows for measurement error). |
| ets_pct_on_time | ets_n_on_time / ets_departures; the 2009 target was 90%. |
| ets_pct_on_time_allowing_30s | ets_n_on_time_allowing_30s / ets_departures. |
| headway_events | Observed gaps between consecutive buses on frequent service (scheduled every 15 min or better). |
| n_bunched | Gaps under 25% of the scheduled gap. |
| n_gap | Gaps over 150% of the scheduled gap. |
| pct_bunched | n_bunched / headway_events. |
| pct_gap | n_gap / headway_events. |
| excess_wait_s | Extra average wait in seconds caused by uneven spacing (actual minus scheduled expected wait). |
| trips_judged | Scheduled trips the collector could judge (excludes collector outages and routes not in the feed). |
| trips_delivered | Trips observed at one or more stops. |
| trips_missing | Trips never seen in the live feed and never announced as cancelled. |
| trips_cancelled | Trips announced as cancelled in the trip-update feed. |
| pct_missing | trips_missing / trips_judged. |

## route_hour.csv

On-time counts by ETS route, day of week and hour, for heatmaps. 3,823 rows.

| column | meaning |
|---|---|
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| route_kind | Express (route number ends in X) or Regular. |
| iso_dow | Day of week number, Monday = 1. |
| day_name | Day of week. |
| day_type | Weekday or Weekend. |
| local_hour | Hour of the event in Edmonton time. |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| n_early | Departures more than 1 minute early. |
| n_late | Departures more than 5 minutes late. |
| pct_on_time | n_on_time / events, as a percentage. Recompute from counts when aggregating. |
| pct_early | n_early / events. |
| pct_late | n_late / events. |
| median_delay_s | Median departure delay in seconds (negative = early). |

## stop_route.csv

On-time counts and delay by ETS route, direction and stop, with coordinates. 8,514 rows.

| column | meaning |
|---|---|
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| direction_id | GTFS direction (0 or 1). |
| headsign | Destination sign for the direction. |
| stop_id | GTFS stop ID. |
| stop_name | Stop name. |
| stop_lat | Stop latitude. |
| stop_lon | Stop longitude. |
| stop_order | Position of the stop along the route (lowest stop sequence seen). |
| is_timepoint | 1 if the stop is a scheduled timepoint. |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| n_early | Departures more than 1 minute early. |
| n_late | Departures more than 5 minutes late. |
| pct_on_time | n_on_time / events, as a percentage. Recompute from counts when aggregating. |
| pct_early | n_early / events. |
| pct_late | n_late / events. |
| median_delay_s | Median departure delay in seconds (negative = early). |
| avg_delay_s | Mean departure delay in seconds. |
| p90_delay_s | 90th percentile departure delay in seconds. |

## route_shapes.csv

Most-used route line per ETS route and direction, as ordered points. 109,673 rows.

| column | meaning |
|---|---|
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| direction_id | GTFS direction (0 or 1). |
| shape_id | GTFS shape ID of the most-used route line. |
| point_order | Order of the point along the route line; use as the Path in Tableau. |
| lat | Point latitude. |
| lon | Point longitude. |

## headways.csv

Observed gaps between consecutive buses on frequent ETS service, at timepoints and transit centres (every stop with --all-headway-stops). 10,305 rows.

| column | meaning |
|---|---|
| service_date | Service day the trip belongs to (after-midnight trips count toward the previous day). |
| day_name | Day of week. |
| day_type | Weekday or Weekend. |
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| direction_id | GTFS direction (0 or 1). |
| headsign | Destination sign for the direction. |
| stop_id | GTFS stop ID. |
| stop_name | Stop name. |
| stop_lat | Stop latitude. |
| stop_lon | Stop longitude. |
| observed_local | When the bus departed the stop (Edmonton time). |
| local_hour | Hour of the event in Edmonton time. |
| trip_id | GTFS trip ID. |
| prev_trip_id | Trip ID of the bus before it at the same stop. |
| actual_headway_s | Seconds since the previous bus departed the stop. |
| scheduled_headway_s | Scheduled seconds between this trip and the one before it. |
| headway_ratio | actual_headway_s / scheduled_headway_s. |
| spacing | bunched, gap or regular. |

## map_layers.csv

Route lines and stop delays for ETS routes in one table, for a two-layer map. 118,187 rows.

| column | meaning |
|---|---|
| layer | shape for route-line points, stop for stops. |
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| direction_id | GTFS direction (0 or 1). |
| headsign | Destination sign for the direction. |
| point_order | Order of the point along the route line; use as the Path in Tableau. |
| lat | Point latitude. |
| lon | Point longitude. |
| stop_id | GTFS stop ID. |
| stop_name | Stop name. |
| stop_order | Position of the stop along the route (lowest stop sequence seen). |
| is_timepoint | 1 if the stop is a scheduled timepoint. |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| n_early | Departures more than 1 minute early. |
| n_late | Departures more than 5 minutes late. |
| avg_delay_s | Mean departure delay in seconds. |
| median_delay_s | Median departure delay in seconds (negative = early). |

## trips.csv

Delivery status of every scheduled ETS trip. 23,905 rows.

| column | meaning |
|---|---|
| service_date | Service day the trip belongs to (after-midnight trips count toward the previous day). |
| day_name | Day of week. |
| day_type | Weekday or Weekend. |
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| route_kind | Express (route number ends in X) or Regular. |
| direction_id | GTFS direction (0 or 1). |
| headsign | Destination sign for the direction. |
| trip_id | GTFS trip ID. |
| scheduled_start_local | Scheduled first departure (Edmonton time). |
| scheduled_end_local | Scheduled last arrival (Edmonton time). |
| start_hour | Hour of the scheduled first departure. |
| status | ran, partial, cancelled, tracked_not_matched, missing, not_in_feed or no_data. |
| stops_scheduled | Stops on the trip. |
| stops_observed | Stops where the bus was timed. |

## story_passages.csv

Every timed stop for the story routes (default 56 and 127). 15,175 rows.

| column | meaning |
|---|---|
| service_date | Service day the trip belongs to (after-midnight trips count toward the previous day). |
| day_name | Day of week. |
| day_type | Weekday or Weekend. |
| route_id | GTFS route ID. |
| route_short_name | Public route number. |
| direction_id | GTFS direction (0 or 1). |
| headsign | Destination sign for the direction. |
| trip_id | GTFS trip ID. |
| vehicle_id | Bus that ran the trip. |
| stop_sequence | Position of the stop in the trip. |
| stop_id | GTFS stop ID. |
| stop_name | Stop name. |
| is_timepoint | 1 if the stop is a scheduled timepoint. |
| event | departure, or arrival at the final stop. |
| scheduled_local | Scheduled time at the stop (Edmonton time). |
| observed_local | When the bus departed the stop (Edmonton time). |
| delay_s | Observed minus scheduled time, seconds (negative = early). |
| window_s | Seconds between the two GPS pings either side of the stop; smaller is more precise. |

## agencies.csv

Summary for ETS and each regional partner agency in the feed. 7 rows.

| column | meaning |
|---|---|
| agency | Transit agency. |
| is_ets | yes for Edmonton Transit Service. |
| events | Departures counted toward on-time performance. |
| n_on_time | Departures 1 minute early to 5 minutes late. |
| pct_on_time | n_on_time / events, as a percentage. Recompute from counts when aggregating. |
| headway_events | Observed gaps between consecutive buses on frequent service (scheduled every 15 min or better). |
| n_bunched | Gaps under 25% of the scheduled gap. |
| pct_bunched | n_bunched / headway_events. |
| trips_judged | Scheduled trips the collector could judge (excludes collector outages and routes not in the feed). |
| trips_missing | Trips never seen in the live feed and never announced as cancelled. |
| pct_missing | trips_missing / trips_judged. |
