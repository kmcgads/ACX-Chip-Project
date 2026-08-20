"""Electrode <-> pixel mapping.

Pure: standard library plus numpy. No OpenCV, no hardware. Every detector
primitive works in electrode units, so this module is what makes the camera
frame comparable to the commanded frame.

Coordinate conventions, fixed here and used everywhere downstream:

* Electrodes are **1-indexed** ``(row, col)``, matching the vendor API and every
  legacy script -- ``Drop(height, width, row, col)`` with ``row=1, col=1`` at the
  top-left (cleanup.py:47-48).
* The electrode grid spans a continuous plane where electrode ``(r, c)`` occupies
  the unit square ``[c-1, c] x [r-1, r]``. So the chip's four corners are
  ``(0,0)``, ``(cols,0)``, ``(cols,rows)``, ``(0,rows)`` and the *centre* of
  electrode ``(r, c)`` is ``(c-0.5, r-0.5)``.
* Pixel coordinates are OpenCV's: ``(x, y)``, x rightwards, y downwards.

The electrode pitch plays no part in the pixel mapping. Corner registration
gives that outright, so nothing in detection depends on the pitch and a wrong
value could not perturb a verdict. The pitch is applied only at the reporting
boundary, converting electrode counts into micrometres -- see
:func:`electrodes_to_um` and friends at the end of this module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

# Corner order used throughout: top-left, top-right, bottom-right, bottom-left.
CORNER_ORDER = ("top_left", "top_right", "bottom_right", "bottom_left")


def fit_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Fit the 3x3 homography mapping ``src`` points onto ``dst`` points.

    Standard DLT. Four correspondences give an exact solution; more are handled
    by least squares. Written out rather than pulled from OpenCV so this module
    stays importable on a machine with no cv2 (docs/spec/design.md §7).

    Args:
        src: (N, 2) array of source points, N >= 4.
        dst: (N, 2) array of destination points.

    Returns:
        (3, 3) homography with ``H[2, 2] == 1``.
    """
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2:
        raise ValueError(f"src and dst must both be (N, 2); got {src.shape}, {dst.shape}")
    n = src.shape[0]
    if n < 4:
        raise ValueError(f"need at least 4 correspondences, got {n}")

    a = np.zeros((2 * n, 8), dtype=float)
    b = np.zeros((2 * n,), dtype=float)
    for i in range(n):
        x, y = src[i]
        u, v = dst[i]
        a[2 * i] = [x, y, 1, 0, 0, 0, -u * x, -u * y]
        a[2 * i + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]
        b[2 * i] = u
        b[2 * i + 1] = v

    h, *_ = np.linalg.lstsq(a, b, rcond=None)
    return np.array([[h[0], h[1], h[2]],
                     [h[3], h[4], h[5]],
                     [h[6], h[7], 1.0]], dtype=float)


def apply_homography(h: np.ndarray, pts: npt.ArrayLike) -> np.ndarray:
    """Apply ``h`` to an (N, 2) array of points, returning (N, 2).

    ``pts`` is ``ArrayLike``, not ``ndarray``, because the first thing this does
    is ``asarray`` it -- callers legitimately pass a bare ``[[x, y]]`` and always
    have. ``h`` stays ``ndarray``: it is used as ``h.T`` without coercion.
    """
    pts = np.atleast_2d(np.asarray(pts, dtype=float))
    ones = np.ones((pts.shape[0], 1), dtype=float)
    homog = np.hstack([pts, ones]) @ h.T
    w = homog[:, 2:3]
    # A zero w means the point maps to infinity -- degenerate registration.
    if np.any(np.abs(w) < 1e-12):
        raise ValueError("degenerate homography: point maps to infinity")
    return homog[:, :2] / w


@dataclass(frozen=True)
class RegistrationCheck:
    """Result of the phase-2 self-check."""

    ok: bool
    reasons: tuple[str, ...] = ()
    centroid_error_electrodes: float = 0.0
    area_ratio: float = 0.0


