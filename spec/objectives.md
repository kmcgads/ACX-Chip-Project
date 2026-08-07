# Acxchip — objectives and prioritized roadmap

**Status:** plan only. No code has been written and none will be until each item below is
individually approved. Authored 2026-08-06 from researcher-stated priorities.

**Relationship to `spec/design.md`:** `design.md` describes *architecture* (layers, module
structure, signatures). This document describes *priority, sequence, and scope*. Where the two
disagree, this document wins and the design doc is amended before that piece is built.
Deltas to `design.md` created by this document are listed in §6.

**Relationship to `workspace/analysis.md`:** every claim below about vendor DLL capability is
sourced to a section of the analysis log. Nothing here is new reverse engineering.

---

## 0. Standing requirements (apply to every priority, not just one)

### 0.1 Improve on the existing scripts, do not bolt onto them

The point of the rebuild is to fix what is wrong, not to wrap it. The concrete defects recorded
in analysis §1, which every new piece must avoid:

| Defect in `project/` today | Requirement on new work |
|---|---|
| Heavy duplication — the `Drop` struct, DLL load, and activate helper are re-pasted across 13 scripts | One definition, imported. No script re-declares `Drop` or re-loads the DLL. |
| Hardcoded absolute paths (`C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\...` in `1pixsplit.py:37`) | Paths come from config, resolved at load, with a clear error when absent. |
| Interactive gating — `input()` between every step (`chipsetup.py:30-58`) makes scripts non-autonomous | **Refined 2026-08-06 after §1.4 q1.** Prompts are legitimate *only where a physical human action is genuinely required* — loading oil or sample, adjusting focus. They must not appear after routine DLL calls, which is the actual defect in `chipsetup.py`. Prompts must be structured and logged (what was asked, what the operator did, when), not bare `input()` with a discarded return. |
| Bare `print()` as the only output; no record survives the run | Structured logging to file, with a machine-readable run artifact. |
| Inconsistent open/close — some scripts leak the USB handle on exception | Context managers own connection lifetime. Release on every exit path. |
| No health/readback — the loop is gated on a human eyeballing the chip | This is exactly what Priority 1 exists to fix. |

### 0.2 Camera policy — the researcher's camera, not the vendor's

**Binding constraint.** New code uses the researcher's own camera and the existing
`project/camera.py` connection to it. New code must **not** depend on, link to, or route through
`MvCameraControl.dll`, `camHalcon.dll`, or any Hikrobot/MVS component (analysis §11, §25, §27).

What this means concretely:

- **Model the structure after `project/camera.py`.** `CameraInterface` (`camera.py:9`) is the
  pattern: address validation, an `_open_camera` / `_close_camera` pair, `take_picture` returning
  `(path, frame)`, and HSV-based `detect_drop_color` returning a dict with `area_px` and
  `bounding_box`. New camera code keeps that shape and that vocabulary.
- **Keep the real hardware path.** `cv2.VideoCapture(addr, cv2.CAP_DSHOW)` with MJPG
  (`camera.py:21-22`) is the actual connection to the actual camera. That stays.
- **The vendor's ONNX droplet detectors are deliberately not used.** Analysis §24 found five
  YOLOv5 models wired into the vendor app via OpenCV DNN. We are not adopting them. Two reasons
  beyond your instruction: they are the vendor's measurement path, and one of them
  (`multi_droplet.onnx`) does not exist on disk at all. Detection stays HSV/contour, modelled on
  `detect_drop_color`.

**Held-open capture — ✅ APPROVED 2026-08-06.** `camera.py` opens and releases the device on
*every* call — `take_picture` (`camera.py:45-57`) does open → read → release. That is correct for
one-shot stills and wrong for live view: a visualization loop cannot pay a device-open per frame.
The camera object will hold the capture open across frames, keeping your API shape. This is a
change *to the lifetime model*, not to the connection method or the camera.

> **Scheduling note.** Approved, but it is code, and the standing rule is no code before the
> per-script gate. A held-open capture with no consumer is also untestable in isolation, so I
> intend to implement it as part of the Priority 1 build, not as a standalone edit now. Say the
> word if you would rather have it as a separate change first.

