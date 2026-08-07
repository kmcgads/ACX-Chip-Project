"""Per-run registration: misclick guards, caching, and drift.

Standard library only. These are the checks that are possible *before* the
droplet test runs -- four points give an exact homography fit, so nothing
downstream can tell a typo from a good pick.
"""

import json
import tempfile
import unittest
from pathlib import Path

from chiphealth import calibration
from chiphealth.calibration import Calibration

FRAME = (1920, 1080)
GOOD = [(100.0, 80.0), (1500.0, 90.0), (1495.0, 1000.0), (105.0, 995.0)]


class TestValidateCorners(unittest.TestCase):

    def test_a_sane_pick_passes(self):
        self.assertEqual(calibration.validate_corners(GOOD, FRAME), [])

    def test_wrong_count(self):
        self.assertTrue(calibration.validate_corners(GOOD[:3], FRAME))
        self.assertTrue(calibration.validate_corners([], FRAME))
        self.assertTrue(calibration.validate_corners(None, FRAME))

    def test_point_outside_the_frame(self):
        bad = [(100.0, 80.0), (5000.0, 90.0), (1495.0, 1000.0), (105.0, 995.0)]
        problems = calibration.validate_corners(bad, FRAME)
        self.assertTrue(any("outside" in p for p in problems))
        self.assertTrue(any("top-right" in p for p in problems))

    def test_double_click_is_caught(self):
        bad = [(100.0, 80.0), (105.0, 82.0), (1495.0, 1000.0), (105.0, 995.0)]
        problems = calibration.validate_corners(bad, FRAME)
        self.assertTrue(any("double-click" in p for p in problems))

    def test_collinear_points_are_caught(self):
        bad = [(100.0, 100.0), (400.0, 100.0), (800.0, 100.0), (1200.0, 100.0)]
        problems = calibration.validate_corners(bad, FRAME)
        self.assertTrue(problems)

    def test_wrong_winding_order_is_caught(self):
        """Counter-clockwise means the corners were clicked in the wrong order.

        The homography would still fit perfectly and mirror the whole chip.
        """
        problems = calibration.validate_corners(list(reversed(GOOD)), FRAME)
        self.assertTrue(any("wrong order" in p for p in problems))

    def test_non_square_quad_is_flagged(self):
        """The array is 128x128, so a long thin quad is the wrong feature."""
        bad = [(100.0, 500.0), (1500.0, 500.0), (1500.0, 560.0), (100.0, 560.0)]
        problems = calibration.validate_corners(bad, FRAME)
        self.assertTrue(any("roughly square" in p for p in problems))

    def test_frame_size_optional(self):
        self.assertEqual(calibration.validate_corners(GOOD), [])

    def test_signed_area_positive_for_correct_order(self):
        self.assertGreater(calibration.signed_area(GOOD), 0)
        self.assertLess(calibration.signed_area(list(reversed(GOOD))), 0)


class TestCache(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "calibration.json"
        self.cal = Calibration(corners_px=tuple(tuple(p) for p in GOOD),
                               frame_size=FRAME, px_per_electrode=(10.9, 7.1),
                               created="2026-08-07T12:00:00+00:00",
                               chip_id="chip-A")

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        calibration.save_cache(self.path, self.cal)
        again = calibration.load_cache(self.path)
        self.assertEqual(again.corners_px, self.cal.corners_px)
        self.assertEqual(again.frame_size, self.cal.frame_size)
        self.assertEqual(again.px_per_electrode, self.cal.px_per_electrode)
        self.assertEqual(again.chip_id, "chip-A")

    def test_missing_file_is_none_not_an_error(self):
        self.assertIsNone(calibration.load_cache(self.path))

    def test_corrupt_cache_is_ignored_rather_than_fatal(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(calibration.load_cache(self.path))

    def test_cache_missing_keys_is_ignored(self):
        self.path.write_text(json.dumps({"nonsense": 1}), encoding="utf-8")
        self.assertIsNone(calibration.load_cache(self.path))

    def test_creates_parent_directories(self):
        nested = Path(self.tmp.name) / "a" / "b" / "cal.json"
        calibration.save_cache(nested, self.cal)
        self.assertTrue(nested.exists())


class TestDrift(unittest.TestCase):

    def _cal(self, corners, ppe=(10.9, 7.1), frame=FRAME, created="t0"):
        return Calibration(corners_px=tuple(tuple(p) for p in corners),
                           frame_size=frame, px_per_electrode=ppe, created=created)

    def test_first_calibration_has_nothing_to_compare(self):
        report = calibration.drift_report(self._cal(GOOD), None)
        self.assertTrue(report["first_calibration"])
        self.assertFalse(report["warn"])
        self.assertIn("First calibration", calibration.describe_drift(report))

    def test_no_movement(self):
        report = calibration.drift_report(self._cal(GOOD), self._cal(GOOD))
        self.assertEqual(report["max_delta_px"], 0.0)
        self.assertFalse(report["warn"])

    def test_small_nudge_is_measured_but_not_warned(self):
        moved = [(x + 3, y + 4) for x, y in GOOD]  # 5px each
        report = calibration.drift_report(self._cal(moved), self._cal(GOOD))
        self.assertAlmostEqual(report["max_delta_px"], 5.0, places=1)
        self.assertFalse(report["warn"])

    def test_large_jump_is_warned_as_a_possible_misclick(self):
        moved = [(x + 400, y) for x, y in GOOD]
        report = calibration.drift_report(self._cal(moved), self._cal(GOOD))
        self.assertGreater(report["max_delta_px"], 150)
        self.assertTrue(report["warn"])
        self.assertIn("misclick", calibration.describe_drift(report))

    def test_scale_change_is_reported(self):
        """A moving camera changes magnification, which changes how noisy the
        measurement is -- that has to be explicable after the fact."""
        report = calibration.drift_report(self._cal(GOOD, ppe=(12.0, 7.8)),
                                          self._cal(GOOD, ppe=(10.9, 7.1)))
        self.assertGreater(report["scale_change_pct"], 5.0)
        self.assertIn("scale changed", calibration.describe_drift(report))

    def test_frame_size_change_is_called_out(self):
        report = calibration.drift_report(self._cal(GOOD, frame=(1280, 720)),
                                          self._cal(GOOD, frame=FRAME))
        self.assertTrue(report["frame_size_changed"])
        self.assertIn("FRAME SIZE ALSO CHANGED", calibration.describe_drift(report))

    def test_per_corner_deltas(self):
        moved = list(GOOD)
        moved[2] = (moved[2][0] + 30, moved[2][1])
        report = calibration.drift_report(self._cal(moved), self._cal(GOOD))
        self.assertEqual(report["deltas_px"], [0.0, 0.0, 30.0, 0.0])


if __name__ == "__main__":
    unittest.main()
