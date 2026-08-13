"""Actuation, arming gate, and the ABI contract.

The contract tests pin the facts recovered by disassembly in
workspace/analysis.md §2, so a vendor DLL swap fails loudly here rather than
corrupting the stack at runtime (spec/design.md §7).
"""

import ctypes
import unittest

from chiphealth.actuation import (ArmingError, ChipController, ChipError, Drop,
                                  FakeBackend, N_RAILS, REQUIRED_EXPORTS,
                                  RealBackend, make_backend)

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
        be.readback = [45, 0, 45, 0, 0, 0, 0, 0, 0]  # rail 2 dropped out
        chip = controller(armed=True, backend=be)
        chip.open()
        check = chip.verify_voltage()
        self.assertFalse(check.ok)
        self.assertEqual(len(check.mismatches), 1)
        rail, want, got = check.mismatches[0]
        self.assertEqual((rail, want, got), (1, 45, 0))
        self.assertIn("rail 2", check.summary())
        self.assertIn("VOLTAGE MISMATCH", check.summary())

    def test_the_2026_08_10_fault_is_caught_at_startup(self):
        """The real one: commanded 45/45/45, rails read 16/15/0."""
        be = FakeBackend(rows=ROWS, cols=COLS)
        be.readback = [16, 15, 0, 0, 0, 0, 0, 0, 0]
        chip = controller(armed=True, backend=be)
        chip.open()
        check = chip.verify_voltage()
        self.assertFalse(check.ok)
        self.assertEqual([m[0] for m in check.mismatches], [0, 1, 2])
        self.assertIn("rail 3: commanded 45V, reads 0V", check.summary())

    def test_small_drift_is_tolerated(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        be.readback = [44, 46, 45, 0, 0, 0, 0, 0, 0]
        chip = controller(armed=True, backend=be)
        chip.open()
        self.assertTrue(chip.verify_voltage().ok)

    def test_tolerance_is_configurable(self):
        be = FakeBackend(rows=ROWS, cols=COLS)
        be.readback = [44, 45, 45, 0, 0, 0, 0, 0, 0]
        chip = ChipController(be, ROWS, COLS, VOLTS, armed=True, step_delay_s=0.0,
                              sleep=lambda _s: None, volt_tolerance=0)
        chip.open()
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

    def test_a_refresh_re_reads_the_device(self):
        """The cache is for not spamming the bus, not for hiding a change the
        operator just made to a connector."""
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be)
        chip.open()
        self.assertTrue(chip.verify_voltage().ok)
        be.readback = [45, 45, 0, 0, 0, 0, 0, 0, 0]   # rail 3 drops out
        self.assertTrue(chip.verify_voltage().ok, "cached read should not change")
        check = chip.verify_voltage(refresh=True)
        self.assertFalse(check.ok)
        self.assertIn("rail 3", check.summary())


class TestVoltageSequenceMatchesLegacy(unittest.TestCase):
    """The startup sequence must match the proven legacy scripts call for call.

    csvvolcont.py:148-176 is the reference: it is the only legacy script that
    brings the supply up with no human in the loop, and it reaches 45/45/45.
    The interactive scripts issue the identical calls in the identical order.
    """

    def calls(self, **kw):
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be, **kw)
        chip.open()
        chip.verify_voltage()
        return be, [n for n, _ in be.calls]

    def test_call_order_matches_csvvolcont(self):
        _, names = self.calls()
        self.assertEqual(names, ["InitUSB", "OpenUSB", "SetPower", "SetVolt",
                                 "InquireVolt"])

    def test_inquire_volt_is_called_exactly_once(self):
        """Every legacy script calls it once. It is a libusb bulk transfer
        (analysis §2), not a getter -- an earlier version made up to 14 USB
        round-trips during power-up."""
        _, names = self.calls()
        self.assertEqual(names.count("InquireVolt"), 1)

    def test_all_nine_rails_reach_the_backend_in_order(self):
        be, _ = self.calls()
        volts = next(a for n, a in be.calls if n == "SetVolt")
        self.assertEqual(list(volts), [45, 45, 45, 0, 0, 0, 0, 0, 0])

    def test_rail_three_is_commanded_like_the_other_two(self):
        """No legacy script treats rail 3 specially; neither may we.

        chipsetup.py:41, 1pixsplit.py:134, dropsplitoff.py:43, mdmixing.py:192
        and mdmixwithmerge.py:307 all pass a literal 45 in the third position;
        cleanup.py:66 sets VOLT_3 = 45. Rail 3 is the one that read 0 V on every
        armed attempt on 2026-08-10, so this pins that we command it normally.
        """
        be, _ = self.calls()
        volts = next(a for n, a in be.calls if n == "SetVolt")
        self.assertEqual(volts[0], volts[1])
        self.assertEqual(volts[1], volts[2])


