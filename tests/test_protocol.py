"""The camera-free, operator-gated split protocol.

The load-bearing test here is `TestNoVisionStack`, which hard-blocks cv2 and
numpy at the import hook and then runs the entire protocol armed. That is the
requirement stated as an executable claim rather than as a docstring promise:
if someone adds `import numpy` anywhere under `microdrop`, or reaches into
`chiphealth.geometry` for a convenience helper, this fails.
"""

import builtins
import sys
import unittest

from chiphealth.actuation import ChipController, Drop, FakeBackend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import ChipConfig
from microdrop import protocol as PR
from microdrop import splitplan as SP
from microdrop.protocol import OperatorAbort, SplitSession

ROWS = COLS = 128


def chip(armed=True):
    c = ChipController(FakeBackend(rows=ROWS, cols=COLS), ROWS, COLS,
                       (45, 45, 45, 0, 0, 0, 0, 0, 0), armed=armed,
                       step_delay_s=0.0, sleep=lambda _s: None)
    c.open()
    return c


class Operator:
    """A scripted person. `answers` is consumed in order; None means 'yes'."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.asked: list[str] = []
        self.told: list[str] = []

    def confirm(self, question, detail=""):
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else True

    def announce(self, message):
        self.told.append(message)


def session(op=None, **kw):
    op = op or Operator()
    kw.setdefault("chip", chip())
    return SplitSession(confirm=op.confirm, announce=op.announce, **kw), op


class TestNoVisionStack(unittest.TestCase):
    """No camera, no OpenCV, no numpy, no calibration file. Proven, not stated."""

    def test_the_whole_protocol_runs_with_cv2_and_numpy_blocked(self):
        real_import = builtins.__import__

        def guard(name, *a, **k):
            if name.split(".")[0] in {"cv2", "numpy"}:
                raise ImportError(f"BLOCKED: {name}")
            return real_import(name, *a, **k)

        saved = {m: sys.modules[m] for m in list(sys.modules)
                 if m.split(".")[0] in {"cv2", "numpy"}}
        for m in saved:
            del sys.modules[m]
        builtins.__import__ = guard
        try:
            import importlib
            for m in ("microdrop.protocol", "microdrop.splitplan"):
                importlib.reload(sys.modules[m])
            from microdrop.protocol import SplitSession as Fresh
            op = Operator()
            s = Fresh(chip=chip(), confirm=op.confirm, announce=op.announce)
            report = s.run()
            # Checked while the block is still in force: nothing pulled them
            # back in on the way through.
            still_absent = not ({"cv2", "numpy"} & set(sys.modules))
            frames = s.chip.frames_sent
        finally:
            builtins.__import__ = real_import
            sys.modules.update(saved)
            import importlib
            for m in ("microdrop.splitplan", "microdrop.protocol"):
                importlib.reload(sys.modules[m])

        self.assertTrue(still_absent, "the vision stack was imported after all")
        self.assertIn("VOLUME EQUALITY", report)
        self.assertEqual(frames, 88)          # 87 tree frames + the hold frame
        self.assertEqual(len(op.asked), 4)    # the operator really was gating

    def test_the_protocol_module_imports_nothing_from_the_vision_stack(self):
        """Static check, so the failure names the offending module.

        `geometry`, `detector` and `calibration` are the three chiphealth
        modules that need numpy or a camera; the split path must reach none of
        them.
        """
        import inspect
        for mod in (PR, SP):
            src = inspect.getsource(mod)
            for banned in ("import cv2", "import numpy", "geometry",
                           "detector", "calibration"):
                offending = [ln for ln in src.splitlines()
                             if banned in ln
                             and ln.lstrip().startswith(("import ", "from "))]
                self.assertEqual(offending, [], f"{mod.__name__}: {offending}")

    def test_no_calibration_is_consumed_even_from_an_earlier_run(self):
        """It does not read a calibration done separately either.

        There are no pixel coordinates anywhere in this path -- only electrode
        indices -- so there is nothing for a homography to be needed for.
        Checked on the CODE, with every docstring stripped -- module, class and
        function. The prose deliberately names `calibration.json` and
        `corners_px` in order to say they are never touched, and a plain text
        search cannot tell that apart from touching them.
        """
        import ast
        import inspect

        def code_only(src: str) -> str:
            tree = ast.parse(src)
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if (isinstance(body, list) and body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(getattr(body[0], "value", None), ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
            return ast.unparse(tree)

        for mod in (PR, SP):
            body = code_only(inspect.getsource(mod))
            for banned in ("calibration", "load_cache", "corners_px",
                           "homography", "pixel_to_electrode", "ElectrodeFrame"):
                self.assertNotIn(banned, body, f"{mod.__name__}: {banned}")


class TestGates(unittest.TestCase):

    def test_it_asks_before_loading_and_at_every_stage(self):
        s, op = session()
        s.run()
        # 1 load gate + one per stage of a 3-stage tree. No approach here.
        self.assertEqual(len(op.asked), 4)
        self.assertIn("loaded at row 55, col 55", op.asked[0])
        for i, q in enumerate(op.asked[1:]):
            self.assertIn(f"Stage {i}", q)

    def test_a_no_at_the_load_gate_stops_the_run(self):
        s, op = session(Operator([False]))
        with self.assertRaises(OperatorAbort):
            s.run()
        self.assertEqual(len(op.asked), 1)

    def test_a_no_mid_tree_stops_before_the_next_stage(self):
        s, op = session(Operator([True, True, False]))
        with self.assertRaises(OperatorAbort):
            s.run()
        # Stage 0 and 1 frames went out; stage 2's did not.
        self.assertEqual(len(op.asked), 3)
        stage2 = [f for st in s.plan.steps if st.stage == 2 for f in st.frames]
        self.assertEqual(len(s.chip.intended),
                         s.plan.n_frames - len(stage2) + 1)  # +1 = hold frame

    def test_every_answer_is_recorded(self):
        """The only artifact a camera-free run produces."""
        s, op = session()
        s.run()
        self.assertEqual([a for _, a in s.log], ["yes"] * 4)
        self.assertIn("[yes]", s.report())

    def test_the_load_gate_warns_about_an_undersized_droplet(self):
        """The failure the operator is specifically standing in for."""
        seen = {}

        def confirm(question, detail=""):
            seen[question] = detail
            return True

        SplitSession(chip=chip(), confirm=confirm,
                     announce=lambda m: None).run()
        load = next(d for q, d in seen.items() if "loaded at" in q)
        self.assertIn("FILLS the rectangle", load)
        self.assertIn("cannot be halved evenly", load)


class TestEnergising(unittest.TestCase):

    def test_the_target_is_energised_before_liquid_is_asked_for(self):
        """45V is needed to hold a droplet, so the prompt must come second."""
        order = []
        be = FakeBackend(rows=ROWS, cols=COLS)
        c = ChipController(be, ROWS, COLS, (45,) * 3 + (0,) * 6, armed=True,
                           step_delay_s=0.0, sleep=lambda _s: None)
        c.open()
        real = c.activate

        def spy(drops, **kw):
            order.append(("activate", tuple((d.row, d.col) for d in drops)))
            return real(drops, **kw)

        c.activate = spy
        op = Operator()

        def confirm(q, detail=""):
            order.append(("ask", q))
            return op.confirm(q, detail)

        SplitSession(chip=c, confirm=confirm, announce=op.announce).run()
        self.assertEqual(order[0][0], "activate")
        self.assertEqual(order[0][1], ((55, 55),))
        self.assertEqual(order[1][0], "ask")

    def test_a_dry_run_energises_nothing_but_still_gates(self):
        s, op = session(chip=chip(armed=False))
        s.run()
        self.assertEqual(s.chip.frames_sent, 0)
        self.assertEqual(len(op.asked), 4)
        self.assertTrue(any("DRY-RUN" in m for m in op.told))

    def test_all_frames_reach_the_chip_in_an_armed_run(self):
        s, op = session()
        s.run()
        self.assertEqual(s.chip.frames_sent, s.plan.n_frames + 1)  # + hold


class TestClearanceStillGates(unittest.TestCase):
    """The geometry gate runs before the operator is asked for anything."""

    def test_a_bad_root_is_refused_before_any_prompt(self):
        s, op = session(root=SP.default_root())      # row 5, col 10
        with self.assertRaises(ClearanceViolation):
            s.run()
        self.assertEqual(op.asked, [])
        self.assertEqual(s.chip.frames_sent, 0)

    def test_the_override_still_has_to_be_asked_for(self):
        s, op = session(root=SP.default_root(), allow_violations=True)
        import logging
        logging.disable(logging.ERROR)
        try:
            s.run()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(len(op.asked), 4)


class TestApproach(unittest.TestCase):
    """Walking in is optional, and confirmed on arrival when used."""

    def test_no_approach_by_default(self):
        s, _ = session()
        self.assertIsNone(s.approach)

    def test_walking_in_adds_an_arrival_gate(self):
        s, op = session(approach_from=SP.default_root())
        s.run()
        self.assertEqual(len(op.asked), 5)
        self.assertIn("arrive at row 55, col 55", op.asked[1])
        self.assertIn("nothing left behind", op.asked[1])

    def test_the_arrival_gate_is_about_the_loss_nothing_else_can_see(self):
        s, op = session(approach_from=SP.default_root())
        s.run()
        self.assertEqual(s.approach.electrodes, 95)
        self.assertEqual(s.chip.frames_sent,
                         1 + s.approach.n_frames + s.plan.n_frames)

    def test_a_no_on_arrival_stops_before_the_tree(self):
        s, op = session(Operator([True, False]), approach_from=SP.default_root())
        with self.assertRaises(OperatorAbort):
            s.run()
        self.assertEqual(s.chip.frames_sent, 1 + s.approach.n_frames)


class TestReportIsHonest(unittest.TestCase):

    def test_it_says_what_was_not_verified(self):
        """A run of confident yeses must not read later as a measurement."""
        s, _ = session()
        r = s.run()
        self.assertIn("NOT VERIFIED THIS RUN (no camera in the loop)", r)
        self.assertIn("property of the PLAN", r)
        self.assertIn("no per-electrode readback", r)

    def test_it_still_carries_the_volume_assumptions(self):
        s, _ = session()
        r = s.run()
        self.assertIn("UNIFORM PLATE GAP", r)
        self.assertIn("VOLUME EQUALITY", r)

    def test_no_absolute_volume_appears(self):
        s, _ = session()
        r = s.run().lower()
        for banned in ("nanolitre", " nl", "microlitre", "picolitre"):
            self.assertNotIn(banned, r)


class TestCommandLine(unittest.TestCase):
    """`python -m microdrop.protocol` -- the entry point the operator types."""

    def _main(self, *argv):
        import contextlib
        import io
        import logging
        out = io.StringIO()
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(out):
                rc = PR.main(["--backend", "fake", *argv])
        finally:
            logging.disable(logging.NOTSET)
        return rc, out.getvalue()

    def test_plan_only_touches_no_hardware_and_succeeds(self):
        rc, out = self._main("--plan-only")
        self.assertEqual(rc, 0)
        self.assertIn("row=55 col=55", out)
        self.assertIn("8 pieces", out)
        self.assertIn("no USB handle was opened", out)

    def test_plan_only_refuses_a_position_that_does_not_fit(self):
        """Exit 2, and the message names the sides -- not a traceback."""
        rc, out = self._main("--plan-only", "--at", "5,10")
        self.assertEqual(rc, 2)
        self.assertIn("top: short by 4", out)
        self.assertIn("left: short by 3", out)

    def test_the_default_position_is_split_root(self):
        rc, out = self._main("--plan-only")
        self.assertEqual(rc, 0)
        self.assertIn(f"row={SP.SPLIT_ROOT_ROW} col={SP.SPLIT_ROOT_COL}", out)

    def test_sixteen_pieces_is_an_axes_flag(self):
        rc, out = self._main("--plan-only", "--axes", "WHWH")
        self.assertEqual(rc, 0)
        self.assertIn("16 pieces", out)
        self.assertIn("25 electrodes each", out)

    def test_the_walk_is_opt_in(self):
        rc, plain = self._main("--plan-only")
        self.assertNotIn("approach", plain)
        rc, walked = self._main("--plan-only", "--walk-from", "5,10")
        self.assertEqual(rc, 0)
        self.assertIn("95 electrodes, 190 frames", walked)

    def test_dry_run_is_the_default_and_says_so_in_the_report(self):
        rc, out = self._main("--yes")
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN: no electrode was energised", out)

    def test_auto_confirm_is_recorded_as_verifying_nothing(self):
        """--yes must not produce a report that reads like a checked run."""
        rc, out = self._main("--yes")
        self.assertIn("RUN NOTES:", out)
        self.assertIn("this run verified NOTHING", out)

    def test_an_armed_run_actually_energises(self):
        rc, out = self._main("--yes", "--arm", "--step-delay", "0")
        self.assertEqual(rc, 0)
        self.assertNotIn("DRY RUN: no electrode", out)

    def test_bad_axes_and_bad_position_are_argparse_errors(self):
        for bad in (("--axes", "XYZ"), ("--at", "nonsense")):
            with self.assertRaises(SystemExit) as ctx:
                PR.build_parser().parse_args(list(bad))
            self.assertEqual(ctx.exception.code, 2)

    def test_help_names_the_dry_run_default(self):
        """The operator has to be able to tell a safe invocation from a live
        one by reading --help, not by reading the source."""
        text = PR.build_parser().format_help()
        self.assertIn("WITHOUT THIS IT IS A DRY RUN", text)
        self.assertIn("--plan-only", text)


if __name__ == "__main__":
    unittest.main()
