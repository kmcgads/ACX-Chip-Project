"""The clearance gate, at the two layers that are not the split tree.

`test_splitplan.TestClearanceGate` covers the plan-level gate. This covers:

  * the primitive in `chiphealth.clearance` -- the arithmetic every layer
    shares, including the 1-based convention that the 2026-08-13 fix settled
  * `ChipController.activate`, the choke point every drop on this chip passes
    through, armed or dry
  * `HealthRun.phase0c_clearance`, which measures the resting frame, the
    registration window and the whole coarse sweep before the operator is
    asked to load anything

The requirement being tested (researcher, 2026-08-13): stop and ask before
loading or moving a drop into a position that lacks clearance, rather than
silently running or silently clipping -- everywhere a drop is loaded or moved,
not just in the split tree.
"""

import logging
import tempfile
import unittest
from pathlib import Path

from chiphealth import clearance as C
from chiphealth import simulate, sweep
from chiphealth.actuation import ChipController, Drop, FakeBackend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import RunConfig
from chiphealth.recorder import RunRecorder
from chiphealth.run_health import HealthRun, LiveView, Prompter, SyntheticSource

ROWS = COLS = 128


def controller(armed=False, allow_violations=False, rows=ROWS, cols=COLS):
    be = FakeBackend(rows=rows, cols=cols)
    chip = ChipController(be, rows, cols, (45, 45, 45, 0, 0, 0, 0, 0, 0),
                          armed=armed, step_delay_s=0.0, sleep=lambda _s: None,
                          allow_violations=allow_violations)
    chip.open()
    return chip


def fake_backend(chip) -> FakeBackend:
    """The fake this controller was built with, typed so `.calls` is visible.

    `ChipController.backend` is declared as the `Backend` protocol, which has
    no `.calls` -- that is the fake's call recorder, and it should stay off the
    protocol rather than be added to it for the tests' convenience. Narrowing
    here also checks the thing the assertion below depends on: that this really
    is the fake and not a real rig.
    """
    be = chip.backend
    if not isinstance(be, FakeBackend):
        raise AssertionError(f"expected a FakeBackend, got {type(be).__name__}")
    return be


class TestMeasure(unittest.TestCase):

    def test_a_drop_inside_the_array_is_clear(self):
        c = C.measure([Drop(20, 20, 5, 10)], ROWS, COLS)
        self.assertTrue(c.ok)
        self.assertEqual(c.short_sides(), {})

    def test_the_first_electrode_is_one_not_zero(self):
        """The convention the 2026-08-13 fix settled.

        Row 1 is the first electrode and row 128 is the last. `splitplan` used
        to test `r0 < 0 ... r1 >= rows`, which accepts row 0 (the controller
        refuses it) and rejects row 128 (the controller accepts it, and
        cleanup.py energises the full array routinely).
        """
        self.assertEqual(C.FIRST_INDEX, 1)
        self.assertTrue(C.fits([Drop(1, 1, 1, 1)], ROWS, COLS))
        self.assertTrue(C.fits([Drop(128, 128, 1, 1)], ROWS, COLS))
        self.assertFalse(C.fits([Drop(1, 1, 0, 1)], ROWS, COLS))
        self.assertFalse(C.fits([Drop(1, 1, 129, 1)], ROWS, COLS))

    def test_each_side_is_measured_separately(self):
        c = C.measure([(-2, 5, 3, 140)], ROWS, COLS)
        self.assertEqual(c.short_sides(), {"top": 3, "right": 12})
        self.assertEqual(c.shortfall["bottom"], 0)
        self.assertEqual(c.shortfall["left"], 0)

    def test_it_measures_the_union_of_a_whole_plan(self):
        """One answer for the operation, not one per frame -- the question is
        how far the whole thing overhangs."""
        boxes = [(5, 10, 5, 10), (-4, 2, 20, 30), (100, 110, 1, 5)]
        c = C.measure(boxes, ROWS, COLS)
        self.assertEqual(c.bounds, (-4, 110, 1, 30))
        self.assertEqual(c.short_sides(), {"top": 5})
        self.assertEqual(c.n_boxes, 3)

    def test_an_empty_frame_is_clear(self):
        """deactivate_all sends zero drops and must never be gated."""
        self.assertTrue(C.measure([], ROWS, COLS).ok)

    def test_it_accepts_drops_steps_nodes_and_tuples(self):
        """Every layer spells its geometry differently; the gate sits below
        all of them and must not make callers translate."""
        from microdrop.splitplan import DropNode
        step = sweep.Step(idx=0, row=5, col=5, h=20, w=20,
                          axis=sweep.AXIS_COL, direction=1,
                          kind=sweep.KIND_GROW, band=0)
        node = DropNode("d", None, 0, 20, 20, 5, 5)
        for item in (Drop(20, 20, 5, 5), step, node, (5, 24, 5, 24)):
            self.assertEqual(C.as_boxes([item]), [(5, 24, 5, 24)], repr(item))

    def test_something_with_no_geometry_is_refused_not_ignored(self):
        with self.assertRaises(TypeError):
            C.as_boxes(["not a drop"])


