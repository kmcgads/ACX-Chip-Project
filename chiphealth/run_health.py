"""Orchestrator: the eight-phase chip-health run.

    0 preflight      1 load prompt    2 registration   3 baseline
    4 coarse sweep   5 triage         6 fine pass      7 shutdown

One process, three modules: capture, actuation, and a pure detector
(docs/spec/p1_chip_health_design.md §3). OpenCV is imported lazily and only for the
live window and the real camera, so a synthetic run works on a machine with
neither cv2 nor hardware.

Run it:

    python -m chiphealth.run_health --chip-id chip-A --simulate
    python -m chiphealth.run_health --chip-id chip-A --camera 1            # dry-run
    python -m chiphealth.run_health --chip-id chip-A --camera 1 --arm      # live

Dry-run is the default and drives the entire pipeline -- sweep, camera,
detector, recorder, live window -- logging intended frames without energising.
It is a real test mode, not a crippled one. Arming is one flag.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from pathlib import Path
from typing import Callable

from . import DETECTOR_VERSION, SCHEMA_VERSION
from . import calibration, clearance, simulate, sweep
from .actuation import ChipController, Drop, make_backend
from .config import RunConfig, SweepConfig, from_env
from .detector import Detector, Observation
from .geometry import ElectrodeFrame, check_registration
from .recorder import DEGRADED, FAIL, RunRecorder

log = logging.getLogger("chiphealth")

# Below this, a 1-electrode feature is only a few pixels and the detector's
# electrode-unit thresholds sit at the noise floor. Not a hard limit -- a
# warning, because the operator can still choose to proceed.
MIN_USABLE_PX_PER_ELECTRODE = 4.0


# ── frame sources ────────────────────────────────────────────────────────────

class SyntheticSource:
    """Observations from the synthetic rig. No camera, no cv2.

    A fixture for exercising the pipeline, not a model of the instrument
    (see simulate.py).
    """

    def __init__(self, rig: simulate.SyntheticRig) -> None:
        self.rig = rig
        self.index = 0

    def read(self, step, t: float) -> tuple[int, object, Observation]:
        idx = self.index
        self.index += 1
        return idx, None, self.rig.observe(step, idx, t)

    def read_raw(self):
        """No camera, so no frame. The picker never runs against this source."""
        return None

    def close(self) -> None:
        pass


class CameraSource:
    """Observations from the researcher's camera via camera.py."""

    def __init__(self, cam, min_electrodes: float = 1.0,
                 min_saturation: int = 30, frame_size=None) -> None:
        self.cam = cam
        self.min_electrodes = min_electrodes
        self.min_saturation = min_saturation
        # (width, height) actually delivered by the device this run. Checked
        # against the calibration's expected size in phase 2.
        self.frame_size = frame_size

    def read(self, step, t: float) -> tuple[int, object, Observation]:
        idx, frame = self.cam.read_frame()
        obs = self.cam.observe(frame, step_idx=step.idx, frame_index=idx, t=t,
                               min_electrodes=self.min_electrodes,
                               min_saturation=self.min_saturation)
        return idx, frame, obs

    def read_raw(self):
        """A frame with no analysis run on it.

        The corner picker needs this: detection converts pixels to electrode
        coordinates, which requires the registration the picker exists to
        establish. Calling read() before corners are picked raises
        "camera is not registered to the chip" on the very first frame.
        """
        return self.cam.read_frame()[1]

    def close(self) -> None:
        self.cam.close_stream()


# ── live view ────────────────────────────────────────────────────────────────

class LiveView:
    """Commanded frame beside the camera view. No-op when headless.

    This is the "show electrode actuation taking place" half of Priority 1, and
    it is also how the operator notices a run going wrong early enough to stop
    it.

    This base class is the **headless implementation**: every method is a
    working no-op. It is what a synthetic or --headless run gets, and what any
    run gets when OpenCV is unavailable.

    Structured as a null object rather than as one class holding
    ``cv2 = None``. Under the old shape, ``self.cv2`` was genuinely None on
    every headless run, and methods like ``render_commanded`` dereferenced it
    without a guard -- safe only because their one caller happened to check
    first. That is an invariant living in the call sites rather than in the
    object, and it would break the moment anything else called them. Here the
    subclass holds a real module or does not exist, so there is nothing to
    guard and nothing to get wrong.
    """

    enabled = False

    def render_commanded(self, step, swept):
        return None

    def show(self, step, swept, frame, events) -> bool:
        """No window to draw. Never asks the caller to stop."""
        return True

    def close(self) -> None:
        pass


