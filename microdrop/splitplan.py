"""Split-tree planner: one droplet -> 2^n pieces through n symmetric splits.

Produces the tree (id, parent, stage, geometry) and the ordered frames that
realise it. Pure -- no DLL, no USB, no `ActivateElec`. An executor hands the
frames to `chiphealth.actuation.ChipController.activate`; this module can be
run and tested on a machine with no rig, which is the same rule
`detector.py` and `sweep.py` follow.

Mechanics come from `csvvolcont.py` via `params.py`. Read that module's
header first -- it records what csvvolcont proves and what it does not.


THE AXIS PROBLEM
================
A 20x20 cannot reach 8 equal pieces through three splits on one axis:
20 -> 10 -> 5 -> 2.5, and 5 will not halve. So both axes must be used, and
every valid ordering ends at a 10x5 or 5x10 piece.

    W,H,W   20x20 -> 20x10 -> 10x10 -> 10x5      (default)
    H,W,H   20x20 -> 10x20 -> 10x10 -> 5x10
    W,W,H   20x20 -> 20x10 -> 20x5  -> 10x5      (valid, but see below)
    W,W,W   20x20 -> 20x10 -> 20x5  -> refused

SYMMETRY IS THE PRIORITY
========================
Researcher decision, 2026-08-13: where csvvolcont's mechanics conflict with a
true 50/50 halving, symmetry wins. csvvolcont dispenses from a stationary
reservoir; this halves. Two of its details were reversed here -- the stretch
is centred on the parent rather than anchored at its origin, and the neck
erodes centre-out rather than one-sided. `params` records both in full, with
the reasoning and the one proven number that had to move (20 -> 35 became
20 -> 36, because an odd surplus cannot be halved on an integer grid).

The resulting claim is exact, not approximate: every split is an exact 50/50
division of its parent's footprint, and every frame of every split is mirror-
symmetric about its parent's centre line. `test_splitplan.TestSymmetry`
verifies both at all three stages rather than only at the root.

VOLUME EQUALITY
===============
Method changed 2026-08-13 (researcher). It was: measure `ChipConfig.gap_um`,
image the split pieces, compare. That was blocked on a measurement nobody had
taken, so volume equality sat permanently UNVERIFIED and the package could only
claim footprint.

It is now based on the PIXEL SIZE OF THE ACTIVATED ELECTRODE AREA -- the
electrode count and geometry of each split piece, as a proxy for its volume.
See `volume_equality` and `DropNode.activated_area_electrodes`.

This works because equality is a ratio, and the gap cancels out of a ratio:
`V = A x g`, so `V_a/V_b = A_a/A_b` when the gap is the same under both. The
unmeasured gap still blocks any ABSOLUTE figure -- there is no nanolitre number
in this repo and there must not be -- but "these eight pieces hold the same
amount" is decidable exactly, from geometry, today.

THE ASSUMPTION IT RESTS ON is a UNIFORM PLATE GAP across the compared pieces.
Note this is not the same unknown as the gap's value: the proxy needs only that
the gap is the SAME under both pieces, not what it is. Neither has been
measured. A tilted or unevenly-compressed top plate breaks it, and breaks it
worst for the pieces furthest apart -- which after three splits is every pair
that matters. `VOLUME_EQUALITY_ASSUMPTIONS` lists that and three smaller ones
in full, and `volume_equality().describe()` prints them with the verdict so the
caveat travels with the claim.

Footprint symmetry is separately exact and separately tested: every split is an
exact 50/50 division and every frame is mirror-symmetric. That is a claim about
the actuation sequence; the volume claim above is a claim about the plan. The
proxy is checkable against the rig without new tooling -- `detector` reports
observed blob area in the same electrode units -- but it has not been checked
yet, and it measures the plan, not the chip.

LOAD CLEARANCE
==============
Centring the stretch is not free. An origin-anchored stretch grows only in
`+` and needs no room behind the load position; a centred one grows both ways,
cumulatively down the tree, and for the default 8-piece plan needs 8 clear
electrodes above/below and 12 either side of the loaded 20x20. The sweep's
load position (row 5, col 10) does not have that, so `plan_tree(default_root())`
reports off-grid violations by design rather than clipping silently. See
`required_margin` and `cleared_root`.

Reporting is not refusing, though, and until 2026-08-13 nothing refused. Call
`require_clearance(plan)` before handing any plan to an executor: it measures
`plan_bounds` against the array and raises `ClearanceViolation` naming each
short side and by how much, and `allow_violations=True` is the only way past.
Every individual frame is independently gated at `ChipController.activate`, so
a plan that skips this gate still cannot energise an off-grid electrode -- it
just finds out several frames in, with liquid already on the chip.

THE LOAD POSITION IS NOT THE SPLIT POSITION
===========================================
Resolved this session, and it dissolves the conflict above rather than
settling it. The clearance requirement is on where the droplet SPLITS. Nothing
requires a human to load it there. So the operator keeps loading at
`SweepConfig` (row 5, col 10) -- unchanged, and still the only definition of
where the sweep loads -- and `plan_approach` walks the 20x20 to the split
position first, one electrode per grow/release pair, using the transport
primitive `sweep.grow_release` that this repo already has under test.

    default_root()   where it is LOADED     row 5,  col 10   (SweepConfig)
    split_root()     where it is SPLIT      row 55, col 55   (chosen; centred)
    cleared_root()   the least nudge that fits, for tests    row 9,  col 13

Because the split position is now free rather than forced, it was chosen on
merit: the chip centre, which is the only position class with room for the
16-piece tree and the place where a corner-picking scale error costs least.
`split_root`'s docstring has the full argument and the cost -- 95 electrodes
of travel, and transport is where liquid gets lost.

Positions here are 1-BASED, electrode (1,1) top-left, matching
`chiphealth.geometry` and `ChipController._validate`. That was not true before
2026-08-13: `plan_tree`'s off-grid test read `r0 < 0 or r1 >= rows`, one
electrode out at every edge and in opposite directions, so `cleared_root()`
returned (8, 12) -- a position this module called clean and the controller
refused for 63 of the plan's 87 frames. Both now measure through
`chiphealth.clearance`, and `cleared_root()` returns (9, 13).

So at least one HEIGHT-axis split is unavoidable, and csvvolcont never
performs one -- every drop in that file is `MAIN_H = 10` tall from load to
merge. Its height-axis parameters do not exist to be copied. What transfers
is the sequencing (stretch, then erode the neck at one electrode per contact
line per frame at 0.5 s, holding every other piece in every frame) and the
ratios, both of which are axis-agnostic. `params.SplitParams` applies them to
whichever axis it is handed. That is a DERIVATION, and it is the main thing in
this plan that hardware has not yet agreed to.

Six of the eight orderings are legal, and they are not equally good. What
separates them is the worst aspect ratio any droplet reaches AT FULL STRETCH,
which is the thinnest it ever gets and therefore the moment it is most likely
to neck down on its own:

    W,H,W  3.6    W,H,H  3.6    H,W,W  3.6    H,W,H  3.6
    W,W,H  7.2    H,H,W  7.2
    W,W,W  refused (20 -> 10 -> 5, and 5 will not halve)   H,H,H  likewise

The two bad orderings are exactly the ones that spend both same-axis splits
BEFORE touching the other axis: that drives one dimension to 5 electrodes and
then stretches the other to 35, giving a 35x5 sliver 1.23 mm wide. That is
where liquid breaks up and throws satellites unbidden, and satellite control
is precisely what there is no waveform knob for (§2.3).

W,H,W is the default as one of the four joint-best; the choice among those
four is arbitrary and all six remain reachable. `test_splitplan` pins this
table, so an ordering change that makes slivers fails loudly.


WHAT THIS PLAN IS AND IS NOT
============================
Eight 10x5 pieces is 2.465 x 1.232 mm each at the measured 246.48 um pitch
(objectives.md §2.1). `1pixsplit.py` reached 5x3, or 1.232 x 0.739 mm. So
this tree does NOT push the minimum-size floor down -- it is a parallel
dispensing result: eight pieces of equal, known FOOTPRINT from one load, with
full provenance for each, and equal volume by the activated-area proxy under a
uniform gap. Still no figure in nanolitres, here or anywhere else in the repo:
that needs the gap's value, which the proxy does not supply (§2.4 q3).

That is worth having, but it does not on its own answer §2.4 question 1,
which asks whether Priority 2's deliverable is smallest-droplet or
positioning-precision, and which the spec still records as UNANSWERED. A
uniform 8-way tree is arguably a third thing. Depth is not capped here: the
planner takes an axis list of any length, so continuing to 16 or 32 pieces is
a parameter change, and the floor it hits is a finding rather than an input.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from typing import Iterable, Literal, Sequence

from chiphealth import clearance, sweep
from chiphealth.actuation import Drop
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import ChipConfig

from . import params as P

Axis = Literal["H", "W"]

#: Default order for a 20x20 -> 8. See THE AXIS PROBLEM above.
DEFAULT_AXES: tuple[Axis, ...] = ("W", "H", "W")


# ── Tree ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DropNode:
    """One droplet at one point in the tree.

    `id` encodes the lineage: the root is ``d``, its children ``d0`` and
    ``d1``, theirs ``d00``/``d01``/``d10``/``d11``. `stage` is therefore
    always ``len(id) - 1``, but it is stored rather than recomputed so a node
    read out of an artifact means something on its own.
    """

    id: str
    parent: str | None
    stage: int
    height: int
    width: int
    row: int
    col: int
    #: Axis of the split that produced this node; None for the root.
    born_axis: Axis | None = None

    def extent(self, axis: Axis) -> int:
        return self.height if axis == "H" else self.width

    def origin(self, axis: Axis) -> int:
        return self.row if axis == "H" else self.col

    def bounds(self) -> tuple[int, int, int, int]:
        """(row0, row1, col0, col1), inclusive -- matches Drop.covers()."""
        return (self.row, self.row + self.height - 1,
                self.col, self.col + self.width - 1)

    def drop(self) -> Drop:
        return Drop(self.height, self.width, self.row, self.col)

    def size_mm(self, cfg: ChipConfig | None = None) -> tuple[float, float] | None:
        """Physical footprint, or None if the pitch is unknown.

        Footprint only. An ABSOLUTE volume needs the plate gap, which is
        unmeasured and must not be invented (objectives.md §2.4 q3). Volume
        EQUALITY between two pieces is a different question and does not need
        it -- see :meth:`activated_area_electrodes`.
        """
        cfg = cfg or ChipConfig()
        if cfg.pitch_um is None:
            return None
        mm = cfg.pitch_um / 1000.0
        return (self.height * mm, self.width * mm)

    def activated_area_electrodes(self) -> int:
        """The volume proxy: how many electrode cells this piece activates.

        This is what volume equality is now based on (researcher decision,
        2026-08-13), replacing "measure `ChipConfig.gap_um`, then image the
        pieces". The substitution works because equality is a RATIO and the gap
        cancels out of it:

            V = A x g            A = area, g = plate gap
            V_a / V_b = A_a / A_b        when g_a == g_b

        So the unmeasured gap blocks an absolute figure -- there is still no
        nanolitre number anywhere in this repo and there must not be -- while
        leaving "these two pieces hold the same amount" fully decidable from
        geometry this module already knows exactly.

        It is a count of ELECTRODES, deliberately, not of camera pixels. The
        two are the same measurement in different units: `detector` already
        reports observed blobs in electrode units via
        `ElectrodeFrame.area_px_to_electrodes`, so a planned
        `activated_area_electrodes` and an observed `blob.area_electrodes` are
        directly comparable with no conversion and no dependence on framing or
        camera resolution.

        WHAT THIS ASSUMES -- see `volume_equality` for the full list. The load-
        bearing one is that the plate gap is UNIFORM across the pieces being
        compared. Equal area only implies equal volume if the liquid is the
        same height in both, and nothing in this repo has measured that.
        """
        return self.height * self.width

    def activated_area_mm2(self, cfg: ChipConfig | None = None) -> float | None:
        """The proxy in physical units, or None if the pitch is unknown.

        Area, not volume, and the naming says so. Reportable because the pitch
        is measured (246.48 um, config.ChipConfig); a volume would need the gap
        and is not reportable.
        """
        cfg = cfg or ChipConfig()
        if cfg.pitch_um is None:
            return None
        return self.activated_area_electrodes() * (cfg.pitch_um / 1000.0) ** 2


def _stretch_origin(node: DropNode, axis: Axis, extent: int) -> int:
    """Origin along `axis` for a stretch to `extent` CENTRED on the node.

    csvvolcont anchors its stretch at the origin and grows one way, because
    its reservoir cannot move. A halving has no reservoir and no reason to
    prefer a direction, and an origin-anchored stretch makes one child travel
    the whole surplus while the other barely moves. Centring splits the travel
    evenly, which is the whole point (see `params` divergence note).

    The surplus must be even for the centre to land on the grid; `stretch_to`
    guarantees that, and this refuses rather than silently biasing a child by
    half an electrode.
    """
    surplus = extent - node.extent(axis)
    if surplus % 2:
        raise ValueError(
            f"a centred stretch needs an even surplus, got {surplus} "
            f"({node.extent(axis)} -> {extent} on axis {axis}); "
            f"an odd surplus cannot be shared equally between the two children"
        )
    return node.origin(axis) - surplus // 2


def _stretched(node: DropNode, axis: Axis, extent: int) -> Drop:
    """The node's footprint stretched to `extent` along `axis`, centred."""
    o = _stretch_origin(node, axis, extent)
    if axis == "H":
        return Drop(extent, node.width, o, node.col)
    return Drop(node.height, extent, node.row, o)


