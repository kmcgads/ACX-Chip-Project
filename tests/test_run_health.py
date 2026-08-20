"""Orchestrator and offline re-scoring, driven through the real entry points."""

import contextlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

import rescore
from chiphealth import simulate, sweep
from chiphealth.actuation import ChipController, FakeBackend
from chiphealth.config import DetectorConfig, RunConfig
from chiphealth.detector import Detector
from chiphealth.recorder import RunRecorder

from . import not_none
from chiphealth.run_health import (HealthRun, LiveView, Prompter, SyntheticSource,
                                   main, parse_corners, parse_dead)

ROWS = COLS = 128

# Two tests below assert the NO-OPENCV fallback path -- what happens on a
# machine where `import cv2` fails. They are statements about that environment,
# not about the code, so on a machine that HAS OpenCV they are unrunnable
# rather than failing: the picker really can be created, and asserting it comes
# back None is asserting something false about a working install.
#
# They were failing outright (not skipping) on the WSL box, which has cv2
# 4.13.0, and had been for long enough that two permanent red marks were
# background noise -- exactly the condition in which a real regression goes
# unnoticed. Guarded rather than deleted: the fallback they cover is real and
# still matters on a bare rig machine, which is where the chip-health run's
# corner picking actually has to degrade gracefully.
try:  # pragma: no cover - environment probe
    import cv2 as _cv2  # noqa: F401
    HAVE_CV2 = True
except Exception:  # pragma: no cover
    HAVE_CV2 = False

NEEDS_NO_CV2 = unittest.skipIf(
    HAVE_CV2, "asserts the no-OpenCV fallback; OpenCV is installed here")


class TestArgParsing(unittest.TestCase):

    def test_dead_blocks(self):
        dead = parse_dead("3,12")
        self.assertEqual(len(dead), 16)
        self.assertIn((13, 49), dead)

    def test_dead_column(self):
        self.assertEqual(len(parse_dead("col=61")), 128)

    def test_combined(self):
        """Block (3,12) covers cols 49-52, so it does not overlap column 61."""
        self.assertEqual(len(parse_dead("3,12;col=61")), 16 + 128)

    def test_corners(self):
        self.assertEqual(len(not_none(parse_corners("1,2;3,4;5,6;7,8"))), 4)
        self.assertIsNone(parse_corners(""))
        with self.assertRaises(ValueError):
            parse_corners("1,2;3,4")


class SimRun(unittest.TestCase):
    """Drive a full synthetic run through main()."""

    DEAD = "3,12;col=61"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)  # the run is chatty by design
        with contextlib.redirect_stdout(io.StringIO()):
            cls.rc = main(["--chip-id", "chip-T", "--simulate", "--dead", cls.DEAD,
                           "--runs-root", cls.tmp.name, "--headless",
                           "--non-interactive", "--step-delay", "0"])
        logging.disable(logging.NOTSET)
        cls.run_dir = next(Path(cls.tmp.name).iterdir())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def jsonl(self, name):
        return rescore.load_jsonl(self.run_dir / name)


class TestSyntheticRun(SimRun):

    def test_exit_code_zero(self):
        self.assertEqual(self.rc, 0)

    def test_all_artifacts_written(self):
        for name in ("run.json", "timeline.jsonl", "observations.jsonl",
                     "events.jsonl", "coverage.json", "summary.md"):
            self.assertTrue((self.run_dir / name).exists(), name)

    def test_coarse_sweep_plus_fine_pass_ran(self):
        self.assertGreater(len(self.jsonl("timeline.jsonl")), 867)

    def test_faults_were_found(self):
        events = [e for e in self.jsonl("events.jsonl")
                  if e.get("sample") != "negative"]
        self.assertTrue(events)
        self.assertIn("drag", {e["kind"] for e in events})

    def test_dry_run_by_default_so_nothing_was_energised(self):
        meta = json.loads((self.run_dir / "run.json").read_text())
        self.assertFalse(meta["armed"])

    def test_no_rows_are_left_uncovered(self):
        cov = json.loads((self.run_dir / "coverage.json").read_text())
        self.assertEqual(cov["never_covered_rows"], [])

    def test_run_confirms_full_electrode_coverage_in_its_own_notes(self):
        """The run verifies its own traversal, so a geometry change cannot
        quietly reintroduce a blind spot."""
        meta = json.loads((self.run_dir / "run.json").read_text())
        self.assertFalse(any("never under a leading edge" in n
                             for n in meta["notes"]), meta["notes"])

    def test_fine_pass_cap_is_reported_not_silent(self):
        meta = json.loads((self.run_dir / "run.json").read_text())
        self.assertTrue(any("NOT re-tested" in n for n in meta["notes"]),
                        f"notes were {meta['notes']}")

    def test_operator_prompts_are_logged_with_what_was_asked(self):
        meta = json.loads((self.run_dir / "run.json").read_text())
        asked = [p["asked"] for p in meta["prompts"]]
        self.assertTrue(asked)
        self.assertTrue(any("voltage connection" in a for a in asked), asked)
        self.assertTrue(any("LOAD THE SUBSTANCE" in a for a in asked), asked)
        self.assertTrue(any("20x20" in a for a in asked), asked)

    def test_auto_confirmations_are_recorded_as_such(self):
        """An unattended run must never look like one a human checked."""
        meta = json.loads((self.run_dir / "run.json").read_text())
        responses = {p["response"] for p in meta["prompts"]}
        self.assertTrue(any("non-interactive" in r for r in responses), responses)

    def test_voltage_check_is_recorded_in_the_notes(self):
        meta = json.loads((self.run_dir / "run.json").read_text())
        self.assertTrue(any(n.startswith("Voltage check:") for n in meta["notes"]),
                        meta["notes"])


class TestSeqIsUnique(SimRun):
    """Regression: step.idx repeats across fine-pass legs; seq must not.

    Keying the observation stream on step.idx silently paired coarse-pass steps
    with fine-pass observations, and re-scoring a run at its own thresholds then
    failed to reproduce its own results.
    """

    def test_step_idx_does_repeat(self):
        idxs = [r["step"] for r in self.jsonl("timeline.jsonl")]
        self.assertGreater(len(idxs), len(set(idxs)),
                           "expected step.idx to repeat across fine-pass legs")

    def test_seq_does_not_repeat(self):
        seqs = [r["seq"] for r in self.jsonl("timeline.jsonl")]
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_timeline_and_observations_align_one_to_one(self):
        t = {r["seq"] for r in self.jsonl("timeline.jsonl")}
        o = {r["seq"] for r in self.jsonl("observations.jsonl")}
        self.assertEqual(t, o)


class TestRescore(SimRun):

    def _cfg(self, **kw):
        return DetectorConfig(**kw)

    def test_identical_thresholds_reproduce_the_run_exactly(self):
        original = [e for e in self.jsonl("events.jsonl")
                    if e.get("sample") != "negative"]
        res = rescore.rescore(self.run_dir, self._cfg(), 4, ROWS, COLS)
        diff = rescore.compare(self.jsonl("events.jsonl"), res["events"])
        self.assertEqual(diff["gained"], [])
        self.assertEqual(diff["lost"], [])
        self.assertEqual(len(res["events"]), len(original))

    def test_every_step_is_replayed(self):
        res = rescore.rescore(self.run_dir, self._cfg(), 4, ROWS, COLS)
        self.assertEqual(res["replayed"], res["steps"])

    def test_raising_the_threshold_loses_events(self):
        strict = rescore.rescore(self.run_dir, self._cfg(lag_electrodes=6.0),
                                 4, ROWS, COLS)
        base = rescore.rescore(self.run_dir, self._cfg(), 4, ROWS, COLS)
        self.assertLess(len(strict["events"]), len(base["events"]))

    def test_label_promotion(self):
        events = self.jsonl("events.jsonl")
        eid = events[0]["event_id"]
        self.assertEqual(events[0]["label_source"], "auto")
        self.assertTrue(rescore.promote_label(self.run_dir, eid, "human_confirmed"))
        after = {e["event_id"]: e for e in self.jsonl("events.jsonl")}
        self.assertEqual(after[eid]["label_source"], "human_confirmed")
        # restore, so other tests in the class see the original state
        rescore.promote_label(self.run_dir, eid, "auto")

    def test_unknown_event_id_reports_not_found(self):
        self.assertFalse(rescore.promote_label(self.run_dir, "nope", "auto"))

    def test_invalid_label_rejected(self):
        with self.assertRaises(SystemExit):
            rescore.promote_label(self.run_dir, "any", "trust-me")


