"""Per-run chip registration: caching, validation, and drift tracking.

Pure: standard library only. No OpenCV, no camera. The click-to-pick UI is a
thin shell in ``run_health`` that calls into here, so everything that can be
wrong about a calibration is testable without a rig.

Why this is per-run rather than a constant: the camera moves between runs
(researcher, 2026-08-07). Hardcoded corners would be stale the first time it is
nudged, and nothing downstream would notice -- the homography still fits and
registration near the load position still passes.

What a moving camera costs, and why the numbers below get recorded: it changes
apparent scale as well as position. Detection adapts automatically because every
threshold downstream of registration is in electrode units, but the underlying
*measurement* is genuinely noisier at lower magnification. Recording
px-per-electrode per run is what lets a noisy week be explained rather than
mistaken for degradation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# A corner must move more than this before it is treated as a real remount
# rather than measurement slop.
DEFAULT_DRIFT_WARN_PX = 150.0

# The electrode array is square, so a wildly non-square quad usually means the
# wrong feature was clicked.
MAX_SIDE_RATIO = 2.5

# Two corners closer than this are almost certainly a double-click.
MIN_CORNER_SEPARATION_PX = 20.0

CORNER_NAMES = ("top-left", "top-right", "bottom-right", "bottom-left")


@dataclass(frozen=True)
class Calibration:
    """One registration: where the chip was, and how big it looked."""

    corners_px: tuple[tuple[float, float], ...]
    frame_size: tuple[int, int]
    px_per_electrode: tuple[float, float] = (0.0, 0.0)
    created: str = ""
    chip_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["corners_px"] = [list(p) for p in self.corners_px]
        d["frame_size"] = list(self.frame_size)
        d["px_per_electrode"] = list(self.px_per_electrode)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(
            corners_px=tuple(tuple(float(v) for v in p) for p in d["corners_px"]),
            frame_size=tuple(int(v) for v in d["frame_size"]),
            px_per_electrode=tuple(float(v) for v in d.get("px_per_electrode",
                                                           (0.0, 0.0))),
            created=d.get("created", ""),
            chip_id=d.get("chip_id", ""),
        )


def load_cache(path) -> Calibration | None:
    """Previous calibration, or None. A corrupt cache is ignored, not fatal."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return Calibration.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError):
        return None


def save_cache(path, cal: Calibration) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cal.to_dict(), indent=2), encoding="utf-8")


# ── validation ───────────────────────────────────────────────────────────────

def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def signed_area(corners) -> float:
    """Shoelace area. Positive for TL, TR, BR, BL in image coordinates (y down)."""
    total = 0.0
    n = len(corners)
    for i in range(n):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def validate_corners(corners, frame_size=None) -> list[str]:
    """Catch a misclick before it becomes a silently wrong calibration.

    Four points give an *exact* homography fit, so nothing downstream can tell a
    typo from a good pick -- there is no residual to inspect. These are the
    checks that are possible before the droplet test runs.

    Returns a list of problems; empty means it looks sane.
    """
    problems: list[str] = []
    if corners is None or len(corners) != 4:
        return [f"need exactly 4 corners, got {0 if not corners else len(corners)}"]

    for i, (x, y) in enumerate(corners):
        if frame_size and not (0 <= x <= frame_size[0] and 0 <= y <= frame_size[1]):
            problems.append(
                f"{CORNER_NAMES[i]} ({x:.0f}, {y:.0f}) is outside the "
                f"{frame_size[0]}x{frame_size[1]} frame")

    for i in range(4):
        for j in range(i + 1, 4):
            d = _dist(corners[i], corners[j])
            if d < MIN_CORNER_SEPARATION_PX:
                problems.append(
                    f"{CORNER_NAMES[i]} and {CORNER_NAMES[j]} are only {d:.0f}px "
                    f"apart -- double-click?")

    area = signed_area(corners)
    if abs(area) < 1.0:
        problems.append("the four corners are collinear or coincident")
    elif area < 0:
        problems.append(
            "corners are in the wrong order or wound the wrong way -- expected "
            "top-left, top-right, bottom-right, bottom-left")

    sides = [_dist(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    if min(sides) > 0:
        ratio = max(sides) / min(sides)
        if ratio > MAX_SIDE_RATIO:
            problems.append(
                f"sides differ by {ratio:.1f}x; the 128x128 electrode array "
                f"should look roughly square -- wrong feature clicked?")
    return problems


# ── drift ────────────────────────────────────────────────────────────────────

def corner_deltas(new, old) -> list[float]:
    """Per-corner pixel movement between two calibrations."""
    if not old or len(old) != len(new):
        return []
    return [_dist(n, o) for n, o in zip(new, old)]


def drift_report(new: Calibration, old: Calibration | None,
                 warn_px: float = DEFAULT_DRIFT_WARN_PX) -> dict:
    """How far the chip moved in frame since the last run.

    Recorded whether or not it is large. Run-to-run coordinate jitter is a real
    source of variance in the longitudinal record, and it is only explicable
    afterwards if it was measured at the time.
    """
    if old is None:
        return {"first_calibration": True, "deltas_px": [], "max_delta_px": 0.0,
                "warn": False, "scale_change_pct": 0.0}

    deltas = corner_deltas(new.corners_px, old.corners_px)
    max_delta = max(deltas) if deltas else 0.0

    old_scale = sum(old.px_per_electrode) / 2.0
    new_scale = sum(new.px_per_electrode) / 2.0
    scale_pct = (100.0 * (new_scale - old_scale) / old_scale) if old_scale else 0.0

    return {
        "first_calibration": False,
        "deltas_px": [round(d, 1) for d in deltas],
        "max_delta_px": round(max_delta, 1),
        "warn": max_delta > warn_px,
        "scale_change_pct": round(scale_pct, 2),
        "previous_created": old.created,
        "frame_size_changed": tuple(new.frame_size) != tuple(old.frame_size),
    }


def describe_drift(report: dict) -> str:
    """One line for the log and the run notes."""
    if report.get("first_calibration"):
        return "First calibration on record; no drift to compare against."
    parts = [f"corners moved up to {report['max_delta_px']}px since the last run"]
    if report.get("scale_change_pct"):
        parts.append(f"scale changed {report['scale_change_pct']:+.2f}%")
    if report.get("frame_size_changed"):
        parts.append("FRAME SIZE ALSO CHANGED")
    line = ", ".join(parts) + "."
    if report.get("warn"):
        line += (" That is a large jump -- a misclick looks like this too. "
                 "Worth a second look before trusting the run.")
    return line