class ElectrodeFrame:
    """Bidirectional electrode <-> pixel mapping for one camera placement.

    Built from the four chip corners clicked once in the live window. Cacheable
    and reusable across runs for as long as the camera does not move.
    """

    def __init__(self, h_e2p: np.ndarray, rows: int, cols: int) -> None:
        self.rows = int(rows)
        self.cols = int(cols)
        self._h = np.asarray(h_e2p, dtype=float)
        self._h_inv = np.linalg.inv(self._h)

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_corners(cls, corners_px, rows: int, cols: int) -> "ElectrodeFrame":
        """Build from the four chip corners in pixels.

        Args:
            corners_px: four (x, y) pixel points in CORNER_ORDER -- top-left,
                top-right, bottom-right, bottom-left of the *electrode array*.
            rows, cols: chip dimensions in electrodes (128 x 128).
        """
        corners_px = np.asarray(corners_px, dtype=float)
        if corners_px.shape != (4, 2):
            raise ValueError(f"expected 4 corner points, got shape {corners_px.shape}")
        plane = np.array([[0.0, 0.0],
                          [float(cols), 0.0],
                          [float(cols), float(rows)],
                          [0.0, float(rows)]], dtype=float)
        return cls(fit_homography(plane, corners_px), rows, cols)

    def to_dict(self) -> dict:
        return {"h_e2p": self._h.tolist(), "rows": self.rows, "cols": self.cols}

    @classmethod
    def from_dict(cls, d: dict) -> "ElectrodeFrame":
        return cls(np.array(d["h_e2p"], dtype=float), d["rows"], d["cols"])

    # ── mapping ──────────────────────────────────────────────────────────────

    def electrode_to_pixel(self, row: float, col: float) -> tuple[float, float]:
        """Centre of electrode (row, col), 1-indexed, to pixel (x, y)."""
        pt = apply_homography(self._h, [[col - 0.5, row - 0.5]])[0]
        return float(pt[0]), float(pt[1])

    def pixel_to_electrode(self, x: float, y: float) -> tuple[float, float]:
        """Pixel (x, y) to fractional electrode (row, col), 1-indexed.

        Fractional on purpose -- sub-electrode contact-line positions are what
        make the drag metric meaningful.
        """
        pt = apply_homography(self._h_inv, [[x, y]])[0]
        return float(pt[1] + 0.5), float(pt[0] + 0.5)

    def corners_px(self) -> np.ndarray:
        plane = np.array([[0.0, 0.0],
                          [float(self.cols), 0.0],
                          [float(self.cols), float(self.rows)],
                          [0.0, float(self.rows)]], dtype=float)
        return apply_homography(self._h, plane)

    # ── scale ────────────────────────────────────────────────────────────────

    def px_per_electrode(self) -> tuple[float, float]:
        """Approximate (px per column, px per row).

        Averaged over the whole chip. Under perspective the true scale varies
        across the frame; this is the mean, which is what the area thresholds
        want. Use :meth:`local_px_per_electrode` when a per-location figure
        matters.
        """
        c = self.corners_px()
        top = float(np.linalg.norm(c[1] - c[0]))
        bottom = float(np.linalg.norm(c[2] - c[3]))
        left = float(np.linalg.norm(c[3] - c[0]))
        right = float(np.linalg.norm(c[2] - c[1]))
        return (top + bottom) / 2.0 / self.cols, (left + right) / 2.0 / self.rows

    def local_px_per_electrode(self, row: float, col: float) -> tuple[float, float]:
        """(px per column, px per row) in the neighbourhood of one electrode."""
        x0, y0 = self.electrode_to_pixel(row, col)
        x1, y1 = self.electrode_to_pixel(row, col + 1)
        x2, y2 = self.electrode_to_pixel(row + 1, col)
        return (float(np.hypot(x1 - x0, y1 - y0)),
                float(np.hypot(x2 - x0, y2 - y0)))

    def electrode_area_px(self) -> float:
        """Approximate pixel area of a single electrode cell."""
        pc, pr = self.px_per_electrode()
        return pc * pr

    def area_px_to_electrodes(self, area_px: float) -> float:
        cell = self.electrode_area_px()
        return float(area_px) / cell if cell > 0 else 0.0

    def min_area_px(self, min_electrodes: float) -> float:
        """Pixel-area threshold for a blob of ``min_electrodes`` cells.

        This replaces ``detect_drop_color``'s fixed ``min_area=500``
        (camera.py:59) for wide-field use. At whole-chip framing one electrode is
        of the order of 100-225 px^2, so the fixed 500 silently discards 1-2
        electrode residue -- exactly the evidence the health check exists to
        find (docs/spec/p1_chip_health_design.md §8).
        """
        return float(min_electrodes) * self.electrode_area_px()

    # ── bounding boxes ───────────────────────────────────────────────────────

    def bbox_px_to_electrode(self, x: float, y: float, w: float, h: float
                             ) -> tuple[float, float, float, float]:
        """Pixel bbox (x, y, w, h) to electrode (row, col, height, width).

        Corners are mapped individually and re-bounded, so a rotated or
        perspective-skewed chip does not silently shrink the box.
        """
        pts = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=float)
        mapped = apply_homography(self._h_inv, pts)
        cols = mapped[:, 0] + 0.5
        rows = mapped[:, 1] + 0.5
        r0, r1 = float(rows.min()), float(rows.max())
        c0, c1 = float(cols.min()), float(cols.max())
        return r0, c0, r1 - r0, c1 - c0

    def clamp(self, row: float, col: float) -> tuple[float, float]:
        return (min(max(row, 1.0), float(self.rows)),
                min(max(col, 1.0), float(self.cols)))

    def contains(self, row: float, col: float) -> bool:
        return 1.0 <= row <= self.rows and 1.0 <= col <= self.cols


