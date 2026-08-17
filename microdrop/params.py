"""Split parameters, traced to ``colormixing/csvvolcont.py``.

Every value below is either PROVEN -- a literal from that file -- or DERIVED
from its ratios for a geometry it never performs. Which is which, the line
numbers, and why csvvolcont rather than `1pixsplit.py`:
docs/guides/provenance.md

WHERE THIS DIVERGES FROM csvvolcont, AND WHY IT MUST
====================================================
Researcher decision 2026-08-13: where csvvolcont's mechanics and a true 50/50
halving conflict, SYMMETRY WINS. csvvolcont dispenses from a stationary
reservoir, which is a different problem, and two of its details are actively
wrong for halving:

1. ONE-SIDED NECK EROSION drains the whole neck into one side. In a dispense
   the neck belongs to the reservoir; in a halving it hands ~44% of the
   stretched footprint to one child. Replaced by CENTRE-OUT erosion.
2. ORIGIN-ANCHORED STRETCH makes one child travel the whole surplus while the
   other barely moves. Replaced by a CENTRE-ANCHORED stretch.

Both force an EVEN neck gap -- an odd gap cannot be halved on an integer grid,
in space or in erosion order -- which is why `stretch_to` rounds to even rather
than to nearest, and is the one place a proven number moved (20 -> 35 became
20 -> 36).

Inherited unchanged: the stretch-then-split ORDER, the 1.75 ratio, one
electrode per contact line per frame, the 0.5s dwell, and holding every live
piece in every frame.

TWO GAPS csvvolcont DOES NOT COVER, both flagged where they are used:

1. It only ever splits along WIDTH -- every drop in it is 10 tall from load to
   merge. An 8-piece tree needs at least one height split, so the height-axis
   behaviour is a DERIVATION and is the main thing hardware has not agreed to.
2. It DISPENSES rather than halves, so a symmetric split's placement is derived
   from the ratios, not copied.

VOLUME EQUALITY rests on activated electrode area, and the plate gap cancels
out of a ratio -- but only if the gap is UNIFORM across the compared pieces,
which is unmeasured. The claim therefore stays UNVERIFIED against the rig: it
is a property of the PLAN, not a measurement of liquid. No absolute volume is
claimed here or anywhere in this repo. See docs/guides/volume-equality.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

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

    # csvvolcont's step 5 (L277-299), mirrored: two stubs, one per child.
    # OFF by default because it is a real open question -- read one way that
    # step sweeps the trail back into the reservoir, read another the loop
    # bounds are reversed and it re-opens a neck step 3 already closed. Nothing
    # in that file says which, and 8 pieces would repeat it 7 times.
    neck_retract: bool = False

    #: Per-stage stretch ratio overrides, as (stage_index, ratio) pairs. Empty
    #: means every stage uses `stretch_ratio`. A tuple of pairs rather than a
    #: dict so the dataclass stays frozen and hashable.
    #:
    #: THIS IS THE ONLY LEVER ON SEPARATION. `neck_gap` is `stretch_to(e) - e`,
    #: so how far apart two children land is fully determined by how far their
    #: parent was stretched first. There is no margin parameter to widen.
    #:
    #: PER STAGE, NEVER PER PIECE. Each split must be mirror-symmetric about its
    #: parent's centre line (`splitplan._stretch_origin` refuses an odd surplus;
    #: `TestSymmetry` checks every frame). Unequal placement would also give the
    #: two neck stubs different lengths, draining the neck unevenly -- and
    #: `volume_equality` would NOT catch it, because it counts electrodes and
    #: both children stay the same size.
    #:
    #: Any override is off the proven evidence, and what bounds it is the ASPECT
    #: RATIO at full stretch, not chip margin.
    #: docs/guides/separation-and-dwell-tuning.md
    stage_stretch_ratios: tuple[tuple[int, float], ...] = ()

    #: EXTRA dwell per frame, in seconds, on top of the controller's step delay.
    #:
    #: ADDITIVE, NOT ABSOLUTE. A dry run sets `ChipController.step_delay_s` to 0
    #: so a plumbing check does not sit through the proven dwell; an absolute
    #: per-stage dwell would override that zero and resurrect that bug. As an
    #: addend it cannot -- the controller only sleeps when its baseline is > 0.
    extra_settle_s: float = 0.0

    #: Per-stage `extra_settle_s` overrides. Same shape and rules as
    #: `stage_stretch_ratios`, and deliberately a SEPARATE field: widening and
    #: dwelling are two different hypotheses about why a split fails to part,
    #: and setting both on one stage gives a result nobody can attribute.
    #: docs/guides/separation-and-dwell-tuning.md#the-dwell-experiment
    stage_extra_settle_s: tuple[tuple[int, float], ...] = ()

    def for_stage(self, stage: int, n_stages: int) -> "SplitParams":
        """The parameters that apply at `stage` of an `n_stages` tree.

        Identity for every stage with no override. Returning a substituted copy
        rather than branching inside `split_frames` keeps the stage rule in one
        place, and keeps `split_frames` a function of the parameters it is
        handed.

        The copy clears the overrides, so the result is a plain single-ratio
        parameter set. Applying `for_stage` to it again is a no-op rather than
        a second substitution.

        Out-of-range stage indices RAISE rather than being ignored. Silently
        doing nothing is the wrong failure for this: an override aimed at a
        stage that does not exist means the caller believes it widened -- or
        slowed -- a split it did not, which is precisely the belief that gets a
        geometry run on a chip under the wrong name.
        """
        ratios = dict(self.stage_stretch_ratios)
        settles = dict(self.stage_extra_settle_s)
        for name, mapping in (("stage_stretch_ratios", ratios),
                              ("stage_extra_settle_s", settles)):
            bad = sorted(s for s in mapping if not 0 <= s < n_stages)
            if bad:
                raise ValueError(
                    f"{name} targets stage(s) {bad}, but this tree has "
                    f"{n_stages} stage(s), numbered 0 to {n_stages - 1}"
                )
        return replace(
            self,
            stretch_ratio=ratios.get(stage, self.stretch_ratio),
            extra_settle_s=settles.get(stage, self.extra_settle_s),
            stage_stretch_ratios=(),
            stage_extra_settle_s=(),
        )

    def settle_s(self) -> float:
        """Total intended dwell per frame: the proven baseline plus any extra.

        This is what the PLAN records on each frame, and therefore what
        `duration_s()` reports. The controller computes the same total from its
        own baseline and the addend -- see `extra_settle_s` for why the two are
        kept separate rather than the plan simply dictating the sleep.
        """
        return PROVEN_SETTLE_S + self.extra_settle_s

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
