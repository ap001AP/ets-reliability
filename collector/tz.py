"""Edmonton time zone loaded from the pinned tzdata package, independent of the host's tz database."""

from __future__ import annotations

from importlib.resources import files
from zoneinfo import ZoneInfo


def _load() -> ZoneInfo:
    with (files("tzdata.zoneinfo") / "America" / "Edmonton").open("rb") as fh:
        return ZoneInfo.from_file(fh, key="America/Edmonton")


EDMONTON = _load()
