"""Chip actuation: the ``DLLTest.dll`` binding, the arming gate, and a fake rig.

Deduplicates the `Drop` struct, DLL load and `activate()` helper that are
re-pasted across all 13 legacy scripts (spec/objectives.md §0.1). Adapted from
cleanup.py:16-37 and chipsetup.py:27-53, minus the `input()` call between every
step.

Vendor mapping (workspace/analysis.md §2 established the real ABI by
disassembly -- it diverges from the vendor PDF):

    InitUSB()                                    -> init_usb
    OpenUSB()                                    -> open_usb
    CloseUSB()                                   -> close_usb
    SetPower(bool)                               -> set_power
    SetVolt(int x9)                              -> set_volt
    InquireVolt(int* x9)                         -> inquire_volt
    ActivateElec(rows, cols, count, Drop*)       -> activate_elec

There is no per-electrode readback in this API, and no read-back of electrode
state at all. Nothing in this module can tell you whether an electrode worked.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, Sequence

from .clearance import ClearanceViolation, require as require_clearance

log = logging.getLogger(__name__)

# Number of voltage rails SetVolt/InquireVolt take. chipsetup.py:47.
N_RAILS = 9

# The 7 exports the Python layer can reach.
REQUIRED_EXPORTS = ("InitUSB", "OpenUSB", "CloseUSB", "SetPower", "SetVolt",
                    "InquireVolt", "ActivateElec")


class ChipError(Exception):
    """Base for actuation failures."""


class ArmingError(ChipError):
    """A hardware-mutating call was attempted in dry-run."""


class AbiError(ChipError):
    """The loaded DLL does not look like the one this code was written against."""


class Drop(ctypes.Structure):
    """One rectangular group of electrodes.

    Field order is ``(height, width, row, col)`` -- verified by disassembly
    (workspace/analysis.md §2) and identical in every legacy script
    (cleanup.py:16-22, 1pixsplit.py:41-47, chipsetup.py:17-23). It is **not** the
    order the vendor PDF documents, which is why test_actuation pins it.
    """

    _fields_ = [
        ("height", ctypes.c_int),
        ("width", ctypes.c_int),
        ("row", ctypes.c_int),
        ("col", ctypes.c_int),
    ]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"Drop(h={self.height}, w={self.width}, "
                f"row={self.row}, col={self.col})")

    def covers(self) -> tuple[int, int, int, int]:
        return (self.row, self.row + self.height - 1,
                self.col, self.col + self.width - 1)


@dataclass(frozen=True)
class VoltageCheck:
    """Result of reading the rails back and comparing them to what was sent.

    ``InquireVolt`` is the only health signal this API offers at startup, and it
    is **global** -- 9 rails for the whole chip, not per-electrode. It confirms
    the supply and the USB link are working. It says nothing about any
    individual electrode.
    """

    ok: bool
    dry_run: bool
    rc: int
    commanded: tuple[int, ...]
    measured: tuple[int, ...]
    mismatches: tuple[tuple[int, int, int], ...] = ()  # (rail, commanded, measured)

    def summary(self) -> str:
        if self.dry_run:
            return ("DRY-RUN: SetVolt was never issued, so the rails read "
                    f"{list(self.measured)}. Nothing to verify -- pass --arm for "
                    "a real voltage check.")
        if self.ok:
            return (f"Rails match: commanded {list(self.commanded)}, "
                    f"measured {list(self.measured)} (rc={self.rc}).")
        lines = [f"VOLTAGE MISMATCH (rc={self.rc}):"]
        for rail, want, got in self.mismatches:
            lines.append(f"  rail {rail + 1}: commanded {want}V, reads {got}V")
        return "\n".join(lines)


class Backend(Protocol):
    """What the controller needs from a rig, real or fake."""

    def init_usb(self) -> int: ...
    def open_usb(self) -> int: ...
    def close_usb(self) -> int: ...
    def set_power(self, on: bool) -> int: ...
    def set_volt(self, volts: Sequence[int]) -> int: ...
    def inquire_volt(self) -> tuple[int, list[int]]: ...
    def activate_elec(self, rows: int, cols: int, drops: Sequence[Drop]) -> int: ...


# ── real hardware ────────────────────────────────────────────────────────────

class RealBackend:
    """ctypes binding to ``DLLTest.dll``. Windows only."""

    def __init__(self, dll_dir: str, dll_name: str = "DLLTest.dll") -> None:
        self.dll_path = os.path.join(dll_dir, dll_name)
        if hasattr(os, "add_dll_directory"):  # Windows
            os.add_dll_directory(dll_dir)
        self.lib = ctypes.CDLL(self.dll_path)
        self.check_abi()
        self._pin_signatures()

    def check_abi(self) -> list[str]:
        """Refuse to run against a DLL missing any export we call.

        Honest about its limits: a vendor update that *reverted* to the
        documented signatures while keeping the same names would be a
        stack-corrupting crash, not a Python exception, and no load-time probe
        can catch that. Symbol presence is what is checkable here; the field
        order and arity live in the contract tests instead (spec/design.md §5.1,
        ADR-0003).
        """
        missing = [name for name in REQUIRED_EXPORTS if not hasattr(self.lib, name)]
        if missing:
            raise AbiError(
                f"{self.dll_path} is missing {missing}. This is not the DLL this "
                f"binding was written against; refusing to continue."
            )
        return missing

    def _pin_signatures(self) -> None:
        """Set return types only. Deliberately NO argtypes.

        The legacy scripts (chipsetup.py, cleanup.py, 1pixsplit.py) declare no
        argtypes at all and demonstrably work against this DLL. Analysis §2
        established that its real ABI diverges from the vendor documentation,
        so the working scripts are the only reliable specification -- and
        pinning types they never pinned changes how ctypes marshals every call
        for no benefit. An earlier version pinned SetPower as c_bool, sending
        one byte where the working code sends four.

        Default marshalling: Python int -> C int, byref(c_int) -> int*, and a
        ctypes array -> pointer. Exactly what the legacy calls produce.
        """
        for name in REQUIRED_EXPORTS:
            getattr(self.lib, name).restype = ctypes.c_int

    def init_usb(self) -> int:
        return int(self.lib.InitUSB())

    def open_usb(self) -> int:
        return int(self.lib.OpenUSB())

    def close_usb(self) -> int:
        return int(self.lib.CloseUSB())

    def set_power(self, on: bool) -> int:
        return int(self.lib.SetPower(1 if on else 0))

    def set_volt(self, volts: Sequence[int]) -> int:
        if len(volts) != N_RAILS:
            raise ValueError(f"SetVolt takes {N_RAILS} rails, got {len(volts)}")
        return int(self.lib.SetVolt(*[int(v) for v in volts]))

    # Out-params are pre-filled with this so an untouched parameter is
    # distinguishable from a rail that genuinely reads zero. The legacy script
    # happens to pre-fill 1..9, which serves the same purpose by accident.
    UNWRITTEN = -31337

    def inquire_volt(self) -> tuple[int, list[int]]:
        cells = [ctypes.c_int(self.UNWRITTEN) for _ in range(N_RAILS)]
        rc = int(self.lib.InquireVolt(*[ctypes.byref(c) for c in cells]))
        raw = [c.value for c in cells]
        untouched = [i + 1 for i, v in enumerate(raw) if v == self.UNWRITTEN]
        if untouched:
            log.warning("InquireVolt did not write rails %s -- those readings "
                        "are absent, not zero.", untouched)
        return rc, [0 if v == self.UNWRITTEN else v for v in raw]

    def activate_elec(self, rows: int, cols: int, drops: Sequence[Drop]) -> int:
        n = len(drops)
        if n == 0:
            return int(self.lib.ActivateElec(rows, cols, 0, None))
        arr = (Drop * n)(*drops)
        return int(self.lib.ActivateElec(rows, cols, n, arr))


# ── fake rig ─────────────────────────────────────────────────────────────────

@dataclass
class FakeBackend:
    """In-memory rig, so the whole stack runs with no hardware and no Windows.

    Default backend on any non-Windows machine (spec/design.md §7, ADR-0001).

    ``dead`` injects known-bad electrodes. That matters more than it sounds:
    there is no ground-truth faulty region on the real chip yet
    (spec/objectives.md §1.4 q11), so injected faults are the only way to test
    the detector against a known answer before the chip provides one.
    """

    rows: int = 128
    cols: int = 128
    dead: set[tuple[int, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.dead is None:
            self.dead = set()
        self.powered = False
        self.volts: list[int] = [0] * N_RAILS
        self.opened = False
        self.frame: list[Drop] = []
        self.calls: list[tuple[str, object]] = []
        # A supply that does not reach what it was commanded. Set this to model
        # the 2026-08-10 fault -- commanded (45,45,45), rails read (16,15,0) --
        # which the default fake cannot represent, because SetVolt there simply
        # stores what it was given and InquireVolt hands it straight back.
        self.readback: list[int] | None = None

    def _record(self, name: str, payload: object = None) -> int:
        self.calls.append((name, payload))
        return 1

    def init_usb(self) -> int:
        return self._record("InitUSB")

    def open_usb(self) -> int:
        self.opened = True
        return self._record("OpenUSB")

    def close_usb(self) -> int:
        self.opened = False
        return self._record("CloseUSB")

    def set_power(self, on: bool) -> int:
        self.powered = bool(on)
        return self._record("SetPower", on)

    def set_volt(self, volts: Sequence[int]) -> int:
        if len(volts) != N_RAILS:
            raise ValueError(f"SetVolt takes {N_RAILS} rails, got {len(volts)}")
        self.volts = [int(v) for v in volts]
        return self._record("SetVolt", tuple(self.volts))

    def inquire_volt(self) -> tuple[int, list[int]]:
        self._record("InquireVolt")
        if self.readback is not None:
            return 1, list(self.readback)
        return 1, list(self.volts)

    def activate_elec(self, rows: int, cols: int, drops: Sequence[Drop]) -> int:
        self.frame = list(drops)
        return self._record("ActivateElec", [(d.height, d.width, d.row, d.col)
                                             for d in drops])

    # ── introspection for tests ──────────────────────────────────────────────

    def energised_cells(self) -> set[tuple[int, int]]:
        """Cells the current frame commands, minus the injected dead ones."""
        out: set[tuple[int, int]] = set()
        for d in self.frame:
            r0, r1, c0, c1 = d.covers()
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    if (r, c) not in self.dead:
                        out.add((r, c))
        return out


# ── controller ───────────────────────────────────────────────────────────────

class ChipController:
    """Stateful, safe wrapper: owns connection lifetime and the arming gate.

    Context manager, so the USB handle is released on **every** exit path
    including exceptions -- the leak flagged in spec/objectives.md §0.1.

    Dry-run is the default. Arming is one obvious step, because the gate must
    not obstruct real testing (researcher, spec/objectives.md §1.4):

        --arm   |   ChipController(..., armed=True)   |   ACXCHIP_ARM=1
    """

    def __init__(self, backend: Backend, rows: int, cols: int,
                 volts: Sequence[int], armed: bool = False,
                 step_delay_s: float = 0.5, sleep=time.sleep,
                 volt_tolerance: int = 2, volt_settle_s: float = 0.3,
                 power_settle_s: float = 0.0,
                 volt_poll_diagnostic: bool = False,
                 allow_violations: bool = False,
                 log_frames: bool = False) -> None:
        self.backend = backend
        self.rows = rows
        self.cols = cols
        self.volts = list(volts)
        self.armed = bool(armed)
        # Session-wide clearance override. False, and there is no config field
        # or environment variable that can flip it -- see clearance.require.
        # A caller may still override one single frame via activate(...).
        self.allow_violations = bool(allow_violations)
        self.step_delay_s = float(step_delay_s)
        self._sleep = sleep
        self.volt_tolerance = int(volt_tolerance)
        self.volt_settle_s = float(volt_settle_s)
        self.power_settle_s = float(power_settle_s)
        self.volt_poll_diagnostic = bool(volt_poll_diagnostic)

        #: Log every ActivateElec call with its exact struct fields and rc.
        #: For bring-up: it is the only way to see from Python what the DLL
        #: was actually handed.
        self.log_frames = bool(log_frames)

        self.frames_sent = 0
        self.frames_suppressed = 0
        #: Return code of the most recent ActivateElec, or None if none sent.
        self.last_activate_rc: int | None = None
        #: (call, rc) for every DLL call, in order. See :meth:`_record_rc`.
        self.rc_log: list[tuple[str, int]] = []
        self._open = False
        self.intended: list[list[tuple[int, int, int, int]]] = []
        # The startup rail reading, taken once in open(). Cached because
        # InquireVolt is a USB round-trip, not a getter -- see read_rails.
        self._rails: tuple[int, list[int]] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self) -> "ChipController":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def open(self) -> None:
        """Init, open, power on, set voltage, read the rails back ONCE.

        This sequence deliberately copies ``csvvolcont.py:148-176`` call for
        call, because that is the one legacy script that brings the supply up
        with no human in the loop and it reliably reaches 45/45/45:

            InitUSB() -> OpenUSB() -> SetPower(True) -> SetVolt(9 ints)
            -> sleep(0.3) -> InquireVolt(9 int*)      <- exactly one call

        The interactive scripts (chipsetup.py:26-55, cleanup.py, 1pixsplit.py,
        dropsplitoff.py, mdmixing.py) issue the identical calls in the identical
        order; they differ only in having an ``input()`` where the sleep is, so
        their timing is however long the operator took and is not copyable.

        **One InquireVolt, not fourteen.** Analysis §2 records that InquireVolt
        issues a ``libusb_bulk_transfer`` and parses an 18-byte 0xAA-framed
        response -- it is a USB round-trip, not a getter. Every working script
        calls it once. An earlier version of this method polled it every 0.25 s
        for up to 3 s and then read twice more, making up to 14 round-trips
        during power-up. That reading is now taken once here and cached; the
        polling survives as an opt-in diagnostic (``volt_poll_diagnostic``).

        The `input()` prompt after every call is gone -- that is the §0.1
        defect, not a feature. The prompt that was doing real work, the voltage
        confirmation, is phase 0b in the orchestrator.
        """
        self._record_rc("InitUSB", self.backend.init_usb())
        rc_open = self.backend.open_usb()
        self._record_rc("OpenUSB", rc_open)
        # The ONE call whose convention is evidenced: every legacy script
        # writes `if res:` here (chipsetup.py:29). Kept as the only
        # truthiness test in this class.
        if not rc_open:
            raise ChipError("OpenUSB failed -- is the device connected?")
        self._open = True
        if self.armed:
            # Return codes were previously discarded, so a refused SetPower
            # looked identical to a successful one.
            rc_power = self.backend.set_power(True)
            self._record_rc("SetPower", rc_power)
            log.info("SetPower(True) -> rc=%s", rc_power)
            # csvvolcont.py issues SetVolt immediately after SetPower. Defaults
            # to 0; configurable only so a genuinely slow supply can be given
            # room without editing code.
            if self.power_settle_s > 0:
                log.info("  waiting %.2fs before SetVolt (non-default)",
                         self.power_settle_s)
                self._sleep(self.power_settle_s)
            rc_volt = self.backend.set_volt(self.volts)
            self._record_rc("SetVolt", rc_volt)
            log.info("SetVolt%s -> rc=%s", tuple(self.volts), rc_volt)

            if self.volt_poll_diagnostic:
                self._poll_rails()
            elif self.volt_settle_s > 0:
                self._sleep(self.volt_settle_s)
        else:
            log.info("DRY-RUN: skipping SetPower(True) and SetVolt%s", tuple(self.volts))

        rc, rails = self.backend.inquire_volt()
        self._record_rc("InquireVolt", rc)
        self._rails = (rc, list(rails))
        log.info("InquireVolt rc=%s rails=%s (global rails, NOT per-electrode)", rc, rails)

    def _poll_rails(self) -> None:
        """Opt-in only: watch the rails ramp, one InquireVolt per 0.25 s.

        OFF by default, because it diverges from the proven sequence -- see
        :meth:`open`. Turn it on to diagnose a supply that is not reaching its
        commanded voltage: rising readings mean it needs longer, flat ones mean
        it has stopped short.

        Unlike the version this replaces, it does **not** stop early on two
        equal readings. Two consecutive zeros during ramp-up look exactly like
        two consecutive readings of a settled supply, so that early exit cut
        the wait to 0.5 s precisely in the case where more time was wanted.
        """
        polls = max(1, int(self.volt_settle_s / 0.25)) if self.volt_settle_s else 1
        log.info("  volt poll diagnostic ON -- %d extra InquireVolt calls", polls)
        for i in range(polls):
            self._sleep(0.25)
            rc, rails = self.backend.inquire_volt()
            log.info("  settle poll %d/%d: rc=%s rails=%s", i + 1, polls, rc, rails)

    def read_rails(self, refresh: bool = False) -> tuple[int, list[int]]:
        """The startup rail reading. Cached; does NOT hit the device again.

        InquireVolt is a USB round-trip (analysis §2), and the legacy scripts
        call it exactly once per power-up. :meth:`open` takes that one reading;
        everything downstream reads it from here.

        ``refresh=True`` forces a fresh round-trip. Use it when you genuinely
        want to know the rails *now* -- after the operator has been asked to
        check a connector, say -- not to re-confirm what open() already read.
        """
        if refresh or self._rails is None:
            rc, rails = self.backend.inquire_volt()
            self._rails = (rc, list(rails))
        return self._rails

    def verify_voltage(self, refresh: bool = False) -> VoltageCheck:
        """Compare the startup rail reading against what was commanded.

        Previously this readback was logged and nothing looked at it, so a chip
        that powered up with a dead rail would sweep all 899 moves and report
        every electrode as failing. Now it is checked, and the orchestrator gates
        the run on an operator confirming it (phase 0b).

        Uses the reading :meth:`open` already took rather than issuing another
        InquireVolt, so a startup makes exactly one -- matching every legacy
        script. Pass ``refresh=True`` to re-read after physically changing
        something.
        """
        rc, rails = self.read_rails(refresh=refresh)
        measured = tuple(int(v) for v in rails)
        commanded = tuple(int(v) for v in self.volts)

        if not self.armed:
            # SetVolt was never issued, so the readback means nothing. Saying
            # "mismatch" here would train the operator to click past a real one.
            return VoltageCheck(ok=True, dry_run=True, rc=rc,
                                commanded=commanded, measured=measured)

        mismatches = tuple(
            (i, want, got) for i, (want, got) in enumerate(zip(commanded, measured))
            if abs(want - got) > self.volt_tolerance
        )
        return VoltageCheck(ok=not mismatches, dry_run=False, rc=rc,
                            commanded=commanded, measured=measured,
                            mismatches=mismatches)

    def close(self) -> None:
        """De-energise and release, whatever happened upstream."""
        if not self._open:
            return
        try:
            if self.armed:
                self.deactivate_all()
                self.backend.set_power(False)
        finally:
            self.backend.close_usb()
            self._open = False

    # ── actuation ────────────────────────────────────────────────────────────

    def activate(self, drops: Sequence[Drop], settle: bool = True,
                 allow_violations: bool | None = None,
                 extra_settle_s: float = 0.0) -> int:
        """Send one electrode frame. The whole frame, every time.

        Wraps ``ActivateElec(rows, cols, count, Drop*)``. There is no
        per-electrode call and no read-back -- this sets the entire frame in one
        shot (workspace/analysis.md §2).

        In dry-run the intended frame is recorded and logged, not sent.

        **The clearance gate runs here, on every frame, armed or not.** This is
        the single choke point every drop on this chip passes through -- the
        chip-health resting frame, the registration hold, every sweep step, the
        fine-pass probe and every split-tree frame all arrive at this method --
        so gating it is what makes the guarantee "nowhere, not just the split
        tree" rather than a promise about the call sites that were remembered.
        Dry runs are gated too: a dry run exists to prove the plumbing, and a
        plan that cannot execute armed has not proved anything.

        Refusing an off-grid frame is not new; ``_validate`` did it. What is new
        is that the refusal names each short side and by how much, and that it
        can be overridden deliberately rather than only by editing geometry.
        ``allow_violations`` defaults to the session-wide value set at
        construction (itself False); pass it explicitly to override one frame.
        """
        self.intended.append([(d.height, d.width, d.row, d.col) for d in drops])
        for d in drops:
            self._validate(d)
        require_clearance(
            drops, self.rows, self.cols,
            what=f"frame {len(self.intended)} ({len(drops)} drop(s))",
            allow_violations=(self.allow_violations if allow_violations is None
                              else allow_violations),
        )

        if not self.armed:
            self.frames_suppressed += 1
            log.debug("DRY-RUN frame %d: %s", len(self.intended),
                      self.intended[-1])
            rc = 0
        else:
            rc = self.backend.activate_elec(self.rows, self.cols, drops)
            self.frames_sent += 1
            self.last_activate_rc = rc
            self._record_rc("ActivateElec", rc)
            if self.log_frames:
                log.info("ActivateElec(rows=%d, cols=%d, count=%d, %s) -> rc=%s",
                         self.rows, self.cols, len(drops),
                         [(d.height, d.width, d.row, d.col) for d in drops], rc)

        # `extra_settle_s` is an ADDEND, and the `> 0` guard is on the baseline
        # alone. A dry run sets step_delay_s to 0 precisely so a plumbing check
        # does not sit through the proven dwell (da70561); an extra that could
        # fire against a zero baseline would resurrect that. So an unarmed run
        # stays instant no matter what any stage asks for.
        if settle and self.step_delay_s > 0:
            self._sleep(self.step_delay_s + max(0.0, extra_settle_s))
        return rc

    def _record_rc(self, call: str, rc: int) -> None:
        """Record a DLL return code, and warn ONLY when it is unambiguous.

        WHAT THESE RETURN CODES ARE, as far as anything establishes it:

        `analysis.md` §17 says DLLTest.dll's error signalling is "return codes
        only" -- so they do mean something -- but no disassembly pinned the
        convention, and of the five calls the legacy scripts make, exactly ONE
        tests its result: ``OpenUSB``, as ``if res:`` (chipsetup.py:29,
        1pixsplit.py:126, cleanup.py:77). ``SetPower``, ``SetVolt``,
        ``InquireVolt`` and ``ActivateElec`` all assign ``res`` and never look
        at it. There is therefore NO evidence for truthy-means-success on any
        call except OpenUSB, and generalising OpenUSB's convention to the other
        four is an assumption, not a finding.

        The evidence actively contradicts it. On the instrument, 2026-08-13:

            SetPower(True)  -> rc=0     and the rails came up
            SetVolt(45,...) -> rc=0     and the rails read 46,46,46,0,0,0,0,0,0
            InquireVolt     -> rc=18
            ActivateElec    -> rc=0

        Two things fall out. InquireVolt is the calibration point: §2 records
        that it parses an **18-byte** 0xAA-framed USB response, and it returned
        exactly 18 -- so the return is a BYTE COUNT, not a boolean. Under that
        reading a write-only command with no response body returns 0 because it
        receives nothing, which is the normal, healthy value. And SetVolt is
        the control: it returned 0 while demonstrably working, since the rails
        came back in the exact commanded PATTERN (three high, six zero), which
        is not something a failed call or a leftover idle voltage produces.

        So ``rc == 0`` from a write command is not a failure signal, and this
        method no longer treats it as one. An earlier version warned that
        ActivateElec's rc=0 meant the call was "REFUSED"; that was wrong, and
        it was wrong in the expensive direction -- it pointed at software while
        the calls were in fact being accepted.

        A NEGATIVE rc is still worth flagging: libusb error codes are negative
        and a byte count cannot be, so under either reading negative is bad.
        """
        self.rc_log.append((call, rc))
        if rc < 0:
            log.warning(
                "%s returned rc=%s. Negative is an error under both readings "
                "of this API's return codes -- a libusb error code, or an "
                "impossible byte count. This one is worth chasing.", call, rc)

    def rc_summary(self) -> str:
        """Every DLL return code this session, with the caveat attached."""
        if not self.rc_log:
            return "No DLL calls recorded."
        out = ["DLL return codes this session:"]
        out += [f"  {call:<14} rc={rc}" for call, rc in self.rc_log]
        out.append(
            "Only OpenUSB's convention is evidenced (truthy = success, "
            "chipsetup.py:29). InquireVolt returning 18 matches the 18-byte "
            "response analysis.md §2 disassembled, which suggests these are "
            "BYTE COUNTS -- making rc=0 the normal value for a write-only "
            "command, not a failure. Negative would be unambiguous; 0 is not.")
        return "\n".join(out)

    def require_armed(self, what: str) -> None:
        if not self.armed:
            raise ArmingError(
                f"{what} needs an armed session. Dry-run is the default; "
                f"pass --arm or set ACXCHIP_ARM=1."
            )

    def deactivate_all(self) -> int:
        """Clear every electrode. cleanup.py:157."""
        return self.backend.activate_elec(self.rows, self.cols, [])

    def _validate(self, d: Drop) -> None:
        """Per-drop sanity that is NOT about clearance.

        A non-positive extent is a malformed drop, not a misplaced one: there
        is no side to be short on and no margin that would fix it, so it is
        never covered by ``allow_violations``. Bounds moved out to
        ``clearance.require``, which measures the whole frame at once and can
        say which side is short and by how much.
        """
        if d.height <= 0 or d.width <= 0:
            raise ValueError(f"{d!r} has non-positive extent")


def make_backend(kind: str, dll_dir: str, dll_name: str,
                 rows: int, cols: int) -> Backend:
    """Pick a backend. ``auto`` uses the real DLL only where it can load."""
    if kind == "fake":
        return FakeBackend(rows=rows, cols=cols)
    if kind == "real":
        return RealBackend(dll_dir, dll_name)
    if kind != "auto":
        raise ValueError(f"unknown backend {kind!r}")

    if os.name != "nt":
        log.info("Not Windows -- using FakeBackend. The vendor DLLs are "
                 "Windows x64 PE binaries and cannot load here.")
        return FakeBackend(rows=rows, cols=cols)
    try:
        return RealBackend(dll_dir, dll_name)
    except (OSError, AbiError) as exc:
        log.warning("Could not load %s (%s) -- falling back to FakeBackend.",
                    dll_name, exc)
        return FakeBackend(rows=rows, cols=cols)
