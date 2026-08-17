# Volume equality

What "eight equal pieces" claims, what it does not, and the one assumption it
rests on. Extracted from `microdrop/splitplan.py` and `microdrop/params.py`.

## The method

Equality is read off the **activated electrode area** of each piece — the pixel
size of the activated region, in electrode units.

This works because equality is a *ratio*, and the plate gap cancels out of a
ratio:

```
V = A × g        so        V_a / V_b = A_a / A_b       when g is the same
```

Equal footprint therefore gives equal volume without ever knowing the gap.

The method changed on 2026-08-13. It used to be: measure `ChipConfig.gap_um`,
image the split pieces, compare. That was blocked on a measurement nobody had
taken, so volume equality sat permanently UNVERIFIED and the package could only
claim footprint. The area proxy makes *"these eight pieces hold the same
amount"* decidable exactly, from geometry, today.

Centre-out erosion is what makes the two counts equal in the first place — it
removed the systematic bias that one-sided erosion built in.

## What is still not claimed

**No absolute volume.** The proxy gives ratios, not quantities. The unmeasured
gap means no nanolitre figure exists here or anywhere in this repo, and there
must not be one. That needs the gap's value, which the proxy does not supply.

**Not verified against the rig.** The claim is a property of the *plan*.
Nothing has measured the liquid. It is checkable without new tooling —
`detector` reports observed blob area in the same electrode units — but it has
not been checked.

## The four assumptions

Listed in full in `splitplan.VOLUME_EQUALITY_ASSUMPTIONS`, and printed with
every verdict so the caveat travels with the claim.

### 1. Uniform plate gap — the load-bearing one

Volume is area × gap, so equal area means equal volume only if the gap is the
same under both pieces.

Note this is **not the same unknown as the gap's value**. The proxy needs only
that the gap is the *same* under both pieces, not what it is — which is exactly
why it works at all. But that sameness has never been checked either. A tilted
or unevenly-compressed top plate breaks it, and breaks it worst for the pieces
furthest apart, which after three splits is every pair that matters.

### 2. Liquid fills the activated footprint, and only it

The proxy counts commanded electrodes, not liquid. Bulge past the contact line,
an incompletely wetted cell, or liquid still bridging where the neck opened all
make the count and the contents disagree.

### 3. Equal perimeter effects

The meniscus lives at the edge, so its share of the volume scales with
perimeter, not area. Two pieces of equal area but different aspect ratio (10x5
against 5x10) have equal perimeter and are fine; a tree mixing aspect ratios at
the same stage would not be. The default W,H,W tree keeps every leaf 10x5, so
this holds by construction — **re-check it if the axis order changes.**

### 4. Nothing was lost

Satellites thrown during the break, and residue left on the eroded neck, are
volume that left a piece without changing the electrode count of what remains.
The tree is symmetric, so losses should be symmetric too — but "should be" is
the assumption.

## What the proxy cannot see

The proxy counts electrodes, so it is blind to anything that moves liquid
without changing the count. The important case:

**An asymmetric split would still report `equal`.** If the two children of a
split were placed at different distances from the parent centre, their neck
stubs would have different lengths and the neck would drain unevenly — but both
children would still be the same size, so `volume_equality` would report
`equal=True` and the same `area_electrodes`.

This is one of the reasons per-piece widening is refused rather than merely
discouraged. See
[why per stage and not per piece](separation-and-dwell-tuning.md#why-per-stage-and-not-per-piece).

## See also

- [The symmetric split algorithm](symmetric-split.md)
- [Separation and dwell tuning](separation-and-dwell-tuning.md)
