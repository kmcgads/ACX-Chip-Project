# Acxchip — requirements

**Written 2026-08-12.** This file was an unfilled template until now; the requirements below were
already established and enforced in code and tests, just never collected in one place. Every item
cites where it comes from. Nothing here is new — if an item is not traceable to `objectives.md`,
`p1_chip_health_design.md`, `workspace/analysis.md`, or a test, it does not belong here.

## Problem

An ACX/Sigenex AM-DMF chip moves droplets by energising electrodes on a 128×128 grid. The chip
**reports nothing back**: `ActivateElec` sets the entire frame in one shot, there is no
per-electrode readback of any kind, and `InquireVolt` returns 9 global rails (analysis §2,
`objectives.md` §1.1). So there is no way to ask which electrodes still work.

Electrodes degrade. Today the only way to know is a human watching droplets and forming an
impression. That does not survive the session, cannot be compared across months, and cannot say
*where* on the chip the problem is.

**For whom:** the researcher operating this one instrument, and whoever inherits the chip's
history. The output is meant to be read months later, by someone reconstructing why a chip's
behaviour changed.

## Functional requirements

- [x] **Infer per-region chip health optically**, since it cannot be queried. Command a known
      pattern, observe what the liquid does through the camera, attribute the difference
      (`objectives.md` §1.1).
- [x] **Report a verdict per 4×4-electrode block** — a 32×32 = 1024-block map (§1.4).
- [x] **`unknown` is a first-class outcome, distinct from `fail`.** A region with no liquid over it
      is not a failing region; conflating them would be a lie about coverage (§1.3).
- [x] **Detect the three signatures the researcher actually observes**: drag, residue, and
      no-movement — plus `unreachable` for the fine pass (§1.7, `detector.py`).
- [x] **Cover every electrode, and prove it.** The traversal self-checks that every electrode falls
      under a leading edge at least once, and says so in the run notes if it does not
      (`sweep.untested_electrodes`, verified in the run).
- [x] **Never energise by accident.** Dry-run is the default; `--arm` or `ACXCHIP_ARM=1` arms a
      session, and the disarm must be easy and documented so it does not obstruct real testing
      (`design.md` §9 q5).
- [x] **Verify the voltage rails before sweeping**, and refuse to continue without operator
      confirmation on a mismatch. A chip with one dead rail would otherwise sweep all 899 moves,
      report the whole chip as failing, and put that in the longitudinal record (phase 0b).
- [x] **Prompt only where a physical human action is genuinely required** — loading oil or sample,
      adjusting focus, topping up. Never after a routine DLL call. Every prompt records what was
      asked, what the operator answered, and when (§0.1).
- [x] **Write a machine-readable artifact per run**, with stable field names, timestamp, chip
      identity and all run parameters, good enough to support retrospective analysis without
      re-running history (§1.4 q10, §1.6).
- [x] **Be re-scorable offline.** Thresholds are estimates; a run must be replayable at different
      thresholds without touching hardware (`rescore.py`).
- [x] **Transport must not tear the droplet.** A one-electrode move is a grow/release pair, never a
      simultaneous grab and release (`sweep.grow_release`).
- [x] **Never truncate silently.** Anything dropped by a cap — fine-pass targets, uncovered rows —
      is named in the run notes.

## Non-functional requirements

- [x] **No shim, no native build.** Only `DLLTest.dll`'s 7 flat-C exports (§1.2).
- [x] **The researcher's own camera only.** `cv2.VideoCapture` + `CAP_DSHOW` + MJPG. No
      `MvCameraControl.dll`, no `camHalcon.dll`, no Hikrobot/MVS component, and none of the five
      vendor YOLOv5 ONNX detectors (§0.2).
- [x] **Autofocus off**, focus set by hand, focus state recorded per run — runs at different focus
      are not directly comparable (§0.2).
- [x] **Detection logic must be testable with neither rig nor OpenCV present.** `detector.py`,
      `sweep.py`, `geometry.py` and `calibration.py` are standard library only; blob extraction is
      isolated in the camera layer.
- [x] **Thresholds in electrode units, not pixels**, so that moving or re-scaling the camera
      re-derives detection sensitivity automatically.
- [x] **One definition of everything.** No re-declared `Drop`, no re-loaded DLL, no hardcoded
      absolute paths — the six defects catalogued in §0.1.
- [x] **Connection lifetime owned by context managers**, released on every exit path including
      exceptions (§0.1).
- [x] **Structured logging to file.** No bare `print()` as the only output (§0.1).
- [x] **Runs are resumable and partial.** Full-chip coverage in one sitting is not realistic with
      manual droplet loading; coverage accumulates across runs (§1.4).
- [ ] **Tests must not depend on the ambient environment.** Currently violated by two tests that
      assert OpenCV is absent — see `p1_build_status.md`.

## Out of scope

- **Per-electrode electrical readback / percentage scoring.** No known path with this hardware or
  vendor API (§1.6). Not dismissed; there is simply no route today.
- **Droplet volumes in nanolitres.** Footprint follows from the electrode pitch; volume needs the
  plate gap, which is unmeasured. `droplet_volume_nl()` returns `None` and a test enforces it.
  **Do not invent a gap value** (§2.4 q3).
- **The ML model itself.** This design produces its training dataset, nothing more.
- **Automatic degradation learning.** The artifact schema must not preclude it; nothing implements
  it (§1.6).
- **Axis, temperature, light, magnet.** Axis deferred 2026-08-12 (`objectives.md` Appendix A); the
  other three are shim-gated (§3).
- **Vendor path planning.** `MultiAgentPathPlanning.dll` is managed .NET, reachable only via a
  bridge. Not adopted (§26).
- **Any claim that a verdict is a device readback.** Every verdict here is optical inference. The
  CLI epilog says so on every invocation, and it must keep saying so.
