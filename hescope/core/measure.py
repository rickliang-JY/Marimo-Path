"""Physical measurements for HE-Scope (level-0 geometry -> microns)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Measurement:
    kind: str  # "box"
    width_um: float | None  # None when mpp unknown
    height_um: float | None
    diagonal_um: float | None
    width_px: float  # level-0 pixels
    height_px: float


def measure_box(
    p0: tuple[float, float],
    p1: tuple[float, float],
    mpp: float | None,
) -> Measurement:
    """Level-0 coords of a box's two corners -> physical size.

    um = px * mpp when mpp is known, else None.
    """
    w_px = abs(p1[0] - p0[0])
    h_px = abs(p1[1] - p0[1])
    if mpp is None:
        w_um = h_um = d_um = None
    else:
        w_um = w_px * mpp
        h_um = h_px * mpp
        d_um = math.hypot(w_px, h_px) * mpp
    return Measurement(
        kind="box",
        width_um=w_um,
        height_um=h_um,
        diagonal_um=d_um,
        width_px=w_px,
        height_px=h_px,
    )


def format_measurement(m: Measurement) -> str:
    """Human-readable measurement string.

    e.g. '512.0 x 384.0 px = 127.2 x 95.4 um (diag 159.1 um)' or px-only
    when mpp was None.
    """
    px = f"{m.width_px:.1f} x {m.height_px:.1f} px"
    if m.width_um is None or m.height_um is None or m.diagonal_um is None:
        return px
    return (
        f"{px} = {m.width_um:.1f} x {m.height_um:.1f} um "
        f"(diag {m.diagonal_um:.1f} um)"
    )
