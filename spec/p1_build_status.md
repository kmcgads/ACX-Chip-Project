# Priority 1 — build status: COMPLETE

**Updated 2026-08-07.** All modules written. **224 tests, all passing.** Not yet run against
real hardware — that is the next step and it needs the instrument PC.

    cd project && python3 -m unittest discover -s tests -t .

## Improvement round — 2026-08-07

Three researcher-requested changes, all landed.

**1. Voltage confirmation gate (new phase 0b).** Previously `InquireVolt` was called and its
result *logged and never checked*. `ChipController.verify_voltage()` now compares the readback
against the commanded rails within `volt_tolerance` (default ±2 V), and phase 0b requires the
operator to confirm before the run continues — refusing to proceed on "n". Without this, a chip
with one dead rail would sweep all 901 steps and report the entire chip as failing, then leave
that result in the longitudinal record.

Dry-run reads all zeros because `SetVolt` is skipped, so that case is reported as
*"DRY-RUN: nothing to verify"* rather than as a mismatch — calling it a fault would train the
operator to click past a real one.

> Note on the original omission: `chipsetup.py:48-53` printed the rails and gated on
> `input("Voltage query completed")`. It was stripped under the §0.1 "no `input()` after every
> DLL call" cleanup. That was an over-application of the rule — this prompt was doing real work.

**2. Load-the-substance gate.** Phase 1 already existed and prompted; it now **re-asks** up to
three times and aborts if never confirmed, instead of proceeding on any answer. The instruction
is explicit about oil, substance, size and position.

Also added the **mid-run top-up prompt** that `objectives.md` §1.4 q1 called for and the first
build did not implement: when the droplet falls below `topup_area_frac` (default 50%) of the
commanded window for `topup_after_steps` consecutive steps, the run de-energises, asks the
operator to top up, and resumes by re-establishing the interrupted frame. Capped at
`max_topups` so it cannot nag. This matters because a shrinking droplet is *not* an electrode
fault — untreated, every remaining step would report failure.

**3. Full chip coverage — the gap was 448 electrodes, not 128.**

Measured, not assumed. Row 1 was 128 of it; the other 320 were rows 2–21 × cols 5–20.

*Why bands 1–6 were already fine:* a single-direction band cannot cover both ends — sweeping
right the leading edge is `col+w-1` and can never be below `w`; sweeping left it is `col` and
can never exceed `col_max`. Each band misses ~20 columns at one end, and the **band change
fills them in**: its leading edge is a row sweeping across exactly the columns the next band
will miss. The serpentine heals itself at its own corners. Band 0 had no preceding corner.

Two changes:

- `first_band_row` is now separate from the load position. Bands start at row 1; the run walks
  the window up one row from row 2 first.
- Band 0 gets the corner turn it lacked: out to `col_min + w`, back to `col_min`, then away.

| | Before | After |
|---|---|---|
| Steps | 867 | **901** (+34) |
| Delay at 0.5 s | 7.22 min | **7.51 min** |
| Electrodes never tested | 448 (2.73%) | **0** |
| `unknown` blocks in a real run | 20 | **0** |
| Vertical sweep untested | (had gaps) | **0** (907 steps) |

+4% run time for complete coverage. No new electrical risk — `cleanup.py` energises row 1
routinely as part of full-chip activation.

The run now **verifies its own traversal** via `sweep.untested_electrodes()` and logs
*"Traversal reaches every one of 16384 electrodes"*, or names the count it cannot reach. A
future geometry change cannot silently reintroduce a blind spot.

Still true: this closes *column-direction* coverage. Row-direction transitions are only tested
at band changes — the pre-existing anisotropy, for which `--axes both` remains the answer
(1808 steps, 15.07 min).

---

## What exists

| File | Lines | Role |
|---|---|---|
| `camera.py` | 444 | **edited in place** (was 214) — persistent capture, wide-field detection |
| `chiphealth/config.py` | 190 | all former magic numbers, one place |
| `chiphealth/geometry.py` | 270 | homography, electrode↔pixel, registration check |
| `chiphealth/sweep.py` | 295 | bands, serpentine, block map, fine routing |
| `chiphealth/detector.py` | 326 | drag · residue · no-movement · unreachable |
| `chiphealth/actuation.py` | 375 | `Drop`, real + fake backends, arming gate |
| `chiphealth/recorder.py` | 443 | coverage map, artifacts, dataset fields |
| `chiphealth/simulate.py` | 167 | synthetic rig with injectable faults |
| `chiphealth/run_health.py` | 664 | eight-phase orchestrator + CLI |
| `rescore.py` | 220 | offline re-scoring, label promotion |
| `tests/` | 1557 | 169 tests |

