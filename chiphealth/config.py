"""Configuration for the chip-health run.

Replaces the hardcoded literals scattered through the legacy scripts
(spec/objectives.md §0.1). Every value below appeared as a magic number in at
least one of project/*.py; the source is cited where that is the case.

Nothing here reads the filesystem or the hardware at import time.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Vendor DLL ────────────────────────────────────────────────────────────────
# Was hardcoded at 1pixsplit.py:37 (and cleanup.py:13, and every other script).
# Confirmed still current by the researcher, 2026-08-06.
DEFAULT_DLL_DIR = r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows"
DEFAULT_DLL_NAME = "DLLTest.dll"


@dataclass
class ChipConfig:
    """Physical chip geometry.

    128x128 confirmed by the researcher 2026-08-06; matches the
    ``ActivateElec(128, 128, ...)`` calls at chipsetup.py:70 and cleanup.py:36.
    """

    rows: int = 128
    cols: int = 128

    # Electrode pitch in micrometres. Deliberately unknown: deferred to
    # Priority 3 (spec/objectives.md §1.4, §3.1). Everything in this package
    # works in *electrode* units, so this is only needed to report physical
    # dimensions. Do not invent a value.
    pitch_um: float | None = None

    # Voltage rails. 45V on the first three, matching chipsetup.py:47 and
    # cleanup.py:64-72. 45V is the agreed degradation-tracking baseline
    # (spec/objectives.md §1.4) -- lower voltages do not actuate reliably and
    # would manufacture false dead electrodes.
    volts: tuple[int, ...] = (45, 45, 45, 0, 0, 0, 0, 0, 0)

    # How far a rail may read back from what was commanded before the startup
    # check calls it a mismatch. InquireVolt returns integers; a rail or two of
    # slop is normal, a rail reading 0 when 45 was commanded is not.
    volt_tolerance: int = 2

    def __post_init__(self) -> None:
        if len(self.volts) != 9:
            raise ValueError(f"SetVolt takes exactly 9 rails, got {len(self.volts)}")


@dataclass
class SweepConfig:
    """Coarse-pass geometry (spec/p1_chip_health_design.md §2, phase 4)."""

    # The operator-loaded starting droplet: 20x20 at row 2, col 5.
    # Researcher-specified, 2026-08-06.
    start_row: int = 2
    start_col: int = 5
    window_h: int = 20
    window_w: int = 20

    # One electrode per step. EWOD transport requires the activated region to
    # overlap the droplet, so the window cannot jump; this mirrors the
    # one-column-at-a-time discipline in 1pixsplit.py and dropsplitoff.py.
    step_electrodes: int = 1

    # Seconds between activations. cleanup.py:61 uses the same 0.5s default.
    step_delay_s: float = 0.5

    # "h" (horizontal serpentine only) or "both" (adds a vertical pass).
    # A horizontal sweep mainly exercises column-to-column transitions; the
    # vertical pass covers the other axis at double the cost.
    axes: str = "h"

    # Where the first band begins -- NOT where the droplet is loaded. Keeping
    # these separate is what lets row 1 be swept even though the operator loads
    # at row 2. Set to `start_row` to reproduce the old, incomplete coverage.
    first_band_row: int = 1

    # Give band 0 the corner turn the other bands get for free from their band
    # change. Without it, rows 2-21 x cols 5-20 are never on a leading edge.
    # Costs ~32 steps; see sweep.plan_serpentine.
    prime_band0: bool = True

    # Fine pass.
    block: int = 4  # 4x4 electrode blocks -> a 32x32 verdict map
    probe_h: int = 5  # probe droplet split off the main one for fine work
    probe_w: int = 5
    max_fine_targets: int = 24  # anything dropped by this cap is logged, never silent
    fine_travel_slack: float = 2.0  # x expected steps before declaring unreachable


@dataclass
class DetectorConfig:
    """Thresholds for the three failure signatures.

    All of these are ESTIMATES. There is no ground-truth bad region on this chip
    (spec/objectives.md §1.4 q11), so the first runs are threshold calibration
    rather than measurement, and early verdicts are provisional. The artifact is
    designed so runs can be re-scored offline once better values are known
    (rescore.py).
    """

    # Drag: lag of the observed contact line behind the commanded window edge,
    # in electrodes. 0-1 is normal transport latency.
    lag_electrodes: float = 2.0
    # ... sustained this many consecutive steps. Persistence is what separates a
    # real sticky spot from single-frame detection wobble.
    lag_persist_steps: int = 3

    # Residue: liquid left inside the already-swept region.
    residue_min_area_electrodes: float = 1.0

    # No movement: primary blob centroid static while the window translates.
    no_move_steps: int = 5
    no_move_tol_electrodes: float = 0.5

    # Registration self-check (design §2, phase 2): the initial droplet is at a
    # known position and size, so a bad coordinate frame is caught before
    # anything is energised.
    registration_centroid_tol_electrodes: float = 4.0
    registration_area_tol_frac: float = 0.5


@dataclass
class CaptureConfig:
    """Video, stills and dataset capture (spec/objectives.md §1.8)."""

    camera_address: int | str = 1  # camera.py:200 uses index 1
    autofocus: bool = False  # off for measurement runs, researcher 2026-08-06

    # ── fixed camera calibration ─────────────────────────────────────────────
    #
    # The four corners of the ACTIVE 128x128 electrode array, in pixels, in the
    # order top-left, top-right, bottom-right, bottom-left. Hardcoded because
    # the camera is fixed relative to the chip.
    #
    # Pick the corners of the electrode ARRAY -- not the glass, substrate or
    # cartridge housing. A few pixels of imprecision is harmless (one electrode
    # is roughly 11 px wide at whole-chip framing, against a 2-electrode drag
    # threshold), but picking the wrong boundary is a systematic SCALE error:
    # 50 px on a ~1400 px span is ~4.6 electrodes of drift at the far corner,
    # which would manufacture false drag along the far edge.
    #
    # Four points give an exact homography fit, so a typo produces a perfectly
    # successful-looking calibration. The phase-2 droplet check catches gross
    # errors (wrong order, flips, rotations) but validates near the load
    # position, so it is least sensitive to exactly the scale error above.
    corners_px: tuple[tuple[float, float], ...] | None = None

    # Frame size (width, height) the corners were measured at. A different
    # capture resolution silently rescales every pixel coordinate, so a
    # mismatch is refused rather than tolerated. None disables the check.
    expected_frame_size: tuple[int, int] | None = None

    # The camera moves between runs, so registration is redone every run by
    # default: the operator clicks the four corners in the live window at
    # phase 2. The previous run's corners are pre-loaded as a starting
    # proposal. Set reuse_calibration to skip picking when nothing has moved.
    pick_corners: bool = True
    reuse_calibration: bool = False
    calibration_cache: str = "calibration.json"

    record_video: bool = True
    video_fps: float = 15.0

    # Routine stills every 5s, in addition to continuous video.
    # Researcher requirement 2026-08-06.
    still_interval_s: float = 5.0

    # Matched negatives: an all-positive dataset cannot train a classifier
    # (spec/objectives.md §1.8). Fraction of clean steps sampled and saved.
    negative_sample_rate: float = 0.02

    baseline_frames: int = 10

    # Mid-run top-up. All loading is manual, and more liquid CAN be added during
    # a run, so the script asks for it rather than assuming everything is
    # present at the start (spec/objectives.md §1.4 q1).
    topup_enabled: bool = True
    topup_area_frac: float = 0.5    # of the commanded window area
    topup_after_steps: int = 5      # consecutive low/missing observations
    max_topups: int = 5             # then stop asking and say so


@dataclass
class RunConfig:
    """Everything one run needs."""

    chip: ChipConfig = field(default_factory=ChipConfig)
    sweep: SweepConfig = field(default_factory=SweepConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)

    dll_dir: str = DEFAULT_DLL_DIR
    dll_name: str = DEFAULT_DLL_NAME

    runs_root: Path = Path("runs")
    chip_id: str = ""  # mandatory at run time -- see require_chip_id()

    armed: bool = False  # dry-run is the default (spec/objectives.md §1.4)
    backend: str = "auto"  # "auto" | "real" | "fake"

    headless: bool = False  # skip the live window

    @property
    def dll_path(self) -> str:
        return str(Path(self.dll_dir) / self.dll_name)

    def require_chip_id(self) -> str:
        """Fail loudly rather than let longitudinal data silently mix chips."""
        if not self.chip_id.strip():
            raise ValueError(
                "chip_id is required. Without it, runs from different chips are "
                "indistinguishable and the degradation history is worthless. "
                "Pass --chip-id."
            )
        return self.chip_id.strip()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["runs_root"] = str(self.runs_root)
        d["dll_path"] = self.dll_path
        return d


def from_env(cfg: RunConfig | None = None) -> RunConfig:
    """Apply environment overrides.

    ACXCHIP_ARM=1  arms the session (spec/objectives.md §1.4 -- the disarm/arm
                   switch must be obvious and easy, not a hidden ceremony).
    ACXCHIP_DLL    overrides the DLL directory.
    """
    cfg = cfg or RunConfig()
    if os.environ.get("ACXCHIP_ARM", "").strip() in {"1", "true", "yes", "on"}:
        cfg.armed = True
    dll_dir = os.environ.get("ACXCHIP_DLL", "").strip()
    if dll_dir:
        cfg.dll_dir = dll_dir
    return cfg
