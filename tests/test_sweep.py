"""Path planning tests. No hardware, no OpenCV."""

import unittest

from chiphealth import sweep
from chiphealth.sweep import AXIS_COL, AXIS_ROW

# The real geometry, from spec/p1_chip_health_design.md.
ROWS = COLS = 128
WIN = 20
START_ROW, START_COL = 5, 10   # moved off the edge electrodes 2026-08-12


class TestBands(unittest.TestCase):

    def test_default_bands_start_at_row_one(self):
        """Bands begin at row 1, not at the load position."""
        self.assertEqual(sweep.plan_bands(ROWS, WIN),
                         [1, 21, 41, 61, 81, 101, 109])

    def test_bands_from_the_load_row_leave_the_top_rows_out(self):
        """The old behaviour, kept reachable and pinned.

        Starting bands at the load row loses every row above it -- four of them
        now that the droplet loads at row 5, where it used to be one.
        """
        self.assertEqual(sweep.plan_bands(ROWS, WIN, START_ROW),
                         [5, 25, 45, 65, 85, 105, 109])
        self.assertEqual(sweep.uncovered_rows(ROWS, WIN, START_ROW), [1, 2, 3, 4])

    def test_final_band_is_clamped_not_dropped(self):
        """The last band overlaps rather than leaving the bottom edge untested."""
        tops = sweep.plan_bands(ROWS, WIN)
        self.assertEqual(tops[-1] + WIN - 1, ROWS)
        self.assertLess(tops[-1], tops[-2] + WIN)  # genuinely overlaps

    def test_no_rows_are_uncovered_by_default(self):
        self.assertEqual(sweep.uncovered_rows(ROWS, WIN), [])

    def test_exact_fit_needs_no_clamped_band(self):
        self.assertEqual(sweep.plan_bands(100, 20, 1), [1, 21, 41, 61, 81])

    def test_rejects_off_chip_start(self):
        with self.assertRaises(ValueError):
            sweep.plan_bands(128, 20, 120)
        with self.assertRaises(ValueError):
            sweep.plan_bands(10, 20, 1)


class TestGrowRelease(unittest.TestCase):
    """The pair emitter both passes share. One electrode = two frames."""

    @staticmethod
    def cells(step):
        r0, r1, c0, c1 = step.covers()
        return {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}

    def pair(self, axis, direction, kind=sweep.KIND_GROW):
        return sweep.grow_release(0, 10, 10, 4, 6, axis, direction, kind, band=0)

    def test_the_grow_only_adds_never_drops(self):
        """The whole point: previously-energised cells must stay energised."""
        for axis in (AXIS_COL, AXIS_ROW):
            for d in (+1, -1):
                with self.subTest(axis=axis, direction=d):
                    grow, release = self.pair(axis, d)
                    start = self.cells(sweep.Step(
                        idx=-1, row=10, col=10, h=4, w=6, axis=axis,
                        direction=d, kind=sweep.KIND_GROW, band=0))
                    grown = self.cells(grow)
                    self.assertTrue(start < grown, "grow dropped cells")
                    # Exactly one new rank of electrodes, no more.
                    added = len(grown) - len(start)
                    self.assertEqual(added, 4 if axis == AXIS_COL else 6)

    def test_the_release_restores_the_window_one_electrode_along(self):
        for axis, d, want in ((AXIS_COL, +1, (10, 11)), (AXIS_COL, -1, (10, 9)),
                              (AXIS_ROW, +1, (11, 10)), (AXIS_ROW, -1, (9, 10))):
            with self.subTest(axis=axis, direction=d):
                _, release = self.pair(axis, d)
                self.assertEqual((release.row, release.col), want)
                self.assertEqual((release.h, release.w), (4, 6))

    def test_both_frames_share_the_leading_edge(self):
        """The release drops the trailing edge; it does not advance the front.

        If it did, the release would be entering fresh territory unobserved --
        the detector skips scoring it.
        """
        for axis in (AXIS_COL, AXIS_ROW):
            for d in (+1, -1):
                with self.subTest(axis=axis, direction=d):
                    grow, release = self.pair(axis, d)
                    self.assertEqual(grow.leading_edge, release.leading_edge)

    def test_backwards_growth_moves_the_origin_back_and_widens(self):
        grow, _ = self.pair(AXIS_COL, -1)
        self.assertEqual((grow.col, grow.w), (9, 7))
        grow, _ = self.pair(AXIS_ROW, -1)
        self.assertEqual((grow.row, grow.h), (9, 5))

    def test_forwards_growth_holds_the_origin(self):
        grow, _ = self.pair(AXIS_COL, +1)
        self.assertEqual((grow.col, grow.w), (10, 7))
        grow, _ = self.pair(AXIS_ROW, +1)
        self.assertEqual((grow.row, grow.h), (10, 5))

    def test_the_trailing_frame_is_always_a_release(self):
        """Whatever the grow is labelled, the release must be KIND_RELEASE --
        that label is what stops the detector and simulator scoring it."""
        for kind in (sweep.KIND_GROW, sweep.KIND_TRANSPORT):
            with self.subTest(kind=kind):
                grow, release = self.pair(AXIS_COL, +1, kind=kind)
                self.assertEqual(grow.kind, kind)
                self.assertEqual(release.kind, sweep.KIND_RELEASE)

    def test_indices_are_consecutive(self):
        grow, release = sweep.grow_release(7, 10, 10, 4, 6, AXIS_COL, +1,
                                           sweep.KIND_GROW, band=0)
        self.assertEqual((grow.idx, release.idx), (7, 8))


