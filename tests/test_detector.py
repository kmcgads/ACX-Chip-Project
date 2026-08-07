"""Detector tests: drag, residue, no-movement, unreachable.

Standard library only. The detector consumes extracted blobs in electrode
coordinates, so the whole decision layer is testable with no camera and no rig.
"""

import unittest

from chiphealth.config import DetectorConfig
from chiphealth.detector import (Blob, Detector, Observation, compute_lag,
                                 KIND_DRAG, KIND_NO_MOVEMENT, KIND_RESIDUE,
                                 KIND_UNREACHABLE)
from chiphealth.sweep import AXIS_COL, Step, KIND_TRAVEL

WIN = 20


def step(idx, row, col, direction=+1, axis=AXIS_COL):
    return Step(idx=idx, row=row, col=col, h=WIN, w=WIN, axis=axis,
                direction=direction, kind=KIND_TRAVEL, band=0)


def tracking_blob(s, lag=0.0):
    """A droplet sitting exactly under the window, optionally trailing by `lag`."""
    if s.axis == AXIS_COL:
        col = s.col - 0.5 - (lag * s.direction)
        row = s.row - 0.5
    else:
        col = s.col - 0.5
        row = s.row - 0.5 - (lag * s.direction)
    return Blob(centroid_row=row + WIN / 2.0, centroid_col=col + WIN / 2.0,
                area_electrodes=float(WIN * WIN),
                row=row, col=col, height=float(WIN), width=float(WIN))


def obs(s, *blobs, frame=None):
    return Observation(step_idx=s.idx, frame_index=frame if frame is not None else s.idx,
                       t=s.idx * 0.5, blobs=tuple(blobs))


class TestLag(unittest.TestCase):

    def test_perfect_tracking_is_zero(self):
        s = step(0, 10, 10)
        self.assertAlmostEqual(compute_lag(s, tracking_blob(s)), 0.0, places=9)

    def test_trailing_droplet_gives_positive_lag(self):
        s = step(0, 10, 10)
        self.assertAlmostEqual(compute_lag(s, tracking_blob(s, lag=3.0)), 3.0, places=9)

    def test_lag_sign_is_direction_independent(self):
        right = step(0, 10, 10, direction=+1)
        left = step(0, 10, 10, direction=-1)
        self.assertAlmostEqual(compute_lag(right, tracking_blob(right, 2.5)), 2.5, places=9)
        self.assertAlmostEqual(compute_lag(left, tracking_blob(left, 2.5)), 2.5, places=9)

    def test_row_axis_lag(self):
        from chiphealth.sweep import AXIS_ROW
        s = step(0, 10, 10, axis=AXIS_ROW)
        self.assertAlmostEqual(compute_lag(s, tracking_blob(s, 4.0)), 4.0, places=9)

    def test_droplet_running_ahead_is_negative_not_a_fault(self):
        s = step(0, 10, 10)
        self.assertLess(compute_lag(s, tracking_blob(s, lag=-1.5)), 0.0)


class TestDrag(unittest.TestCase):

    def setUp(self):
        self.cfg = DetectorConfig()
        self.det = Detector(self.cfg, block=4)

    def test_clean_run_fires_nothing(self):
        events = []
        for i in range(30):
            s = step(i, 10, 10 + i)
            events += self.det.observe(s, obs(s, tracking_blob(s))).events
        self.assertEqual(events, [])

    def test_transient_lag_below_persistence_does_not_fire(self):
        """One or two stalled steps is detection wobble, not a sticky spot."""
        events = []
        for i in range(10):
            s = step(i, 10, 10 + i)
            lag = 5.0 if i in (4, 5) else 0.0  # persist is 3
            events += self.det.observe(s, obs(s, tracking_blob(s, lag))).events
        self.assertEqual(events, [])

    def test_sustained_lag_fires_once(self):
        events = []
        for i in range(10):
            s = step(i, 10, 10 + i)
            lag = 5.0 if i >= 4 else 0.0
            events += self.det.observe(s, obs(s, tracking_blob(s, lag))).events
        drags = [e for e in events if e.kind == KIND_DRAG]
        self.assertEqual(len(drags), 1)
        self.assertEqual(drags[0].step_idx, 6)  # 4, 5, 6 -> persistence met
        self.assertGreaterEqual(drags[0].severity, 5.0)

    def test_lag_below_threshold_never_fires(self):
        events = []
        for i in range(20):
            s = step(i, 10, 10 + i)
            events += self.det.observe(s, obs(s, tracking_blob(s, 1.0))).events
        self.assertEqual(events, [])

    def test_streak_resets_on_recovery(self):
        events = []
        pattern = [5.0, 5.0, 0.0, 5.0, 5.0, 0.0]
        for i, lag in enumerate(pattern):
            s = step(i, 10, 10 + i)
            events += self.det.observe(s, obs(s, tracking_blob(s, lag))).events
        self.assertEqual(events, [])

    def test_deduplicated_per_block(self):
        """One sticky spot must not dominate the dataset."""
        events = []
        for i in range(40):
            s = step(i, 10, 10)  # window not advancing -> same block each time
            events += self.det.observe(s, obs(s, tracking_blob(s, 6.0))).events
        self.assertEqual(len([e for e in events if e.kind == KIND_DRAG]), 1)

    def test_event_carries_dataset_fields(self):
        for i in range(6):
            s = step(i, 10, 10 + i)
            res = self.det.observe(s, obs(s, tracking_blob(s, 6.0)))
            if res.events:
                e = res.events[0]
                self.assertEqual(e.label_source, "auto")
                self.assertEqual(e.stage, "coarse")
                self.assertGreaterEqual(e.detector_version, 1)
                self.assertIn("electrode", e.detail)
                return
        self.fail("no event fired")


