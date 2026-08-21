"""
symmovressplit.py — split, move, and merge back into a growing reservoir.

✅ CONFIRMED ON HARDWARE 2026-08-21. The full sequence ran live and completed:
all four splits, all four transits, and ALL THREE MERGES, down to the final 5x5
piece. Operator-confirmed at every gate. This is the FIRST merge sequence this
repository has ever confirmed on a chip.

⚠ ONE RUN. n = 1, and this repo has been burned by exactly that: the 8-piece
tree was labelled "VERIFIED WORKING ON HARDWARE" on 2026-08-13 and the
identical script failed to separate on 2026-08-17, four days later, at which
point the label was withdrawn. Treat this as one confirmed run, not as a
reproducibility claim. A second successful run is what would make it one.
Record the chip id with the run (CONTRIBUTING.md#labelling-hardware-verification
asks for it and it is not captured here yet).

WHAT THE RUN DID AND DID NOT ESTABLISH
======================================
The distinction matters more than usual here, because a camera-free run can
confirm a sequence completed without confirming what the liquid did.

    ESTABLISHED    The mechanics work end to end. Three merges into a growing
                   reservoir -- something with no implementation, no tests and
                   no precedent in this repo beyond three equal pieces meeting
                   at a point -- produced an operator-confirmed result. The
                   commanded-area approximation (360 for 350, 380 for 375) did
                   not prevent it.
    NOT ESTABLISHED
                   That the merges COALESCED rather than leaving adjacent
                   bodies. That the reservoir holds 350 then 375 electrodes of
                   liquid. That nothing was shed in transit. All three are
                   unobservable without readback or a camera, and remain so
                   after a perfect run -- they are limits of the instrument,
                   not doubts about the sequence.

WHERE THE MERGE STEPS NOW STAND
===============================
    SPLITTING          `microdrop.splitplan.split_frames`, 1,096 lines and 86
                       tests, symmetry checked on EVERY frame, volume equality
                       computed. A 20x20 tree separated on hardware 2026-08-13
                       (and then failed to reproduce on 08-17).
    MOVING             `basics/dropsplitoff.py` step 4 and
                       `basics/mdmixwithmerge.py` move_pieces_to_meet(). Both
                       ran on hardware, and now so has this reuse of them.
    MERGING            Ran on hardware 2026-08-21 -- but STILL no
                       implementation in `microdrop/`, STILL no tests, STILL no
                       planner support. The behaviour is now evidenced; the
                       CODE is not. A regression here would be caught by an
                       operator's eye at the rig and by nothing else.

  * MERGES REMAIN THE HARDEST STEP TO GATE, and a successful run does not
    change that. After a split an operator can count pieces. After a merge,
    "did these actually coalesce, or are they merely adjacent?" is a weaker
    judgement by eye, with no readback and no camera. Keep looking hard.
  * THE COMMANDED AREA DOES NOT MATCH THE LIQUID after merges 2 and 3. See
    "MERGE ARITHMETIC" below. A deliberate, accepted approximation -- and one
    the 2026-08-21 run suggests is tolerable in practice.

⚠ ALWAYS ARMED. There is no dry run. Opening the chip issues SetPower and
SetVolt, so THE RAILS COME UP BEFORE THE FIRST GATE IS ASKED. `--arm` is
accepted and does nothing.

WHY THIS IS PROCEDURAL AND NOT A `plan_tree` CALL
=================================================
`plan_tree` splits EVERY live piece at EVERY stage -- a 20x20 on ("W","H","W")
gives eight equal 10x5 leaves, and there is no way to hold the reservoir out.
This sequence is asymmetric (the reservoir stops splitting and starts
absorbing), so the tree planner cannot express it.

What IS reused is the primitive underneath: `split_frames(parent, axis, held)`
performs one symmetric split while naming held pieces in every frame. That
keeps the three splits on tested, centre-out, volume-equal code. Only the
movement and the merges are hand-built, and both follow a proven script.

MOVEMENT MECHANIC, COPIED FROM dropsplitoff.py STEP 4
=====================================================
One electrode per frame, whole-rectangle re-declaration, EVERY LIVE DROP NAMED
IN EVERY FRAME:

    activate([
        Drop(H, W, row, RESERVOIR_COL),      # held -- re-declared identically
        Drop(H, W, row, current_col),        # mover -- col advances by 1
    ])

Deliberately NOT `chiphealth.sweep.grow_release`. The caterpillar is the better
idea on paper and has never run on a chip; this pattern has. Opposite-direction
movement comes from mdmixwithmerge.move_pieces_to_meet(), which moves one piece
up and another down IN THE SAME FRAME -- so the two movers here are
SIMULTANEOUS, not sequential. There is no collision risk between them because
they are always on disjoint ROWS, which `check_geometry` verifies frame by
frame rather than assuming.

WHY THE DROPLET WALKS RIGHT BEFORE THE FIRST SPLIT
==================================================
The centred stretch pushes each child outward by half the surplus. A W-split of
a 20-wide parent stretches to 36, so it needs 8 clear electrodes on the left.
Loaded at col 5 that reaches col -3 and goes off the array. Walking 8 right to
col 13 first both clears the edge AND lands the left child at cols 5-14 -- so
the reservoir ends up exactly where it was asked to be.

MERGE ARITHMETIC
================
    reservoir after split 1     20 x 10  = 200 electrodes
    + A1 (10x10)                        = 300  commanded 20x15 = 300  EXACT
    + B1 (10x5)                         = 350  commanded 20x18 = 360  +2.9%
    + C1 (5x5)                          = 375  commanded 20x19 = 380  +1.3%

Merges 2 and 3 have no rectangle of height 20 with exactly their area, and
`Drop` can only express rectangles, so the width is rounded UP and the
reservoir is commanded slightly larger than its contents (researcher decision,
option A). The over-commit does not compound -- it IMPROVES, 2.9% to 1.3%,
because 375 sits closer to a multiple of 20 than 350 does. Liquid is tracked
separately from the commanded rectangle in `build_sequence` for exactly this
reason: deriving each merge from the previous COMMANDED area would fold the
over-commit into the arithmetic and inflate it every round.

For scale, splits in this repo run at 45-60% areal coverage during the stretch,
so a 1-3% over-command is small -- but it is a real mismatch and the run report
says so.

WHY THE LAST TRANSIT GOES LEFT
==============================
Every transit before it moves the continuing piece further right. The fourth
cannot: C2 starts at cols 115-119 and +25 would put it at 140-144, off a
128-wide array. Shortening that transit to the 9 electrodes that would fit
would make it the one un-proven movement distance in the run. Instead C2 moves
the proven 25 electrodes BACK toward the reservoir, into space the returning
pieces have vacated. It is the only transit where both movers travel the same
direction; they remain on disjoint rows, so nothing changes about collision
safety.

Operator gates, exit codes, and what a camera-free run cannot verify:
docs/guides/running-the-split-scripts.md

Usage: python microdrop/testing/symmovressplit.py   (live -- no other mode)
"""

