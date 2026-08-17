"""
run_16piece_split.py — 16 pieces of 5x5 from one 20x20 droplet.

⚠ NOT CONFIRMED ON HARDWARE. This is a candidate, not a record of a run that
worked. The geometry plans clean and has been checked every way a dry run can
check it, but stages 2 and 3 both failed to separate live and the fixes below
have not been back on a chip. Do not restore a "verified" label, or tag
`split-16piece-verified`, until a live run parts all sixteen pieces.

⚠ ALWAYS ARMED. There is no dry run. Opening the chip issues SetPower and
SetVolt, so THE RAILS COME UP BEFORE THE FIRST GATE IS ASKED. Answering `n` at
the phase 0 voltage gate stops the run before any electrode is energised, but
not before the supply is live. `--arm` is accepted and does nothing.
Hardware-free check: `python -m microdrop.protocol --plan-only --axes WHWH`

TWO CHANGES ARE IN PLAY, AND ONLY ONE IS ISOLATED
  geometry  stages 2 and 3 stretch at 2.2 instead of 1.75, opening both neck
            gaps from 8 to 12. Stages 0 and 1 keep the proven ratio.
  dwell     stage 3 alone holds each frame 1.0s instead of 0.5s.

Stage 2 is therefore the DWELL CONTROL: widened exactly like stage 3, not
slowed. Stage 2 parting while stage 3 does not, or the reverse, is the
informative outcome. The control is good but not perfect -- the two stages
differ in axis and parent shape as well as dwell. How to read each combination
of results, and the numbers behind 2.2 and 1.0s:
docs/guides/separation-and-dwell-tuning.md

5x5 IS THE FLOOR for a 20x20. 20 = 2^2 x 5, so four halvings is all
divisibility allows and the planner refuses a fifth rather than guessing which
child gets the extra electrode. If 5x5 does not hold, no 32-piece tree from
this droplet can either, whatever else changes.

Every parameter below is HARDCODED, and check_geometry() refuses to run if the
planner stops producing them -- including refusing if the widening leaks into
stages 0-1 or the dwell leaks off stage 3. Nothing about the split is
recomputed here: geometry lives in `microdrop.splitplan`, sequencing in
`microdrop.protocol`, both under test.

Operator gates, exit codes, and what a camera-free run cannot verify:
docs/guides/running-the-split-scripts.md

Usage: python microdrop/run_16piece_split.py   (live -- there is no other mode)
"""

import argparse
import os
import sys

# Run directly, not as `python -m`. The script lives inside the `microdrop`
# package, so the PROJECT ROOT -- the parent of this file's directory -- is
# what has to be importable, not the directory itself.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from chiphealth.actuation import ChipController, make_backend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import DEFAULT_DLL_DIR, DEFAULT_DLL_NAME, ChipConfig
from microdrop import params as P
from microdrop import splitplan as SP
from microdrop.protocol import OperatorAbort, SplitSession

# ── The candidate configuration ───────────────────────────────────────────────
# Load, walk and split positions are the same ones the 8-piece script uses.
# Written out rather than read from splitplan's defaults: if a later change
# moves `split_root` or `DEFAULT_AXES`, this file must keep doing what it says
# or refuse. See check_geometry().

LOAD_ROW, LOAD_COL = 5, 55 ##where droplet is loaded for movement
SPLIT_ROW, SPLIT_COL = 55, 55   # where the tree runs
DROPLET_H, DROPLET_W = 30,30   # the starting droplet
AXES = ("W", "H", "W", "H")     # 4 stages -> 16 pieces

# The two stages that failed live. Stages 0-1 keep the proven 1.75 because they
# worked. docs/guides/separation-and-dwell-tuning.md
WIDENED_RATIO = 2.2
STAGE_STRETCH_RATIOS = ((2, WIDENED_RATIO), (3, WIDENED_RATIO))

# Stage 3 ONLY, on purpose: an experiment run on two stages at once cannot say
# whether time or distance was the factor. Stage 2 keeps the proven dwell and
# is the control.
STAGE3_EXTRA_SETTLE_S = 0.5
STAGE_EXTRA_SETTLE_S = ((3, STAGE3_EXTRA_SETTLE_S),)

# What the above must produce. Taken from `python -m microdrop.protocol
# --plan-only --axes WHWH --stretch-stage 2:2.2 --stretch-stage 3:2.2` on
# 2026-08-17. These are DRY-RUN expectations: they pin the planner, they do not
# certify the physics. A mismatch means the planner changed under this script,
# and it stops rather than running a geometry nobody has looked at.
EXPECT_PIECES = 16
EXPECT_LEAF = (5, 5)            # electrodes; 1.232 x 1.232 mm at the 246.48um pitch
EXPECT_WALK_ELECTRODES = 50
EXPECT_APPROACH_FRAMES = 100
EXPECT_TREE_FRAMES = 207        # was 159 unwidened. 1 + 100 + 207 = 308 frames
EXPECT_WIDENED_GAP = 12         # stages 2 and 3. Was 8. THE GEOMETRY CHANGE
EXPECT_EARLY_GAP = 16           # stages 0-1, unchanged and must stay unchanged

