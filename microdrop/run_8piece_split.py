"""
run_8piece_split.py — 8 pieces of 10x5 from one 20x20 droplet.

⚠ NOT CONFIRMED ON HARDWARE. The last stage (4->8) was widened on 2026-08-17
after a live run failed to separate there. A green test suite is not
verification; do not restore a "verified" label until a live run parts all
eight pieces. Why 2.2, what it costs, and why widening buys margin rather than
fixing a root cause: docs/guides/separation-and-dwell-tuning.md

⚠ ALWAYS ARMED. There is no dry run. Opening the chip issues SetPower and
SetVolt, so THE RAILS COME UP BEFORE THE FIRST GATE IS ASKED. Answering `n` at
the phase 0 voltage gate stops the run before any electrode is energised, but
not before the supply is live. `--arm` is accepted and does nothing.
Hardware-free check: `python -m microdrop.protocol --plan-only`

Every parameter below is HARDCODED, and check_geometry() refuses to run if the
planner stops producing them -- a script that can be pointed at a different
geometry stops being a record of anything. Nothing about the split is
recomputed here: geometry lives in `microdrop.splitplan`, sequencing in
`microdrop.protocol`, both under test.

Operator gates, exit codes, and what a camera-free run cannot verify:
docs/guides/running-the-split-scripts.md

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
# Written out rather than read from splitplan's defaults on purpose: if a later
# change moves `split_root` or `DEFAULT_AXES`, this script must keep doing what
# it says or refuse. See check_geometry().

LOAD_ROW, LOAD_COL = 5, 55      # where the operator loads the droplet
SPLIT_ROW, SPLIT_COL = 55, 55   # where the tree runs
DROPLET_H, DROPLET_W = 20, 20   # the starting droplet
AXES = ("W", "H", "W")          # 3 stages -> 8 pieces

# Stage 2 (the last) only; stages 0 and 1 keep the proven 1.75 because they
# worked. docs/guides/separation-and-dwell-tuning.md
FINAL_STRETCH_RATIO = 2.2
STAGE_STRETCH_RATIOS = ((2, FINAL_STRETCH_RATIO),)

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

    A script that quietly ran a new geometry under the old name would destroy
    the thing it exists to preserve. Returns the approach so the caller need
    not re-establish that it exists.
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

    # Pinned separately so neither can drift into the other: the final gap is
    # what was widened, the early gap is what must NOT have been.
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
              "  `python -m microdrop.protocol --plan-only "
              f"--stretch-stage 2:{FINAL_STRETCH_RATIO}`\n"
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
    # Accepted and ignored, so old muscle memory does not abort a run at the
    # rig with `unrecognized arguments`.
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
        # A fake rig passes the rail check identically to a real one, so an
        # accidental fake run reads as a success that moved no liquid.
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
    # This script chooses the numbers; splitplan decides what frames they imply.
    session = SplitSession(
        chip=chip,
        root=SP.DropNode(id="d", parent=None, stage=0,
                         height=DROPLET_H, width=DROPLET_W,
                         row=SPLIT_ROW, col=SPLIT_COL),
        axes=AXES,
        cfg=cfg,
        sp=P.SplitParams(stage_stretch_ratios=STAGE_STRETCH_RATIOS),
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
