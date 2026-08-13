"""Split parameters, traced to ``colormixing/csvvolcont.py``.

WHERE THIS DELIBERATELY DIVERGES FROM csvvolcont
================================================
Decided by the researcher 2026-08-13, after a stage-by-stage symmetry audit:
where csvvolcont's mechanics and true 50/50 halving conflict, SYMMETRY WINS.
csvvolcont dispenses from a stationary reservoir, which is a different
problem, and two of its details are actively wrong for halving:

1. ONE-SIDED NECK EROSION. csvvolcont pins the bridge's far edge and marches
   the near one, so the whole neck stays connected to one side and drains
   there. In a dispense that is the point -- the neck belongs to the
   reservoir. In a halving it hands ~44% of the stretched footprint to one
   child. Replaced by CENTRE-OUT erosion: the neck breaks in the middle and
   each half retracts into its own child. See `erode_steps`.

2. ORIGIN-ANCHORED STRETCH. csvvolcont grows in one direction because the
   reservoir cannot move. In a halving that makes one child travel the whole
   stretch while the other barely moves. Replaced by a CENTRE-ANCHORED
   stretch: both contact lines advance one electrode per frame, in opposite
   directions. See `stretch_steps` and `splitplan._stretch_origin`.

Both changes force an EVEN neck gap -- an odd gap cannot be halved on an
integer grid, in space or in erosion order -- which is why `stretch_to`
rounds to even (`_round_even`) rather than to nearest. That is the one place
a proven number moved: 20 -> 35 became 20 -> 36. See `_round_even`.

What is inherited unchanged: the stretch-then-split ORDER, the 1.75 stretch
ratio, one electrode per contact line per frame, the 0.5 s dwell, and holding
every live piece in every frame.

FOOTPRINT, AND WHAT IT NOW BUYS
===============================
Everything below is symmetric in ELECTRODE FOOTPRINT, and the tests verify that
exactly.

As of 2026-08-13 that also settles volume EQUALITY, under one assumption. The
method changed (researcher): equality is no longer pending a `ChipConfig.gap_um`
measurement plus imaging of the pieces, it is based on the activated electrode
area of each piece -- pixel size of the activated region, in electrode units.
The gap cancels out of a ratio (`V = A x g`, so `V_a/V_b = A_a/A_b`), so equal
footprint gives equal volume PROVIDED THE GAP IS UNIFORM across the two
children. That uniformity is unmeasured and is the assumption to watch; a
tilted or unevenly-compressed top plate breaks it. See
`splitplan.volume_equality` and `splitplan.VOLUME_EQUALITY_ASSUMPTIONS`.

What has NOT changed: no absolute volume. The proxy gives ratios, not
quantities, so the unmeasured gap still means no nanolitre figure exists here
or anywhere in the repo (objectives.md §2.4 q3), and the claim stays
UNVERIFIED against the rig -- it is a property of the plan, checkable against
`detector`'s electrode-unit blob areas but not yet checked. Centre-out erosion
is what makes the two counts equal in the first place; it removed the
systematic bias one-sided erosion built in.

WHY csvvolcont AND NOT 1pixsplit
================================
`1pixsplit.py` is not a reliable basis for this work (researcher, 2026-08-13)
and is not cited by anything here. This is not a new position for the repo --
`chiphealth/config.py` already reaches for csvvolcont for exactly this reason
where the two disagree on timing, calling it "the one legacy script that sets
voltage with no human in the loop, and therefore the only proven timing this
binding can actually match" (`volt_settle_s`, `power_settle_s`).

The mechanical difference that matters most for splitting:

    1pixsplit.py  step 3   one ActivateElec call patterns reservoir + piece
                           and asks the liquid to snap apart in a single
                           frame. Its own comments say "No neck loop", twice.

    csvvolcont.py step 3   the neck is eroded over `gap+1` frames, one
                           electrode per frame, and the liquid is given
                           0.5 s at each. The break is walked, not snapped.

Every parameter below is the csvvolcont value or a ratio derived from it.
Line numbers refer to `colormixing/csvvolcont.py` at commit 834d4b2.

WHAT csvvolcont DOES NOT COVER
==============================
Two gaps, both real, both flagged in the module that uses these values:

1. csvvolcont only ever splits along WIDTH. Every drop it creates is
   `MAIN_H = 10` tall from load to merge; height is never a split axis. It
   therefore supplies no proven numbers for a height-axis split, and an
   8-piece tree needs at least one (see `splitplan` module docstring).

2. csvvolcont DISPENSES -- it takes a 10-wide piece off a 15-wide reservoir
   remnant. It does not HALVE. A symmetric split is a different geometry and
   its placement is derived here from the ratios, not copied.

Both derivations are marked DERIVED below. Everything marked PROVEN is a
literal from the file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ── PROVEN: the split sequence, csvvolcont.split_and_move (L206-302) ──────────

# Load footprint. csvvolcont loads 10 tall x 20 wide (L221) -- the 20 is the
# extent along the axis it is about to split, and it is the number the rest of
# these ratios are relative to.
PROVEN_SPLIT_AXIS_EXTENT = 20

# Stretch before splitting: 20 -> 35, one electrode per frame, 15 frames
# (L230-235, `for i in range(1, 16)` giving widths 21..35).
PROVEN_STRETCH_TO = 35
STRETCH_RATIO = PROVEN_STRETCH_TO / PROVEN_SPLIT_AXIS_EXTENT  # 1.75

# Neck erosion. neck_start = start_col + MAIN_W = 17 (L214), neck_gap =
# PIECE_START_COL - neck_start = 30 - 17 = 13 (L215), opened over
# `range(neck_gap + 1)` = 14 frames (L240-259). The bridge's right edge stays
# pinned at col 29 while its left edge marches right, so the neck erodes from
# the reservoir side towards the piece rather than thinning uniformly.
#
# NOT INHERITED. Recorded as the trace of what csvvolcont does; this package
# erodes centre-out instead. See the divergence note in the module docstring.
PROVEN_NECK_GAP = 13

# Piece pad at the moment of separation: 10 wide (L72), i.e. half the parent
# extent. Note the pad deliberately LEADS the liquid -- the stretched drop
# reaches col 36, the piece pad reaches col 39, so three electrodes of pad sit
# ahead of the contact line pulling it forward.
PROVEN_PIECE_START_W = 10

# Translation while pinching: 25 frames (L73), width interpolated linearly and
# rounded (L267). `pinch_width` below is that expression, unchanged.
PROVEN_TRANSLATE_STEPS = 25

# Dwell after every ActivateElec (L137). Matches chiphealth SweepConfig
# step_delay_s, which cites the same 0.5 s across the working scripts.
PROVEN_SETTLE_S = 0.5

# Stage-boundary pauses: 1 s after a load, 2 s before each of stretch / split /
# move / deactivate (L224, L229, L239, L264, L280). These are not per-frame.
PROVEN_STAGE_PAUSE_S = 2.0

# Rails. 45/45/45 with the rest at 0 (L162-165), verified within +/-2 V
# (L182-183) after a 0.3 s settle (L168). Already encoded in
# chiphealth.config.ChipConfig; repeated here only as the trace.
PROVEN_VOLTS = (45, 45, 45, 0, 0, 0, 0, 0, 0)
PROVEN_VOLT_TOLERANCE = 2
PROVEN_VOLT_SETTLE_S = 0.3


def pinch_width(start_w: int, end_w: int, step: int, steps: int) -> int:
    """Width at `step` of a pinching translation. csvvolcont.py:267 verbatim.

    Kept as the original expression, including `round`'s banker's rounding, so
    that a plan for csvvolcont's own geometry reproduces csvvolcont's frames
    exactly rather than approximately.
    """
    return round(start_w - (start_w - end_w) * step / steps)


def _round_even(value: float) -> int:
    """Nearest EVEN integer, ties rounding up.

    Used only for DERIVED geometry, never for `pinch_width`.

    Even, not nearest, because a symmetric split needs an even neck gap twice
    over: the stretch is centred on the parent, so the surplus must divide
    evenly on either side, and the neck erodes centre-out, so it must divide
    evenly into two stubs. An odd gap makes both of those off-by-one, and a
    one-electrode bias is 20% of a child at the 5-electrode leaves.

    This is the one place a proven csvvolcont number moves. At the proven
    extent 20 the raw target is 20 x 1.75 = 35, which is odd; 34 and 36 are
    equidistant and the tie goes up, giving 36. That is +1 electrode of
    stretch (aspect 3.6 rather than 3.5 at the worst stage) and is the price
    of exact symmetry. `test_splitplan` pins it so the deviation stays visible.

    Ties round up rather than down so the stretch is never SHORTER than the
    proven one -- a thinner neck is the safer direction to err for a break.
    """
    lo = 2 * math.floor(value / 2)
    return lo if (value - lo) < (lo + 2 - value) else lo + 2


@dataclass(frozen=True)
class SplitParams:
    """Parameters for one symmetric split, scaled from the proven case.

    At `parent_extent = 20` -- csvvolcont's own case -- `stretch_to` comes out
    at 36 and `neck_gap` at 16, against csvvolcont's proven 35 and 13.

    The stretch is one electrode longer than proven, because 35 is odd and an
    odd surplus cannot be centred or halved on an integer grid (`_round_even`).

    The gap does not match and cannot: csvvolcont's 13 is the gap between a
    15-wide remnant and a 10-wide piece inside a 35-wide stretch, which is a
    dispense. A halving puts two 10-wide children inside a 36-wide stretch, so
    its gap is forced to 16. The DERIVED gap is the arithmetic consequence of
    halving, not a re-tuning of the proven one.
    """

    stretch_ratio: float = STRETCH_RATIO

    # Mirror csvvolcont's step 5, which re-energises a bridge from the
    # reservoir out to the departed piece and then retracts it one electrode at
    # a time (L277-299) -- but symmetrically: two stubs, one per child, each
    # retracting into its own child.
    #
    # OFF by default, and this is a real open question rather than a
    # preference. In csvvolcont that step runs AFTER the piece has already
    # travelled 25 columns away, so the bridge it builds spans 37 columns of
    # bare chip between two settled drops. Read one way it sweeps the trail
    # back into the reservoir; read another way the loop bounds are reversed
    # and it is re-opening a neck that step 3 already closed. Nothing in the
    # file says which, and a plan for 8 pieces would repeat it 7 times.
    # Defaulted off so it is a decision, not an inheritance.
    neck_retract: bool = False

    def stretch_to(self, parent_extent: int) -> int:
        """Full-stretch extent. Always even -- see `_round_even`."""
        s = _round_even(parent_extent * self.stretch_ratio)
        if s <= parent_extent:
            raise ValueError(
                f"stretch_ratio {self.stretch_ratio} does not stretch a "
                f"{parent_extent}-electrode parent ({parent_extent} -> {s}); "
                f"there would be no neck to erode"
            )
        return s

    def stretch_steps(self, parent_extent: int) -> int:
        """Frames to reach full stretch.

        DIVERGES from csvvolcont, which advances ONE contact line one
        electrode per frame (L230). The stretch here is centred, so BOTH
        contact lines advance one electrode per frame in opposite directions
        and the surplus is covered in half as many frames. The per-contact-line
        rate -- the thing the 0.5 s dwell is actually about -- is unchanged.
        """
        return self.neck_gap(parent_extent) // 2

    def erode_steps(self, parent_extent: int) -> int:
        """Frames to open the neck, counting the intact-neck frame.

        DIVERGES from csvvolcont's `range(neck_gap + 1)` (L240) for the same
        reason as `stretch_steps`: the neck parts in the middle and both new
        contact lines retreat one electrode per frame into their own child, so
        `gap` electrodes clear in `gap // 2` frames, plus the intact frame.
        """
        return self.neck_gap(parent_extent) // 2 + 1

    def child_extent(self, parent_extent: int) -> int:
        if parent_extent % 2:
            raise ValueError(
                f"symmetric split needs an even extent on the split axis, "
                f"got {parent_extent}"
            )
        return parent_extent // 2

    def neck_gap(self, parent_extent: int) -> int:
        """DERIVED. Two half-extent children at the two ends of the stretched
        extent leaves `stretch_to - parent_extent` between them.

        Always even, because `stretch_to` and `parent_extent` both are. The
        centred stretch spends `gap // 2` of it on each side of the parent, and
        the centre-out erosion clears `gap // 2` of it into each child.

        Cross-check against the only symmetric split in the repo: 1pixsplit's
        step 6 halves a 15-tall piece into two 5-tall halves with S2_GAP = 5.
        At `stretch_ratio=1.5` the formula here gives 6, not 5, because 15 is
        odd and gets rounded up to an even 16. One electrode apart, and the
        extra electrode is exactly what makes the split halvable. That file is
        not the basis for anything here; this only records where the two land.
        """
        return self.stretch_to(parent_extent) - parent_extent


DEFAULT = SplitParams()
