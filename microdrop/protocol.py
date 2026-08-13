"""Running a split with a human in the loop and no camera.

DELIBERATELY CAMERA-FREE (researcher, this session). Nothing here imports cv2,
numpy, `chiphealth.geometry`, `chiphealth.detector` or `chiphealth.calibration`,
and nothing reads `calibration.json`. Positions are electrode indices commanded
straight through `ActivateElec`; verification is a person looking down the
microscope and answering a question. `test_protocol` pins the absence of the
vision stack by hard-blocking those imports and running the whole protocol
anyway, so this cannot regress by someone adding a convenient import.

WHY IT CAN BE CAMERA-FREE AT ALL
================================
Because the split pipeline never needed to observe anything. A homography maps
CAMERA PIXELS to electrode indices; it exists so the detector can say where a
blob it photographed sits on the chip. Commanding is the other direction and
does not use it -- `ActivateElec(rows, cols, count, Drop*)` takes electrode
indices, and `splitplan` emits electrode indices. So the calibration was never
load-bearing for placement, only for measurement.

WHAT THE OPERATOR IS REPLACING
==============================
Everything the camera would have checked, which is more than it sounds:

    the droplet is present at all     `detector` primary blob
    it fills the commanded region     registration area/centroid check
    it survived transport             drag / residue / no_movement
    the split produced N pieces       blob count
    the pieces are equal              blob area, in electrode units

Only the first four are things a person at the eyepiece can genuinely judge.
The fifth is not: `volume_equality` predicts every leaf activates the same
number of electrodes, and confirming that by eye is not a measurement. See
NOT VERIFIED below, and do not let a run of confident "y" answers read later as
though the volume claim was checked.

NOT VERIFIED WITHOUT A CAMERA
=============================
Recorded here because the honest limits of a run belong with the code that
performs it, not only in a conversation:

1. THE VOLUME PROXY IS UNCHECKED, AND STAYS UNCHECKABLE. `volume_equality`
   asserts equal activated electrode area, which is exact but is a property of
   the PLAN. Confirming it against liquid needs `blob.area_electrodes` from the
   detector -- the same units, no conversion, which is what made the proxy
   attractive -- and that needs a camera. All four of
   `VOLUME_EQUALITY_ASSUMPTIONS` therefore stay untested, including the
   load-bearing uniform-gap one.

2. THERE IS NO ELECTRODE READBACK TO FALL BACK ON. This is not a gap the
   camera was covering for; the API has none at all. `actuation` says it
   outright: "There is no per-electrode readback in this API [...] Nothing in
   this module can tell you whether an electrode worked." `InquireVolt` reads
   nine global rails, not electrodes. So with no camera the ONLY evidence any
   of this worked is the operator's eye.

3. TRANSPORT LOSS IS INVISIBLE. Liquid shed along an approach walk, or residue
   left on the path, is exactly the signature `detector` exists to find. A
   droplet arriving smaller than its commanded footprint silently breaks the
   tree's premise -- an even split needs a full 20x20 -- and the split will
   still run and still report equal FOOTPRINTS. This is the strongest argument
   for loading at the split position rather than walking to it.

4. A REPEAT OF 2026-08-10 WOULD NOT ANNOUNCE ITSELF. That break-up happened
   during transport. Open-loop, the first sign would be the wrong number of
   pieces at the end.

The mitigation is the confirmation gates below: they are checkpoints, and the
answers are recorded in `SplitSession.log` so a run that went wrong has some
record of where the operator last saw it going right.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence

from chiphealth.actuation import ChipController, Drop, make_backend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import DEFAULT_DLL_DIR, DEFAULT_DLL_NAME, ChipConfig

from . import params as P
from . import splitplan as SP


def console_confirm(question: str, detail: str = "") -> bool:  # pragma: no cover
    """Default gate: a real y/n at the terminal. Anything else re-asks."""
    if detail:
        print(f"\n{detail}")
    while True:
        answer = input(f"\n>>> {question} [y/n] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("    please answer y or n")


def console_announce(message: str) -> None:  # pragma: no cover
    print(message)


class OperatorAbort(RuntimeError):
    """The operator answered no at a gate. Not an error -- a decision.

    Distinct from `ClearanceViolation`, which is geometry refusing, and from
    `ChipError`, which is hardware failing. Callers that want to tell "the
    person stopped it" from "it broke" need these to be different types.
    """


@dataclass
class SplitSession:
    """One camera-free split run, gated by a person at each checkpoint.

    `confirm` and `announce` are injected so this is testable with no terminal
    and no rig -- the same reason `ChipController` takes a backend.
    """

    chip: ChipController
    root: SP.DropNode | None = None
    axes: Sequence[SP.Axis] = SP.DEFAULT_AXES
    sp: P.SplitParams = P.DEFAULT
    cfg: ChipConfig | None = None
    #: Where the droplet already is, if it must be walked in. None = it is
    #: loaded at the split position directly, which is the intended protocol.
    approach_from: SP.DropNode | None = None
    confirm: Callable[..., bool] = console_confirm
    announce: Callable[[str], None] = console_announce
    allow_violations: bool = False

    #: (question, answer) for every gate, in order. The only artifact a
    #: camera-free run produces, so it is not optional.
    log: list[tuple[str, str]] = field(default_factory=list)

    #: Anything that makes this run less trustworthy than it looks -- an
    #: auto-confirm, a clearance override. Printed in `report`, because the
    #: report is the only thing that outlives the terminal.
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cfg = self.cfg or ChipConfig()
        self.root = self.root or SP.split_root()
        self.plan = SP.plan_tree(self.root, self.axes, self.sp, cfg=self.cfg)
        self.approach = (
            SP.plan_approach(self.approach_from, self.root.row, self.root.col)
            if self.approach_from is not None else None
        )

    # ── gates ────────────────────────────────────────────────────────────────

    def _gate(self, question: str, detail: str = "") -> None:
        ok = bool(self.confirm(question, detail))
        self.log.append((question, "yes" if ok else "no"))
        if not ok:
            raise OperatorAbort(question)

    def _preflight(self) -> None:
        """Clearance first, before the operator is asked for anything.

        Same gate the rest of the repo uses -- pure arithmetic, no camera, no
        rig -- so a geometry that cannot run costs a message rather than a
        loaded chip.
        """
        if self.approach is not None:
            SP.require_approach_clearance(self.approach, self.cfg,
                                          self.allow_violations)
        SP.require_clearance(self.plan, self.cfg, self.allow_violations)

    # ── phases ───────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Energise the target, then ask for liquid. In that order.

        Holding a droplet at a position needs 45 V on this hardware, so a
        prompt with nothing energised asks the operator to place liquid into a
        region that cannot hold it. Every legacy script does it this way, and
        `run_health.phase1_load` does it for the same reason.

        This is also what makes the load position a non-issue: the field does
        the positioning, so loading at row 55 is no harder than at row 5.
        """
        r = self.approach_from or self.root
        if self.chip.armed:
            self.chip.activate([Drop(r.height, r.width, r.row, r.col)],
                               settle=False)
        else:
            self.announce("DRY-RUN: nothing is energised, so liquid will not "
                          "stay. Expected for a dry run.")
        self._gate(
            f"Is a {r.height}x{r.width} droplet loaded at row {r.row}, "
            f"col {r.col} and holding?",
            f"LOAD:\n"
            f"  1. Silicon oil filler.\n"
            f"  2. Test substance on top.\n"
            f"  3. Form a {r.height}x{r.width} droplet at row {r.row}, "
            f"col {r.col} -- that region is energised and holding.\n"
            f"  Check down the microscope that it FILLS the rectangle. A "
            f"droplet smaller than {r.height}x{r.width} cannot be halved "
            f"evenly and nothing downstream will notice.")

    def walk(self) -> None:
        """Run the approach, if there is one, and have its arrival confirmed.

        The confirmation is the whole reason this is a separate phase. Liquid
        lost over 95 electrodes of travel is invisible to everything else in
        this pipeline (see NOT VERIFIED §3), so a person has to look before the
        tree commits to a geometry that assumes a full footprint.
        """
        if self.approach is None:
            return
        a = self.approach
        self.announce(f"APPROACH {a.from_rc} -> {a.to_rc}: {a.electrodes} "
                      f"electrodes, {a.n_frames} frames, {a.duration_s():.0f}s")
        for f in a.frames:
            self.chip.activate(list(f.drops),
                               allow_violations=self.allow_violations)
        self._gate(
            f"Did the droplet arrive at row {a.to_rc[0]}, col {a.to_rc[1]} "
            f"still {a.height}x{a.width}, with nothing left behind?",
            "Look for liquid shed along the path and for a droplet that no "
            "longer fills the rectangle. Both break the equal-split premise, "
            "and neither is detectable any other way in this pipeline.")

    def split(self) -> None:
        """Drive the tree, pausing at each stage boundary for a look."""
        for stage in range(len(self.plan.axes)):
            steps = [s for s in self.plan.steps if s.stage == stage]
            for step in steps:
                for f in step.frames:
                    self.chip.activate(list(f.drops), settle=True,
                                       allow_violations=self.allow_violations)
            live = self.plan.stages[stage + 1]
            self._gate(
                f"Stage {stage} ({self.plan.axes[stage]}-axis) done -- do you "
                f"count {len(live)} separate pieces?",
                f"Expected after stage {stage}: {len(live)} pieces, each "
                f"{self.plan.nodes[live[0]].height}x"
                f"{self.plan.nodes[live[0]].width} electrodes. Fewer means a "
                f"neck did not open; more means it broke up or threw "
                f"satellites. Either way the next stage assumes this one "
                f"worked.")

    def report(self) -> str:
        """What the run claims, and what it did not check."""
        eq = SP.volume_equality(self.plan)
        out = [SP.describe(self.plan, self.cfg), "", eq.describe(), "",
               "NOT VERIFIED THIS RUN (no camera in the loop):",
               "  - the volume proxy above is a property of the PLAN. Nothing "
               "measured the liquid, so every assumption it rests on is "
               "untested.",
               "  - there is no per-electrode readback in this API, so the "
               "operator's answers below are the only evidence anything "
               "actuated at all."]
        if self.notes:
            out += ["", "RUN NOTES:"] + [f"  ! {n}" for n in self.notes]
        out += ["", "Operator answers:"]
        out.extend(f"  [{a}] {q}" for q, a in self.log)
        return "\n".join(out)

    # ── the whole thing ──────────────────────────────────────────────────────

    def run(self) -> str:
        self._preflight()
        self.announce(
            f"SPLIT: {2 ** len(self.axes)} pieces, "
            f"{self.plan.n_frames} frames, {self.plan.duration_s():.0f}s of "
            f"dwell, root {self.root.height}x{self.root.width} at "
            f"row {self.root.row}, col {self.root.col}. No camera: every "
            f"check in this run is yours.")
        self.load()
        self.walk()
        self.split()
        return self.report()


