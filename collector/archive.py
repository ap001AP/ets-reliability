"""Raw feed and static GTFS archiving."""

from __future__ import annotations

import gzip
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests


def save_raw(archive_dir: Path, feed: str, polled_at: datetime, content: bytes) -> Path:
    day_dir = archive_dir / "raw" / polled_at.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{feed}_{polled_at.strftime('%H%M%S')}.pb.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(content)
    return path


def fetch_static(session: requests.Session, url: str, timeout: float) -> tuple[bytes, str, datetime]:
    fetched_at = datetime.now(timezone.utc)
    resp = session.get(url, timeout=timeout * 4)
    resp.raise_for_status()
    content = resp.content
    return content, hashlib.sha256(content).hexdigest(), fetched_at


def save_static(archive_dir: Path, content: bytes, sha256: str, fetched_at: datetime) -> Path:
    static_dir = archive_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    path = static_dir / f"gtfs_{fetched_at.strftime('%Y%m%d')}_{sha256[:10]}.zip"
    path.write_bytes(content)
    return path