import argparse
import os
import sys

# Run directly, not as `python -m`. This file sits two levels inside the
# package, so the PROJECT ROOT is three dirnames up.
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from chiphealth import clearance as CL
from chiphealth.actuation import ChipController, Drop, make_backend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import DEFAULT_DLL_DIR, DEFAULT_DLL_NAME, ChipConfig
from microdrop import params as P
from microdrop import splitplan as SP

# ── The configuration ─────────────────────────────────────────────────────────

LOAD_ROW, LOAD_COL = 55, 5      # where the operator loads the droplet
DROPLET_H, DROPLET_W = 20, 20   # the starting droplet
SPLIT1_COL = 13                 # LOAD_COL + 8; see "WHY THE DROPLET WALKS"
TRANSIT = 25                    # dropsplitoff.py step 4's proven distance

STEP_DELAY_S = P.PROVEN_SETTLE_S    # 0.5s, csvvolcont.py:137
BAR = "=" * 68
RULE = "─" * 60


# ── Sequence construction ─────────────────────────────────────────────────────
# A phase is (name, kind, frames). A frame is a tuple of (h, w, row, col)
# rectangles -- every live drop, every frame. Built up front and validated in
# full before the chip is opened, so a geometry error costs a message rather
# than a loaded chip.

def _rect(node) -> tuple[int, int, int, int]:
    return (node.height, node.width, node.row, node.col)