**Autofocus — ✅ DECIDED 2026-08-06: off.** `camera.py:23` currently sets
`CAP_PROP_AUTOFOCUS, 1`. For measurement and test runs it goes off; the researcher sets focus by
hand. This closes the ΔE variance risk flagged in `design.md` §9 q4. Autofocus state must be
recorded in the per-run artifact, since a run done at a different manual focus is not directly
comparable to earlier runs — which matters for the longitudinal tracking in q10.

### 0.3 Process — gated, one script at a time

1. This document is the full plan. It ships first.
2. No code until you approve.
3. Before I begin *designing* or *creating* any individual script, I stop and ask you explicitly.
   One script at a time, in priority order. Not batched.
4. Approval of Priority 1 is not approval of Priority 2.

---

## 1. PRIORITY 1 — Electrode actuation visualization + chip health / coverage

**Goal.** A script that shows electrode actuation happening visually, in real or near-real time,
and evaluates how much of the chip is actually functioning.

### 1.1 The hardware fact that shapes this entire piece

There is **no per-electrode readback on this chip.** This is not a design preference, it is the
ABI (analysis §2, restated in `design.md` §3):

- `ActivateElec(rows, cols, count, Drop*)` sets the **entire 128×128 electrode frame in one
  shot.** There is no call that addresses a single electrode.
- There is **no read-back of electrode state at all.** Nothing reports which electrodes latched.
- `InquireVolt` returns **9 global voltage rails**, not per-electrode state (`chipsetup.py:47-53`).

Therefore "chip health" cannot be *queried*. It has to be **inferred optically**: command a known
pattern, observe what the liquid actually does through your camera, and attribute the difference.
The visualization and the health check are the same mechanism viewed two ways — which is why you
were right to scope them as one script.

Two consequences to state plainly:

- The "commanded" view is exact and free (it comes from our own client-side model of what we sent).
  The "actual" view is optical, noisy, and the hard part.
- **A dead electrode is only observable where there is liquid to move.** An unenergised region
  with no droplet over it looks identical to a working region with no droplet over it. This
  determines the entire coverage method and is the #1 open question below.

### 1.2 What full-fidelity health would require, and why we are not blocked on it

Analysis §16 found that `MicrofluidicsInterFace.dll` — the vendor app's own chip path — exposes
`SendReadICState`, `SendPolling`, and typed error signals, i.e. genuine device-reported health,
plus the `CRC Check Error` / `IC Noraml` / `Electrify Success` status stream that produced the
3,889 events in the logs. Python cannot reach any of it: that DLL is MSVC-mangled C++ over
Qt types and terminates in `InterFace.dll` (analysis §20), so it needs the `acxshim.dll` native
build — `design.md` §9 question 1, still unanswered.

**Priority 1 is therefore designed to work with zero shim and zero native build**, using only the
7 flat-C `DLLTest.dll` exports Python already has. If the shim later happens, device-reported IC
state becomes a *second, corroborating* health channel layered onto the optical one. The optical
method is not a stopgap for it — with no per-electrode readback even in the vendor API, optical
remains the only per-electrode evidence either way.

### 1.3 Scope of the deliverable

- Live view: commanded electrode frame beside the camera's view of the chip, updating together.
- A health/coverage run: drive a defined actuation sequence, observe response per region, and
  emit a per-region pass/fail/unknown map plus a saved artifact (image + machine-readable result).
- **"unknown" is a first-class outcome**, distinct from "fail". Regions never covered by liquid
  during the run are unknown, not broken. A coverage map that silently reports untested area as
  healthy would be worse than no map.
- Global rail health from `InquireVolt` logged alongside, labelled as global — never presented as
  per-electrode.
- Runs against the fake backend with no hardware, per `design.md` §7.

### 1.4 Open questions — ✅ ALL ANSWERED 2026-08-06

**Liquid setup.** Silicon oil as filler, with a test substance on top — currently dyed water,
expected to change to other chemicals later. **All loading is manual, by hand.** This is the real
reason the existing scripts are full of `input()` calls. More liquid *can* be added mid-run, so
the script must be able to **prompt the operator to load more when it needs it**, rather than
assuming everything is present at start. Consequences: the coverage method is
droplet-route-based, not flood-based; the script needs a defined "operator intervention" state
that pauses safely, records what was asked, and resumes; and detection thresholds must not be
hard-tuned to dyed water, since the test substance will change (see `detect_drop_color`'s
saturation parameters, `camera.py:59-66`).

