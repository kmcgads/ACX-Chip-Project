"""
6pixsplit.py — 4 pieces of 3x3 from one 6x6 droplet.

⚠ NOT CONFIRMED ON HARDWARE. This is the SMALLEST piece this repo has ever
planned -- 3x3 is 9 electrodes, against the 16-piece tree's 5x5 (25) and
microtest1.py's best real result of 5x3 (15). Treat a run as an experiment
about whether a 3x3 piece can be parted at all, not as a procedure. Do not add
a "verified" label until a live run parts all four pieces; see
CONTRIBUTING.md#labelling-hardware-verification.

⚠ TWO LIVE RUNS HAVE FAILED TO SEPARATE. Run 1 at the proven 1.75 stretch and
0.5s dwell did not fully separate. Run 2, with both stages widened to 2.2 and
stage 1 slowed to 1.0s, failed at STAGE 0 -- the first split did not produce
two pieces at all. Stage 0 has since been widened again, to 4.0. See the tuning
block below for the sequence.

WHY MORE STRETCH IS PROBABLY THE WRONG KNOB
===========================================
Recorded here because the next person to reach for a bigger ratio should see
the argument against it first.

Stage 0 is the gentlest split in this repository. Compare it against the ONLY
split this planner has ever separated on hardware -- the 20x20 tree's stage 0,
which parted on 2026-08-13 -- using areal coverage, liquid area over activated
area, which is what decides whether the liquid can still SPAN a stretch:

    configuration                     liquid  activated  coverage  aspect
    20x20 stage 0, r=1.75                400        720       56%    1.80   SEPARATED
    6x6   stage 0, r=1.75 (run 1)         36         60       60%    1.67   failed
    6x6   stage 0, r=2.2  (run 2)         36         84       43%    2.33   failed
    6x6   stage 0, r=4.0  (now)           36        144       25%    4.00   ?

At 1.75 this stage was GENTLER THAN THE ONE THAT WORKED on both measures --
more coverage, less aspect -- and it still failed. That is close to
dispositive: the plan is not what is wrong. Widening moves coverage the WRONG
WAY, asking 36 electrodes of liquid to span a 144-electrode region. A stretch
thins the neck only if the liquid reaches both ends.

What actually changed between the split that works and this one is SCALE: 36
electrodes of liquid against 400, and 3-electrode children against 10. The
open, unmeasured variables that scale with it:

  * RAIL VOLTAGE UNDER LOAD. `InquireVolt` reads nine GLOBAL rails and says
    nothing about what a 6-electrode region sees. Rail 3 read 0 V on every
    armed chip-health attempt on 2026-08-10 and that fault was never resolved.
    A weak supply moves a 400-electrode droplet and stalls a 36-electrode one.
  * DROPLET VOLUME AT LOAD. The load gate is a person eyeballing a 1.48 mm
    square. Under-fill and there is not enough liquid to span any stretch;
    over-fill and it bulges instead of necking. At this size the relative
    error of "looks right" is large.
  * THE PLATE GAP, still unmeasured (`ChipConfig.gap_um is None`). A 3x3 piece
    is 0.739 mm across. If the gap is a meaningful fraction of that, the slab
    approximation the whole volume model rests on stops holding.
  * FILLER OIL AND SURFACE STATE, not modelled anywhere in this repo.

CHEAPEST WAY TO SETTLE IT: a scale ladder. Run this same two-stage H,W tree at
20x20, 16x16, 12x12, 8x8 and 6x6 at the PROVEN 1.75, and find the size where it
stops working. That isolates droplet size from every rig variable, needs no new
code -- `python -m microdrop.protocol --plan-only --axes HW` plans each one --
and turns "small splits fail" into a number.

RULED OUT: MISSING OR MISTIMED DEACTIVATION
===========================================
Checked 2026-08-20, because "the neck never switches off" is a natural
suspicion for a split that will not part. It is not what is happening.

`ActivateElec(rows, cols, count, Drop*)` REPLACES THE ENTIRE 128x128 FRAME on
every call. There is no deactivate function -- the vendor exposes seven exports
and none of them is one (`actuation.REQUIRED_EXPORTS`), so an explicit
switch-off is not even expressible. Anything absent from a frame is off.
Confirmed four ways: the export list; `FakeBackend.activate_elec` doing
`self.frame = list(drops)`, an assignment; analysis.md SS2's disassembly ("sets
the entire 128x128 frame in one shot", the DLL looping the array in 16-byte
strides bounded by `count`); and dropsplitoff.py's own "Step 5: Deactivate neck
column by column", which calls plain `activate()` with a SHRINKING bridge and
finally omits it -- there is no off-call in the proven script either.

Stage 0's 19 frames, traced: frames 0-8 grow 8x6 -> 24x6 (+12 electrodes each,
6 per side, centred); frame 9 patterns into two 3x6 children plus two 9x6 neck
stubs; frames 10-17 shrink both stubs one row per side per frame (-12 each,
de-energising by omission); frame 18 drops the stubs entirely. The neck retreats
at one electrode per side per frame, the finest granularity available, and each
frame holds for the full dwell.

So this repo does the same thing dropsplitoff does, and slightly more carefully:
centre-out from two stubs rather than one-sided from one bridge. Nothing is
missing and there is nothing to add.

THE DECIDING ARGUMENT: the identical code path, with the identical
deactivation-by-omission, SEPARATED A 20x20 ON HARDWARE on 2026-08-13. A broken
switch-off would have broken that run too. Deactivation is also not a separately
timed event -- it is the same USB write as the activation, so there is no
ordering between "off" and "on" to get wrong and no window where both are live.

⚠ ALWAYS ARMED. There is no dry run. Opening the chip issues SetPower and
SetVolt, so THE RAILS COME UP BEFORE THE FIRST GATE IS ASKED. Answering `n` at
the phase 0 voltage gate stops the run before any electrode is energised, but
not before the supply is live. `--arm` is accepted and does nothing.
Hardware-free check: `python -m microdrop.protocol --plan-only --axes HW`

THE AXIS SEQUENCE, AND WHY IT IS NOT WHAT IT LOOKS LIKE
=======================================================
The end state -- 6x6 -> two 3x6 -> four 3x3 -- needs ALTERNATING axes, not the
same axis twice. The arithmetic is short and worth keeping here, because "split
the same way twice" is the natural way to describe this shape and it is wrong:

    6 = 2 x 3.  ONE factor of two per axis.

So each axis can be halved exactly once. Halving H takes 6 -> 3; halving H
again would need 3 -> 1.5, and `SplitParams.child_extent` refuses an odd extent
rather than rounding. The planner does support consecutive same-axis splits in
general -- a 20x20 on ("W", "W") gives four 20x5 -- so nothing needed changing.
It simply cannot apply to a 6x6, and no change to splitplan would make it.

    stage 0   axis H   6x6  -> two 3x6     halves the HEIGHT, width stays 6
    stage 1   axis W   3x6  -> two 3x3     halves the WIDTH,  height stays 3

THE 3-SIDE IS NEVER SPLIT, which is the real requirement. Stage 0 creates it;
stage 1 acts on the other axis, the one still at 6. `check_geometry()` pins the
per-stage axis so a future edit cannot quietly point stage 1 at the 3.

Sizes are HxW throughout, matching Drop(height, width, row, col): axis "H"
halves the height, axis "W" halves the width.

Every parameter below is HARDCODED, and check_geometry() refuses to run if the
planner stops producing them -- a script that can be pointed at a different
geometry stops being a record of anything. Nothing about the split is
recomputed here: geometry lives in `microdrop.splitplan`, sequencing in
`microdrop.protocol`, both under test.

Operator gates, exit codes, and what a camera-free run cannot verify:
docs/guides/running-the-split-scripts.md

Usage: python microdrop/testing/6pixsplit.py   (live -- there is no other mode)
"""

