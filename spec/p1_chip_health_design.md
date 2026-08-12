# Priority 1 — Electrode actuation visualization + chip health check

**Design document. No code written.** Approved to design 2026-08-06; implementation gated on a
further explicit go-ahead.

Governed by `spec/objectives.md` §0 (standing requirements), §1 (Priority 1), and the vendor-DLL
findings in `workspace/analysis.md`. Architecture context in `spec/design.md`.

**Design rule for this document, per researcher instruction:** adapt and extend the working
scripts already in `project/`. Nothing here is designed from scratch where existing code covers
it. The reuse map is §9 and is intended to be read as part of the design, not as an appendix.

---

## 1. What this builds

One continuous, operator-attended run that:

1. Sweeps a droplet across the whole chip while showing commanded actuation beside the live
   camera view.
2. Watches for **dragging** and **residue** — the researcher-observed signatures of a bad
   electrode — in real time.
3. Immediately re-tests flagged areas at 4×4 resolution, in the same run, with no liquid reload.
4. Writes a per-run artifact designed to accumulate into a labelled training dataset.

### What it cannot do, restated so it is never quietly assumed

There is **no per-electrode readback in any vendor API** (analysis §2, §16). Every verdict this
script produces is **optical inference**, never a device report. `InquireVolt` is logged, but it
returns 9 global rails and is presented as such. Any function that appears to "check an
electrode" is answering from the client-side model plus the camera — and its docstring must say
so (`design.md` §3).

---

## 2. Run structure

Eight phases, one process, one run ID.

| Phase | Name | Operator involved | Energised |
|---|---|---|---|
| 0 | Preflight | no | no |
| 1 | Load prompt | **yes** | no |
| 2 | Registration | **yes** (first run only) | no |
| 3 | Baseline | no | no |
| 4 | Coarse sweep | no | **yes** |
| 5 | Triage | no | no |
| 6 | Fine pass | no | **yes** |
| 7 | Shutdown | no | no |

### Phase 0 — Preflight

Load config; locate and load `DLLTest.dll`; run the ABI sanity check (`design.md` §5.1 /
ADR-0003 — `InquireVolt` arity probe, refuse to proceed on mismatch); open USB; power on; set
voltage; read voltage back and log it. Open the camera stream. Resolve the arming state.

Adapted from `chipsetup.py:27-53`, with the `input()` calls between every step removed — that is
the §0.1 defect. The only prompts that survive are the ones in Phase 1 and 2, where a human
physically has to do something.

### Phase 1 — Operator load prompt

The script prompts for and expects:

```
initial droplet:  20 × 20 electrodes
position:         row 2, col 5   (top-left region)
→ Drop(height=20, width=20, row=2, col=5)
```

The operator loads silicon oil filler plus the test substance by hand and confirms. The prompt is
**structured and logged** — what was asked, what the operator acknowledged, when — not a bare
`input()` with a discarded return (§0.1).

Droplet manipulation by hand is not the script's concern (researcher); **creating the initial
20×20 droplet is** the only load step the run depends on.

### Phase 2 — Registration (electrode ↔ pixel)

The detector needs to convert between electrode coordinates and pixels. Four chip corners clicked
once in the live window give a homography; it is cached in config and reused while the camera
does not move.

**Electrode pitch is not needed for this** — corner registration gives the full mapping. The pitch
did not block anything here; it would only be needed to report physical units rather than electrode
counts. *(Update 2026-08-10: resolved at 246.48 µm, `objectives.md` §2.1. It was deferred to what
was then Priority 3 and is now Priority 2.)*

**Self-check, free:** the initial droplet is at a *known* position and size. After registration,
the detected blob's centroid must map to approximately (2,5)+10 and its area to ~400 electrodes.
If it does not, registration is wrong and the run aborts before energising anything. This is the
cheapest possible guard against a silently wrong coordinate frame poisoning every verdict.

### Phase 3 — Baseline

Capture N frames with nothing energised. Establishes: per-pixel noise floor, the illumination
field, the resting droplet position, and the "clean chip" reference that residue detection is
differenced against. Written to the artifact.

### Phase 4 — Coarse sweep

