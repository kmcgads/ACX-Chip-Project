"""Electrode <-> pixel mapping tests. numpy only, no OpenCV."""

import unittest
from typing import TypedDict

import numpy as np

from chiphealth import geometry
from chiphealth.geometry import ElectrodeFrame

from . import not_none

ROWS = COLS = 128

# A plausible whole-chip framing: slightly rotated and perspective-skewed, as a
# hand-aimed camera over a chip actually is.
CORNERS = [[100.0, 80.0], [1500.0, 90.0], [1495.0, 1000.0], [105.0, 995.0]]


class TestHomography(unittest.TestCase):

    def test_exact_fit_on_four_points(self):
        src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
        dst = np.array([[10, 20], [30, 22], [31, 45], [9, 43]], dtype=float)
        h = geometry.fit_homography(src, dst)
        got = geometry.apply_homography(h, src)
        np.testing.assert_allclose(got, dst, atol=1e-9)

    def test_identity(self):
        pts = np.array([[0, 0], [5, 0], [5, 7], [0, 7]], dtype=float)
        h = geometry.fit_homography(pts, pts)
        np.testing.assert_allclose(geometry.apply_homography(h, pts), pts, atol=1e-9)

    def test_needs_four_points(self):
        with self.assertRaises(ValueError):
            geometry.fit_homography(np.zeros((3, 2)), np.zeros((3, 2)))

    def test_shape_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            geometry.fit_homography(np.zeros((4, 2)), np.zeros((5, 2)))


class TestElectrodeFrame(unittest.TestCase):

    def setUp(self):
        self.f = ElectrodeFrame.from_corners(CORNERS, ROWS, COLS)

    def test_corners_map_back_to_the_clicked_pixels(self):
        np.testing.assert_allclose(self.f.corners_px(), np.array(CORNERS), atol=1e-6)

    def test_round_trip_is_exact(self):
        for row, col in [(1, 1), (2, 5), (64, 64), (128, 128), (109, 109)]:
            x, y = self.f.electrode_to_pixel(row, col)
            r, c = self.f.pixel_to_electrode(x, y)
            self.assertAlmostEqual(r, row, places=6)
            self.assertAlmostEqual(c, col, places=6)

    def test_electrode_one_one_sits_near_the_top_left_corner(self):
        x, y = self.f.electrode_to_pixel(1, 1)
        self.assertLess(abs(x - CORNERS[0][0]), 20)
        self.assertLess(abs(y - CORNERS[0][1]), 20)

    def test_px_per_electrode_is_frame_over_grid(self):
        pc, pr = self.f.px_per_electrode()
        self.assertAlmostEqual(pc, 1395.0 / 128, delta=0.5)
        self.assertAlmostEqual(pr, 912.5 / 128, delta=0.5)

    def test_min_area_px_replaces_the_legacy_fixed_threshold(self):
        """camera.py:59's min_area=500 discards several electrodes of residue."""
        one = self.f.min_area_px(1)
        self.assertLess(one, 500.0)
        self.assertAlmostEqual(self.f.min_area_px(2), 2 * one, places=6)

    def test_area_conversion_round_trips(self):
        px = self.f.min_area_px(7.5)
        self.assertAlmostEqual(self.f.area_px_to_electrodes(px), 7.5, places=6)

    def test_bbox_conversion(self):
        x, y = self.f.electrode_to_pixel(10, 10)
        pc, pr = self.f.px_per_electrode()
        r0, c0, h, w = self.f.bbox_px_to_electrode(x, y, pc * 20, pr * 20)
        self.assertAlmostEqual(h, 20, delta=1.0)
        self.assertAlmostEqual(w, 20, delta=1.0)
        self.assertAlmostEqual(r0, 9.5, delta=1.0)
        self.assertAlmostEqual(c0, 9.5, delta=1.0)

    def test_serialisation_round_trip(self):
        again = ElectrodeFrame.from_dict(self.f.to_dict())
        self.assertEqual(self.f.electrode_to_pixel(50, 50),
                         again.electrode_to_pixel(50, 50))

    def test_contains_and_clamp(self):
        self.assertTrue(self.f.contains(1, 1))
        self.assertTrue(self.f.contains(128, 128))
        self.assertFalse(self.f.contains(0, 5))
        self.assertFalse(self.f.contains(5, 129))
        self.assertEqual(self.f.clamp(-3, 500), (1.0, 128.0))

    def test_rejects_wrong_corner_count(self):
        with self.assertRaises(ValueError):
            ElectrodeFrame.from_corners(CORNERS[:3], ROWS, COLS)