import argparse
import os
import sys

# Run directly, not as `python -m` -- the filename starts with a digit and is
# not an importable module name. This file sits two levels inside the package,
# so the PROJECT ROOT is three dirnames up, not two as in the sibling runners.
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

# Load and split share a ROW, so the walk is one straight leg along row 55 --
# no corner. Was (5, 55), which shared a COLUMN and walked vertically; the two
# are transposes and cost exactly the same 50 electrodes. The 44 clear
# electrodes between the two footprints (load ends at col 10, split starts at
# col 55) are checked by `check_geometry`, not assumed.
LOAD_ROW, LOAD_COL = 55, 5      # where the operator loads the droplet
SPLIT_ROW, SPLIT_COL = 55, 55   # where the tree runs
DROPLET_H, DROPLET_W = 6, 6     # the starting droplet
AXES = ("H", "W")               # 2 stages -> 4 pieces. See the module docstring

# ── Tuning, after two live runs failed to separate ────────────────────────────
# Two knobs, deliberately separate, because widening and dwelling are two
# different hypotheses about why a split fails to part and setting both on one
# stage gives a result nobody can attribute (`params.SplitParams`).
#
# HISTORY, so the numbers are readable as a sequence rather than a guess:
#   run 1   both stages 1.75, 0.5s        did not fully separate
#   run 2   both stages 2.2, stage 1 1.0s STAGE 0 did not separate AT ALL
#   now     stage 0 -> 4.0, stage 1 unchanged
#
# WIDENING. Stage 0 only this time, on request: 6 x 4.0 = 24 exactly, so its
# stretch_to goes 14 -> 24 (the +10 electrodes asked for) and its neck gap
# 8 -> 18. Anything in 3.9-4.1 rounds to the same 24; 3.8 gives 22, 4.2 gives
# 26. Stage 1 is untouched at 2.2 -- the two are independent by design.
#
# Widening costs nothing in SEPARATION here, unlike the 8- and 16-piece trees.
# There, pushing a child outwards pushed it towards the NEIGHBOURING GROUP's
# child, so sibling and non-sibling separation moved in opposite directions.
# This tree has four leaves in a 2x2 grid and no neighbouring group, so both
# gaps only ever grow. Zero violations at every ratio tried.
#
#     ratio   stretch_to   gap   stage 0 aspect   stage 1 aspect
#      1.75       10        4         1.67             3.33     run 1
#      2.2        14        8         2.33             4.67     run 2; stage 1 still here
#      2.4        14        8         2.33             4.67     identical to 2.2
#      2.6        16       10         2.67             5.33
#      3.0        18       12         3.00             6.00
#      4.0        24       18         4.00             8.00     stage 0 now
#      3.6        22       16         3.67             7.33     stage 1 ceiling
#
# 4.0 IS SAFE ON STAGE 0 AND WOULD NOT BE ON STAGE 1. splitplan records 7.2
# as "where liquid breaks up and throws satellites unbidden". Stage 0 stretches
# a 6-wide parent, so 24/6 = 4.00 -- comfortable. Stage 1 stretches a 3-wide
# parent, so the same ratio would give 24/3 = 8.00, PAST the threshold. This is
# exactly why the two knobs are separate, and why 4.0 must not be copied across.
#
# APPLIED AS REQUESTED, AND EXPECTED NOT TO HELP. See "WHY MORE STRETCH IS
# PROBABLY THE WRONG KNOB" in the module docstring.
STAGE_STRETCH_RATIOS = ((0, 4.0), (1, 2.2))

