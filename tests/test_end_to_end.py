"""The whole coarse pass, end to end, against injected faults.

867 real steps through sweep -> synthetic rig -> detector -> recorder, with no
camera, no OpenCV and no chip. Injected dead electrodes are the only ground
truth available until the real chip provides some (spec/objectives.md §1.4 q11).

Read the passes here for what they are: evidence the pipeline wires together and
the detector finds faults it is *given*. They say nothing about whether the
thresholds are right for the real rig -- that is what the first calibration runs
on the instrument are for.
"""

import tempfile
import unittest
from pathlib import Path

from chiphealth import simulate, sweep
from chiphealth.actuation import ChipController, Drop, FakeBackend
from chiphealth.config import RunConfig
from chiphealth.detector import Detector, KIND_DRAG, KIND_RESIDUE
from chiphealth.recorder import DEGRADED, FAIL, PASS, RunRecorder, UNKNOWN

ROWS = COLS = 128


def build(dead=(), seed=0):
    cfg = RunConfig()
    cfg.chip_id = "test-chip"
    tmp = tempfile.TemporaryDirectory()
    cfg.runs_root = Path(tmp.name)
    rig = simulate.SyntheticRig(dead=set(dead))
    det = Detector(cfg.detector, block=cfg.sweep.block)
    rec = RunRecorder(cfg, "20260807T000000Z", cfg.chip_id,
                      image_writer=lambda p, f: None, rng_seed=seed)
    rec.start()
    return cfg, rig, det, rec, tmp


def run_sweep(cfg, rig, det, rec, limit=None):
    steps = sweep.plan_serpentine(ROWS, COLS, cfg.sweep.window_h, cfg.sweep.window_w,
                                  cfg.sweep.start_row, cfg.sweep.start_col)
    if limit:
        steps = steps[:limit]
    events = []
    for s in steps:
        t = s.idx * cfg.sweep.step_delay_s
        obs = rig.observe(s, frame_index=s.idx, t=t)
        res = det.observe(s, obs)
        rec.log_step(s, res)
        for e in res.events:
            rec.log_event(e, full_frame="F", roi="R")
            events.append(e)
        # Flagged stills fire on every event, independent of the routine
        # cadence -- the review set must not depend on an event happening to
        # coincide with the 5-second tick.
        if res.events:
            rec.capture_still(t, "F", flagged=True)
        if rec.should_capture_still(t):
            rec.capture_still(t, "F", flagged=False)
    return steps, events


class TestHealthyChip(unittest.TestCase):
    """A chip with nothing wrong must produce a clean map, not a noisy one."""

    def setUp(self):
        self.cfg, rig, det, self.rec, self.tmp = build(dead=())
        self.steps, self.events = run_sweep(self.cfg, rig, det, self.rec)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_sweep_ran(self):
        self.assertEqual(len(self.steps), 901)
        self.assertEqual(self.rec.n_steps, 901)

    def test_the_traversal_reaches_every_electrode(self):
        self.assertEqual(sweep.untested_electrodes(self.steps, ROWS, COLS), set())

    def test_no_false_positives(self):
        self.assertEqual(self.events, [])

    def test_no_block_marked_bad(self):
        counts = self.rec.coverage.counts()
        self.assertEqual(counts[DEGRADED], 0)
        self.assertEqual(counts[FAIL], 0)

    def test_most_of_the_chip_is_covered(self):
        counts = self.rec.coverage.counts()
        self.assertGreater(counts[PASS], 900)  # of 1024

    def test_block_row_zero_is_now_genuinely_covered(self):
        """Row 1 is swept, so block row 0's `pass` is earned rather than inferred
        from its other rows. This used to be the case where block granularity
        hid an untested row."""
        self.assertEqual(self.rec.coverage.get(0, 5), PASS)
        self.assertEqual(sweep.uncovered_rows(ROWS, 20), [])

    def test_the_uncovered_row_reporting_still_works_when_there_is_a_gap(self):
        """The mechanism has to survive, even though the default no longer
        needs it -- a non-default geometry can still leave rows out."""
        self.rec.coverage.never_covered_rows = sweep.uncovered_rows(ROWS, 20, 2)
        self.rec.finalize()
        self.assertEqual(self.rec.coverage.never_covered_rows, [1])
        text = self.rec.paths.summary.read_text()
        self.assertIn("Not reached by any band", text)
        self.assertIn("rows: [1]", text)

    def test_still_cadence_is_five_seconds(self):
        span = len(self.steps) * 0.5
        self.assertAlmostEqual(self.rec.n_routine_stills, span / 5.0, delta=2)