## Verified by running it

Full synthetic run, 128×128, faults injected at block (3,12) and column 61:

```
python3 -m chiphealth.run_health --chip-id chip-A --simulate \
        --dead "3,12;col=61" --headless --non-interactive --step-delay 0
```

- 867 coarse steps + 128 fine-pass steps = 995 activations
- 86 events across all three signatures (`drag`, `residue`, `no_movement`)
- coverage `{unknown: 20, pass: 959, degraded: 10, fail: 35}`
- 45 suspicious blocks found, 24 re-tested, **21 dropped by the cap and named in the notes**
- artifacts: `run.json`, `timeline.jsonl`, `observations.jsonl`, `events.jsonl`,
  `coverage.json`, `summary.md`

Offline replay at the same thresholds reproduces the run **exactly** (86 → 86, +0 −0);
raising the drag threshold to 6 electrodes drops 18 events.

## Bugs found and fixed during the build

Each was caught by a test or a run, not by inspection.

1. **The rig checked the commanded leading edge, not the droplet's own frontier.** A dead
   column produced *zero* events — the blockage vanished as soon as the window advanced past
   it. This is the failure mode a matrix-addressed array most likely has, so it mattered.
2. **Flagged stills were gated on the 5-second cadence**, so an event only produced a review
   image if it happened to coincide with the tick. The two streams are now independent, and a
   flagged capture no longer resets the routine timer.
3. **The probe split ran off the chip.** The sweep ends at col 109; the split placed the probe
   at cols 125–129 on a 128-wide chip. The clamp ignored the walk-away steps and there was no
   leftward mirror. Since the sweep always ends at the right edge, splitting left is the
   *normal* case, not a fallback.
4. **`step.idx` is not unique across a run.** Fine-pass legs are planned independently and
   restart at 0, so keying the observation stream on it paired coarse-pass steps with
   fine-pass observations — and a run re-scored at its own thresholds failed to reproduce
   itself. There is now a monotonic `seq`, with a regression test asserting that `step.idx`
   *does* repeat and `seq` does not.

## Limitations the tests assert rather than hide

- **Block granularity hides the uncovered row.** Row 1 is never swept, but block row 0 spans
  rows 1–4, so it reads `pass` on the strength of rows 2–4. The grid cannot express "partly
  untested", so uncovered rows are carried separately in `coverage.json` and printed under
  *Not reached by any band*.
- **`simulate.py` is a fixture, not physics.** No contact-angle model, no surface tension. Any
  dead cell across the frontier stalls the whole droplet, where a real one would deform and
  partially advance. Passing against it means the pipeline wires together and the detector
  finds faults it is *given* — nothing about whether the thresholds suit the real rig.
- **Thresholds are estimates.** No ground-truth faulty region exists yet. The first runs on the
  instrument are calibration; `rescore.py` exists so they can be re-scored afterwards.

## camera.py back-compatibility — verified

`masterscript3.py:37`, `bayesxcam.py:31`, `bayesopttest1.py:492` all import `CameraInterface`.
Checked by AST: top-level imports are still exactly `cv2, datetime, numpy, pathlib, typing`;
all 8 original methods present with unchanged signatures; `detect_drop_color` untouched; both
new imports (`chiphealth.geometry`, `chiphealth.detector`) are lazy, inside method bodies.

Thirteen methods added: `open_stream`, `close_stream`, `streaming`, `__enter__`, `__exit__`,
`read_frame`, `set_registration`, `registration`, `_require_registration`, `min_area_px_for`,
`liquid_mask`, `detect_droplets_wide`, `observe`.

---

## Per-run corner picking — 2026-08-07 (supersedes the fixed calibration below)

**The camera moves between runs**, so registration is redone every run. The hardcoded-corners
approach described in the next section is superseded: `capture.corners_px` is now a fallback,
not the source of truth.

**Operator picks four corners at phase 2**, after the droplet is loaded. `u` undo, `r` reset,
`enter` accept, `q` cancel. The previous run's corners are drawn as a grey outline and can be
accepted outright with `a`, so a small nudge does not mean picking blind.