# DWELL: stage 1 only, unchanged. Stage 0 is NOT slowed, so it remains the
# dwell control -- the one variable still isolated after two rounds of
# widening. Additive, so a 0.5s baseline becomes 1.0s on stage 1.
#
# Reading the outcome:
#   stage 0 parts now         the stretch was the binding constraint after all,
#                             and the coverage argument below is wrong
#   stage 0 still fails       STOP WIDENING. Three ratios spanning 60% to 25%
#                             areal coverage have now failed; the cause is not
#                             in the plan. Instrument the rig instead
#   stage 0 throws satellites the trade-off landed badly; go back to 2.2 and
#                             look at volume and voltage
STAGE_EXTRA_SETTLE_S = ((1, 0.5),)

# What the above must produce. A mismatch means the planner changed under this
# script, and it stops rather than running a geometry nobody has looked at.
EXPECT_PIECES = 4
EXPECT_LEAF = (3, 3)            # electrodes; 0.739 x 0.739 mm at the 246.48um pitch
EXPECT_WALK_ELECTRODES = 50
EXPECT_APPROACH_FRAMES = 100
EXPECT_TREE_FRAMES = 37         # stage 0 is 19 frames, each stage-1 split 9
EXPECT_GAP_BY_STAGE = {0: 18, 1: 8}   # stage 0 stretch_to 24, stage 1 stretch_to 14
EXPECT_STAGE_AXES = {0: "H", 1: "W"}