class TestInjectedDeadBlock(unittest.TestCase):
    """One dead 4x4 block, mid-chip."""

    DEAD_BR, DEAD_BC = 3, 12

    def setUp(self):
        dead = simulate.dead_block(self.DEAD_BR, self.DEAD_BC)
        self.cfg, self.rig, det, self.rec, self.tmp = build(dead=dead)
        self.steps, self.events = run_sweep(self.cfg, self.rig, det, self.rec)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_fault_is_found(self):
        self.assertTrue(self.events, "injected dead block produced no events")

    def test_drag_is_the_signature_that_fires(self):
        self.assertTrue(any(e.kind == KIND_DRAG for e in self.events))

    def test_residue_is_left_behind_after_tearing_free(self):
        self.assertTrue(self.rig.stuck_at)
        self.assertTrue(any(e.kind == KIND_RESIDUE for e in self.events))

    def test_the_flagged_region_is_near_the_injected_fault(self):
        drags = [e for e in self.events if e.kind == KIND_DRAG]
        self.assertTrue(any(abs(e.block_row - self.DEAD_BR) <= 1
                            for e in drags),
                        f"no drag near block row {self.DEAD_BR}: "
                        f"{[(e.block_row, e.block_col) for e in drags]}")

    def test_coverage_map_condemns_blocks(self):
        bad = self.rec.coverage.suspicious_blocks()
        self.assertTrue(bad)

    def test_healthy_area_is_not_condemned(self):
        """A single fault must not smear across the map."""
        counts = self.rec.coverage.counts()
        self.assertLess(counts[DEGRADED] + counts[FAIL], 120)

    def test_every_event_produced_a_flagged_still_and_images(self):
        self.assertGreater(self.rec.n_flagged_stills, 0)
        self.assertEqual(self.rec.images_skipped, 0)

    def test_artifacts_written(self):
        stats = self.rec.finalize()
        self.assertTrue(self.rec.paths.events_jsonl.exists())
        self.assertTrue(self.rec.paths.timeline.exists())
        self.assertTrue(self.rec.paths.coverage.exists())
        self.assertEqual(stats["events"], len(self.events))


class TestInjectedDeadColumn(unittest.TestCase):
    """A dead drive line -- the failure a matrix-addressed array actually has."""

    DEAD_COL = 61

    def setUp(self):
        self.cfg, self.rig, det, self.rec, self.tmp = build(
            dead=simulate.dead_column(self.DEAD_COL))
        self.steps, self.events = run_sweep(self.cfg, self.rig, det, self.rec)

    def tearDown(self):
        self.tmp.cleanup()

    def test_found(self):
        self.assertTrue(self.events)

    def test_flagged_across_multiple_bands(self):
        """A column fault should show up in more than one band, not just one."""
        rows = {e.block_row for e in self.events}
        self.assertGreater(len(rows), 1)

    def test_dedup_keeps_it_from_flooding_the_dataset(self):
        """One structural fault must not produce hundreds of near-identical rows."""
        self.assertLess(len(self.events), 200)


class TestDryRunDrivesTheWholePipeline(unittest.TestCase):
    """Dry-run must exercise everything except the physics."""

    def test_no_frames_reach_the_backend_but_all_are_recorded(self):
        cfg, rig, det, rec, tmp = build(dead=simulate.dead_block(5, 5))
        try:
            be = FakeBackend(rows=ROWS, cols=COLS)
            chip = ChipController(be, ROWS, COLS, cfg.chip.volts, armed=False,
                                  step_delay_s=0.0, sleep=lambda _s: None)
            chip.open()
            steps = sweep.plan_serpentine(ROWS, COLS, 20, 20, 2, 5)[:120]
            for s in steps:
                chip.activate([Drop(s.h, s.w, s.row, s.col)])
                obs = rig.observe(s, s.idx, s.idx * 0.5)
                rec.log_step(s, det.observe(s, obs))
            chip.close()

            self.assertEqual(chip.frames_sent, 0)
            self.assertEqual(chip.frames_suppressed, 120)
            self.assertEqual(len(chip.intended), 120)
            self.assertEqual(rec.n_steps, 120)
            self.assertFalse(be.powered)
        finally:
            tmp.cleanup()


class TestFineRouteFromSweepEnd(unittest.TestCase):
    """The no-reload rule: the fine pass starts where the sweep left the liquid."""

    def test_targets_ordered_from_the_final_window_position(self):
        steps = sweep.plan_serpentine(ROWS, COLS, 20, 20, 2, 5)
        end = (float(steps[-1].row), float(steps[-1].col))
        self.assertEqual(end, (109.0, 109.0))
        targets = [(10, 10), (100, 100), (60, 60)]
        ordered, dropped = sweep.plan_fine_route(end, targets)
        self.assertEqual(ordered[0], (100, 100))  # nearest to where we ended
        self.assertEqual(dropped, [])

    def test_cap_surfaces_what_it_dropped(self):
        ordered, dropped = sweep.plan_fine_route(
            (109.0, 109.0), [(i * 5 + 1, i * 5 + 1) for i in range(30)],
            max_targets=24)
        self.assertEqual(len(ordered), 24)
        self.assertEqual(len(dropped), 6)


if __name__ == "__main__":
    unittest.main()
