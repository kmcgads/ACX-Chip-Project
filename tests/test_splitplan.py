"""Split-tree planner tests. No hardware, no OpenCV.

Two groups matter most.

TestCsvvolcontFidelity pins the planner to the proven file, so that if someone
retunes a ratio the divergence shows up as a failure naming csvvolcont rather
than as a surprise at the rig. It now also pins the two places the planner
DELIBERATELY diverges, so those cannot quietly revert either.

TestSymmetry is the newer and stronger group. Symmetry is the stated top
priority (researcher, 2026-08-13), so it is checked structurally -- mirror
invariance of every frame about the parent's centre line -- at every stage of
the tree, not just at the root.

All of it is FOOTPRINT symmetry. Volume equality needs the plate gap and is
unverified; see test_volume_equality_is_not_claimed.
"""

import itertools
import unittest

from chiphealth import clearance
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import ChipConfig
from microdrop import params as P
from microdrop import splitplan as SP
from microdrop.splitplan import (
    DropNode, cleared_root, default_root, plan_tree, require_clearance,
    required_margin, split_frames,
)


def _root(h=20, w=20, r=5, c=10):
    return DropNode(id="d", parent=None, stage=0, height=h, width=w, row=r, col=c)


def _axis_cells(drops, axis):
    """The set of coordinates the drops occupy along `axis`, per cross-row.

    Splits only ever move liquid along one axis, so reducing a frame to its
    footprint on that axis is lossless for symmetry purposes and makes the
    mirror test read as arithmetic rather than as geometry.
    """
    out = set()
    for d in drops:
        r0, r1, c0, c1 = d.covers()
        span = range(r0, r1 + 1) if axis == "H" else range(c0, c1 + 1)
        cross = range(c0, c1 + 1) if axis == "H" else range(r0, r1 + 1)
        out.update(itertools.product(span, cross))
    return out


def _mirror(cells, twice_centre):
    return {(twice_centre - x, y) for x, y in cells}


def _all_steps(plan):
    """Every step re-derived with nothing held, so a frame contains only the
    drops belonging to that split. Held pieces are elsewhere on the chip and
    are not part of the split's own symmetry."""
    for step in plan.steps:
        parent = plan.nodes[step.parent_id]
        yield parent, split_frames(parent, step.axis, [])[0]


def _worst_stretched_aspect(plan):
    """Worst aspect ratio any droplet reaches, measured at full stretch.

    The stretch is when a drop is longest and thinnest, so it is the moment
    that decides whether the ordering is sane -- not the settled footprint.
    """
    worst = 0.0
    for s in plan.steps:
        p = plan.nodes[s.parent_id]
        dims = (p.height, s.stretch_to) if s.axis == "W" else (s.stretch_to, p.width)
        worst = max(worst, max(dims) / min(dims))
    return worst


