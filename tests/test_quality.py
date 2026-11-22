"""Tests for the data-quality report: availability, outage detection and report sections."""

from __future__ import annotations

from analysis import build, quality
from tests.test_trip_status import DAY, local, world  # noqa: F401


def test_outage_detected_with_cause(world):
    world.execute(
        "INSERT INTO rt.poll_log (feed, polled_at, http_status, error) VALUES ('vehicle_positions', %s, 500, 'HTTP 500')",
        (local("09:10"),),
    )
    _, gaps = quality.outages(world)
    assert len(gaps) == 1
    feed, start, end, minutes, cause = gaps[0]
    assert feed == "vehicle_positions" and 40 <= minutes <= 41 and cause == "HTTP 500"


def test_gap_without_logged_errors_is_attributed_to_collector(world):
    _, gaps = quality.outages(world)
    assert gaps[0][4].startswith("no polls logged")


def test_availability_reflects_gap(world):
    _, rows = quality.availability(world)
    vp = next(r for r in rows if r[0] == "vehicle_positions")
    assert 70 < vp[6] < 75


def test_report_contains_every_section(world):
    build.build_trip_status(world, DAY)
    text = quality.report(world)
    for heading in ("# Data quality", "## Feed availability", "## Field completeness",
                    "## Schedule and live feed consistency", "## Method validation",
                    "## Issues found and how they were handled"):
        assert heading in text
    assert "not_in_feed" in text or "never appear in the live vehicle feed" in text
    assert "Stop-sequence field jumps ahead" in text


def test_md_table_formats_values():
    out = quality.md_table(["a", "b"], [(1234, 5.678), ("x|y", None)])
    assert out.splitlines()[2] == "| 1,234 | 5.7 |"
    assert out.splitlines()[3] == "| x/y |  |"