# Frame COUNT is unchanged by the dwell experiment -- only how long each frame
# is held. Stage 3 is 8 splits x 13 frames = 104 frames, so +0.5s each is +52s.
EXPECT_STAGE3_FRAMES = 104
EXPECT_TREE_DWELL_S = 155.5     # was 103.5 at a flat 0.5s
EXPECT_TOTAL_DWELL_S = 205.5    # + the 50s approach walk. Was 153.5

STEP_DELAY_S = P.PROVEN_SETTLE_S    # 0.5s, csvvolcont.py:137
BAR = "=" * 68
RULE = "─" * 60

#: Carried into the run report, which is the only thing that outlives the
#: terminal. A transcript of confident yeses must not read six months from now
#: as though this geometry had been proven.
UNVERIFIED_NOTE = (
    "NOT HARDWARE-VERIFIED: stages 2 and 3 stretch at 2.2 instead of the "
    "proven 1.75, opening both neck gaps from 8 to 12. That widening had never "
    "been on a chip as of 2026-08-17, and it was reached by adding margin, not "
    "by identifying why the unwidened stages failed to separate -- the earlier "
    "8-piece failure happened with geometry identical to a run that worked, so "
    "geometry was never shown to be the differing variable. Worst stretch "
    "aspect rises 3.6 -> 4.4 against the 7.2 that splitplan records as where "
    "liquid throws satellites, so watch stage 3 for satellites specifically. "
    "Do not cite this configuration as proven on the strength of one "
    "transcript.")