class TestCsvvolcontFidelity(unittest.TestCase):
    """Pins to colormixing/csvvolcont.py. Not to 1pixsplit.py."""

    def test_stretch_target_is_the_proven_stretch_rounded_to_even(self):
        """20 -> 36, where csvvolcont proves 20 -> 35 (L221, L230-235).

        The +1 is the one place a proven number moved, and it is forced: 35 is
        odd, and an odd surplus can be neither centred on the parent nor split
        into two equal stubs. Ties round up, so the stretch is never shorter
        than proven. Pinned here so the deviation is impossible to miss.
        """
        raw = P.PROVEN_SPLIT_AXIS_EXTENT * P.STRETCH_RATIO
        self.assertEqual(raw, P.PROVEN_STRETCH_TO)  # ratio itself unchanged
        self.assertEqual(P.DEFAULT.stretch_to(P.PROVEN_SPLIT_AXIS_EXTENT), 36)

    def test_stretch_target_is_always_even(self):
        """Everything downstream assumes a halvable surplus."""
        for extent in range(2, 65, 2):
            self.assertEqual(P.DEFAULT.stretch_to(extent) % 2, 0, extent)
            self.assertEqual(P.DEFAULT.neck_gap(extent) % 2, 0, extent)

    def test_stretch_is_one_electrode_per_contact_line_per_frame(self):
        """DIVERGES from csvvolcont's `for i in range(1, 16)`.

        The stretch is centred, so both contact lines advance together and the
        16-electrode surplus is covered in 8 frames rather than 16. Each
        contact line still moves exactly one electrode per 0.5 s frame, which
        is what the proven dwell is actually about.
        """
        self.assertEqual(P.DEFAULT.stretch_steps(20), 8)
        step, _ = split_frames(_root(), "W", [])
        widths = [d.width for f in step.frames if "STRETCH" in f.label
                  for d in f.drops]
        self.assertEqual(widths, [22, 24, 26, 28, 30, 32, 34, 36])

    def test_pinch_width_is_the_proven_expression(self):
        """csvvolcont.py:267, spot-checked across its own 25-step travel."""
        got = [P.pinch_width(10, 3, i, 25) for i in (1, 12, 13, 25)]
        self.assertEqual(got, [10, 7, 6, 3])

    def test_settle_matches_the_proven_dwell(self):
        """0.5s after every ActivateElec (csvvolcont.py:137)."""
        step, _ = split_frames(_root(), "W", [])
        self.assertTrue(all(f.settle_s == 0.5 for f in step.frames))

    def test_neck_erodes_one_electrode_per_side_per_frame(self):
        """DIVERGES from csvvolcont's `range(neck_gap + 1)` (L240).

        The neck parts in the middle, so both new contact lines retreat at
        once and a 16-electrode gap opens in 8 frames plus the intact-neck
        frame. Per-contact-line rate is unchanged from the proven one.
        """
        step, _ = split_frames(_root(), "W", [])
        erode = [f for f in step.frames if "ERODE" in f.label]
        self.assertEqual(len(erode), step.neck_gap // 2 + 1)

    def test_neck_erodes_from_the_centre_not_from_one_side(self):
        """Reverses csvvolcont, deliberately (researcher, 2026-08-13).

        csvvolcont pins the bridge's far edge and marches the near one, so the
        whole neck drains into one side. Correct when dispensing back into a
        reservoir; a systematic volume bias when halving. Here the neck leaves
        two stubs, one rooted at each child's inner edge, and each retreats
        into its own child. Neither child is ever cut off early.
        """
        step, (a, b) = split_frames(_root(), "W", [])
        seen = 0
        for f in step.frames:
            if "stub=" not in f.label:
                continue
            seen += 1
            left, right = f.drops[1], f.drops[2]
            # Each stub stays welded to its own child for the whole erosion...
            self.assertEqual(left.col, a.col + a.width,
                             f"left stub left child 0 in {f.label}")
            self.assertEqual(right.col + right.width, b.col,
                             f"right stub left child 1 in {f.label}")
            # ...and the two are always the same size.
            self.assertEqual(left.width, right.width, f.label)
        self.assertEqual(seen, step.neck_gap // 2)

    def test_each_child_receives_the_same_share_of_the_neck(self):
        """The whole point of centre-out erosion, stated as a count.

        Under the old one-sided erosion child 0 detached after one frame and
        child 1 stayed welded to the neck for all 15 remaining ones. Now the
        two are welded for exactly as many frames as each other, and each
        stub sweeps exactly half the gap.
        """
        for parent, step in _all_steps(plan_tree(cleared_root())):
            stubs = [f for f in step.frames if "stub=" in f.label]
            widths = [f.drops[1] for f in stubs]
            self.assertEqual(len(stubs), step.neck_gap // 2, step.parent_id)
            swept = max(d.width if step.axis == "W" else d.height
                        for d in widths)
            self.assertEqual(swept, step.neck_gap // 2, step.parent_id)

    def test_neck_retract_is_off_by_default(self):
        """csvvolcont step 5 is not inherited silently. See SplitParams."""
        self.assertFalse(P.DEFAULT.neck_retract)
        step, _ = split_frames(_root(), "W", [])
        self.assertFalse(any("RETRACT" in f.label for f in step.frames))

    def test_neck_retract_is_available_when_asked_for(self):
        sp = P.SplitParams(neck_retract=True)
        step, _ = split_frames(_root(), "W", [], sp)
        self.assertTrue(any("RETRACT" in f.label for f in step.frames))


class TestSplitGeometry(unittest.TestCase):

    def test_children_are_equal_halves(self):
        _, (a, b) = split_frames(_root(20, 20), "W", [])
        self.assertEqual((a.height, a.width), (20, 10))
        self.assertEqual((b.height, b.width), (20, 10))

    def test_odd_extent_is_refused_not_rounded(self):
        """A 5-wide drop cannot halve evenly, and guessing which child gets
        the extra electrode would silently make the pieces unequal."""
        with self.assertRaises(ValueError):
            split_frames(_root(10, 5), "W", [])

    def test_gap_between_children_is_the_stretch_surplus(self):
        _, (a, b) = split_frames(_root(20, 20), "W", [])
        self.assertEqual(b.col - (a.col + a.width), 16)

    def test_gap_formula_lands_one_off_the_one_symmetric_split_ever_run(self):
        """1pixsplit's step 6 halves 15-tall into two 5-tall with S2_GAP = 5.

        The formula here gives 6: 10 x 1.5 = 15 is odd and rounds up to an
        even 16, and 16 - 10 = 6. The extra electrode is exactly what makes
        the gap halvable, so the disagreement is the rounding rule doing its
        job rather than a discrepancy to chase. That file is not the basis for
        anything here; this only records where the two land.
        """
        self.assertEqual(P.SplitParams(stretch_ratio=1.5).neck_gap(10), 6)

    def test_height_axis_mirrors_width_axis(self):
        """The axis generalisation csvvolcont cannot itself prove."""
        _, (aw, bw) = split_frames(_root(20, 20), "W", [])
        _, (ah, bh) = split_frames(_root(20, 20), "H", [])
        self.assertEqual((aw.width, bw.col - aw.col), (ah.height, bh.row - ah.row))

    def test_no_frame_has_self_overlapping_drops(self):
        step, _ = split_frames(_root(), "W", [])
        for f in step.frames:
            total = sum(d.height * d.width for d in f.drops)
            self.assertEqual(len(f.cells()), total, f"overlap in {f.label}")

    def test_the_intact_neck_spans_both_children_with_no_dry_gap(self):
        """A dry electrode between piece and neck breaks the liquid path.

        At the first erode frame the two stubs meet in the middle, so the
        parent is still one continuous body: child 0, stub, stub, child 1.
        """
        step, (a, b) = split_frames(_root(), "W", [])
        first = next(f for f in step.frames if "stub=" in f.label)
        left, right = first.drops[1], first.drops[2]
        self.assertEqual(left.col, a.col + a.width)
        self.assertEqual(left.col + left.width, right.col)  # stubs meet
        self.assertEqual(right.col + right.width, b.col)

    def test_the_neck_parts_in_the_middle(self):
        """The dry region opens at the parent's centre and grows outwards."""
        parent = _root()
        step, _ = split_frames(parent, "W", [])
        twice_centre = 2 * parent.col + parent.width - 1
        for f in step.frames:
            if "stub=" not in f.label:
                continue
            left, right = f.drops[1], f.drops[2]
            dry_lo = left.col + left.width
            dry_hi = right.col - 1
            if dry_hi < dry_lo:
                continue  # the intact frame: no dry region yet
            self.assertEqual(dry_lo + dry_hi, twice_centre,
                             f"dry region off-centre in {f.label}")


class TestSymmetry(unittest.TestCase):
    """Symmetry is the top priority (researcher, 2026-08-13), so it is checked
    structurally and at EVERY stage of the tree, not just at the root split.

    `_all_steps` yields all seven splits of the default plan -- 1 at stage 0,
    2 at stage 1, 4 at stage 2 -- so each test below covers 1->2, 2->4 and
    4->8 in one pass. A regression at the leaves cannot hide behind a correct
    root.
    """

    def setUp(self):
        self.plan = plan_tree(cleared_root())
        self.steps = list(_all_steps(self.plan))
        # 1 + 2 + 4: guards the loops below against silently covering nothing.
        self.assertEqual(len(self.steps), 7)
        self.assertEqual([s.stage for _, s in self.steps], [0, 1, 1, 2, 2, 2, 2])

    # ── footprint ────────────────────────────────────────────────────────────

    def test_every_split_is_an_exact_half_at_every_stage(self):
        for parent, step in self.steps:
            a, b = (self.plan.nodes[i] for i in step.child_ids)
            self.assertEqual(a.extent(step.axis) * 2, step.parent_extent,
                             f"{step.parent_id} child 0 is not half")
            self.assertEqual(b.extent(step.axis) * 2, step.parent_extent,
                             f"{step.parent_id} child 1 is not half")
            self.assertEqual((a.height, a.width), (b.height, b.width),
                             f"{step.parent_id} children differ")

    def test_children_are_equidistant_from_the_parent_centre(self):
        """Neither child may end up nearer to where the parent was."""
        for parent, step in self.steps:
            a, b = (self.plan.nodes[i] for i in step.child_ids)
            ax = step.axis
            out_a = parent.origin(ax) - a.origin(ax)
            out_b = ((b.origin(ax) + b.extent(ax) - 1)
                     - (parent.origin(ax) + parent.extent(ax) - 1))
            self.assertEqual(out_a, out_b,
                             f"{step.parent_id} children moved unequally")
            self.assertEqual(out_a, step.neck_gap // 2, step.parent_id)

    # ── process ──────────────────────────────────────────────────────────────

    def test_every_frame_of_every_split_is_mirror_symmetric(self):
        """The strongest form of the claim, and the one worth keeping.

        Reflect every frame about the parent's centre line on the split axis;
        the energised cells must be unchanged. This covers the stretch, the
        intact neck, every erosion frame and the final open frame in one
        assertion, at all three stages. Anything one-sided anywhere in the
        sequence fails here.
        """
        for parent, step in self.steps:
            ax = step.axis
            twice_centre = 2 * parent.origin(ax) + parent.extent(ax) - 1
            for f in step.frames:
                cells = _axis_cells(f.drops, ax)
                self.assertEqual(
                    cells, _mirror(cells, twice_centre),
                    f"{f.label} is not mirror-symmetric about the parent centre",
                )

    def test_the_stretch_stays_centred_at_every_frame_not_just_the_last(self):
        """An origin-anchored stretch would pass an end-state check and fail
        this one -- which is exactly the bug this replaced."""
        for parent, step in self.steps:
            ax = step.axis
            twice_centre = 2 * parent.origin(ax) + parent.extent(ax) - 1
            frames = [f for f in step.frames if "STRETCH" in f.label]
            self.assertEqual(len(frames), step.neck_gap // 2, step.parent_id)
            for f in frames:
                d = f.drops[0]
                lo = d.row if ax == "H" else d.col
                hi = lo + (d.height if ax == "H" else d.width) - 1
                self.assertEqual(lo + hi, twice_centre,
                                 f"{f.label} drifted off the parent centre")

    def test_both_contact_lines_advance_one_electrode_per_frame(self):
        """Symmetric, and still at the proven per-contact-line rate."""
        for parent, step in self.steps:
            ax = step.axis
            frames = [f for f in step.frames if "STRETCH" in f.label]
            extents = [f.drops[0].height if ax == "H" else f.drops[0].width
                       for f in frames]
            self.assertEqual(
                extents,
                [step.parent_extent + 2 * i for i in range(1, len(frames) + 1)],
                step.parent_id,
            )
            self.assertEqual(extents[-1], step.stretch_to, step.parent_id)

    def test_neither_child_detaches_before_the_other(self):
        """Under one-sided erosion child 0 was cut loose after one frame while
        child 1 held the neck to the end. Both must now part together."""
        for parent, step in self.steps:
            ax = step.axis
            a, b = (self.plan.nodes[i] for i in step.child_ids)
            for f in step.frames:
                if "stub=" not in f.label:
                    continue
                left, right = f.drops[1], f.drops[2]
                if ax == "W":
                    self.assertEqual(left.col, a.col + a.width, f.label)
                    self.assertEqual(right.col + right.width, b.col, f.label)
                else:
                    self.assertEqual(left.row, a.row + a.height, f.label)
                    self.assertEqual(right.row + right.height, b.row, f.label)

    def test_symmetry_holds_when_the_tree_goes_deeper(self):
        """16 pieces, four stages. Symmetry is structural, not tuned to depth."""
        plan = plan_tree(cleared_root(axes=("W", "H", "W", "H")),
                         axes=("W", "H", "W", "H"))
        steps = list(_all_steps(plan))
        self.assertEqual(len(steps), 15)
        for parent, step in steps:
            ax = step.axis
            twice_centre = 2 * parent.origin(ax) + parent.extent(ax) - 1
            for f in step.frames:
                cells = _axis_cells(f.drops, ax)
                self.assertEqual(cells, _mirror(cells, twice_centre), f.label)

    def test_retract_is_symmetric_too_when_enabled(self):
        """The optional step must not reintroduce the bias by the back door."""
        parent = _root()
        sp = P.SplitParams(neck_retract=True)
        step, _ = split_frames(parent, "W", [], sp)
        twice_centre = 2 * parent.col + parent.width - 1
        frames = [f for f in step.frames if "RETRACT" in f.label]
        self.assertTrue(frames)
        for f in frames:
            cells = _axis_cells(f.drops, "W")
            self.assertEqual(cells, _mirror(cells, twice_centre), f.label)

    # ── the limit of the claim ───────────────────────────────────────────────

    def test_volume_equality_is_not_claimed(self):
        """Volume equality, by the method adopted 2026-08-13.

        The method changed: equality no longer waits on `ChipConfig.gap_um`
        plus imaging, it is read off the ACTIVATED ELECTRODE AREA of each
        piece. The gap cancels out of a ratio, so equal area gives equal
        volume without ever knowing the gap -- see `splitplan.volume_equality`.

        The name is kept from the pre-2026-08-13 version because what is *not*
        claimed is the half of this that still constrains the package: a ratio
        is not a quantity, so there is still no absolute volume anywhere, and
        the equality claim itself is a property of the PLAN that no rig has
        confirmed. Both halves are asserted here.
        """
        from chiphealth.config import ChipConfig

        plan = plan_tree(cleared_root())

        # CLAIMED: equal volume, from equal activated area.
        eq = SP.volume_equality(plan)
        self.assertTrue(eq.equal)
        self.assertEqual(eq.area_electrodes, 50)          # 10x5 leaves
        self.assertEqual(len(eq.areas), 8)
        self.assertEqual(set(eq.areas.values()), {50})
        for leaf in plan.leaves:
            self.assertEqual(leaf.activated_area_electrodes(),
                             leaf.height * leaf.width)

        # NOT CLAIMED: any absolute volume. The gap is still unmeasured, and
        # the proxy does not supply it -- it only makes it cancel.
        self.assertIsNone(ChipConfig().gap_um)
        self.assertFalse(hasattr(DropNode, "volume_nl"))
        self.assertFalse(hasattr(DropNode, "volume"))
        self.assertIn("unverified", P.__doc__.lower())

        # NOT CLAIMED: that the chip obliges. The assumption that makes the
        # proxy valid must travel with the verdict, not live in a comment.
        self.assertIn("UNIFORM PLATE GAP", eq.describe())
        self.assertTrue(any("UNIFORM PLATE GAP" in a
                            for a in SP.VOLUME_EQUALITY_ASSUMPTIONS))

    def test_volume_equality_is_decided_by_area_not_by_the_gap(self):
        """The point of the substitution: the verdict must not depend on the
        gap being known, only on it being the same for both pieces.

        Measuring the gap tomorrow must not change any answer this gives, or
        the proxy was secretly using it.
        """
        from dataclasses import replace as dc_replace
        from chiphealth.config import ChipConfig

        plan = plan_tree(cleared_root())
        before = SP.volume_equality(plan)
        # A gap value appearing changes nothing -- and neither would a
        # different one, because it cancels.
        for gap in (None, 100.0, 250.0):
            cfg = dc_replace(ChipConfig(), gap_um=gap)
            self.assertIsNotNone(plan.leaves[0].activated_area_mm2(cfg))
            self.assertEqual(SP.volume_equality(plan).areas, before.areas)

    def test_unequal_leaves_are_reported_unequal(self):
        """The proxy has to be able to say no, or it is not a check.

        A W,H,W tree from a 20x20 gives eight 10x5 leaves. Stopping one stage
        early gives four 10x10s -- still equal. Mixing depths is what makes
        areas differ, so this builds that case directly rather than trusting
        that the planner can never produce it.
        """
        plan = plan_tree(cleared_root())
        eq = SP.volume_equality(plan)
        self.assertTrue(eq.equal)

        # Same plan, one leaf swapped for a piece of a different size.
        odd = DropNode("d999", "d00", 3, 10, 4, 1, 1)
        plan.nodes[odd.id] = odd
        plan.stages[-1] = plan.stages[-1][:-1] + (odd.id,)
        bad = SP.volume_equality(plan)
        self.assertFalse(bad.equal)
        self.assertIsNone(bad.area_electrodes)
        self.assertIn("NOT equal", bad.describe())


class TestLoadClearance(unittest.TestCase):
    """Centring the stretch costs clearance behind the load position."""

    def test_the_default_load_position_no_longer_has_the_clearance(self):
        """Reported, not clipped. The sweep's load position is row 5 col 10 and
        a centred stretch needs 8 above and 12 either side, so the default plan
        runs off the top and left edges. Moving SweepConfig.start_row would
        move the chip-health sweep with it, so this module reports the conflict
        rather than resolving it unilaterally."""
        plan = plan_tree(default_root())
        self.assertTrue(any(v.kind == "off-grid" for v in plan.violations))

    def test_required_margin_is_the_cumulative_half_surplus(self):
        """8 on the H axis (one 20-extent split), 12 on W (a 20 then a 10)."""
        self.assertEqual(required_margin(default_root()),
                         {"top": 8, "bottom": 8, "left": 12, "right": 12})

    def test_a_cleared_load_position_plans_without_violations(self):
        self.assertEqual(plan_tree(cleared_root()).violations, [])

    def test_cleared_root_moves_only_as_far_as_it_must(self):
        """(9, 13), not (8, 12).

        The margin is 8 above and 12 left, and electrode 1 is the first one, so
        the root sits at row margin+1 and the stretch reaches exactly row 1.
        This returned (8, 12) until 2026-08-13, which put the stretch on row 0
        -- a row `plan_tree` accepted (its check was 0-based) and
        `ChipController._validate` refused, for 63 of the plan's 87 frames.
        `test_cleared_root_actually_executes` is the regression guard.
        """
        root, cleared = default_root(), cleared_root()
        self.assertEqual((cleared.row, cleared.col), (9, 13))
        self.assertEqual((cleared.height, cleared.width),
                         (root.height, root.width))

    def test_cleared_root_actually_executes(self):
        """The bug the 1-based fix closed, pinned end to end.

        Planning clean is worth nothing if the controller then refuses the
        frames. Every frame of the cleared plan must survive the same gate that
        guards a real ActivateElec.
        """
        from chiphealth.actuation import ChipController, FakeBackend
        from chiphealth.config import ChipConfig

        cfg = ChipConfig()
        chip = ChipController(FakeBackend(rows=cfg.rows, cols=cfg.cols),
                              cfg.rows, cfg.cols, cfg.volts,
                              armed=False, step_delay_s=0.0,
                              sleep=lambda _s: None)
        plan = plan_tree(cleared_root())
        for step in plan.steps:
            for f in step.frames:
                chip.activate(list(f.drops))          # must not raise
        self.assertEqual(len(chip.intended), plan.n_frames)

    def test_plan_bounds_of_the_cleared_plan_start_at_electrode_one(self):
        """Only as far in as it must: row 1 and col 1 are touched, not row 0
        (off-chip) and not row 2 (a wasted electrode of margin)."""
        r0, r1, c0, c1 = SP.plan_bounds(plan_tree(cleared_root()))
        self.assertEqual((r0, c0), (1, 1))
        self.assertLessEqual(r1, 128)
        self.assertLessEqual(c1, 128)


class TestClearanceGate(unittest.TestCase):
    """The gate itself: it must refuse, name the shortfall, and be overridable
    only on purpose (researcher requirement, 2026-08-13).

    Reporting a violation into a list nobody has to read is what this replaces.
    """

    def test_it_blocks_the_sweep_load_position(self):
        """row 5, col 10 -- SweepConfig's position, which does not fit.

        The gate must refuse it and say which sides are short and by how much,
        rather than running and clipping. It must NOT move it: `SweepConfig`
        and `default_root()` are left exactly where they are, and the run
        stops instead.
        """
        root = default_root()
        self.assertEqual((root.row, root.col), (5, 10))  # unmoved

        with self.assertRaises(ClearanceViolation) as ctx:
            require_clearance(plan_tree(root))

        c = ctx.exception.clearance
        self.assertFalse(c.ok)
        # Needs 8 above / 12 left of a root at row 5, col 10, and the first
        # electrode is 1: short 8+1-5=4 on top and 12+1-10=3 on the left.
        self.assertEqual(c.short_sides(), {"top": 4, "left": 3})
        msg = str(ctx.exception)
        self.assertIn("top", msg)
        self.assertIn("short by 4", msg)
        self.assertIn("left", msg)
        self.assertIn("short by 3", msg)
        # Bottom and right have room, and must not be named.
        self.assertNotIn("bottom", msg)
        self.assertNotIn("right:", msg)

    def test_row5_col45_is_short_on_the_top_only(self):
        """The position proposed on 2026-08-13, checked rather than assumed.

        Columns are fine -- 45 clears the 12 needed on the left, and the piece
        ends at col 64 with 63 to spare on the right. The TOP is short by 4:
        the plan reaches row -3 and the first electrode is row 1. Recorded as a
        test so the answer is not re-derived by hand next time.
        """
        root = DropNode("d", None, 0, 20, 20, 5, 45)
        c = SP.check_root(root)
        self.assertFalse(c.ok)
        self.assertEqual(c.short_sides(), {"top": 4})
        self.assertEqual(SP.plan_bounds(plan_tree(root)), (-3, 32, 33, 76))
        with self.assertRaises(ClearanceViolation):
            require_clearance(plan_tree(root))

    def test_it_passes_a_cleared_root(self):
        """The other side of the gate: a plan that fits must go through."""
        c = require_clearance(plan_tree(cleared_root()))
        self.assertTrue(c.ok)
        self.assertEqual(c.short_sides(), {})
        self.assertIn("clear", c.describe())

    def test_the_override_flag_lets_a_bad_plan_through(self):
        """`allow_violations=True` is the only way past, and it works.

        It returns the same measured clearance rather than a bare True, so a
        caller that overrides still has the numbers to record.
        """
        plan = plan_tree(default_root())
        with self.assertRaises(ClearanceViolation):
            require_clearance(plan)                      # no default bypass
        c = require_clearance(plan, allow_violations=True)
        self.assertFalse(c.ok)
        self.assertEqual(c.short_sides(), {"top": 4, "left": 3})

    def test_the_override_defaults_to_off_everywhere(self):
        """A bypass that can be left switched on is not a decision.

        Checked by signature so a later refactor cannot flip a default without
        this failing.
        """
        import inspect
        from chiphealth import clearance as C
        from chiphealth.actuation import ChipController

        for fn in (C.require, require_clearance, SP.require_clearance):
            self.assertIs(
                inspect.signature(fn).parameters["allow_violations"].default,
                False, fn.__qualname__)
        self.assertIs(
            inspect.signature(ChipController.__init__)
            .parameters["allow_violations"].default, False)
        # And no way to set it from config or the environment.
        from chiphealth.config import ChipConfig, RunConfig, SweepConfig
        for cfg in (RunConfig(), ChipConfig(), SweepConfig()):
            self.assertFalse([f for f in vars(cfg) if "violation" in f.lower()],
                             type(cfg).__name__)

    def test_the_gate_is_the_same_check_the_controller_applies(self):
        """One convention, measured in one place.

        The whole reason cleared_root() was broken is that these two disagreed.
        For every root, `require_clearance` raising and `ChipController`
        rejecting a frame must be the same answer.
        """
        from chiphealth.actuation import ChipController, FakeBackend
        from chiphealth.config import ChipConfig

        cfg = ChipConfig()
        for row, col in ((5, 10), (5, 45), (9, 13), (9, 100), (60, 60)):
            root = DropNode("d", None, 0, 20, 20, row, col)
            plan = plan_tree(root)
            gate_ok = SP.check_root(root).ok

            chip = ChipController(FakeBackend(rows=cfg.rows, cols=cfg.cols),
                                  cfg.rows, cfg.cols, cfg.volts, armed=False,
                                  step_delay_s=0.0, sleep=lambda _s: None)
            frames_ok = True
            for step in plan.steps:
                for f in step.frames:
                    try:
                        chip.activate(list(f.drops))
                    except ClearanceViolation:
                        frames_ok = False
            self.assertEqual(gate_ok, frames_ok, f"disagreement at ({row},{col})")

    def test_a_violation_names_every_short_side_at_once(self):
        """Four sides short, four sides reported -- not just the first found.

        An operator fixing one side at a time because the message only ever
        names one is the failure mode this avoids.
        """
        from chiphealth import clearance as C
        tiny = ChipConfig(rows=4, cols=4)
        with self.assertRaises(ClearanceViolation) as ctx:
            C.require([(-2, 9, -3, 7)], tiny.rows, tiny.cols, what="x")
        self.assertEqual(ctx.exception.clearance.short_sides(),
                         {"top": 3, "bottom": 5, "left": 4, "right": 3})


class TestSplitPosition(unittest.TestCase):
    """The split position chosen this session, and the walk that reaches it.

    The decision it encodes: the clearance requirement is on where the droplet
    SPLITS, not on where a human loads it, so the load position stays at
    SweepConfig and the droplet is transported.
    """

    def test_the_chosen_position_is_row_55_col_55(self):
        r = SP.split_root()
        self.assertEqual((r.row, r.col), (55, 55))
        self.assertEqual((SP.SPLIT_ROOT_ROW, SP.SPLIT_ROOT_COL), (55, 55))
        self.assertEqual((r.height, r.width), (20, 20))

    def test_it_does_not_move_the_load_position(self):
        """The whole point of transporting: SweepConfig is left alone."""
        from chiphealth.config import SweepConfig
        s = SweepConfig()
        self.assertEqual((s.start_row, s.start_col), (5, 10))
        self.assertEqual((default_root().row, default_root().col), (5, 10))

    def test_the_split_position_is_clear_for_eight_pieces(self):
        c = require_clearance(plan_tree(SP.split_root()))
        self.assertTrue(c.ok)

    def test_the_split_position_is_clear_for_sixteen_pieces(self):
        """Reason 1 for choosing it: cleared_root() cannot do this.

        The tree bottoms out at 16 -- 20 = 2^2 x 5, so a fifth split would need
        to halve a 5 -- and W,H,W,H needs 12 clear all round. (9, 13) is short
        4 on top; (55, 55) fits. Going to 16 pieces is then a parameter change.
        """
        axes = ("W", "H", "W", "H")
        self.assertFalse(SP.check_root(cleared_root(), axes).ok)
        self.assertEqual(SP.check_root(cleared_root(), axes).short_sides(),
                         {"top": 4})

        plan = plan_tree(SP.split_root(), axes)
        self.assertTrue(require_clearance(plan).ok)
        self.assertEqual(len(plan.leaves), 16)
        self.assertEqual({(n.height, n.width) for n in plan.leaves}, {(5, 5)})

    def test_it_is_centred_on_both_axes_at_both_depths(self):
        """Reason 2: equal margin every direction, so drift has equal room."""
        for axes, expected in ((("W", "H", "W"), (46, 46, 42, 42)),
                               (("W", "H", "W", "H"), (42, 42, 42, 42))):
            r0, r1, c0, c1 = SP.plan_bounds(plan_tree(SP.split_root(), axes))
            gaps = (r0 - 1, 128 - r1, c0 - 1, 128 - c1)
            self.assertEqual(gaps, expected, axes)

    def test_the_approach_walks_from_the_load_position_to_the_split_position(self):
        """The protocol as specified: load at row 5 col 55, move to row 55
        col 55. Load and split share a column, so this is one straight leg
        of 50 rows with no corner -- two frames each, grow then release."""
        approach, root = SP.approach_to_split()
        self.assertEqual(approach.from_rc, (SP.SPLIT_LOAD_ROW, SP.SPLIT_LOAD_COL))
        self.assertEqual(approach.from_rc, (5, 55))
        self.assertEqual(approach.to_rc, (55, 55))
        self.assertEqual((root.row, root.col), (55, 55))
        self.assertEqual(approach.electrodes, 50)
        self.assertEqual(approach.n_frames, 100)
        self.assertAlmostEqual(approach.duration_s(), 50.0)
        # No column leg at all: every frame sits in column 55.
        self.assertEqual({d.col for f in approach.frames for d in f.drops},
                         {55})

    def test_the_load_position_needs_no_split_clearance(self):
        """A load is a plain hold. Only the SPLIT needs the tree's margin, and
        by then the droplet is elsewhere -- which is what lets the load
        position be dictated by where the operator can reach."""
        load = SP.load_root()
        self.assertEqual((load.row, load.col), (5, 55))
        self.assertEqual((load.height, load.width), (20, 20))
        self.assertTrue(clearance.fits([load.bounds()], 128, 128))
        # It could not host the tree, and does not have to.
        self.assertFalse(SP.check_root(load).ok)

    def test_walking_from_the_sweep_position_would_cost_nearly_twice_as_much(self):
        """Why the load column matters: (5,10) needs an L-turn, (5,55) does not."""
        far, _ = SP.approach_to_split(load=default_root())
        self.assertEqual(far.electrodes, 95)
        near, _ = SP.approach_to_split()
        self.assertEqual(near.electrodes, 50)

    def test_every_approach_frame_only_grows_or_only_releases(self):
        """The discipline the 2026-08-10 break-up bought.

        Grow into new territory while holding everything already energised,
        and only then release behind -- never both in the same frame, which is
        what asks the liquid to let go and grab in the same instant.

        Stated on the ENERGISED CELLS, not on the drop's origin: a release
        moves the origin one and shrinks the extent one, which is a single
        trailing edge being dropped even though two fields changed. So the
        invariant is that consecutive frames nest, and differ by exactly one
        full row or column.
        """
        approach, _ = SP.approach_to_split()
        prev = None
        grows = releases = 0
        for f in approach.frames:
            self.assertEqual(len(f.drops), 1, f.label)
            cells = f.cells()
            if prev is not None:
                added, removed = cells - prev, prev - cells
                self.assertFalse(added and removed,
                                 f"{f.label} grows and releases in one frame")
                changed = added or removed
                self.assertEqual(len(changed), 20,
                                 f"{f.label} moved more than one electrode line")
                grows += bool(added)
                releases += bool(removed)
            prev = cells
        # 50 electrodes of travel, one grow and one release each, strictly
        # alternating. The very first frame is a grow with nothing before it to
        # compare against, so 49 grows are observed and all 50 releases are.
        self.assertEqual(approach.n_frames, 100)
        self.assertEqual((grows, releases), (49, 50))

    def test_the_approach_never_shrinks_the_droplet(self):
        """It relocates a 20x20; it must not resize one. The tree needs the
        full footprint to halve evenly."""
        approach, _ = SP.approach_to_split()
        for f in approach.frames:
            d = f.drops[0]
            self.assertGreaterEqual(d.height, 20, f.label)
            self.assertGreaterEqual(d.width, 20, f.label)
            self.assertLessEqual(d.height * d.width, 21 * 20, f.label)

    def test_the_approach_ends_exactly_on_the_split_root(self):
        approach, root = SP.approach_to_split()
        last = approach.frames[-1].drops[0]
        self.assertEqual((last.row, last.col), (root.row, root.col))
        self.assertEqual((last.height, last.width), (root.height, root.width))

    def test_the_approach_is_gated_too(self):
        approach, _ = SP.approach_to_split()
        self.assertTrue(SP.require_approach_clearance(approach).ok)

        off = SP.plan_approach(DropNode("d", None, 0, 20, 20, 5, 10), 5, 120)
        with self.assertRaises(ClearanceViolation) as ctx:
            SP.require_approach_clearance(off)
        self.assertEqual(ctx.exception.clearance.short_sides(), {"right": 11})

    def test_the_approach_refuses_to_resize(self):
        small = DropNode("d", None, 0, 10, 10, 5, 10)
        with self.assertRaises(ValueError):
            SP.approach_to_split(load=small)

    def test_the_whole_protocol_executes(self):
        """Approach then tree, every frame through the real controller gate."""
        from chiphealth.actuation import ChipController, FakeBackend

        cfg = ChipConfig()
        chip = ChipController(FakeBackend(rows=cfg.rows, cols=cfg.cols),
                              cfg.rows, cfg.cols, cfg.volts, armed=False,
                              step_delay_s=0.0, sleep=lambda _s: None)
        approach, root = SP.approach_to_split()
        plan = plan_tree(root)
        SP.require_approach_clearance(approach)
        require_clearance(plan)
        for f in approach.frames:
            chip.activate(list(f.drops))
        for step in plan.steps:
            for f in step.frames:
                chip.activate(list(f.drops))
        self.assertEqual(len(chip.intended), approach.n_frames + plan.n_frames)

    def test_the_pieces_are_still_equal(self):
        """Transporting first must not disturb what the tree guarantees."""
        plan = plan_tree(SP.split_root())
        eq = SP.volume_equality(plan)
        self.assertTrue(eq.equal)
        self.assertEqual(eq.area_electrodes, 50)
        self.assertEqual({(n.height, n.width) for n in plan.leaves}, {(10, 5)})


class TestHolding(unittest.TestCase):
    """csvvolcont holds every finished piece in every activate() call."""

    def test_every_frame_holds_every_other_live_drop(self):
        held = [DropNode("x", "d", 1, 10, 5, 60, 60)]
        step, _ = split_frames(_root(), "W", held)
        for f in step.frames:
            self.assertTrue(
                any(d.row == 60 and d.col == 60 for d in f.drops),
                f"held drop missing from {f.label}",
            )

    def test_a_split_parent_is_not_held_alongside_its_children(self):
        """The bug the validator caught: a consumed parent must disappear.

        Holding it would energise a 20x10 pad underneath the two pieces that
        replaced it, merging them straight back together.
        """
        plan = plan_tree(cleared_root())
        for i, step in enumerate(plan.steps):
            # Parents split by an EARLIER step no longer exist as liquid. The
            # current step's own parent is excluded: it is legitimately on the
            # chip during its own stretch frames.
            consumed = [plan.nodes[s.parent_id] for s in plan.steps[:i]]
            for f in step.frames:
                boxes = {(d.row, d.col, d.height, d.width) for d in f.drops}
                for p in consumed:
                    self.assertNotIn(
                        (p.row, p.col, p.height, p.width), boxes,
                        f"consumed parent {p.id} still held in {f.label}",
                    )


class TestPlanTree(unittest.TestCase):

    def test_three_splits_give_eight_pieces(self):
        plan = plan_tree(cleared_root())
        self.assertEqual(len(plan.leaves), 8)
        self.assertEqual({n.stage for n in plan.leaves}, {3})

    def test_leaves_are_uniform(self):
        """Equal pieces is the whole claim of a symmetric tree."""
        sizes = {(n.height, n.width) for n in plan_tree(cleared_root()).leaves}
        self.assertEqual(sizes, {(10, 5)})

    def test_leaves_are_evenly_spaced(self):
        """A consequence of centring worth pinning on its own.

        Under the origin-anchored stretch the leaves landed at cols 10, 23, 35,
        48 -- gaps of 13, 12, 13, because the stage-0 and stage-2 surpluses
        were anchored the same way and did not compose evenly. Centred, the
        columns come out uniformly spaced.
        """
        plan = plan_tree(cleared_root())
        cols = sorted({n.col for n in plan.leaves})
        rows = sorted({n.row for n in plan.leaves})
        self.assertEqual(len(set(b - a for a, b in zip(cols, cols[1:]))), 1)
        self.assertEqual(len(rows), 2)

    def test_leaves_do_not_touch(self):
        plan = plan_tree(cleared_root())
        for a, b in itertools.combinations(plan.leaves, 2):
            self.assertGreaterEqual(SP._separation(a.bounds(), b.bounds()), 2)

    def test_every_node_knows_its_parent_and_stage(self):
        plan = plan_tree(cleared_root())
        for node in plan.nodes.values():
            self.assertEqual(node.stage, len(node.id) - 1)
            if node.parent is None:
                self.assertEqual(node.id, "d")
            else:
                self.assertIn(node.parent, plan.nodes)
                self.assertEqual(plan.nodes[node.parent].stage, node.stage - 1)

    def test_lineage_walks_back_to_the_root(self):
        plan = plan_tree(cleared_root())
        self.assertEqual(plan.lineage("d011"), ("d", "d0", "d01", "d011"))

    def test_depth_is_not_capped_at_three(self):
        """16 pieces is a parameter change, not a rewrite."""
        axes = ("W", "H", "W", "H")
        plan = plan_tree(cleared_root(axes=axes), axes=axes)
        self.assertEqual(len(plan.leaves), 16)
        self.assertEqual({(n.height, n.width) for n in plan.leaves}, {(5, 5)})

    def test_single_axis_order_is_refused(self):
        """W,W,W reaches 20x5 and 5 will not halve. Both axes are required."""
        with self.assertRaises(ValueError):
            plan_tree(default_root(), axes=("W", "W", "W"))

    def test_axis_ordering_table(self):
        """Pins the table in the module docstring.

        W,W,H and H,H,W spend both same-axis splits before touching the other
        axis, reach a 35x5 sliver, and are the orderings to avoid. They are
        legal geometry though, so the planner offers them rather than
        pretending they are impossible.
        """
        expected = {"WWH": 7.2, "WHW": 3.6, "WHH": 3.6,
                    "HWW": 3.6, "HWH": 3.6, "HHW": 7.2}
        for axes, worst in expected.items():
            axes_t = tuple(axes)
            plan = plan_tree(cleared_root(axes=axes_t), axes=axes_t)
            self.assertAlmostEqual(_worst_stretched_aspect(plan), worst,
                                   msg=f"ordering {axes}")
            self.assertEqual(len(plan.leaves), 8)
            self.assertEqual(len({(n.height, n.width) for n in plan.leaves}), 1)

    def test_the_default_is_one_of_the_joint_best_orderings(self):
        self.assertAlmostEqual(
            _worst_stretched_aspect(plan_tree(cleared_root())), 3.6)

    def test_off_grid_plan_is_reported_not_silently_clipped(self):
        root = DropNode("d", None, 0, 20, 20, 5, 110)
        plan = plan_tree(root, axes=("W",))
        self.assertTrue(any(v.kind == "off-grid" for v in plan.violations))

    def test_frame_count_and_dwell(self):
        plan = plan_tree(cleared_root())
        # 17 frames for each 20-extent split (8 stretch + 9 erode),
        # 9 for each 10-extent split (4 stretch + 5 erode). Roughly half the
        # old count: both contact lines now move in every frame, so the same
        # per-contact-line rate covers the surplus in half the frames.
        self.assertEqual(plan.n_frames, 3 * 17 + 4 * 9)
        self.assertAlmostEqual(plan.duration_s(), plan.n_frames * 0.5)


class TestPhysicalUnits(unittest.TestCase):

    def test_leaf_footprint_at_the_measured_pitch(self):
        leaf = plan_tree(cleared_root()).leaves[0]
        h_mm, w_mm = leaf.size_mm()
        self.assertAlmostEqual(h_mm, 2.4648, places=3)
        self.assertAlmostEqual(w_mm, 1.2324, places=3)

    def test_all_eight_leaves_have_the_same_footprint_in_mm(self):
        sizes = {leaf.size_mm() for leaf in plan_tree(cleared_root()).leaves}
        self.assertEqual(len(sizes), 1)

    def test_no_volume_is_invented(self):
        """Footprint follows from the pitch; volume needs the unmeasured gap
        (objectives.md §2.4 q3). Nothing here reports a volume at all."""
        from chiphealth.config import ChipConfig
        self.assertIsNone(ChipConfig().gap_um)
        self.assertFalse(hasattr(DropNode, "volume_nl"))


class TestFinalStageStretch(unittest.TestCase):
    """`SplitParams.final_stretch_ratio`: widen the LAST stage only.

    Added 2026-08-17 after a live 8-piece run failed to fully separate at the
    4->8 stage. Separation is not a settable margin -- `neck_gap` is
    `stretch_to(e) - e` -- so the only lever is stretching the parent further
    before eroding, and it is restricted to the last stage so that stages
    proven on hardware keep their proven numbers.
    """

    def test_default_is_off_and_changes_nothing(self):
        """The whole feature must be invisible unless asked for."""
        self.assertIsNone(P.DEFAULT.final_stretch_ratio)
        base = plan_tree(SP.split_root(), ("W", "H", "W"))
        same = plan_tree(SP.split_root(), ("W", "H", "W"), P.SplitParams())
        self.assertEqual(base.n_frames, same.n_frames)
        self.assertEqual([n.bounds() for n in base.leaves],
                         [n.bounds() for n in same.leaves])

    def test_for_stage_substitutes_only_the_last_stage(self):
        sp = P.SplitParams(final_stretch_ratio=2.2)
        self.assertEqual(sp.for_stage(0, 3).stretch_ratio, P.STRETCH_RATIO)
        self.assertEqual(sp.for_stage(1, 3).stretch_ratio, P.STRETCH_RATIO)
        self.assertEqual(sp.for_stage(2, 3).stretch_ratio, 2.2)

    def test_for_stage_is_idempotent(self):
        """The substituted copy is a plain single-ratio parameter set, so a
        second application cannot substitute again."""
        sp = P.SplitParams(final_stretch_ratio=2.2).for_stage(2, 3)
        self.assertIsNone(sp.final_stretch_ratio)
        self.assertEqual(sp.for_stage(2, 3).stretch_ratio, 2.2)

    def test_earlier_stages_are_untouched_frame_for_frame(self):
        """The point of the whole exercise: stages that worked stay proven."""
        base = plan_tree(SP.split_root(), ("W", "H", "W"))
        wide = plan_tree(SP.split_root(), ("W", "H", "W"),
                         P.SplitParams(final_stretch_ratio=2.2))
        for stage in (0, 1):
            b = [s for s in base.steps if s.stage == stage]
            w = [s for s in wide.steps if s.stage == stage]
            self.assertEqual([s.neck_gap for s in b], [s.neck_gap for s in w])
            self.assertEqual([s.stretch_to for s in b], [s.stretch_to for s in w])
            self.assertEqual([f.label for s in b for f in s.frames],
                             [f.label for s in w for f in s.frames])

    def test_the_last_stage_gap_opens_from_8_to_12(self):
        wide = plan_tree(SP.split_root(), ("W", "H", "W"),
                         P.SplitParams(final_stretch_ratio=2.2))
        last = [s for s in wide.steps if s.stage == 2]
        self.assertEqual({s.neck_gap for s in last}, {12})
        self.assertEqual({s.stretch_to for s in last}, {22})
        self.assertEqual(wide.n_frames, 103)          # was 87

    def test_widening_still_halves_evenly_and_clears(self):
        """Bought separation must not cost symmetry or fit."""
        wide = plan_tree(SP.split_root(), ("W", "H", "W"),
                         P.SplitParams(final_stretch_ratio=2.2))
        self.assertEqual(wide.violations, [])
        self.assertEqual({(n.height, n.width) for n in wide.leaves}, {(10, 5)})
        eq = SP.volume_equality(wide)
        self.assertTrue(eq.equal)
        self.assertEqual(eq.area_electrodes, 50)

    def test_siblings_gain_separation_and_neighbours_lose_it(self):
        """The trade-off that picked 2.2 over 2.4, pinned so it stays visible.

        Widening pushes each child outwards -- towards the NEIGHBOURING
        group's child. Sibling separation and non-sibling separation move in
        opposite directions, so 'more separation' is only true of the pair
        being split.
        """
        def seps(ratio):
            sp = P.SplitParams(final_stretch_ratio=ratio) if ratio else P.DEFAULT
            plan = plan_tree(SP.split_root(), ("W", "H", "W"), sp)
            sib, non = [], []
            for a, b in itertools.combinations(plan.leaves, 2):
                s = SP._separation(a.bounds(), b.bounds())
                (sib if a.id[:-1] == b.id[:-1] else non).append(s)
            return min(sib), min(non)

        self.assertEqual(seps(None), (8, 8))
        self.assertEqual(seps(2.2), (12, 4))
        # 2.4 is planable but leaves two settled pieces at the clearance floor.
        self.assertEqual(seps(2.4), (14, 2))

    def test_it_does_not_leak_into_the_16_piece_tree(self):
        """run_16piece_split.py passes no override, so WHWH is unaffected."""
        plan = plan_tree(SP.split_root(), ("W", "H", "W", "H"))
        self.assertEqual(plan.n_frames, 159)
        self.assertEqual({s.neck_gap for s in plan.steps if s.stage == 3}, {8})


if __name__ == "__main__":
    unittest.main()
