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
from typing import Sequence

from chiphealth.actuation import ChipController, Drop, FakeBackend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import ChipConfig
from microdrop import params as P
from microdrop import protocol as PR
from microdrop import splitplan as SP
from microdrop.protocol import OperatorAbort, SplitSession
from microdrop.splitplan import plan_tree, require_clearance

from . import not_none

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
        # 1 hold + 100 approach (50 electrodes, grow+release) + 87 tree.
        self.assertEqual(frames, 188)
        self.assertEqual(len(op.asked), 5)    # the operator really was gating

    #: chiphealth modules that need numpy or a camera. The split path reaches
    #: none of them; everything it does need (config, actuation, clearance,
    #: sweep) is pure standard library.
    VISION_MODULES = {"cv2", "numpy", "chiphealth.geometry",
                      "chiphealth.detector", "chiphealth.calibration",
                      "geometry", "detector", "calibration"}

    #: Names that only exist to serve a camera. Referencing any of them would
    #: mean a pixel had got into a path that deals in electrode indices.
    VISION_NAMES = {"ElectrodeFrame", "load_cache", "corners_px",
                    "pixel_to_electrode", "electrode_to_pixel",
                    "area_px_to_electrodes", "set_registration",
                    "check_registration", "apply_homography"}

    def _ast(self, mod):
        import ast
        import inspect
        return ast.parse(inspect.getsource(mod))

    def test_neither_module_imports_the_vision_stack(self):
        """Checked on the AST, not on the text.

        A substring search over the source cannot tell a reference apart from
        prose promising the absence of one -- and both modules contain that
        prose deliberately, in docstrings and in `--help`. So this walks import
        nodes and nothing else.
        """
        import ast
        for mod in (PR, SP):
            imported = set()
            for node in ast.walk(self._ast(mod)):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    base = (node.module or "")
                    imported.add(base)
                    imported |= {f"{base}.{a.name}" for a in node.names}
            self.assertEqual(imported & self.VISION_MODULES, set(),
                             f"{mod.__name__} imports the vision stack")

    def test_neither_module_references_a_camera_only_name(self):
        """Catches a reference reached indirectly, without an import of its own."""
        import ast
        for mod in (PR, SP):
            used = set()
            for node in ast.walk(self._ast(mod)):
                if isinstance(node, ast.Attribute):
                    used.add(node.attr)
                elif isinstance(node, ast.Name):
                    used.add(node.id)
            self.assertEqual(used & self.VISION_NAMES, set(),
                             f"{mod.__name__} reaches for a camera-only name")

    def test_no_calibration_file_path_is_opened(self):
        """It does not consume a calibration done separately, either.

        There are no pixel coordinates anywhere in this path -- only electrode
        indices -- so there is nothing a homography could be needed for. This
        looks for filesystem access rather than for the word: the modules name
        `calibration.json` in prose precisely to promise they never read it.
        """
        import ast
        for mod in (PR, SP):
            called = {n.func.id for n in ast.walk(self._ast(mod))
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            self.assertEqual(called & {"open", "load_cache"}, set(),
                             f"{mod.__name__} opens a file")


class TestGates(unittest.TestCase):

    def test_it_asks_before_loading_at_arrival_and_at_every_stage(self):
        """The protocol as specified: load at row 5 col 55, move to row 55
        col 55, split. So five gates -- load, arrival, and three stages."""
        s, op = session()
        s.run()
        self.assertEqual(len(op.asked), 5)
        self.assertIn("loaded at row 5, col 55", op.asked[0])
        self.assertIn("arrive at row 55, col 55", op.asked[1])
        for i, q in enumerate(op.asked[2:]):
            self.assertIn(f"Stage {i}", q)

    def test_a_no_at_the_load_gate_stops_the_run(self):
        s, op = session(Operator([False]))
        with self.assertRaises(OperatorAbort):
            s.run()
        self.assertEqual(len(op.asked), 1)

    def test_a_no_mid_tree_stops_before_the_next_stage(self):
        # load, arrival, stage 0, then refuse at stage 1.
        s, op = session(Operator([True, True, True, False]))
        with self.assertRaises(OperatorAbort):
            s.run()
        self.assertEqual(len(op.asked), 4)
        later = [f for st in s.plan.steps if st.stage == 2 for f in st.frames]
        self.assertEqual(
            len(s.chip.intended),
            1 + not_none(s.approach).n_frames + s.plan.n_frames - len(later))

    def test_every_answer_is_recorded(self):
        """The only artifact a camera-free run produces."""
        s, op = session()
        s.run()
        self.assertEqual([a for _, a in s.log], ["yes"] * 5)
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

        # Signature mirrors ChipController.activate so the spy is a genuine
        # stand-in rather than an untyped shim -- a drift in the real signature
        # should surface here, not be swallowed by **kw.
        def spy(drops: Sequence[Drop], settle: bool = True,
                allow_violations: bool | None = None,
                extra_settle_s: float = 0.0) -> int:
            order.append(("activate", tuple((d.row, d.col) for d in drops)))
            return real(drops, settle=settle, allow_violations=allow_violations,
                        extra_settle_s=extra_settle_s)

        c.activate = spy
        op = Operator()

        def confirm(q, detail=""):
            order.append(("ask", q))
            return op.confirm(q, detail)

        SplitSession(chip=c, confirm=confirm, announce=op.announce).run()
        self.assertEqual(order[0][0], "activate")
        self.assertEqual(order[0][1], ((5, 55),))   # the LOAD position
        self.assertEqual(order[1][0], "ask")

    def test_a_dry_run_energises_nothing_but_still_gates(self):
        s, op = session(chip=chip(armed=False))
        s.run()
        self.assertEqual(s.chip.frames_sent, 0)
        self.assertEqual(len(op.asked), 5)
        self.assertTrue(any("DRY-RUN" in m for m in op.told))

    def test_all_frames_reach_the_chip_in_an_armed_run(self):
        s, op = session()
        s.run()
        self.assertEqual(s.chip.frames_sent,
                         1 + not_none(s.approach).n_frames + s.plan.n_frames)


class TestClearanceStillGates(unittest.TestCase):
    """The geometry gate runs before the operator is asked for anything."""

    def test_a_bad_root_is_refused_before_any_prompt(self):
        s, op = session(root=SP.default_root())      # row 5, col 10
        with self.assertRaises(ClearanceViolation):
            s.run()
        self.assertEqual(op.asked, [])
        self.assertEqual(s.chip.frames_sent, 0)

    def test_the_override_still_has_to_be_asked_for(self):
        s, op = session(root=SP.default_root(), transport=False,
                        allow_violations=True)
        import logging
        logging.disable(logging.ERROR)
        try:
            s.run()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(len(op.asked), 4)   # no arrival gate: transport off


class TestApproach(unittest.TestCase):
    """Walking in is optional, and confirmed on arrival when used."""

    def test_the_walk_is_the_default_and_runs_from_the_load_position(self):
        s, _ = session()
        approach = not_none(s.approach)
        self.assertEqual(approach.from_rc, (SP.SPLIT_LOAD_ROW,
                                            SP.SPLIT_LOAD_COL))
        self.assertEqual(approach.to_rc, (SP.SPLIT_ROOT_ROW,
                                          SP.SPLIT_ROOT_COL))

    def test_sharing_a_column_makes_it_one_straight_leg(self):
        """Load and split share column 55, so the walk has no corner: 50
        electrodes down, against 95 and an L-turn from the sweep position."""
        s, _ = session()
        approach = not_none(s.approach)
        self.assertEqual(approach.electrodes, 50)
        self.assertEqual(approach.n_frames, 100)
        cols = {d.col for f in approach.frames for d in f.drops}
        self.assertEqual(cols, {SP.SPLIT_LOAD_COL})

    def test_transport_false_skips_it_entirely(self):
        s, op = session(transport=False)
        s.run()
        self.assertIsNone(s.approach)
        self.assertEqual(len(op.asked), 4)

    def test_the_arrival_gate_is_about_the_loss_nothing_else_can_see(self):
        s, op = session()
        s.run()
        self.assertIn("nothing left behind", op.asked[1])
        self.assertEqual(s.chip.frames_sent,
                         1 + not_none(s.approach).n_frames + s.plan.n_frames)

    def test_a_no_on_arrival_stops_before_the_tree(self):
        s, op = session(Operator([True, False]))
        with self.assertRaises(OperatorAbort):
            s.run()
        self.assertEqual(s.chip.frames_sent, 1 + not_none(s.approach).n_frames)


class TestSixteenPieces(unittest.TestCase):
    """`--axes WHWH`: 16 pieces of 5x5, no new code, no new position.

    The intermediate step before any 32-piece work. 20 = 2^2 x 5, so four
    halvings is the ceiling for a 20x20 and 5x5 leaves are as small as this
    tree can go -- a fifth split would have to halve a 5. If 5x5 does not hold
    on the rig, 32 pieces cannot either, and this run finds that out in
    130 seconds without a new load protocol.
    """

    AXES = ("W", "H", "W", "H")

    def test_it_plans_clean_at_the_verified_positions(self):
        plan = plan_tree(SP.split_root(), self.AXES)
        self.assertEqual(require_clearance(plan).ok, True)
        # Zero violations covers overlap and separation, not just the edge.
        self.assertEqual(plan.violations, [])

    def test_sixteen_uniform_five_by_five_leaves(self):
        plan = plan_tree(SP.split_root(), self.AXES)
        self.assertEqual(len(plan.leaves), 16)
        self.assertEqual({(n.height, n.width) for n in plan.leaves}, {(5, 5)})

    def test_the_leaves_are_evenly_spaced_and_well_separated(self):
        """120 pairs at 16 pieces -- the count where crowding would show up
        first, and the check I had not run before recommending it."""
        import itertools as it
        plan = plan_tree(SP.split_root(), self.AXES)
        seps = [SP._separation(a.bounds(), b.bounds())
                for a, b in it.combinations(plan.leaves, 2)]
        self.assertEqual(len(seps), 120)
        self.assertGreaterEqual(min(seps), 2)          # the planner's floor
        rows = sorted({n.row for n in plan.leaves})
        cols = sorted({n.col for n in plan.leaves})
        self.assertEqual({b - a for a, b in zip(rows, rows[1:])}, {13})
        self.assertEqual({b - a for a, b in zip(cols, cols[1:])}, {13})

    def test_the_pieces_are_equal_by_the_proxy(self):
        eq = SP.volume_equality(plan_tree(SP.split_root(), self.AXES))
        self.assertTrue(eq.equal)
        self.assertEqual(eq.area_electrodes, 25)

    def test_the_protocol_adds_one_gate_per_extra_stage(self):
        s, op = session(axes=self.AXES)
        s.run()
        self.assertEqual(len(op.asked), 6)             # load + arrival + 4
        self.assertIn("count 16 separate pieces", op.asked[-1])

    def test_the_whole_run_is_260_frames(self):
        s, op = session(axes=self.AXES)
        s.run()
        self.assertEqual(not_none(s.approach).n_frames, 100)
        self.assertEqual(s.plan.n_frames, 159)
        self.assertEqual(s.chip.frames_sent, 260)      # + the hold frame

    def test_a_fifth_split_is_refused_not_approximated(self):
        """The ceiling, stated as a failure. 20 = 2^2 x 5: after four halvings
        both axes are 5, and there is no factor of 2 left on either. The
        planner refuses rather than guessing which child gets the extra
        electrode -- a 3/2 split would be a 50% volume difference."""
        with self.assertRaises(ValueError) as ctx:
            plan_tree(SP.split_root(), ("W", "H", "W", "H", "W"))
        self.assertIn("even extent", str(ctx.exception))


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
        # --step-delay 0: these exercise the entry point, not the dwell, and
        # the real 0.5s default would make this class sleep for well over a
        # minute of wall clock for no added coverage.
        import contextlib
        import io
        import logging
        out = io.StringIO()
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(out):
                rc = PR.main(["--backend", "fake", "--step-delay", "0", *argv])
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

    def test_the_walk_is_on_by_default_and_is_50_electrodes(self):
        rc, out = self._main("--plan-only")
        self.assertEqual(rc, 0)
        self.assertIn("(5, 55) -> (55, 55)", out)
        self.assertIn("50 electrodes, 100 frames", out)

    def test_no_walk_removes_it(self):
        rc, out = self._main("--plan-only", "--no-walk")
        self.assertEqual(rc, 0)
        self.assertNotIn("approach", out)

    def test_load_at_overrides_the_load_position(self):
        rc, out = self._main("--plan-only", "--load-at", "5,10")
        self.assertEqual(rc, 0)
        self.assertIn("95 electrodes, 190 frames", out)

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
        rc, out = self._main("--yes", "--arm", "--allow-fake-arm")
        self.assertEqual(rc, 0)
        self.assertNotIn("DRY RUN: no electrode", out)

    def test_arming_the_fake_backend_is_refused_by_default(self):
        """The bring-up trap: a fake rig satisfies the rail check exactly like
        a real one, so an accidental fake armed run reads as a success that
        moved no liquid. Exit 4, and the message names the WSL cause."""
        rc, out = self._main("--yes", "--arm")
        self.assertEqual(rc, 4)
        self.assertIn("Refusing to --arm against the fake backend", out)
        self.assertIn("WSL", out)
        self.assertIn(".venv\\Scripts\\python.exe", out)

    def test_the_backend_is_named_before_anything_else(self):
        """The one line that distinguishes a real run from a fake one."""
        rc, out = self._main("--yes")
        self.assertTrue(out.startswith("BACKEND: FakeBackend"), out[:80])
        self.assertIn("NOT the hardware", out)

    def test_dump_prints_the_exact_struct_and_touches_nothing(self):
        rc, out = self._main("--dump")
        self.assertEqual(rc, 0)
        self.assertIn("Drop(20, 20, 55, 55)", out)
        self.assertIn("(height, width, row, col)", out)
        self.assertIn("rows 55..74, cols 55..74", out)
        self.assertIn("400 electrodes", out)
        self.assertNotIn("BACKEND", out)          # no backend was constructed

    def test_dump_honours_poke_and_size(self):
        rc, out = self._main("--dump", "--poke", "1,1", "--size", "1x1")
        self.assertIn("Drop(1, 1, 1, 1)", out)
        self.assertIn("rows 1..1, cols 1..1", out)

    def test_poke_sends_exactly_one_frame(self):
        rc, out = self._main("--poke", "55,55")
        self.assertEqual(rc, 0)
        self.assertIn("Drop(20, 20, 55, 55)", out)
        self.assertIn("DRY RUN: that call was never issued", out)

    def test_poke_reports_every_return_code_not_just_activate(self):
        """Singling out ActivateElec's rc is what made the first version
        misleading -- SetPower and SetVolt returned the same 0 and nothing
        said so. All of them are shown together, with the caveat attached."""
        rc, out = self._main("--poke", "55,55", "--arm", "--allow-fake-arm")
        self.assertEqual(rc, 0)
        self.assertIn("DLL return codes this session:", out)
        for call in ("SetPower", "SetVolt", "InquireVolt", "ActivateElec"):
            self.assertIn(call, out)
        self.assertIn("Only OpenUSB's convention is evidenced", out)
        self.assertIn("no per-electrode readback", out)

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


class TestPerStageDwell(unittest.TestCase):
    """`SplitParams.stage_extra_settle_s`: hold one stage's frames longer.

    Added 2026-08-17 to test whether the 8->16 split fails for want of TIME
    rather than want of distance -- the other hypothesis from the stretch
    widening, and only answerable if the two are changed separately.

    The dwell lives in two places that must agree: `Frame.settle_s`, which is
    what `duration_s()` reports, and `ChipController.step_delay_s`, which is
    what actually sleeps. Before this feature nothing read `Frame.settle_s` at
    run time, so these tests check the reported and the slept duration against
    each other rather than each against a constant.
    """

    AXES = ("W", "H", "W", "H")
    SP16 = P.SplitParams(stage_stretch_ratios=((2, 2.2), (3, 2.2)),
                         stage_extra_settle_s=((3, 0.5),))

    def _timed_chip(self, armed=True, baseline=0.5):
        slept = []
        c = ChipController(FakeBackend(rows=ROWS, cols=COLS), ROWS, COLS,
                           (45, 45, 45, 0, 0, 0, 0, 0, 0), armed=armed,
                           step_delay_s=baseline, sleep=slept.append)
        c.open()
        # An armed open() sleeps volt_settle_s (0.3s) after SetVolt. That is
        # startup, not frame dwell, so it is dropped rather than counted -- the
        # claim under test is about what each ActivateElec is followed by.
        slept.clear()
        return c, slept

    def test_default_is_off_and_every_frame_keeps_the_proven_dwell(self):
        self.assertEqual(P.DEFAULT.stage_extra_settle_s, ())
        self.assertEqual(P.DEFAULT.extra_settle_s, 0.0)
        self.assertEqual(P.DEFAULT.settle_s(), P.PROVEN_SETTLE_S)
        plan = plan_tree(SP.split_root(), self.AXES)
        self.assertEqual({f.settle_s for s in plan.steps for f in s.frames},
                         {P.PROVEN_SETTLE_S})

    def test_only_the_named_stage_is_slowed_in_the_plan(self):
        plan = plan_tree(SP.split_root(), self.AXES, self.SP16)
        s3 = {f.settle_s for s in plan.steps if s.stage == 3 for f in s.frames}
        rest = {f.settle_s for s in plan.steps if s.stage != 3 for f in s.frames}
        self.assertEqual(s3, {1.0})
        self.assertEqual(rest, {P.PROVEN_SETTLE_S})

    def test_the_frame_count_does_not_change_only_the_dwell(self):
        """Slowing a stage must not add or remove a single activation."""
        geom_only = P.SplitParams(stage_stretch_ratios=((2, 2.2), (3, 2.2)))
        fast = plan_tree(SP.split_root(), self.AXES, geom_only)
        slow = plan_tree(SP.split_root(), self.AXES, self.SP16)
        self.assertEqual(fast.n_frames, slow.n_frames)
        self.assertEqual([f.label for s in fast.steps for f in s.frames],
                         [f.label for s in slow.steps for f in s.frames])
        self.assertAlmostEqual(fast.duration_s(), 103.5)
        self.assertAlmostEqual(slow.duration_s(), 155.5)   # 104 frames x +0.5s

    def test_what_is_slept_matches_what_the_plan_reports(self):
        """The two dwell sources agreeing is the whole correctness claim."""
        c, slept = self._timed_chip()
        s, _ = session(chip=c, root=SP.split_root(), axes=self.AXES,
                       sp=self.SP16,
                       approach_from=SP.DropNode(id="d", parent=None, stage=0,
                                                 height=20, width=20,
                                                 row=5, col=55))
        s.run()
        self.assertEqual(sorted(set(slept)), [0.5, 1.0])
        self.assertEqual(slept.count(1.0), 104)            # stage 3 only
        self.assertAlmostEqual(
            sum(slept), not_none(s.approach).duration_s() + s.plan.duration_s())

    def test_a_dry_run_still_sleeps_nothing(self):
        """The da787-era regression guard: an extra must never fire against a
        zero baseline, or an unarmed plumbing check sits through the dwell
        again (da70561)."""
        c, slept = self._timed_chip(armed=False, baseline=0.0)
        s, _ = session(chip=c, root=SP.split_root(), axes=self.AXES,
                       sp=self.SP16,
                       approach_from=SP.DropNode(id="d", parent=None, stage=0,
                                                 height=20, width=20,
                                                 row=5, col=55))
        s.run()
        self.assertEqual(slept, [])

    def test_a_negative_extra_cannot_shorten_the_proven_dwell(self):
        c, slept = self._timed_chip()
        c.activate([Drop(2, 2, 10, 10)], settle=True, extra_settle_s=-10.0)
        self.assertEqual(slept, [0.5])

    def test_an_extra_for_a_stage_that_does_not_exist_raises(self):
        sp = P.SplitParams(stage_extra_settle_s=((9, 0.5),))
        with self.assertRaises(ValueError) as ctx:
            plan_tree(SP.split_root(), self.AXES, sp)
        self.assertIn("stage_extra_settle_s", str(ctx.exception))

    def test_the_operator_is_told_which_stage_is_slowed(self):
        c, _ = self._timed_chip()
        s, op = session(chip=c, root=SP.split_root(), axes=self.AXES,
                        sp=self.SP16,
                        approach_from=SP.DropNode(id="d", parent=None, stage=0,
                                                  height=20, width=20,
                                                  row=5, col=55))
        s.run()
        said = [m for m in op.told if "extra" in m]
        self.assertEqual(len(said), 1)
        self.assertIn("stage 3", said[0])


if __name__ == "__main__":
    unittest.main()
