"""
run_8piece_split.py
───────────────────
VERIFIED 8-PIECE SPLIT — 2026-08-13

The known-good configuration, live-tested on chip trial01 and confirmed
holding. Tagged in git as `split-8piece-verified`. This script exists so that
run can be reproduced exactly, by name, without remembering flags — and so it
stays reproducible after work starts on 16 and 32 pieces.

Every parameter below is HARDCODED. There is no flag to change the position,
the axis order or the piece count, and that is the point: the moment this
script can be pointed at a different geometry it stops being the record of
what was verified.

Each run:
  1. Connect, power up, and verify the 45V rails read back
  2. Energise a 20x20 hold at row 5, col 55 and wait for the operator to load
  3. Walk the droplet 50 electrodes down column 55 to row 55, col 55
  4. Split W -> H -> W into 8 pieces of 10x5, pausing for a piece count
     after each stage
  5. Print what the run claims and what it did not verify

NOTE — no camera, and what that costs
─────────────────────────────────────
Nothing here imports cv2, numpy or any calibration. Positions are electrode
indices commanded straight through ActivateElec, so no homography is involved
and none is needed. The consequence is that YOU are the only verification:
this API has no per-electrode readback, so the four y/n gates below are the
sole evidence that anything actuated. Answering `n` at any gate stops the run.

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

Usage: python microdrop/run_8piece_split.py          (dry run)
       python microdrop/run_8piece_split.py --arm    (live)
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

# ── The verified configuration ────────────────────────────────────────────────
# These are the numbers that were live-tested. They are written out rather than
# read from splitplan's defaults on purpose: if a later change moves
# `split_root` or `DEFAULT_AXES` for 16- or 32-piece work, this script must
# keep doing what it says on the tin, or refuse. See check_geometry().

LOAD_ROW, LOAD_COL = 5, 55      # where the operator loads the droplet
SPLIT_ROW, SPLIT_COL = 55, 55   # where the tree runs
DROPLET_H, DROPLET_W = 20, 20   # the starting droplet
AXES = ("W", "H", "W")          # 3 stages -> 8 pieces

# What the above must produce. A mismatch means the planner changed under this
# script, and it stops rather than running an unverified geometry.
EXPECT_PIECES = 8
EXPECT_LEAF = (10, 5)           # electrodes; 2.465 x 1.232 mm at the 246.48um pitch
EXPECT_WALK_ELECTRODES = 50
EXPECT_APPROACH_FRAMES = 100
EXPECT_TREE_FRAMES = 87

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
    """Refuse to run if the planner no longer produces the verified geometry.

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
            "\n  REFUSING TO RUN: this is no longer the verified geometry.\n"
            + "".join(f"    - {p}\n" for p in problems)
            + "\n  microdrop/splitplan.py has changed since 2026-08-13. Either\n"
              "  restore it, or re-verify on hardware and update the EXPECT_*\n"
              "  constants at the top of this file to match what you verified.\n")

    return approach


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the verified 8-piece split. Dry run unless --arm.")
    ap.add_argument("--arm", action="store_true",
                    help="Energise electrodes. Without this nothing is sent to "
                         "the chip and no liquid moves.")
    args = ap.parse_args()

    banner("VERIFIED 8-PIECE SPLIT — 2026-08-13")
    print(f"  load     {DROPLET_H}x{DROPLET_W} at row {LOAD_ROW}, col {LOAD_COL}")
    print(f"  walk     {EXPECT_WALK_ELECTRODES} electrodes down column {LOAD_COL}"
          f" to row {SPLIT_ROW}, col {SPLIT_COL}")
    print(f"  split    {' -> '.join(AXES)}  ->  {EXPECT_PIECES} pieces of "
          f"{EXPECT_LEAF[0]}x{EXPECT_LEAF[1]}")
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
        # happen again quietly.
        print("\n  The vendor DLL did not load, so --arm would energise nothing.\n"
              "  Almost always: you are on WSL/Linux, where the Windows x64 DLL\n"
              "  cannot load. Use the Windows interpreter:\n"
              "    .\\.venv\\Scripts\\python.exe microdrop\\run_8piece_split.py --arm\n")
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
