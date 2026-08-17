"""
run_16piece_split.py
────────────────────
16-PIECE SPLIT — DRY-RUN VERIFIED, NOT YET CONFIRMED ON HARDWARE

⚠ THIS IS NOT A RECORD OF A RUN THAT WORKED. It is a candidate.

The geometry below plans clean and has been checked every way a dry run can
check it -- clearance, leaf count, leaf size, pairwise separation, volume
equality by the activated-area proxy -- but no liquid has ever been through
it. Nothing here should be cited as a result, and the header stays as it is
until a live run is done and confirmed holding. At that point, and not
before: change this header, tag the commit `split-16piece-verified`, and say
what chip it ran on -- the way `run_8piece_split.py` does.

WHAT IS ACTUALLY NEW HERE, AND IT IS ONLY ONE THING
───────────────────────────────────────────────────
The first three stages ARE the verified 8-piece run: same load position, same
walk, same split position, same W -> H -> W prefix, frame for frame. This
script adds a fourth stage. If stages 0-2 misbehave, that is a regression in
something already proven and the interesting news is there, not at stage 3.

Stage 3 takes eight 10x5 pieces to sixteen 5x5 ones -- 1.232 x 1.232 mm, and
that is the FLOOR for a 20x20 droplet. 20 = 2^2 x 5, so four halvings is all
divisibility allows; a fifth would have to halve a 5, and the planner refuses
rather than guess which child gets the extra electrode. So this run answers
the last question this droplet can ask. If 5x5 does not hold, no 32-piece
tree from a 20x20 can either, whatever else changes.

Every parameter below is HARDCODED, for the same reason as in the 8-piece
script: the moment this file can be pointed at a different geometry it stops
being a record of anything.

Each run:
  1. Connect, power up, and verify the 45V rails read back
  2. Energise a 20x20 hold at row 5, col 55 and wait for the operator to load
  3. Walk the droplet 50 electrodes down column 55 to row 55, col 55
  4. Split W -> H -> W -> H into 16 pieces of 5x5, pausing for a piece count
     after each stage -- expect 2, then 4, then 8, then 16
  5. Print what the run claims and what it did not verify

NOTE — no camera, and what that costs
─────────────────────────────────────
Nothing here imports cv2, numpy or any calibration. Positions are electrode
indices commanded straight through ActivateElec, so no homography is involved
and none is needed. The consequence is that YOU are the only verification:
this API has no per-electrode readback, so the six y/n gates below -- load,
arrival, and a piece count after each of the four stages -- are the sole
evidence that anything actuated. Answering `n` at any gate stops the run.

The gates matter more here than at 8 pieces. Pieces this small are where a
neck that does not fully open, or a satellite thrown during the break, is
easiest to miss and hardest to see -- and the count you are asked for is the
only thing standing between that and a run that reads later as a success.

NOTE — dry run is the default
─────────────────────────────
Without --arm nothing is energised: the frames are planned, gated and logged
but SetPower/SetVolt/ActivateElec are never issued. Use that to rehearse the
prompts. Add --arm for a real run.

NOTE — relationship to microdrop/protocol.py
────────────────────────────────────────────
This file is a front end, not a reimplementation. All the split geometry --
the centred stretch, the centre-out erosion, the clearance gate, the tree --
lives in `microdrop.splitplan` and is driven by `microdrop.protocol`, both of
which are covered by the test suite. Nothing about the split is recomputed
here. What this file owns is the hardcoding, the presentation and the refusal
to run against the wrong rig.

Usage: python microdrop/run_16piece_split.py          (dry run)
       python microdrop/run_16piece_split.py --arm    (live)
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
# The load position, the walk and the split position are exactly the verified
# 8-piece ones -- unchanged on purpose, so the only variable in this run is the
# fourth split. Written out rather than read from splitplan's defaults for the
# same reason as in the 8-piece script: if a later change moves `split_root` or
# `DEFAULT_AXES`, this file must keep doing what it says, or refuse.
# See check_geometry().

LOAD_ROW, LOAD_COL = 5, 55      # where the operator loads the droplet
SPLIT_ROW, SPLIT_COL = 55, 55   # where the tree runs
DROPLET_H, DROPLET_W = 20, 20   # the starting droplet
AXES = ("W", "H", "W", "H")     # 4 stages -> 16 pieces

# What the above must produce. Taken from `python -m microdrop.protocol
# --plan-only --axes WHWH` on 2026-08-17, against splitplan at ebcb64e. These
# are DRY-RUN expectations: they pin the planner, they do not certify the
# physics. A mismatch means the planner changed under this script, and it stops
# rather than running a geometry nobody has looked at.
EXPECT_PIECES = 16
EXPECT_LEAF = (5, 5)            # electrodes; 1.232 x 1.232 mm at the 246.48um pitch
EXPECT_WALK_ELECTRODES = 50
EXPECT_APPROACH_FRAMES = 100
EXPECT_TREE_FRAMES = 159        # 1 hold + 100 approach + 159 = 260 frames, ~130s

STEP_DELAY_S = P.PROVEN_SETTLE_S    # 0.5s, csvvolcont.py:137
BAR = "=" * 68
RULE = "─" * 60

#: Carried into the run report, which is the only thing that outlives the
#: terminal. A transcript of confident yeses must not read six months from now
#: as though this geometry had been proven.
UNVERIFIED_NOTE = (
    "NOT HARDWARE-VERIFIED: this 16-piece geometry had never been run on a "
    "chip as of 2026-08-17. The operator answers below are the first evidence "
    "of anything. Do not cite this configuration as proven on the strength of "
    "one transcript.")


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

    Same guard as the 8-piece script, and it earns its place here for a
    slightly different reason. There the constants protect a run that is known
    to have worked. Here they protect the ONE claim this file can honestly
    make -- that what it will energise is what was checked in the dry run on
    2026-08-17. If `splitplan` has moved since, this script would be putting an
    unexamined geometry on a chip under a name that suggests otherwise.

    Returns the approach, so the caller does not have to reach back into
    `session.approach` and re-establish that it exists. See the refusal below.
    """
    plan, approach = session.plan, session.approach

    # Checked first and raised immediately rather than collected into
    # `problems` below, because it is a different kind of failure. Every other
    # check here detects `splitplan` moving under a script that pins a fixed
    # geometry. A missing approach cannot mean that: `SplitSession` only leaves
    # it None when `transport` is False or `approach_from` is None, both of
    # which are hardcoded above. So this fires for an edit to THIS file, and
    # says so. Raising here is also what lets the return type promise a real
    # Approach -- a type checker cannot tell that appending to `problems`
    # guarantees the `if problems` raise below.
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
              "  restore it, or re-check with `python -m microdrop.protocol\n"
              "  --plan-only --axes WHWH` and update the EXPECT_* constants at\n"
              "  the top of this file to match what you checked.\n")

    return approach


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the candidate 16-piece split. Dry run unless --arm. "
                    "This geometry is dry-run checked but NOT yet confirmed on "
                    "hardware.")
    ap.add_argument("--arm", action="store_true",
                    help="Energise electrodes. Without this nothing is sent to "
                         "the chip and no liquid moves.")
    args = ap.parse_args()

    banner("16-PIECE SPLIT — DRY-RUN VERIFIED, NOT CONFIRMED ON HARDWARE")
    print(f"  load     {DROPLET_H}x{DROPLET_W} at row {LOAD_ROW}, col {LOAD_COL}")
    print(f"  walk     {EXPECT_WALK_ELECTRODES} electrodes down column {LOAD_COL}"
          f" to row {SPLIT_ROW}, col {SPLIT_COL}")
    print(f"  split    {' -> '.join(AXES)}  ->  {EXPECT_PIECES} pieces of "
          f"{EXPECT_LEAF[0]}x{EXPECT_LEAF[1]}")
    print(f"  status   stages 0-2 are the verified 8-piece run; stage 3 is new")
    print(f"  mode     {'ARMED — electrodes will be energised' if args.arm else 'DRY RUN — nothing will be energised'}")

    # ── Rig ────────────────────────────────────────────────────────────────
    cfg = ChipConfig()
    backend = make_backend("auto", DEFAULT_DLL_DIR, DEFAULT_DLL_NAME,
                           cfg.rows, cfg.cols)
    is_fake = type(backend).__name__ == "FakeBackend"
    say("Rig", f"{type(backend).__name__}"
        + ("  <- NOT the hardware" if is_fake else f"  ({DEFAULT_DLL_DIR})"))

    if is_fake and args.arm:
        # A fake rig satisfies the rail check identically to a real one, so an
        # accidental fake armed run reads as a success that moved no liquid.
        # This cost a debugging session on 2026-08-13; it does not get to
        # happen again quietly. It would be worse here: this is the run that
        # decides whether 5x5 holds, and a fake one would answer yes.
        print("\n  The vendor DLL did not load, so --arm would energise nothing.\n"
              "  Almost always: you are on WSL/Linux, where the Windows x64 DLL\n"
              "  cannot load. Use the Windows interpreter:\n"
              "    .\\.venv\\Scripts\\python.exe microdrop\\run_16piece_split.py --arm\n")
        return 4

    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=args.arm,
                          step_delay_s=STEP_DELAY_S if args.arm else 0.0,
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
        transport=True,
        approach_from=SP.DropNode(id="d", parent=None, stage=0,
                                  height=DROPLET_H, width=DROPLET_W,
                                  row=LOAD_ROW, col=LOAD_COL),
        confirm=confirm,
        announce=lambda m: say("Split", m),
    )
    approach = check_geometry(session)
    session.notes.append(UNVERIFIED_NOTE)
    if not args.arm:
        session.notes.append(
            "DRY RUN: nothing was energised, so no liquid moved. The gate "
            "sequence and the geometry were exercised; nothing else was.")

    total = 1 + approach.n_frames + session.plan.n_frames
    say("Plan", f"{total} frames, ~{approach.duration_s() + session.plan.duration_s():.0f}s "
                f"of dwell at {STEP_DELAY_S}s")

    with chip:
        banner("PHASE 0 — power and rails")
        check = chip.verify_voltage()
        for line in check.summary().splitlines():
            say("Volts", line)
        if args.arm and not check.ok:
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