**Geometry: a translating 20×20 window in a serpentine raster.**

A correction to how §1.7 phrased this. `cleanup.py` *shrinks* a region anchored at the top-left
corner, which drags everything **inward to a collection point** — that is a collection operation,
and it is the right tool for end-of-run consolidation. It is the wrong tool for coverage: with a
single 20×20 droplet already near the top-left, a shrinking boundary sweeps mostly over dry chip
and observes nothing. To interrogate every electrode the window must **translate**, carrying the
droplet with it.

The translation pattern itself is not new either — it is what `1pixsplit.py` steps 4/7/8 and
`dropsplitoff.py` already do ("move piece N cols, one column at a time").

```
band 1   row   2 : ─────────────────────────────────▶
band 2   row  22 : ◀─────────────────────────────────
band 3   row  42 : ─────────────────────────────────▶
band 4   row  62 : ◀─────────────────────────────────
band 5   row  82 : ─────────────────────────────────▶
band 6   row 102 : ◀─────────────────────────────────
band 7   row 109 : ─────────────────────────────────▶   (clamped; overlaps band 6)
                   col 1 ←──────────────────────→ col 109
```

- Window top-left travels col 1 → 109 (window is 20 wide, so 109+19 = 128).
- Seven bands at top rows 2, 22, 42, 62, 82, 102, and a final band **clamped to 109** so the
  bottom edge is covered; this deliberately overlaps band 6 by 13 rows rather than leaving an
  untested strip.
- **One electrode per step.** EWOD transport requires the activated region to overlap the
  droplet; the window cannot jump. This is the same one-at-a-time discipline the existing split
  scripts use.
- Each step is one `ActivateElec` call with a single `Drop` — identical in form to
  `cleanup.py`'s `activate()`, translating instead of shrinking.

**Cost:** ~880 steps (4 + 7×108 + 6×20) ≈ **7.3 min** at the default 0.5 s, ~3.7 min at 0.25 s.

Worth stating plainly: this is **not much faster in raw delay** than the 1,024-block estimate.
The win is that it is one continuous unattended run with liquid present throughout, versus 1,024
tests each needing a human to put liquid in the right place. The operator cost differs by orders
of magnitude even though the clock time does not.

> **Known anisotropy.** A horizontal serpentine mainly exercises **column-to-column** transitions;
> an electrode's row-direction behaviour is only tested at band edges. A second, vertical sweep
> would cover the other axis at double the cost. Offered as a flag (`--sweep both`), default off,
> and the coverage map records which axis a verdict came from.
>
> **Edge strips.** Row 1 and any column outside window travel are never a leading edge. They are
> recorded **unknown**, not pass.

### Phase 5 — Triage

