"""Orchestrator: the eight-phase chip-health run.

    0 preflight      1 load prompt    2 registration   3 baseline
    4 coarse sweep   5 triage         6 fine pass      7 shutdown

One process, three modules: capture, actuation, and a pure detector
(spec/p1_chip_health_design.md §3). OpenCV is imported lazily and only for the
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
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from pathlib import Path

from . import DETECTOR_VERSION, SCHEMA_VERSION
from . import calibration, simulate, sweep
from .actuation import ChipController, Drop, make_backend
from .config import RunConfig, from_env
from .detector import Detector, Observation
from .geometry import check_registration
from .recorder import DEGRADED, FAIL, RunRecorder

log = logging.getLogger("chiphealth")


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

    def close(self) -> None:
        self.cam.close_stream()


# ── live view ────────────────────────────────────────────────────────────────

class LiveView:
    """Commanded frame beside the camera view. No-op when headless.

    This is the "show electrode actuation taking place" half of Priority 1, and
    it is also how the operator notices a run going wrong early enough to stop
    it.
    """

    def __init__(self, chip_rows: int, chip_cols: int, enabled: bool = True,
                 scale: int = 4) -> None:
        self.rows = chip_rows
        self.cols = chip_cols
        self.scale = scale
        self.cv2 = None
        self.np = None
        if not enabled:
            return
        try:  # lazy: a synthetic run must not need OpenCV
            import cv2
            import numpy as np
            self.cv2, self.np = cv2, np
        except ImportError:
            log.warning("OpenCV not available -- running without the live window.")

    @property
    def enabled(self) -> bool:
        return self.cv2 is not None

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
        if not self.enabled:
            return True
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
        if self.enabled:
            self.cv2.destroyAllWindows()


class CornerPicker:
    """Click the four corners of the electrode array in the live window.

    A thin OpenCV shell -- every check that can be made about a set of corners
    lives in ``calibration.validate_corners`` and is tested without a camera.

    Registration is redone each run because the camera moves between runs. The
    previous run's corners are offered as a starting proposal, so a small nudge
    does not mean picking blind.
    """

    WINDOW = "registration - click the 4 corners of the ELECTRODE ARRAY"

    def __init__(self) -> None:
        self.cv2 = None
        try:
            import cv2
            self.cv2 = cv2
        except ImportError:
            log.warning("OpenCV not available -- cannot pick corners.")

    @property
    def enabled(self) -> bool:
        return self.cv2 is not None

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
    asked and what came back (spec/objectives.md §0.1).
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
            log.warning("DRY-RUN: no electrode will be energised. Pass --arm for "
                        "a live run.")

    def phase0b_voltage(self) -> bool:
        """Confirm the voltage connection before anything depends on it.

        A gate, not a log line. If a rail is dead the run would otherwise sweep
        all 901 steps and report the whole chip as failing -- an expensive way
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
        instruction = (
            f"LOAD THE SUBSTANCE:\n"
            f"  1. Silicon oil filler.\n"
            f"  2. Test substance on top.\n"
            f"  3. Form a {s.window_h}x{s.window_w} droplet at "
            f"row {s.start_row}, col {s.start_col} (top-left region).")
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
            self.ask.ask("Adjust focus by hand now (autofocus is off).", self.now())
            self._record_calibration(corners, cam)

        s = self.cfg.sweep
        probe = sweep.Step(idx=-1, row=s.start_row, col=s.start_col,
                           h=s.window_h, w=s.window_w, axis=sweep.AXIS_COL,
                           direction=+1, kind=sweep.KIND_TRAVEL, band=-1)
        _, _, obs = self.source.read(probe, self.now())
        primary = obs.primary()
        if primary is None:
            self.rec.note("Registration check: no droplet visible. Aborted before "
                          "energising.")
            log.error("No droplet detected -- cannot verify registration.")
            return False

        res = check_registration(
            (primary.centroid_row, primary.centroid_col), primary.area_electrodes,
            s.start_row, s.start_col, s.window_h, s.window_w,
            self.cfg.detector.registration_centroid_tol_electrodes,
            self.cfg.detector.registration_area_tol_frac)
        if not res.ok:
            for reason in res.reasons:
                log.error("Registration check failed: %s", reason)
                self.rec.note(f"Registration check failed: {reason}")
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

        picker = CornerPicker()
        if not picker.enabled:
            self.rec.note("Corner picking needs OpenCV, which is unavailable.")
            return None

        frame_size = getattr(self.source, "frame_size", None)
        proposal = cached.corners_px if cached else None
        for attempt in range(1, attempts + 1):
            picked = picker.pick(lambda: self.source.read(
                self._resting_step(), self.now())[1], proposal)
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
        reg = getattr(cam, "registration", None)
        ppe = (tuple(reg.px_per_electrode()) if hasattr(reg, "px_per_electrode")
               else (0.0, 0.0))
        frame_size = tuple(getattr(self.source, "frame_size", None) or (0, 0))

        cal = calibration.Calibration(
            corners_px=tuple(tuple(float(v) for v in p) for p in corners),
            frame_size=frame_size, px_per_electrode=ppe,
            created=datetime.now(timezone.utc).isoformat(),
            chip_id=self.cfg.chip_id)

        report = calibration.drift_report(cal, self._prev_cal)
        line = calibration.describe_drift(report)
        (log.warning if report.get("warn") else log.info)(line)
        self.rec.note(f"Calibration: {line}")
        if ppe != (0.0, 0.0):
            log.info("Scale: %.2f x %.2f px per electrode.", *ppe)

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
        """Frames with nothing energised: the reference residue is differenced against."""
        log.info("PHASE 3  baseline (%d frames)", self.cfg.capture.baseline_frames)
        s = self.cfg.sweep
        rest = sweep.Step(idx=-1, row=s.start_row, col=s.start_col, h=s.window_h,
                          w=s.window_w, axis=sweep.AXIS_COL, direction=+1,
                          kind=sweep.KIND_TRAVEL, band=-1)
        for i in range(self.cfg.capture.baseline_frames):
            _, frame, _ = self.source.read(rest, self.now())
            if frame is not None:
                self.rec._save(self.rec.paths.baseline / f"baseline_{i:02d}.jpg", frame)

    def phase4_coarse(self) -> list:
        log.info("PHASE 4  coarse sweep")
        s = self.cfg.sweep
        steps = sweep.plan_serpentine(self.cfg.chip.rows, self.cfg.chip.cols,
                                      s.window_h, s.window_w,
                                      s.start_row, s.start_col,
                                      first_band_row=s.first_band_row,
                                      prime=s.prime_band0)
        if s.axes == "both":
            steps += sweep.plan_vertical(self.cfg.chip.rows, self.cfg.chip.cols,
                                         s.window_h, s.window_w,
                                         s.start_row, s.start_col,
                                         first_band_col=s.first_band_row,
                                         prime=s.prime_band0)
        log.info("%d steps, %.1f min of delay alone at %.2fs",
                 len(steps), sweep.total_duration_s(steps, s.step_delay_s) / 60.0,
                 s.step_delay_s)

        self.rec.coverage.never_covered_rows = sweep.uncovered_rows(
            self.cfg.chip.rows, s.window_h, s.first_band_row)

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
        """
        if not targets or self.aborted:
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
        log.info("coverage: %s", stats["coverage"])
        log.info("artifacts: %s", self.rec.paths.root)
        return stats

    def run(self, corners=None) -> dict:
        # The calibration normally lives in config (fixed camera); an explicit
        # argument or --corners overrides it for a one-off.
        corners = corners if corners is not None else self.cfg.capture.corners_px
        try:
            self.phase0_preflight()
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
        except BaseException:
            # De-energise on every exit path, including Ctrl-C.
            log.exception("Run failed -- de-energising.")
            try:
                self.chip.close()
                self.source.close()
                self.view.close()
                self.rec.note("Run ended with an exception; chip de-energised.")
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
        (spec/objectives.md §1.4 q1).

        Distinct from a fault: a shrinking or vanished droplet means there is
        nothing left to test with, not that the electrodes underneath are bad.
        Left unhandled, every remaining step would report failure.
        """
        cap = self.cfg.capture
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
        """Steps translating a window from `frm` to `to`, one electrode at a time."""
        steps = []
        row, col = int(frm[0]), int(frm[1])
        idx = 0
        while col != int(to[1]):
            d = 1 if int(to[1]) > col else -1
            col += d
            steps.append(sweep.Step(idx=idx, row=row, col=col, h=h, w=w,
                                    axis=sweep.AXIS_COL, direction=d,
                                    kind=kind, band=-1))
            idx += 1
        while row != int(to[0]):
            d = 1 if int(to[0]) > row else -1
            row += d
            steps.append(sweep.Step(idx=idx, row=row, col=col, h=h, w=w,
                                    axis=sweep.AXIS_ROW, direction=d,
                                    kind=kind, band=-1))
            idx += 1
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
        self._last_transport_budget = budget
        self._last_transport_spent = len(steps)
        if len(steps) > budget:
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
            # Walk the probe clear of the parent.
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
                   help="Camera index for the real run (camera.py uses 1).")
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
    p.add_argument("--step-delay", type=float, default=None,
                   help="Seconds between activations (default 0.5).")
    p.add_argument("--block", type=int, default=None, help="Fine block size.")
    p.add_argument("--axes", choices=("h", "both"), default=None,
                   help="'both' adds a vertical sweep at double the cost.")
    p.add_argument("--max-fine-targets", type=int, default=None)
    p.add_argument("--runs-root", default=None)
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


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    cfg = from_env(RunConfig())
    cfg.chip_id = args.chip_id
    cfg.armed = cfg.armed or args.arm
    cfg.backend = args.backend
    cfg.headless = args.headless
    if args.step_delay is not None:
        cfg.sweep.step_delay_s = args.step_delay
    if args.block is not None:
        cfg.sweep.block = args.block
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

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec = RunRecorder(cfg, run_id, cfg.chip_id, image_writer=_image_writer())

    backend = make_backend("fake" if args.simulate else cfg.backend,
                           cfg.dll_dir, cfg.dll_name,
                           cfg.chip.rows, cfg.chip.cols)
    chip = ChipController(backend, cfg.chip.rows, cfg.chip.cols, cfg.chip.volts,
                          armed=cfg.armed, step_delay_s=cfg.sweep.step_delay_s,
                          volt_tolerance=cfg.chip.volt_tolerance)

    cli_corners = parse_corners(args.corners)
    if cli_corners:
        cfg.capture.corners_px = tuple(tuple(p) for p in cli_corners)
    if args.frame_size:
        w, h = (int(v) for v in args.frame_size.lower().split("x"))
        cfg.capture.expected_frame_size = (w, h)
    cfg.capture.reuse_calibration = args.reuse_calibration
    cfg.capture.pick_corners = not args.no_pick
    if args.calibration_cache:
        cfg.capture.calibration_cache = args.calibration_cache

    if args.simulate:
        source = SyntheticSource(simulate.SyntheticRig(dead=parse_dead(args.dead)))
    else:
        source = _camera_source(cfg)

    view = LiveView(cfg.chip.rows, cfg.chip.cols,
                    enabled=not cfg.headless and not args.simulate)
    prompter = Prompter(rec, interactive=not (args.non_interactive or args.simulate))

    run = HealthRun(cfg, chip, source, rec, view, prompter)
    stats = run.run()
    print(f"\nRun {run_id}: {stats['events']} events, coverage {stats['coverage']}")
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
    cam.open_stream(autofocus=cfg.capture.autofocus)

    # Measure what the device is actually delivering, so the hardcoded pixel
    # calibration can be checked against it in phase 2.
    _, frame = cam.read_frame()
    height, width = frame.shape[:2]
    log.info("Camera delivering %dx%d (%.1f px per electrode across %d columns)",
             width, height, width / cfg.chip.cols, cfg.chip.cols)

    if cfg.capture.corners_px:
        cam.set_registration(cfg.capture.corners_px, cfg.chip.rows, cfg.chip.cols)
    return CameraSource(cam, frame_size=(width, height))


if __name__ == "__main__":
    raise SystemExit(main())