class TestRealBackendMarshalling(unittest.TestCase):
    """How RealBackend actually calls the DLL, with a stub in place of it.

    The fake backend cannot catch a marshalling regression -- it receives a
    Python sequence, not whatever ctypes would put on the stack. These build a
    RealBackend without loading a DLL and inspect the arguments it constructs.
    """

    class StubLib:
        def __init__(self):
            self.calls = []

            def rec(name):
                def f(*a):
                    self.calls.append((name, a))
                    return 1
                f.restype = None
                return f
            for n in REQUIRED_EXPORTS:
                setattr(self, n, rec(n))

    def backend(self):
        rb = RealBackend.__new__(RealBackend)        # skip the CDLL load
        rb.dll_path = "<stub>"
        rb.lib = self.StubLib()
        return rb

    def test_set_volt_sends_nine_plain_python_ints(self):
        """chipsetup.py:41 passes 45,45,45,0,0,0,0,0,0 positionally with no
        argtypes declared, so ctypes marshals each as a C int. Wrapping them in
        anything else, or passing an array, changes what lands on the stack."""
        rb = self.backend()
        rb.set_volt((45, 45, 45, 0, 0, 0, 0, 0, 0))
        name, args = rb.lib.calls[-1]
        self.assertEqual(name, "SetVolt")
        self.assertEqual(len(args), 9, "SetVolt takes 9 separate args, not an array")
        self.assertEqual(list(args), [45, 45, 45, 0, 0, 0, 0, 0, 0])
        for v in args:
            self.assertIs(type(v), int, f"expected a plain int, got {type(v)}")

    def test_set_power_sends_an_int_not_a_c_bool(self):
        """An earlier version pinned SetPower as c_bool, sending one byte where
        the working scripts send four. chipsetup.py:37 passes True with no
        argtypes, which ctypes marshals as a 4-byte C int."""
        rb = self.backend()
        rb.set_power(True)
        _, args = rb.lib.calls[-1]
        self.assertEqual(len(args), 1)
        self.assertIs(type(args[0]), int)
        self.assertEqual(args[0], 1)

    def test_inquire_volt_sends_nine_separate_pointers(self):
        """analysis §2: 9 output int* pointers, matching the legacy
        InquireVolt(byref(v1)...byref(v9)) -- not one pointer to an array."""
        rb = self.backend()
        rb.inquire_volt()
        name, args = rb.lib.calls[-1]
        self.assertEqual(name, "InquireVolt")
        self.assertEqual(len(args), 9)
        # byref() yields a CArgObject, not a _Pointer. What matters is that
        # there are nine of them and none is a bare int.
        for a in args:
            self.assertNotIsInstance(a, int)
            self.assertEqual(type(a).__name__, "CArgObject")


class TestVoltageDiagnosticFlag(unittest.TestCase):

    def names(self, **kw):
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = controller(armed=True, backend=be, **kw)
        chip.open()
        chip.verify_voltage()
        return be, [n for n, _ in be.calls]

    def test_off_by_default(self):
        _, names = self.names()
        self.assertEqual(names.count("InquireVolt"), 1)

    def test_can_be_turned_on(self):
        _, names = self.names(volt_poll_diagnostic=True)
        self.assertGreater(names.count("InquireVolt"), 1,
                           "diagnostic mode should poll")

    def test_no_delay_between_set_power_and_set_volt_by_default(self):
        slept = []
        be = FakeBackend(rows=ROWS, cols=COLS)
        chip = ChipController(be, ROWS, COLS, VOLTS, armed=True, step_delay_s=0.0,
                              sleep=slept.append)
        chip.open()
        # csvvolcont issues them back to back; the only sleep is the post-SetVolt
        # settle, and it is 0.3s.
        self.assertEqual(slept, [0.3])

    def test_the_poll_does_not_stop_early_on_equal_readings(self):
        """Two consecutive zeros during ramp-up look exactly like two readings
        of a settled supply. The old early-exit cut the wait to 0.5s precisely
        when more time was wanted."""
        be = FakeBackend(rows=ROWS, cols=COLS)
        be.readback = [0] * 9                  # never ramps
        chip = ChipController(be, ROWS, COLS, VOLTS, armed=True, step_delay_s=0.0,
                              sleep=lambda _s: None, volt_settle_s=1.0,
                              volt_poll_diagnostic=True)
        chip.open()
        polls = [n for n, _ in be.calls].count("InquireVolt")
        self.assertEqual(polls, int(1.0 / 0.25) + 1)   # 4 polls + the one read