class TestRegistrationGuard(unittest.TestCase):
    """A wrong coordinate frame must stop the run before anything is energised."""

    def _run(self, rig):
        tmp = tempfile.TemporaryDirectory()
        cfg = RunConfig()
        cfg.chip_id = "chip-R"
        cfg.runs_root = Path(tmp.name)
        cfg.sweep.step_delay_s = 0.0
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = ChipController(be, ROWS, COLS, cfg.chip.volts, armed=True,
                              step_delay_s=0.0, sleep=lambda _s: None)
        rec = RunRecorder(cfg, "rtest", cfg.chip_id, image_writer=lambda p, f: None)
        run = HealthRun(cfg, chip, SyntheticSource(rig), rec,
                        LiveView(),
                        Prompter(rec, interactive=False))
        logging.disable(logging.CRITICAL)
        try:
            stats = run.run()
        finally:
            logging.disable(logging.NOTSET)
        return stats, be, rec, tmp

    def test_blank_frame_aborts_before_the_sweep(self):
        class Blank(simulate.SyntheticRig):
            def observe(self, step, frame_index, t):
                from chiphealth.detector import Observation
                return Observation(step.idx, frame_index, t, ())

        stats, be, rec, tmp = self._run(Blank())
        try:
            self.assertTrue(stats["aborted"])
            self.assertEqual(stats["steps"], 0)
            # Two activations now: the phase-1 holding frame, then the
            # shutdown clear. Energising before registration is verified is
            # unavoidable on this hardware -- a droplet cannot be held at a
            # known position without voltage, and registration cannot be
            # verified without a held droplet. What matters is that the abort
            # still de-energises: the LAST activation is the empty clear and
            # the supply is off.
            activations = [payload for name, payload in be.calls
                           if name == "ActivateElec"]
            self.assertEqual(activations, [[(20, 20, 5, 10)], []])
            self.assertEqual(activations[-1], [])
            self.assertEqual(be.frame, [])
            self.assertFalse(be.powered)
        finally:
            tmp.cleanup()

    def test_displaced_droplet_aborts_with_a_reason(self):
        class Displaced(simulate.SyntheticRig):
            def observe(self, step, frame_index, t):
                from chiphealth.detector import Blob, Observation
                b = Blob(centroid_row=90.0, centroid_col=90.0,
                         area_electrodes=400.0, row=80.0, col=80.0,
                         height=20.0, width=20.0)
                return Observation(step.idx, frame_index, t, (b,))

        stats, be, rec, tmp = self._run(Displaced())
        try:
            self.assertTrue(stats["aborted"])
            meta = json.loads(rec.paths.run_json.read_text())
            self.assertTrue(any("Registration check failed" in n
                                for n in meta["notes"]))
        finally:
            tmp.cleanup()

    def test_good_registration_proceeds(self):
        """A healthy chip flags nothing, so there is no fine pass to run.

        Exactly 867 steps -- the coarse sweep and nothing more.
        """
        stats, be, rec, tmp = self._run(simulate.SyntheticRig())
        try:
            self.assertFalse(stats.get("aborted"))
            self.assertEqual(stats["steps"], 1798)
            self.assertEqual(stats["events"], 0)
        finally:
            tmp.cleanup()


class ScriptedPrompter(Prompter):
    """A prompter with pre-canned yes/no answers, so gates can be exercised."""

    def __init__(self, recorder, answers):
        super().__init__(recorder, interactive=False)
        self.answers = list(answers)
        self.asked: list[str] = []
        self.confirmed: list[str] = []

    def ask(self, question, t=0.0):
        self.asked.append(question)
        self.recorder.log_prompt(question, "scripted", t)
        return ""

    def confirm(self, question, detail="", t=0.0):
        self.confirmed.append(question)
        answer = self.answers.pop(0) if self.answers else True
        self.recorder.log_prompt(question, "yes" if answer else "no", t)
        return answer


class GateCase(unittest.TestCase):

    def _build(self, answers, rig=None, armed=True, dead=(), source=None,
               **capture):
        tmp = tempfile.TemporaryDirectory()
        cfg = RunConfig()
        cfg.chip_id = "chip-G"
        cfg.runs_root = Path(tmp.name)
        cfg.sweep.step_delay_s = 0.0
        # Keep the calibration cache inside the temp dir. Left at its default it
        # writes project/calibration.json, which the next real run would then
        # offer to the operator as the "previous" corners -- fabricated test
        # coordinates presented as a starting proposal.
        cfg.capture.calibration_cache = str(Path(tmp.name) / "calibration.json")
        for k, v in capture.items():
            setattr(cfg.capture, k, v)
        be = FakeBackend(rows=ROWS, cols=COLS, dead=set(dead))
        chip = ChipController(be, ROWS, COLS, cfg.chip.volts, armed=armed,
                              step_delay_s=0.0, sleep=lambda _s: None,
                              volt_tolerance=cfg.chip.volt_tolerance)
        rec = RunRecorder(cfg, "gate", cfg.chip_id, image_writer=lambda p, f: None)
        prompter = ScriptedPrompter(rec, answers)
        run = HealthRun(cfg, chip,
                        source or SyntheticSource(rig or simulate.SyntheticRig()),
                        rec, LiveView(), prompter)
        return run, be, rec, prompter, tmp

    @staticmethod
    def _run_quietly(run):
        logging.disable(logging.CRITICAL)
        try:
            return run.run()
        finally:
            logging.disable(logging.NOTSET)


class StubCam:
    """Stands in for CameraInterface: just enough for the registration path."""

    def __init__(self, registered: bool = False) -> None:
        self._reg = "registration" if registered else None
        self.set_with = []

    @property
    def registration(self):
        return self._reg

    def set_registration(self, corners_px, chip_rows=128, chip_cols=128):
        self.set_with.append(corners_px)
        self._reg = "registration"
        return self._reg


class StubCameraSource(SyntheticSource):
    """A synthetic rig wearing a camera source's attributes."""

    def __init__(self, frame_size, cam=None, rig=None):
        super().__init__(rig or simulate.SyntheticRig())
        self.frame_size = frame_size
        self.cam = cam or StubCam()


CORNERS = ((100.0, 80.0), (1500.0, 90.0), (1495.0, 1000.0), (105.0, 995.0))