**Arming.** ✅ Yes, add the gate — **with an explicit, easy, documented way to disarm.** This is a
live-hardware tool, not a simulator, and the safety gate must not become an obstacle to real
testing. So: dry-run is the default, and arming is a single obvious step (`--arm` flag,
`Chip(arm=True)`, or `ACXCHIP_ARM=1`), documented at the top of the script's help output. No
multi-step ceremony, no hidden env var. This closes `design.md` §9 q5.

**Pass / degraded / fail / unknown.** ✅ Adopted as proposed, as the default for now. See §1.6 for
the electrical-feedback follow-up this raised.

**Coverage granularity.** **4×4 electrode blocks** (not the proposed 8×8) — finer, but not
per-electrode. On a 128×128 chip that is a 32×32 grid = **1,024 blocks**.

**Chip geometry.** ✅ Confirmed 128×128, matching `ActivateElec(128, 128, ...)` at
`chipsetup.py:70`. **Electrode pitch is not yet known** — the researcher will supply it at
Priority 3. Explicitly *not* blocking P1. ⏰ **I owe a reminder at the start of Priority 3**
(agreed 2026-08-06); see §3.1.

**Voltage.** **45 V**, matching `SetVolt(45,45,45,0,0,0,0,0,0)` at `chipsetup.py:47`. Lower
voltages do not actuate reliably, so a lower setting would manufacture false dead electrodes.
45 V is the **baseline for tracking electrode degradation over time**. If later testing suggests
45 V is itself contributing to degradation, that gets revisited then — noted, not designed around.

**Run timing.** Overall run length is **user-driven via prompts, never preset or hardcoded**,
matching the existing scripts' operating model. Between individual electrode activations, default
to a **0.5 s delay, adjustable** in the script.

**Results display.** Live window showing commanded frame vs. camera view, **plus a persistent
per-run log/data file written every run.** The explicit purpose is longitudinal: tracking device
performance over time and refining the scripts against real results. That makes the artifact
schema a first-class design concern, not an afterthought — it needs stable field names, a run
timestamp, chip identity, and the run parameters (voltage, delay, focus state, block size)
recorded alongside the results, or runs will not be comparable to each other months apart.

**Known dead region.** None available. Electrode degradation is expected to accumulate over time.
The negative-control approach stands for initial validation. See §1.6 for the
degradation-learning follow-up.

**Environment.** ✅ Confirmed: development here against `FakeBackend` with no hardware; real
testing on the Windows instrument PC.

**DLL path.** ✅ Confirmed still current:
`C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll`
(as hardcoded at `1pixsplit.py:37`). Moves into config per §0.1, with this as the default value.

#### Resolved parameters

| Parameter | Value | Source |
|---|---|---|
| Grid | 128 × 128 electrodes | confirmed |
| Block size | 4 × 4 electrodes → 32 × 32 = 1,024 blocks | researcher |
| Voltage | 45 V (degradation baseline) | researcher |
| Inter-activation delay | 0.5 s, adjustable | researcher |
| Run length | user-driven, never preset | researcher |
| Electrode pitch | ⏰ deferred to Priority 3 | researcher |
| Filler / sample | silicon oil + dyed water (sample will change) | researcher |
| Loading | manual, mid-run top-up supported via prompt | researcher |
| Arming | dry-run default, easy explicit disarm | researcher |
| Autofocus | off; manual focus | researcher |
| Artifact | live window + persistent per-run file | researcher |

> **Runtime arithmetic worth seeing before design.** 1,024 blocks at 0.5 s per activation is
> ~8.5 minutes of *delay alone*, before camera, USB, or analysis time — and that assumes a droplet
> is already in position at every block, which it will not be, since coverage is route-based and
> loading is manual. **Full-chip coverage in one sitting is not realistic.** I will design for
> partial, resumable coverage that accumulates across runs, which also happens to be what
> longitudinal degradation tracking needs. Flagging now rather than discovering it mid-build.

### 1.6 Future directions raised by these answers (logged, not designed)