#: The dwell experiment, carried into the report alongside the geometry note.
#: Two variables are in play in this run and only one of them is isolated;
#: saying so in the artifact is the difference between a result and an anecdote.
DWELL_NOTE = (
    "DWELL EXPERIMENT, STAGE 3 ONLY: stage 3 holds each of its 104 frames for "
    "1.0s instead of the proven 0.5s. Stages 0-2 keep 0.5s, so stage 2 is the "
    "control -- it is widened exactly like stage 3 but not slowed. READ THE "
    "RESULT THIS WAY: stage 2 parts and stage 3 parts means widening was "
    "enough and the extra time is unproven; stage 2 fails and stage 3 parts "
    "points at dwell; both fail means neither distance nor time at these "
    "values is the answer and the cause is elsewhere (droplet volume, oil, "
    "surface state, voltage under load, plate gap). Note the confound: stage 2 "
    "and stage 3 differ in axis and parent shape as well as dwell, so the "
    "control is good but not perfect.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    """Section header, so the phases are findable in a long terminal."""
    print(f"\n{BAR}\n  {title}\n{BAR}")


def say(tag: str, message: str) -> None:
    """One tagged progress line."""
    print(f"  [{tag}] {message}")


def confirm(question: str, detail: str = "") -> bool:
    """A real y/n gate. Anything but y/n re-asks; `n` stops the run.

    Deliberately not an Enter-to-continue prompt. These gates are the only
    verification this pipeline has, and a prompt you can dismiss by leaning on
    the keyboard is not a check.
    """
    if detail:
        print(f"\n{detail}")
    print(f"\n  {RULE}")
    try:
        while True:
            answer = input(f"  >>> {question} [y/n] ").strip().lower()
            if answer in ("y", "yes"):
                print(f"  {RULE}")
                return True
            if answer in ("n", "no"):
                print(f"  {RULE}")
                return False
            print("      please answer y or n")
    except (EOFError, KeyboardInterrupt):
        print("\n      no answer -- treating as 'n'.")
        return False


def check_geometry(session: SplitSession) -> SP.Approach:
    """Refuse to run if the planner no longer produces the expected geometry.

    Protects the one claim this file can honestly make: that what it energises
    is what was checked. Returns the approach so the caller need not
    re-establish that it exists.
    See CONTRIBUTING.md#the-check_geometry-pattern.
    """
    plan, approach = session.plan, session.approach

    # Raised here rather than collected into `problems`: a missing approach
    # means THIS file lost transport=True or approach_from, not that splitplan
    # moved. Raising is also what lets the return type promise a real Approach
    # -- a checker cannot see that a non-empty `problems` implies the raise.
    if approach is None:
        raise SystemExit(
            "\n  REFUSING TO RUN: no approach was planned, so the droplet would\n"
            "  be split wherever it happened to be loaded rather than walked to\n"
            "  the intended split position. The SplitSession in main() must keep\n"
            f"  transport=True and approach_from set to row {LOAD_ROW}, col {LOAD_COL}.\n")

    leaves = {(n.height, n.width) for n in plan.leaves}
    problems = []

    if len(plan.leaves) != EXPECT_PIECES:
        problems.append(f"{len(plan.leaves)} pieces, expected {EXPECT_PIECES}")
    if leaves != {EXPECT_LEAF}:
        problems.append(f"leaf sizes {sorted(leaves)}, expected {EXPECT_LEAF}")
    if plan.n_frames != EXPECT_TREE_FRAMES:
        problems.append(f"{plan.n_frames} tree frames, expected {EXPECT_TREE_FRAMES}")

    # Pinned separately so neither can drift into the other: stages 2-3 are
    # what was widened, stages 0-1 are what must NOT have been.
    widened = {s.neck_gap for s in plan.steps if s.stage in (2, 3)}
    early = {s.neck_gap for s in plan.steps if s.stage in (0, 1)}
    if widened != {EXPECT_WIDENED_GAP}:
        problems.append(f"stage 2-3 neck gap {sorted(widened)}, expected "
                        f"{EXPECT_WIDENED_GAP}")
    if early != {EXPECT_EARLY_GAP}:
        problems.append(f"stage 0-1 neck gap {sorted(early)}, expected "
                        f"{EXPECT_EARLY_GAP} -- the widening must apply to "
                        f"stages 2 and 3 only; 0 and 1 have worked on hardware")

    # Pinned on both sides: stage 3 must be slowed and every other stage must
    # NOT be, or this stops being a controlled experiment.
    s3 = [f for s in plan.steps if s.stage == 3 for f in s.frames]
    others = [f for s in plan.steps if s.stage != 3 for f in s.frames]
    want3 = P.PROVEN_SETTLE_S + STAGE3_EXTRA_SETTLE_S
    if len(s3) != EXPECT_STAGE3_FRAMES:
        problems.append(f"{len(s3)} stage-3 frames, expected "
                        f"{EXPECT_STAGE3_FRAMES}")
    if {f.settle_s for f in s3} != {want3}:
        problems.append(f"stage-3 dwell {sorted({f.settle_s for f in s3})}, "
                        f"expected {want3}s")
    if {f.settle_s for f in others} != {P.PROVEN_SETTLE_S}:
        problems.append(
            f"stage 0-2 dwell {sorted({f.settle_s for f in others})}, expected "
            f"the proven {P.PROVEN_SETTLE_S}s -- stage 2 is the CONTROL for "
            f"this experiment and must keep it")
    if abs(plan.duration_s() - EXPECT_TREE_DWELL_S) > 0.01:
        problems.append(f"tree dwell {plan.duration_s()}s, expected "
                        f"{EXPECT_TREE_DWELL_S}s")
    if approach.electrodes != EXPECT_WALK_ELECTRODES:
        problems.append(f"walk is {approach.electrodes} electrodes, "
                        f"expected {EXPECT_WALK_ELECTRODES}")
    if approach.n_frames != EXPECT_APPROACH_FRAMES:
        problems.append(f"{approach.n_frames} approach frames, "
                        f"expected {EXPECT_APPROACH_FRAMES}")
    if plan.violations:
        problems.append(f"{len(plan.violations)} geometry violation(s)")

    if problems:
        raise SystemExit(
            "\n  REFUSING TO RUN: this is no longer the geometry that was\n"
            "  dry-run checked.\n"
            + "".join(f"    - {p}\n" for p in problems)
            + "\n  microdrop/splitplan.py has changed since 2026-08-17. Either\n"
              "  restore it, or re-check with\n"
              "  `python -m microdrop.protocol --plan-only --axes WHWH "
              f"--stretch-stage 2:{WIDENED_RATIO} --stretch-stage "
              f"3:{WIDENED_RATIO}`\n"
              "  and update the EXPECT_* constants at the top of this file to\n"
              "  match what you checked.\n")

    return approach


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the candidate 16-piece split. ALWAYS ARMED: running "
                    "this script energises the chip. There is no dry run -- "
                    "use `python -m microdrop.protocol --plan-only --axes "
                    "WHWH` for a check that touches no hardware. This geometry "
                    "is dry-run checked but NOT yet confirmed on hardware, and "
                    "its stage 2 has already failed live in the 8-piece run.")
    # Accepted and ignored, so old muscle memory does not abort a run at the
    # rig with `unrecognized arguments`.
    ap.add_argument("--arm", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    banner("16-PIECE SPLIT — DRY-RUN VERIFIED, NOT CONFIRMED ON HARDWARE")
    if args.arm:
        say("Note", "--arm is accepted but no longer needed; this script is "
                    "always armed.")
    print(f"  load     {DROPLET_H}x{DROPLET_W} at row {LOAD_ROW}, col {LOAD_COL}")
    print(f"  walk     {EXPECT_WALK_ELECTRODES} electrodes down column {LOAD_COL}"
          f" to row {SPLIT_ROW}, col {SPLIT_COL}")
    print(f"  split    {' -> '.join(AXES)}  ->  {EXPECT_PIECES} pieces of "
          f"{EXPECT_LEAF[0]}x{EXPECT_LEAF[1]}")
    print(f"  change   stages 2 AND 3 stretch at {WIDENED_RATIO} "
          f"(not {P.STRETCH_RATIO}); neck gap 8 -> {EXPECT_WIDENED_GAP} on both")
    print(f"  status   stages 0-1 unchanged at the proven ratio, gap "
          f"{EXPECT_EARLY_GAP}")
    print(f"  dwell    stage 3 holds each frame "
          f"{P.PROVEN_SETTLE_S + STAGE3_EXTRA_SETTLE_S}s "
          f"(+{STAGE3_EXTRA_SETTLE_S}s); stages 0-2 keep the proven "
          f"{P.PROVEN_SETTLE_S}s")
    print(f"  WATCH    gates 5 and 6 -- both stages failed to separate live.")
    print(f"           Gate 5 (stage 2) is widened only: the DWELL CONTROL.")
    print(f"           Gate 6 (stage 3) is widened AND slowed.")
    print(f"  mode     ARMED — the rails come up on connect and electrodes "
          f"will be energised")

    # ── Rig ────────────────────────────────────────────────────────────────
    cfg = ChipConfig()
    backend = make_backend("auto", DEFAULT_DLL_DIR, DEFAULT_DLL_NAME,
                           cfg.rows, cfg.cols)
    is_fake = type(backend).__name__ == "FakeBackend"
    say("Rig", f"{type(backend).__name__}"
        + ("  <- NOT the hardware" if is_fake else f"  ({DEFAULT_DLL_DIR})"))

    if is_fake:
        # A fake rig passes the rail check identically to a real one, so an
        # accidental fake run reads as a success that moved no liquid -- and
        # this is the run that decides whether 5x5 holds.
        print("\n  The vendor DLL did not load, so this run would energise\n"
              "  nothing while looking exactly like one that did.\n"
              "  Almost always: you are on WSL/Linux, where the Windows x64 DLL\n"
              "  cannot load. Use the Windows interpreter:\n"
              "    .\\.venv\\Scripts\\python.exe microdrop\\run_16piece_split.py\n")
        return 4

    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=True,
                          step_delay_s=STEP_DELAY_S,
                          volt_tolerance=cfg.volt_tolerance,
                          volt_settle_s=cfg.volt_settle_s,
                          power_settle_s=cfg.power_settle_s)

    # ── The run ────────────────────────────────────────────────────────────
    # This script chooses the numbers; splitplan decides what frames they imply.
    session = SplitSession(
        chip=chip,
        root=SP.DropNode(id="d", parent=None, stage=0,
                         height=DROPLET_H, width=DROPLET_W,
                         row=SPLIT_ROW, col=SPLIT_COL),
        axes=AXES,
        cfg=cfg,
        sp=P.SplitParams(stage_stretch_ratios=STAGE_STRETCH_RATIOS,
                         stage_extra_settle_s=STAGE_EXTRA_SETTLE_S),
        transport=True,
        approach_from=SP.DropNode(id="d", parent=None, stage=0,
                                  height=DROPLET_H, width=DROPLET_W,
                                  row=LOAD_ROW, col=LOAD_COL),
        confirm=confirm,
        announce=lambda m: say("Split", m),
    )
    approach = check_geometry(session)
    session.notes.append(UNVERIFIED_NOTE)
    session.notes.append(DWELL_NOTE)

    total = 1 + approach.n_frames + session.plan.n_frames
    say("Plan", f"{total} frames, ~{approach.duration_s() + session.plan.duration_s():.0f}s "
                f"of dwell — {STEP_DELAY_S}s a frame everywhere except stage 3, "
                f"which holds {P.PROVEN_SETTLE_S + STAGE3_EXTRA_SETTLE_S}s")

    with chip:
        banner("PHASE 0 — power and rails")
        check = chip.verify_voltage()
        for line in check.summary().splitlines():
            say("Volts", line)
        if not check.ok:
            print("\n  Rails do not match what was commanded. Refusing to split.\n")
            return 3
        if not confirm("Is the voltage connection verified and good?"):
            print("\n  Stopped before loading.\n")
            return 1

        banner("PHASES 1-3 — load, walk, split")
        try:
            report = session.run()
        except OperatorAbort as exc:
            banner("STOPPED BY THE OPERATOR")
            print(f"  at: {exc}\n")
            print(session.report())
            return 1
        except ClearanceViolation as exc:
            banner("REFUSED — geometry does not fit")
            print(exc)
            return 2

    banner("COMPLETE")
    print(report)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
