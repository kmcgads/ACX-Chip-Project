# Priority 1 — build status: BUILT, blocked on a hardware fault

**Updated 2026-08-12.** All modules written. **326 tests, 324 passing** (the 2 failures are
environment-dependent — see below). Run against real hardware on 2026-08-10: the camera and
detection paths worked, **but no armed run has ever passed the voltage check**, so no armed run's
verdicts are yet interpretable. That fault is the one thing standing between here and a result.

    .venv/Scripts/python.exe -m unittest discover -s tests -t .

> The venv in this repo is a **Windows** venv (`.venv/Scripts/`, not `.venv/bin/`) and does not
> have pytest installed, so `unittest` is the runner. Under WSL, invoke `python.exe` as above.

**The 2 failing tests.** `test_picker_create_returns_none_without_opencv` and
`test_picker_unavailable_says_so_and_still_aborts_cleanly` both assert that `CornerPicker.create()`
returns `None` when OpenCV is unavailable. OpenCV *is* installed in this venv, so they fail here —
and would fail on any machine with cv2. They are testing real behaviour with the wrong setup; the
fix is to mock the import rather than depend on the ambient environment. Not caused by any recent
change, and not a defect in the code under test.

## Status since 2026-08-07

| Change | State |
|---|---|
| Camera bug fixes | ✅ committed |
| Resolution / boundary-filter fixes | ✅ committed, validated |
| Caterpillar transport — coarse sweep | ✅ committed (`ab25606`, `0f6e383`) |
| Caterpillar transport — fine pass | ✅ written and tested, **uncommitted** as of 2026-08-12 |
| First armed runs on the instrument | ⚠ attempted 2026-08-10, **blocked by a voltage fault** |
| A valid armed run | ❌ not yet achieved |

### Caterpillar transport — why every move is now two frames

A one-electrode move used to be a single `ActivateElec` that advanced the window's origin and held
its width, which asks the liquid to release behind and grab ahead in the same instant. It cannot
reflow that fast. Every working legacy script avoids this: `mdmixing.py` grows the region (width
20 → 35) before anything releases, and `cleanup.py` only ever shrinks a corner-anchored region.

A move is now a **grow/release pair** — extend one column into fresh territory holding everything
already energised, then drop the trailing column. `sweep.grow_release()` is the single emitter,
used by the coarse serpentine and by the fine pass's `_walk`. The release frame is labelled
`KIND_RELEASE`, which is what tells the detector and the simulator not to score it: there is no new
leading edge on a release, and judging residue there would flag liquid that is simply still moving.

Consequences worth knowing:

- The coarse sweep is **1798 frames** (899 electrode moves × 2), not 899. At the default 0.5 s
  step delay that is ~15 minutes of delay alone, before camera and analysis time.
- The fine pass costs about 64 s more than it used to, at 24 targets.
- `_transport_to` compares **electrode moves**, not frames, against its budget — otherwise
  `fine_travel_slack` would silently halve and every `unreachable` record would double against
  runs made before the change.
- `_split_probe`'s walk-clear step is deliberately **not** caterpillared. It is `dropsplitoff.py`'s
  hardware-proven sequence and the split is the most failure-prone step in the run, so it was not
  changed without rig evidence. It carries the same hazard; there is a comment saying so.

### Voltage startup now copies `csvvolcont.py` call for call

**2026-08-12.** The values were never wrong — `ChipConfig.volts` has always been
`(45, 45, 45, 0, 0, 0, 0, 0, 0)`, matching every legacy script, and rail 3 has always been
commanded exactly like rails 1 and 2. The divergence was in *how many times* we talked to the
device and *when*.

| | Legacy (all 8 scripts) | chiphealth before | chiphealth now |
|---|---|---|---|
| Call order | `InitUSB`→`OpenUSB`→`SetPower`→`SetVolt`→`InquireVolt` | same | same |
| `SetVolt` args | 9 positional ints | 9 positional ints | 9 positional ints |
| `argtypes` | none | none | none |
| Delay `SetPower`→`SetVolt` | `input()` / none | **2.0 s** | **0** |
| Delay `SetVolt`→read | `input()` / 0.3 s | 0.25 s then polling | **0.3 s** |
| **`InquireVolt` calls** | **1** | **up to 14** | **1** |