class TestActivateReturnCode(unittest.TestCase):
    """ActivateElec's rc is the only status the hardware gives for an
    actuation. It was computed and discarded until 2026-08-13, which made "the
    DLL refused the call" and "the DLL accepted it and nothing moved"
    indistinguishable from Python -- the two halves of a bring-up problem."""

    class Refusing(FakeBackend):
        def activate_elec(self, rows, cols, drops):
            super().activate_elec(rows, cols, drops)
            return 0                      # OpenUSB's convention: falsy = failed

    class Erroring(FakeBackend):
        def activate_elec(self, rows, cols, drops):
            super().activate_elec(rows, cols, drops)
            return -1                     # libusb error codes are negative

    def test_zero_is_NOT_treated_as_a_failure(self):
        """The correction of 2026-08-13.

        An earlier version warned that rc=0 meant the call was REFUSED,
        generalising OpenUSB's `if res:` to a call no legacy script tests. On
        the instrument SetVolt also returned 0 while demonstrably working (the
        rails came back in the exact commanded pattern), and InquireVolt
        returned 18 -- the byte count of the response analysis.md §2
        disassembled. So 0 is the normal value for a write-only command, and
        warning about it pointed at software while the calls were being
        accepted.
        """
        chip = controller(armed=True, backend=self.Refusing(rows=ROWS, cols=COLS))
        chip.open()
        with self.assertNoLogs("chiphealth.actuation", level="WARNING"):
            chip.activate([Drop(20, 20, 55, 55)])

    def test_a_negative_return_is_warned_about(self):
        """Unambiguous under either reading: libusb errors are negative, and a
        byte count cannot be."""
        chip = controller(armed=True, backend=self.Erroring(rows=ROWS, cols=COLS))
        chip.open()
        with self.assertLogs("chiphealth.actuation", level="WARNING") as log:
            chip.activate([Drop(20, 20, 55, 55)])
        self.assertIn("rc=-1", "\n".join(log.output))

    def test_every_dll_call_is_recorded_not_just_activate(self):
        """The inconsistency that made the first warning misleading: SetPower
        and SetVolt returned 0 too, and nothing said so."""
        chip = controller(armed=True)
        chip.open()
        chip.activate([Drop(20, 20, 55, 55)])
        calls = [c for c, _ in chip.rc_log]
        for expected in ("InitUSB", "OpenUSB", "SetPower", "SetVolt",
                         "InquireVolt", "ActivateElec"):
            self.assertIn(expected, calls)

    def test_the_summary_states_the_convention_is_unpinned(self):
        chip = controller(armed=True)
        chip.open()
        text = chip.rc_summary()
        self.assertIn("Only OpenUSB's convention is evidenced", text)
        self.assertIn("BYTE COUNTS", text)

    def test_a_truthy_return_is_not_warned_about(self):
        chip = controller(armed=True)
        chip.open()
        with self.assertNoLogs("chiphealth.actuation", level="WARNING"):
            chip.activate([Drop(20, 20, 55, 55)])

    def test_the_last_return_code_is_kept(self):
        chip = controller(armed=True)
        chip.open()
        self.assertIsNone(chip.last_activate_rc)
        chip.activate([Drop(20, 20, 55, 55)])
        self.assertEqual(chip.last_activate_rc, 1)

    def test_log_frames_shows_the_exact_struct_fields(self):
        """Bring-up needs to see what the DLL was handed, in its field order."""
        chip = controller(armed=True, backend=FakeBackend(rows=ROWS, cols=COLS),
                          log_frames=True)
        chip.open()
        with self.assertLogs("chiphealth.actuation", level="INFO") as log:
            chip.activate([Drop(20, 20, 55, 55)])
        self.assertIn("(20, 20, 55, 55)", "\n".join(log.output))

    def test_frames_are_not_logged_by_default(self):
        chip = controller(armed=True)
        self.assertFalse(chip.log_frames)


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
