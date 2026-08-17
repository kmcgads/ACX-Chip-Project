"""
run_8piece_split.py
───────────────────
8-PIECE SPLIT — WIDENED FINAL STAGE, NOT YET CONFIRMED ON HARDWARE

⚠ THE "VERIFIED" LABEL HAS BEEN PULLED. It is not re-applied until a live run
confirms the 4->8 stage separates completely. Tests passing is not that.

WHY IT WAS PULLED — 2026-08-17
──────────────────────────────
A live run of the previously-verified configuration did NOT fully separate at
the last stage: the four 10x10 parents stretched and eroded, but the pieces
did not come completely apart. That contradicts the 2026-08-13 run this file
was built to reproduce, and the git tag `split-8piece-verified` should be read
with that in mind -- it records what was believed on that date, and the belief
did not survive contact with the chip a second time.

READ THE NEXT PARAGRAPH BEFORE TRUSTING THE FIX BELOW.

THE GEOMETRY WAS IDENTICAL ACROSS BOTH RUNS. Same script, same hardcoded
numbers, same plan, frame for frame. So whatever differed between a working
4->8 and a failing one, IT WAS NOT THE GEOMETRY. Widening the final stage, as
this file now does, does not address a root cause -- it buys margin against a
cause that has not been identified. Candidates that geometry cannot fix, and
that no dry run can rule out: droplet volume at load, filler oil, chip surface
state or residue from a prior run, actual rail voltage under load, dwell at
the smallest pieces, plate gap. If the widened stage also fails, the answer is
in that list and not in a larger number here.

WHAT CHANGED, AND ONLY THIS
───────────────────────────
The LAST stage (4->8) now stretches its 10-electrode parents to 22 instead of
18, opening the neck gap from 8 to 12 -- the two children of each split end up
12 electrodes apart instead of 8. Stages 0 and 1 are untouched, frame for
frame, because they worked: both still stretch 20 -> 36 at the proven 1.75
ratio over 17 frames.

Separation is not a margin that can be set. `SplitParams.neck_gap` is
`stretch_to(extent) - extent`, so the only lever on how far apart two children
land is how far their parent was stretched first. Widening therefore means
leaving the one number in `params.py` that came whole from a working script
(`STRETCH_RATIO = 1.75`, csvvolcont L230-235). At the last stage this file now
uses 2.2, which is off that evidence: it asks the same liquid to follow a
longer pad. That is the intended mechanism -- a thinner neck breaks more
readily -- but it is also how you get a satellite in the middle instead of a
clean break, and nothing here knows where that limit is.

WHY 2.2 AND NOT MORE. Widening pushes each child outwards, towards the
NEIGHBOURING group's child, so sibling separation and non-sibling separation
move in opposite directions. Measured at the fixed split position:

    ratio    sibling sep    nearest non-sibling    tree frames   violations
    1.75              8                      8             87            0
    2.2              12                      4            103            0
    2.4              14                      2            111            0
    2.6              16                      0            119            6

2.4 is available and puts the siblings 14 apart, but it leaves two settled
pieces from different groups only 2 electrodes apart, which is the planner's
own floor and a merge risk in the other direction. 2.6 collides outright.
2.2 is the largest step that keeps everything comfortable; going further needs
the four groups spread out first, which would mean changing the earlier stages
that already work.

Every parameter below is HARDCODED. There is no flag to change the position,
the axis order or the piece count, and that is the point: the moment this
script can be pointed at a different geometry it stops being a record of
anything.

Each run:
  1. Connect, power up, and verify the 45V rails read back
  2. Energise a 20x20 hold at row 5, col 55 and wait for the operator to load
  3. Walk the droplet 50 electrodes down column 55 to row 55, col 55
  4. Split W -> H -> W into 8 pieces of 10x5, pausing for a piece count
     after each stage. The last stage is the widened one -- that is the gate
     to look hardest at
  5. Print what the run claims and what it did not verify

NOTE — no camera, and what that costs
─────────────────────────────────────
Nothing here imports cv2, numpy or any calibration. Positions are electrode
indices commanded straight through ActivateElec, so no homography is involved
and none is needed. The consequence is that YOU are the only verification:
this API has no per-electrode readback, so the four y/n gates below are the
sole evidence that anything actuated. Answering `n` at any gate stops the run.

NOTE — THIS SCRIPT IS ALWAYS ARMED
──────────────────────────────────
There is no dry run. Running this file energises the chip: opening it issues
SetPower(True) and SetVolt, so THE RAILS COME UP BEFORE YOU ARE ASKED
ANYTHING. The first gate -- the phase 0 voltage confirmation -- happens with
45V already commanded, and answering `n` there stops the run before any
electrode is activated, but not before the supply is live.

`--arm` is still accepted so old muscle memory does not error out at the rig,
but it does nothing.

For a check that touches no hardware, this is not the script. Use:

    python -m microdrop.protocol --plan-only

which opens no USB handle, asks nothing and energises nothing.

NOTE — relationship to microdrop/protocol.py
────────────────────────────────────────────
This file is a front end, not a reimplementation. All the split geometry --
the centred stretch, the centre-out erosion, the clearance gate, the tree --
lives in `microdrop.splitplan` and is driven by `microdrop.protocol`, both of
which are covered by the test suite. Nothing about the split is recomputed
here. What this file owns is the hardcoding, the presentation and the refusal
to run against the wrong rig.

Usage: python microdrop/run_8piece_split.py    (live -- there is no other mode)
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

# ── The configuration ─────────────────────────────────────────────────────────
# Load, walk and split positions are the live-tested ones and are unchanged.
# The final-stage stretch is not -- see the header. Written out rather than read
# from splitplan's defaults on purpose: if a later change moves `split_root` or
# `DEFAULT_AXES` for 16- or 32-piece work, this script must keep doing what it
# says on the tin, or refuse. See check_geometry().

LOAD_ROW, LOAD_COL = 5, 55      # where the operator loads the droplet
SPLIT_ROW, SPLIT_COL = 55, 55   # where the tree runs
DROPLET_H, DROPLET_W = 20, 20   # the starting droplet
AXES = ("W", "H", "W")          # 3 stages -> 8 pieces

# Applies to the LAST stage only; stages 0 and 1 keep the proven 1.75. This is
# the single number that changed on 2026-08-17 and the only reason this file no
# longer says "verified". See SplitParams.final_stretch_ratio.
FINAL_STRETCH_RATIO = 2.2

# What the above must produce. A mismatch means the planner changed under this
# script, and it stops rather than running a geometry nobody has looked at.
EXPECT_PIECES = 8
EXPECT_LEAF = (10, 5)           # electrodes; 2.465 x 1.232 mm at the 246.48um pitch
EXPECT_WALK_ELECTRODES = 50
EXPECT_APPROACH_FRAMES = 100
EXPECT_TREE_FRAMES = 103        # was 87 at the proven ratio; 4 splits x 4 frames
EXPECT_FINAL_GAP = 12           # was 8. THE CHANGE. Pinned so it cannot drift
EXPECT_EARLY_GAP = 16           # stages 0-1, unchanged and must stay unchanged

STEP_DELAY_S = P.PROVEN_SETTLE_S    # 0.5s, csvvolcont.py:137
BAR = "=" * 68
RULE = "─" * 60


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

    The whole value of this script is being the record of a run that worked on
    hardware. If `splitplan` changes -- a different stretch ratio, a different
    erosion pattern, a re-tuned neck gap -- the numbers below move, and a
    script that quietly ran the new geometry under the old name would destroy
    exactly the thing it exists to preserve.

    Returns the approach, so the caller does not have to reach back into
    `session.approach` and re-establish that it exists. See the refusal below.
    """
    plan, approach = session.plan, session.approach

    # Checked first and raised immediately rather than collected into
    # `problems` below, because it is a different kind of failure. Every other
    # check here detects `splitplan` moving under a script that claims to be a
    # fixed record. A missing approach cannot mean that: `SplitSession` only
    # leaves it None when `transport` is False or `approach_from` is None, both
    # of which are hardcoded above. So this fires for an edit to THIS file, and
    # says so. Raising here is also what lets the return type promise a real
    # Approach -- a type checker cannot tell that appending to `problems`
    # guarantees the `if problems` raise below.
    if approach is None:
        raise SystemExit(
            "\n  REFUSING TO RUN: no approach was planned, so the droplet would\n"
            "  be split wherever it happened to be loaded rather than walked to\n"
            "  the verified split position. The SplitSession in main() must keep\n"
            f"  transport=True and approach_from set to row {LOAD_ROW}, col {LOAD_COL}.\n")

    leaves = {(n.height, n.width) for n in plan.leaves}
    problems = []

    if len(plan.leaves) != EXPECT_PIECES:
        problems.append(f"{len(plan.leaves)} pieces, expected {EXPECT_PIECES}")
    if leaves != {EXPECT_LEAF}:
        problems.append(f"leaf sizes {sorted(leaves)}, expected {EXPECT_LEAF}")
    if plan.n_frames != EXPECT_TREE_FRAMES:
        problems.append(f"{plan.n_frames} tree frames, expected {EXPECT_TREE_FRAMES}")

    # The two halves of the 2026-08-17 change, pinned separately so neither can
    # drift into the other. The final gap is what was widened; the early gap is
    # what must NOT have been, because those stages worked on hardware and the
    # whole point of a per-stage ratio is leaving them alone.
    last = len(AXES) - 1
    final_gaps = {s.neck_gap for s in plan.steps if s.stage == last}
    early_gaps = {s.neck_gap for s in plan.steps if s.stage < last}
    if final_gaps != {EXPECT_FINAL_GAP}:
        problems.append(f"final-stage neck gap {sorted(final_gaps)}, "
                        f"expected {EXPECT_FINAL_GAP}")
    if early_gaps != {EXPECT_EARLY_GAP}:
        problems.append(f"early-stage neck gap {sorted(early_gaps)}, expected "
                        f"{EXPECT_EARLY_GAP} -- the widening must apply to the "
                        f"LAST stage only; stages 0-1 are hardware-proven")
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
            "\n  REFUSING TO RUN: this is no longer the geometry this script\n"
            "  was written for.\n"
            + "".join(f"    - {p}\n" for p in problems)
            + "\n  microdrop/splitplan.py or params.py has changed since\n"
              "  2026-08-17. Either restore it, or re-check with\n"
              "  `python -m microdrop.protocol --plan-only --final-stretch "
              f"{FINAL_STRETCH_RATIO}`\n"
              "  and update the EXPECT_* constants at the top of this file to\n"
              "  match what you checked.\n")

    return approach


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the 8-piece split with the widened final stage. NOT "
                    "yet confirmed on hardware. ALWAYS ARMED: running "
                    "this script energises the chip. There is no dry run -- "
                    "use `python -m microdrop.protocol --plan-only` for a "
                    "check that touches no hardware.")
    # Accepted and ignored. Kept only so that typing the flag this script used
    # to need does not abort the run with `unrecognized arguments` at the rig.
    ap.add_argument("--arm", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    banner("8-PIECE SPLIT — WIDENED FINAL STAGE, NOT CONFIRMED ON HARDWARE")
    if args.arm:
        say("Note", "--arm is accepted but no longer needed; this script is "
                    "always armed.")
    print(f"  load     {DROPLET_H}x{DROPLET_W} at row {LOAD_ROW}, col {LOAD_COL}")
    print(f"  walk     {EXPECT_WALK_ELECTRODES} electrodes down column {LOAD_COL}"
          f" to row {SPLIT_ROW}, col {SPLIT_COL}")
    print(f"  split    {' -> '.join(AXES)}  ->  {EXPECT_PIECES} pieces of "
          f"{EXPECT_LEAF[0]}x{EXPECT_LEAF[1]}")
    print(f"  change   final stage stretches at {FINAL_STRETCH_RATIO} "
          f"(not {P.STRETCH_RATIO}); neck gap 8 -> {EXPECT_FINAL_GAP}. "
          f"Stages 0-1 unchanged")
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
        # A fake rig satisfies the rail check identically to a real one, so an
        # accidental fake run reads as a success that moved no liquid. This
        # cost a debugging session on 2026-08-13; it does not get to happen
        # again quietly. Now that there is no dry run, there is no reason at
        # all to be here on the fake backend, so this refuses unconditionally
        # rather than only when arming.
        print("\n  The vendor DLL did not load, so this run would energise\n"
              "  nothing while looking exactly like one that did.\n"
              "  Almost always: you are on WSL/Linux, where the Windows x64 DLL\n"
              "  cannot load. Use the Windows interpreter:\n"
              "    .\\.venv\\Scripts\\python.exe microdrop\\run_8piece_split.py\n")
        return 4

    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=True,
                          step_delay_s=STEP_DELAY_S,
                          volt_tolerance=cfg.volt_tolerance,
                          volt_settle_s=cfg.volt_settle_s,
                          power_settle_s=cfg.power_settle_s)

    # ── The run ────────────────────────────────────────────────────────────
    # Every geometric decision is delegated. This script chooses the numbers;
    # splitplan decides what frames they imply.
    session = SplitSession(
        chip=chip,
        root=SP.DropNode(id="d", parent=None, stage=0,
                         height=DROPLET_H, width=DROPLET_W,
                         row=SPLIT_ROW, col=SPLIT_COL),
        axes=AXES,
        cfg=cfg,
        sp=P.SplitParams(final_stretch_ratio=FINAL_STRETCH_RATIO),
        transport=True,
        approach_from=SP.DropNode(id="d", parent=None, stage=0,
                                  height=DROPLET_H, width=DROPLET_W,
                                  row=LOAD_ROW, col=LOAD_COL),
        confirm=confirm,
        announce=lambda m: say("Split", m),
    )
    approach = check_geometry(session)
    session.notes.append(
        f"FINAL STAGE WIDENED, NOT HARDWARE-VERIFIED: the last stage stretched "
        f"at {FINAL_STRETCH_RATIO} instead of the proven {P.STRETCH_RATIO}, "
        f"opening the neck gap from 8 to {EXPECT_FINAL_GAP} electrodes. Stages "
        f"0-1 are unchanged. This geometry had never been on a chip as of "
        f"2026-08-17, and it was reached by adding margin, not by identifying "
        f"why the proven geometry failed on a second run.")

    total = 1 + approach.n_frames + session.plan.n_frames
    say("Plan", f"{total} frames, ~{approach.duration_s() + session.plan.duration_s():.0f}s "
                f"of dwell at {STEP_DELAY_S}s")

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