`InquireVolt` is not a getter. Analysis §2 records that each call issues a `libusb_bulk_transfer`
and parses an 18-byte `0xAA`-framed response — so the old path made up to fourteen USB round-trips
during power-up where the proven scripts make one. The rails are now read once in
`ChipController.open()` and cached; `verify_voltage()` judges that reading instead of taking its
own. `read_rails(refresh=True)` forces a fresh read when you have physically changed something.

Two smaller fixes in the same pass:

- **The settle loop's early exit was backwards.** It returned as soon as two consecutive polls
  matched — and two consecutive readings of `(0,0,0)` during ramp-up are indistinguishable from a
  settled supply. A slow rail therefore got *less* settle time, not more. The polling survives as
  `--volt-poll`, off by default, and no longer exits early.
- **`FakeBackend` could not model this fault at all.** Its `SetVolt` stored what it was given and
  `InquireVolt` handed it straight back, so a supply that fails to reach its commanded voltage was
  unrepresentable. New `FakeBackend.readback` overrides what `InquireVolt` returns; there is now a
  test reproducing the exact 2026-08-10 reading (commanded 45/45/45, reads 16/15/0).

**This is a sequence fix, not a proven cause.** It removes a real and unjustified divergence from
your working code, which is worth doing on its own terms. It does not establish that the divergence
caused the fault — a rail flat at 0 V is equally consistent with a connection or supply problem. If
rail 3 still reads 0 after this, that is strong evidence the fault is physical. `--volt-poll` is
the tool for watching it.

### Run length: what controls it, and what is safe

`--step-delay` is the dominant term. The sweep is 2054 commanded frames, and every one of them
sleeps for it, so wall-clock ≈ `2054 × step_delay` plus per-frame work.

Measured 2026-08-12 on this machine:

| Configuration | Time | Bound by |
|---|---|---|
| `--simulate --step-delay 0` | **1.9 s** | software only (~0.9 ms/frame) |
| `--simulate --step-delay 0.01` | 25 s | sleep; Windows granularity adds ~1.4 ms per call |
| dry run, real camera, `--step-delay 0` | **~1.5 min** (est.) | camera grab + ~5.4 ms/frame detection |
| armed, `--step-delay 0.5` | **~17 min** + camera | the delay |

The ~5.4 ms is a measured HSV+threshold+contour pass on a 1920×1080 frame. The grab itself is not
measurable without the rig; at MJPG 30 fps expect ~33 ms, hence the ~1.5 min estimate.

**Dry runs have no timing floor and do not need one.** `ChipController.activate` never calls
`ActivateElec` when disarmed, and `open()` skips `SetPower`/`SetVolt` — so no electrode is
energised and there is no liquid being driven. The reflow constraint that makes fast timing risky
with liquid on the chip does not exist. Use `--step-delay 0`; do not use a small non-zero value
like 0.001, which costs ~1.4 ms per frame anyway for no benefit.

For iterating on sweep/detector/recorder logic, `--simulate --step-delay 0` is 1.9 s and skips the
camera entirely. Use the real camera only when the camera path is what you are testing.

### Why 0.5 s, and whether the caterpillar changes it

**Where 0.5 came from: the legacy scripts, and nothing else.** `1pixsplit.py:65` is
`ActivateElec(...)` followed by `time.sleep(0.5)`; `cleanup.py` sets `STEP_DELAY = 0.5`. **No
reflow time has ever been measured on this rig.** 0.5 is "what the working scripts do", not "what
the liquid needs". Worth knowing: `1pixsplit.py` uses the same 0.5 s for its *stretch* loop (a pure
grow, lines 164-169) as for `move_drop`'s translations — legacy does not distinguish the two.

**What the caterpillar changed, quantitatively.** It cuts both ways:

- *Per frame, it asks less.* A legacy translation frame asks the liquid to advance its leading edge
  and release its trailing edge in the same instant. A caterpillar grow asks only that it advance
  one column while everything behind stays energised — it is never unsupported. The release asks
  only that it retract into territory that is still energised.
- *Per electrode of travel, it already doubled the time.* Two frames per move, so at 0.5 s the
  liquid gets **1.0 s per electrode** where the legacy scripts give it 0.5 s.

So the sweep currently runs the liquid at half the legacy speed per electrode, with each frame
making a smaller demand. There is real headroom.

**The defensible faster value is 0.25 s**, and the argument is arithmetic rather than a guess: two
frames × 0.25 s restores exactly the 0.5 s per electrode of travel that the legacy scripts use,
while each individual frame asks strictly less of the liquid than a legacy frame did. It also
returns the coarse sweep to ~7.5 min, its pre-caterpillar duration.

**The honest counter-argument:** `1pixsplit.py` gives a *pure grow* the full 0.5 s, so measured
per frame rather than per move, 0.25 gives our grow frames half what legacy gives a comparable
one. Which framing the physics favours is genuinely unknown, because the reflow time has never
been measured.

**Do not change it on the next armed run.** Voltage is the unresolved blocker. Fixing voltage and
dropping the delay in the same run reproduces the 2026-08-10 problem of not being able to attribute
the outcome. Run at 0.5 first and get a baseline.

**Then measure it rather than guess.** `detector.compute_lag` already records, every step, how far
the contact line trails the commanded edge in electrodes — that is precisely "is the liquid keeping
up", and it is in `timeline.jsonl` for every run. The procedure:

1. First valid armed run at 0.5 → baseline lag distribution. Median near 0 with p95 well under the
   2.0-electrode drag threshold means there is slack.
2. Ramp test over **one band**, not the whole chip: 0.5, 0.35, 0.25, 0.18 — a couple of minutes
   each. Watch where lag starts climbing above the baseline.
3. Adopt the fastest delay where lag stays flat, with margin.

### The ramp test

`--bands N` stops the sweep after N bands (7 at the default geometry), which is what makes step 2
practical. Band 0 alone is 290 frames — 16% of the run — and it is the same traversal every time,
so it is a clean like-for-like comparison across delays. Note band 0 includes the priming leg, so
it is not a *typical* band; that does not matter here, because the comparison is between runs of
the identical path.

| bands | frames | 0.50 | 0.35 | 0.25 | 0.18 |
|---|---|---|---|---|---|
| 1 | 286 | 2.4 min | 1.7 | 1.2 | 0.9 |
| 2 | 546 | 4.5 | 3.2 | 2.3 | 1.6 |
| 7 (full) | 1798 | 15.0 | 10.5 | 7.5 | 5.4 |

```bash
for d in 0.50 0.35 0.25 0.18; do
  .venv/Scripts/python.exe -m chiphealth.run_health \
      --chip-id trial01 --arm --camera 0 --bands 1 --step-delay $d
done
```

**Whole ramp: ~7 minutes of rig time**, camera included. Then compare the `lag` distribution per
run out of `timeline.jsonl` and take the fastest delay where it stays flat.

Two things the flag does deliberately: every row outside the swept bands is reported `unknown` in
`coverage.json`, and the run carries a `PARTIAL SWEEP: N of 7 bands ... NOT a coverage result` note.
A truncated run must never read later as a clean bill of health. Runs below 0.5 also carry the
`NON-DEFAULT TIMING` note, so a ramp run is doubly marked.

### The armed timing guard

`--step-delay` below `armed_min_step_delay_s` (0.25) refuses to start an armed run, naming the
2026-08-10 confound and pointing at `--allow-fast-armed`. Anything below the proven 0.5 is allowed
but written into the run notes as `NON-DEFAULT TIMING`, so a fast run can never be mistaken months
later for a proven-timing one. Dry runs are not gated at all.

### ⚠ The 2026-08-10 armed session — and why its diagnosis is confounded

Sixteen armed attempts. **Every one that recorded a voltage check failed it:**