Recorded so they are not lost. **Neither is part of the initial design**, per the researcher.

1. **Electrical feedback instead of optical inference — score electrodes as a percentage rather
   than a category.** This is the right thing to want: a continuous per-electrode score would beat
   a four-state optical category in every way. An honest statement of where it stands today —
   `DLLTest.dll` exposes no per-electrode readback and no current sensing; `InquireVolt` returns
   9 global rails only; and even `MicrofluidicsInterFace.dll`'s `SendReadICState` / `SendPolling`
   (analysis §16), which the shim would unlock, reports **IC and device state, not per-electrode
   state**. So there is **no known path to this with the current hardware and vendor API.** Not
   dismissed — the ways it could become possible are a vendor query about undocumented
   diagnostics, the shim's status stream turning out to be richer than the strings suggest, or
   external instrumentation. To revisit as we build, as requested.
2. **Learn degradation patterns automatically.** With per-run artifacts accumulating from day one
   (q10), the data needed to detect drift and predict failing regions gets collected regardless.
   The initial design does not analyse it, but the artifact schema should be good enough that this
   is possible later without re-running history — which is a design constraint on the schema now,
   and cheap to honour if decided now rather than retrofitted.

### 1.7 Method — two-stage sweep, then targeted fine pass (adopted 2026-08-06)

Supersedes the pure block-by-block proposal. Adopted after the researcher proposed a wide-field
approach and the tradeoffs were assessed.

**Stage 1 — coarse: continuous sweep.** A shrinking/moving activated region sweeping the whole
chip, matching `cleanup.py`'s existing pattern (full 128×128 activation, then 98 shrink steps at
`STEP_DELAY = 0.5` down to a 30×30 corner target). Rationale: in EWOD the observable information
is at the **contact line**, not in the bulk, so a moving boundary interrogates every electrode
once as it passes — whereas a static large-region activation yields roughly one verdict, not one
per electrode. The sweep also drags liquid across the whole chip, which is what makes full
coverage possible without mid-run reloading. `cleanup.py` further establishes that full-chip
activation at 45 V is safe on this rig.

**Stage 2 — fine: tile-by-tile 4×4** on flagged regions only, not the whole chip.

**Observed failure signatures — researcher-confirmed, not inferred.** The researcher has directly
observed on this rig that a droplet passing over a bad electrode **visibly drags**, and sometimes
**leaves part of itself stuck behind**. This is the primary thing to detect — not merely
"did liquid end up here." Three distinct detector primitives follow:

| Signature | Nature | What it measures |
|---|---|---|
| **Drag / lag** | dynamic, inter-frame | actual contact line lagging behind the commanded region edge, measured in electrodes |
| **Residue** | static, post-pass | liquid remaining where the boundary has already swept past |
| **No movement** | dynamic | no response to a commanded transition at all |

Drag is the one that forces real design: it cannot be seen in a single frame. It requires
frame-to-frame comparison against the commanded frame at that instant, which is what drives the
script-structure decision in §1.9.

> ⚠ **`detect_drop_color`'s `min_area=500` default (`camera.py:59`) will reject exactly the
> evidence we most need.** At whole-chip framing one electrode is roughly 100–225 px², so a
> 1–2 electrode residue spot falls under the threshold and is silently discarded. That default was
> tuned for a close-up of a single droplet. It must be re-derived for wide-field use.

**No reload between stages — and the consequence.** Per researcher requirement, there is **no
manual liquid reload or reset between the coarse and fine stages**; the fine pass continues from
wherever the sweep physically left the liquid. Because a `cleanup.py`-style sweep consolidates
everything into a corner block, this means:

- The fine pass is not "activate the flagged 4×4 block." It is **transport the liquid from the
  corner to each flagged region, then test locally.** Cost is dominated by travel, not by the
  0.5 s test.
- Routing may have to cross other suspect regions, and the droplet may get **stuck en route**.
  "Could not reach the target" must be a first-class outcome — and it is itself health
  information, not just a failed test.
- Visit order matters, since liquid can be lost or stranded during the pass. Nearest-first or a
  planned route over all flagged regions. (Conceptually related to the `SafetyDistance` /
  MAPF ideas in analysis §26, which remain **not adopted** — noted only as prior art.)