class TestCalibrationRecording(GateCase):
    """The calibration must be in the record, or a bad region found months from
    now cannot be told apart from a remount artifact."""

    def test_corners_and_frame_sizes_land_in_run_json(self):
        run, be, rec, prompter, tmp = self._build(
            [True, True], source=StubCameraSource((1920, 1080)),
            corners_px=CORNERS, expected_frame_size=(1920, 1080))
        try:
            self._run_quietly(run)
            meta = json.loads(rec.paths.run_json.read_text())
        finally:
            tmp.cleanup()
        self.assertEqual([tuple(p) for p in meta["corners_px"]], list(CORNERS))
        self.assertEqual(tuple(meta["expected_frame_size"]), (1920, 1080))
        self.assertEqual(tuple(meta["actual_frame_size"]), (1920, 1080))

    def test_config_corners_are_applied_without_a_cli_argument(self):
        cam = StubCam()
        run, be, rec, prompter, tmp = self._build(
            [True, True], source=StubCameraSource((1920, 1080), cam=cam),
            corners_px=CORNERS, expected_frame_size=(1920, 1080))
        try:
            self._run_quietly(run)
        finally:
            tmp.cleanup()
        self.assertEqual(len(cam.set_with), 1)
        self.assertEqual(tuple(cam.set_with[0]), CORNERS)


class TestFrameSizeGuard(GateCase):

    def test_mismatch_stops_the_run_before_sweeping(self):
        run, be, rec, prompter, tmp = self._build(
            [True, True], source=StubCameraSource((1280, 720)),
            corners_px=CORNERS, expected_frame_size=(1920, 1080))
        try:
            stats = self._run_quietly(run)
            notes = rec.notes
        finally:
            tmp.cleanup()
        self.assertTrue(stats["aborted"])
        self.assertEqual(stats["steps"], 0)
        self.assertTrue(any("FRAME SIZE MISMATCH" in n for n in notes), notes)
        self.assertTrue(any("1280x720" in n and "1920x1080" in n for n in notes))

    def test_matching_size_proceeds(self):
        run, be, rec, prompter, tmp = self._build(
            [True, True], source=StubCameraSource((1920, 1080)),
            corners_px=CORNERS, expected_frame_size=(1920, 1080))
        try:
            stats = self._run_quietly(run)
        finally:
            tmp.cleanup()
        self.assertFalse(stats.get("aborted"))
        self.assertEqual(stats["steps"], 1798)

    def test_no_expected_size_means_no_check(self):
        run, be, rec, prompter, tmp = self._build(
            [True, True], source=StubCameraSource((640, 480)),
            corners_px=CORNERS, expected_frame_size=None)
        try:
            stats = self._run_quietly(run)
        finally:
            tmp.cleanup()
        self.assertFalse(stats.get("aborted"))

    def test_synthetic_runs_are_unaffected(self):
        run, be, rec, prompter, tmp = self._build(
            [True, True], expected_frame_size=(1920, 1080))
        try:
            stats = self._run_quietly(run)
        finally:
            tmp.cleanup()
        self.assertFalse(stats.get("aborted"))


class TestPickerUsesRawFrames(unittest.TestCase):
    """The picker must not run detection on the frames it displays.

    Detection converts pixels to electrode coordinates, which needs the
    registration the picker exists to create. Binding the picker's frame grab
    to CameraSource.read raised "camera is not registered to the chip" on the
    very first frame, killing the run before a single frame was displayed --
    and it was invisible to --simulate and to every test.
    """

    class Cam:
        def __init__(self):
            self.observe_calls = 0
            self.frames = 0
            self.registration = None

        def read_frame(self):
            self.frames += 1
            return self.frames, f"frame{self.frames}"

        def observe(self, *a, **kw):
            self.observe_calls += 1
            raise RuntimeError("camera is not registered to the chip")

    def test_read_raw_returns_a_frame_without_analysing_it(self):
        from chiphealth.run_health import CameraSource
        cam = self.Cam()
        src = CameraSource(cam, frame_size=(1920, 1080))
        self.assertEqual(src.read_raw(), "frame1")
        self.assertEqual(cam.observe_calls, 0)

    def test_read_raw_grabs_a_fresh_frame_each_call(self):
        """The picker view is live, so every loop iteration must re-grab."""
        from chiphealth.run_health import CameraSource
        cam = self.Cam()
        src = CameraSource(cam, frame_size=(1920, 1080))
        self.assertEqual([src.read_raw() for _ in range(3)],
                         ["frame1", "frame2", "frame3"])
        self.assertEqual(cam.frames, 3)

    def test_read_would_have_failed_before_registration(self):
        """Pins the actual bug: the analysed path raises pre-registration."""
        from chiphealth.run_health import CameraSource
        cam = self.Cam()
        src = CameraSource(cam, frame_size=(1920, 1080))
        step = sweep.Step(idx=0, row=2, col=5, h=20, w=20, axis=sweep.AXIS_COL,
                          direction=+1, kind=sweep.KIND_TRAVEL, band=0)
        with self.assertRaises(RuntimeError):
            src.read(step, 0.0)

    def test_synthetic_source_exposes_the_same_interface(self):
        src = SyntheticSource(simulate.SyntheticRig())
        self.assertIsNone(src.read_raw())


class TestMissingRegistration(GateCase):

    def _no_corners_run(self, **capture):
        run, be, rec, prompter, tmp = self._build(
            [True, True], source=StubCameraSource((1920, 1080)),
            corners_px=None, **capture)
        try:
            stats = self._run_quietly(run)
            return stats, list(rec.notes)
        finally:
            tmp.cleanup()

    def test_no_pick_and_no_corners_aborts_with_an_actionable_message(self):
        """Previously this died deep in detect_droplets_wide with a bare
        RuntimeError, after the chip had already been powered up."""
        stats, notes = self._no_corners_run(pick_corners=False)
        self.assertTrue(stats["aborted"])
        self.assertEqual(stats["steps"], 0)
        # Match the abort message itself, not any note mentioning registration:
        # a loose selector here silently latched onto an unrelated note the
        # first time another one was added.
        message = next(n for n in notes if n.startswith("No chip registration"))
        self.assertIn("--corners", message)
        self.assertIn("TL;TR;BR;BL", message)
        self.assertIn("corners_px", message)
        self.assertIn("--reuse-calibration", message)

    @NEEDS_NO_CV2
    def test_picker_unavailable_says_so_and_still_aborts_cleanly(self):
        """No OpenCV on this machine, so the picker cannot open. The run must
        stop with an explanation rather than crash mid-sweep."""
        stats, notes = self._no_corners_run(pick_corners=True)
        self.assertTrue(stats["aborted"])
        self.assertEqual(stats["steps"], 0)
        self.assertTrue(any("OpenCV" in n for n in notes), notes)
        self.assertTrue(any("--corners" in n for n in notes), notes)


class TestVoltageGate(GateCase):

    def test_declining_stops_the_run_before_any_sweeping(self):
        run, be, rec, prompter, tmp = self._build([False])
        logging.disable(logging.CRITICAL)
        try:
            stats = run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertTrue(stats["aborted"])
        self.assertEqual(stats["steps"], 0)
        self.assertIn("voltage connection", prompter.confirmed[0])
        self.assertFalse(be.powered)

    def test_the_gate_comes_before_the_load_step(self):
        run, be, rec, prompter, tmp = self._build([False])
        logging.disable(logging.CRITICAL)
        try:
            run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertEqual(len(prompter.confirmed), 1)
        self.assertEqual(prompter.asked, [])  # never got to the load instruction

    def test_confirming_proceeds(self):
        run, be, rec, prompter, tmp = self._build([True, True])
        logging.disable(logging.CRITICAL)
        try:
            stats = run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertFalse(stats.get("aborted"))
        self.assertEqual(stats["steps"], 1798)

    def test_mismatch_is_surfaced_to_the_operator_and_logged(self):
        run, be, rec, prompter, tmp = self._build([False])
        # `readback`, not `volts`: SetVolt stores what it is given, so the only
        # way to model a supply that does not reach its commanded voltage --
        # the actual 2026-08-10 fault -- is to override what InquireVolt
        # returns. It must be set before phase 0 opens the chip, because the
        # rails are now read once at startup and that reading is what the gate
        # judges.
        be.readback = [45, 0, 45, 0, 0, 0, 0, 0, 0]
        logging.disable(logging.CRITICAL)
        try:
            run.phase0_preflight()
            ok = run.phase0b_voltage()
            meta_notes = rec.notes
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertFalse(ok)
        self.assertTrue(any("mismatch" in n.lower() for n in meta_notes), meta_notes)