def _bridge(node: DropNode, axis: Axis, origin: int, extent: int) -> Drop:
    """The neck: a pad spanning `extent` along `axis`, full width across it."""
    if axis == "H":
        return Drop(extent, node.width, origin, node.col)
    return Drop(node.height, extent, node.row, origin)


def _child(node: DropNode, axis: Axis, index: int, origin: int,
           extent: int) -> DropNode:
    if axis == "H":
        h, w, r, c = extent, node.width, origin, node.col
    else:
        h, w, r, c = node.height, extent, node.row, origin
    return DropNode(
        id=f"{node.id}{index}",
        parent=node.id,
        stage=node.stage + 1,
        height=h, width=w, row=r, col=c,
        born_axis=axis,
    )


# ── Frames ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Frame:
    """One `ActivateElec` call: every drop on the chip at that instant.

    csvvolcont prepends `RESERVOIRS` to every call and holds each finished
    piece via `held_pieces()`. There is no reservoir here -- the root droplet
    is the whole material -- but the hold-everything half of that discipline
    carries over unchanged: an unheld piece is an unpowered piece, and it will
    drift or merge. Every frame below therefore names every live drop.
    """

    label: str
    drops: tuple[Drop, ...]
    #: Seconds to dwell after this frame. csvvolcont.py:137.
    settle_s: float = P.PROVEN_SETTLE_S

    def cells(self) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for d in self.drops:
            r0, r1, c0, c1 = d.covers()
            out.update(itertools.product(range(r0, r1 + 1), range(c0, c1 + 1)))
        return out