**`chiphealth/calibration.py`** (new, pure — no OpenCV, no camera) holds everything that can be
checked before the droplet test. Four points give an *exact* homography fit, so there is no
residual to inspect and a typo calibrates perfectly and wrongly. The guards:

| Check | Catches |
|---|---|
| Point outside the frame | Stray click |
| Corners < 20 px apart | Double-click |
| Collinear / zero area | Degenerate pick |
| Negative signed area | Wrong order or winding — would mirror the whole chip |
| Side ratio > 2.5 | Wrong feature clicked; the 128×128 array is square |

Rejected picks are re-offered up to three times, each rejection logged with its reason.

**Drift is recorded every run**: per-corner pixel movement, max delta, scale change, and a
frame-size-changed flag, all into `run.json` alongside the corners themselves. A jump over
150 px is warned as a likely misclick.

**Why the scale matters.** A moving camera changes magnification as well as position. Detection
adapts on its own — every threshold downstream of registration is in electrode units, and
`min_area_px` is derived from the registration — but the measurement is genuinely noisier at
lower magnification. Recording px-per-electrode is what lets a noisy week be explained instead
of mistaken for degradation.

**Flags:** `--reuse-calibration` (skip picking when nothing moved), `--no-pick` (scripted runs;
requires `--corners` or a reused cache), `--calibration-cache PATH`.

**Run-to-run jitter, quantified.** At ~11 px/electrode a ±5 px pick error is ±0.5 electrode —
about 12% of a 4-electrode block, harmless. A ±20 px error is ±2 electrodes, half a block, which
would smear longitudinal comparisons. Within a run the tolerance is generous; across runs,
consistency of *which feature you click* matters more than precision.

## Fixed camera calibration — 2026-08-07 *(superseded by the above; retained for context)*

The camera is fixed relative to the chip, so the four corners are hardcoded rather than clicked.
Two supporting changes:

**Corners live in config and are recorded in every run.** `capture.corners_px` (TL, TR, BR, BL,
in pixels) and `capture.expected_frame_size`. Both land in `run.json` alongside the *actual*
frame size the camera delivered. Without this, a bad region found months later cannot be told
apart from a remount artifact. `--corners` and `--frame-size` still override for a one-off.

**Frame-size mismatch is refused.** The corners are pixel coordinates, so a different capture
resolution rescales all of them — and nothing else in the run would notice: the homography still
fits, registration near the load position still passes, and the error grows with distance from
it. Phase 2 now compares the delivered frame size against the calibration and stops the run.

**Missing registration now fails cleanly.** A camera run without corners used to die deep inside
`detect_droplets_wide` with a bare `RuntimeError`, after the chip was already powered. Phase 2
catches it and names both `capture.corners_px` and the `--corners` syntax.

### Choosing the corners

- Corners of the **active 128×128 electrode array** — not the glass, substrate, or cartridge.
- Order **TL, TR, BR, BL**.
- A few pixels of imprecision is harmless: one electrode is ~11 px wide at whole-chip framing,
  against a 2-electrode drag threshold. Picking the **wrong boundary** is the real risk — 50 px
  on a ~1400 px span is ~4.6 electrodes of drift at the far corner, enough to manufacture false
  drag along the far edge.
- Four points give an *exact* homography fit, so a typo produces a perfectly successful-looking
  calibration. The phase-2 droplet check catches gross errors (wrong order, flips, rotations) but
  validates near the load position, so it is least sensitive to exactly that scale error.

### What invalidates it

Refocusing (focus breathing shifts magnification on many lenses) · zoom or working-distance
change · capture resolution change (now caught) · **chip reseating or replacement** — the camera
staying put does not mean the array does, and this will happen every time a chip is swapped.

## Next: first run on the instrument PC

Nothing here has touched hardware. Suggested order:

1. `python camera.py` — confirm the camera opens and print the frame size. Divide width by 128
   for px/electrode.
2. `--simulate` on the instrument PC, to confirm the environment.
3. **Dry run with the real camera**, no `--arm`: exercises camera, registration, detector,
   recorder and the live window without energising anything.
4. Live: add `--arm`.

The four chip corners are needed for `--corners "x,y;x,y;x,y;x,y"` (TL;TR;BR;BL). Expect the
first live runs to need threshold tuning — that is what they are for.

Still deferred: electrode pitch (⏰ before Priority 3), electrical/percentage scoring (§1.6),
the ML model itself, automatic degradation learning.