# Pinned so the two knobs cannot silently merge. The widening must be on BOTH
# stages and the extra dwell on stage 1 ONLY -- if a later edit slows stage 0
# as well, the control is gone and the next run answers nothing.
EXPECT_STRETCH_BY_STAGE = {0: 4.0, 1: 2.2}
EXPECT_EXTRA_SETTLE_BY_STAGE = {0: 0.0, 1: 0.5}

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
    # moved. Raising is also what lets the return type promise a real Approach.
    if approach is None:
        raise SystemExit(
            "\n  REFUSING TO RUN: no approach was planned, so the droplet would\n"
            "  be split wherever it happened to be loaded rather than walked to\n"
            "  the split position. The SplitSession in main() must keep\n"
            f"  transport=True and approach_from set to row {LOAD_ROW}, col {LOAD_COL}.\n")

    leaves = {(n.height, n.width) for n in plan.leaves}
    problems = []

    if len(plan.leaves) != EXPECT_PIECES:
        problems.append(f"{len(plan.leaves)} pieces, expected {EXPECT_PIECES}")
    if leaves != {EXPECT_LEAF}:
        problems.append(f"leaf sizes {sorted(leaves)}, expected {EXPECT_LEAF}")
    if plan.n_frames != EXPECT_TREE_FRAMES:
        problems.append(f"{plan.n_frames} tree frames, expected {EXPECT_TREE_FRAMES}")

    gaps = {s.stage: s.neck_gap for s in plan.steps}
    if gaps != EXPECT_GAP_BY_STAGE:
        problems.append(f"neck gaps by stage {gaps}, expected "
                        f"{EXPECT_GAP_BY_STAGE} -- the two stages are no longer "
                        f"widened independently")

    # The two knobs, pinned separately. Widening on both stages, extra dwell on
    # stage 1 only: that asymmetry is what makes stage 0 a control, and a run
    # that lost it would look identical in the report while answering nothing.
    n = len(AXES)
    stretch = {s: session.sp.for_stage(s, n).stretch_ratio for s in range(n)}
    settle = {s: session.sp.for_stage(s, n).extra_settle_s for s in range(n)}
    if stretch != EXPECT_STRETCH_BY_STAGE:
        problems.append(f"stretch ratios {stretch}, expected "
                        f"{EXPECT_STRETCH_BY_STAGE}")
    if settle != EXPECT_EXTRA_SETTLE_BY_STAGE:
        problems.append(f"extra dwell {settle}, expected "
                        f"{EXPECT_EXTRA_SETTLE_BY_STAGE} -- stage 0 must stay "
                        f"un-slowed as the dwell control")

    # THE CHECK THIS SCRIPT EXISTS FOR. Stage 1 must act on the axis still at
    # 6, never on the 3 that stage 0 created. Pinning the per-stage axis is
    # what stops an edit to AXES or to splitplan's ordering from producing a
    # tree that asks for an odd halving -- or worse, one that happens to be
    # legal but is no longer the shape described above.
    stage_axes = {s.stage: s.axis for s in plan.steps}
    if stage_axes != EXPECT_STAGE_AXES:
        problems.append(f"stage axes {stage_axes}, expected {EXPECT_STAGE_AXES} "
                        f"-- stage 1 must halve the 6-side, not the 3-side")
    for s in plan.steps:
        parent = plan.nodes[s.parent_id]
        extent = parent.height if s.axis == "H" else parent.width
        if extent != 6:
            problems.append(f"stage {s.stage} splits a {extent}-extent, "
                            f"expected 6 (an odd extent cannot be halved)")

    if approach.electrodes != EXPECT_WALK_ELECTRODES:
        problems.append(f"walk is {approach.electrodes} electrodes, "
                        f"expected {EXPECT_WALK_ELECTRODES}")
    if approach.n_frames != EXPECT_APPROACH_FRAMES:
        problems.append(f"{approach.n_frames} approach frames, "
                        f"expected {EXPECT_APPROACH_FRAMES}")

    # The 50-electrode cost holds only because load and split share a row, so
    # the walk is one straight leg. Move the load off row 55 and the planner
    # adds a corner: further to travel, and a turn taken with the droplet at
    # its full 6x6. Pinned because the frame count alone would not say why.
    walk_rows = {d.row for f in approach.frames for d in f.drops}
    if walk_rows != {SPLIT_ROW}:
        problems.append(f"the walk touches rows {sorted(walk_rows)}, expected "
                        f"only row {SPLIT_ROW} -- load and split must share a "
                        f"row so the approach has no corner")

    # Load and split footprints must not overlap, and the corridor between
    # them must be real. Both are 6 wide on the same rows, so this is just
    # column arithmetic -- but it is the check that would have caught a load
    # position accidentally placed on top of the tree.
    load_last_col = LOAD_COL + DROPLET_W - 1
    corridor = SPLIT_COL - load_last_col - 1
    if corridor < 1:
        problems.append(f"load occupies cols {LOAD_COL}-{load_last_col} and "
                        f"the split starts at col {SPLIT_COL}: they overlap or "
                        f"touch, leaving no corridor")
    if plan.violations:
        problems.append(f"{len(plan.violations)} geometry violation(s)")

    if problems:
        raise SystemExit(
            "\n  REFUSING TO RUN: this is no longer the geometry this script\n"
            "  was written for.\n"
            + "".join(f"    - {p}\n" for p in problems)
            + "\n  microdrop/splitplan.py or params.py has changed. Either\n"
              "  restore it, or re-check with\n"
              "  `python -m microdrop.protocol --plan-only --axes HW`\n"
              "  and update the EXPECT_* constants at the top of this file to\n"
              "  match what you checked.\n")

    return approach


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split one 6x6 droplet into four 3x3 pieces. NOT confirmed "
                    "on hardware, and the smallest pieces this repo has "
                    "planned. ALWAYS ARMED: running this script energises the "
                    "chip. There is no dry run -- use `python -m "
                    "microdrop.protocol --plan-only --axes HW` for a check "
                    "that touches no hardware.")
    # Accepted and ignored, so old muscle memory does not abort a run at the
    # rig with `unrecognized arguments`.
    ap.add_argument("--arm", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    banner("6x6 -> FOUR 3x3 — NOT CONFIRMED ON HARDWARE")
    if args.arm:
        say("Note", "--arm is accepted but not needed; this script is always "
                    "armed.")
    print(f"  load     {DROPLET_H}x{DROPLET_W} at row {LOAD_ROW}, col {LOAD_COL}")
    print(f"  walk     {EXPECT_WALK_ELECTRODES} electrodes along row {LOAD_ROW}"
          f" to row {SPLIT_ROW}, col {SPLIT_COL}  (straight leg, no corner)")
    print(f"  stage 0  axis H   {DROPLET_H}x{DROPLET_W} -> two 3x6   "
          f"(halves the height; width stays 6)   stretch 2.2, dwell "
          f"{P.PROVEN_SETTLE_S}s  <- CONTROL")
    print(f"  stage 1  axis W   3x6 -> two 3x3   "
          f"(halves the width; the 3-side is never split)   stretch 2.2, "
          f"dwell {P.PROVEN_SETTLE_S + 0.5}s")
    print(f"  change   stage 0 stretch 2.2 -> 4.0 (neck gap 8 -> "
          f"{EXPECT_GAP_BY_STAGE[0]}) after it failed to separate at all. "
          f"Stage 1 unchanged at 2.2 / gap {EXPECT_GAP_BY_STAGE[1]}, still the "
          f"only slowed stage")
    print(f"  WARN note   stage 0 is now at 25% areal coverage (36 electrodes of "
          f"liquid over a 144-electrode stretch). Everything that has ever "
          f"separated on this rig ran at 45-56%. See the module docstring.")
    print(f"  result   {EXPECT_PIECES} pieces of "
          f"{EXPECT_LEAF[0]}x{EXPECT_LEAF[1]}  (0.739 x 0.739 mm, "
          f"{EXPECT_LEAF[0] * EXPECT_LEAF[1]} electrodes each)")
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
              "    .\\.venv\\Scripts\\python.exe microdrop\\testing\\6pixsplit.py\n")
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
    session.notes.append(
        f"UNTESTED GEOMETRY: 6x6 halved to four {EXPECT_LEAF[0]}x{EXPECT_LEAF[1]} "
        f"pieces of {EXPECT_LEAF[0] * EXPECT_LEAF[1]} electrodes each. This is "
        f"smaller than anything this repo has parted on a chip -- the 16-piece "
        f"tree bottoms out at 5x5 and microtest1.py reached 5x3.")
    session.notes.append(
        f"WIDENED TWICE AFTER TWO FAILED RUNS, NOT HARDWARE-VERIFIED: stage 0 "
        f"now stretches at 4.0 (neck gap {EXPECT_GAP_BY_STAGE[0]}) after it "
        f"failed to separate at all at 2.2; stage 1 is unchanged at 2.2 (gap "
        f"{EXPECT_GAP_BY_STAGE[1]}) and remains the only slowed stage at "
        f"{P.PROVEN_SETTLE_S + 0.5}s per frame. THE GEOMETRIC EVIDENCE ARGUES "
        f"AGAINST THIS HELPING: at the proven {P.STRETCH_RATIO} this stage ran "
        f"at 60% areal coverage and 1.67 aspect, both GENTLER than the 20x20 "
        f"split that separated on hardware 2026-08-13 (56%, 1.80), and it "
        f"still failed. Stage 0 is now at 25% coverage, half of anything that "
        f"has ever worked. If this run also fails, stop widening and "
        f"instrument the rig -- rail voltage under load, droplet volume at "
        f"load, and the unmeasured plate gap are the open variables.")

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