class TestNoMovement(unittest.TestCase):

    def test_static_droplet_fires(self):
        det = Detector(DetectorConfig(), block=4)
        frozen = tracking_blob(step(0, 10, 10))
        events = []
        for i in range(10):
            s = step(i, 10, 10 + i)
            events += det.observe(s, obs(s, frozen)).events
        self.assertTrue(any(e.kind == KIND_NO_MOVEMENT for e in events))

    def test_moving_droplet_does_not_fire(self):
        det = Detector(DetectorConfig(), block=4)
        events = []
        for i in range(20):
            s = step(i, 10, 10 + i)
            events += det.observe(s, obs(s, tracking_blob(s))).events
        self.assertFalse(any(e.kind == KIND_NO_MOVEMENT for e in events))


class TestResidue(unittest.TestCase):

    def setUp(self):
        self.det = Detector(DetectorConfig(), block=4)

    def _advance(self, n=12, extra=()):
        """Walk the window right, optionally with extra blobs present."""
        events = []
        for i in range(n):
            s = step(i, 10, 10 + i)
            events += self.det.observe(s, obs(s, tracking_blob(s), *extra)).events
        return events

    def test_liquid_left_behind_is_flagged(self):
        self._advance(8)
        left_behind = Blob(centroid_row=15.0, centroid_col=11.0, area_electrodes=3.0,
                           row=14.0, col=10.0, height=2.0, width=2.0)
        events = []
        for i in range(8, 16):
            s = step(i, 10, 10 + i)
            events += self.det.observe(s, obs(s, tracking_blob(s), left_behind)).events
        residue = [e for e in events if e.kind == KIND_RESIDUE]
        self.assertEqual(len(residue), 1)
        self.assertAlmostEqual(residue[0].col, 11.0, places=6)

    def test_liquid_under_the_window_is_not_residue(self):
        events = self._advance(12)
        self.assertFalse(any(e.kind == KIND_RESIDUE for e in events))

    def test_tiny_speck_below_threshold_ignored(self):
        self._advance(8)
        speck = Blob(centroid_row=15.0, centroid_col=11.0, area_electrodes=0.2,
                     row=14.9, col=10.9, height=0.2, width=0.2)
        events = []
        for i in range(8, 14):
            s = step(i, 10, 10 + i)
            events += self.det.observe(s, obs(s, tracking_blob(s), speck)).events
        self.assertFalse(any(e.kind == KIND_RESIDUE for e in events))

    def test_swept_region_grows_behind_the_window(self):
        self._advance(10)
        self.assertTrue(self.det.swept_cells)
        # everything swept must be behind the current window
        self.assertTrue(all(c < 10 + 9 + WIN for _, c in self.det.swept_cells))


class TestMisc(unittest.TestCase):

    def test_no_blobs_is_recorded_not_scored(self):
        """A blank frame is a detection failure, not a per-electrode verdict."""
        det = Detector(DetectorConfig(), block=4)
        s = step(0, 10, 10)
        res = det.observe(s, obs(s))
        self.assertEqual(res.events, [])
        self.assertFalse(res.clean)
        self.assertIsNone(res.lag)

    def test_tested_blocks_are_the_leading_edge_only(self):
        det = Detector(DetectorConfig(), block=4)
        s = step(0, 10, 10)
        res = det.observe(s, obs(s, tracking_blob(s)))
        self.assertTrue(res.tested_blocks)
        self.assertLessEqual(len(res.tested_blocks), WIN // 4 + 1)

    def test_unreachable_event(self):
        det = Detector(DetectorConfig(), block=4)
        e = det.unreachable((40, 60), step_idx=900, frame_index=1200, t=450.0,
                            spent=61, budget=60)
        self.assertEqual(e.kind, KIND_UNREACHABLE)
        self.assertEqual((e.block_row, e.block_col), (9, 14))
        self.assertIn("60", e.detail)

    def test_event_serialises(self):
        det = Detector(DetectorConfig(), block=4)
        e = det.unreachable((10, 10), 1, 1, 0.5, 5, 4)
        d = e.to_dict()
        for key in ("kind", "step_idx", "row", "col", "block_row", "block_col",
                    "severity", "detail", "stage", "detector_version",
                    "label_source"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
