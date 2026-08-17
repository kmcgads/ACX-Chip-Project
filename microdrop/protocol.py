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
import sys
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
    #: Walk the droplet in from the load position before splitting. TRUE by
    #: default: the researcher specified that loading happens at row 5, col 55
    #: and the droplet is then moved to the split position, so the walk is part
    #: of the protocol rather than an option. Set False to load directly at
    #: `root` instead, which costs no travel but requires loading mid-chip.
    #:
    #: Named `transport`, not `walk`: `walk()` is the phase method below,
    #: and a dataclass field of the same name silently shadows it.
    transport: bool = True
    #: Where the droplet is loaded. None means `splitplan.load_root()`.
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
        if self.transport:
            self.approach_from = self.approach_from or SP.load_root()
        else:
            self.approach_from = None
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


def _stage_ratio(text: str) -> tuple[int, float]:
    """`N:RATIO` for --stretch-stage. Stage index is 0-based, as in the plan."""
    try:
        stage, ratio = text.split(":")
        return int(stage), float(ratio)
    except Exception:
        raise argparse.ArgumentTypeError(
            f"expected STAGE:RATIO with a 0-based stage (e.g. 2:2.2), "
            f"got {text!r}")


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
    p.add_argument("--load-at", type=_position, default=None, metavar="ROW,COL",
                   help="Where the droplet is LOADED, before being moved to "
                        f"the split position. Default row {SP.SPLIT_LOAD_ROW}, "
                        f"col {SP.SPLIT_LOAD_COL}, which shares a column with "
                        "the split position so the walk is one straight leg of "
                        "50 electrodes. A load needs no split clearance -- it "
                        "is a plain hold -- so this can be wherever you can "
                        "reach. Use 5,10 for the sweep's load position (95 "
                        "electrodes and an L-turn).")
    p.add_argument("--no-walk", action="store_true",
                   help="Load directly at the split position and skip the "
                        "transport entirely. Zero travel, so zero invisible "
                        "transport loss -- but it means loading mid-chip.")
    p.add_argument("--axes", type=_axes, default=SP.DEFAULT_AXES,
                   help="Split axis order, W/H per stage. Default WHW = 8 "
                        "pieces of 10x5. WHWH = 16 of 5x5, which the default "
                        "position has room for. (default: %(default)s)")
    p.add_argument("--stretch-stage", action="append", default=None,
                   metavar="N:RATIO", dest="stretch_stage",
                   help="Stretch ratio for stage N only; every other stage "
                        f"keeps the proven {P.STRETCH_RATIO}. Repeatable, e.g. "
                        "--stretch-stage 2:2.2 --stretch-stage 3:2.2. Raising "
                        "it is the ONLY way to put the two children of a split "
                        "further apart -- the neck gap is stretch_to(e) - e, "
                        "so there is no separation margin to widen directly. "
                        "Applies to every parent in the stage; per-piece "
                        "widening is refused by the symmetry invariant. Off "
                        "the proven evidence: see "
                        "SplitParams.stage_stretch_ratios. Recorded in the run "
                        "notes.")
    p.add_argument("--backend", choices=("auto", "real", "fake"), default="auto",
                   help="'auto' uses the vendor DLL where it can load and a "
                        "fake rig otherwise -- so this is safe off-Windows. "
                        "(default: %(default)s)")
    p.add_argument("--step-delay", type=float, default=None, metavar="S",
                   help=f"Seconds after every frame. Defaults to "
                        f"{P.PROVEN_SETTLE_S} when ARMED -- csvvolcont.py:137, "
                        f"and it matches Frame.settle_s so the duration the "
                        f"plan reports is the duration you get -- and to 0 for "
                        f"a DRY RUN, where nothing is energised so no liquid "
                        f"needs time to reflow. Same reasoning as "
                        f"SweepConfig.dry_run_step_delay_s.")
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

    g = p.add_argument_group(
        "bring-up / diagnostics",
        "For isolating a software addressing problem from a hardware one.")
    g.add_argument("--poke", type=_position, default=None, metavar="ROW,COL",
                   help="Activate ONE rectangle at ROW,COL and hold it. No "
                        "plan, no tree, no operator gates -- a single "
                        "ActivateElec call, its exact struct fields and its "
                        "return code printed. This is the minimal command.")
    g.add_argument("--size", default="20x20", metavar="HxW",
                   help="Size for --poke. (default: %(default)s) A 1x1 may be "
                        "invisible even when working; 20x20 is what the split "
                        "protocol holds and is easy to see.")
    g.add_argument("--dump", action="store_true",
                   help="Print the exact ActivateElec arguments for the hold "
                        "frame at the current position and exit. No hardware.")
    g.add_argument("--log-frames", action="store_true",
                   help="Log every ActivateElec call with its struct fields "
                        "and return code as it goes out.")
    g.add_argument("--allow-fake-arm", action="store_true",
                   help="Permit --arm against the FAKE backend. Refused by "
                        "default: a fake rig satisfies the rail check exactly "
                        "like a real one, so an accidental fake armed run "
                        "looks like a successful run that moved no liquid.")
    return p


