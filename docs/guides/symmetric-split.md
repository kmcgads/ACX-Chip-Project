# The symmetric split algorithm

How one droplet becomes 2^n equal pieces. Extracted from the module docstrings
of `microdrop/splitplan.py` and `microdrop/params.py`.

## Symmetry is the priority

Researcher decision, 2026-08-13: **where csvvolcont's mechanics conflict with a
true 50/50 halving, symmetry wins.**

csvvolcont dispenses from a stationary reservoir; this package halves. Two of
its details are actively wrong for halving and were reversed:

| | csvvolcont | Here |
|---|---|---|
| Stretch | Anchored at the origin, grows one way — the reservoir cannot move | **Centred** on the parent; both contact lines advance one electrode per frame in opposite directions |
| Neck erosion | Pins the bridge's far edge and marches the near one, draining the whole neck into one side | **Centre-out**: the neck parts in the middle and each half retracts into its own child |

One-sided erosion hands ~44% of the stretched footprint to one child. Correct
when dispensing back into a reservoir; a systematic volume bias when halving.

Both changes force an **even neck gap** — an odd gap can be halved neither in
space nor in erosion order. That is why `stretch_to` rounds to even rather than
to nearest, and it is the one place a proven number moved: 20 → 35 became
20 → 36. Ties round *up*, so the stretch is never shorter than proven; a
thinner neck is the safer direction to err for a break.

What is inherited unchanged: the stretch-then-split order, the 1.75 ratio, one
electrode per contact line per frame, the 0.5s dwell, and holding every live
piece in every frame.

## The claim, exactly

Every split is an exact 50/50 division of its parent's footprint, and every
frame of every split is mirror-symmetric about its parent's centre line.
`test_splitplan.TestSymmetry` verifies both at every stage rather than only at
the root.

**Symmetry is per split, not per tree.** `_all_steps` re-derives each step with
nothing held, on the grounds that *"held pieces are elsewhere on the chip and
are not part of the split's own symmetry"*. This is what makes it legal to give
different stages different stretch ratios — and illegal to give the two
children of one split different treatment. See
[why per stage and not per piece](separation-and-dwell-tuning.md#why-per-stage-and-not-per-piece).

## The axis problem

A 20x20 cannot reach 8 equal pieces through three splits on one axis:
20 → 10 → 5 → 2.5, and 5 will not halve. Both axes must be used, and every
valid ordering ends at a 10x5 or 5x10 piece.

Six of the eight orderings are legal, and they are not equally good. What
separates them is the **worst aspect ratio any droplet reaches at full
stretch** — the thinnest it ever gets, and therefore the moment it is most
likely to neck down on its own:

| Ordering | Worst aspect | |
|---|---|---|
| W,H,W · W,H,H · H,W,W · H,W,H | **3.6** | joint-best; `W,H,W` is the default |
| W,W,H · H,H,W | 7.2 | avoid — a 35x5 sliver, 1.23 mm wide |
| W,W,W · H,H,H | refused | 20 → 10 → 5, and 5 will not halve |

The two bad orderings are exactly the ones that spend both same-axis splits
before touching the other axis. **7.2 is where liquid breaks up and throws
satellites unbidden**, and satellite control is precisely what there is no
waveform knob for.

`test_splitplan.test_axis_ordering_table` pins this table, so an ordering
change that makes slivers fails loudly.

## One split, frame by frame

For a parent of extent `e` on the split axis:

```
child_extent = e // 2                        refuses an odd extent
stretch_to   = _round_even(e * ratio)        always even
neck_gap     = stretch_to - e                always even
stretch_steps = gap // 2                     both contact lines move at once
erode_steps   = gap // 2 + 1                 includes the intact-neck frame
```

**STRETCH** — the parent grows from `e` to `stretch_to`, centred, two
electrodes per frame (one per contact line).

**ERODE** — the two children are energised at the two ends of the stretched
extent, and the bridge between them is eaten from the middle outwards, one
electrode per side per frame. Each stub stays welded to its own child's inner
edge for the whole erosion, so neither child is ever cut off from the liquid it
is meant to keep.

**RETRACT** — optional, off by default. csvvolcont's step 5 re-energises a
bridge and retracts it. Read one way it sweeps the trail back into the
reservoir; read another the loop bounds are reversed and it re-opens a neck
step 3 already closed. Nothing in that file says which, and a plan for 8 pieces
would repeat it 7 times. Defaulted off so it is a decision, not an inheritance.

There is **no translate-and-pinch stage**. csvvolcont walks its piece 25
columns clear because its reservoir must stay put; a halving leaves both
children already separated by the stretch. Pieces that need to move afterwards
should be walked with `sweep.grow_release`, the transport primitive already
under test.

## Depth, and the floor

The planner takes an axis list of any length, so 16 or 32 pieces is a parameter
change rather than a rewrite.

The floor for a 20x20 droplet is **16 pieces of 5x5**. 20 = 2² × 5, so four
halvings is all divisibility allows; a fifth would have to halve a 5, and the
planner refuses rather than guess which child gets the extra electrode — a 3/2
split is a 50% volume difference.

32 pieces from a 20x20 is therefore impossible, whatever else changes. It would
need a larger starting droplet and a new load protocol.

## The load position is not the split position

The clearance requirement is on where the droplet *splits*. Nothing requires a
human to load it there. The operator loads where they can reach, and
`plan_approach` walks the droplet to the split position first, one
grow/release pair per electrode.

| Function | Meaning | Position |
|---|---|---|
| `default_root()` | where it is LOADED (SweepConfig) | row 5, col 10 |
| `split_root()` | where it is SPLIT — chosen, centred | row 55, col 55 |
| `cleared_root()` | the least nudge that fits, for tests | row 9, col 13 |

Decoupling the two is what made the split position choosable on merit rather
than forced to whatever a human can conveniently reach. Row 55, col 55 was
chosen because it is the only position class with room for the 16-piece tree
and the place where a corner-picking scale error costs least. The cost is 50
electrodes of travel, and transport is where liquid gets lost.

## What is derived rather than proven

`params.py` marks each value. **PROVEN** means a literal from
`colormixing/csvvolcont.py`; **DERIVED** means computed from those ratios for a
geometry csvvolcont never performs. Two gaps are real and flagged:

1. **csvvolcont only ever splits along WIDTH.** Every drop in it is 10 tall
   from load to merge; height is never a split axis. An 8-piece tree needs at
   least one height split, so the height-axis behaviour is a derivation and is
   the main thing in this plan hardware has not agreed to.
2. **csvvolcont dispenses, it does not halve.** It takes a 10-wide piece off a
   15-wide remnant. A symmetric split is a different geometry and its placement
   is derived from the ratios, not copied.

See [Provenance](provenance.md) for why csvvolcont and not `1pixsplit.py`.

## See also

- [The clearance gate](clearance-gate.md) — what refuses a plan that does not fit
- [Volume equality](volume-equality.md) — what "equal pieces" is and is not claiming
- [Separation and dwell tuning](separation-and-dwell-tuning.md) — the stretch ratios actually in use