class TestLoadGate(GateCase):

    def test_declining_three_times_stops_the_run(self):
        run, be, rec, prompter, tmp = self._build([True, False, False, False])
        logging.disable(logging.CRITICAL)
        try:
            stats = run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertTrue(stats["aborted"])
        self.assertEqual(stats["steps"], 0)
        self.assertEqual(len(prompter.asked), 3)  # re-asked, not assumed

    def test_instruction_names_the_substance_size_and_position(self):
        run, be, rec, prompter, tmp = self._build([True, True])
        logging.disable(logging.CRITICAL)
        try:
            run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        instruction = prompter.asked[0]
        self.assertIn("LOAD THE SUBSTANCE", instruction)
        self.assertIn("Silicon oil", instruction)
        self.assertIn("20x20", instruction)
        self.assertIn("row 5, col 10", instruction)

    def test_a_later_yes_still_proceeds(self):
        run, be, rec, prompter, tmp = self._build([True, False, True])
        logging.disable(logging.CRITICAL)
        try:
            stats = run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertFalse(stats.get("aborted"))
        self.assertEqual(len(prompter.asked), 2)


class TestTopUp(GateCase):
    """Liquid running out is not an electrode fault, and must not read as one."""

    class Draining(simulate.SyntheticRig):
        """Droplet shrinks away after a while."""

        def observe(self, step, frame_index, t):
            obs = super().observe(step, frame_index, t)
            if step.idx < 40 or not obs.blobs:
                return obs
            from chiphealth.detector import Blob, Observation
            b = obs.blobs[0]
            shrunk = Blob(centroid_row=b.centroid_row, centroid_col=b.centroid_col,
                          area_electrodes=20.0, row=b.row, col=b.col,
                          height=4.0, width=5.0)
            return Observation(obs.step_idx, obs.frame_index, obs.t, (shrunk,))

    def test_prompt_fires_when_the_droplet_shrinks(self):
        run, be, rec, prompter, tmp = self._build([True, True] + [True] * 10,
                                                  rig=self.Draining())
        logging.disable(logging.CRITICAL)
        try:
            run.run()
            notes = rec.notes
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertTrue(any("Liquid low" in n for n in notes), notes)
        self.assertTrue(any("Top up" in q for q in prompter.confirmed))

    def test_declining_a_top_up_stops_the_run(self):
        run, be, rec, prompter, tmp = self._build([True, True, False],
                                                  rig=self.Draining())
        logging.disable(logging.CRITICAL)
        try:
            stats = run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertTrue(stats["aborted"])
        self.assertLess(stats["steps"], 901)

    def test_prompting_is_capped_so_it_cannot_nag_forever(self):
        run, be, rec, prompter, tmp = self._build([True, True] + [True] * 50,
                                                  rig=self.Draining(),
                                                  max_topups=2)
        logging.disable(logging.CRITICAL)
        try:
            run.run()
            notes = rec.notes
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        topups = [q for q in prompter.confirmed if "Top up" in q]
        self.assertEqual(len(topups), 2)
        self.assertTrue(any("limit" in n for n in notes), notes)

    def test_disabled_by_config(self):
        run, be, rec, prompter, tmp = self._build([True, True],
                                                  rig=self.Draining(),
                                                  topup_enabled=False)
        logging.disable(logging.CRITICAL)
        try:
            run.run()
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertFalse(any("Top up" in q for q in prompter.confirmed))


class TestProbeSplit(unittest.TestCase):
    """The sweep ends at the right edge, so the probe must split leftward."""

    def _run_at(self, pos):
        tmp = tempfile.TemporaryDirectory()
        cfg = RunConfig()
        cfg.chip_id = "chip-P"
        cfg.runs_root = Path(tmp.name)
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = ChipController(be, ROWS, COLS, cfg.chip.volts, armed=True,
                              step_delay_s=0.0, sleep=lambda _s: None)
        rec = RunRecorder(cfg, "ptest", cfg.chip_id, image_writer=lambda p, f: None)
        rec.start()
        chip.open()
        run = HealthRun(cfg, chip, SyntheticSource(simulate.SyntheticRig()), rec,
                        LiveView(),
                        Prompter(rec, interactive=False))
        run.pos = pos
        ok = run._split_probe()
        return ok, run, tmp

    def test_splits_leftward_at_the_right_hand_edge(self):
        ok, run, tmp = self._run_at((109, 109))
        try:
            self.assertTrue(ok, "probe split failed at the sweep's end position")
            self.assertLess(run.pos[1], 109)
        finally:
            tmp.cleanup()

    def test_splits_rightward_with_room(self):
        ok, run, tmp = self._run_at((10, 10))
        try:
            self.assertTrue(ok)
            self.assertGreater(run.pos[1], 10)
        finally:
            tmp.cleanup()

    def test_probe_stays_on_chip_wherever_it_starts(self):
        for col in (1, 40, 80, 100, 109):
            ok, run, tmp = self._run_at((50, col))
            try:
                self.assertTrue(ok, f"failed at col {col}")
                self.assertGreaterEqual(run.pos[1], 1)
                self.assertLessEqual(run.pos[1] + 5 - 1, COLS)
            finally:
                tmp.cleanup()