class TestRequire(unittest.TestCase):

    def test_it_raises_naming_the_short_sides(self):
        with self.assertRaises(ClearanceViolation) as ctx:
            C.require([Drop(20, 20, -5, 10)], ROWS, COLS, what="the load")
        msg = str(ctx.exception)
        self.assertIn("the load", msg)
        self.assertIn("top: short by 6", msg)
        self.assertIn("Nothing was energised", msg)

    def test_the_override_is_the_only_way_past(self):
        bad = [Drop(20, 20, -5, 10)]
        with self.assertRaises(ClearanceViolation):
            C.require(bad, ROWS, COLS)
        c = C.require(bad, ROWS, COLS, allow_violations=True)
        self.assertFalse(c.ok)

    def test_taking_the_override_is_logged_at_error(self):
        """An override that leaves no trace is not auditable."""
        with self.assertLogs("chiphealth.clearance", level="ERROR") as log:
            C.require([Drop(20, 20, -5, 10)], ROWS, COLS,
                      allow_violations=True)
        self.assertIn("CLEARANCE OVERRIDE", "\n".join(log.output))

    def test_it_is_a_valueerror(self):
        """ChipController.activate raised ValueError for an off-grid drop long
        before this module existed. Anything catching that keeps working."""
        self.assertTrue(issubclass(ClearanceViolation, ValueError))


class TestActivateIsGated(unittest.TestCase):
    """The choke point. Every drop on this chip goes through activate()."""

    def test_it_refuses_an_off_grid_drop(self):
        chip = controller(armed=True)
        with self.assertRaises(ClearanceViolation) as ctx:
            chip.activate([Drop(20, 20, -3, 10)])
        self.assertEqual(ctx.exception.clearance.short_sides(), {"top": 4})

    def test_nothing_is_sent_when_it_refuses(self):
        """'Stop', not 'stop after'. The DLL must not see the frame."""
        chip = controller(armed=True)
        be = fake_backend(chip)
        before = len(be.calls)
        with self.assertRaises(ClearanceViolation):
            chip.activate([Drop(20, 20, -3, 10)])
        self.assertEqual(len(be.calls), before)
        self.assertEqual(chip.frames_sent, 0)

    def test_a_dry_run_is_gated_too(self):
        """A dry run exists to prove the plumbing. A plan that cannot execute
        armed has not proved anything, so it must fail in dry-run as well."""
        chip = controller(armed=False)
        with self.assertRaises(ClearanceViolation):
            chip.activate([Drop(20, 20, -3, 10)])

    def test_one_bad_drop_in_a_good_frame_still_refuses(self):
        chip = controller(armed=True)
        with self.assertRaises(ClearanceViolation):
            chip.activate([Drop(5, 5, 10, 10), Drop(5, 5, 10, 126)])

    def test_the_per_call_override_works(self):
        chip = controller(armed=True)
        logging.disable(logging.ERROR)
        try:
            chip.activate([Drop(20, 20, -3, 10)], allow_violations=True)
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(chip.frames_sent, 1)

    def test_the_session_override_works_and_defaults_off(self):
        chip = controller(armed=True, allow_violations=True)
        logging.disable(logging.ERROR)
        try:
            chip.activate([Drop(20, 20, -3, 10)])
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(chip.frames_sent, 1)
        self.assertFalse(controller(armed=True).allow_violations)

    def test_a_per_call_false_beats_a_session_override(self):
        """The override is per operation; turning it on for a session must not
        make it impossible to insist on the check for one frame."""
        chip = controller(armed=True, allow_violations=True)
        with self.assertRaises(ClearanceViolation):
            chip.activate([Drop(20, 20, -3, 10)], allow_violations=False)

    def test_a_malformed_drop_is_not_covered_by_the_override(self):
        """Zero extent is not a clearance problem -- there is no side to be
        short on and no margin that would fix it."""
        chip = controller(armed=True, allow_violations=True)
        with self.assertRaises(ValueError) as ctx:
            chip.activate([Drop(0, 5, 10, 10)])
        self.assertNotIsInstance(ctx.exception, ClearanceViolation)

    def test_the_full_array_still_activates(self):
        """cleanup.py:109 energises all 128x128 routinely. A gate that broke
        that would be measuring the wrong edge."""
        chip = controller(armed=True)
        chip.activate([Drop(128, 128, 1, 1)])
        self.assertEqual(chip.frames_sent, 1)

    def test_deactivate_all_is_never_gated(self):
        chip = controller(armed=True)
        chip.deactivate_all()