def _walk(mover, held, axis, distance, sign):
    """One-electrode-per-frame translation. dropsplitoff.py step 4.

    `mover` and every entry of `held` are (h, w, row, col). Yields one frame
    per electrode, each naming the mover at its new position plus every held
    rectangle re-declared identically.
    """
    h, w, row, col = mover
    frames = []
    for i in range(1, distance + 1):
        if axis == "col":
            pos = (h, w, row, col + sign * i)
        else:
            pos = (h, w, row + sign * i, col)
        frames.append(tuple(held) + (pos,))
    return frames


def _walk_pair(a, b, held, a_axis, a_dist, a_sign, b_axis, b_dist, b_sign):
    """Two movers in opposite directions, IN THE SAME FRAME.

    mdmixwithmerge.move_pieces_to_meet() is the precedent: one piece up and one
    down in a single ActivateElec. Distances may differ -- the shorter mover
    clamps at its destination and is then simply held, which is what the frame
    already expresses since every drop is re-declared every frame.
    """
    frames = []
    for i in range(1, max(a_dist, b_dist) + 1):
        ai, bi = min(i, a_dist), min(i, b_dist)
        ah, aw, ar, ac = a
        bh, bw, br, bc = b
        apos = ((ah, aw, ar, ac + a_sign * ai) if a_axis == "col"
                else (ah, aw, ar + a_sign * ai, ac))
        bpos = ((bh, bw, br, bc + b_sign * bi) if b_axis == "col"
                else (bh, bw, br + b_sign * bi, bc))
        frames.append(tuple(held) + (apos, bpos))
    return frames