| Run | Commanded | Read back |
|---|---|---|
| `20260810T205023Z` | 45, 45, 45 | 1, 1, 0 |
| `20260810T205044Z` | 45, 45, 45 | 0, 0, 0 |
| `20260810T210337Z` | 45, 45, 45 | **16, 15, 0** |

Rail 3 read **0 V on every attempt**. The best any run achieved was 16/15/0.

Three runs did sweep (`211057Z`, `211551Z`, `213032Z`, reaching 384 / 184 timeline frames before
being stopped). All three were killed before `finalize()`, so their voltage notes were never
written — **we do not know what the rails were doing during the run that produced the split.**

What those runs recorded: `drag` with the contact line ~4 electrodes behind the commanded edge,
then 69 `no_movement` events, then a `residue` blob of **321 electrodes** — most of a 400-electrode
window's worth of liquid sitting in already-swept territory, with `primary_area` falling 340 → 225.
That is the droplet ceasing to follow and the window walking away from it.

**Three candidate causes, and the artifacts cannot separate them:**

1. **Voltage** — 16 V with one rail dead is plausibly too weak to drag a 20×20 droplet at all.
2. **Step delay** — those runs used `step_delay_s: 0.05`, ten times faster than the 0.5 s default
   and than what `1pixsplit.py` uses. 50 ms is very little reflow time.
3. **Transport pattern** — the simultaneous grab/release, since fixed.

The caterpillar fix is correct on its own merits — the physics is sound and every legacy script
already does it — but **it should not be assumed to be the fix for what was observed.** Any code
comment or note asserting that the 2026-08-10 split was *caused* by the transport pattern is
stating more than the evidence supports. Those runs also predate the fix entirely: their timeline
frames are `travel`/`band`, the pre-caterpillar kinds. The caterpillar has never run on hardware.

## Improvement round — 2026-08-07

Three researcher-requested changes, all landed.

**1. Voltage confirmation gate (new phase 0b).** Previously `InquireVolt` was called and its
result *logged and never checked*. `ChipController.verify_voltage()` now compares the readback
against the commanded rails within `volt_tolerance` (default ±2 V), and phase 0b requires the
operator to confirm before the run continues — refusing to proceed on "n". Without this, a chip
with one dead rail would sweep all 899 moves and report the entire chip as failing, then leave
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

Line counts as of 2026-08-12.

| File | Lines | Role |
|---|---|---|
| `colormixing/camera.py` | 456 | **edited in place** (was 214) — persistent capture, wide-field detection |
| `chiphealth/config.py` | 303 | all former magic numbers, one place |
| `chiphealth/geometry.py` | 320 | homography, electrode↔pixel, registration check |
| `chiphealth/calibration.py` | 233 | corner validation, cache, drift reporting |
| `chiphealth/sweep.py` | 371 | bands, serpentine, `grow_release`, block map, fine routing |
| `chiphealth/detector.py` | 326 | drag · residue · no-movement · unreachable |
| `chiphealth/actuation.py` | 492 | `Drop`, real + fake backends, arming gate, voltage verify |
| `chiphealth/recorder.py` | 454 | coverage map, artifacts, dataset fields |
| `chiphealth/simulate.py` | 181 | synthetic rig with injectable faults |
| `chiphealth/run_health.py` | 1304 | eight-phase orchestrator + corner picker + CLI |
| `rescore.py` | 220 | offline re-scoring, label promotion |
| `tests/` | 2654 | 285 tests |

## Verified by running it

Full synthetic run, 128×128, faults injected at block (3,12) and column 61. Figures re-measured
2026-08-12 — they changed when caterpillar transport doubled the frame count and the row-1
coverage fix closed the last blind spot.

```
.venv/Scripts/python.exe -m chiphealth.run_health --chip-id sim --simulate \
        --dead "3,12;col=61" --headless --non-interactive --step-delay 0
```

- **2054 commanded frames**: 899 coarse grows + 128 fine transports + 1027 releases
- 86 events across all three signatures (`drag`, `residue`, `no_movement`)
- coverage `{unknown: 0, pass: 979, degraded: 10, fail: 35}` — `unknown` is now **0**, where it
  used to be 20, because bands start at row 1