def check_registration(observed_centroid_rc: tuple[float, float],
                       observed_area_electrodes: float,
                       expected_row: int,
                       expected_col: int,
                       expected_h: int,
                       expected_w: int,
                       centroid_tol_electrodes: float,
                       area_tol_frac: float) -> RegistrationCheck:
    """Validate the coordinate frame against the known initial droplet.

    The operator loads a droplet of known size at a known position (20x20 at
    ``SweepConfig.start_row`` / ``start_col``, currently row 5, col 10), so the
    mapping can be checked for free before anything is energised. A silently
    wrong coordinate frame would poison every verdict in the run, and this is
    the cheapest guard against it
    (docs/spec/p1_chip_health_design.md §2, phase 2).
    """
    exp_centre = (expected_row + expected_h / 2.0 - 0.5,
                  expected_col + expected_w / 2.0 - 0.5)
    dr = observed_centroid_rc[0] - exp_centre[0]
    dc = observed_centroid_rc[1] - exp_centre[1]
    centroid_err = float(np.hypot(dr, dc))

    exp_area = float(expected_h * expected_w)
    area_ratio = observed_area_electrodes / exp_area if exp_area else 0.0

    reasons: list[str] = []
    if centroid_err > centroid_tol_electrodes:
        reasons.append(
            f"droplet centroid is {centroid_err:.1f} electrodes from the expected "
            f"({exp_centre[0]:.1f}, {exp_centre[1]:.1f}); tolerance is "
            f"{centroid_tol_electrodes}"
        )
    lo, hi = 1.0 - area_tol_frac, 1.0 + area_tol_frac
    if not (lo <= area_ratio <= hi):
        reasons.append(
            f"droplet area is {area_ratio:.2f}x the expected {exp_area:.0f} "
            f"electrodes; tolerance is {lo:.2f}-{hi:.2f}x"
        )

    return RegistrationCheck(
        ok=not reasons,
        reasons=tuple(reasons),
        centroid_error_electrodes=centroid_err,
        area_ratio=float(area_ratio),
    )


# ── physical units ───────────────────────────────────────────────────────────
#
# Everything above, and every threshold in the detector, works in electrode
# units. These convert at the reporting boundary only. Keeping the split sharp
# means the pitch can be wrong without changing a single verdict -- it would
# only mislabel the axes of a report.
#
# Pitch resolved 2026-08-10: 31.55 mm / 128 = 246.48 um (config.ChipConfig).


def electrodes_to_um(n: float, pitch_um: float) -> float:
    """Electrode counts to micrometres along one axis."""
    return float(n) * float(pitch_um)


def electrode_area_um2(pitch_um: float) -> float:
    """Footprint of a single electrode cell, in square micrometres."""
    return float(pitch_um) ** 2


def footprint_um2(n_electrodes: float, pitch_um: float) -> float:
    """Footprint of a droplet covering ``n_electrodes`` cells."""
    return float(n_electrodes) * electrode_area_um2(pitch_um)


def droplet_volume_nl(n_electrodes: float, pitch_um: float,
                      gap_um: float | None) -> float | None:
    """Approximate droplet volume in nanolitres.

    Returns None when the plate gap is unknown, which it currently is. The
    footprint follows from the pitch, but volume does not -- a droplet is a
    slab, and without its thickness any figure would be invented. Callers must
    handle None rather than substitute a guess.

    Treats the droplet as a right prism over its footprint: real droplets bulge
    at the edges, so this is a lower bound on the true volume, not a
    measurement.
    """
    if gap_um is None:
        return None
    if gap_um <= 0:
        raise ValueError("gap_um must be positive")
    um3 = footprint_um2(n_electrodes, pitch_um) * float(gap_um)
    return um3 / 1e6  # 1 nL = 1e6 um^3


