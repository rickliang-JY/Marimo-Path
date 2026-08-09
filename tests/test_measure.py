"""Tests for hescope.measure (Part B.4)."""

from __future__ import annotations

import math

from hescope.measure import Measurement, format_measurement, measure_box


def test_measure_box_with_mpp():
    m = measure_box((100.0, 200.0), (612.0, 584.0), 0.25)
    assert m.kind == "box"
    assert m.width_px == 512.0
    assert m.height_px == 384.0
    assert m.width_um == 512.0 * 0.25
    assert m.height_um == 384.0 * 0.25
    assert m.diagonal_um == math.hypot(512.0, 384.0) * 0.25


def test_measure_box_corner_order_irrelevant():
    a = measure_box((0.0, 0.0), (40.0, 30.0), 0.5)
    b = measure_box((40.0, 30.0), (0.0, 0.0), 0.5)
    assert a == b
    assert a.width_um == 20.0 and a.height_um == 15.0


def test_measure_box_mpp_none():
    m = measure_box((10.0, 10.0), (110.0, 60.0), None)
    assert m.width_px == 100.0 and m.height_px == 50.0
    assert m.width_um is None
    assert m.height_um is None
    assert m.diagonal_um is None


def test_format_measurement_with_mpp():
    m = measure_box((100.0, 200.0), (612.0, 584.0), 0.25)
    s = format_measurement(m)
    assert s == "512.0 x 384.0 px = 128.0 x 96.0 um (diag 160.0 um)"


def test_format_measurement_matches_spec_example_shape():
    # spec example: 512x384 px at mpp 0.2484375 -> '127.2 x 95.4 um (diag 159.1 um)'
    mpp = 127.2 / 512.0
    m = measure_box((0.0, 0.0), (512.0, 384.0), mpp)
    s = format_measurement(m)
    assert s.startswith("512.0 x 384.0 px = ")
    assert "(diag " in s and s.endswith(" um)")


def test_format_measurement_mpp_none_px_only():
    m = measure_box((0.0, 0.0), (100.0, 50.0), None)
    s = format_measurement(m)
    assert s == "100.0 x 50.0 px"
    assert "um" not in s


def test_format_rounding_one_decimal():
    m = Measurement(
        kind="box",
        width_um=12.34,
        height_um=5.65,
        diagonal_um=13.57,
        width_px=49.36,
        height_px=22.6,
    )
    assert format_measurement(m) == "49.4 x 22.6 px = 12.3 x 5.7 um (diag 13.6 um)"


def test_measurement_frozen():
    m = measure_box((0.0, 0.0), (10.0, 10.0), 0.25)
    try:
        m.width_px = 99.0
    except Exception:
        pass
    else:
        raise AssertionError("Measurement must be frozen")
    assert m.width_px == 10.0