- 45 suspicious blocks found, 24 re-tested, **21 dropped by the cap and named in the notes**
- fine-pass legs alternate `transport` / `release` with zero violations across all 24 targets
- every event fires on a `grow` or `transport` frame; **none** on a `release`
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

## Next: the first *valid* armed run

Steps 1–3 below were completed on 2026-08-10 (ten dry runs, `chip-id trial01`); the camera,
registration and detection paths all work against real hardware. Step 4 was attempted sixteen
times and never passed the voltage gate.

### Prerequisite — resolve the voltage fault

**Do not run armed until phase 0b reports 45/45/45 within ±2 V.** Rail 3 reading 0 V on every
attempt looks like a connection or supply fault, not noise. If phase 0b reports a mismatch, answer
**no**. Confirming past it is what makes the results uninterpretable, and it is the trap the
2026-08-10 session fell into.

### Then

```bash
# 1. dry run, real camera, re-pick corners
.venv/Scripts/python.exe -m chiphealth.run_health \
    --chip-id trial01 --camera 0 --frame-size 1920x1080

# 2. armed, once the dry run looks right and the voltage gate passes
.venv/Scripts/python.exe -m chiphealth.run_health \
    --chip-id trial01 --arm --camera 0 --frame-size 1920x1080 \
    --step-delay 0.5 --axes h

# if the rails still do not reach 45V, watch them ramp:
.venv/Scripts/python.exe -m chiphealth.run_health \
    --chip-id trial01 --arm --camera 0 --volt-poll --volt-settle 3.0
```

`--volt-poll` prints an `InquireVolt` reading every 0.25 s after `SetVolt`. Rising values mean the
supply needs longer — raise `--volt-settle`. Values flat at 16/15/0 mean it has stopped short, and
the problem is upstream of the software.

Re-pick the corners rather than passing `--reuse-calibration`: the cached calibration is from
2026-08-10 and chip reseating invalidates it even when the camera has not moved. Keep
`--step-delay 0.5`; do not repeat the 0.05 used on 2026-08-10.

**Expect** ~1798 coarse frames ≈ 15 min of delay alone, plus camera and analysis time, then a fine
pass of roughly 2 min. Operator prompts at: voltage confirm (0b), load the substance (phase 1,
re-asks up to 3×), focus check (phase 2), and top-up if the droplet shrinks.

**Watch for**

- **Phase 0b** — the whole run's validity rests on this one answer.
- **Phase 2 registration** — centroid error and area ratio. Large error means the corners are
  wrong; abort and re-pick rather than sweeping on a bad frame.
- **`primary_area`** near 400 for a 20×20 window. Drifting toward 200 means liquid loss, and the
  top-up prompt should fire on its own.
- **`residue` with severity in the hundreds** — that is the droplet being left behind, not an
  electrode fault. If it recurs with the voltage good and the delay at 0.5 s, then the transport
  pattern genuinely was not the cause and the next place to look is elsewhere.
- **`no_movement` clusters** early in band 0 — the same reading.
- The timeline should alternate `grow`/`release` throughout, with **no `travel` frames**. A
  `travel` frame means the caterpillar path was bypassed.

Expect the first valid runs to need threshold tuning — that is what they are for, and `rescore.py`
re-scores them offline afterwards, so a run with good voltage is worth keeping even if its verdicts
need retuning.

## Still deferred

- **Plate gap** ⏰ — unmeasured, so `droplet_volume_nl()` returns `None` and volumes are
  unreportable. Needed before Priority 2 can state results in nanolitres
  (`docs/spec/objectives.md` §2.4 q3).
- Electrical / percentage scoring — no known path with the current hardware (`objectives.md` §1.6).
- The ML model itself, and automatic degradation learning.
- Mocking cv2 in the two environment-dependent picker tests.

**Electrode pitch is no longer deferred** — resolved 2026-08-10 at 246.48 µm, in
`ChipConfig.pitch_um`.
