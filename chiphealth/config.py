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

    # Electrode pitch in micrometres -- the physical width of one electrode,
    # and the constant that converts electrode counts into real distance.
    #
    # RESOLVED 2026-08-10 by researcher measurement, closing the question
    # deferred at spec/objectives.md §1.4 and §2.1 (§2.1 was §3.1 before the
    # 2026-08-12 priority renumbering):
    #
    #     active grid   31.55 mm square  (995.4025 mm^2)
    #     electrodes    128 x 128 = 16,384
    #     pitch         31.55 mm / 128 = 246.48 um
    #     cell area     246.48^2 = 60,752 um^2 = 0.0608 mm^2
    #
    # Everything in this package still computes in *electrode* units; the pitch
    # is only applied when reporting physical dimensions, so it cannot perturb
    # detection. See geometry.electrodes_to_um and friends.
    pitch_um: float | None = 246.48

    # Physical extent of the active electrode array, for cross-checking the
    # pitch against a measurement rather than trusting one number.
    grid_width_mm: float = 31.55
    grid_height_mm: float = 31.55

    # Plate gap in micrometres -- the spacing between the electrode surface and
    # the top plate. STILL UNKNOWN. Footprint area comes from the pitch, but
    # volume needs this too, so droplet volumes stay unreportable until it is
    # measured. Do not invent a value.
    gap_um: float | None = None

    # Voltage rails. 45V on the first three, matching chipsetup.py:47 and
    # cleanup.py:64-72. 45V is the agreed degradation-tracking baseline
    # (spec/objectives.md §1.4) -- lower voltages do not actuate reliably and
    # would manufacture false dead electrodes.
    volts: tuple[int, ...] = (45, 45, 45, 0, 0, 0, 0, 0, 0)

    # How far a rail may read back from what was commanded before the startup
    # check calls it a mismatch. InquireVolt returns integers; a rail or two of
    # slop is normal, a rail reading 0 when 45 was commanded is not.
    volt_tolerance: int = 2

    # Seconds to wait after SetVolt before reading the rails back.
    #
    # 0.3 s copies csvvolcont.py:168 exactly -- the one legacy script that sets
    # voltage with no human in the loop, and therefore the only proven timing
    # this binding can actually match. The interactive scripts (chipsetup.py
    # etc.) have an input() here instead, so their delay is however long the
    # operator took and is not a number we can copy.
    volt_settle_s: float = 0.3

    # Seconds between SetPower and SetVolt. csvvolcont.py:158-166 issues them
    # back to back with no delay, so the default is 0.
    #
    # This was 2.0 on the theory that chipsetup.py's input() prompt here was
    # load-bearing. That was an assumption, not something any working script
    # does, and csvvolcont reaches 45 V without it. Left configurable so a real
    # supply-ramp problem can still be given time, but the default now matches
    # the proven sequence.
    power_settle_s: float = 0.0

    # Poll InquireVolt repeatedly while the rails settle, instead of reading
    # once. OFF by default and deliberately so.
    #
    # InquireVolt is not a passive getter: analysis §2 records that each call
    # issues a libusb_bulk_transfer and parses an 18-byte 0xAA-framed response.
    # Every legacy script calls it EXACTLY ONCE. Polling made up to 14 USB
    # round-trips during power-up where the proven scripts make one, which is
    # an unjustified divergence from the only known-good sequence.
    #
    # Turn it on when you want to watch a supply ramp -- it prints each reading
    # and is the right tool for diagnosing a slow rail. Just do not leave it on
    # for a measurement run.
    volt_poll_diagnostic: bool = False

    def __post_init__(self) -> None:
        if len(self.volts) != 9:
            raise ValueError(f"SetVolt takes exactly 9 rails, got {len(self.volts)}")


