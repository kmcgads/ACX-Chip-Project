"""Actuation, arming gate, and the ABI contract.

The contract tests pin the facts recovered by disassembly in
workspace/analysis.md §2, so a vendor DLL swap fails loudly here rather than
corrupting the stack at runtime (spec/design.md §7).
"""

import ctypes
import unittest

from chiphealth.actuation import (ArmingError, ChipController, ChipError, Drop,
                                  FakeBackend, N_RAILS, REQUIRED_EXPORTS,
                                  make_backend)

ROWS = COLS = 128
VOLTS = (45, 45, 45, 0, 0, 0, 0, 0, 0)


def controller(armed=False, backend=None, **kw):
    be = backend or FakeBackend(rows=ROWS, cols=COLS)
    return ChipController(be, ROWS, COLS, VOLTS, armed=armed,
                          step_delay_s=0.0, sleep=lambda _s: None, **kw)


class TestAbiContract(unittest.TestCase):
    """These assertions are the analysis §2 hazard turned into a tripwire."""

    def test_drop_is_four_ints_sixteen_bytes(self):
        self.assertEqual(ctypes.sizeof(Drop), 16)

    def test_drop_field_order(self):
        """(height, width, row, col) -- NOT the order the vendor PDF documents."""
        self.assertEqual([n for n, _ in Drop._fields_],
                         ["height", "width", "row", "col"])

    def test_drop_field_types(self):
        for _, t in Drop._fields_:
            self.assertIs(t, ctypes.c_int)

    def test_drop_positional_construction_matches_legacy_scripts(self):
        """cleanup.py and 1pixsplit.py both build Drop(h, w, row, col)."""
        d = Drop(20, 20, 2, 5)
        self.assertEqual((d.height, d.width, d.row, d.col), (20, 20, 2, 5))
        self.assertEqual(d.covers(), (2, 21, 5, 24))

    def test_nine_rails(self):
        self.assertEqual(N_RAILS, 9)
        self.assertEqual(len(VOLTS), N_RAILS)

    def test_required_exports_are_the_seven_python_can_reach(self):
        self.assertEqual(len(REQUIRED_EXPORTS), 7)
        self.assertIn("ActivateElec", REQUIRED_EXPORTS)
        self.assertIn("InquireVolt", REQUIRED_EXPORTS)


class TestFakeBackend(unittest.TestCase):

    def test_defaults_to_fake_off_windows(self):
        be = make_backend("auto", "/nonexistent", "DLLTest.dll", ROWS, COLS)
        self.assertIsInstance(be, FakeBackend)

    def test_explicit_fake(self):
        self.assertIsInstance(make_backend("fake", "", "", ROWS, COLS), FakeBackend)

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            make_backend("banana", "", "", ROWS, COLS)

    def test_set_volt_arity_enforced(self):
        be = FakeBackend()
        with self.assertRaises(ValueError):
            be.set_volt([45, 45, 45])

    def test_injected_dead_electrodes_are_excluded(self):
        be = FakeBackend(rows=ROWS, cols=COLS, dead={(5, 5), (5, 6)})
        be.activate_elec(ROWS, COLS, [Drop(4, 4, 4, 4)])
        cells = be.energised_cells()
        self.assertEqual(len(cells), 16 - 2)
        self.assertNotIn((5, 5), cells)
        self.assertIn((4, 4), cells)

    def test_records_calls(self):
        be = FakeBackend()
        be.init_usb()
        be.open_usb()
        self.assertEqual([n for n, _ in be.calls], ["InitUSB", "OpenUSB"])


