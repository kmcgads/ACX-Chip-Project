"""Path planning tests. No hardware, no OpenCV."""

import unittest

from chiphealth import sweep
from chiphealth.sweep import AXIS_COL, AXIS_ROW

# The real geometry, from spec/p1_chip_health_design.md.
ROWS = COLS = 128
WIN = 20
START_ROW, START_COL = 2, 5


class TestBands(unittest.TestCase):

    def test_default_bands_start_at_row_one(self):
        """Bands begin at row 1, not at the load position."""
        self.assertEqual(sweep.plan_bands(ROWS, WIN),
                         [1, 21, 41, 61, 81, 101, 109])

    def test_bands_from_the_load_row_leave_row_one_out(self):
        """The old behaviour, kept reachable and pinned."""
        self.assertEqual(sweep.plan_bands(ROWS, WIN, START_ROW),
                         [2, 22, 42, 62, 82, 102, 109])
        self.assertEqual(sweep.uncovered_rows(ROWS, WIN, START_ROW), [1])

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


class TestSerpentine(unittest.TestCase):

    def setUp(self):
        self.steps = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL)

    def test_step_count_and_cost(self):
        # 1802 = 901 electrode-moves x 2 (grow + release), so the droplet is
        # never asked to release behind and grab ahead in the same instant.
        self.assertEqual(len(self.steps), 1802)
        self.assertAlmostEqual(sweep.total_duration_s(self.steps, 0.5), 901.0)

    def test_every_electrode_is_reached(self):
        """The whole point of the priming leg and the row-1 band start."""
        self.assertEqual(sweep.untested_electrodes(self.steps, ROWS, COLS), set())

    def test_the_old_geometry_missed_448_electrodes(self):
        """Pins the regression the fix closed, so it cannot come back unnoticed.

        Row 1 entirely (128), plus rows 2-21 x cols 5-20 (320) -- band 0 had no
        preceding corner to fill in the columns its single direction misses.
        """
        old = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL,
                                    first_band_row=START_ROW, prime=False)
        missed = sweep.untested_electrodes(old, ROWS, COLS)
        self.assertEqual(len(old), 1734)   # 867 moves x 2
        self.assertEqual(len(missed), 448)
        self.assertEqual(len({c for r, c in missed if r == 1}), 128)
        self.assertEqual(sorted({c for r, c in missed if r == 2}), list(range(5, 21)))

    def test_the_fix_costs_34_steps(self):
        old = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL,
                                    first_band_row=START_ROW, prime=False)
        self.assertEqual(len(self.steps) - len(old), 68)   # 34 moves x 2

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
        """The droplet loads at row 2; band 0 starts at row 1."""
        first = self.steps[0]
        self.assertEqual(first.kind, sweep.KIND_GROW)
        self.assertEqual(first.axis, AXIS_ROW)
        self.assertEqual(first.direction, -1)
        self.assertEqual(first.row, 1)

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
        # Exactly one goes upward: the initial climb from the load row to row 1.
        # The single upward climb is a grow+release pair, so two steps.
        self.assertEqual(sum(1 for s in changes if s.direction == -1), 2)
        self.assertTrue(all(s.direction == +1 for s in changes[2:]))


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
        """Not a bug: the load position transposes to row 5, col 2, so the
        climb to the first band is 4 steps instead of 1."""
        vertical = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        self.assertEqual(len(vertical), 1814)   # 907 moves x 2

    def test_both_axes_together_cost_roughly_double(self):
        h = sweep.plan_serpentine(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        v = sweep.plan_vertical(ROWS, COLS, WIN, WIN, START_ROW, START_COL)
        minutes = sweep.total_duration_s(h + v, 0.5) / 60.0
        self.assertAlmostEqual(minutes, 30.13, delta=0.3)


if __name__ == "__main__":
    unittest.main()
