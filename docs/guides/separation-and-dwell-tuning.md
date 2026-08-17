# Separation and dwell tuning

Why the split stages have the stretch ratios and dwell times they do, what was
tried, and what each knob costs. Extracted from the module docstrings of
`microdrop/run_8piece_split.py` and `microdrop/run_16piece_split.py`.

**Status: none of the tuning on this page is confirmed on hardware.** Every
value here was reached by adding margin against a failure whose cause has not
been identified. Read [The thing to understand first](#the-thing-to-understand-first)
before trusting any number below.

## The thing to understand first

On 2026-08-13 the 8-piece split ran live and separated. On 2026-08-17 the same
script, with the same hardcoded numbers and the same plan frame for frame, did
**not** fully separate at its last stage.

The geometry was identical across both runs. Whatever differed, it was not the
geometry.

Everything on this page widens or slows the geometry anyway, because that is
what is reachable from software. It buys margin against a cause still
unidentified. If a widened, slowed stage still fails, the answer is in this
list and not in a larger number:

| Candidate | Why software cannot rule it out |
|---|---|
| Droplet volume at load | The load gate is a human eyeball on a 20x20 rectangle |
| Filler oil | Not modelled anywhere in this repo |
| Chip surface state, residue from a prior run | No per-electrode readback exists |
| Rail voltage under load | `InquireVolt` reads 9 global rails, not what a 5x5 piece sees |
| Dwell at the smallest pieces | Partially tested — see [the dwell experiment](#the-dwell-experiment) |
| Plate gap | `ChipConfig.gap_um` is `None`; unmeasured |

## Separation is not a setting

There is no margin, gap or spacing parameter anywhere in the planner. From
`microdrop/params.py`:

```
neck_gap(e)   = stretch_to(e) - e
stretch_to(e) = _round_even(e * stretch_ratio)
```

The two children of a split are placed at the two ends of the stretched
extent, so how far apart they land is *entirely* determined by how far their
parent was stretched first. The only lever is `stretch_ratio`.

The gap must be **even**. The stretch is centred on the parent and the erosion
runs centre-out, so an odd gap can be halved neither in space nor in erosion
order. `_round_even` enforces it and `_stretch_origin` raises on an odd
surplus.

## What widening costs

Widening a split pushes each child outwards — towards the **neighbouring
group's** child. Sibling separation and non-sibling separation therefore move
in opposite directions.

### 8-piece tree, widening the last stage only

| ratio | sibling sep | nearest non-sibling | tree frames | violations |
|---|---|---|---|---|
| 1.75 (proven) | 8 | 8 | 87 | 0 |
| **2.2 (in use)** | **12** | **4** | **103** | **0** |
| 2.4 | 14 | 2 | 111 | 0 |
| 2.6 | 16 | 0 | 119 | 6 |

2.4 puts siblings 14 apart but leaves two settled pieces from different groups
only 2 electrodes apart — the planner's own floor, and a merge risk in the
other direction. 2.6 collides outright. 2.2 is the largest comfortable step.

### 16-piece tree, per-stage combinations

| stage ratios (0,1,2,3) | sibling sep | nearest non-sib | frames | worst aspect |
|---|---|---|---|---|
| 1.75, 1.75, 1.75, 1.75 | 8 | 8 | 159 | 3.6 |
| **1.75, 1.75, 2.2, 2.2 (in use)** | **12** | **4** | **207** | **4.4** |
| 1.75, 1.75, 2.4, 2.4 | 14 | 2 | 231 | 4.8 |
| 2.2, 2.2, 2.2, 2.2 | 12 | 12 | 231 | 4.4 |

The last row removes the crowding entirely, by spreading the four groups apart
before the late splits happen. It was rejected for now because it changes
stages 0 and 1, which have never failed.

## Aspect ratio is the real ceiling

Not chip margin. The 16-piece tree occupies rows 43–86 and cols 43–86 of a
128x128 array — 42 free electrodes on every side. Space is not the constraint.

`microdrop/splitplan.py`'s axis-ordering table records **3.6** at full stretch
as joint-best, and **7.2** as *"where liquid breaks up and throws satellites
unbidden"*. Per stage, at the proven ratio:

| stage | axis | parent | stretch | aspect |
|---|---|---|---|---|
| 0 | W | 20x20 | → 36 | 1.8 |
| 1 | H | 20x10 | → 36 | **3.6** |
| 2 | W | 10x10 | → 18 | 1.8 |
| 3 | H | 10x5 | → 18 | **3.6** |

The H stages are the expensive ones — they stretch the long axis of an
already-narrow parent. **Stage 3 was already the worst in the tree**, so
widening it is the most aspect-expensive change available. Stage 2, on a square
10x10, is nearly free (1.8 → 2.2).

If a widened stage 3 throws satellites where the unwidened one merely failed to
part, that is this trade-off landing badly and stage 3 should go back to 1.75.

## Why per stage and not per piece

The natural idea — push the outward-facing child of each split further out and
leave the inward-facing one alone, using the free chip outside the tree rather
than the crowded space inside it — is aimed at exactly the right thing, and is
refused anyway.

**It breaks the symmetry invariant.** Each split must be mirror-symmetric about
its own parent's centre line. `splitplan._stretch_origin` raises on an odd
surplus, and `TestSymmetry` checks every frame, not just the end state.

**It reintroduces a volume bias that the existing check cannot see.** Unequal
placement gives the two neck stubs unequal lengths, so the neck drains unevenly
into the two children — precisely the csvvolcont bias that centre-out erosion
exists to remove. `volume_equality` would still report `equal`, because it
counts activated electrodes and both children are still the same size.

**There is no outer-vs-inner distinction to exploit anyway.** At every stage,
all parents sit at the same distance from the tree centre along the axis being
split: stage 2 at cols 47/47/73/73, stage 3 at rows 47/73.

Differing ratios *between stages* are legal, because symmetry is enforced per
split rather than per tree — `_all_steps` re-derives each step with nothing
held, on the grounds that *"held pieces are elsewhere on the chip and are not
part of the split's own symmetry"*. That is what `stage_stretch_ratios` uses.

## The dwell experiment

Separate hypothesis from widening, deliberately: does a split fail for want of
**time** rather than want of **distance**?

`run_16piece_split.py` holds each of stage 3's 104 frames for 1.0s instead of
the proven 0.5s. Nothing else changes — not the global step delay, not the
approach walk, not stages 0–2.

**Stage 2 is the control.** It is widened exactly like stage 3 and not slowed.

| Outcome | Reading |
|---|---|
| stage 2 parts, stage 3 parts | Widening was enough; the extra time is unproven and should come back out |
| stage 2 fails, stage 3 parts | Points at dwell — the useful outcome |
| both fail | Neither distance nor time at these values; see the table at the top of this page |
| stage 2 parts, stage 3 fails | Stage 3 is harder for a reason neither knob addresses — most likely its 4.4 aspect |

**The control is good but not perfect.** Stage 2 and stage 3 differ in axis
(W vs H) and parent shape (10x10 vs 10x5) as well as in dwell, so a difference
between them is not attributable to time alone. A clean single-variable test
runs the same tree twice:

```bash
# with the dwell
python -m microdrop.protocol --arm --axes WHWH \
    --stretch-stage 2:2.2 --stretch-stage 3:2.2 --settle-stage 3:0.5
# without it
python -m microdrop.protocol --arm --axes WHWH \
    --stretch-stage 2:2.2 --stretch-stage 3:2.2
```

**Where the time goes.** Each of stage 3's eight splits is 13 frames — 6
STRETCH then 7 ERODE — and every one gets the extra 0.5s, so roughly half the
added time is spent stretching and half opening the neck. If the aim is
specifically to give the liquid longer to *part*, the erode frames are the ones
that matter and the stretch frames are along for the ride. Separating them
would need a per-phase dwell, which does not exist.

## Current values

| Script | Stage | Stretch ratio | Neck gap | Dwell |
|---|---|---|---|---|
| `run_8piece_split.py` | 0, 1 | 1.75 (proven) | 16 | 0.5s |
| | 2 (last) | 2.2 | 12 | 0.5s |
| `run_16piece_split.py` | 0, 1 | 1.75 (proven) | 16 | 0.5s |
| | 2 | 2.2 | 12 | 0.5s (control) |
| | 3 (last) | 2.2 | 12 | **1.0s** |

Both scripts pin these in `check_geometry()`, which refuses to run if the
planner stops producing them — including refusing if a widening leaks into the
stages that have worked on hardware.

## See also

- [The symmetric split algorithm](symmetric-split.md) — what a stage actually does
- [Running the split scripts](running-the-split-scripts.md) — operator gates and what a run does not verify
- [Volume equality](volume-equality.md) — why the area proxy would not catch an asymmetric split