@dataclass
class SweepConfig:
    """Coarse-pass geometry (spec/p1_chip_health_design.md §2, phase 4)."""

    # The operator-loaded starting droplet: 20x20 at row 5, col 10.
    #
    # Moved from (2, 5) on 2026-08-12: the old position sat the droplet's edge
    # against the outer electrodes, which is not where you want to be loading by
    # hand. This is the ONLY place the load position is defined -- the load
    # prompt, the registration check, the resting frame and the traversal plan
    # all read it from here, so moving it moves all of them together.
    #
    # It is not free: the sweep still starts every band at row 1
    # (`first_band_row`), so the window now walks up four rows instead of one
    # before band 0 begins. Coverage stays complete -- verified every run by
    # sweep.untested_electrodes.
    start_row: int = 5
    start_col: int = 10
    window_h: int = 20
    window_w: int = 20

    # One electrode per step. EWOD transport requires the activated region to
    # overlap the droplet, so the window cannot jump; this mirrors the
    # one-column-at-a-time discipline in 1pixsplit.py and dropsplitoff.py.
    step_electrodes: int = 1

    # Seconds between activations. 0.5 matches the working scripts exactly:
    # 1pixsplit.py's activate() sleeps 0.5s and move_drop() calls it once per
    # one-electrode step, and cleanup.py/cleanreload.py both use
    # STEP_DELAY = 0.5. Their 1s and 2s sleeps are STAGE boundaries -- after a
    # load, after a split, before shutdown -- not per-step delays.
    #
    # Note this is a sleep AFTER each activation, not the period. Frame
    # capture, detection and artifact writing add ~150-180ms per step at 1080p,
    # so our real interval is ~0.67s where the legacy scripts sit at ~0.5s.
    # Override with --step-delay, which takes precedence.
    #
    # IMPORTANT, since caterpillar transport landed: one electrode of TRAVEL is
    # now two activations (grow, then release), so at this delay the liquid gets
    # 1.0s per electrode of travel where the legacy scripts give it 0.5s. See
    # armed_min_step_delay_s.
    #
    # This is the ARMED default. Dry runs use dry_run_step_delay_s instead.
    step_delay_s: float = 0.5

    # What a DRY run waits between activations. Zero, and deliberately.
    #
    # The delay exists for one reason: to give liquid time to reflow before the
    # next frame. A dry run energises nothing -- ChipController.activate never
    # calls ActivateElec when disarmed, and open() skips SetPower/SetVolt -- so
    # there is no liquid, nothing moves, and nothing needs time. Charging a dry
    # run 0.5s per frame turned a plumbing check into a 15-minute wait for no
    # physical reason.
    #
    # At 1798 frames that is ~15 min saved; a dry run with a real camera is then
    # bound by the camera (~1.5 min) rather than by an artificial sleep. An
    # explicit --step-delay always wins, and the value actually used is logged
    # and recorded in run.json, so a run is never silently timed differently
    # from what its operator expected.
    dry_run_step_delay_s: float = 0.0

    # Floor on step_delay_s for an ARMED run. Below this the run refuses to
    # start unless explicitly overridden.
    #
    # Why 0.25 and not 0.5: legacy gives one electrode of travel 0.5s in a
    # single activation (1pixsplit.py's move_drop -> activate -> sleep(0.5)).
    # Caterpillar splits that move into two frames, so 0.25s per frame preserves
    # exactly the same 0.5s-per-electrode budget the working scripts use, while
    # asking less of the liquid within each frame. That makes 0.25 the fastest
    # value with an actual argument behind it rather than a guess.
    #
    # It is a floor, not a recommendation. 0.5 remains the default and the only
    # value with hardware behind it. Anything below 0.5 is recorded in the run
    # notes so a fast run can never be mistaken later for a proven-timing one.
    #
    # This exists because --step-delay 0.05 was used on the 2026-08-10 armed
    # session and became one of three confounded candidate causes for the
    # droplet coming apart. Fast timing is fine in dry-run -- nothing is
    # energised and there is no liquid -- and the guard is what makes it safe to
    # use fast values there habitually without one leaking into an armed run.
    armed_min_step_delay_s: float = 0.25

    # Stop after this many bands. None = the whole chip, which is the only
    # setting that produces a coverage result.
    #
    # For timing work, not measurement. A step-delay ramp needs the same short
    # traversal repeated at several delays; at 128 rows and a 20-high window
    # there are 7 bands, so `--bands 1` is roughly a seventh of the run. The
    # sweep then misses most of the chip and every affected row is reported
    # `unknown`, with a PARTIAL SWEEP note in run.json -- a truncated run must
    # never be readable later as a clean bill of health.
    max_bands: int | None = None

    # "h" (horizontal serpentine only) or "both" (adds a vertical pass).
    # A horizontal sweep mainly exercises column-to-column transitions; the
    # vertical pass covers the other axis at double the cost.
    axes: str = "h"

    # Where the first band begins -- NOT where the droplet is loaded. Keeping
    # these separate is what lets row 1 be swept even though the operator loads
    # at row 5. Set to `start_row` to reproduce the old, incomplete coverage.
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

    # OpenCV/DirectShow device index. NOT a fixed property of the microscope --
    # it depends on which cameras are connected when the process starts, so it
    # can move if a webcam is plugged in or the microscope is on a different
    # port. Override with --camera. Researcher confirmed 0 on 2026-08-10.
    camera_address: int | str = 0
    autofocus: bool = False  # off for measurement runs, researcher 2026-08-06

    # Capture resolution to request. Unset, the c922 driver defaults to
    # 640x480, at which the chip spanned ~1.7 px per electrode in the
    # 2026-08-10 dry run -- so a 1-electrode residue threshold was ~2.4 px^2,
    # i.e. noise. The device falls back silently if it cannot honour this, and
    # the delivered size is measured either way. None leaves the driver default.
    frame_width: int | None = 1920
    frame_height: int | None = 1080

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

    # Skip the phase-2 droplet check. Needed for a no-voltage run: this
    # hardware requires 45V to hold a droplet at a known position, so with the
    # chip unpowered there is nothing to validate the coordinate frame against.
    # The run then trusts the picked corners instead of confirming them.
    skip_droplet_check: bool = False

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
