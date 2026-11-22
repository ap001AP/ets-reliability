"""Load archived static GTFS zips into the gtfs schema."""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

from collector import db
from collector.config import Config
from collector.tz import EDMONTON

log = logging.getLogger("gtfs_static")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "sql" / "gtfs_schema.sql"

TEXT, INT, FLOAT, DATE = "text", "int", "float", "date"

TABLES: dict[str, dict[str, str]] = {
    "routes": {
        "route_id": TEXT, "agency_id": TEXT, "route_short_name": TEXT, "route_long_name": TEXT,
        "route_desc": TEXT, "route_type": INT, "route_color": TEXT, "route_text_color": TEXT,
        "route_sort_order": INT,
    },
    "trips": {
        "trip_id": TEXT, "route_id": TEXT, "service_id": TEXT, "trip_headsign": TEXT,
        "trip_short_name": TEXT, "direction_id": INT, "block_id": TEXT, "shape_id": TEXT,
        "wheelchair_accessible": INT, "bikes_allowed": INT,
    },
    "stops": {
        "stop_id": TEXT, "stop_code": TEXT, "stop_name": TEXT, "stop_desc": TEXT,
        "stop_lat": FLOAT, "stop_lon": FLOAT, "zone_id": TEXT, "location_type": INT,
        "parent_station": TEXT, "wheelchair_boarding": INT, "platform_code": TEXT,
    },
    "stop_times": {
        "trip_id": TEXT, "stop_sequence": INT, "stop_id": TEXT, "arrival_time": TEXT,
        "departure_time": TEXT, "stop_headsign": TEXT, "pickup_type": INT, "drop_off_type": INT,
        "shape_dist_traveled": FLOAT, "timepoint": INT,
    },
    "calendar": {
        "service_id": TEXT, "monday": INT, "tuesday": INT, "wednesday": INT, "thursday": INT,
        "friday": INT, "saturday": INT, "sunday": INT, "start_date": DATE, "end_date": DATE,
    },
    "calendar_dates": {"service_id": TEXT, "date": DATE, "exception_type": INT},
    "shapes": {
        "shape_id": TEXT, "shape_pt_sequence": INT, "shape_pt_lat": FLOAT, "shape_pt_lon": FLOAT,
        "shape_dist_traveled": FLOAT,
    },
}
REQUIRED = ("routes", "trips", "stops", "stop_times")


class FeedError(ValueError):
    pass


def parse_gtfs_time(value: str) -> int:
    match = re.fullmatch(r"(\d{1,3}):([0-5]\d):([0-5]\d)", value.strip())
    if not match:
        raise ValueError(f"bad GTFS time {value!r}")
    h, m, s = map(int, match.groups())
    return h * 3600 + m * 60 + s


def parse_gtfs_date(value: str) -> date:
    value = value.strip()
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"bad GTFS date {value!r}")
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))


CONVERTERS = {TEXT: str, INT: lambda v: int(float(v)), FLOAT: float, DATE: parse_gtfs_date}


def _member(zf: zipfile.ZipFile, table: str) -> str | None:
    for name in zf.namelist():
        if Path(name).name == f"{table}.txt":
            return name
    return None


@dataclass
class TableLoad:
    table: str
    rows: int = 0
    ignored_columns: list[str] = field(default_factory=list)


@contextlib.contextmanager
def read_rows(zf: zipfile.ZipFile, member: str, table: str):
    types = TABLES[table]
    with zf.open(member) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        header = [h.strip() for h in next(reader, [])]
        used = [h for h in header if h in types]
        ignored = [h for h in header if h not in types]
        index = [header.index(h) for h in used]
        columns = list(used)
        if table == "stop_times":
            columns += ["arrival_s", "departure_s"]

        def rows():
            for line_no, record in enumerate(reader, start=2):
                if not any(cell.strip() for cell in record):
                    continue
                out = []
                try:
                    for col, i in zip(used, index):
                        cell = record[i].strip() if i < len(record) else ""
                        out.append(CONVERTERS[types[col]](cell) if cell else None)
                    if table == "stop_times":
                        arr = record[header.index("arrival_time")].strip() if "arrival_time" in header else ""
                        dep = record[header.index("departure_time")].strip() if "departure_time" in header else ""
                        out.append(parse_gtfs_time(arr) if arr else None)
                        out.append(parse_gtfs_time(dep) if dep else None)
                except (ValueError, IndexError) as exc:
                    raise FeedError(f"{member} line {line_no}: {exc}") from exc
                yield out

        yield columns, ignored, rows()


def _fetched_at(conn: psycopg.Connection, sha: str, path: Path) -> datetime:
    if conn.execute("SELECT to_regclass('rt.static_snapshots')").fetchone()[0]:
        row = conn.execute("SELECT fetched_at FROM rt.static_snapshots WHERE sha256 = %s", (sha,)).fetchone()
        if row:
            return row[0]
    match = re.search(r"gtfs_(\d{8})_", path.name)
    if match:
        d = parse_gtfs_date(match.group(1))
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _feed_info(zf: zipfile.ZipFile) -> dict:
    member = _member(zf, "feed_info")
    if not member:
        return {}
    with zf.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        row = next(reader, None) or {}
    row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
    return {
        "feed_version": row.get("feed_version") or None,
        "feed_start_date": parse_gtfs_date(row["feed_start_date"]) if row.get("feed_start_date") else None,
        "feed_end_date": parse_gtfs_date(row["feed_end_date"]) if row.get("feed_end_date") else None,
    }


