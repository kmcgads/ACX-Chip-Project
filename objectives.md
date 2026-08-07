it# Acxchip — objectives and prioritized roadmap

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
| Interactive gating — `input()` between every step (`chipsetup.py:30-58`) makes scripts non-autonomous | Nothing blocks on a human by default. Step-through is an explicit opt-in flag. |
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

**One structural change I will need to propose, and will ask about before building.**
`camera.py` opens and releases the device on *every* call — `take_picture` (`camera.py:45-57`)
does open → read → release. That is correct for one-shot stills and wrong for live view: a
visualization loop cannot pay a device-open per frame. So the new camera object needs to hold the
capture open across frames, while keeping your API shape. This is a change *to the lifetime
model*, not to the connection method or the camera. It is the first thing I will confirm with
you under Priority 1.

**One recommendation inside the constraint, not a change to it.** `camera.py:23` sets
`CAP_PROP_AUTOFOCUS, 1` — autofocus on. For quantitative colour and size work, autofocus
refocusing mid-run is a real variance source (already flagged in `design.md` §9 q4). Same camera,
same connection — I would just pin focus for measurement runs. Your call, not a blocker.

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

### 1.4 Open questions — I need these answered before I design this script

1. **How is liquid present during the check?** This decides the whole method. Options as I
   understand your rig: walk one droplet over a route and test only what it touches (slow, honest,
   partial coverage); flood/fill a region; or accept testing only the working area you actually
   use. I do not know your consumable and filler-oil situation well enough to pick — this is a
   real question, not a formality.
2. **What counts as "functioning"?** Droplet moves when commanded / droplet deforms detectably /
   optical change above a threshold. Different answers give different scripts.
3. **Coverage granularity.** Per electrode (128×128 = 16,384 cells), or per block (e.g. 8×8
   regions)? Optical resolution and run time both push toward blocks.
4. **Chip geometry confirmation.** Existing scripts pass `ActivateElec(128, 128, ...)`
   (`chipsetup.py:70`). Confirm 128×128 is your actual chip, and tell me the electrode pitch —
   I need it for the px → electrode calibration that makes any of this quantitative.
5. **Arming/safety.** `design.md` §9 q5 proposed refusing hardware-mutating calls unless
   explicitly armed. A health sweep energises broadly across the chip, which makes this the most
   urgent open question of the five. Still needs your yes/no.

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

- No code written.
- No script designed. This document states *what* each piece is for and what it must respect; it
  deliberately stops short of designing Priority 1.
- `spec/design.md` not modified — deltas in §6 are recorded, not applied.
- `inputs/research_plan.md` untouched, per the standing hold.

**Next step:** answer §1.4 (especially q1 and q5), then tell me to begin designing Priority 1.
I will ask again before writing anything.