class TestFineTransport(unittest.TestCase):
    """The fine pass is a caterpillar too: grow, then release.

    The coarse sweep was fixed after the droplet necked and split mid-transport
    on 2026-08-10, but `_walk` kept the old simultaneous grab/release -- and it
    runs on a probe droplet a fraction of the parent's volume, so it had even
    less liquid to spare than the sweep did.
    """

    def _runner(self, slack=2.0):
        tmp = tempfile.TemporaryDirectory()
        cfg = RunConfig()
        cfg.chip_id = "chip-F"
        cfg.runs_root = Path(tmp.name)
        cfg.sweep.fine_travel_slack = slack
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = ChipController(be, ROWS, COLS, cfg.chip.volts, armed=True,
                              step_delay_s=0.0, sleep=lambda _s: None)
        rec = RunRecorder(cfg, "ftest", cfg.chip_id, image_writer=lambda p, f: None)
        rec.start()
        chip.open()
        run = HealthRun(cfg, chip, SyntheticSource(simulate.SyntheticRig()), rec,
                        LiveView(), Prompter(rec, interactive=False))
        return run, tmp

    @staticmethod
    def cells(step):
        r0, r1, c0, c1 = step.covers()
        return {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}

    def walk(self, frm, to, h=5, w=5):
        run, tmp = self._runner()
        try:
            return run._walk(frm, to, h, w, sweep.KIND_TRANSPORT)
        finally:
            tmp.cleanup()

    # ── the pairing itself ───────────────────────────────────────────────────

    def test_each_move_is_a_pair(self):
        """Two commanded frames per electrode, on an L-shaped leg."""
        steps = self.walk((10, 10), (14, 17))   # 4 rows + 7 cols = 11 moves
        self.assertEqual(len(steps), 22)

    def test_frames_alternate_transport_then_release(self):
        steps = self.walk((10, 10), (14, 17))
        for grow, release in zip(steps[::2], steps[1::2]):
            self.assertEqual(grow.kind, sweep.KIND_TRANSPORT)
            self.assertEqual(release.kind, sweep.KIND_RELEASE)

    def test_a_grow_never_releases_anything(self):
        """The whole point -- the fine-pass twin of the coarse-sweep test."""
        for frm, to in (((10, 10), (14, 17)), ((60, 60), (55, 52)),
                        ((30, 5), (30, 9)), ((5, 30), (9, 30))):
            with self.subTest(frm=frm, to=to):
                prev = None
                for s in self.walk(frm, to):
                    cur = self.cells(s)
                    if prev is not None and s.kind == sweep.KIND_TRANSPORT:
                        self.assertTrue(prev <= cur,
                                        f"grow at step {s.idx} dropped cells")
                    prev = cur

    def test_no_frame_jumps_more_than_one_electrode(self):
        """EWOD transport cannot jump: the window must overlap the droplet.

        A grow holds the origin and widens; only the release advances it. So
        the bound is one, not exactly one.
        """
        frm = (10, 10)
        for to in ((14, 17), (6, 4), (10, 20), (20, 10)):
            with self.subTest(to=to):
                prev = frm
                for s in self.walk(frm, to):
                    self.assertLessEqual(
                        abs(s.row - prev[0]) + abs(s.col - prev[1]), 1,
                        f"step {s.idx} jumped from {prev} to {(s.row, s.col)}")
                    prev = (s.row, s.col)

    def test_the_walk_still_arrives(self):
        for to in ((14, 17), (6, 4), (10, 20), (20, 10)):
            with self.subTest(to=to):
                last = self.walk((10, 10), to)[-1]
                self.assertEqual((last.row, last.col), to)

    def test_a_zero_length_walk_emits_nothing(self):
        self.assertEqual(self.walk((10, 10), (10, 10)), [])

    def test_indices_are_unique_and_consecutive(self):
        steps = self.walk((10, 10), (14, 17))
        self.assertEqual([s.idx for s in steps], list(range(len(steps))))

    # ── the budget ───────────────────────────────────────────────────────────

    def test_budget_is_spent_in_moves_not_frames(self):
        """Recorded `spent` must stay comparable with runs from before the pair.

        Counting frames would double every `unreachable` record for the same
        distance and halve the effective `fine_travel_slack`.
        """
        run, tmp = self._runner()
        try:
            driven = []
            run._drive = lambda step: (driven.append(step), True)[1]
            run.pos = (10, 10)
            self.assertTrue(run._transport_to((14, 17), 5, 5))
            self.assertEqual(run._last_transport_spent, 11)   # moves
            self.assertEqual(len(driven), 22)                 # frames
            self.assertEqual(run.pos, (14, 17))
        finally:
            tmp.cleanup()

    def test_default_slack_still_reaches_targets_it_used_to(self):
        """Regression guard on the units: at slack 2.0 an 11-move leg is well
        inside a 22-move budget. Comparing frames would leave it at zero
        margin, and any slack below 2.0 would fail outright."""
        run, tmp = self._runner(slack=2.0)
        try:
            run._drive = lambda step: True
            run.pos = (10, 10)
            self.assertTrue(run._transport_to((14, 17), 5, 5))
            self.assertEqual(run._last_transport_budget, 22)
            self.assertLess(run._last_transport_spent,
                            run._last_transport_budget)
        finally:
            tmp.cleanup()

    def test_an_over_budget_leg_is_unreachable_and_drives_nothing(self):
        run, tmp = self._runner(slack=0.5)
        try:
            driven = []
            run._drive = lambda step: (driven.append(step), True)[1]
            run.pos = (10, 10)
            self.assertFalse(run._transport_to((14, 17), 5, 5))
            self.assertEqual(driven, [], "gave up but energised anyway")
            self.assertEqual(run._last_transport_spent, 11)
            self.assertEqual(run._last_transport_budget, 6)   # round(11 * 0.5)
        finally:
            tmp.cleanup()

    def test_the_unreachable_event_reports_moves(self):
        run, tmp = self._runner(slack=0.5)
        try:
            run._drive = lambda step: True
            run.pos = (10, 10)
            self.assertFalse(run._transport_to((14, 17), 5, 5))
            ev = run.det.unreachable((14, 17), step_idx=-1, frame_index=-1,
                                     t=0.0, spent=run._last_transport_spent,
                                     budget=run._last_transport_budget)
            self.assertIn("within 6 steps", ev.detail)
            self.assertIn("11 spent", ev.detail)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()


class TestNoOptionalCv2(unittest.TestCase):
    """cv2/np must never be a None attribute waiting to be dereferenced.

    The old shape set self.cv2 = None on every headless run and dereferenced it
    in render_commanded/pick without a guard -- safe only because the single
    caller happened to check first. Pylance was right to flag it. These tests
    pin the null-object structure that removed the Optional.
    """

    def test_headless_view_has_no_cv2_attribute_at_all(self):
        from chiphealth.run_health import LiveView
        v = LiveView()
        self.assertFalse(v.enabled)
        self.assertFalse(hasattr(v, "cv2"))
        self.assertFalse(hasattr(v, "np"))

    def test_headless_view_methods_are_working_no_ops(self):
        """Callable directly, not only from a guarded caller."""
        from chiphealth.run_health import LiveView
        v = LiveView()
        step = sweep.Step(idx=0, row=2, col=5, h=20, w=20, axis=sweep.AXIS_COL,
                          direction=+1, kind=sweep.KIND_TRAVEL, band=0)
        self.assertIsNone(v.render_commanded(step, set()))
        self.assertTrue(v.show(step, set(), None, []))
        v.close()

    def test_factory_returns_headless_view_when_disabled(self):
        from chiphealth.run_health import LiveView, make_live_view
        v = make_live_view(128, 128, enabled=False)
        self.assertIsInstance(v, LiveView)
        self.assertFalse(v.enabled)

    def test_factory_falls_back_to_headless_without_opencv(self):
        """No cv2 on this machine, so the real view is unreachable -- and the
        fallback must be an object, never None."""
        from chiphealth.run_health import make_live_view
        v = make_live_view(128, 128, enabled=True)
        self.assertIsNotNone(v)
        self.assertTrue(hasattr(v, "show"))

    def test_real_view_requires_cv2_by_construction(self):
        """OpenCvLiveView cannot exist without the modules it dereferences."""
        from chiphealth.run_health import OpenCvLiveView
        import inspect
        params = list(inspect.signature(OpenCvLiveView.__init__).parameters)
        self.assertIn("cv2", params)
        self.assertIn("np", params)

    @NEEDS_NO_CV2
    def test_picker_create_returns_none_without_opencv(self):
        from chiphealth.run_health import CornerPicker
        self.assertIsNone(CornerPicker.create())

    def test_picker_requires_cv2_by_construction(self):
        from chiphealth.run_health import CornerPicker
        import inspect
        self.assertIn("cv2",
                      list(inspect.signature(CornerPicker.__init__).parameters))