class TestHealthRunIsGated(unittest.TestCase):
    """Requirement 3: the gate covers the chip-health run, not just splits."""

    def _run(self, start_row=5, start_col=10, allow=False, axes="h"):
        tmp = tempfile.TemporaryDirectory()
        cfg = RunConfig()
        cfg.chip_id = "chip-C"
        cfg.runs_root = Path(tmp.name)
        cfg.sweep.start_row = start_row
        cfg.sweep.start_col = start_col
        cfg.sweep.axes = axes
        chip = ChipController(FakeBackend(rows=ROWS, cols=COLS), ROWS, COLS,
                              cfg.chip.volts, armed=True, step_delay_s=0.0,
                              sleep=lambda _s: None, allow_violations=allow)
        chip.open()
        rec = RunRecorder(cfg, "ctest", cfg.chip_id, image_writer=lambda p, f: None)
        rec.start()
        run = HealthRun(cfg, chip, SyntheticSource(simulate.SyntheticRig()),
                        rec, LiveView(), Prompter(rec, interactive=False))
        return run, tmp

    def test_the_default_sweep_geometry_is_clear(self):
        """row 5, col 10 with a translating 20x20 window fits fine -- the
        centred-stretch margin is a SPLIT cost, not a sweep cost, and the gate
        must not conflate them."""
        run, tmp = self._run()
        try:
            run.phase0c_clearance()            # must not raise
        finally:
            tmp.cleanup()

    def test_it_refuses_an_off_grid_resting_frame(self):
        """The phase-1 hold: energised so the operator has somewhere to load
        into. If it does not fit, nobody should be asked to load."""
        run, tmp = self._run(start_row=120)
        try:
            with self.assertRaises(ClearanceViolation) as ctx:
                run.phase0c_clearance()
            c = ctx.exception.clearance
            self.assertIn("resting frame", c.what)
            self.assertEqual(c.short_sides(), {"bottom": 11})
        finally:
            tmp.cleanup()

    def test_it_refuses_before_anything_is_energised(self):
        """The whole point of doing this at phase 0c."""
        run, tmp = self._run(start_row=120)
        try:
            with self.assertRaises(ClearanceViolation):
                run.phase0c_clearance()
            self.assertEqual(run.chip.frames_sent, 0)
            self.assertEqual(run.chip.intended, [])
        finally:
            tmp.cleanup()

    def test_it_covers_the_registration_window(self):
        run, tmp = self._run(start_col=126)
        try:
            with self.assertRaises(ClearanceViolation):
                run.phase0c_clearance()
        finally:
            tmp.cleanup()

    def test_it_covers_the_whole_coarse_sweep(self):
        """Not just the load position: every commanded window of the
        traversal, so a bad band cannot surface 400 steps in."""
        run, tmp = self._run()
        try:
            steps = run._coarse_steps()
            self.assertTrue(C.fits(steps, ROWS, COLS))
            # The gate really is looking at them: break one and it must catch it.
            steps.append(sweep.Step(idx=9999, row=125, col=5, h=20, w=20,
                                    axis=sweep.AXIS_COL, direction=1,
                                    kind=sweep.KIND_GROW, band=9))
            with self.assertRaises(ClearanceViolation) as ctx:
                C.require(steps, ROWS, COLS, what="phase 4 coarse sweep")
            self.assertEqual(ctx.exception.clearance.short_sides(), {"bottom": 16})
        finally:
            tmp.cleanup()

    def test_the_vertical_pass_is_covered_when_enabled(self):
        run, tmp = self._run(axes="both")
        try:
            run.phase0c_clearance()
            self.assertGreater(len(run._coarse_steps()),
                               len(sweep.plan_serpentine(
                                   ROWS, COLS, 20, 20, 5, 10,
                                   first_band_row=1, prime=True)))
        finally:
            tmp.cleanup()

    def test_the_override_lets_the_run_proceed_and_is_recorded(self):
        """Overriding must leave evidence in run.json, not only the console --
        a run whose geometry is untrustworthy has to say so afterwards."""
        run, tmp = self._run(start_row=120, allow=True)
        logging.disable(logging.ERROR)
        try:
            run.phase0c_clearance()            # must not raise
            notes = " ".join(run.rec.notes)
            self.assertIn("CLEARANCE OVERRIDE", notes)
            self.assertIn("bottom", notes)
        finally:
            logging.disable(logging.NOTSET)
            tmp.cleanup()

    def test_the_gate_runs_before_the_load_prompt(self):
        """Ordering, pinned. phase0c must come before phase1_load in run()."""
        import inspect
        src = inspect.getsource(HealthRun.run)
        self.assertLess(src.index("phase0c_clearance"), src.index("phase1_load"))
        self.assertLess(src.index("phase0c_clearance"), src.index("phase0b_voltage"))


class TestConfigIsUnmoved(unittest.TestCase):
    """The gate stops bad plans; it does not relocate anyone's config
    (researcher, 2026-08-13)."""

    def test_sweep_start_position_is_untouched(self):
        from chiphealth.config import SweepConfig
        s = SweepConfig()
        self.assertEqual((s.start_row, s.start_col), (5, 10))
        self.assertEqual((s.window_h, s.window_w), (20, 20))

    def test_default_root_still_reads_from_sweepconfig(self):
        from microdrop.splitplan import default_root
        r = default_root()
        self.assertEqual((r.row, r.col), (5, 10))


if __name__ == "__main__":
    unittest.main()
