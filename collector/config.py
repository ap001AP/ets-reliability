"""Collector settings loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ETS_BASE = "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService"


@dataclass(frozen=True)
class Config:
    database_url: str
    vehicle_positions_url: str
    trip_updates_url: str
    alerts_url: str
    static_url: str
    realtime_interval_s: float
    alerts_interval_s: float
    static_interval_s: float
    upcoming_stops: int
    raw_archive_every: int
    archive_dir: Path
    http_timeout_s: float

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        env = os.environ.get
        return cls(
            database_url=env("DATABASE_URL", "postgresql://ets:ets@localhost:5432/ets"),
            vehicle_positions_url=env("VEHICLE_POSITIONS_URL", f"{ETS_BASE}/Vehicle/VehiclePositions.pb"),
            trip_updates_url=env("TRIP_UPDATES_URL", f"{ETS_BASE}/TripUpdate/TripUpdates.pb"),
            alerts_url=env("ALERTS_URL", f"{ETS_BASE}/Alert/Alerts.pb"),
            static_url=env("STATIC_GTFS_URL", f"{ETS_BASE}/GTFS/gtfs.zip"),
            realtime_interval_s=float(env("REALTIME_INTERVAL_S", "30")),
            alerts_interval_s=float(env("ALERTS_INTERVAL_S", "300")),
            static_interval_s=float(env("STATIC_INTERVAL_S", "21600")),
            upcoming_stops=int(env("UPCOMING_STOPS", "1")),
            raw_archive_every=int(env("RAW_ARCHIVE_EVERY", "20")),
            archive_dir=Path(env("ARCHIVE_DIR", "archive")),
            http_timeout_s=float(env("HTTP_TIMEOUT_S", "15")),
        )