class TestNoDropletCheck(GateCase):
    """--no-droplet-check: for a no-voltage run, where 45V is needed to hold a
    droplet at a known position and there is therefore nothing to verify the
    coordinate frame against."""

    class Blank(simulate.SyntheticRig):
        def observe(self, step, frame_index, t):
            from chiphealth.detector import Observation
            return Observation(step.idx, frame_index, t, ())

    def test_blank_frame_still_aborts_by_default(self):
        run, be, rec, prompter, tmp = self._build([True, True], rig=self.Blank())
        try:
            self.assertTrue(self._run_quietly(run)["aborted"])
        finally:
            tmp.cleanup()

    def test_skipping_lets_a_no_droplet_run_proceed(self):
        run, be, rec, prompter, tmp = self._build([True, True], rig=self.Blank())
        run.cfg.skip_droplet_check = True
        try:
            stats = self._run_quietly(run)
            notes = list(rec.notes)
        finally:
            tmp.cleanup()
        self.assertFalse(stats.get("aborted"))
        self.assertEqual(stats["steps"], 1798)
        self.assertTrue(any("UNVERIFIED" in n for n in notes), notes)

    def test_the_run_record_says_positions_were_not_confirmed(self):
        """A run done this way must be identifiable later, not silently
        indistinguishable from a verified one."""
        run, be, rec, prompter, tmp = self._build([True, True], rig=self.Blank())
        run.cfg.skip_droplet_check = True
        try:
            self._run_quietly(run)
            meta = json.loads(rec.paths.run_json.read_text())
        finally:
            tmp.cleanup()
        note = next(n for n in meta["notes"] if "SKIPPED" in n)
        self.assertIn("UNVERIFIED", note)
        self.assertIn("trusted, not", note)

    def test_default_is_off(self):
        self.assertFalse(RunConfig().skip_droplet_check)


class TestScaleWarning(unittest.TestCase):
    """A coarse optical scale makes every electrode-unit threshold meaningless.

    The 2026-08-10 dry run came in at 1.7 x 1.4 px per electrode, which was only
    discovered in post-hoc analysis. The run should say so while it is running.
    """

    def _ppe_for(self, corners):
        from chiphealth.geometry import ElectrodeFrame
        return min(ElectrodeFrame.from_corners(corners, 128, 128).px_per_electrode())

    def test_the_real_dry_run_corners_are_below_the_usable_threshold(self):
        from chiphealth.run_health import MIN_USABLE_PX_PER_ELECTRODE
        actual = [(244.0, 215.0), (440.0, 215.0), (472.0, 398.0), (229.0, 391.0)]
        ppe = self._ppe_for(actual)
        self.assertLess(ppe, MIN_USABLE_PX_PER_ELECTRODE)
        self.assertAlmostEqual(ppe, 1.42, delta=0.05)

    def test_a_well_framed_chip_is_above_it(self):
        from chiphealth.run_health import MIN_USABLE_PX_PER_ELECTRODE
        full = [(60.0, 20.0), (1860.0, 20.0), (1860.0, 1060.0), (60.0, 1060.0)]
        self.assertGreater(self._ppe_for(full), MIN_USABLE_PX_PER_ELECTRODE)