**Coarse-pass blind spot, carried forward.** A single dead electrode bridged by live neighbours
can be missed by the sweep, and anything the coarse pass does not flag is never handed to the
fine pass. Two mitigations to design in: audit a small **random sample of unflagged regions** each
run to estimate the miss rate, and rely on the longitudinal record, where a degrading region
shows as drift even when individual runs read clean.

### 1.8 Trouble-event capture — core requirement, not an afterthought

Researcher requirement 2026-08-06, motivated by eventually training an ML/agentic model to
recognise sticky-spot behaviour on its own. **Every** detected trouble event automatically
produces a saved image plus a structured record.

Minimum fields per event: `run_id`, timestamp, frame index, electrode coordinates (row/col
range), tile/block id, `failure_kind` ∈ {drag, no_movement, residue, unreachable}, a severity
metric (lag in electrodes, residue area), the commanded frame at that instant, stage
(coarse/fine), and run parameters (voltage, delay, focus state, block size). Images saved as
both a cropped ROI (for training) and the full frame (for context).

Four things that decide whether this dataset is usable later, all cheap now and expensive to
retrofit:

1. **Record continuous video for the whole run, not only event snapshots.** Events are emitted by
   a heuristic detector, so snapshot-only capture makes the coarse pass's **false negatives
   permanently unrecoverable** — you can never mine for the trouble the detector missed. A minute
   or two of MJPG is tens of megabytes; this is the cheapest insurance in the design.
2. **Capture matched negatives.** A dataset containing only trouble events cannot train a
   classifier — there is nothing to contrast against. Healthy regions must be sampled and saved
   too.
3. **Version the detector.** Every record carries a `detector_version`, so that when the detector
   improves and historical runs are re-scored, old and new labels remain distinguishable.
4. **Separate auto-labels from human-confirmed labels.** Initial labels come from an HSV/contour
   heuristic. A model trained purely on them can at best learn to imitate that heuristic — it
   cannot exceed it. A `label_source` field (auto / human-confirmed / human-corrected) is what
   makes the dataset worth more than the detector that generated it. Confirming labels is
   researcher time, not script time; the design just needs to leave the door open.

### 1.9 Script structure — recommendation (researcher decision pending)

See the assessment delivered 2026-08-06. Recommendation in brief: **one process, three separable
modules, threaded capture** — plus a **separate offline re-analysis script**. The split that pays
is online-vs-offline, not camera-vs-actuation.

### 1.5 Vendor-DLL mapping

| Need | Source | Status |
|---|---|---|
| Set electrode frame | `DLLTest.dll` `ActivateElec` | ✅ available now, ABI verified (§2) |
| Power / voltage | `SetPower`, `SetVolt`, `InquireVolt` | ✅ available now |
| Per-electrode readback | — | ❌ does not exist in any vendor API |
| Device-reported IC state | `MicrofluidicsInterFace.dll` `SendReadICState` (§16) | ⛔ shim-gated, out of scope for P1 |
| Camera | researcher's own, via `project/camera.py` | ✅ per §0.2 |
| Vendor camera / ONNX detectors (§24, §27) | — | 🚫 excluded by policy |

---

## 2. PRIORITY 2 — Axis movement, with real error reporting

**Goal.** Real axis control via direct ctypes to `MCDLL_NET.dll`, with error reporting that is
honest about the known ~56% init failure rate.

Starts only after Priority 1 is approved *and* built.

### 2.1 What analysis already established

- `MCDLL_NET.dll` is an off-the-shelf Ethernet motion SDK, 232 exports, of which the vendor app
  uses **13** (§15). Those 13 are the binding surface, listed in `design.md` §5.2. No shim needed.
- The transport is **raw Ethernet via WinPcap**: `MCDLL_NET.dll` → `wpcap.dll` → `Packet.dll`
  (§15.1). `_Net` means Ethernet, not .NET. The NPF driver must be installed and the process needs
  rights to open an adapter.
- The failure signature in the logs is `MCF_Open_Net` → "Failed to Open the Axis", **250 of 442
  attempts** (§13).
