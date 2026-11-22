"""Unit tests for projecting pings and stops onto shapes and timing stop crossings."""

from __future__ import annotations

import numpy as np

from analysis import shape_match as sm


def straight_shape() -> sm.Shape:
    return sm.build_shape(np.array([[0.0, y] for y in (0, 500, 1000, 1500, 2000)]))


def test_build_shape_drops_duplicate_points_and_measures_length():
    shape = sm.build_shape(np.array([[0, 0], [0, 0], [0, 300], [400, 300]], dtype=float))
    assert len(shape.seg_len) == 2
    assert shape.length == 700


def test_locate_stops_on_straight_road():
    stop_a, offset = sm.locate_stops(straight_shape(), np.array([[10, 0], [10, 750], [-15, 2000]], dtype=float))
    assert np.allclose(stop_a, [0, 750, 2000])
    assert np.allclose(offset, [10, 10, 15])


def test_locate_stops_on_out_and_back_loop_keeps_order():
    loop = sm.build_shape(np.array([[0, 0], [0, 1000], [20, 1000], [20, 0]], dtype=float))
    stop_a, _ = sm.locate_stops(loop, np.array([[2, 0], [10, 1000], [18, 0]], dtype=float))
    assert stop_a[0] < 5
    assert 1000 <= stop_a[1] <= 1020
    assert stop_a[2] > 2000


def test_first_ping_at_loop_terminal_matches_start_not_end():
    loop = sm.build_shape(np.array([[0, 0], [0, 1000], [20, 1000], [20, 0]], dtype=float))
    a = sm.locate_pings(loop, np.array([[10, 0], [0, 300]], dtype=float), np.array([0.0, 30.0]))
    assert a[0] < 15
    assert abs(a[1] - 300) < 1


def test_locate_pings_drops_off_route_and_clamps_backwards_jitter():
    pts = np.array([[0, 0], [0, 200], [600, 250], [0, 180], [0, 450]], dtype=float)
    t = np.array([0.0, 30.0, 45.0, 60.0, 90.0])
    a = sm.locate_pings(straight_shape(), pts, t)
    assert np.isnan(a[2])
    assert a[3] == a[1] == 200
    assert a[4] == 450


def test_locate_pings_rejects_implausible_jump_ahead():
    pts = np.array([[0, 0], [0, 1900], [0, 250]], dtype=float)
    a = sm.locate_pings(straight_shape(), pts, np.array([0.0, 30.0, 60.0]))
    assert np.isnan(a[1])
    assert a[2] == 250


def test_crossings_interpolate_departure_time():
    t = np.array([0.0, 30.0, 60.0, 90.0, 120.0])
    a = np.array([5.0, 5.0, 5.0, 305.0, 605.0])
    stop_a = np.array([0.0, 500.0, 1900.0])
    found = sm.crossings(t, a, stop_a, offset_m=30.0)
    assert [k for k, *_ in found] == [0, 1]
    k0, when0, window0, n0, event0 = found[0]
    assert abs(when0 - (60 + 30 * 25 / 300)) < 1e-9 and window0 == 30 and n0 == 1 and event0 == "departure"
    assert abs(found[1][1] - (90 + 30 * 225 / 300)) < 1e-9


def test_crossings_skip_stops_passed_before_first_ping():
    found = sm.crossings(np.array([0.0, 30.0]), np.array([400.0, 700.0]), np.array([0.0, 500.0]))
    assert [k for k, *_ in found] == [1]


def test_crossings_ignore_nan_pings_and_count_stops_in_window():
    t = np.array([0.0, 30.0, 60.0])
    a = np.array([0.0, np.nan, 900.0])
    found = sm.crossings(t, a, np.array([100.0, 400.0, 1500.0]), offset_m=0.0)
    assert [(k, window, n) for k, _, window, n, _ in found] == [(0, 60.0, 2), (1, 60.0, 2)]


def loop_with_layover_at_end() -> sm.Shape:
    return sm.build_shape(np.array([[0, 0], [0, 1000], [40, 1000], [40, 30]], dtype=float))


def test_layover_at_loop_end_does_not_lock_the_trip():
    shape = loop_with_layover_at_end()
    pts = np.array([[42, 32], [41, 31], [42, 30], [0, 50], [0, 300], [0, 600], [0, 900], [40, 700]], dtype=float)
    t = np.arange(len(pts)) * 30.0
    a = sm.locate_pings(shape, pts, t)
    assert np.isnan(a[:3]).all()
    assert np.allclose(a[3:7], [50, 300, 600, 900])
    assert abs(a[7] - (1000 + 40 + 300)) < 1


def test_layover_reset_recovers_stop_crossings():
    shape = loop_with_layover_at_end()
    pts = np.array([[42, 32], [41, 31], [0, 10], [0, 250], [0, 500], [0, 750]], dtype=float)
    t = np.arange(len(pts)) * 30.0
    a = sm.locate_pings(shape, pts, t)
    stop_a, _ = sm.locate_stops(shape, np.array([[5, 0], [5, 400]], dtype=float))
    found = sm.crossings(t, a, stop_a)
    assert [k for k, *_ in found] == [0, 1]


def test_single_backward_outlier_does_not_reset():
    pts = np.array([[0, 0], [0, 400], [0, 50], [0, 700]], dtype=float)
    a = sm.locate_pings(straight_shape(), pts, np.array([0.0, 30.0, 60.0, 90.0]))
    assert a[0] == 0 and a[1] == 400 and a[3] == 700


def test_final_stop_records_arrival():
    t = np.array([0.0, 30.0, 60.0, 90.0])
    a = np.array([0.0, 300.0, 700.0, 1000.0])
    found = sm.crossings(t, a, np.array([0.0, 500.0, 1000.0]))
    assert [(k, event) for k, *_, event in found] == [(0, "departure"), (1, "departure"), (2, "arrival")]
    arrival = found[2][1]
    assert abs(arrival - (60 + 30 * (1000 - sm.ARRIVE_OFFSET_M - 700) / 300)) < 1e-9


def test_bus_waiting_past_first_stop_departs_when_it_pulls_away():
    t = np.array([0.0, 30.0, 60.0, 90.0, 120.0])
    a = np.array([180.0, 182.0, 181.0, 400.0, 700.0])
    found = sm.crossings(t, a, np.array([0.0, 600.0, 2000.0]))
    k, when, window, n, event = found[0]
    assert k == 0 and event == "departure"
    assert abs(when - (60 + 30 * (180 + sm.DEPART_OFFSET_M - 181) / (400 - 181))) < 1e-9


def test_moving_bus_first_seen_past_first_stop_is_not_adjusted():
    t = np.array([0.0, 30.0, 60.0])
    a = np.array([180.0, 400.0, 650.0])
    found = sm.crossings(t, a, np.array([0.0, 600.0, 2000.0]))
    assert [k for k, *_ in found] == [1]


def test_bus_waiting_far_past_first_stop_is_not_adjusted():
    t = np.array([0.0, 30.0, 60.0])
    a = np.array([500.0, 505.0, 900.0])
    found = sm.crossings(t, a, np.array([0.0, 700.0, 2000.0]))
    assert [k for k, *_ in found] == [1]
