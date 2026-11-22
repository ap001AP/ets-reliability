# Dashboard layout guide

Dashboard size: **1400 × 1000** (Tableau: Size → Fixed size → Custom). Background images are drawn at 2× (2800 × 2000) for sharp text; add each as an Image object at x 0, y 0, w 1400, h 1000 with **Fit image** on, then float every sheet on top at the positions below and set sheet backgrounds to None (transparent).

Colours: background `#061F3F`, panel `#0B2C55`, chart area `#082447`, highlight `#0A5FA8`, text `#FFFFFF` / `#9DB7D5`, on time `#2BB3A3`, early `#F2B53A`, late `#E8604C`, accent `#4FA8F0`.

## Navigation buttons (same on every page)

Float a Navigation button over each stop, set it to go to that page, and make it invisible (Button style: Image with a fully transparent PNG, or Text with an empty title, no border and no fill).

| page | x | y | w | h |
|---|---|---|---|---|
| Overview | 587 | 32 | 126 | 58 |
| Routes | 721 | 32 | 126 | 58 |
| When | 854 | 32 | 126 | 58 |
| Where | 988 | 32 | 126 | 58 |
| Stories | 1122 | 32 | 126 | 58 |
| About | 1255 | 32 | 126 | 58 |

## Overview  (`bg_overview.png`)

| panel | place sheet at x | y | w | h | suggested content |
|---|---|---|---|---|---|
| ON TIME | 56 | 160 | 219 | 46 | Big number (BAN) |
| ETS STANDARD | 323 | 160 | 219 | 46 | Big number (BAN) |
| BUNCHED | 590 | 160 | 219 | 46 | Big number (BAN) |
| EXCESS WAIT | 857 | 160 | 219 | 46 | Big number (BAN) |
| MISSING TRIPS | 1124 | 160 | 220 | 46 | Big number (BAN) |
| ON-TIME BY DAY | 56 | 304 | 837 | 266 | network_daily.csv |
| ABOUT THIS DATA | 941 | 304 | 403 | 266 | Text: scope and dates |
| LEAST RELIABLE ROUTES | 56 | 650 | 620 | 280 | routes.csv |
| MOST BUNCHED ROUTES | 724 | 650 | 620 | 280 | routes.csv |

## Routes  (`bg_routes.png`)

| panel | place sheet at x | y | w | h | suggested content |
|---|---|---|---|---|---|
| FILTERS | 160 | 130 | 1184 | 44 | Route kind, day type |
| ROUTE RANKING | 56 | 248 | 728 | 682 | routes.csv table |
| ON TIME VS BUNCHING | 832 | 248 | 512 | 356 | routes.csv scatter |
| SELECTED ROUTE | 832 | 680 | 512 | 254 | Route detail on selection |

## When  (`bg_when.png`)

| panel | place sheet at x | y | w | h | suggested content |
|---|---|---|---|---|---|
| FILTERS | 160 | 130 | 1184 | 44 | Route, route kind |
| ON-TIME BY HOUR AND DAY | 56 | 248 | 1288 | 376 | route_hour.csv heatmap |
| EARLY AND LATE BY HOUR | 56 | 704 | 837 | 226 | route_hour.csv |
| WEEKDAY VS WEEKEND | 941 | 704 | 403 | 226 | network_daily.csv |

## Where  (`bg_where.png`)

| panel | place sheet at x | y | w | h | suggested content |
|---|---|---|---|---|---|
| DELAY MAP | 56 | 168 | 837 | 762 | stop_route.csv points + route_shapes.csv lines |
| ROUTE AND DIRECTION | 941 | 164 | 403 | 94 | Route and direction filters |
| DELAY ALONG THE ROUTE | 941 | 334 | 403 | 376 | stop_route.csv by stop_order |
| LEGEND AND NOTES | 941 | 790 | 403 | 140 | Colour legend, text |

## Stories  (`bg_stories.png`)

| panel | place sheet at x | y | w | h | suggested content |
|---|---|---|---|---|---|
| ROUTE 56 · CENTURY PARK | 56 | 164 | 403 | 349 | Story text |
| DEPARTURES FROM CENTURY PARK | 507 | 168 | 837 | 341 | headways.csv, route 056 |
| ROUTE 127 · A DELAY THAT CARRIED | 56 | 585 | 403 | 349 | Story text |
| DELAY BY TRIP | 507 | 589 | 837 | 341 | trip-level delays, route 127 |

## About  (`bg_about.png`)

| panel | place sheet at x | y | w | h | suggested content |
|---|---|---|---|---|---|
| HOW IT'S MEASURED | 56 | 168 | 837 | 236 |  |
| VALIDATION | 941 | 168 | 403 | 236 | Text or BANs |
| FEED UPTIME AND OUTAGES | 56 | 484 | 620 | 446 | Text or table from data_quality.md |
| ISSUES FOUND AND FIXED | 724 | 484 | 620 | 446 | Text |