- Four disassembly-confirmed fragilities give a candidate mechanism (§15.4): no BPF filter is ever
  installed, so the promiscuous receive returns whatever frame arrives first; the receive is a
  single unretried `pcap_next_ex`; `to_ms=-1` is undocumented in WinPcap; and adapter identity is
  **positional** in an unstable enumeration order, capped at 16 adapters.
- The retry-3-times logic is in the **EXE**, not the DLL (§21) — so we get no retry for free and
  must implement our own.
- `AxisCache.dat` is all zeros except two `0x09090909` filler values (§22), consistent with the
  axis never having been successfully taught on this machine.

### 2.2 What "clean, real error reporting" means here

The 56% number is why this priority exists, so the error model is the feature, not decoration.
Failures must be **separated by cause**, because they have different fixes:

- **Environmental** — NPF driver absent/stopped, no adapter permission, no adapter found. Not a
  device fault. Must say so, and say what to do.
- **Discovery** — the §15.3 loop walked every adapter and none answered. Distinguish "wrong
  adapter picked" from "controller not responding".
- **Device** — adapter opened, controller answered, motion refused.
- **Unknown** — reported as unknown. Never rounded to a device fault.

Every failure carries which of the 13 vendor calls failed, its raw return code, and the adapter
context — not a bare `False`, which is what the current scripts would give you.

### 2.3 A testable diagnostic, offered but not assumed

§15.4 ends in an explicit, **unconfirmed** prediction: if the mechanism is promiscuous-receive
contention, the failure rate should track background broadcast/multicast volume on the axis NIC
and should drop sharply on a dedicated point-to-point link. Nothing has been observed running —
this is inference from disassembly only.

I can build a small diagnostic that measures this rather than assuming it. Whether that is worth
your time is your call; I will ask at the Priority 2 gate. Marking it clearly: **hypothesis, not
finding.**

### 2.4 Open questions for Priority 2 (not needed yet — asked at the gate)

- Has the axis ever been homed/taught successfully on this machine? §22 suggests not.
- Is the axis on a dedicated NIC or a shared network?
- Which axes are physically present and what are their travel limits? No axis card or adapter
  field exists in `Config.ini` (§21), so this has to come from you.

---

## 3. PRIORITY 3 — Minimum-size droplet splitting

**Goal.** A dedicated script for splitting a droplet as small as possible, beyond what the current
scripts achieve.

Starts only after Priorities 1 and 2 are approved and built.

### 3.1 Why this depends on Priority 1

"As small as possible" is a measurement claim. Without calibrated optical measurement you cannot
tell a genuinely smaller droplet from a differently-lit one. The px → electrode calibration and
the detection path built in Priority 1 are what make Priority 3 falsifiable. This is the main
reason the ordering you chose works.

> ⏰ **REMINDER OWED AT THE START OF PRIORITY 3.** The **electrode pitch (µm)** is still unknown.
> The researcher deferred it here deliberately (§1.4) and asked to be reminded rather than
> blocked. Without it, droplet size has no physical units — only "electrodes across", which is
> enough for relative comparison but not for reporting an actual volume or dimension. Ask before
> designing P3.

### 3.2 Starting point in the existing code

`1pixsplit.py` (283 lines) already encodes a real two-stage strategy: stretch, pattern into two
pieces in one `ActivateElec` call, then translate with width pinching (10 → 3 columns), reaching a
5×3 piece. `dropsplitoff.py` (361 lines) holds the horizontal single-split case. The physical
constraint documented in that file's header — *piece height must match the drop being split* — is
prior art we keep, not rediscover. New work starts from what these established and pushes the
floor down; it does not restart from zero.

### 3.3 The capability ceiling to be honest about

Analysis §16 compared `DLLTest.dll`'s 7 Python-facing exports against
`MicrofluidicsInterFace.dll`'s 57. Python has **no frame control, no timing control, no polarity
control, no frequency control, and no chip-type selection.** For fine droplet manipulation those
are exactly the knobs that matter: actuation timing and waveform are standard levers for
controlling splitting and satellite formation in AM-DMF.

So the honest statement is: **Priority 3 will push the floor down using geometry and sequencing
alone, and there may be a hard limit that only the shim can move.** I will not know where that
limit is until we measure. If we hit it, that becomes concrete evidence for answering
`design.md` §9 question 1 — which is a better basis for that decision than the guess available
today.

