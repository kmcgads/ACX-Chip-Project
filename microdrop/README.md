# `microdrop/`

Symmetric droplet splitting: plan a tree that halves one droplet into 2ⁿ equal
pieces, and drive it on hardware with a human in the loop.

**No camera.** Nothing here imports cv2, numpy or any calibration file.
Positions are electrode indices commanded straight through `ActivateElec`, so
no homography is involved and none is needed. The cost is that the operator's
y/n answers are the only evidence anything actuated — this API has no
per-electrode readback.

## Files

| File | Purpose |
|---|---|
| `splitplan.py` | The planner. Pure geometry — no DLL, no USB. Builds the tree and the ordered frames that realise it. |
| `params.py` | Split parameters, each marked PROVEN (a literal from `csvvolcont.py`) or DERIVED. |
| `protocol.py` | `SplitSession` — drives a plan on hardware with operator gates. Also the general-purpose CLI. |
| `run_8piece_split.py` | Fixed-configuration runner: 8 pieces of 10×5. |
| `run_16piece_split.py` | Fixed-configuration runner: 16 pieces of 5×5. |
| `workingressplit.py` | Split / move / **merge** — 3 splits, 2 merges into a growing reservoir. Every frame ran on hardware 2026-08-21 as a prefix of the run below; stopping at that point has not itself been run. |
| [`testing/`](testing/) | Experimental runners, below. |

## `testing/`

Scripts for geometries being investigated rather than relied on. Same
conventions as the runners above — armed-only, `check_geometry()` guard,
operator gates — but each is an experiment.

| File | Status |
|---|---|
| `testing/symmovressplit.py` | Split / move / merge, 4 splits and **3 merges** into a growing reservoir. ✅ **Confirmed on hardware 2026-08-21** — the first merge sequence this repo has confirmed. One run; see the header on why that is not yet a reproducibility claim. |
| `testing/6pixsplit.py` | 6×6 → four 3×3, the smallest pieces planned here. Three live runs have failed to separate; the header records why more stretch is probably the wrong knob. |
| `testing/scaleladder.py` | The same H,W tree at 20×20 / 16×16 / 12×12 / 8×8 / 6×6, to find the size at which splitting stops working. Not yet run. |

> **Merging has no implementation in `microdrop/`.** It is hand-built frame
> construction in the two reservoir scripts — no planner support, no tests. The
> 2026-08-21 run evidences the *behaviour*; the *code* is still unguarded by
> anything but an operator's eye.

## Usage

```bash
# hardware-free: plan, clearance verdict, volume claim. No USB handle.
python -m microdrop.protocol --plan-only --axes WHWH

# fixed configurations (Windows, chip connected, ALWAYS ARMED)
.\.venv\Scripts\python.exe microdrop\run_8piece_split.py
.\.venv\Scripts\python.exe microdrop\run_16piece_split.py

# general-purpose, live
python -m microdrop.protocol --arm --axes WHWH --stretch-stage 2:2.2
```

> ⚠ Both runners are **always armed** — no dry run — and neither geometry is
> currently confirmed on hardware. See
> [Running the split scripts](../docs/guides/running-the-split-scripts.md).

## How a split works, briefly

Each split **stretches** the parent to ~1.75× along one axis, centred, then
**erodes** the neck from the middle outwards, one electrode per side per frame,
leaving two children of exactly half the parent's footprint at the two ends.

Two properties are exact and tested at every frame of every stage: each split
is a 50/50 division, and each frame is mirror-symmetric about its parent's
centre line.

A 20×20 droplet bottoms out at **16 pieces of 5×5** — 20 = 2² × 5, so four
halvings is all divisibility allows and the planner refuses a fifth rather than
guessing which child gets the extra electrode.

Full detail: [The symmetric split algorithm](../docs/guides/symmetric-split.md).

## Design notes worth knowing before changing anything

- **Separation is not a setting.** `neck_gap = stretch_to(e) - e`. The only
  lever on how far apart two children land is how far their parent was
  stretched first. See [Separation and dwell tuning](../docs/guides/separation-and-dwell-tuning.md).
- **Overrides are per stage, never per piece.** Giving the two children of one
  split different treatment breaks the symmetry invariant *and* reintroduces a
  volume bias that `volume_equality` cannot detect.
- **The runners have no flags on purpose.** Each is a record of one geometry;
  `check_geometry()` refuses to run if the planner stops producing it.
- **Volume equality is an activated-area proxy**, not a measurement, and there
  is no nanolitre figure anywhere in this repo. See
  [Volume equality](../docs/guides/volume-equality.md).

## Dependencies

`chiphealth.actuation` (the DLL binding and `ChipController`),
`chiphealth.clearance` (the off-grid gate) and `chiphealth.config`
(`ChipConfig`). Nothing in `chiphealth` depends on `microdrop`.

## Tests

`tests/test_splitplan.py` and `tests/test_protocol.py`. `TestNoVisionStack`
hard-blocks cv2 and numpy at the import hook and runs the entire protocol
armed, so the no-camera claim is executable rather than a promise.