class TestResolutionRequest(unittest.TestCase):

    def test_config_requests_1080p_by_default(self):
        cfg = RunConfig()
        self.assertEqual((cfg.capture.frame_width, cfg.capture.frame_height),
                         (1920, 1080))

    def test_camera_resolution_is_opt_in_so_legacy_callers_are_unchanged(self):
        """_open_camera and open_stream must default to the driver's own mode."""
        import ast, pathlib
        src = pathlib.Path("colormixing/camera.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "CameraInterface")
        for name in ("_open_camera", "open_stream"):
            fn = next(f for f in cls.body
                      if isinstance(f, ast.FunctionDef) and f.name == name)
            self.assertIn("resolution", [a.arg for a in fn.args.args])
            # self.fail() is NoReturn, so this narrows where assertIsInstance
            # cannot -- and says which node it actually found.
            default = fn.args.defaults[-1]
            if not isinstance(default, ast.Constant):
                self.fail(f"{name}'s last default is {type(default).__name__}, "
                          f"not a literal constant")
            self.assertIsNone(default.value)


class TestOnChipFilter(unittest.TestCase):
    """94% of the dry run's detections were background scored as liquid."""

    def test_detect_droplets_wide_discards_off_chip_blobs(self):
        import pathlib
        src = pathlib.Path("colormixing/camera.py").read_text(encoding="utf-8")
        body = src[src.index("def detect_droplets_wide"):src.index("def observe")]
        self.assertIn("ef.contains(row, col)", body)
        self.assertIn("continue", body)

    def test_contains_rejects_the_real_off_chip_centroids(self):
        """Actual centroids from runs/20260810T183909Z."""
        from chiphealth.geometry import ElectrodeFrame
        ef = ElectrodeFrame.from_corners(
            [(244.0, 215.0), (440.0, 215.0), (472.0, 398.0), (229.0, 391.0)], 128, 128)
        for row, col in [(168, -15), (-31, -53), (-44, 105), (-49, -12)]:
            self.assertFalse(ef.contains(row, col), f"({row},{col}) should be off-chip")
        self.assertTrue(ef.contains(64, 64))


class TestTopUpFollowsDropletCheck(GateCase):
    """--no-droplet-check must disable top-up prompts too.

    Both assume a real droplet. Without one, the largest on-chip blob is an
    artifact far smaller than the commanded window, so every frame reads as
    liquid running out -- five prompts before max_topups stopped it in the
    2026-08-10 run.
    """

    class Tiny(simulate.SyntheticRig):
        """Primary blob far below the top-up threshold, as an empty chip gives.

        The registration probe (step.idx < 0) gets a full-size droplet so the
        run can get past phase 2 when the droplet check is enabled -- otherwise
        it aborts there and never reaches the sweep this test is about.

        Both blobs are derived from the configured load position rather than
        hardcoded. They used to be literals matching row 2, col 5; when the load
        position moved to (5, 10) the probe no longer sat where registration
        expected it, phase 2 aborted, and this test failed for a reason that had
        nothing to do with top-up prompts.
        """

        def observe(self, step, frame_index, t):
            from chiphealth.config import SweepConfig
            from chiphealth.detector import Blob, Observation
            s = SweepConfig()
            # Electrode k spans [k-0.5, k+0.5], so a window at (row, col) has
            # its bbox origin half a cell back and its centroid half a cell in
            # from the far edge.
            r0, c0 = s.start_row - 0.5, s.start_col - 0.5
            if step.idx < 0:                      # registration probe
                b = Blob(centroid_row=r0 + s.window_h / 2.0,
                         centroid_col=c0 + s.window_w / 2.0,
                         area_electrodes=float(s.window_h * s.window_w),
                         row=r0, col=c0,
                         height=float(s.window_h), width=float(s.window_w))
            else:                                 # empty chip: a small artifact
                b = Blob(centroid_row=r0 + s.window_h / 2.0,
                         centroid_col=c0 + s.window_w / 2.0,
                         area_electrodes=89.0, row=r0 + 1.0, col=c0 + 1.0,
                         height=10.0, width=10.0)
            return Observation(step.idx, frame_index, t, (b,))

    def test_prompts_fire_when_the_droplet_check_is_on(self):
        run, be, rec, prompter, tmp = self._build([True, True] + [True] * 20,
                                                  rig=self.Tiny())
        run.cfg.skip_droplet_check = False
        try:
            self._run_quietly(run)
            asked = [q for q in prompter.confirmed if "Top up" in q]
        finally:
            tmp.cleanup()
        self.assertTrue(asked, "expected top-up prompts with the check enabled")

    def test_no_prompts_when_the_droplet_check_is_skipped(self):
        run, be, rec, prompter, tmp = self._build([True, True] + [True] * 20,
                                                  rig=self.Tiny())
        run.cfg.skip_droplet_check = True
        try:
            stats = self._run_quietly(run)
            asked = [q for q in prompter.confirmed if "Top up" in q]
            notes = list(rec.notes)
        finally:
            tmp.cleanup()
        self.assertEqual(asked, [])
        self.assertFalse(any("Liquid low" in n for n in notes), notes)
        # >= because a static artifact flags blocks, so the fine pass adds steps
        self.assertGreaterEqual(stats["steps"], 1798)

    def test_the_skip_note_says_top_up_is_disabled_too(self):
        run, be, rec, prompter, tmp = self._build([True, True], rig=self.Tiny())
        run.cfg.skip_droplet_check = True
        try:
            self._run_quietly(run)
            note = next(n for n in rec.notes if "SKIPPED" in n)
        finally:
            tmp.cleanup()
        self.assertIn("Top-up prompts are disabled", note)


class TestStepDelayGuard(unittest.TestCase):
    """Fast timing is safe in dry-run and gated when armed.

    Dry-run energises nothing -- ChipController.activate skips ActivateElec and
    open() skips SetPower/SetVolt -- so the reflow constraint that makes 0.05s
    dangerous with liquid on the chip does not exist. The floor is what stops a
    fast dry-run value reaching an armed run via shell history.
    """

    def cfg(self, delay, armed):
        from chiphealth.config import RunConfig
        c = RunConfig()
        c.chip_id = "t"
        c.armed = armed
        c.sweep.step_delay_s = delay
        return c

    def check(self, delay, armed, allow=False):
        from chiphealth.run_health import check_step_delay
        return check_step_delay(self.cfg(delay, armed), allow_fast_armed=allow)

    def test_dry_run_has_no_floor_at_all(self):
        for delay in (0.0, 0.001, 0.05):
            with self.subTest(delay=delay):
                self.assertIsNone(self.check(delay, armed=False))

    def test_armed_below_the_floor_is_refused(self):
        res = not_none(self.check(0.05, armed=True),
                       "a fast armed run must be refused, not waved through")
        self.assertFalse(res.ok)
        self.assertIn("0.25", res.message)
        self.assertIn("2026-08-10", res.message)

    def test_armed_at_the_default_is_silent(self):
        self.assertIsNone(self.check(0.5, armed=True))

    def test_armed_at_the_floor_is_allowed_but_recorded(self):
        res = not_none(self.check(0.25, armed=True))
        self.assertTrue(res.ok)
        note = not_none(res.note)
        self.assertIn("NON-DEFAULT TIMING", note)
        # 0.25 per frame x 2 frames = the 0.5s per electrode legacy gives.
        self.assertIn("0.50s", note)

    def test_the_override_permits_a_fast_armed_run(self):
        res = not_none(self.check(0.05, armed=True, allow=True))
        self.assertTrue(res.ok)
        self.assertIn("NON-DEFAULT TIMING", not_none(res.note))

    def test_a_fast_armed_run_is_refused_with_a_nonzero_exit(self):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            rc = main(["--chip-id", "c", "--simulate", "--arm", "--step-delay",
                       "0.05", "--runs-root", tmp.name, "--headless",
                       "--non-interactive"])
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertEqual(rc, 2)

    def test_a_fast_dry_run_is_not_refused(self):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(["--chip-id", "c", "--simulate", "--step-delay", "0",
                           "--runs-root", tmp.name, "--headless",
                           "--non-interactive"])
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertEqual(rc, 0)


class TestBandsFlag(unittest.TestCase):
    """--bands truncates the sweep and says so in the artifact."""

    def run_with(self, *extra):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(["--chip-id", "b", "--simulate", "--step-delay", "0",
                           "--runs-root", tmp.name, "--headless",
                           "--non-interactive", *extra])
            d = next(Path(tmp.name).iterdir())
            meta = json.loads((d / "run.json").read_text())
            cov = json.loads((d / "coverage.json").read_text())
            steps = len(rescore.load_jsonl(d / "timeline.jsonl"))
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        return rc, meta, cov, steps

    def test_one_band_runs_far_fewer_steps(self):
        _, _, _, few = self.run_with("--bands", "1")
        _, _, _, all_ = self.run_with()
        self.assertLess(few, all_ / 3)

    def test_the_partial_sweep_is_named_in_the_notes(self):
        _, meta, _, _ = self.run_with("--bands", "1")
        note = not_none(
            next((n for n in meta["notes"] if "PARTIAL SWEEP" in n), None),
            f"no PARTIAL SWEEP note in {meta['notes']}")
        self.assertIn("1 of 7 bands", note)
        self.assertIn("NOT a coverage result", note)

    def test_untested_rows_are_reported_not_hidden(self):
        _, _, cov, _ = self.run_with("--bands", "1")
        self.assertEqual(cov["never_covered_rows"], list(range(21, 129)))

    def test_a_full_run_carries_no_partial_note(self):
        _, meta, cov, _ = self.run_with()
        self.assertFalse(any("PARTIAL SWEEP" in n for n in meta["notes"]))
        self.assertEqual(cov["never_covered_rows"], [])

    def test_zero_bands_is_refused_before_a_run_dir_is_made(self):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            rc = main(["--chip-id", "b", "--simulate", "--bands", "0",
                       "--runs-root", tmp.name, "--headless", "--non-interactive"])
            self.assertEqual(rc, 2)
            self.assertEqual(list(Path(tmp.name).iterdir()), [])
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()


class TestExceptionIsRecorded(unittest.TestCase):
    """What ended a run has to survive in the artifact, not just the console.

    The console is exactly what gets lost: a terminal scrolls, or is closed,
    and then the run folder cannot say what happened.
    """

    def describe(self, exc):
        from chiphealth.run_health import describe_exception
        try:
            raise exc
        except BaseException as e:
            return describe_exception(e)

    def test_a_real_error_records_type_message_and_location(self):
        note = self.describe(ValueError("probe left the chip"))
        self.assertIn("ValueError", note)
        self.assertIn("probe left the chip", note)
        self.assertIn("test_run_health.py:", note)
        self.assertIn("de-energised", note)

    def test_ctrl_c_is_not_reported_as_a_failure(self):
        """A run folder that calls Ctrl-C a failure sends the next reader
        hunting for a bug that is not there."""
        note = self.describe(KeyboardInterrupt())
        self.assertIn("INTERRUPTED by the operator", note)
        self.assertIn("Ctrl-C", note)
        self.assertIn("Not a failure", note)

    def test_a_message_less_exception_still_names_its_type(self):
        self.assertIn("RuntimeError", self.describe(RuntimeError()))

    def test_the_note_reaches_run_json(self):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            run, be, rec, prompter, tmp2 = None, None, None, None, None
            from chiphealth.run_health import HealthRun
            cfg = RunConfig()
            cfg.chip_id = "x"
            cfg.runs_root = Path(tmp.name)
            cfg.sweep.step_delay_s = 0.0
            chip = ChipController(FakeBackend(rows=ROWS, cols=COLS), ROWS, COLS,
                                  cfg.chip.volts, armed=False, step_delay_s=0.0,
                                  sleep=lambda _s: None)
            rec = RunRecorder(cfg, "exc", cfg.chip_id, image_writer=lambda p, f: None)
            r = HealthRun(cfg, chip, SyntheticSource(simulate.SyntheticRig()), rec,
                          LiveView(), Prompter(rec, interactive=False))
            r.phase3_baseline = lambda: (_ for _ in ()).throw(
                RuntimeError("camera fell over"))
            with self.assertRaises(RuntimeError):
                r.run()
            note = next(n for n in rec.notes if "RuntimeError" in n)
            self.assertIn("camera fell over", note)
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()


class TestDryRunVerdictsAreMarked(unittest.TestCase):
    """A dry run still writes a coverage map, and that map means nothing."""

    def test_the_artifact_says_the_verdicts_are_not_measurements(self):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--chip-id", "d", "--simulate", "--step-delay", "0",
                      "--bands", "1", "--runs-root", tmp.name, "--headless",
                      "--non-interactive"])
            meta = json.loads((next(Path(tmp.name).iterdir()) / "run.json").read_text())
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        note = not_none(
            next((n for n in meta["notes"] if n.startswith("DRY-RUN:")), None),
            f"no DRY-RUN note in {meta['notes']}")
        self.assertIn("ARTEFACTS OF NOT ENERGISING", note)
        self.assertIn("--arm", note)

    def test_an_armed_run_carries_no_such_note(self):
        """Built directly rather than via main(): an armed run cannot use a
        zero step delay (the guard floors it at 0.25s), and 290 frames x 0.25s
        is two minutes of real sleeping inside a unit test."""
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            cfg = RunConfig()
            cfg.chip_id = "d"
            cfg.armed = True
            cfg.runs_root = Path(tmp.name)
            chip = ChipController(FakeBackend(rows=ROWS, cols=COLS), ROWS, COLS,
                                  cfg.chip.volts, armed=True, step_delay_s=0.0,
                                  sleep=lambda _s: None)
            rec = RunRecorder(cfg, "armed", cfg.chip_id,
                              image_writer=lambda p, f: None)
            r = HealthRun(cfg, chip, SyntheticSource(simulate.SyntheticRig()), rec,
                          LiveView(), Prompter(rec, interactive=False))
            r.phase0_preflight()
            notes = list(rec.notes)
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        self.assertFalse(any(n.startswith("DRY-RUN:") for n in notes), notes)