class _Expected(TypedDict):
    """Keeps the per-key types through ``**EXPECTED``.

    As a plain dict literal this is inferred ``dict[str, float]`` -- the join of
    the int and float values -- so unpacking it handed ``float`` to the four
    ``int`` parameters and produced twenty errors about arguments the tests
    were never actually passing. The values below are and always were ints;
    only the inference was lossy. ``expected_row``/``col``/``h``/``w`` are
    genuinely integers: they describe the commanded ``Drop``, whose fields are
    ints. The *observed* centroid is deliberately fractional, and is a separate
    parameter.
    """

    expected_row: int
    expected_col: int
    expected_h: int
    expected_w: int
    centroid_tol_electrodes: float
    area_tol_frac: float


class TestRegistrationCheck(unittest.TestCase):
    """The free guard: the initial droplet is at a known place and size."""

    EXPECTED: _Expected = {
        "expected_row": 2, "expected_col": 5,
        "expected_h": 20, "expected_w": 20,
        "centroid_tol_electrodes": 4.0, "area_tol_frac": 0.5}

    def test_correct_registration_passes(self):
        res = geometry.check_registration((11.5, 14.5), 400.0, **self.EXPECTED)
        self.assertTrue(res.ok)
        self.assertEqual(res.reasons, ())
        self.assertAlmostEqual(res.centroid_error_electrodes, 0.0, places=6)
        self.assertAlmostEqual(res.area_ratio, 1.0, places=6)

    def test_small_error_still_passes(self):
        res = geometry.check_registration((13.0, 16.0), 380.0, **self.EXPECTED)
        self.assertTrue(res.ok)

    def test_displaced_centroid_fails_with_a_reason(self):
        res = geometry.check_registration((60.0, 60.0), 400.0, **self.EXPECTED)
        self.assertFalse(res.ok)
        self.assertTrue(any("centroid" in r for r in res.reasons))

    def test_wrong_area_fails(self):
        res = geometry.check_registration((11.5, 14.5), 40.0, **self.EXPECTED)
        self.assertFalse(res.ok)
        self.assertTrue(any("area" in r for r in res.reasons))

    def test_both_wrong_reports_both(self):
        res = geometry.check_registration((90.0, 90.0), 5.0, **self.EXPECTED)
        self.assertFalse(res.ok)
        self.assertEqual(len(res.reasons), 2)


if __name__ == "__main__":
    unittest.main()


class TestPhysicalUnits(unittest.TestCase):
    """Pitch resolved 2026-08-10: 31.55 mm / 128 = 246.48 um."""

    PITCH = 246.48

    def test_pitch_reproduces_the_measured_grid_width(self):
        self.assertAlmostEqual(
            geometry.electrodes_to_um(128, self.PITCH) / 1000.0, 31.55, places=2)

    def test_pitch_reproduces_the_measured_grid_area(self):
        mm2 = geometry.footprint_um2(128 * 128, self.PITCH) / 1e6
        self.assertAlmostEqual(mm2, 995.4025, delta=0.1)

    def test_cell_area(self):
        self.assertAlmostEqual(geometry.electrode_area_um2(self.PITCH),
                               60752.4, delta=1.0)

    def test_volume_is_none_without_a_gap(self):
        """The footprint follows from the pitch; the volume does not. Callers
        must handle None rather than have a number invented for them."""
        self.assertIsNone(geometry.droplet_volume_nl(1, self.PITCH, None))

    def test_volume_with_a_gap(self):
        # Returns None only when the gap is unknown; a gap is supplied here, so
        # not_none also pins that supplying one actually produces a figure.
        self.assertAlmostEqual(
            not_none(geometry.droplet_volume_nl(1, self.PITCH, 100.0)),
            6.075, delta=0.01)
        self.assertAlmostEqual(
            not_none(geometry.droplet_volume_nl(25, self.PITCH, 100.0)),
            151.9, delta=0.5)

    def test_bad_gap_rejected(self):
        with self.assertRaises(ValueError):
            geometry.droplet_volume_nl(1, self.PITCH, -5.0)


class TestPitchIsWiredIntoConfig(unittest.TestCase):

    def test_config_carries_the_resolved_pitch(self):
        from chiphealth.config import ChipConfig
        c = ChipConfig()
        # pitch_um is declared `float | None` because size_mm() branches on an
        # unknown pitch. It is not unknown -- resolved 2026-08-10 -- and this
        # asserts it is still wired in.
        self.assertAlmostEqual(not_none(c.pitch_um), 246.48, places=2)
        self.assertAlmostEqual(c.grid_width_mm, 31.55, places=2)

    def test_gap_remains_unknown(self):
        from chiphealth.config import ChipConfig
        self.assertIsNone(ChipConfig().gap_um)

    def test_config_pitch_matches_its_own_grid_measurement(self):
        """Guards against the two numbers drifting apart in edits."""
        from chiphealth.config import ChipConfig
        c = ChipConfig()
        self.assertAlmostEqual(not_none(c.pitch_um),
                               c.grid_width_mm * 1000.0 / c.cols, places=2)