@dataclass(frozen=True)
class SplitStep:
    """Everything that happens to turn one parent into two children."""

    parent_id: str
    child_ids: tuple[str, str]
    stage: int
    axis: Axis
    parent_extent: int
    stretch_to: int
    neck_gap: int
    frames: tuple[Frame, ...]

    @property
    def n_frames(self) -> int:
        return len(self.frames)


def split_frames(parent: DropNode, axis: Axis, held: Sequence[DropNode],
                 sp: P.SplitParams = P.DEFAULT) -> tuple[SplitStep, tuple[DropNode, DropNode]]:
    """Frames for one symmetric split, and the two children it produces.

    Sequence. The ORDER is csvvolcont's; the two mechanics marked DIVERGES are
    deliberately not (researcher decision 2026-08-13, `params` docstring):

      STRETCH  extent -> even(extent*1.75), CENTRED on the parent, both
               contact lines advancing one electrode per frame in opposite
               directions.
               DIVERGES from csvvolcont L230-235, which anchors at the origin
               and grows one way because its reservoir cannot move.
      ERODE    the neck parts in the MIDDLE and retreats outwards, one
               electrode per frame per side, leaving two stubs that each
               shrink back into their own child.
               DIVERGES from csvvolcont L240-259, which pins the bridge's far
               edge and marches the near one, draining the whole neck into one
               side. Correct for a dispense, a systematic volume bias for a
               halving.
      RETRACT  optional, off by default -- see SplitParams.neck_retract

    Every frame is mirror-symmetric about the parent's centre line on the
    split axis, which is the strongest statement of intent here and is what
    `test_splitplan.TestSymmetry` actually checks.

    Symmetric in FOOTPRINT, and the two children activate the same number of
    electrodes -- which is what volume equality is now based on, since the gap
    cancels out of a ratio (see the module docstring and `volume_equality`).
    Centre-out erosion is what removes the systematic bias that made the two
    counts unequal under csvvolcont's one-sided erosion. It rests on the plate
    gap being uniform across the two children, which is unmeasured.

    There is no translate-and-pinch stage. csvvolcont's step 4 walks its piece
    25 columns clear of the reservoir because the reservoir must stay put; a
    halving leaves both children already separated by the stretch, so adding a
    translation would be unproven motion for no purpose. Pieces that need to
    move afterwards should be walked with `sweep.grow_release`, which is the
    transport primitive this repo already has under test.
    """
    e = parent.extent(axis)
    child_e = sp.child_extent(e)
    s = sp.stretch_to(e)
    gap = sp.neck_gap(e)
    half_gap = gap // 2
    o = _stretch_origin(parent, axis, s)

    a = _child(parent, axis, 0, o, child_e)
    b = _child(parent, axis, 1, o + s - child_e, child_e)

    hold = tuple(n.drop() for n in held)
    frames: list[Frame] = []

    for i in range(1, sp.stretch_steps(e) + 1):
        frames.append(Frame(
            label=f"{parent.id} STRETCH {axis} {e + 2 * i}",
            drops=hold + (_stretched(parent, axis, e + 2 * i),),
        ))

    # Stub roots: the inner edge of each child, where its half of the neck
    # stays attached. The stubs erode towards these, never away from them, so
    # neither child is ever cut off from the liquid it is meant to keep.
    left_root = o + child_e
    right_root = o + s - child_e
    for k in range(sp.erode_steps(e)):
        stub = half_gap - k
        pieces = [a.drop(), b.drop()]
        if stub > 0:
            pieces.insert(1, _bridge(parent, axis, left_root, stub))
            pieces.insert(2, _bridge(parent, axis, right_root - stub, stub))
            lbl = f"{parent.id} ERODE dry={2 * k} stub={stub}"
        else:
            lbl = f"{parent.id} ERODE open"
        frames.append(Frame(label=lbl, drops=hold + tuple(pieces)))

    if sp.neck_retract:
        for stub in range(half_gap, 0, -1):
            frames.append(Frame(
                label=f"{parent.id} RETRACT stub={stub}",
                drops=hold + (a.drop(),
                              _bridge(parent, axis, left_root, stub),
                              _bridge(parent, axis, right_root - stub, stub),
                              b.drop()),
            ))
        frames.append(Frame(
            label=f"{parent.id} RETRACT done",
            drops=hold + (a.drop(), b.drop()),
        ))

    step = SplitStep(
        parent_id=parent.id, child_ids=(a.id, b.id), stage=parent.stage,
        axis=axis, parent_extent=e, stretch_to=s, neck_gap=gap,
        frames=tuple(frames),
    )
    return step, (a, b)