class TestSerpentine(unittest.TestCase):

    def setUp(self):
        self.steps = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL)

    def test_step_count_and_cost(self):
        # 1798 = 899 electrode-moves x 2 (grow + release), so the droplet is
        # never asked to release behind and grab ahead in the same instant.
        # Was 1802 when the droplet loaded at (2, 5); loading at (5, 10) costs
        # 6 extra frames climbing to row 1 and saves 10 on a shorter prime.
        self.assertEqual(len(self.steps), 1798)
        self.assertAlmostEqual(sweep.total_duration_s(self.steps, 0.5), 899.0)

    def test_every_electrode_is_reached(self):
        """The whole point of the priming leg and the row-1 band start."""
        self.assertEqual(sweep.untested_electrodes(self.steps, ROWS, COLS), set())

    def test_the_old_geometry_missed_732_electrodes(self):
        """Pins the regression the fix closed, so it cannot come back unnoticed.

        Rows 1-4 entirely (512), plus rows 5-24 x cols 10-20 (220) -- band 0 has
        no preceding corner to fill in the columns its single direction misses.

        Both numbers moved with the load position on 2026-08-12: at (2, 5) this
        was 448 = row 1 (128) + rows 2-21 x cols 5-20 (320). Loading four rows
        further down loses four rows instead of one, which is precisely what
        `first_band_row=1` exists to prevent.
        """
        old = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL,
                                    first_band_row=START_ROW, prime=False)
        missed = sweep.untested_electrodes(old, ROWS, COLS)
        self.assertEqual(len(old), 1738)   # 869 moves x 2
        self.assertEqual(len(missed), 732)
        self.assertEqual(sorted({r for r, c in missed if c == 1}), [1, 2, 3, 4])
        self.assertEqual(len({c for r, c in missed if r == 1}), 128)
        self.assertEqual(sorted({c for r, c in missed if r == 5}),
                         list(range(10, 21)))

    def test_the_fix_costs_30_moves(self):
        old = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL,
                                    first_band_row=START_ROW, prime=False)
        self.assertEqual(len(self.steps) - len(old), 60)   # 30 moves x 2

    def test_steps_alternate_grow_then_release(self):
        for a, b in zip(self.steps[::2], self.steps[1::2]):
            self.assertEqual(a.kind, sweep.KIND_GROW)
            self.assertEqual(b.kind, sweep.KIND_RELEASE)

    def test_a_grow_never_releases_anything(self):
        """The whole point: previously-energised cells must stay energised."""
        prev = None
        for s in self.steps:
            r0, r1, c0, c1 = s.covers()
            cur = {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
            if prev is not None and s.kind == sweep.KIND_GROW:
                self.assertTrue(prev <= cur, f"grow at step {s.idx} dropped cells")
            prev = cur

    def test_window_never_leaves_the_chip(self):
        for s in self.steps:
            self.assertGreaterEqual(s.row, 1)
            self.assertGreaterEqual(s.col, 1)
            self.assertLessEqual(s.row + s.h - 1, ROWS)
            self.assertLessEqual(s.col + s.w - 1, COLS)

    def test_no_step_moves_more_than_one_electrode(self):
        """EWOD transport cannot jump: the window must overlap the droplet."""
        prev = (START_ROW, START_COL)
        for s in self.steps:
            # A grow leaves the origin put and widens; only the release
            # advances it. So the bound is one, not exactly one.
            self.assertLessEqual(abs(s.row - prev[0]) + abs(s.col - prev[1]), 1,
                                 f"step {s.idx} jumped from {prev} to {(s.row, s.col)}")
            prev = (s.row, s.col)

    def test_run_climbs_to_row_one_before_sweeping(self):
        """The droplet loads at row 5; band 0 starts at row 1."""
        first = self.steps[0]
        self.assertEqual(first.kind, sweep.KIND_GROW)
        self.assertEqual(first.axis, AXIS_ROW)
        self.assertEqual(first.direction, -1)
        # A grow widens backwards without moving the origin's far edge, so the
        # first frame's origin is one row above the load row, not row 1.
        self.assertEqual(first.row, START_ROW - 1)
        # It does reach row 1, and only then starts travelling in columns.
        climb = [s for s in self.steps if s.axis == AXIS_ROW and s.direction == -1]
        self.assertEqual(climb[-1].row, 1)
        self.assertEqual(self.steps[len(climb)].axis, AXIS_COL)

    def test_band_zero_primes_out_and_back(self):
        """Right to col_min+w, back to col_min, then away -- the corner turn
        every other band gets for free from its band change."""
        band0 = [s for s in self.steps if s.band == 0 and s.axis == AXIS_COL]
        cols = [s.col for s in band0]
        # The first step is a grow, which widens without moving the
        # origin; the release that follows is what advances it.
        self.assertEqual(cols[0], START_COL)          # grow, origin held
        self.assertEqual(cols[1], START_COL + 1)      # release, heads right
        # 5 -> 21 is 16 moves = 32 steps now that each move is a pair.
        self.assertIn(21, cols[:40])                  # out to col_min + w
        self.assertIn(1, cols)                        # back to the left edge
        self.assertEqual(cols[-1], COLS - WIN + 1)    # then away to the right

    def test_band_zero_leading_edges_span_the_whole_width(self):
        band0 = [s for s in self.steps if s.band == 0 and s.axis == AXIS_COL]
        self.assertEqual({s.leading_edge for s in band0}, set(range(1, COLS + 1)))

    def test_bands_alternate_direction(self):
        travel = [s for s in self.steps if s.axis == AXIS_COL]
        by_band = {}
        for s in travel:
            by_band.setdefault(s.band, []).append(s)
        # Band 0 ends heading right; band 1 must head left, and so on.
        self.assertEqual(by_band[0][-1].direction, +1)
        self.assertEqual(by_band[1][-1].direction, -1)
        self.assertEqual(by_band[2][-1].direction, +1)

    def test_reaches_both_column_extremes(self):
        cols = {s.col for s in self.steps}
        self.assertIn(1, cols)
        self.assertIn(COLS - WIN + 1, cols)

    def test_band_changes_are_row_moves(self):
        changes = [s for s in self.steps if s.axis == AXIS_ROW]
        self.assertTrue(all(s.axis == AXIS_ROW for s in changes))
        # The only upward moves are the initial climb from the load row to
        # row 1: 4 moves from row 5, each a grow+release pair, so 8 frames.
        climb = 2 * (START_ROW - 1)
        self.assertEqual(sum(1 for s in changes if s.direction == -1), climb)
        self.assertTrue(all(s.direction == +1 for s in changes[climb:]))


class TestEdges(unittest.TestCase):

    def _step(self, direction, axis=AXIS_COL, row=10, col=10):
        return sweep.Step(idx=0, row=row, col=col, h=WIN, w=WIN,
                          axis=axis, direction=direction,
                          kind=sweep.KIND_TRAVEL, band=0)

    def test_leading_edge_rightward(self):
        s = self._step(+1)
        self.assertEqual(s.leading_edge, 10 + WIN - 1)

    def test_leading_edge_leftward(self):
        s = self._step(-1)
        self.assertEqual(s.leading_edge, 10)

    def test_leading_edge_downward(self):
        s = self._step(+1, axis=AXIS_ROW)
        self.assertEqual(s.leading_edge, 10 + WIN - 1)

    def test_leading_edge_blocks_are_a_single_column_strip(self):
        """Only the contact line is tested, never the bridged interior."""
        s = self._step(+1)
        leading = sweep.leading_edge_blocks(s, 4)
        self.assertEqual(len({bc for _, bc in leading}), 1)   # one column of blocks
        self.assertLessEqual(len(leading), WIN // 4 + 1)      # 20 rows, may straddle


class TestBlocks(unittest.TestCase):

    def test_grid_shape(self):
        self.assertEqual(sweep.block_grid_shape(128, 128, 4), (32, 32))

    def test_block_of_is_zero_indexed_from_electrode_one(self):
        self.assertEqual(sweep.block_of(1, 1, 4), (0, 0))
        self.assertEqual(sweep.block_of(4, 4, 4), (0, 0))
        self.assertEqual(sweep.block_of(5, 5, 4), (1, 1))
        self.assertEqual(sweep.block_of(128, 128, 4), (31, 31))

    def test_bounds_round_trip(self):
        for br, bc in [(0, 0), (5, 9), (31, 31)]:
            r0, r1, c0, c1 = sweep.block_bounds(br, bc, 4)
            self.assertEqual(sweep.block_of(r0, c0, 4), (br, bc))
            self.assertEqual(sweep.block_of(r1, c1, 4), (br, bc))


class TestFineRoute(unittest.TestCase):

    def test_nearest_first(self):
        targets = [(100, 100), (10, 10), (50, 50)]
        ordered, dropped = sweep.plan_fine_route((1.0, 1.0), targets)
        self.assertEqual(ordered, [(10, 10), (50, 50), (100, 100)])
        self.assertEqual(dropped, [])

    def test_cap_reports_what_it_dropped(self):
        """A silently truncated list would read as 'everything was checked'."""
        targets = [(10, 10), (20, 20), (30, 30), (40, 40)]
        ordered, dropped = sweep.plan_fine_route((1.0, 1.0), targets, max_targets=2)
        self.assertEqual(len(ordered), 2)
        self.assertEqual(dropped, [(30, 30), (40, 40)])

    def test_transport_budget_scales_with_distance(self):
        near = sweep.expected_transport_steps((1, 1), (1, 10), 2.0)
        far = sweep.expected_transport_steps((1, 1), (1, 100), 2.0)
        self.assertEqual(near, 18)
        self.assertGreater(far, near)

    def test_budget_is_never_zero(self):
        self.assertGreaterEqual(sweep.expected_transport_steps((5, 5), (5, 5), 2.0), 1)


class TestVertical(unittest.TestCase):

    def test_vertical_sweep_transposes_axes(self):
        steps = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        travel = [s for s in steps if s.kind == sweep.KIND_TRAVEL]
        self.assertTrue(all(s.axis == AXIS_ROW for s in travel))
        for s in steps:
            self.assertLessEqual(s.row + s.h - 1, ROWS)
            self.assertLessEqual(s.col + s.w - 1, COLS)

    def test_vertical_sweep_also_reaches_every_electrode(self):
        vertical = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        self.assertEqual(sweep.untested_electrodes(vertical, ROWS, COLS), set())

    def test_vertical_step_count_differs_slightly_from_horizontal(self):
        """Not a bug: the load position transposes, so (5, 10) becomes a start
        at row 10, col 5 and the climb to the first band is a different length
        from the horizontal pass's."""
        vertical = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        self.assertEqual(len(vertical), 1818)   # 909 moves x 2
        horizontal = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        self.assertNotEqual(len(vertical), len(horizontal))

    def test_both_axes_together_cost_roughly_double(self):
        h = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        v = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        minutes = sweep.total_duration_s(h + v, 0.5) / 60.0
        self.assertAlmostEqual(minutes, 30.13, delta=0.3)


if __name__ == "__main__":
    unittest.main()


class TestPartialSweep(unittest.TestCase):
    """--bands: a short traversal for step-delay timing work.

    Deliberately incomplete. The value of the flag is that the run says so.
    """

    def plan(self, n=None):
        return sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL,
                                     max_bands=n)

    def test_one_band_is_a_prefix_of_the_full_sweep(self):
        full, part = self.plan(), self.plan(1)
        self.assertEqual([s.to_dict() for s in part],
                         [s.to_dict() for s in full[:len(part)]])

    def test_each_extra_band_only_adds(self):
        prev = 0
        for n in range(1, 8):
            cur = len(self.plan(n))
            self.assertGreater(cur, prev, f"band {n} added nothing")
            prev = cur
        self.assertEqual(prev, len(self.plan()))

    def test_only_the_requested_bands_appear(self):
        for n in (1, 3):
            with self.subTest(bands=n):
                self.assertEqual({s.band for s in self.plan(n)}, set(range(n)))

    def test_one_band_is_a_small_fraction_of_the_run(self):
        """The whole point: a ramp test at four delays must not cost an hour."""
        self.assertLess(len(self.plan(1)) / len(self.plan()), 0.25)

    def test_indices_stay_contiguous(self):
        steps = self.plan(2)
        self.assertEqual([s.idx for s in steps], list(range(len(steps))))

    def test_a_partial_sweep_leaves_most_of_the_chip_untested(self):
        missed = sweep.untested_electrodes(self.plan(1), ROWS, COLS)
        self.assertGreater(len(missed), ROWS * COLS // 2)

    def test_uncovered_rows_reports_the_truncation(self):
        rows = sweep.uncovered_rows(ROWS, WIN, 1, max_bands=1)
        self.assertEqual(rows, list(range(WIN + 1, ROWS + 1)))
        self.assertEqual(sweep.uncovered_rows(ROWS, WIN, 1), [])

    def test_the_full_sweep_is_unchanged_by_the_new_parameter(self):
        self.assertEqual([s.to_dict() for s in self.plan(None)],
                         [s.to_dict() for s in sweep.plan_serpentine(
                             ROWS, COLS, WIN, WIN, START_ROW, START_COL)])

    def test_zero_or_negative_bands_is_refused(self):
        for n in (0, -1):
            with self.subTest(bands=n), self.assertRaises(ValueError):
                self.plan(n)

    def test_vertical_honours_it_too(self):
        part = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL,
                                   max_bands=1)
        full = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        self.assertLess(len(part), len(full))
        self.assertEqual({s.band for s in part}, {0})
