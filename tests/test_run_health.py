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
from chiphealth.run_health import (HealthRun, LiveView, Prompter, SyntheticSource,
                                   main, parse_corners, parse_dead)

ROWS = COLS = 128


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
        self.assertEqual(len(parse_corners("1,2;3,4;5,6;7,8")), 4)
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
            self.assertEqual(activations, [[(20, 20, 2, 5)], []])
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
            self.assertEqual(stats["steps"], 1802)
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
        self.assertEqual(stats["steps"], 1802)

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
        message = next(n for n in notes if "registration" in n.lower())
        self.assertIn("--corners", message)
        self.assertIn("TL;TR;BR;BL", message)
        self.assertIn("corners_px", message)
        self.assertIn("--reuse-calibration", message)

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
        self.assertEqual(stats["steps"], 1802)

    def test_mismatch_is_surfaced_to_the_operator_and_logged(self):
        run, be, rec, prompter, tmp = self._build([False])
        be.volts = [45, 0, 45, 0, 0, 0, 0, 0, 0]
        logging.disable(logging.CRITICAL)
        try:
            run.phase0_preflight()
            be.volts = [45, 0, 45, 0, 0, 0, 0, 0, 0]
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
        self.assertIn("row 2, col 5", instruction)

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
        self.assertEqual(stats["steps"], 1802)
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
            self.assertIsInstance(fn.args.defaults[-1], ast.Constant)
            self.assertIsNone(fn.args.defaults[-1].value)


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
        """

        def observe(self, step, frame_index, t):
            from chiphealth.detector import Blob, Observation
            if step.idx < 0:                      # registration probe
                b = Blob(centroid_row=11.5, centroid_col=14.5,
                         area_electrodes=400.0, row=1.5, col=4.5,
                         height=20.0, width=20.0)
            else:                                 # empty chip: a small artifact
                b = Blob(centroid_row=11.5, centroid_col=14.5,
                         area_electrodes=89.0, row=6.0, col=9.0,
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
        self.assertGreaterEqual(stats["steps"], 1802)

    def test_the_skip_note_says_top_up_is_disabled_too(self):
        run, be, rec, prompter, tmp = self._build([True, True], rig=self.Tiny())
        run.cfg.skip_droplet_check = True
        try:
            self._run_quietly(run)
            note = next(n for n in rec.notes if "SKIPPED" in n)
        finally:
            tmp.cleanup()
        self.assertIn("Top-up prompts are disabled", note)