def build_sequence(sp: P.SplitParams):
    """Every frame this run will command, with the state it produces.

    Returns a list of (name, kind, frames, note). Pure geometry -- no DLL, no
    USB, no chip. This is what `check_geometry` validates and what `main`
    drives.
    """
    phases = []

    # ── load ──────────────────────────────────────────────────────────────
    drop = (DROPLET_H, DROPLET_W, LOAD_ROW, LOAD_COL)
    phases.append(("load", "hold", [(drop,)],
                   f"{DROPLET_H}x{DROPLET_W} held at row {LOAD_ROW}, col {LOAD_COL}"))

    # ── walk right to the split position ──────────────────────────────────
    dist = SPLIT1_COL - LOAD_COL
    phases.append(("walk to split position", "walk",
                   _walk(drop, (), "col", dist, +1),
                   f"{dist} electrodes right, col {LOAD_COL} -> {SPLIT1_COL}"))
    parent = SP.DropNode(id="d", parent=None, stage=0, height=DROPLET_H,
                         width=DROPLET_W, row=LOAD_ROW, col=SPLIT1_COL)

    # ── split 1: halve the width -> reservoir + piece A ───────────────────
    step, (res, piece_a) = SP.split_frames(parent, "W", [], sp)
    phases.append(("split 1 (W): 20x20 -> two 20x10", "split",
                   [tuple(_rect(d) for d in f.drops) for f in step.frames],
                   f"reservoir at col {res.col}, piece A at col {piece_a.col}, "
                   f"neck gap {step.neck_gap}"))
    R = _rect(res)
    # Liquid, tracked separately from the COMMANDED rectangle. They coincide
    # until merge 2 over-commands; after that, deriving the next merge from
    # R[0]*R[1] would compound the over-commit into the arithmetic.
    res_liquid = R[0] * R[1]

    # ── transit 1: A right, reservoir stationary ──────────────────────────
    A = _rect(piece_a)
    phases.append(("transit 1: A moves right", "transit",
                   _walk(A, (R,), "col", TRANSIT, +1),
                   f"{TRANSIT} electrodes right, reservoir held"))
    A = (A[0], A[1], A[2], A[3] + TRANSIT)

    # ── align A so its H-split children land on the reservoir's rows ──────
    # The centred H stretch throws children 8 rows outward. Dropping A by 8
    # first puts the returning child exactly on the reservoir's top half, so
    # its journey home is a pure sideways move with no second leg.
    phases.append(("align A for the height split", "walk",
                   _walk(A, (R,), "row", 8, +1),
                   "8 electrodes down, so the returning child lands on the "
                   "reservoir's rows"))
    A = (A[0], A[1], A[2] + 8, A[3])

    # ── split 2: halve the height -> A1 (returns) + A2 (goes on) ──────────
    a_node = SP.DropNode(id="a", parent=None, stage=0,
                         height=A[0], width=A[1], row=A[2], col=A[3])
    r_node = SP.DropNode(id="R", parent=None, stage=0,
                         height=R[0], width=R[1], row=R[2], col=R[3])
    step, (a1, a2) = SP.split_frames(a_node, "H", [r_node], sp)
    phases.append(("split 2 (H): 20x10 -> two 10x10", "split",
                   [tuple(_rect(d) for d in f.drops) for f in step.frames],
                   f"A1 at row {a1.row} (returns), A2 at row {a2.row} "
                   f"(continues), neck gap {step.neck_gap}"))
    A1, A2 = _rect(a1), _rect(a2)

    # ── transit 2: A1 home, A2 onward, SIMULTANEOUSLY ─────────────────────
    a1_home = R[3] + R[1]              # touch the reservoir's right edge
    a1_dist = A1[3] - a1_home
    phases.append(("transit 2: A1 left / A2 right (same frames)", "transit",
                   _walk_pair(A1, A2, (R,), "col", a1_dist, -1,
                              "col", TRANSIT, +1),
                   f"A1 {a1_dist} left to col {a1_home}, A2 {TRANSIT} right; "
                   f"disjoint rows so they cannot collide"))
    A1 = (A1[0], A1[1], A1[2], a1_home)
    A2 = (A2[0], A2[1], A2[2], A2[3] + TRANSIT)

    # ── merge 1: reservoir absorbs A1. EXACT area ─────────────────────────
    res_liquid += A1[0] * A1[1]
    R = (R[0], -(-res_liquid // R[0]), R[2], R[3])  # 20 x 15
    phases.append(("MERGE 1: reservoir absorbs A1", "merge",
                   [(R, A2)],
                   f"commanded {R[0]}x{R[1]} = {R[0] * R[1]}, liquid "
                   f"{res_liquid} -- EXACT"))

    # ── split 3: halve A2's width -> B1 (returns) + B2 (goes on) ──────────
    a2_node = SP.DropNode(id="a2", parent=None, stage=0,
                          height=A2[0], width=A2[1], row=A2[2], col=A2[3])
    r_node = SP.DropNode(id="R", parent=None, stage=0,
                         height=R[0], width=R[1], row=R[2], col=R[3])
    step, (b1, b2) = SP.split_frames(a2_node, "W", [r_node], sp)
    phases.append(("split 3 (W): 10x10 -> two 10x5", "split",
                   [tuple(_rect(d) for d in f.drops) for f in step.frames],
                   f"B1 at col {b1.col} (returns), B2 at col {b2.col} "
                   f"(continues), neck gap {step.neck_gap}"))
    B1, B2 = _rect(b1), _rect(b2)

    # ── align B1 onto the reservoir's rows ────────────────────────────────
    b1_rows = R[2] + R[0] - B1[0]      # sit on the reservoir's bottom half
    up = B1[2] - b1_rows
    phases.append(("align B1 for its return", "walk",
                   _walk(B1, (R, B2), "row", up, -1),
                   f"{up} electrodes up to row {b1_rows}, onto the "
                   f"reservoir's rows"))
    B1 = (B1[0], B1[1], b1_rows, B1[3])

    # ── transit 3: B1 home, B2 onward, SIMULTANEOUSLY ─────────────────────
    b1_home = R[3] + R[1]
    b1_dist = B1[3] - b1_home
    phases.append(("transit 3: B1 left / B2 right (same frames)", "transit",
                   _walk_pair(B1, B2, (R,), "col", b1_dist, -1,
                              "col", TRANSIT, +1),
                   f"B1 {b1_dist} left to col {b1_home}, B2 {TRANSIT} right"))
    B1 = (B1[0], B1[1], B1[2], b1_home)
    B2 = (B2[0], B2[1], B2[2], B2[3] + TRANSIT)

    # ── merge 2: reservoir absorbs B1. APPROXIMATE area ───────────────────
    res_liquid += B1[0] * B1[1]
    R = (R[0], -(-res_liquid // R[0]), R[2], R[3])  # 20 x 18
    phases.append(("MERGE 2: reservoir absorbs B1", "merge",
                   [(R, B2)],
                   f"commanded {R[0]}x{R[1]} = {R[0] * R[1]}, liquid "
                   f"{res_liquid} -- OVER-COMMANDED by "
                   f"{R[0] * R[1] - res_liquid} "
                   f"({(R[0] * R[1] / res_liquid - 1) * 100:.1f}%)"))

    # ── split 4: halve B2's height -> C1 (returns) + C2 (goes on) ─────────
    # MUST be the height axis. B2 is 10x5 and 5 is odd, so a width split would
    # need 2.5 and `child_extent` refuses it rather than rounding. The H
    # stretch also happens to cost no COLUMN space -- it grows rows 77-94 and
    # leaves B2 at cols 115-119 -- which is why a 4th split fits at all this
    # close to the edge.
    b2_node = SP.DropNode(id="b2", parent=None, stage=0,
                          height=B2[0], width=B2[1], row=B2[2], col=B2[3])
    r_node = SP.DropNode(id="R", parent=None, stage=0,
                         height=R[0], width=R[1], row=R[2], col=R[3])
    step, (c1, c2) = SP.split_frames(b2_node, "H", [r_node], sp)
    phases.append(("split 4 (H): 10x5 -> two 5x5", "split",
                   [tuple(_rect(d) for d in f.drops) for f in step.frames],
                   f"C1 at row {c1.row} (returns), C2 at row {c2.row} "
                   f"(continues), neck gap {step.neck_gap}"))
    C1, C2 = _rect(c1), _rect(c2)

    # ── align C1 onto the reservoir's rows ────────────────────────────────
    c1_rows = R[2] + R[0] - C1[0]
    up = C1[2] - c1_rows
    phases.append(("align C1 for its return", "walk",
                   _walk(C1, (R, C2), "row", up, -1),
                   f"{up} electrodes up to row {c1_rows}"))
    C1 = (C1[0], C1[1], c1_rows, C1[3])

    # ── transit 4: C1 home, C2 also LEFT ──────────────────────────────────
    # The one transit where both movers go the SAME way. Continuing right
    # would put C2 at cols 140-144, off a 128-wide array. Rather than shorten
    # the transit to a token 9 electrodes, C2 moves back toward the reservoir
    # into the space the returning pieces vacated -- the proven 25 electrodes,
    # in the only direction with room. Still one frame for both, and still
    # collision-free because the two are always on disjoint rows.
    c1_home = R[3] + R[1]
    c1_dist = C1[3] - c1_home
    phases.append(("transit 4: C1 left / C2 left (same frames)", "transit",
                   _walk_pair(C1, C2, (R,), "col", c1_dist, -1,
                              "col", TRANSIT, -1),
                   f"C1 {c1_dist} left to col {c1_home}, C2 {TRANSIT} LEFT "
                   f"(right would leave the array)"))
    C1 = (C1[0], C1[1], C1[2], c1_home)
    C2 = (C2[0], C2[1], C2[2], C2[3] - TRANSIT)

    # ── merge 3: reservoir absorbs C1 ─────────────────────────────────────
    res_liquid += C1[0] * C1[1]
    R = (R[0], -(-res_liquid // R[0]), R[2], R[3])  # 20 x 19
    phases.append(("MERGE 3: reservoir absorbs C1", "merge",
                   [(R, C2)],
                   f"commanded {R[0]}x{R[1]} = {R[0] * R[1]}, liquid "
                   f"{res_liquid} -- OVER-COMMANDED by "
                   f"{R[0] * R[1] - res_liquid} "
                   f"({(R[0] * R[1] / res_liquid - 1) * 100:.1f}%)"))

    return phases, R, C2


# ── What the above must produce ───────────────────────────────────────────────

EXPECT_RESERVOIR = (20, 19, 55, 5)      # final reservoir, 380 commanded
EXPECT_FAR_PIECE = (5, 5, 90, 90)       # C2, the piece that never came back
EXPECT_FINAL_LIQUID = 375               # reservoir contents
EXPECT_TOTAL_LIQUID = 400               # conserved from the 20x20
EXPECT_PHASES = 16
EXPECT_FRAMES = 310


def check_geometry(phases, reservoir, far_piece):
    """Refuse to run unless every frame is on-grid, overlap-free, and expected.

    Stronger than the sibling runners' guard, because this sequence is
    hand-built rather than emitted by `plan_tree`: nothing upstream has already
    checked it. Every frame is measured, not sampled.
    See CONTRIBUTING.md#the-check_geometry-pattern.
    """
    cfg = ChipConfig()
    problems: list[str] = []
    total = 0

    for name, kind, frames, _note in phases:
        for i, frame in enumerate(frames):
            total += 1
            boxes = [(r, r + h - 1, c, c + w - 1) for h, w, r, c in frame]
            for (h, w, r, c), box in zip(frame, boxes):
                if not CL.measure([box], cfg.rows, cfg.cols).ok:
                    problems.append(
                        f"{name} frame {i}: {h}x{w} at row {r}, col {c} "
                        f"is off the array")
            for x in range(len(boxes)):
                for y in range(x + 1, len(boxes)):
                    r0, r1, c0, c1 = boxes[x]
                    s0, s1, d0, d1 = boxes[y]
                    if r0 <= s1 and s0 <= r1 and c0 <= d1 and d0 <= c1:
                        problems.append(
                            f"{name} frame {i}: {frame[x]} overlaps {frame[y]}")

    if len(phases) != EXPECT_PHASES:
        problems.append(f"{len(phases)} phases, expected {EXPECT_PHASES}")
    if total != EXPECT_FRAMES:
        problems.append(f"{total} frames, expected {EXPECT_FRAMES}")
    if reservoir != EXPECT_RESERVOIR:
        problems.append(f"reservoir ends {reservoir}, expected {EXPECT_RESERVOIR}")
    if far_piece != EXPECT_FAR_PIECE:
        problems.append(f"far piece ends {far_piece}, expected {EXPECT_FAR_PIECE}")

    # Liquid conservation. The commanded reservoir is deliberately larger than
    # its contents after merge 2, so this counts LIQUID, not commanded area.
    liquid = EXPECT_FINAL_LIQUID + far_piece[0] * far_piece[1]
    if liquid != EXPECT_TOTAL_LIQUID:
        problems.append(f"liquid accounts for {liquid} electrodes, expected "
                        f"{EXPECT_TOTAL_LIQUID} -- the sequence is losing or "
                        f"inventing droplet")

    if problems:
        raise SystemExit(
            "\n  REFUSING TO RUN: the sequence does not match what this script\n"
            "  was written for.\n"
            + "".join(f"    - {p}\n" for p in problems[:12])
            + ("    ... and more\n" if len(problems) > 12 else "")
            + "\n  microdrop/splitplan.py or params.py has changed, or the\n"
              "  layout constants above were edited. Re-derive before running.\n")
    return total


# ── Helpers ───────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{BAR}\n  {title}\n{BAR}")


def say(tag: str, message: str) -> None:
    print(f"  [{tag}] {message}")


def confirm(question: str, detail: str = "") -> bool:
    """A real y/n gate. Anything but y/n re-asks; `n` stops the run."""
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


GATE = {
    "load": "Is the 20x20 droplet loaded at row 55, col 5 and filling the "
            "rectangle?",
    "split 1 (W): 20x20 -> two 20x10": "TWO 20x10 pieces, fully separated?",
    "transit 1: A moves right": "Has piece A arrived, with the reservoir "
                                "completely stationary and nothing left behind?",
    "split 2 (H): 20x10 -> two 10x10": "TWO 10x10 pieces, fully separated?",
    "MERGE 1: reservoir absorbs A1":
        "Has A1 COALESCED into the reservoir -- one body, not two touching "
        "pieces -- and has A2 moved right? (This worked on 2026-08-21, but "
        "coalesced-vs-adjacent is the hardest call in the run to make by eye. "
        "Look hard before answering y.)",
    "split 3 (W): 10x10 -> two 10x5": "TWO 10x5 pieces, fully separated?",
    "MERGE 2: reservoir absorbs B1":
        "Has B1 COALESCED into the reservoir, and B2 moved right? (Same call "
        "as merge 1, and the reservoir is now commanded slightly larger than "
        "its contents.)",
    "split 4 (H): 10x5 -> two 5x5": "TWO 5x5 pieces, fully separated? These "
                                    "are the smallest pieces in the run.",
    "MERGE 3: reservoir absorbs C1":
        "Has C1 COALESCED into the reservoir, and C2 moved LEFT? (Note C2 "
        "moves left here, not right -- the only transit that does.)",
}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split a 20x20 into a reservoir and pieces, move pieces "
                    "right, and merge two of them back into the reservoir. "
                    "Confirmed on hardware 2026-08-21 (one run). The merge "
                    "steps have run, but still have no implementation or "
                    "tests in microdrop/. ALWAYS ARMED: running this "
                    "energises the chip.")
    ap.add_argument("--arm", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    sp = P.DEFAULT
    phases, reservoir, far_piece = build_sequence(sp)
    total = check_geometry(phases, reservoir, far_piece)

    banner("SPLIT / MOVE / MERGE — CONFIRMED ON HARDWARE 2026-08-21 (n=1)")
    if args.arm:
        say("Note", "--arm is accepted but not needed; always armed.")
    for name, kind, frames, note in phases:
        say(kind.upper()[:6], f"{name}  ({len(frames)} frames) — {note}")
    say("Plan", f"{total} frames, ~{total * STEP_DELAY_S:.0f}s of dwell at "
                f"{STEP_DELAY_S}s")
    say("Note", "the three MERGE phases completed live on 2026-08-21. They "
                "still have no tested implementation behind them, and "
                "coalesced-vs-adjacent remains unverifiable by eye. See the "
                "module docstring.")

    cfg = ChipConfig()
    backend = make_backend("auto", DEFAULT_DLL_DIR, DEFAULT_DLL_NAME,
                           cfg.rows, cfg.cols)
    is_fake = type(backend).__name__ == "FakeBackend"
    say("Rig", f"{type(backend).__name__}"
        + ("  <- NOT the hardware" if is_fake else f"  ({DEFAULT_DLL_DIR})"))
    if is_fake:
        print("\n  The vendor DLL did not load, so this run would energise\n"
              "  nothing while looking exactly like one that did.\n"
              "  Use the Windows interpreter:\n"
              "    .\\.venv\\Scripts\\python.exe "
              "microdrop\\testing\\symmovressplit.py\n")
        return 4

    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=True,
                          step_delay_s=STEP_DELAY_S,
                          volt_tolerance=cfg.volt_tolerance,
                          volt_settle_s=cfg.volt_settle_s,
                          power_settle_s=cfg.power_settle_s)

    log: list[tuple[str, str]] = []
    with chip:
        banner("PHASE 0 — power and rails")
        check = chip.verify_voltage()
        for line in check.summary().splitlines():
            say("Volts", line)
        if not check.ok:
            print("\n  Rails do not match what was commanded. Refusing.\n")
            return 3
        if not confirm("Is the voltage connection verified and good?"):
            print("\n  Stopped before loading.\n")
            return 1

        try:
            for name, kind, frames, note in phases:
                banner(f"{kind.upper()} — {name}")
                say("Note", note)
                for frame in frames:
                    chip.activate([Drop(h, w, r, c) for h, w, r, c in frame])
                question = GATE.get(name)
                if question:
                    ok = confirm(question)
                    log.append((question, "yes" if ok else "no"))
                    if not ok:
                        banner("STOPPED BY THE OPERATOR")
                        print(f"  at: {name}\n")
                        return 1
        except ClearanceViolation as exc:
            banner("REFUSED — geometry does not fit")
            print(exc)
            return 2

    banner("COMPLETE")
    print(f"  reservoir {reservoir[0]}x{reservoir[1]} at row {reservoir[2]}, "
          f"col {reservoir[3]} — {EXPECT_FINAL_LIQUID} electrodes of liquid "
          f"in a {reservoir[0] * reservoir[1]}-electrode command")
    print(f"  far piece {far_piece[0]}x{far_piece[1]} at row {far_piece[2]}, "
          f"col {far_piece[3]}")
    print("\n  NOT VERIFIED THIS RUN:")
    print("    These are limits of a camera-free rig, not doubts about the")
    print("    sequence. They stayed true on 2026-08-21, when it completed.")
    print("    - that any of the three merges actually coalesced rather than")
    print("      leaving touching bodies. No readback, no camera; a gate is an eye.")
    print("    - that the reservoir contains 375 electrodes of liquid. That is")
    print("      arithmetic from the plan, not a measurement.")
    print("    - that nothing was shed in transit. 400 electrodes went in;")
    print("      whether 400 came out is unobservable here.")
    print("    - that this reproduces. One confirmed run, 2026-08-21. The")
    print("      8-piece tree was confirmed once and failed four days later.")
    for q, a in log:
        print(f"    gate: {a:3s}  {q[:70]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