# ── Plan ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    stage: int
    kind: str
    detail: str


@dataclass
class SplitPlan:
    root: DropNode
    axes: tuple[Axis, ...]
    nodes: dict[str, DropNode] = field(default_factory=dict)
    steps: list[SplitStep] = field(default_factory=list)
    #: Live (unsplit) node ids after each stage; index 0 is before any split.
    stages: list[tuple[str, ...]] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)

    @property
    def leaves(self) -> tuple[DropNode, ...]:
        return tuple(self.nodes[i] for i in self.stages[-1])

    @property
    def n_frames(self) -> int:
        return sum(s.n_frames for s in self.steps)

    def duration_s(self) -> float:
        """Frame dwell only. Stage-boundary pauses and operator prompts extra."""
        return sum(f.settle_s for s in self.steps for f in s.frames)

    def lineage(self, node_id: str) -> tuple[str, ...]:
        out: list[str] = []
        cur: str | None = node_id
        while cur is not None:
            out.append(cur)
            cur = self.nodes[cur].parent
        return tuple(reversed(out))


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ar0, ar1, ac0, ac1 = a
    br0, br1, bc0, bc1 = b
    return not (ar1 < br0 or br1 < ar0 or ac1 < bc0 or bc1 < ac0)


def _separation(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    """Chebyshev gap in electrodes; 0 when touching, negative when overlapping."""
    ar0, ar1, ac0, ac1 = a
    br0, br1, bc0, bc1 = b
    dr = br0 - ar1 - 1 if br0 > ar1 else (ar0 - br1 - 1 if ar0 > br1 else -1)
    dc = bc0 - ac1 - 1 if bc0 > ac1 else (ac0 - bc1 - 1 if ac0 > bc1 else -1)
    return max(dr, dc)


def plan_tree(root: DropNode, axes: Sequence[Axis] = DEFAULT_AXES,
              sp: P.SplitParams = P.DEFAULT,
              cfg: ChipConfig | None = None,
              min_separation: int = 2) -> SplitPlan:
    """Plan `len(axes)` symmetric splits, producing 2**len(axes) pieces.

    Splits proceed stage by stage; within a stage, parents are taken in
    (row, col) order so the plan is deterministic and reproducible from the
    artifact. Validation runs as the plan is built -- geometry is checked
    against the live set including the stretched footprint mid-split, which is
    the widest the chip ever gets and the moment a collision would actually
    happen.

    `min_separation` borrows the `SafetyDistance` idea from the vendor path
    planner, which objectives.md §3 records as worth keeping even though the
    DLL itself was not adopted.
    """
    cfg = cfg or ChipConfig()
    plan = SplitPlan(root=root, axes=tuple(axes))
    plan.nodes[root.id] = root
    live: list[str] = [root.id]
    plan.stages.append(tuple(live))

    def check(stage: int, kind: str, boxes: dict[str, tuple[int, int, int, int]]) -> None:
        for name, box in boxes.items():
            r0, r1, c0, c1 = box
            # 1-BASED, via chiphealth.clearance. This test used to read
            # `r0 < 0 or r1 >= cfg.rows`, which is 0-based and disagreed by one
            # electrode at every edge with ChipController._validate -- the thing
            # that actually guards ActivateElec. The visible symptom was
            # cleared_root() planning with zero violations here and then having
            # 63 of its 87 frames refused by the controller. Measured in one
            # place now so the two cannot drift apart again.
            c = clearance.measure([box], cfg.rows, cfg.cols)
            if not c.ok:
                short = ", ".join(f"{side} by {n}"
                                  for side, n in c.short_sides().items())
                plan.violations.append(Violation(
                    stage, "off-grid",
                    f"{kind}: {name} spans rows {r0}-{r1} cols {c0}-{c1}, "
                    f"grid is {cfg.rows}x{cfg.cols} (rows/cols "
                    f"{clearance.FIRST_INDEX}-{cfg.rows}) -- short {short}",
                ))
        for (n1, b1), (n2, b2) in itertools.combinations(boxes.items(), 2):
            if _overlap(b1, b2):
                plan.violations.append(Violation(
                    stage, "overlap", f"{kind}: {n1} overlaps {n2}"))
            elif (sep := _separation(b1, b2)) < min_separation:
                plan.violations.append(Violation(
                    stage, "separation",
                    f"{kind}: {n1} and {n2} are {sep} electrodes apart, "
                    f"minimum is {min_separation}",
                ))

    for stage, axis in enumerate(axes):
        # Identity unless `sp.stage_stretch_ratios` names this stage, in which
        # case it gets a different ratio and every other stage keeps the proven
        # one. Resolved once per stage, OUTSIDE the parent loop below, which is
        # what makes the widening per-stage rather than per-piece -- see
        # SplitParams.stage_stretch_ratios for why per-piece is refused.
        sp_stage = sp.for_stage(stage, len(axes))
        parents = sorted((plan.nodes[i] for i in live),
                         key=lambda n: (n.row, n.col))
        # Parents of this stage that have not been split yet. A parent leaves
        # this list the moment its children exist -- it is gone, and holding it
        # would energise a pad underneath the two pieces that replaced it.
        pending: list[str] = [n.id for n in parents]
        next_live: list[str] = []
        for parent in parents:
            pending.remove(parent.id)
            others = [plan.nodes[i] for i in pending] + \
                     [plan.nodes[i] for i in next_live]
            step, (a, b) = split_frames(parent, axis, others, sp_stage)

            boxes = {n.id: n.bounds() for n in others}
            boxes[f"{parent.id}*stretched"] = _stretched(
                parent, axis, step.stretch_to).covers()
            check(stage, "mid-split", boxes)

            plan.nodes[a.id] = a
            plan.nodes[b.id] = b
            plan.steps.append(step)
            next_live.extend([a.id, b.id])
        live = next_live
        check(stage, "settled", {i: plan.nodes[i].bounds() for i in live})
        plan.stages.append(tuple(live))

    return plan


# ── Approach ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Approach:
    """The walk from where the droplet is loaded to where it gets split.

    This is what makes the load position stop mattering (researcher, this
    session). The tree needs 8 clear electrodes above and below and 12 either
    side of the root, and the sweep's load position does not have that -- but
    the requirement is on where the droplet SPLITS, not on where a human puts
    it. Walking it there first satisfies the clearance without asking anyone to
    load somewhere new, and without touching `SweepConfig`.
    """

    from_rc: tuple[int, int]
    to_rc: tuple[int, int]
    height: int
    width: int
    frames: tuple[Frame, ...]

    @property
    def n_frames(self) -> int:
        return len(self.frames)

    @property
    def electrodes(self) -> int:
        """Electrodes of travel. Two frames each -- grow, then release."""
        return abs(self.to_rc[0] - self.from_rc[0]) + abs(self.to_rc[1] - self.from_rc[1])

    def duration_s(self) -> float:
        return sum(f.settle_s for f in self.frames)

    def bounds(self) -> tuple[int, int, int, int]:
        return _frames_bounds(self.frames)


def plan_approach(load: DropNode, to_row: int, to_col: int) -> Approach:
    """Walk `load` to (to_row, to_col), one electrode at a time.

    Uses `sweep.grow_release`, which is the transport primitive this repo
    already has under test and the one this module's docstring has always
    pointed at for moving pieces. Every electrode of travel is TWO frames:
    grow into the new territory while holding everything already energised,
    then release the trailing edge. Commanding the destination in one call
    instead asks the liquid to let go behind and grab ahead in the same
    instant, and on hardware 2026-08-10 that necked and split the droplet
    mid-transport.

    L-shaped: rows first, then columns. One axis at a time is what
    `grow_release` supports, and it is the right constraint -- a diagonal step
    would move the contact line two electrodes in one frame. Which leg goes
    first is arbitrary and does not change the frame count.

    NOT a reason to relax the split clearance. The approach only relocates the
    droplet; the tree still needs its full margin around wherever it lands, and
    `require_clearance` still has to pass at the destination.

    THE RISK THIS ADDS, stated plainly: transport is where liquid gets lost.
    The 2026-08-10 break-up happened during transport, residue left on the path
    is exactly the signature `detector` exists to find, and a droplet that
    arrives smaller than `load.height x load.width` cannot be split into equal
    pieces because the tree assumes a full footprint. That argues for keeping
    the walk short where the clearance allows a choice -- see `split_root`,
    which does not, and says why.
    """
    frames: list[Frame] = []
    row, col = load.row, load.col
    idx = 0

    def leg(axis: str, target: int) -> None:
        nonlocal row, col, idx
        while (row if axis == sweep.AXIS_ROW else col) != target:
            cur = row if axis == sweep.AXIS_ROW else col
            d = 1 if target > cur else -1
            grow, release = sweep.grow_release(
                idx, row, col, load.height, load.width,
                axis, d, sweep.KIND_TRANSPORT, band=0)
            for s, tag in ((grow, "grow"), (release, "release")):
                frames.append(Frame(
                    label=f"{load.id} APPROACH {axis} {tag} -> ({s.row},{s.col})",
                    drops=(Drop(s.h, s.w, s.row, s.col),),
                ))
            if axis == sweep.AXIS_ROW:
                row += d
            else:
                col += d
            idx += 2

    leg(sweep.AXIS_ROW, to_row)
    leg(sweep.AXIS_COL, to_col)

    return Approach(from_rc=(load.row, load.col), to_rc=(to_row, to_col),
                    height=load.height, width=load.width,
                    frames=tuple(frames))


def approach_to_split(load: DropNode | None = None,
                      target: DropNode | None = None) -> tuple[Approach, DropNode]:
    """The whole load-to-split move: walk `load` onto `target`'s position.

    Defaults are the protocol as specified: load at `load_root` (row 5,
    col 55), split at `split_root` (row 55, col 55). Returns the approach and
    the root node the tree should then be planned from.

    Pass `load=default_root()` to walk from the chip-health sweep's load
    position instead -- 95 electrodes and an L-turn rather than 50 straight
    down.
    """
    load = load or load_root()
    target = target or split_root()
    if (load.height, load.width) != (target.height, target.width):
        raise ValueError(
            f"the approach moves a droplet, it does not resize one: loaded "
            f"{load.height}x{load.width} but the split root is "
            f"{target.height}x{target.width}"
        )
    return plan_approach(load, target.row, target.col), target


def require_approach_clearance(approach: Approach,
                               cfg: ChipConfig | None = None,
                               allow_violations: bool = False
                               ) -> clearance.Clearance:
    """Gate the transport, on the same terms as `require_clearance` gates the tree.

    A walk cannot leave the array by more than the grow step's one electrode,
    so this rarely fires -- but "rarely" is not "never", and the endpoints come
    from config that can be edited.
    """
    cfg = cfg or ChipConfig()
    return clearance.require(
        [approach.bounds()], cfg.rows, cfg.cols,
        what=(f"approach walking {approach.height}x{approach.width} from "
              f"row {approach.from_rc[0]}, col {approach.from_rc[1]} to "
              f"row {approach.to_rc[0]}, col {approach.to_rc[1]}"),
        allow_violations=allow_violations,
    )


# ── Reporting ─────────────────────────────────────────────────────────────────


def describe(plan: SplitPlan, cfg: ChipConfig | None = None) -> str:
    cfg = cfg or ChipConfig()
    out: list[str] = []
    r = plan.root
    out.append(f"root {r.id}: {r.height}x{r.width} at row={r.row} col={r.col}")
    out.append(f"axes {' -> '.join(plan.axes)}   "
               f"{2 ** len(plan.axes)} pieces   "
               f"{plan.n_frames} frames   "
               f"{plan.duration_s():.0f}s of dwell")
    out.append("")
    for stage, step in enumerate(plan.steps):
        a, b = (plan.nodes[i] for i in step.child_ids)
        out.append(
            f"  s{step.stage} {step.axis}  {step.parent_id} "
            f"({step.parent_extent}) -> {a.id},{b.id} "
            f"stretch {step.parent_extent}->{step.stretch_to}, "
            f"gap {step.neck_gap}, {step.n_frames} frames"
        )
    out.append("")
    out.append("leaves:")
    for n in sorted(plan.leaves, key=lambda n: (n.row, n.col)):
        mm = n.size_mm(cfg)
        phys = f"  {mm[0]:.3f} x {mm[1]:.3f} mm" if mm else ""
        out.append(f"  {n.id:<6} stage {n.stage}  parent {n.parent:<5} "
                   f"{n.height}x{n.width} at ({n.row},{n.col}){phys}")
    if plan.violations:
        out.append("")
        out.append(f"VIOLATIONS ({len(plan.violations)}):")
        for v in plan.violations:
            out.append(f"  stage {v.stage} {v.kind}: {v.detail}")
    else:
        out.append("")
        out.append("no geometry violations")
    return "\n".join(out)


#: What the activated-area proxy takes on faith. Every one of these is a way
#: for two pieces of identical electrode footprint to hold different amounts of
#: liquid, and none of them is measured on this chip yet.
VOLUME_EQUALITY_ASSUMPTIONS = (
    "UNIFORM PLATE GAP across the compared pieces. This is the load-bearing "
    "one. Volume is area x gap, so equal area means equal volume only if the "
    "gap is the same under both pieces. The gap is unmeasured "
    "(ChipConfig.gap_um is None) AND its uniformity is unmeasured, which are "
    "two separate unknowns -- the proxy needs only the second, which is why it "
    "works at all, but the second has never been checked. A tilted or "
    "unevenly-compressed top plate breaks this, and breaks it worst for pieces "
    "furthest apart -- which after three splits is every pair that matters.",

    "LIQUID FILLS THE ACTIVATED FOOTPRINT, AND ONLY IT. The proxy counts "
    "commanded electrodes, not liquid. Bulge past the contact line, an "
    "incompletely wetted cell, or liquid still bridging where the neck opened "
    "all make the count and the contents disagree.",

    "EQUAL PERIMETER EFFECTS. The meniscus lives at the edge, so its share of "
    "the volume scales with perimeter, not area. Two pieces of equal area but "
    "different aspect ratio (10x5 against 5x10) have equal perimeter and are "
    "fine; a tree mixing aspect ratios at the same stage would not be. The "
    "default W,H,W tree keeps every leaf 10x5, so this holds by construction "
    "-- check it again if the axis order changes.",

    "NOTHING WAS LOST. Satellites thrown during the break, and residue left on "
    "the eroded neck, are volume that left a piece without changing the "
    "electrode count of what remains. The tree is symmetric, so losses should "
    "be symmetric too, but 'should be' is the assumption.",
)


@dataclass(frozen=True)
class VolumeEquality:
    """Whether a plan's leaves hold equal volume, by the activated-area proxy.

    `equal` is a claim about the PLAN, decided exactly from geometry. It is not
    a measurement of liquid, and it is only as good as
    `VOLUME_EQUALITY_ASSUMPTIONS`.
    """

    equal: bool
    areas: dict[str, int]
    assumptions: tuple[str, ...] = VOLUME_EQUALITY_ASSUMPTIONS

    @property
    def area_electrodes(self) -> int | None:
        """The common area, or None when the leaves disagree."""
        vals = set(self.areas.values())
        return vals.pop() if len(vals) == 1 else None

    def describe(self) -> str:
        out: list[str] = []
        if self.equal:
            out.append(f"VOLUME EQUALITY: all {len(self.areas)} leaves activate "
                       f"{self.area_electrodes} electrodes each -- equal by the "
                       f"activated-area proxy.")
        else:
            out.append(f"VOLUME EQUALITY: NOT equal. Leaf areas differ: "
                       f"{sorted(set(self.areas.values()))} electrodes.")
        out.append("No absolute volume is claimed; the plate gap is unmeasured "
                   "and it cancels out of a ratio but not out of a quantity.")
        out.append("Assumes:")
        for a in self.assumptions:
            out.append(f"  - {a}")
        return "\n".join(out)


def volume_equality(plan: SplitPlan) -> VolumeEquality:
    """Are all the plan's leaves equal in volume, by activated electrode area?

    THE METHOD, as of the researcher's 2026-08-13 decision. It replaces
    "measure `ChipConfig.gap_um`, then image the split pieces", which was
    blocked on a measurement nobody had taken and on imaging nobody had done.

    The substitution is sound for EQUALITY specifically, because volume is
    area x gap and the gap cancels out of a ratio:

        V_a / V_b  =  (A_a x g) / (A_b x g)  =  A_a / A_b

    so two pieces activating the same number of electrodes hold the same
    volume, whatever that volume is. That is a strictly weaker claim than the
    old method would have supported -- it yields no nanolitres, and it never
    will -- but it is decidable now, exactly, from geometry this module already
    computes, rather than pending indefinitely.

    It is also checkable against the rig without new tooling: `detector` reports
    observed blob area in the same electrode units
    (`ElectrodeFrame.area_px_to_electrodes`), so the predicted count and a
    measured one are the same quantity.

    WHEN IT BREAKS: see `VOLUME_EQUALITY_ASSUMPTIONS`, and treat the uniform-gap
    one as the answer to "when would this mislead me". If the top plate is
    tilted or unevenly compressed, two pieces of identical footprint at
    opposite corners hold different amounts and this function still says
    `equal=True`. It is measuring the plan, not the chip.
    """
    areas = {n.id: n.activated_area_electrodes() for n in plan.leaves}
    return VolumeEquality(equal=len(set(areas.values())) <= 1, areas=areas)


def _frames_bounds(frames: Iterable[Frame]) -> tuple[int, int, int, int]:
    """(row0, row1, col0, col1) touched by a sequence of frames."""
    r0 = c0 = 10 ** 9
    r1 = c1 = -10 ** 9
    for f in frames:
        for d in f.drops:
            dr0, dr1, dc0, dc1 = d.covers()
            r0, r1 = min(r0, dr0), max(r1, dr1)
            c0, c1 = min(c0, dc0), max(c1, dc1)
    return r0, r1, c0, c1


def plan_bounds(plan: SplitPlan) -> tuple[int, int, int, int]:
    """(row0, row1, col0, col1) actually touched, over EVERY frame.

    Read off the frames rather than recomputed, so it cannot drift from what
    the plan will really energise. Mid-split stretch frames are included, and
    they are the extremes -- a centred stretch reaches further than any
    settled piece does.
    """
    return _frames_bounds(f for step in plan.steps for f in step.frames)


def required_margin(root: DropNode, axes: Sequence[Axis] = DEFAULT_AXES,
                    sp: P.SplitParams = P.DEFAULT) -> dict[str, int]:
    """Clear electrodes needed on each side of `root` for a centred stretch.

    This is the cost of centre-anchoring. An origin-anchored stretch only ever
    grows in `+`, so it needs no clearance behind the load position; a centred
    one grows both ways and needs half the surplus on each side, cumulatively
    down the tree. Planned on an unbounded grid so the answer is the geometric
    requirement rather than a list of the places it happens to collide.
    """
    unbounded = ChipConfig(rows=10 ** 9, cols=10 ** 9)
    plan = plan_tree(root, axes, sp, cfg=unbounded)
    r0, r1, c0, c1 = plan_bounds(plan)
    return {
        "top": max(0, root.row - r0),
        "bottom": max(0, r1 - (root.row + root.height - 1)),
        "left": max(0, root.col - c0),
        "right": max(0, c1 - (root.col + root.width - 1)),
    }


def require_clearance(plan: SplitPlan, cfg: ChipConfig | None = None,
                      allow_violations: bool = False) -> clearance.Clearance:
    """THE GATE. Call this before handing a plan's frames to any executor.

    Measures :func:`plan_bounds` -- the union of every drop in every frame,
    stretch frames included, read off the frames themselves rather than
    recomputed -- against the electrode array, and raises
    ``ClearanceViolation`` naming each short side and by how much.

    Why this and not ``plan.violations``: that list also carries `overlap` and
    `separation` entries, it is advisory by construction, and nothing obliged a
    caller to look at it. ``plan_tree`` still populates it, and it is still the
    right thing to read when you want the per-node detail of *which* piece is
    off-grid at *which* stage. This function is the yes/no that has to be
    answered before anything is energised.

    ``allow_violations=True`` is the only way past and has no default-on path:
    no config field, no environment variable, one keyword a human types.
    Taking it logs at ERROR with the full shortfall.

    Note this gate is about the ARRAY EDGE only. A plan can pass here and still
    be refused for `overlap` or `separation`, which are about drops colliding
    with each other rather than with the edge of the chip -- check
    ``plan.violations`` for those.
    """
    cfg = cfg or ChipConfig()
    return clearance.require(
        [plan_bounds(plan)], cfg.rows, cfg.cols,
        what=(f"split plan from root {plan.root.id} "
              f"({plan.root.height}x{plan.root.width} at row {plan.root.row}, "
              f"col {plan.root.col}, axes {'-'.join(plan.axes)})"),
        allow_violations=allow_violations,
    )


def check_root(root: DropNode, axes: Sequence[Axis] = DEFAULT_AXES,
               sp: P.SplitParams = P.DEFAULT,
               cfg: ChipConfig | None = None) -> clearance.Clearance:
    """Clearance for a load position, without building the plan first.

    The question an operator actually asks -- "can I load here?" -- answered
    before committing to a plan. :func:`require_clearance` is the gate;
    this is the lookup.
    """
    cfg = cfg or ChipConfig()
    unbounded = ChipConfig(rows=10 ** 9, cols=10 ** 9)
    plan = plan_tree(root, axes, sp, cfg=unbounded)
    return clearance.measure(
        [plan_bounds(plan)], cfg.rows, cfg.cols,
        what=(f"a {root.height}x{root.width} load at row {root.row}, "
              f"col {root.col} split {'-'.join(axes)}"),
    )


def default_root(cfg=None) -> DropNode:
    """The operator-loaded droplet, from chiphealth.config.SweepConfig.

    Read from there rather than restated so the load position stays defined in
    exactly one place -- that config comment says it is the only definition and
    it should keep being true.

    NOTE (2026-08-13): since the stretch became centred, this position no
    longer has the clearance an 8-piece tree needs -- see `required_margin` and
    `cleared_root`. Left pointing at SweepConfig anyway: moving `start_row`
    moves the chip-health sweep, its registration check and its resting frame
    with it, and that is not this module's call to make.
    """
    from chiphealth.config import SweepConfig
    sc = cfg or SweepConfig()
    return DropNode(id="d", parent=None, stage=0,
                    height=sc.window_h, width=sc.window_w,
                    row=sc.start_row, col=sc.start_col)


#: Where the operator LOADS the split experiment. Researcher, this session:
#: the droplet loads at row 5, col 55 and is then moved to the split position.
#: Not `SweepConfig` (row 5, col 10) -- that is where the chip-health SWEEP
#: loads and it is deliberately left alone.
SPLIT_LOAD_ROW = 5
SPLIT_LOAD_COL = 55

#: Where the tree SPLITS. Not where the operator loads -- see `split_root`.
SPLIT_ROOT_ROW = 55
SPLIT_ROOT_COL = 55


def load_root(cfg=None) -> DropNode:
    """THE LOAD POSITION for a split run: a 20x20 at row 5, col 55.

    Specified by the researcher, not derived, so there is nothing to compute
    here and nothing to check beyond "is it on the array" -- it is, rows 5-24
    and cols 55-74.

    It deliberately does NOT need the tree's clearance margin. A load is a
    plain rectangular hold; only the SPLIT needs 8 clear electrodes above and
    below and 12 either side, and by the time the tree runs the droplet is at
    `split_root`. That separation is the whole reason the load position can be
    dictated by where the operator can physically reach and the split position
    chosen on merit.

    It shares a COLUMN with `split_root`, which is worth more than it looks:
    the approach is then a single straight leg down column 55, 50 electrodes,
    with no corner. From the sweep's load position it would be 95 electrodes
    and an L-turn. Every electrode of travel is liquid that can be shed
    invisibly (see `protocol`, NOT VERIFIED §3), so halving the walk halves the
    exposure.

    `SweepConfig.start_row/start_col` are untouched and still define where the
    chip-health sweep loads. The two experiments load in different places, and
    that is now stated in two places rather than conflated in one.
    """
    from chiphealth.config import SweepConfig
    s = cfg or SweepConfig()
    return DropNode(id="d", parent=None, stage=0,
                    height=s.window_h, width=s.window_w,
                    row=SPLIT_LOAD_ROW, col=SPLIT_LOAD_COL)


def split_root(cfg=None) -> DropNode:
    """THE SPLIT POSITION: a 20x20 at row 55, col 55. Chosen this session.

    This is a real decision about the physical protocol, made explicitly here
    rather than inherited from `cleared_root`, which is only ever "the least
    nudge that fits" and has no opinion about what is good.

    It is NOT a load position and it does not move one. The operator still
    loads at `SweepConfig` (row 5, col 10) exactly as before; `plan_approach`
    walks the droplet here first. Decoupling the two is what makes this
    choosable at all -- otherwise the answer would be forced to whatever a
    human can conveniently reach.

    WHY THE CENTRE

    1. It is the only position class that leaves room to go deeper. The tree
       bottoms out at 16 pieces of 5x5 -- 20 = 2^2 x 5, so the fourth split is
       the last one divisibility allows -- and W,H,W,H needs 12 clear
       electrodes on all four sides. `cleared_root()` at (9, 13) is short 4 on
       top for that and can only ever do 8. Row 55, col 55 does both: the
       8-piece plan sits in rows 47-82 / cols 43-86 and the 16-piece plan in
       rows 43-86 / cols 43-86. Going from eight 2.465 x 1.232 mm pieces to
       sixteen 1.232 x 1.232 mm ones is then a parameter change, not a move.

    2. It is exactly centred on both axes, for both depths: 46/46 rows and
       42/42 cols of spare margin at 8 pieces, 42 on every side at 16. The
       loaded 20x20 itself sits dead centre -- rows 55-74 has centroid 64.5,
       and so does a 128-electrode axis. Equal room in every direction is what
       lets the operator command a corrective nudge in whichever direction the
       droplet turns out to need one, which matters more without a camera, not
       less: the correction is the only feedback path left.

    3. Nothing in the run history argues for anywhere else. All 18 runs under
       `runs/`, armed ones included, report all 1024 blocks `unknown`, so
       there is no measured good or bad region to steer towards or away from
       (objectives.md §1.4 q11 -- still no ground truth). If a future sweep
       does find a bad region, THAT should override this, and this docstring
       is where to say so.

    A REASON THAT WAS WITHDRAWN. This position was first argued for partly on
    calibration grounds -- that a corner picked on the glass rather than the
    electrode array scales the whole homography, that the resulting error is
    zero at the array centre and worst at the corners (~4.6 electrodes for a
    50 px mis-pick, per `capture.corners_px`), and that the centre is therefore
    where the coordinate frame is most trustworthy. That argument does not
    belong here, for two reasons. It conflated PLACEMENT with OBSERVATION:
    `ActivateElec` is commanded in electrode indices, so no homography stands
    between this plan and the electrodes, and a bad calibration has never been
    able to misplace a droplet -- only to misread one. And this pipeline does
    not observe at all (see `microdrop.protocol`), so there is no homography in
    it to be accurate or otherwise. The position is unchanged, because reasons
    1 and 2 are purely geometric and were doing the work regardless.

    WHAT IT COSTS. Nothing, if the droplet is loaded here directly, which is
    the intended protocol: `protocol.run_split` energises this region first and
    the operator loads INTO it, so the field does the positioning and loading
    at row 55 is no harder by hand than loading at row 5. Use `plan_approach`
    only if the droplet is already elsewhere -- from the sweep's load position
    that is 95 electrodes of travel (190 frames, ~95 s), and transport is where
    liquid gets lost. With no camera in the loop that loss is also invisible,
    which is the main argument for not walking when you do not have to.

    THE ALTERNATIVE, if a walk is unavoidable. (13, 13) is the closest position
    to the sweep's load point that still takes 16 pieces -- 11 electrodes of
    travel against 95. It is not the default because its plan lands flush in
    the corner, rows 1-44 and cols 1-44, with zero spare on two sides: no room
    to command a corrective nudge if the operator sees the droplet sitting off
    position, which without a camera is the only correction mechanism there is.
    (55, 55) keeps 42 electrodes of room in every direction for exactly that.
    """
    from chiphealth.config import SweepConfig
    s = cfg or SweepConfig()
    return DropNode(id="d", parent=None, stage=0,
                    height=s.window_h, width=s.window_w,
                    row=SPLIT_ROOT_ROW, col=SPLIT_ROOT_COL)


def cleared_root(cfg=None, axes: Sequence[Axis] = DEFAULT_AXES,
                 sp: P.SplitParams = P.DEFAULT) -> DropNode:
    """`default_root` nudged just far enough in to satisfy `required_margin`.

    A convenience for planning and for the tests, NOT a second definition of
    the load position. Using it means the operator loads the split experiment
    somewhere other than where the sweep loads, which is a real decision about
    the physical protocol and should be made explicitly rather than inherited
    from this helper.

    The clamp is `margin + FIRST_INDEX`, not `margin`. Electrode 1 is the first
    one, so clearing a margin of 8 above the root means the root sits at row 9
    and the stretch reaches row 1 -- landing it at row 8 puts the stretch on
    row 0, which does not exist. That off-by-one is why this helper used to
    return (8, 12): a position `plan_tree` called clean and
    ``ChipController._validate`` refused for 63 of the plan's 87 frames. It now
    returns (9, 13) and :func:`require_clearance` passes it.
    """
    root = default_root(cfg)
    m = required_margin(root, axes, sp)
    return replace(root, row=max(root.row, m["top"] + clearance.FIRST_INDEX),
                   col=max(root.col, m["left"] + clearance.FIRST_INDEX))


if __name__ == "__main__":  # pragma: no cover
    root = default_root()
    print(describe(plan_tree(root)))
    print()
    print(f"required margin around the load position: {required_margin(root)}")
    print(f"clearance at the load position: {check_root(root).describe()}")
    print()
    try:
        require_clearance(plan_tree(root))
    except ClearanceViolation as exc:
        print("── the gate, at the sweep's load position ──")
        print(exc)
    print()
    print("── the protocol: load where the sweep loads, walk to the centre, split ──")
    approach, split_at = approach_to_split()
    require_approach_clearance(approach)
    print(f"approach: {approach.from_rc} -> {approach.to_rc}, "
          f"{approach.electrodes} electrodes, {approach.n_frames} frames, "
          f"{approach.duration_s():.0f}s")
    plan = plan_tree(split_at)
    require_clearance(plan)
    print(describe(plan))
    print()
    print(volume_equality(plan).describe())
    print()
    deep = ("W", "H", "W", "H")
    print(f"── and the same position takes {deep} to 16 pieces ──")
    p16 = plan_tree(split_at, deep)
    require_clearance(p16)
    print(f"{len(p16.leaves)} leaves, "
          f"{sorted({(n.height, n.width) for n in p16.leaves})}, "
          f"bounds {plan_bounds(p16)}")