class TestArmingGate(unittest.TestCase):

    def test_dry_run_is_the_default(self):
        self.assertFalse(controller().armed)

    def test_dry_run_records_frames_but_sends_nothing(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        with controller(armed=False, backend=be) as chip:
            chip.activate([Drop(20, 20, 2, 5)])
            chip.activate([Drop(20, 20, 2, 6)])
        self.assertEqual(chip.frames_sent, 0)
        self.assertEqual(chip.frames_suppressed, 2)
        self.assertEqual(len(chip.intended), 2)
        self.assertNotIn("ActivateElec", [n for n, _ in be.calls])

    def test_dry_run_does_not_power_on(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        with controller(armed=False, backend=be):
            pass
        self.assertFalse(be.powered)
        self.assertNotIn("SetPower", [n for n, _ in be.calls])

    def test_armed_sends_and_powers(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        with controller(armed=True, backend=be) as chip:
            chip.activate([Drop(20, 20, 2, 5)])
            self.assertTrue(be.powered)
            self.assertEqual(be.volts, list(VOLTS))
        self.assertEqual(chip.frames_sent, 1)

    def test_require_armed_raises_with_actionable_message(self):
        chip = controller(armed=False)
        with self.assertRaises(ArmingError) as ctx:
            chip.require_armed("splitting a probe droplet")
        msg = str(ctx.exception)
        self.assertIn("--arm", msg)
        self.assertIn("ACXCHIP_ARM=1", msg)

    def test_require_armed_passes_when_armed(self):
        controller(armed=True).require_armed("anything")  # must not raise


class TestLifecycle(unittest.TestCase):

    def test_closes_on_normal_exit(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        with controller(armed=True, backend=be):
            pass
        self.assertFalse(be.opened)
        self.assertFalse(be.powered)

    def test_closes_on_exception(self):
        """The handle leak flagged in objectives.md §0.1 must not come back."""
        be = FakeBackend(rows=ROWS, cols=COLS)
        with self.assertRaises(RuntimeError):
            with controller(armed=True, backend=be):
                raise RuntimeError("boom")
        self.assertFalse(be.opened)
        self.assertFalse(be.powered)

    def test_deactivates_all_before_power_off(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        with controller(armed=True, backend=be) as chip:
            chip.activate([Drop(20, 20, 2, 5)])
        names = [n for n, _ in be.calls]
        self.assertLess(names.index("ActivateElec"), len(names) - 1)
        self.assertEqual(be.frame, [])  # cleared
        self.assertEqual(names[-1], "CloseUSB")

    def test_open_failure_is_loud(self):
        class Refusing(FakeBackend):
            def open_usb(self):
                return 0
        with self.assertRaises(ChipError):
            controller(armed=True, backend=Refusing()).open()

    def test_close_is_idempotent(self):
        chip = controller(armed=True)
        chip.close()  # never opened
        chip.open()
        chip.close()
        chip.close()


class TestVoltageVerification(unittest.TestCase):
    """InquireVolt used to be logged and never looked at."""

    def test_armed_and_matching_passes(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be)
        chip.open()
        check = chip.verify_voltage()
        self.assertTrue(check.ok)
        self.assertFalse(check.dry_run)
        self.assertEqual(check.mismatches, ())
        self.assertEqual(check.measured, VOLTS)
        self.assertIn("Rails match", check.summary())

    def test_dead_rail_is_caught_and_named(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be)
        chip.open()
        be.volts = [45, 0, 45, 0, 0, 0, 0, 0, 0]  # rail 2 dropped out
        check = chip.verify_voltage()
        self.assertFalse(check.ok)
        self.assertEqual(len(check.mismatches), 1)
        rail, want, got = check.mismatches[0]
        self.assertEqual((rail, want, got), (1, 45, 0))
        self.assertIn("rail 2", check.summary())
        self.assertIn("VOLTAGE MISMATCH", check.summary())

    def test_small_drift_is_tolerated(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be)
        chip.open()
        be.volts = [44, 46, 45, 0, 0, 0, 0, 0, 0]
        self.assertTrue(chip.verify_voltage().ok)

    def test_tolerance_is_configurable(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = ChipController(be, ROWS, COLS, VOLTS, armed=True, step_delay_s=0.0,
                              sleep=lambda _s: None, volt_tolerance=0)
        chip.open()
        be.volts = [44, 45, 45, 0, 0, 0, 0, 0, 0]
        self.assertFalse(chip.verify_voltage().ok)

    def test_dry_run_is_not_reported_as_a_mismatch(self):
        """SetVolt is skipped in dry-run, so the rails read zero. Calling that a
        fault would train the operator to click past a real one."""
        chip = controller(armed=False)
        chip.open()
        check = chip.verify_voltage()
        self.assertTrue(check.dry_run)
        self.assertTrue(check.ok)
        self.assertIn("DRY-RUN", check.summary())
        self.assertIn("--arm", check.summary())


class TestValidation(unittest.TestCase):

    def test_rejects_off_chip_drop(self):
        chip = controller(armed=True)
        with self.assertRaises(ValueError):
            chip.activate([Drop(20, 20, 120, 5)])

    def test_rejects_zero_extent(self):
        chip = controller(armed=True)
        with self.assertRaises(ValueError):
            chip.activate([Drop(0, 5, 10, 10)])

    def test_rejects_negative_origin(self):
        chip = controller(armed=True)
        with self.assertRaises(ValueError):
            chip.activate([Drop(5, 5, 0, 10)])

    def test_accepts_the_full_chip(self):
        """cleanup.py:109 activates all 128x128 at once, routinely."""
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be)
        chip.open()
        chip.activate([Drop(128, 128, 1, 1)])
        self.assertEqual(len(be.energised_cells()), 128 * 128)
        chip.close()

    def test_accepts_the_starting_droplet(self):
        chip = controller(armed=True)
        chip.open()
        chip.activate([Drop(20, 20, 2, 5)])
        chip.close()


if __name__ == "__main__":
    unittest.main()
