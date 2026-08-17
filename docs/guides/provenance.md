# Provenance: where the numbers come from

Every split parameter in `microdrop/params.py` is either a literal from a
script that demonstrably works, or a ratio derived from one. This guide records
which is which, and why one source was chosen over another.

## Why csvvolcont and not 1pixsplit

`colormixing/csvvolcont.py` is the basis for the split mechanics.
`basics/1pixsplit.py` is **not** a reliable basis for this work (researcher,
2026-08-13) and is not cited by anything in `microdrop/`.

This is not a new position for the repo — `chiphealth/config.py` already
reaches for csvvolcont where the two disagree on timing, calling it *"the one
legacy script that sets voltage with no human in the loop, and therefore the
only proven timing this binding can actually match"*.

The mechanical difference that matters most for splitting:

| | Approach |
|---|---|
| `1pixsplit.py` step 3 | One `ActivateElec` call patterns reservoir + piece and asks the liquid to snap apart in a single frame. Its own comments say *"No neck loop"*, twice. |
| `csvvolcont.py` step 3 | The neck is eroded over `gap+1` frames, one electrode per frame, with 0.5s at each. **The break is walked, not snapped.** |

## PROVEN values

Literals from `colormixing/csvvolcont.py`. Line numbers refer to commit
`834d4b2`.

| Constant | Value | Source |
|---|---|---|
| `PROVEN_SPLIT_AXIS_EXTENT` | 20 | L221 — the load footprint along the axis about to be split |
| `PROVEN_STRETCH_TO` | 35 | L230–235 — 20 → 35, one electrode per frame, 15 frames |
| `STRETCH_RATIO` | 1.75 | 35 / 20 |
| `PROVEN_NECK_GAP` | 13 | L214–215 — recorded as trace only; **not inherited** |
| `PROVEN_PIECE_START_W` | 10 | L72 — half the parent extent |
| `PROVEN_TRANSLATE_STEPS` | 25 | L73 |
| `PROVEN_SETTLE_S` | 0.5 | L137 — dwell after every `ActivateElec` |
| `PROVEN_STAGE_PAUSE_S` | 2.0 | L224, L229, L239, L264, L280 — not per-frame |
| `PROVEN_VOLTS` | 45/45/45/0×6 | L162–165 |
| `PROVEN_VOLT_TOLERANCE` | ±2 V | L182–183 |
| `PROVEN_VOLT_SETTLE_S` | 0.3 | L168 |

`pinch_width()` is kept as csvvolcont's original expression, including
`round()`'s banker's rounding, so a plan for csvvolcont's own geometry
reproduces its frames exactly rather than approximately.

## DERIVED values

Computed from the ratios above for a geometry csvvolcont never performs.

**`stretch_to(e)` = nearest even to `e × 1.75`.** At the proven extent 20 the
raw target is 35, which is odd; 34 and 36 are equidistant and the tie goes up,
giving 36. That is +1 electrode of stretch and is **the one place a proven
number moved**. Ties round up so the stretch is never *shorter* than proven — a
thinner neck is the safer direction to err for a break.

**`neck_gap(e)` = `stretch_to(e) - e`.** At extent 20 this is 16, against
csvvolcont's proven 13. The two cannot match and the difference is not a
re-tuning: csvvolcont's 13 is the gap between a 15-wide *remnant* and a 10-wide
*piece* inside a 35-wide stretch, which is a dispense. A halving puts two
10-wide children inside a 36-wide stretch, so its gap is forced to 16. The
derived gap is the arithmetic consequence of halving.

**`stretch_steps` and `erode_steps` are halved.** csvvolcont advances one
contact line per frame; here the stretch is centred and the erosion is
centre-out, so both contact lines move at once and the same surplus is covered
in half the frames. **The per-contact-line rate — the thing the 0.5s dwell is
actually about — is unchanged.**

## The two gaps csvvolcont does not cover

1. **It only ever splits along WIDTH.** Every drop in it is `MAIN_H = 10` tall
   from load to merge; height is never a split axis. It therefore supplies no
   proven numbers for a height-axis split, and an 8-piece tree needs at least
   one. Applying the ratios to the height axis is a derivation, and it is the
   main thing in this plan that hardware has not yet agreed to.

2. **It dispenses, it does not halve.** A symmetric split is a different
   geometry and its placement is derived from the ratios, not copied.

## A cross-check, recorded not relied on

`1pixsplit.py`'s step 6 halves a 15-tall piece into two 5-tall halves with
`S2_GAP = 5`. At `stretch_ratio=1.5` the formula here gives 6, not 5, because
15 is odd and rounds up to an even 16. One electrode apart — and the extra
electrode is exactly what makes the split halvable. That file is not the basis
for anything here; this only records where the two land.

## Anything off this evidence

`stage_stretch_ratios` and `stage_extra_settle_s` both take values off the
proven evidence deliberately. Any run using them is stamped in its report as
not-proven timing or not-proven geometry, so it cannot later be read as one
that used the proven values. See
[Separation and dwell tuning](separation-and-dwell-tuning.md).

## See also

- [The symmetric split algorithm](symmetric-split.md)
- [Volume equality](volume-equality.md)