Pure computation, nothing energised. Cluster the events from Phase 4 into candidate regions,
rank by severity, and emit the fine-pass target list. A configurable cap on the number of targets
keeps the fine pass bounded; **anything dropped by the cap is logged explicitly** rather than
silently ignored (§0.1's no-silent-truncation principle).

### Phase 6 — Fine pass — and the problem the no-reload rule creates

No manual reload between stages (researcher requirement). The sweep ends with the droplet
wherever band 7 left it. Two consequences, both real design work:

**(a) The fine pass is a transport problem.** Reaching a flagged region means driving liquid
there first. Travel dominates the cost, not the 0.5 s test. The droplet can get **stuck en
route** — so `unreachable` is a first-class outcome and is itself health information, not a
script failure. Visit order matters because liquid is lost along the way; nearest-first, replanned
after each target.

**(b) A 20×20 droplet cannot localise a 4×4 block.** Its contact line is 20 electrodes wide, so
lag localises to that edge, not to a 4×4 cell. To get real 4×4 resolution the run must **split a
small probe droplet** (~5×5) off the main one and walk the probe over the flagged blocks.

That is not new capability — `dropsplitoff.py` and `1pixsplit.py` already do exactly this split,
down to a 5×3 piece, including the constraint recorded in `1pixsplit.py`'s header that *piece
height must match the drop being split*. The fine pass reuses that sequence.

> **Honest risk.** Splitting is the most failure-prone step in the whole run. If the split fails,
> the fine pass **degrades gracefully** to 20×20-edge localisation and records that it did so —
> the run still produces a coarse map rather than aborting. It does not retry indefinitely.

### Phase 7 — Shutdown

De-energise (`ActivateElec(rows, cols, 0, None)`, `cleanup.py:157`), power off, close USB, close
camera, finalise artifacts. Wrapped so it runs on **every** exit path including exception and
Ctrl-C — the §0.1 handle-leak defect. Optionally run `cleanup.py`'s shrink-to-corner to collect
liquid for the operator, which is what that routine is genuinely good at.

---

## 3. Module structure

Per the confirmed decision (§1.9): one process, three modules, threaded capture, plus a separate
offline tool.

```
project/
  camera.py                 ← EDITED IN PLACE, back-compatible (§8)
  chiphealth/
    config.py               paths · chip geometry · thresholds · run params
    actuation.py            DLL load · ABI check · arming · activate()
    detector.py             PURE: (frames, commanded state) → verdicts
    recorder.py             video · stills · events · JSONL · coverage map
    sweep.py                path planning: bands, serpentine, fine-pass routing
    run_health.py           orchestrator: phases, prompts, live window
  rescore.py                OFFLINE re-analysis (separate program)
```

**`detector.py` is a pure function** — no hardware, no file I/O, no camera. That is what allows
drag detection to be developed and tested on recorded video on this Linux box with no rig
attached, and it is what `rescore.py` re-runs over historical recordings.

**`sweep.py` is pure too** — band/step planning is arithmetic, unit-testable without hardware.

> **Deliberate deviation from `design.md` §4.** The design doc specifies a full `acxchip/`
> package with `l0_transport` … `l4_experiment`. This is a **first test-run script meant to be
> iterated on** (researcher), and imposing five layers on it now would slow that down. `chiphealth/`
> is flatter on purpose. The layering is not abandoned: `actuation.py` is the future
> `l1_primitives/chip_dll.py` + part of `l2_subsystems/chip.py`, and reconciliation happens when
> a second subsystem needs the shared L0. Flagging rather than silently diverging.
>
> *(Update 2026-08-12: this originally said "when Priority 2 lands", meaning the axis. The axis is
> deferred — `objectives.md` Appendix A — so there is no second subsystem scheduled and this
> reconciliation has no date. The divergence stands until one arrives.)*

---

## 4. Detector design

Three primitives, from the researcher's directly observed failure signatures (§1.7).

**Inputs to every verdict:** the frame(s), the commanded `Drop` at that instant, and the
registration homography. Detection is always *observed vs. commanded* — never observed alone.

### 4.1 Drag / lag — the primary signal

The researcher-observed behaviour: the droplet visibly drags as it passes over a bad spot.

At each step the commanded window has a leading edge at a known electrode column. Locate the
droplet's actual leading contact line, convert to electrode coordinates, and take the difference:

```
lag = commanded_leading_edge − observed_leading_edge     (in electrodes)
```

Lag of 0–1 is normal transport latency. Lag exceeding a threshold, **sustained over several
consecutive steps**, is a drag event localised to where the edge stalled. Requiring persistence
is what separates a genuine sticky spot from a single-frame detection wobble.

This is the primitive that cannot work on a single frame, and therefore the one that dictated the
single-process architecture.

### 4.2 Residue

Maintain a **swept mask** of electrodes the window has already left. Any liquid detected inside
the swept region — differenced against the Phase 3 baseline — is residue, localised directly to
electrode coordinates.

Residue is the researcher's second observed signature ("part of the droplet gets left behind")
and it has a useful property: it **persists**, so it can be confirmed in a later frame rather
than caught live.

### 4.3 No movement

Droplet centroid unchanged across K consecutive steps while the window is translating.
Distinguished from drag: drag is *partial* response, no-movement is *none*.

### 4.4 Unreachable

Fine pass only. Transport failed to arrive within the expected step count.

### 4.5 Thresholds

Every threshold (lag electrodes, persistence steps, residue area, centroid tolerance) lives in
`config.py`. **Initial values are estimates and will be wrong.** There is no ground-truth bad
region on this chip (§1.4), so the first runs are threshold calibration, not measurement, and the
artifact is designed so those runs can be re-scored offline once better values are known. Verdicts
from early runs must be read as provisional.

---

## 5. Visualization

A single live window, two panes: **commanded frame** (rendered from the client-side model) beside
the **camera view** with detector overlays — swept mask, current window edge, and event markers as
they fire. This is the "show electrode actuation taking place" half of Priority 1, and it is also
how the operator sees a run going wrong early enough to stop it.

---

## 6. Data capture

Three independent streams, per §1.8 and the 5-second cadence requirement.

| Stream | Cadence | Purpose |
|---|---|---|
| **Continuous video** | every frame, whole run | recoverable ground truth; makes false negatives mineable later |
| **Routine stills** | every **5.0 s** (configurable) | uniform time series across the run |
| **Flagged stills** | on every trouble event | the review set for teaching the detector |

Routine and flagged stills are **stored separately** so the researcher can review only the
flagged ones — which is the stated purpose. A still is flagged if it falls inside an active event
window; the same image is never silently reclassified.

### 6.1 Artifact layout

```
runs/<run_id>/
  run.json           params · chip_id · environment · versions · operator prompts+acks
  timeline.jsonl     one record per step: idx, commanded Drop, t, frame idx, lag
  events.jsonl       one record per trouble event
  stills/routine/    5-second cadence
  stills/flagged/    event-associated
  events/            <event_id>_roi.jpg + <event_id>_full.jpg
  video.mkv          continuous
  baseline/          Phase 3 frames
  coverage.json      32×32 block verdict map
  summary.md         human-readable
```

`run_id` is a UTC timestamp. `chip_id` is operator-supplied and **mandatory** — without it,
longitudinal tracking silently mixes chips.

### 6.2 Event record

Per §1.8: `run_id`, `event_id`, timestamp, frame index, electrode row/col range, block id,
`failure_kind` ∈ {`drag`, `no_movement`, `residue`, `unreachable`}, severity metric (lag in
electrodes / residue area), commanded `Drop` at that instant, stage (`coarse`/`fine`), sweep axis,
and the run parameters (voltage, delay, focus state, block size).

Plus the four dataset-integrity fields from §1.8, which exist **only** to make the eventual ML
work possible:

- `detector_version` — so re-scored history stays distinguishable from original labels.
- `label_source` ∈ {`auto`, `human_confirmed`, `human_corrected`} — a model trained purely on
  heuristic labels can at best imitate the heuristic. This field is what lets the dataset exceed
  it. The script only ever writes `auto`; `rescore.py` is where a human promotes labels.
- **Matched negatives** — healthy regions sampled and saved at a configurable rate. An
  all-positive dataset cannot train a classifier.
- Stable field names, versioned schema.

### 6.3 `rescore.py` — offline

Reads a saved run, re-runs `detector.py` at a new version over the recorded video, and writes new
labels alongside the originals without touching hardware. This is what makes improving the
detector cheap: every past run is re-labelled, and no oil gets loaded.

---

## 7. Arming and safety

Dry-run is the default. Arming is **one obvious step**, documented at the top of `--help`, per
the researcher's requirement that the gate must not obstruct live testing:

```
--arm            |  Chip(arm=True)  |  ACXCHIP_ARM=1
```

Dry-run runs the entire pipeline — camera, detector, recorder, live window — and logs intended
frames without energising. That makes it a genuine test mode for everything except the physics,
not a crippled mode.

De-energise on every exit path (Phase 7). 45 V per `chipsetup.py:47`; full-chip activation at
45 V is already routine in `cleanup.py`, so the sweep introduces no new electrical exposure.

---

## 8. Changes to `camera.py` — edited in place, back-compatible

**Hard constraint:** `masterscript3.py:37`, `bayesxcam.py:31`, and `bayesopttest1.py:492` all
`from camera import CameraInterface`. Existing method signatures and behaviour must not change.

| Change | Why | Back-compat |
|---|---|---|
| Persistent capture — `open_stream()` / `close_stream()` / context manager | live view cannot pay a device-open per frame (§0.2, approved) | `take_picture` uses the open stream if present, else falls back to today's open→read→release |
| Autofocus **off** for measurement runs | §1.4 — manual focus; removes ΔE variance | new default-off parameter; existing calls unchanged |
| `detect_droplets_wide()` — returns **all** blobs | `detect_drop_color` returns only the single largest contour (`camera.py:88`), which cannot work when the frame holds a droplet *and* residue spots | new method; `detect_drop_color` untouched |
| Area threshold derived from registration | `min_area=500` (`camera.py:59`) discards 1–2 electrode residue — the exact evidence we want | new method only; old default preserved for old callers |
| Electrode ↔ pixel transforms | needed by every detector primitive | new methods |

The HSV/contour approach itself is kept — it is what already works, and per §0.2 the vendor's
ONNX detectors are excluded. Thresholds must not be hard-tuned to dyed water, since the test
substance will change (§1.4).

---

## 9. Reuse map — what comes from where

Per the researcher's instruction that nothing be designed from scratch where working code exists.

| Element | Source | Adaptation |
|---|---|---|
| `Drop` struct, DLL load, `activate()` helper | `cleanup.py:16-37` | deduplicated into `actuation.py`; `Drop` layout pinned by contract test (analysis §2) |
| Chip constants, `STEP_DELAY = 0.5`, 45 V rails | `cleanup.py:41-72` | moved to `config.py`, all adjustable |
| Init/open/power/set-volt/read-volt sequence | `chipsetup.py:27-53` | `input()` gating removed |
| Full-chip activation + shrink-to-corner | `cleanup.py:109-158` | reused as **end-of-run consolidation**, not as the sweep |
| Deactivate-all | `cleanup.py:157` | shutdown path |
| Translating window, one column at a time | `1pixsplit.py` steps 4/7/8, `dropsplitoff.py` | becomes the serpentine sweep |
| Split-to-small-piece sequence | `dropsplitoff.py`, `1pixsplit.py` | produces the fine-pass probe droplet |
| `CameraInterface`, open/close, `take_picture`, HSV detection | `camera.py` | extended in place (§8) |
| DLL path default | `1pixsplit.py:37` | moved into `config.py` |

---

## 10. Testing

Per `design.md` §7 — no hardware and no Windows on the dev machine.

| Tier | Against | What it covers here |
|---|---|---|
| unit | `FakeBackend` | `sweep.py` band/step planning; `recorder.py` schema; arming gate |
| detector | recorded/synthetic video | drag, residue, no-movement — the pure function, no rig |
| contract | recorded ABI fixtures | `Drop` = 16 bytes `(height,width,row,col)`; `ActivateElec` 4 args; `InquireVolt` 9 pointers (analysis §2) |
| hardware | real rig, Windows | manual, `-m hardware` |

`FakeBackend` simulates a 128×128 grid and emits synthetic frames, including **injected dead
electrodes** — which is the only way to test the detector against known ground truth before the
chip provides any.

---

## 11. Known limits

1. **The coarse pass can miss a bridged single electrode**, and the fine pass only ever sees what
   coarse flagged. Mitigated by random-sample auditing of unflagged regions and by longitudinal
   drift, not eliminated.
2. **Anisotropy** — a horizontal sweep mainly tests column transitions (§ Phase 4).
3. **Thresholds are unvalidated.** No ground-truth bad region exists yet; early runs are
   calibration.
4. **Splitting may fail**, degrading fine-pass resolution to the 20-wide edge.
5. **Sticking from dielectric fouling is indistinguishable from a dead electrode** optically. Both
   are real faults; they are not the same fault.
6. **Detection retuning is required when the sample changes** from dyed water.
7. **Every verdict is optical inference**, never a device report (§1).

## 12. Deferred

~~Electrode pitch~~ ✅ resolved 2026-08-10 at 246.48 µm · plate gap (⏰ still open,
`objectives.md` §2.4 q3 — blocks reporting volumes) · electrical/percentage scoring (§1.6, no
known path today) · the ML model itself — this design produces its dataset, nothing more ·
automatic degradation learning (§1.6) · the shim (`design.md` §9 q1).

---

## 13. Status

Design only. **No code written.** Awaiting explicit approval to implement.