class TestDryRunIsFastByDefault(unittest.TestCase):
    """A dry run must not pay a delay that exists only for liquid.

    The delay gives liquid time to reflow. A dry run energises nothing, so it
    has nothing to wait for -- charging it 0.5s x 1798 frames made a plumbing
    check a 15-minute wait for no physical reason.
    """

    def delay(self, *extra):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--chip-id", "t", "--simulate", "--bands", "1",
                      "--runs-root", tmp.name, "--headless", "--non-interactive",
                      *extra])
            meta = json.loads((next(Path(tmp.name).iterdir()) / "run.json").read_text())
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()
        return meta["config"]["sweep"]["step_delay_s"]

    def test_a_dry_run_defaults_to_no_delay(self):
        """And the value used is in run.json -- never silently retimed."""
        self.assertEqual(self.delay(), 0.0)

    def test_an_explicit_step_delay_still_wins_in_dry_run(self):
        # 0.001 rather than a realistic value: this asserts the override is
        # honoured and recorded, and a real delay would make the test sleep.
        self.assertEqual(self.delay("--step-delay", "0.001"), 0.001)

    def test_an_armed_run_keeps_the_proven_default(self):
        """Checked on the config rather than by running: an armed run at 0.5s
        x 286 frames is 2.4 minutes, which does not belong in a unit test."""
        cfg = RunConfig()
        cfg.armed = True
        self.assertEqual(cfg.sweep.step_delay_s, 0.5)
        self.assertEqual(cfg.sweep.dry_run_step_delay_s, 0.0)


class TestRegistrationFailureIsDiagnosable(unittest.TestCase):
    """A failed registration must leave evidence of what the camera saw.

    The abort happens in phase 2, before the baseline and before any step is
    driven, so the run folder is otherwise completely empty -- which is how
    four consecutive hardware failures produced no images, no observations and
    no blob list to look at.
    """

    class Glare(simulate.SyntheticRig):
        """A blob far bigger than the droplet, somewhere else on the chip.

        Reproduces the 2026-08-12 signature: the primary blob measured 3.6x and
        6.9x the expected 400 electrodes, because primary() is simply the
        largest and a bright region beat the droplet.
        """

        def observe(self, step, frame_index, t):
            from chiphealth.detector import Blob, Observation
            glare = Blob(centroid_row=100.0, centroid_col=95.0,
                         area_electrodes=2776.0, row=80.0, col=75.0,
                         height=40.0, width=40.0)
            droplet = Blob(centroid_row=14.5, centroid_col=19.5,
                           area_electrodes=400.0, row=4.5, col=9.5,
                           height=20.0, width=20.0)
            return Observation(step.idx, frame_index, t, (glare, droplet))

    def _run(self, rig):
        tmp = tempfile.TemporaryDirectory()
        logging.disable(logging.CRITICAL)
        try:
            cfg = RunConfig()
            cfg.chip_id = "r"
            cfg.runs_root = Path(tmp.name)
            chip = ChipController(FakeBackend(rows=ROWS, cols=COLS), ROWS, COLS,
                                  cfg.chip.volts, armed=False, step_delay_s=0.0,
                                  sleep=lambda _s: None)
            rec = RunRecorder(cfg, "regfail", cfg.chip_id,
                              image_writer=lambda p, f: None)
            run = HealthRun(cfg, chip, SyntheticSource(rig), rec, LiveView(),
                            Prompter(rec, interactive=False))
            run.phase0_preflight()
            ok = run.phase2_registration()
            payload = (json.loads(rec.paths.registration_json.read_text())
                       if rec.paths.registration_json.exists() else None)
            return ok, payload, list(rec.notes)
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()

    def test_a_failure_writes_every_blob_not_just_the_primary(self):
        ok, payload_or_none, _ = self._run(self.Glare())
        self.assertFalse(ok)
        payload = not_none(payload_or_none, "no evidence file written")
        self.assertEqual(payload["n_blobs"], 2)
        areas = [b["area_electrodes"] for b in payload["blobs"]]
        self.assertEqual(areas, sorted(areas, reverse=True), "not largest-first")
        self.assertTrue(payload["blobs"][0]["is_primary"])
        runner_up = payload["blobs"][1]
        self.assertAlmostEqual(runner_up["area_electrodes"], 400.0)
        self.assertAlmostEqual(runner_up["centroid_row"], 14.5)

    def test_it_records_what_was_expected_so_the_file_stands_alone(self):
        _, payload_or_none, _ = self._run(self.Glare())
        payload = not_none(payload_or_none, "no evidence file written")
        exp = payload["expected"]
        self.assertAlmostEqual(exp["centroid_row"], 14.5)
        self.assertAlmostEqual(exp["centroid_col"], 19.5)
        self.assertEqual(exp["area_electrodes"], 400.0)
        self.assertEqual(exp["centroid_tol_electrodes"], 4.0)
        self.assertTrue(payload["reasons"])

    def test_the_notes_point_at_the_evidence_file(self):
        _, _, notes = self._run(self.Glare())
        self.assertTrue(any("registration_failure.json" in n for n in notes), notes)
        self.assertTrue(any("LARGEST" in n for n in notes), notes)

    def test_no_droplet_at_all_also_leaves_evidence(self):
        class Empty(simulate.SyntheticRig):
            def observe(self, step, frame_index, t):
                from chiphealth.detector import Observation
                return Observation(step.idx, frame_index, t, ())

        ok, payload_or_none, _ = self._run(Empty())
        self.assertFalse(ok)
        payload = not_none(payload_or_none, "an empty frame is evidence too")
        self.assertEqual(payload["n_blobs"], 0)
        self.assertIn("no blob detected at all", payload["reasons"])

    def test_a_passing_registration_writes_no_failure_file(self):
        class Good(simulate.SyntheticRig):
            def observe(self, step, frame_index, t):
                from chiphealth.detector import Blob, Observation
                b = Blob(centroid_row=14.5, centroid_col=19.5,
                         area_electrodes=400.0, row=4.5, col=9.5,
                         height=20.0, width=20.0)
                return Observation(step.idx, frame_index, t, (b,))

        ok, payload, _ = self._run(Good())
        self.assertTrue(ok)
        self.assertIsNone(payload)