def load_feed(conn: psycopg.Connection, path: Path) -> tuple[str, list[TableLoad]] | None:
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    feed_id = sha[:10]
    if conn.execute("SELECT 1 FROM gtfs.feeds WHERE sha256 = %s", (sha,)).fetchone():
        log.info("%s already loaded as feed %s", path.name, feed_id)
        return None

    zf = zipfile.ZipFile(io.BytesIO(content))
    missing = [t for t in REQUIRED if not _member(zf, t)]
    if missing:
        raise FeedError(f"{path.name} is missing {', '.join(t + '.txt' for t in missing)}")
    if not (_member(zf, "calendar") or _member(zf, "calendar_dates")):
        raise FeedError(f"{path.name} has neither calendar.txt nor calendar_dates.txt")

    info = _feed_info(zf)
    loads: list[TableLoad] = []
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gtfs.feeds (feed_id, sha256, fetched_at, source_path, feed_version, feed_start_date, feed_end_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (feed_id, sha, _fetched_at(conn, sha, path), str(path), info.get("feed_version"),
             info.get("feed_start_date"), info.get("feed_end_date")),
        )
        for table in TABLES:
            member = _member(zf, table)
            if not member:
                continue
            with read_rows(zf, member, table) as (columns, ignored, rows):
                result = TableLoad(table, ignored_columns=ignored)
                with cur.copy(f"COPY gtfs.{table} (feed_id, {', '.join(columns)}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row([feed_id, *row])
                        result.rows += 1
            loads.append(result)
            log.info("%s: %s rows%s", table, f"{result.rows:,}",
                     f" (ignored columns: {', '.join(ignored)})" if ignored else "")
    return feed_id, loads


MATCH_SQL = """
SELECT count(DISTINCT x.{col}) AS seen,
       count(DISTINCT x.{col}) FILTER (WHERE g.{gcol} IS NOT NULL) AS matched
FROM {source} x
LEFT JOIN gtfs.{gtable} g ON g.feed_id = %s AND g.{gcol} = x.{col}
WHERE x.{col} IS NOT NULL
"""

CHECKS = [
    ("vehicle trip_ids in schedule", "rt.vehicle_positions", "trip_id", "trips", "trip_id"),
    ("vehicle route_ids in schedule", "rt.vehicle_positions", "route_id", "routes", "route_id"),
    ("vehicle stop_ids in schedule", "rt.vehicle_positions", "stop_id", "stops", "stop_id"),
    ("trip-update trip_ids in schedule", "rt.trip_updates", "trip_id", "trips", "trip_id"),
    ("trip-update stop_ids in schedule", "rt.trip_updates", "stop_id", "stops", "stop_id"),
]


def timezone_check(conn: psycopg.Connection) -> bool:
    pg_offset = conn.execute(
        "SELECT extract(epoch FROM (now() AT TIME ZONE 'America/Edmonton') - (now() AT TIME ZONE 'UTC'))"
    ).fetchone()[0]
    py_offset = datetime.now(EDMONTON).utcoffset().total_seconds()
    ok = float(pg_offset) == py_offset
    print(f"Edmonton UTC offset: Postgres {float(pg_offset) / 3600:+.0f}h, Python {py_offset / 3600:+.0f}h"
          + ("" if ok else "  <-- MISMATCH: schedule times will be off; update the Postgres image"))
    return ok


def check(conn: psycopg.Connection, feed_id: str) -> None:
    timezone_check(conn)
    print(f"\nFeed {feed_id}")
    for table in TABLES:
        n = conn.execute(f"SELECT count(*) FROM gtfs.{table} WHERE feed_id = %s", (feed_id,)).fetchone()[0]
        print(f"  {table:<16}{n:>12,}")

    span = conn.execute(
        "SELECT min(service_date), max(service_date) FROM gtfs.service_days WHERE feed_id = %s", (feed_id,)
    ).fetchone()
    today = conn.execute(
        "SELECT count(*) FROM gtfs.service_days sd JOIN gtfs.trips t USING (feed_id, service_id) "
        "WHERE sd.feed_id = %s AND sd.service_date = (now() AT TIME ZONE 'America/Edmonton')::date",
        (feed_id,),
    ).fetchone()[0]
    print(f"  service dates     {span[0]} to {span[1]}")
    print(f"  trips scheduled today {today:,}")

    if not conn.execute("SELECT to_regclass('rt.vehicle_positions')").fetchone()[0]:
        return
    print("\nRealtime match rates")
    for label, source, col, gtable, gcol in CHECKS:
        seen, matched = conn.execute(
            MATCH_SQL.format(col=col, source=source, gtable=gtable, gcol=gcol), (feed_id,)
        ).fetchone()
        pct = 100 * matched / seen if seen else 0
        print(f"  {label:<36}{matched:>8,} / {seen:<8,}{pct:6.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load static GTFS zips into Postgres")
    parser.add_argument("zips", nargs="*", type=Path, help="GTFS zip files to load")
    parser.add_argument("--all", action="store_true", help="load every zip in ARCHIVE_DIR/static")
    parser.add_argument("--check", action="store_true", help="print counts and realtime match rates")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    paths = list(args.zips)
    if args.all:
        paths += sorted((cfg.archive_dir / "static").glob("*.zip"))
    if not paths and not args.check:
        parser.error("give zip paths or --all")

    with db.connect(cfg.database_url) as conn:
        db.apply_schema(conn, SCHEMA_PATH)
        for path in paths:
            load_feed(conn, path)
        if args.check:
            latest = conn.execute("SELECT feed_id FROM gtfs.feeds ORDER BY fetched_at DESC LIMIT 1").fetchone()
            if latest:
                check(conn, latest[0])
            else:
                print("No feeds loaded.")


if __name__ == "__main__":
    main()