class OpenCvLiveView(LiveView):
    """The real two-pane window.

    ``cv2`` and ``np`` are injected and non-optional: an instance of this class
    cannot exist without them.
    """

    enabled = True

    def __init__(self, chip_rows: int, chip_cols: int, cv2, np,
                 scale: int = 4) -> None:
        self.rows = chip_rows
        self.cols = chip_cols
        self.scale = scale
        self.cv2 = cv2
        self.np = np

    def render_commanded(self, step, swept):
        """The client-side model: what we believe is energised.

        Exact and free -- it comes from our own record of what was sent, not
        from the device, which reports nothing.
        """
        np = self.np
        canvas = np.zeros((self.rows, self.cols, 3), dtype=np.uint8)
        if swept:
            idx = np.array(sorted(swept), dtype=int)
            canvas[idx[:, 0] - 1, idx[:, 1] - 1] = (40, 40, 40)
        r0, r1, c0, c1 = step.covers()
        canvas[r0 - 1:r1, c0 - 1:c1] = (0, 200, 255)
        return self.cv2.resize(canvas, (self.cols * self.scale, self.rows * self.scale),
                               interpolation=self.cv2.INTER_NEAREST)

    def show(self, step, swept, frame, events) -> bool:
        """Draw one update. Returns False if the operator pressed q."""
        panes = [self.render_commanded(step, swept)]
        if frame is not None:
            h = panes[0].shape[0]
            w = int(frame.shape[1] * h / frame.shape[0])
            panes.append(self.cv2.resize(frame, (w, h)))
        canvas = self.np.hstack(panes) if len(panes) > 1 else panes[0]
        if events:
            self.cv2.putText(canvas, f"{len(events)} event(s)", (10, 24),
                             self.cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        self.cv2.imshow("chip health - commanded | camera", canvas)
        return self.cv2.waitKey(1) & 0xFF != ord("q")

    def close(self) -> None:
        self.cv2.destroyAllWindows()


def _try_import_cv2():
    """(cv2, numpy) if OpenCV is importable, else (None, None)."""
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError:
        return None, None


def make_live_view(chip_rows: int, chip_cols: int, enabled: bool = True,
                   scale: int = 4) -> LiveView:
    """Pick the real window or the headless no-op. The only place that decides."""
    if not enabled:
        return LiveView()
    cv2, np = _try_import_cv2()
    if cv2 is None:
        log.warning("OpenCV not available -- running without the live window.")
        return LiveView()
    return OpenCvLiveView(chip_rows, chip_cols, cv2, np, scale)


class CornerPicker:
    """Click the four corners of the electrode array in the live window.

    A thin OpenCV shell -- every check that can be made about a set of corners
    lives in ``calibration.validate_corners`` and is tested without a camera.

    Registration is redone each run because the camera moves between runs. The
    previous run's corners are offered as a starting proposal, so a small nudge
    does not mean picking blind.

    ``cv2`` is injected and non-optional: use :meth:`create`, which returns
    None when OpenCV is unavailable. An instance therefore always has a working
    module, so ``pick`` and ``_draw`` need no guards -- previously they had
    none *and* dereferenced a possibly-None attribute, safe only because the
    single call site checked first.
    """

    WINDOW = "registration - click the 4 corners of the ELECTRODE ARRAY"

    def __init__(self, cv2) -> None:
        self.cv2 = cv2

    @classmethod
    def create(cls) -> "CornerPicker | None":
        """A picker, or None if OpenCV is unavailable."""
        cv2, _ = _try_import_cv2()
        if cv2 is None:
            log.warning("OpenCV not available -- cannot pick corners.")
            return None
        return cls(cv2)

    def pick(self, grab, proposal=None):
        """Returns four (x, y) points, or None if the operator cancelled."""
        cv2 = self.cv2
        pts: list[tuple[float, float]] = []
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)

        def on_mouse(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
                pts.append((float(x), float(y)))

        cv2.setMouseCallback(self.WINDOW, on_mouse)
        try:
            while True:
                frame = grab()
                canvas = self._draw(frame, pts, proposal)
                cv2.imshow(self.WINDOW, canvas)
                key = cv2.waitKey(30) & 0xFF
                if key in (13, 32) and len(pts) == 4:      # enter / space
                    return [tuple(p) for p in pts]
                if key == ord("a") and proposal and not pts:
                    return [tuple(p) for p in proposal]
                if key == ord("u") and pts:
                    pts.pop()
                if key == ord("r"):
                    pts.clear()
                if key in (27, ord("q")):                  # esc / q
                    return None
        finally:
            cv2.destroyWindow(self.WINDOW)

    def _draw(self, frame, pts, proposal):
        cv2 = self.cv2
        canvas = frame.copy()
        if proposal and not pts:
            for i in range(4):
                a = tuple(int(v) for v in proposal[i])
                b = tuple(int(v) for v in proposal[(i + 1) % 4])
                cv2.line(canvas, a, b, (80, 80, 80), 1)
        for i, (x, y) in enumerate(pts):
            cv2.circle(canvas, (int(x), int(y)), 6, (0, 200, 255), -1)
            cv2.putText(canvas, calibration.CORNER_NAMES[i], (int(x) + 8, int(y) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        for i in range(1, len(pts)):
            cv2.line(canvas, tuple(int(v) for v in pts[i - 1]),
                     tuple(int(v) for v in pts[i]), (0, 200, 255), 1)
        if len(pts) == 4:
            cv2.line(canvas, tuple(int(v) for v in pts[3]),
                     tuple(int(v) for v in pts[0]), (0, 200, 255), 1)

        nxt = (calibration.CORNER_NAMES[len(pts)] if len(pts) < 4
               else "done - enter to accept")
        lines = [f"click: {nxt}",
                 "u undo | r reset | enter accept | q cancel"]
        if proposal and not pts:
            lines.append("a = accept previous run's corners (grey outline)")
        for i, text in enumerate(lines):
            cv2.putText(canvas, text, (12, 26 + i * 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        return canvas


# ── prompts ──────────────────────────────────────────────────────────────────

@dataclass
class Prompter:
    """Structured operator prompts.

    Legitimate only where a physical human action is genuinely required --
    loading oil or sample, adjusting focus. Not after routine DLL calls, which
    is the defect in chipsetup.py:30-58. Every prompt is logged with what was
    asked and what came back (docs/spec/objectives.md §0.1).
    """

    recorder: RunRecorder
    interactive: bool = True

    def ask(self, question: str, t: float = 0.0) -> str:
        if not self.interactive:
            self.recorder.log_prompt(question, "auto-ack (non-interactive)", t)
            log.info("PROMPT (auto): %s", question)
            return ""
        answer = input(f"\n>>> {question}\n    [enter to continue] ").strip()
        self.recorder.log_prompt(question, answer or "ack", t)
        return answer

    def confirm(self, question: str, detail: str = "", t: float = 0.0) -> bool:
        """A real yes/no gate. Anything other than yes does not proceed.

        Non-interactive runs auto-confirm, and the log records that they did --
        so an unattended run is never mistaken for one a human checked.
        """
        if not self.interactive:
            self.recorder.log_prompt(question, "auto-confirmed (non-interactive)", t)
            log.info("CONFIRM (auto): %s", question)
            return True
        if detail:
            print(f"\n{detail}")
        while True:
            answer = input(f"\n>>> {question} [y/n] ").strip().lower()
            if answer in ("y", "yes"):
                self.recorder.log_prompt(question, "yes", t)
                return True
            if answer in ("n", "no"):
                self.recorder.log_prompt(question, "no", t)
                return False
            print("    please answer y or n")


# ── the run ──────────────────────────────────────────────────────────────────

class HealthRun:

    def __init__(self, cfg: RunConfig, chip: ChipController, source,
                 recorder: RunRecorder, view: LiveView,
                 prompter: Prompter) -> None:
        self.cfg = cfg
        self.chip = chip
        self.source = source
        self.rec = recorder
        self.view = view
        self.ask = prompter
        self.det = Detector(cfg.detector, block=cfg.sweep.block, stage="coarse")
        self.t0 = time.monotonic()
        self.pos = (cfg.sweep.start_row, cfg.sweep.start_col)
        self.aborted = False
        self._low_liquid = 0
        self._topups = 0
        self._prev_cal = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    # ── phases ───────────────────────────────────────────────────────────────

    def phase0_preflight(self) -> None:
        log.info("PHASE 0  preflight")
        # The calibration is recorded with every run. Without it, a bad region
        # found months from now cannot be told apart from a remount artifact.
        self.rec.start({"armed": self.cfg.armed,
                        "backend": type(self.chip.backend).__name__,
                        "schema_version": SCHEMA_VERSION,
                        "detector_version": DETECTOR_VERSION,
                        "corners_px": self.cfg.capture.corners_px,
                        "expected_frame_size": self.cfg.capture.expected_frame_size,
                        "actual_frame_size": getattr(self.source, "frame_size", None)})
        self.chip.open()
        if not self.cfg.armed:
            # Say this in the artifact, not only on the console. A dry run
            # still drives the camera and the detector, so it still produces a
            # coverage map -- and that map is meaningless, because nothing was
            # energised and therefore nothing could move. Every step commands a
            # window that marches away from a stationary droplet, which is the
            # textbook drag / no_movement / residue signature. Without this note
            # the run folder is indistinguishable from a real measurement
            # reporting a badly degraded chip.
            msg = ("DRY-RUN: no electrode was energised, so nothing could move. "
                   "The coverage map and every event in this run are ARTEFACTS "
                   "OF NOT ENERGISING, not measurements of the chip -- a "
                   "stationary droplet under a moving commanded window produces "
                   "drag, no_movement and residue by construction. Use this run "
                   "to check the camera, corner-picking and detector plumbing "
                   "only. Pass --arm for a run whose verdicts mean anything.")
            log.warning(msg)
            self.rec.note(msg)

    def phase0c_clearance(self) -> None:
        """Refuse the whole run if any drop it plans to command is off-grid.

        Runs before phase 1, so before a single electrode is energised and
        before the operator is asked to put liquid anywhere.

        Every frame is independently gated at ``ChipController.activate``, so
        nothing here is load-bearing for safety -- what it buys is TIMING. The
        per-frame gate fires at the first bad frame, which for a sweep is
        several hundred steps and several minutes in, with liquid on the chip
        and a half-written run folder. Measuring the plan as a whole up front
        turns that into a refusal before the load prompt.

        The three places a drop is loaded or moved in this run:

          resting frame  the 20x20 hold energised at phase 1 so the operator
                         has somewhere to load into (this hardware cannot hold
                         a droplet at 0V)
          registration   phase 2 measures the droplet against that same window
          coarse sweep   every commanded window of the serpentine, plus the
                         vertical pass when ``axes="both"``

        The FINE pass is deliberately not here. Its probe positions come from
        which blocks the coarse sweep flagged, so they do not exist yet; those
        frames are gated one at a time at ``activate`` like everything else,
        and :meth:`phase6_fine` says so.

        Raises ClearanceViolation rather than returning False like the other
        phase gates. A geometry that does not fit is a configuration error, not
        an operator decision -- there is no answer the operator could give that
        would make it fit, so there is nothing to ask.
        """
        log.info("PHASE 0c  clearance")
        s, chip_cfg = self.cfg.sweep, self.cfg.chip
        allow = self.chip.allow_violations

        window = Drop(s.window_h, s.window_w, s.start_row, s.start_col)
        # Lazy, and in this order deliberately. The load position is the thing
        # an operator can actually act on, so it must be the first thing that
        # fails -- and `plan_serpentine` has its own start_col guard that would
        # otherwise raise first with a message about bands.
        checks: list[tuple[str, Callable[[], list]]] = [
            (f"phase 1 resting frame ({s.window_h}x{s.window_w} at "
             f"row {s.start_row}, col {s.start_col})", lambda: [window]),
            ("phase 2 registration window", lambda: [window]),
            ("phase 4 coarse sweep", self._coarse_steps),
        ]

        for what, items in checks:
            c = clearance.require(items(), chip_cfg.rows, chip_cfg.cols,
                                  what=what, allow_violations=allow)
            log.info("  %s", c.describe().splitlines()[0])
            if not c.ok:
                # Only reachable with the override on -- require() raised
                # otherwise. Put it in the artifact, not just the console.
                self.rec.note(
                    f"CLEARANCE OVERRIDE: {what} does not fit and was allowed "
                    f"anyway (--allow-clearance-violations). Short on "
                    f"{c.short_sides()}. Coordinates outside the electrode "
                    f"array were sent to the vendor DLL, whose behaviour there "
                    f"is unspecified -- this run's geometry is not trustworthy."
                )

    def phase0b_voltage(self) -> bool:
        """Confirm the voltage connection before anything depends on it.

        A gate, not a log line. If a rail is dead the run would otherwise sweep
        all 899 moves and report the whole chip as failing -- an expensive way
        to discover a loose connector, and a misleading result to have sitting
        in the longitudinal record.
        """
        log.info("PHASE 0b  voltage confirmation")
        check = self.chip.verify_voltage()
        for line in check.summary().splitlines():
            (log.info if (check.ok or check.dry_run) else log.error)(line)

        self.rec.note(f"Voltage check: {check.summary().splitlines()[0]}")
        if not check.ok:
            self.rec.note(f"Voltage mismatches: {list(check.mismatches)}")

        question = ("Is the voltage connection verified and good? "
                    "(n stops the run)")
        if not self.ask.confirm(question, check.summary(), self.now()):
            self.rec.note("Operator did not confirm the voltage connection; run "
                          "stopped before the sweep.")
            log.error("Voltage not confirmed by the operator -- stopping.")
            return False
        return True

    def phase1_load(self, attempts: int = 3) -> bool:
        """The load gate: pause, tell the operator what to load, wait.

        Re-asks rather than assuming. Registration (phase 2) independently
        verifies the droplet really is where it should be, but that is a second
        line of defence, not a substitute for asking.
        """
        s = self.cfg.sweep
        log.info("PHASE 1  operator load")

        # Energise the target region BEFORE asking for liquid. Holding a
        # droplet at a position needs active voltage on this hardware, so a
        # prompt with nothing energised asks the operator to place liquid into
        # a region that cannot hold it. Every legacy script does it this way --
        # 1pixsplit.py activates the drop rectangle and only then prompts
        # "Drop loaded"; chipsetup.py calls ActivateElec then waits.
        #
        # The frame persists until the next ActivateElec, so this holds through
        # registration and baseline, and the sweep's first step continues from
        # it (window moves (2,5) -> (1,5), one electrode).
        if self.chip.armed:
            hold = Drop(s.window_h, s.window_w, s.start_row, s.start_col)
            self.chip.activate([hold], settle=False)
            log.info("Holding %dx%d at row %d, col %d -- load into the "
                     "energised region.", s.window_h, s.window_w,
                     s.start_row, s.start_col)
        else:
            log.info("DRY-RUN: no holding frame energised, so liquid will not "
                     "stay. Expected for a dry run.")

        instruction = (
            f"LOAD THE SUBSTANCE:\n"
            f"  1. Silicon oil filler.\n"
            f"  2. Test substance on top.\n"
            f"  3. Form a {s.window_h}x{s.window_w} droplet at "
            f"row {s.start_row}, col {s.start_col} (top-left region).\n"
            f"     That region is energised and holding -- load into it.")
        for attempt in range(1, attempts + 1):
            self.ask.ask(instruction, self.now())
            if self.ask.confirm(
                    f"Is the {s.window_h}x{s.window_w} droplet loaded at "
                    f"row {s.start_row}, col {s.start_col} and ready?",
                    t=self.now()):
                return True
            log.warning("Load not confirmed (attempt %d of %d).", attempt, attempts)
        self.rec.note("Operator never confirmed the initial load; run stopped.")
        return False

    def phase2_registration(self, corners=None) -> bool:
        """Register the chip, then validate against the known initial droplet.

        The droplet's position and size are known, so a wrong coordinate frame
        can be caught here for free -- before anything is energised. A silently
        wrong frame would poison every verdict in the run.
        """
        log.info("PHASE 2  registration")
        if not self._check_frame_size():
            return False

        self._prev_cal = calibration.load_cache(self.cfg.capture.calibration_cache)
        cam = getattr(self.source, "cam", None)

        if cam is not None:
            corners = self._obtain_corners(corners, cam)
            if corners is None:
                # Say what to do about it. This is the only place the operator
                # finds out registration did not happen.
                msg = ("No chip registration for this run, so no verdict would "
                       "mean anything. Pick the four corners of the 128x128 "
                       "electrode array at phase 2, or pass --corners "
                       "'x,y;x,y;x,y;x,y' (TL;TR;BR;BL), or set "
                       "capture.corners_px in config, or --reuse-calibration if "
                       "the camera has not moved since the last run.")
                log.error(msg)
                self.rec.note(msg)
                return False
            cam.set_registration(corners, self.cfg.chip.rows, self.cfg.chip.cols)
            self.ask.ask(
                "Adjust focus by hand now (autofocus is off)." if not
                self.cfg.capture.autofocus else
                "Check focus looks right (autofocus is on).", self.now())
            self._record_calibration(corners, cam)

        if self.cfg.skip_droplet_check:
            # Holding a droplet at a known position needs 45V on this hardware,
            # so a no-voltage run has nothing to check the frame against. The
            # picker and the artifact path can still be exercised -- but every
            # coordinate downstream is then trusted rather than confirmed.
            msg = ("Droplet check SKIPPED (--no-droplet-check): the coordinate "
                   "frame is UNVERIFIED. Positions this run are trusted, not "
                   "confirmed. Do not read its verdicts as measurements. "
                   "Top-up prompts are disabled too -- with no droplet, every "
                   "frame would look like liquid running out.")
            log.warning(msg)
            self.rec.note(msg)
            return True

        s = self.cfg.sweep
        probe = sweep.Step(idx=-1, row=s.start_row, col=s.start_col,
                           h=s.window_h, w=s.window_w, axis=sweep.AXIS_COL,
                           direction=+1, kind=sweep.KIND_TRAVEL, band=-1)
        _, frame, obs = self.source.read(probe, self.now())
        expected = {
            "centroid_row": s.start_row + s.window_h / 2.0 - 0.5,
            "centroid_col": s.start_col + s.window_w / 2.0 - 0.5,
            "area_electrodes": float(s.window_h * s.window_w),
            "start_row": s.start_row, "start_col": s.start_col,
            "window_h": s.window_h, "window_w": s.window_w,
            "centroid_tol_electrodes":
                self.cfg.detector.registration_centroid_tol_electrodes,
            "area_tol_frac": self.cfg.detector.registration_area_tol_frac,
        }
        primary = obs.primary()
        if primary is None:
            msg = ("Registration check: no droplet visible. Aborted before "
                   "energising. See registration_failure.json/.jpg for the "
                   "frame and every blob the detector found.")
            self.rec.log_registration_failure(
                obs, expected, ["no blob detected at all"], frame)
            self.rec.note(msg)
            log.error("No droplet detected -- cannot verify registration.")
            return False

        res = check_registration(
            (primary.centroid_row, primary.centroid_col), primary.area_electrodes,
            s.start_row, s.start_col, s.window_h, s.window_w,
            self.cfg.detector.registration_centroid_tol_electrodes,
            self.cfg.detector.registration_area_tol_frac)
        if not res.ok:
            # Save before returning. This abort happens before the baseline and
            # before any step is driven, so without this the run folder holds no
            # evidence of what the camera saw at the moment it refused.
            self.rec.log_registration_failure(obs, expected, res.reasons, frame)
            for reason in res.reasons:
                log.error("Registration check failed: %s", reason)
                self.rec.note(f"Registration check failed: {reason}")
            log.error("Saved the frame and all %d detected blob(s) to %s",
                      len(obs.blobs), self.rec.paths.registration_json.name)
            self.rec.note(
                f"Registration failure evidence saved: "
                f"{self.rec.paths.registration_json.name} lists all "
                f"{len(obs.blobs)} blob(s) found; the check judges the LARGEST, "
                f"so a glare patch or chip edge bigger than the droplet is what "
                f"gets measured.")
            return False
        log.info("Registration OK (centroid error %.2f electrodes, area ratio %.2f)",
                 res.centroid_error_electrodes, res.area_ratio)
        return True

    def _obtain_corners(self, explicit, cam, attempts: int = 3):
        """Where this run's registration comes from, in priority order.

        Explicit ``--corners`` wins, then an explicitly reused cache, then the
        operator picks. Picking is the default because the camera moves between
        runs.
        """
        cap = self.cfg.capture
        cached = self._prev_cal

        if explicit:
            log.info("Using corners supplied on the command line / in config.")
            return tuple(tuple(p) for p in explicit)

        if cap.reuse_calibration and cached:
            actual = getattr(self.source, "frame_size", None)
            if actual and tuple(actual) != tuple(cached.frame_size):
                log.warning("Cached calibration was taken at %sx%s but the camera "
                            "is delivering %sx%s -- not reusing it.",
                            *cached.frame_size, *actual)
            else:
                log.info("Reusing the cached calibration from %s.",
                         cached.created or "an earlier run")
                return cached.corners_px

        if not cap.pick_corners:
            return None

        picker = CornerPicker.create()
        if picker is None:
            self.rec.note("Corner picking needs OpenCV, which is unavailable.")
            return None

        frame_size = getattr(self.source, "frame_size", None)
        proposal = cached.corners_px if cached else None
        for attempt in range(1, attempts + 1):
            # read_raw, not read: analysis needs the registration we are about
            # to create, so running it here would fail on the first frame.
            picked = picker.pick(self.source.read_raw, proposal)
            if picked is None:
                log.warning("Corner picking cancelled.")
                return None

            problems = calibration.validate_corners(picked, frame_size)
            if not problems:
                return tuple(tuple(p) for p in picked)

            for problem in problems:
                log.error("Corner check: %s", problem)
                self.rec.note(f"Rejected corner pick (attempt {attempt}): {problem}")
            if attempt < attempts and not self.ask.confirm(
                    "Those corners do not look right. Pick again?",
                    "\n".join(f"  - {p}" for p in problems), self.now()):
                return None
        return None

    def _resting_step(self):
        """A no-op commanded window, for grabbing frames outside the sweep."""
        s = self.cfg.sweep
        return sweep.Step(idx=-1, row=s.start_row, col=s.start_col, h=s.window_h,
                          w=s.window_w, axis=sweep.AXIS_COL, direction=+1,
                          kind=sweep.KIND_TRAVEL, band=-1)

    def _record_calibration(self, corners, cam) -> None:
        """Persist this run's registration and how far it moved since the last.

        A moving camera changes apparent scale as well as position. Detection
        adapts on its own, since every threshold downstream of registration is
        in electrode units -- but the measurement is genuinely noisier at lower
        magnification, so the scale goes in the record. A noisy week should be
        explicable, not mysterious.
        """
        # isinstance, not hasattr: this wants a real ElectrodeFrame, and saying
        # so narrows the type instead of leaving `reg` possibly-None at the
        # dereference. The old hasattr check was also quietly shaped around a
        # test stub whose `registration` is a plain string.
        reg = getattr(cam, "registration", None)
        ppe: tuple[float, float] = (0.0, 0.0)
        if isinstance(reg, ElectrodeFrame):
            px_col, px_row = reg.px_per_electrode()
            ppe = (float(px_col), float(px_row))

        frame_size = calibration.as_frame_size(
            getattr(self.source, "frame_size", None) or (0, 0))

        cal = calibration.Calibration(
            corners_px=calibration.as_corners(corners),
            frame_size=frame_size, px_per_electrode=ppe,
            created=datetime.now(timezone.utc).isoformat(),
            chip_id=self.cfg.chip_id)

        report = calibration.drift_report(cal, self._prev_cal)
        line = calibration.describe_drift(report)
        (log.warning if report.get("warn") else log.info)(line)
        self.rec.note(f"Calibration: {line}")
        if ppe != (0.0, 0.0):
            log.info("Scale: %.2f x %.2f px per electrode.", *ppe)
            # Every detector threshold is in electrode units, so the scale sets
            # what they mean in pixels. The 2026-08-10 dry run came in at
            # 1.7 x 1.4, making the 1-electrode residue threshold ~2.4 px^2 --
            # noise. Say so during the run, not afterwards in analysis.
            worst = min(ppe)
            if worst < MIN_USABLE_PX_PER_ELECTRODE:
                msg = (f"SCALE TOO COARSE: {worst:.2f} px per electrode. One "
                       f"electrode is ~{worst ** 2:.1f} px^2, so a 1-electrode "
                       f"threshold is near the noise floor and verdicts will "
                       f"not be trustworthy. Raise the capture resolution or "
                       f"move the camera closer so the chip fills more of the "
                       f"frame.")
                log.warning(msg)
                self.rec.note(msg)

        self.rec.record_calibration({"calibration": cal.to_dict(),
                                     "drift": report})
        try:
            calibration.save_cache(self.cfg.capture.calibration_cache, cal)
        except OSError as exc:  # a read-only cwd must not kill the run
            log.warning("Could not cache the calibration: %s", exc)

    def _check_frame_size(self) -> bool:
        """Refuse a run whose frame size differs from the calibration's.

        The hardcoded corners are pixel coordinates, so a different capture
        resolution rescales every one of them. Nothing else in the run would
        notice: the homography still fits, registration near the load position
        still passes, and the error grows with distance from it. Cheap to check,
        silent and systematic if not.
        """
        expected = self.cfg.capture.expected_frame_size
        actual = getattr(self.source, "frame_size", None)
        if not expected or not actual:
            return True
        if tuple(expected) == tuple(actual):
            log.info("Frame size %sx%s matches the calibration.", *actual)
            return True

        msg = (f"FRAME SIZE MISMATCH: the camera is delivering "
               f"{actual[0]}x{actual[1]}, but corners_px was measured at "
               f"{expected[0]}x{expected[1]}. Every pixel coordinate in the "
               f"calibration is scaled wrong. Re-measure the corners, or fix "
               f"the capture resolution.")
        log.error(msg)
        self.rec.note(msg)
        return False

    def phase3_baseline(self) -> None:
        """Reference frames the residue check is differenced against.

        The holding frame from phase 1 is still applied, so this captures the
        chip with the droplet at rest -- which is what residue detection needs
        to compare against, and the only state in which a droplet exists at a
        known position on this hardware.
        """
        log.info("PHASE 3  baseline (%d frames)", self.cfg.capture.baseline_frames)
        s = self.cfg.sweep
        rest = sweep.Step(idx=-1, row=s.start_row, col=s.start_col, h=s.window_h,
                          w=s.window_w, axis=sweep.AXIS_COL, direction=+1,
                          kind=sweep.KIND_TRAVEL, band=-1)
        for i in range(self.cfg.capture.baseline_frames):
            _, frame, _ = self.source.read(rest, self.now())
            if frame is not None:
                self.rec._save(self.rec.paths.baseline / f"baseline_{i:02d}.jpg", frame)

    def _coarse_steps(self) -> list:
        """The planned coarse traversal. Pure -- energises nothing.

        Split out of :meth:`phase4_coarse` so :meth:`phase0c_clearance` can
        measure the very same steps that will later be driven. Planning it
        twice from the same config would also work right up until someone
        adds a parameter to one call site and not the other.
        """
        s = self.cfg.sweep
        steps = sweep.plan_serpentine(self.cfg.chip.rows, self.cfg.chip.cols,
                                      s.window_h, s.window_w,
                                      s.start_row, s.start_col,
                                      first_band_row=s.first_band_row,
                                      prime=s.prime_band0,
                                      max_bands=s.max_bands)
        if s.axes == "both":
            steps += sweep.plan_vertical(self.cfg.chip.rows, self.cfg.chip.cols,
                                         s.window_h, s.window_w,
                                         s.start_row, s.start_col,
                                         first_band_col=s.first_band_row,
                                         prime=s.prime_band0,
                                         max_bands=s.max_bands)
        return steps

    def phase4_coarse(self) -> list:
        log.info("PHASE 4  coarse sweep")
        s = self.cfg.sweep
        steps = self._coarse_steps()
        log.info("%d steps, %.1f min of delay alone at %.2fs",
                 len(steps), sweep.total_duration_s(steps, s.step_delay_s) / 60.0,
                 s.step_delay_s)

        if s.max_bands is not None:
            # Loud, and in the artifact. A truncated run must never be readable
            # later as a clean bill of health for the rows it never visited.
            total = len(sweep.plan_bands(self.cfg.chip.rows, s.window_h,
                                         s.first_band_row))
            msg = (f"PARTIAL SWEEP: {s.max_bands} of {total} bands. This run is "
                   f"NOT a coverage result -- every row outside those bands is "
                   f"untested and reported unknown. Intended for step-delay "
                   f"timing work only.")
            log.warning(msg)
            self.rec.note(msg)

        self.rec.coverage.never_covered_rows = sweep.uncovered_rows(
            self.cfg.chip.rows, s.window_h, s.first_band_row,
            max_bands=s.max_bands)

        # Verify the planned traversal actually reaches every electrode, and say
        # so if it does not. Cheap, and it means a geometry change can never
        # quietly reintroduce a blind spot.
        missed = sweep.untested_electrodes(steps, self.cfg.chip.rows,
                                           self.cfg.chip.cols)
        if missed:
            msg = (f"{len(missed)} electrodes are never under a leading edge in "
                   f"this traversal and cannot be tested by it.")
            log.warning(msg)
            self.rec.note(msg)
        else:
            log.info("Traversal reaches every one of %d electrodes.",
                     self.cfg.chip.rows * self.cfg.chip.cols)

        for step in steps:
            if not self._drive(step):
                self.aborted = True
                self.rec.note("Run stopped by the operator during the coarse sweep.")
                break
            self.pos = (step.row, step.col)
        return steps

    def phase5_triage(self) -> tuple[list, list]:
        log.info("PHASE 5  triage")
        suspicious = self.rec.coverage.suspicious_blocks()
        targets = [(br * self.cfg.sweep.block + 1, bc * self.cfg.sweep.block + 1)
                   for br, bc in suspicious]
        ordered, dropped = sweep.plan_fine_route(
            (float(self.pos[0]), float(self.pos[1])), targets,
            max_targets=self.cfg.sweep.max_fine_targets)
        if dropped:
            # Never silent: a truncated list would read as "everything checked".
            msg = (f"{len(dropped)} suspicious regions were NOT re-tested -- "
                   f"capped at {self.cfg.sweep.max_fine_targets}: {dropped}")
            log.warning(msg)
            self.rec.note(msg)
        log.info("%d suspicious blocks, %d queued for the fine pass",
                 len(suspicious), len(ordered))
        return ordered, dropped

    def phase6_fine(self, targets: list) -> None:
        """Re-test flagged regions -- with no reload, so this is a transport problem.

        The sweep leaves the liquid wherever band 7 ended. Reaching a flagged
        region means driving it there first, and the droplet can get stuck on
        the way: `unreachable` is a first-class outcome and is itself evidence
        about that path.

        CLEARANCE. This is the one phase :meth:`phase0c_clearance` cannot
        pre-measure: the probe and transport windows are derived from which
        blocks the coarse sweep flagged, so they do not exist until phase 5 has
        run. Every frame is still gated at ``ChipController.activate``, so an
        off-grid probe is refused rather than clipped -- it just surfaces here
        instead of before the load prompt.
        """
        if not targets or self.aborted:
            # Say so. Silence here is indistinguishable from a skipped phase.
            log.info("PHASE 6  fine pass skipped (%s)",
                     "run aborted" if self.aborted else "nothing flagged")
            return
        log.info("PHASE 6  fine pass (%d targets)", len(targets))
        self.det.stage = "fine"

        probe_ok = self._split_probe()
        h, w = ((self.cfg.sweep.probe_h, self.cfg.sweep.probe_w) if probe_ok
                else (self.cfg.sweep.window_h, self.cfg.sweep.window_w))
        if not probe_ok:
            msg = ("Probe split failed -- fine pass degraded to "
                   f"{self.cfg.sweep.window_w}-wide edge localisation. Flagged "
                   "regions are still re-tested, but not at 4x4 resolution.")
            log.warning(msg)
            self.rec.note(msg)

        for target in targets:
            if self.aborted:
                break
            if not self._transport_to(target, h, w):
                ev = self.det.unreachable(target, step_idx=-1, frame_index=-1,
                                          t=self.now(),
                                          spent=self._last_transport_spent,
                                          budget=self._last_transport_budget)
                self.rec.log_event(ev)
                log.warning("unreachable: %s", target)

    def phase7_shutdown(self) -> dict:
        log.info("PHASE 7  shutdown")
        self.chip.close()
        self.source.close()
        self.view.close()
        stats = self.rec.finalize({"aborted": self.aborted})
        log.info("coverage (%d-electrode blocks, %d total): %s",
                 self.cfg.sweep.block ** 2, sum(stats["coverage"].values()),
                 stats["coverage"])
        log.info("artifacts: %s", self.rec.paths.root)
        return stats

    def run(self, corners=None) -> dict:
        # The calibration normally lives in config (fixed camera); an explicit
        # argument or --corners overrides it for a one-off.
        corners = corners if corners is not None else self.cfg.capture.corners_px
        try:
            self.phase0_preflight()
            # Before the voltage prompt and before the load prompt: a geometry
            # that does not fit should cost the operator nothing but a message.
            self.phase0c_clearance()
            if not self.phase0b_voltage():
                self.aborted = True
                return self.phase7_shutdown()
            if not self.phase1_load():
                self.aborted = True
                log.error("Aborting: the substance was never confirmed loaded.")
                return self.phase7_shutdown()
            if not self.phase2_registration(corners):
                self.aborted = True
                log.error("Aborting before the sweep: registration is not "
                          "trustworthy, so no verdict would be either.")
                return self.phase7_shutdown()
            self.phase3_baseline()
            self.phase4_coarse()
            targets, _ = self.phase5_triage()
            self.phase6_fine(targets)
            return self.phase7_shutdown()
        except BaseException as exc:
            # De-energise on every exit path, including Ctrl-C.
            log.exception("Run failed -- de-energising.")
            try:
                self.chip.close()
                self.source.close()
                self.view.close()
                self.rec.note(describe_exception(exc))
                self.rec.finalize({"aborted": True})
            finally:
                raise

    # ── internals ────────────────────────────────────────────────────────────

    def _drive(self, step) -> bool:
        """One commanded step: activate, observe, score, record. False = stop."""
        t = self.now()
        self.chip.activate([Drop(step.h, step.w, step.row, step.col)])
        _, frame, obs = self.source.read(step, t)
        res = self.det.observe(step, obs)
        self.rec.log_step(step, res)
        self.rec.log_observation(step, obs)

        for ev in res.events:
            roi = self._crop(frame, ev)
            self.rec.log_event(ev, full_frame=frame, roi=roi)
        if res.events:
            self.rec.capture_still(t, frame, flagged=True)
        elif frame is not None:
            self.rec.maybe_sample_negative(step, frame)
        if self.rec.should_capture_still(t):
            self.rec.capture_still(t, frame, flagged=False)

        if not self._check_liquid(step, res, t):
            return False
        return self.view.show(step, self.det.swept_cells, frame, res.events)

    def _check_liquid(self, step, res, t: float) -> bool:
        """Ask the operator to top up when the liquid is running out.

        All loading is manual and more can be added mid-run, so the run asks
        rather than assuming everything was present at the start
        (docs/spec/objectives.md §1.4 q1).

        Distinct from a fault: a shrinking or vanished droplet means there is
        nothing left to test with, not that the electrodes underneath are bad.
        Left unhandled, every remaining step would report failure.
        """
        cap = self.cfg.capture
        # Both this and the registration droplet check assume a real droplet
        # exists, so they travel together. Without one the largest on-chip blob
        # is some artifact -- ~89 electrodes against an expected 400 in the
        # 2026-08-10 run -- so every frame looks like liquid running out and the
        # operator gets prompted until max_topups stops it.
        if self.cfg.skip_droplet_check:
            return True
        if not cap.topup_enabled or self._topups >= cap.max_topups:
            return True

        expected = float(step.h * step.w)
        area = res.primary_area
        low = area is None or area < cap.topup_area_frac * expected
        self._low_liquid = self._low_liquid + 1 if low else 0
        if self._low_liquid < cap.topup_after_steps:
            return True

        self._low_liquid = 0
        self._topups += 1
        seen = "nothing visible" if area is None else f"{area:.0f} electrodes"
        msg = (f"Liquid low at step {step.idx} (row {step.row}, col {step.col}): "
               f"{seen} against an expected {expected:.0f}.")
        log.warning(msg)
        self.rec.note(msg)

        # De-energise while a human is working over the chip.
        if self.chip.armed:
            self.chip.deactivate_all()

        if not self.ask.confirm(
                "Top up the test substance, then confirm to resume. "
                "(n stops the run)",
                msg, t):
            self.rec.note("Operator stopped the run at a top-up prompt.")
            self.aborted = True
            return False

        if self._topups >= cap.max_topups:
            note = (f"Top-up limit ({cap.max_topups}) reached; no further "
                    f"top-up prompts this run.")
            log.warning(note)
            self.rec.note(note)

        # Re-establish the frame the operator interrupted.
        self.chip.activate([Drop(step.h, step.w, step.row, step.col)])
        return True

    def _crop(self, frame, event, pad: int = 8):
        """ROI around an event, for the training set."""
        if frame is None or not hasattr(self.source, "cam"):
            return None
        ef = self.source.cam.registration
        if ef is None:
            return None
        x, y = ef.electrode_to_pixel(event.row, event.col)
        pc, pr = ef.px_per_electrode()
        half_x, half_y = int(pad * pc), int(pad * pr)
        x0 = max(0, int(x) - half_x)
        y0 = max(0, int(y) - half_y)
        return frame[y0:int(y) + half_y, x0:int(x) + half_x]

    def _walk(self, frm, to, h, w, kind):
        """Frames translating a window from `frm` to `to`, one electrode at a time.

        Each electrode of travel is a grow/release PAIR, the same discipline the
        coarse sweep uses -- see sweep.grow_release. Moving the window in a
        single call asks the liquid to release behind and grab ahead at once,
        and on hardware 2026-08-10 it necked and split mid-transport. The fine
        pass runs on a probe droplet a fraction of the parent's volume, so it
        has even less to spare than the sweep did.

        Two frames per move, so ``len(steps)`` is twice the distance travelled;
        callers wanting distance want :meth:`_transport_to`'s ``moves``.
        """
        steps = []
        row, col = int(frm[0]), int(frm[1])
        idx = 0
        while col != int(to[1]):
            d = 1 if int(to[1]) > col else -1
            steps.extend(sweep.grow_release(idx, row, col, h, w,
                                            sweep.AXIS_COL, d, kind, band=-1))
            col += d
            idx += 2
        while row != int(to[0]):
            d = 1 if int(to[0]) > row else -1
            steps.extend(sweep.grow_release(idx, row, col, h, w,
                                            sweep.AXIS_ROW, d, kind, band=-1))
            row += d
            idx += 2
        return steps

    def _clamp_origin(self, rc, h, w):
        row = min(max(int(rc[0]), 1), self.cfg.chip.rows - h + 1)
        col = min(max(int(rc[1]), 1), self.cfg.chip.cols - w + 1)
        return row, col

    def _transport_to(self, target, h, w) -> bool:
        """Drive the window to a target. False if it did not arrive in budget."""
        dest = self._clamp_origin(target, h, w)
        budget = sweep.expected_transport_steps(
            (float(self.pos[0]), float(self.pos[1])),
            (float(dest[0]), float(dest[1])), self.cfg.sweep.fine_travel_slack)
        steps = self._walk(self.pos, dest, h, w, sweep.KIND_TRANSPORT)
        # Budget is in electrode MOVES, not commanded frames. _walk emits two
        # frames per move, so comparing len(steps) here would silently halve
        # fine_travel_slack -- and it would double the `spent`/`budget` numbers
        # recorded in every `unreachable` event, making them incomparable with
        # runs recorded before transport became a grow/release pair.
        moves = len(steps) // 2
        self._last_transport_budget = budget
        self._last_transport_spent = moves
        if moves > budget:
            return False
        for step in steps:
            if not self._drive(step):
                self.aborted = True
                return False
            self.pos = (step.row, step.col)
        return True

    def _split_probe(self) -> bool:
        """Split a small probe droplet off the main one.

        A 20x20 droplet has a 20-electrode-wide contact line, so it cannot
        localise a 4x4 block -- the fine pass needs something smaller. The
        sequence is the one already proven in dropsplitoff.py and 1pixsplit.py:
        stretch, then command both pieces in a single ActivateElec, then walk
        the piece away. `1pixsplit.py`'s header records the constraint that the
        piece height must match the drop being split.

        This is the most failure-prone step in the run. Success is confirmed
        optically -- there is no other way to know -- and failure degrades the
        fine pass rather than aborting it.
        """
        s = self.cfg.sweep
        row, col = self._clamp_origin(self.pos, s.window_h, s.window_w)
        ph, pw = s.probe_h, s.probe_w
        gap = 3
        walk = gap + 1

        # Room needed on whichever side the probe goes. The sweep ends at the
        # right-hand edge (col 109 of 109), so the rightward split usually does
        # NOT fit and the mirror is the normal case, not the fallback.
        need = s.window_w + 2 * gap + walk + pw
        if col + need - 1 <= self.cfg.chip.cols:
            direction = +1
            probe_col = col + s.window_w + gap
        elif col - (gap + pw + walk) >= 1:
            direction = -1
            probe_col = col - gap - pw
        else:
            log.warning("No room either side of the parent droplet at col %d to "
                        "split a %dx%d probe.", col, ph, pw)
            return False

        try:
            # Stretch one column at a time -- the discipline the split scripts use.
            for extra in range(1, gap + pw + 1):
                origin = col if direction > 0 else col - extra
                self.chip.activate([Drop(s.window_h, s.window_w + extra, row, origin)])
            # Both pieces in a single frame: parent, gap, probe.
            self.chip.activate([
                Drop(s.window_h, s.window_w, row, col),
                Drop(ph, pw, row, probe_col),
            ])
            # Walk the probe clear of the parent. NOT caterpillar: this is
            # dropsplitoff.py's proven walk-clear, and the split is the most
            # failure-prone step in the run, so it is not being changed without
            # rig evidence. It does carry the same grab/release hazard _walk
            # had -- revisit if the probe is seen splitting here on hardware.
            for off in range(1, walk + 1):
                self.chip.activate([
                    Drop(s.window_h, s.window_w, row, col),
                    Drop(ph, pw, row, probe_col + direction * off),
                ])
        except ValueError as exc:
            log.warning("Probe split could not be commanded: %s", exc)
            return False

        probe_origin = (row, probe_col + direction * walk)
        confirm = sweep.Step(idx=-1, row=probe_origin[0], col=probe_origin[1],
                             h=ph, w=pw, axis=sweep.AXIS_COL, direction=+1,
                             kind=sweep.KIND_PROBE, band=-1)
        _, _, obs = self.source.read(confirm, self.now())
        detached = [b for b in obs.blobs
                    if 0.25 * ph * pw <= b.area_electrodes <= 4.0 * ph * pw]
        if not detached:
            return False
        self.pos = probe_origin
        return True


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chiphealth.run_health",
        description=("Electrode actuation visualization + chip health check. "
                     "DRY-RUN BY DEFAULT: nothing is energised unless you pass "
                     "--arm (or set ACXCHIP_ARM=1)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every verdict is optical inference. This chip reports no "
               "per-electrode state, so nothing here is a device readback.")
    p.add_argument("--chip-id", required=True,
                   help="Which physical chip. Required: without it the "
                        "longitudinal history silently mixes chips.")
    p.add_argument("--arm", action="store_true",
                   help="ENERGISE THE CHIP. Omit for dry-run.")
    p.add_argument("--simulate", action="store_true",
                   help="Synthetic rig, no camera and no hardware.")
    p.add_argument("--dead", default="",
                   help="Simulate only: dead blocks as 'br,bc;br,bc' or "
                        "columns as 'col=61'.")
    p.add_argument("--camera", type=int, default=None,
                   help="OpenCV device index (default 0). Depends on which "
                        "cameras are connected, so check the picker shows the "
                        "chip and not another camera.")
    p.add_argument("--corners", default="",
                   help="Chip corners 'x,y;x,y;x,y;x,y' (TL;TR;BR;BL) of the "
                        "128x128 electrode array. Overrides capture.corners_px "
                        "in config, which is where a fixed camera's calibration "
                        "belongs.")
    p.add_argument("--frame-size", default="",
                   help="Frame size the corners were measured at, e.g. 1920x1080. "
                        "The run is refused if the camera delivers anything else.")
    p.add_argument("--reuse-calibration", action="store_true",
                   help="Skip corner picking and reuse the cached calibration. "
                        "Only when you know the camera has not moved.")
    p.add_argument("--no-pick", action="store_true",
                   help="Never open the corner picker; require --corners or a "
                        "reused cache. For scripted runs.")
    p.add_argument("--calibration-cache", default=None,
                   help="Where the previous run's corners are remembered "
                        "(default calibration.json).")
    p.add_argument("--backend", choices=("auto", "real", "fake"), default="auto")
    p.add_argument("--volt-settle", type=float, default=None,
                   help="Seconds between SetVolt and reading the rails back "
                        "(default 0.3, copied from csvvolcont.py).")
    p.add_argument("--volt-poll", action="store_true",
                   help="DIAGNOSTIC: poll InquireVolt every 0.25s while the "
                        "rails settle, instead of the single read the working "
                        "legacy scripts do. Prints each reading, so use it to "
                        "watch a supply that is not reaching 45V. Off by "
                        "default -- it makes many extra USB round-trips.")
    p.add_argument("--step-delay", type=float, default=None,
                   help="Seconds between activations. Defaults to 0 for a DRY "
                        "run (nothing is energised, so no reflow time is "
                        "needed -- the sweep is then camera-bound, ~1.5 min) "
                        "and 0.5 when ARMED. Armed runs are floored at 0.25s; "
                        "see --allow-fast-armed. 0.5 is the only value with "
                        "hardware behind it.")
    p.add_argument("--allow-fast-armed", action="store_true",
                   help="Permit an armed run below the 0.25s step-delay floor. "
                        "0.05s was one of three confounded candidate causes of "
                        "the 2026-08-10 droplet break-up; do not use this "
                        "without a reason you can write down.")
    p.add_argument("--allow-clearance-violations", action="store_true",
                   help="Permit loading or moving a drop that runs off the "
                        "electrode array. Off by default and there is no config "
                        "field for it, so it cannot be left switched on. The "
                        "vendor DLL's behaviour outside the array is "
                        "unspecified, so the frames it applies may not be the "
                        "ones planned and the run's geometry is not "
                        "trustworthy; taking this is recorded in run.json.")
    p.add_argument("--block", type=int, default=None, help="Fine block size.")
    p.add_argument("--bands", type=int, default=None,
                   help="Stop after N bands instead of sweeping the whole chip "
                        "(7 bands at the default geometry). FOR TIMING WORK, "
                        "NOT MEASUREMENT: a step-delay ramp needs the same short "
                        "traversal at several delays. Rows outside those bands "
                        "are untested and reported unknown, and the run is "
                        "marked PARTIAL SWEEP in its notes.")
    p.add_argument("--axes", choices=("h", "both"), default=None,
                   help="'both' adds a vertical sweep at double the cost.")
    p.add_argument("--max-fine-targets", type=int, default=None)
    p.add_argument("--runs-root", default=None)
    p.add_argument("--no-droplet-check", action="store_true",
                   help="Skip the phase-2 droplet check. For a no-voltage run: "
                        "holding a droplet at a known position needs 45V, so "
                        "with the chip unpowered there is nothing to verify the "
                        "coordinate frame against. Positions become trusted "
                        "rather than confirmed.")
    p.add_argument("--autofocus", action="store_true",
                   help="Leave camera autofocus ON. Default is off, which suits "
                        "a microscope with a manual focus ring; a webcam has no "
                        "ring, so off can leave it stuck out of focus.")
    p.add_argument("--headless", action="store_true", help="No live window.")
    p.add_argument("--non-interactive", action="store_true",
                   help="Auto-acknowledge operator prompts.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def parse_dead(spec: str) -> set[tuple[int, int]]:
    dead: set[tuple[int, int]] = set()
    for part in (s.strip() for s in spec.split(";") if s.strip()):
        if part.startswith("col="):
            dead |= simulate.dead_column(int(part[4:]))
        else:
            br, bc = (int(v) for v in part.split(","))
            dead |= simulate.dead_block(br, bc)
    return dead


def parse_corners(spec: str):
    if not spec.strip():
        return None
    pts = [[float(v) for v in part.split(",")]
           for part in spec.split(";") if part.strip()]
    if len(pts) != 4:
        raise ValueError("need exactly four corners: 'x,y;x,y;x,y;x,y'")
    return pts


def describe_exception(exc: BaseException) -> str:
    """What ended the run, in a form that survives in the artifact.

    This used to record the bare string "Run ended with an exception". The
    exception itself went only to ``log.exception``, i.e. to the console -- so
    the moment a terminal scrolled or was closed, the one piece of information
    worth keeping was gone, and the run folder could not say what had happened.

    Ctrl-C is called out separately because it is not a failure, and a run
    folder that reports it as one sends the next reader hunting for a bug.
    """
    where = ""
    tb = exc.__traceback__
    while tb is not None:            # walk to the innermost frame
        f = tb.tb_frame
        where = f"{os.path.basename(f.f_code.co_filename)}:{tb.tb_lineno} " \
                f"in {f.f_code.co_name}"
        tb = tb.tb_next

    if isinstance(exc, KeyboardInterrupt):
        return (f"Run INTERRUPTED by the operator (Ctrl-C) at {where}; chip "
                f"de-energised. Not a failure -- the artifact is partial "
                f"because the run was stopped, not because anything went wrong.")
    msg = str(exc).strip()
    return (f"Run ended with {type(exc).__name__}"
            f"{': ' + msg if msg else ''} at {where}; chip de-energised.")


@dataclass
class StepDelayCheck:
    """Whether this run's step delay is allowed, and what to record about it."""

    ok: bool
    message: str
    note: str | None = None   # recorded in run.json when timing is non-default


def check_step_delay(cfg, allow_fast_armed: bool = False) -> StepDelayCheck | None:
    """Gate fast timing on whether anything is actually energised.

    **Dry-run: no floor at all.** `ChipController.activate` never calls
    `ActivateElec` when disarmed, and `open()` skips `SetPower`/`SetVolt`, so no
    electrode is energised and there is no liquid being driven. The reflow
    constraint that makes 0.05s dangerous simply does not exist. `--step-delay 0`
    is the right setting for iterating on the camera, registration and detector
    paths.

    **Armed: floored, because there is liquid.** 0.05s on 2026-08-10 is one of
    three confounded candidate causes of the droplet coming apart. The floor is
    what lets fast values be used freely in dry-run without one leaking into an
    armed run by way of shell history.

    Anything below the proven 0.5s is allowed but recorded, so a run at
    non-default timing can never be mistaken for a proven-timing one months
    later.
    """
    delay = cfg.sweep.step_delay_s
    floor = cfg.sweep.armed_min_step_delay_s
    if not cfg.armed:
        return None
    if delay < floor and not allow_fast_armed:
        return StepDelayCheck(
            ok=False,
            message=(
                f"--step-delay {delay} is below the {floor}s floor for an ARMED "
                f"run. Fast timing is fine for dry runs -- nothing is energised "
                f"and there is no liquid -- but with liquid on the chip 0.05s "
                f"was one of three confounded candidate causes of the "
                f"2026-08-10 droplet break-up.\n"
                f"  0.5   the default, and the only value with hardware behind "
                f"it (1pixsplit.py, cleanup.py).\n"
                f"  0.25  the floor: caterpillar splits one electrode of travel "
                f"into two frames, so 0.25 per frame gives the liquid the same "
                f"0.5s per electrode the legacy scripts do.\n"
                f"Pass --allow-fast-armed to override."))
    if delay < 0.5:
        return StepDelayCheck(
            ok=True,
            message=(f"Armed run at --step-delay {delay}, below the proven 0.5s."),
            note=(f"NON-DEFAULT TIMING: step_delay_s={delay} (proven value is "
                  f"0.5). One electrode of travel got {2 * delay:.2f}s across "
                  f"the grow/release pair; the legacy scripts give it 0.5s. "
                  f"Read this run's drag/lag figures with that in mind."))
    return None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    cfg = from_env(RunConfig())
    cfg.chip_id = args.chip_id
    cfg.armed = cfg.armed or args.arm
    cfg.backend = args.backend
    cfg.headless = args.headless
    cfg.chip.volt_poll_diagnostic = args.volt_poll
    if args.volt_settle is not None:
        cfg.chip.volt_settle_s = args.volt_settle
    if args.step_delay is not None:
        cfg.sweep.step_delay_s = args.step_delay
    elif not cfg.armed:
        # Nothing is energised in a dry run, so nothing needs time to reflow.
        # Loud rather than silent: the timing a run used has to be visible.
        cfg.sweep.step_delay_s = cfg.sweep.dry_run_step_delay_s
        log.info("DRY-RUN: step delay %.2fs (armed runs default to %.2fs). "
                 "Nothing is energised, so no reflow time is needed. "
                 "Override with --step-delay.",
                 cfg.sweep.dry_run_step_delay_s, SweepConfig().step_delay_s)
    if args.block is not None:
        cfg.sweep.block = args.block
    if args.bands is not None:
        if args.bands < 1:
            log.error("--bands must be at least 1, got %d", args.bands)
            return 2
        cfg.sweep.max_bands = args.bands
    if args.axes is not None:
        cfg.sweep.axes = args.axes
    if args.max_fine_targets is not None:
        cfg.sweep.max_fine_targets = args.max_fine_targets
    if args.runs_root is not None:
        from pathlib import Path
        cfg.runs_root = Path(args.runs_root)
    if args.camera is not None:
        cfg.capture.camera_address = args.camera
    cfg.require_chip_id()

    fast = check_step_delay(cfg, allow_fast_armed=args.allow_fast_armed)
    if fast is not None and not fast.ok:
        log.error(fast.message)
        return 2

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec = RunRecorder(cfg, run_id, cfg.chip_id, image_writer=_image_writer())

    backend = make_backend("fake" if args.simulate else cfg.backend,
                           cfg.dll_dir, cfg.dll_name,
                           cfg.chip.rows, cfg.chip.cols)
    chip = ChipController(backend, cfg.chip.rows, cfg.chip.cols, cfg.chip.volts,
                          armed=cfg.armed, step_delay_s=cfg.sweep.step_delay_s,
                          volt_tolerance=cfg.chip.volt_tolerance,
                          volt_settle_s=cfg.chip.volt_settle_s,
                          power_settle_s=cfg.chip.power_settle_s,
                          volt_poll_diagnostic=cfg.chip.volt_poll_diagnostic,
                          allow_violations=args.allow_clearance_violations)

    cli_corners = parse_corners(args.corners)
    if cli_corners:
        # as_corners validates the shape, so a malformed --corners fails here
        # with a clear message rather than inside a homography fit.
        cfg.capture.corners_px = calibration.as_corners(cli_corners)
    if args.frame_size:
        w, h = (int(v) for v in args.frame_size.lower().split("x"))
        cfg.capture.expected_frame_size = (w, h)
    cfg.capture.autofocus = args.autofocus
    cfg.skip_droplet_check = args.no_droplet_check
    cfg.capture.reuse_calibration = args.reuse_calibration
    cfg.capture.pick_corners = not args.no_pick
    if args.calibration_cache:
        cfg.capture.calibration_cache = args.calibration_cache

    if args.simulate:
        source = SyntheticSource(simulate.SyntheticRig(dead=parse_dead(args.dead)))
    else:
        source = _camera_source(cfg)

    view = make_live_view(cfg.chip.rows, cfg.chip.cols,
                          enabled=not cfg.headless and not args.simulate)
    prompter = Prompter(rec, interactive=not (args.non_interactive or args.simulate))

    run = HealthRun(cfg, chip, source, rec, view, prompter)
    if fast is not None and fast.note:
        # Before run(), so it lands in run.json even if the run aborts early.
        log.warning(fast.message)
        rec.note(fast.note)
    stats = run.run()
    blk = cfg.sweep.block
    print(f"\nRun {run_id}: {stats['events']} events, "
          f"coverage in {blk}x{blk}-electrode blocks {stats['coverage']}")
    print(f"Artifacts: {rec.paths.root}")
    return 1 if stats.get("aborted") else 0


def _image_writer():
    try:
        import cv2
    except ImportError:
        log.warning("OpenCV not available -- no images will be written.")
        return None
    return lambda path, frame: cv2.imwrite(path, frame)


def _find_camera_dir() -> Path:
    """Locate the directory holding the researcher's camera.py.

    It has moved once already (repo root -> colormixing/), which broke the
    import silently for every --camera run while leaving --simulate and the
    whole test suite green. So this checks the known places, then searches one
    level down, and refuses to guess when it finds more than one candidate --
    picking wrong would mean silently running against a stale copy.
    """
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "colormixing", root):
        if (candidate / "camera.py").is_file():
            return candidate

    found = sorted({p.parent for p in root.glob("*/camera.py")})
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ImportError(
            f"Found camera.py in more than one place: {[str(p) for p in found]}. "
            f"Cannot tell which is the maintained copy; remove the duplicates.")
    raise ImportError(
        f"Could not find camera.py under {root}. Expected it at "
        f"colormixing/camera.py or the repository root.")


def _camera_source(cfg: RunConfig):
    camera_dir = _find_camera_dir()
    sys.path.insert(0, str(camera_dir))
    log.info("Using camera module from %s", camera_dir)
    from camera import CameraInterface  # the researcher's own camera module

    cam = CameraInterface(camera_address=cfg.capture.camera_address)
    requested = None
    if cfg.capture.frame_width and cfg.capture.frame_height:
        requested = (cfg.capture.frame_width, cfg.capture.frame_height)
    cam.open_stream(autofocus=cfg.capture.autofocus, resolution=requested)

    # Measure what the device is actually delivering, so the hardcoded pixel
    # calibration can be checked against it in phase 2.
    _, frame = cam.read_frame()
    height, width = frame.shape[:2]
    log.info("Camera delivering %dx%d", width, height)
    if requested and (width, height) != requested:
        log.warning("Requested %dx%d but the camera returned %dx%d -- it does "
                    "not support that mode.", *requested, width, height)

    if cfg.capture.corners_px:
        cam.set_registration(cfg.capture.corners_px, cfg.chip.rows, cfg.chip.cols)
    return CameraSource(cam, frame_size=(width, height))


if __name__ == "__main__":
    raise SystemExit(main())