Also relevant: `SendElectrify` has an overload taking `QVector<QRect>`, the closest match yet to
the `.Acx` 9-field drop record (§16, §9-of-analysis). The mapping is **unproven** and stays
unproven until someone verifies it.

---

## 4. PRIORITY 4 — Everything else

In whatever order makes sense once 1–3 are done. Recorded here so nothing is lost, not scheduled.

| Item | Gate | Source |
|---|---|---|
| Temperature control | ⛔ shim required — `TempControlInterFace.dll` is Qt/C++, not ctypes-callable | §10 |
| ⚠ `SendSetPID` argument order | **Unresolved.** 4 ints, 3 format slots. Must ship raising `NotImplementedError`. Wrong PID routing on a 4-zone heater is a physical-safety issue, not a bug. | §10, `design.md` §5.4 |
| Light system | ⛔ shim required | §28 |
| Magnet | ⛔ shim required | §28 |
| L3 choreography (merge, mix, graveyard, `.Acx` I/O) | Follows from P1/P3 | `design.md` §5 |
| L4 autonomous campaign (replaces `masterscript3.py`) | Last | `design.md` §5 |
| Path planning | Not adopted. `MultiAgentPathPlanning.dll` is managed .NET SIPP-MAPF (§26); reachable only via a .NET bridge. Note `SafetyDistance` — minimum droplet separation — as a concept worth borrowing even if the DLL is not. | §5, §26 |

---

## 5. Sequence and gates

```
  [ this document ]  ──▶  YOU APPROVE
          │
          ▼
  P1  gate: I ask before designing ──▶ design ──▶ gate: I ask before writing ──▶ build
          │                                                                        │
          ▼                                                             YOU APPROVE RESULT
  P2  gate: I ask before designing ──▶ ... (same shape)
          │
          ▼
  P3  gate: I ask before designing ──▶ ...
          │
          ▼
  P4  re-plan at that point; do not assume this ordering survives P1–P3
```

Nothing in P2 begins while P1 is open. Each gate is a separate ask.

---

## 6. Deltas this document creates in `spec/design.md`

Recorded, not yet applied. I will amend `design.md` on your say-so.

1. **§5.3 `l1_primitives/camera.py` (`MvCameraControl.dll` binding) — CANCELLED.** Replaced by a
   module modelled on `project/camera.py` and wired to your camera (§0.2 above).
2. **§6 `CameraPair` (HighSerial / LowSerial Hikrobot pair) — CANCELLED.** Same reason.
3. **§9 question 4 (camera) — ANSWERED.** Your camera, your `camera.py` structure, no vendor
   camera dependency.
4. **§9 question 1 (shim feasibility) — DEFERRED, and cheaper to defer than it looked.** P1, P2,
   and P3 are all designed to need no native build. The shim decision can now be made later, with
   evidence from P3 about whether the missing timing/polarity control is actually the binding
   constraint on droplet size.
5. **§9 question 2 (scope of first release) — EFFECTIVELY ANSWERED** by the priority order: the
   no-native-build subset, delivered as P1 → P2 → P3.
6. **§9 question 3 (legacy scripts) — STILL OPEN.** §0.1 commits to improving on them rather than
   wrapping them, but whether `project/`'s 13 scripts are left in place, ported, or deprecated is
   still your decision.
7. **§9 question 5 (arming/safety default) — STILL OPEN and now urgent**, because P1's health
   sweep energises broadly across the chip. This is the one §9 question that blocks P1.
8. **§10 "Proposed first step: L0 + L1 axis only" — SUPERSEDED.** Your priorities put the
   electrode/health script first, not the axis.

---

## 7. What has NOT been done

- No code written. **This includes the approved held-open camera change** — approval received
  2026-08-06, implementation deferred to the P1 build (§0.2).
- No script designed. This document states *what* each piece is for and what it must respect; it
  deliberately stops short of designing Priority 1.
- `spec/design.md` not modified — deltas in §6 are recorded, not applied.
- `inputs/research_plan.md` untouched, per the standing hold.

**Status 2026-08-06:** all §1.4 questions answered; nothing blocks the Priority 1 design.

**Next step:** waiting on your go-ahead to begin *designing* Priority 1. I will ask once more
before writing any code.