def _size(text: str) -> tuple[int, int]:
    try:
        h, w = (int(v) for v in text.lower().replace(" ", "").split("x"))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"expected HxW (e.g. 20x20), got {text!r}")
    return h, w


def describe_call(rows: int, cols: int, drops: Sequence[Drop]) -> str:
    """Exactly what goes to the DLL, in the DLL's own terms.

    Field ORDER matters and is not the order the vendor PDF documents: the
    struct is (height, width, row, col), established by disassembly
    (workspace/analysis.md §2) and identical in all 13 legacy scripts. Printed
    positionally as well as by name so a field-order problem is visible.
    """
    out = [f"ActivateElec(rows={rows}, cols={cols}, count={len(drops)}, Drop*["]
    for i, d in enumerate(drops):
        r0, r1, c0, c1 = d.covers()
        out.append(
            f"  [{i}] Drop({d.height}, {d.width}, {d.row}, {d.col})"
            f"   # (height, width, row, col)")
        out.append(
            f"       covers rows {r0}..{r1}, cols {c0}..{c1}  "
            f"= {(r1 - r0 + 1) * (c1 - c0 + 1)} electrodes")
    out.append("])")
    out.append("Indices are 1-BASED: electrode (1,1) is top-left, (128,128) "
               "bottom-right. cleanup.py:108 activates the whole array as "
               "Drop(128, 128, 1, 1), which is the reference for that.")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = ChipConfig()
    stage_ratios = tuple(_stage_ratio(s) for s in (args.stretch_stage or ()))
    sp = P.SplitParams(stage_stretch_ratios=stage_ratios)
    row, col = args.at or (SP.SPLIT_ROOT_ROW, SP.SPLIT_ROOT_COL)
    root = SP.DropNode(id="d", parent=None, stage=0, height=20, width=20,
                       row=row, col=col)
    walk = not args.no_walk
    approach_from = (
        SP.DropNode(id="d", parent=None, stage=0, height=20, width=20,
                    row=args.load_at[0], col=args.load_at[1])
        if (walk and args.load_at) else None)

    # ── plan-only: no USB handle, no backend, nothing energised ──────────────
    if args.plan_only:
        plan = SP.plan_tree(root, args.axes, sp, cfg=cfg)
        try:
            SP.require_clearance(plan, cfg, args.allow_clearance_violations)
            verdict = "CLEARANCE OK"
        except ClearanceViolation as exc:
            print(exc)
            return 2
        if walk:
            app = SP.plan_approach(approach_from or SP.load_root(), row, col)
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

    # ── --dump: exactly what the DLL would be handed. No hardware. ───────────
    if args.dump:
        h, w = _size(args.size) if args.poke else (20, 20)
        r, c = args.poke or (row, col)
        print(describe_call(cfg.rows, cfg.cols, [Drop(h, w, r, c)]))
        return 0

    # ── a real session ───────────────────────────────────────────────────────
    backend = make_backend(args.backend, args.dll_dir, DEFAULT_DLL_NAME,
                           cfg.rows, cfg.cols)
    is_fake = type(backend).__name__ == "FakeBackend"

    # Say which rig this is, before anything else. The rail check cannot tell
    # you: FakeBackend stores what SetVolt was given and hands it straight back
    # to InquireVolt, so it reports "Rails match: commanded [45,45,45,...],
    # measured [45,45,45,...]" exactly like a healthy real one. An armed run
    # against the fake rig is therefore indistinguishable from a successful run
    # in every log line -- except this one.
    print(f"BACKEND: {type(backend).__name__}"
          + (f"  <- NOT the hardware. No electrode can move." if is_fake
             else f"  ({args.dll_dir})"))
    if is_fake and args.arm and not args.allow_fake_arm:
        print("\nRefusing to --arm against the fake backend.\n"
              "  This is almost always one of two things:\n"
              "    * you are running under WSL/Linux. The vendor DLLs are\n"
              "      Windows x64 PE binaries and cannot load there, so\n"
              "      --backend auto silently falls back to the fake rig.\n"
              "      Use the Windows interpreter:\n"
              "        .\\.venv\\Scripts\\python.exe -m microdrop.protocol --arm\n"
              "    * the DLL did not load from --dll-dir (wrong path, or a\n"
              "      missing dependency such as libusb-1.0.dll).\n"
              "  Pass --backend real to see the load error instead of a\n"
              "  fallback, or --allow-fake-arm if a fake armed run is what you\n"
              "  actually want.")
        return 4

    # A dry run energises nothing, so nothing needs time to reflow. Charging it
    # the proven 0.5s turns a 260-frame plumbing check into a 2-minute wait for
    # no physical reason -- the same trap SweepConfig.dry_run_step_delay_s
    # already documents for the chip-health sweep. An explicit --step-delay
    # always wins, and the value actually used is printed below.
    step_delay = (args.step_delay if args.step_delay is not None
                  else (P.PROVEN_SETTLE_S if args.arm else 0.0))

    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=args.arm, step_delay_s=step_delay,
                          volt_tolerance=cfg.volt_tolerance,
                          volt_settle_s=cfg.volt_settle_s,
                          power_settle_s=cfg.power_settle_s,
                          allow_violations=args.allow_clearance_violations,
                          log_frames=args.log_frames)

    # ── --poke: one electrode rectangle, one call, nothing else ──────────────
    if args.poke:
        h, w = _size(args.size)
        drop = Drop(h, w, args.poke[0], args.poke[1])
        with chip:
            print(chip.verify_voltage().summary())
            print()
            print(describe_call(cfg.rows, cfg.cols, [drop]))
            chip.activate([drop], settle=False)
            print()
            print(chip.rc_summary())
            if not chip.armed:
                print("\nDRY RUN: that call was never issued. Add --arm.")
            else:
                print("\nThere is no per-electrode readback in this API, so "
                      "nothing here can confirm the electrode switched.\n"
                      "Look at the chip now: is that rectangle holding?")
                # Only block if there is someone to unblock it. Piped or
                # scripted, holding forever is not a useful default.
                if sys.stdin is not None and sys.stdin.isatty():
                    input(">>> press Enter to de-energise ")
                else:
                    print("(stdin is not a terminal -- de-energising now)")
        return 0

    confirm = (lambda q, detail="": True) if args.yes else console_confirm
    session = SplitSession(
        chip=chip, root=root, axes=args.axes, cfg=cfg, sp=sp,
        transport=walk, approach_from=approach_from, confirm=confirm,
        allow_violations=args.allow_clearance_violations)
    if stage_ratios:
        # Same reasoning as the step-delay note below: a run whose stages were
        # stretched off the proven ratio must not later be read as one that
        # used the proven geometry.
        detail = ", ".join(f"stage {s} at {r}" for s, r in sorted(stage_ratios))
        session.notes.append(
            f"--stretch-stage: {detail}, instead of the proven "
            f"{P.STRETCH_RATIO}. Every other stage is unaffected. This is off "
            f"the csvvolcont evidence and is not a proven geometry.")
    if args.yes:
        session.notes.append(
            "--yes: every operator gate was auto-answered. Nobody looked down "
            "the microscope, so this run verified NOTHING. Plumbing check only.")
    if not args.arm:
        session.notes.append(
            "DRY RUN: no electrode was energised, so no liquid moved. The "
            "geometry and the gate sequence are exercised; nothing else is.")
    if step_delay != P.PROVEN_SETTLE_S:
        # Never silent: a run timed differently from the proven dwell must not
        # be mistaken later for one that used it.
        session.notes.append(
            f"step delay {step_delay}s, not the proven {P.PROVEN_SETTLE_S}s "
            f"(csvvolcont.py:137).")
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