def run_split(chip: ChipController, **kw) -> str:
    """Convenience wrapper. See :class:`SplitSession` for the parameters."""
    return SplitSession(chip=chip, **kw).run()


# ── command line ─────────────────────────────────────────────────────────────


def _position(text: str) -> tuple[int, int]:
    try:
        r, c = (int(v) for v in text.replace(" ", "").split(","))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"expected ROW,COL (e.g. 55,55), got {text!r}")
    return r, c


def _axes(text: str) -> tuple[SP.Axis, ...]:
    out = tuple(ch.upper() for ch in text if not ch.isspace())
    bad = [a for a in out if a not in ("W", "H")]
    if bad or not out:
        raise argparse.ArgumentTypeError(
            f"axes must be a string of W and H (e.g. WHW, WHWH), got {text!r}")
    return out  # type: ignore[return-value]


def build_parser() -> "argparse.ArgumentParser":
    p = argparse.ArgumentParser(
        prog="python -m microdrop.protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run a symmetric split tree with a human in the loop.\n"
                    "No camera, no OpenCV, no numpy, no calibration file: "
                    "positions are electrode indices and verification is you, "
                    "at the microscope, answering y/n at each gate.",
        epilog="Start with --plan-only (touches nothing), then a dry run "
               "(--yes for plumbing), then --arm.",
    )
    p.add_argument("--arm", action="store_true",
                   help="Energise electrodes. WITHOUT THIS IT IS A DRY RUN: "
                        "the frames are planned, validated and logged but "
                        "SetPower/SetVolt/ActivateElec are never issued, so "
                        "nothing moves. Dry-run is the default deliberately.")
    p.add_argument("--at", type=_position, default=None, metavar="ROW,COL",
                   help="Where the tree SPLITS. Default is split_root(), "
                        f"row {SP.SPLIT_ROOT_ROW}, col {SP.SPLIT_ROOT_COL} -- "
                        "see its docstring for why. The droplet is loaded here "
                        "directly unless --walk-from is given.")
    p.add_argument("--walk-from", type=_position, default=None, metavar="ROW,COL",
                   help="Load at ROW,COL and TRANSPORT to the split position "
                        "instead of loading there. Off by default: the "
                        "energised region does the positioning, so loading at "
                        "row 55 is no harder than at row 5, and every "
                        "electrode of travel is liquid you can lose with no "
                        "camera to see it go. Use 5,10 for the sweep's load "
                        "position (95 electrodes, ~95s).")
    p.add_argument("--axes", type=_axes, default=SP.DEFAULT_AXES,
                   help="Split axis order, W/H per stage. Default WHW = 8 "
                        "pieces of 10x5. WHWH = 16 of 5x5, which the default "
                        "position has room for. (default: %(default)s)")
    p.add_argument("--backend", choices=("auto", "real", "fake"), default="auto",
                   help="'auto' uses the vendor DLL where it can load and a "
                        "fake rig otherwise -- so this is safe off-Windows. "
                        "(default: %(default)s)")
    p.add_argument("--step-delay", type=float, default=P.PROVEN_SETTLE_S,
                   metavar="S",
                   help="Seconds after every frame. Default %(default)s "
                        "matches csvvolcont.py:137 and Frame.settle_s, so the "
                        "duration the plan reports is the duration you get.")
    p.add_argument("--plan-only", action="store_true",
                   help="Print the plan, the clearance verdict and the volume "
                        "claim, then stop. Opens no USB handle and asks "
                        "nothing. Run this first.")
    p.add_argument("--yes", action="store_true",
                   help="Auto-answer every operator gate. FOR PLUMBING CHECKS "
                        "ONLY -- it removes the only verification this "
                        "pipeline has. Recorded in the report so such a run "
                        "cannot later be read as one a human checked.")
    p.add_argument("--allow-clearance-violations", action="store_true",
                   help="Permit geometry that runs off the electrode array. "
                        "Recorded in the report. See chiphealth.clearance.")
    p.add_argument("--dll-dir", default=DEFAULT_DLL_DIR,
                   help="Vendor DLL directory. (default: %(default)s)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = ChipConfig()
    row, col = args.at or (SP.SPLIT_ROOT_ROW, SP.SPLIT_ROOT_COL)
    root = SP.DropNode(id="d", parent=None, stage=0, height=20, width=20,
                       row=row, col=col)
    approach_from = (
        SP.DropNode(id="d", parent=None, stage=0, height=20, width=20,
                    row=args.walk_from[0], col=args.walk_from[1])
        if args.walk_from else None)

    # ── plan-only: no USB handle, no backend, nothing energised ──────────────
    if args.plan_only:
        plan = SP.plan_tree(root, args.axes, cfg=cfg)
        try:
            SP.require_clearance(plan, cfg, args.allow_clearance_violations)
            verdict = "CLEARANCE OK"
        except ClearanceViolation as exc:
            print(exc)
            return 2
        if approach_from is not None:
            app = SP.plan_approach(approach_from, row, col)
            SP.require_approach_clearance(app, cfg,
                                          args.allow_clearance_violations)
            print(f"approach {app.from_rc} -> {app.to_rc}: {app.electrodes} "
                  f"electrodes, {app.n_frames} frames, {app.duration_s():.0f}s")
        print(SP.describe(plan, cfg))
        print()
        print(SP.volume_equality(plan).describe())
        print()
        print(f"{verdict}. Nothing was energised and no USB handle was opened.")
        return 0

    # ── a real session ───────────────────────────────────────────────────────
    backend = make_backend(args.backend, args.dll_dir, DEFAULT_DLL_NAME,
                           cfg.rows, cfg.cols)
    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=args.arm, step_delay_s=args.step_delay,
                          volt_tolerance=cfg.volt_tolerance,
                          volt_settle_s=cfg.volt_settle_s,
                          power_settle_s=cfg.power_settle_s,
                          allow_violations=args.allow_clearance_violations)

    confirm = (lambda q, detail="": True) if args.yes else console_confirm
    session = SplitSession(
        chip=chip, root=root, axes=args.axes, cfg=cfg,
        approach_from=approach_from, confirm=confirm,
        allow_violations=args.allow_clearance_violations)
    if args.yes:
        session.notes.append(
            "--yes: every operator gate was auto-answered. Nobody looked down "
            "the microscope, so this run verified NOTHING. Plumbing check only.")
    if not args.arm:
        session.notes.append(
            "DRY RUN: no electrode was energised, so no liquid moved. The "
            "geometry and the gate sequence are exercised; nothing else is.")
    if args.allow_clearance_violations:
        session.notes.append(
            "--allow-clearance-violations: geometry off the electrode array "
            "was permitted. The vendor DLL's behaviour there is unspecified.")

    with chip:
        check = chip.verify_voltage()
        print(check.summary())
        if args.arm and not check.ok:
            print("\nRails do not match what was commanded. Refusing to split "
                  "-- fix the supply, or investigate with chiphealth's "
                  "--volt-poll diagnostic.")
            return 3
        try:
            print(session.run())
        except OperatorAbort as exc:
            print(f"\nSTOPPED by the operator at: {exc}")
            print(session.report())
            return 1
        except ClearanceViolation as exc:
            print(exc)
            return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
