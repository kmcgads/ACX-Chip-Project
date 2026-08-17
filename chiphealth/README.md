# `chiphealth/`

Chip-health assessment: sweep a droplet across the array, watch it with a
camera, and score which blocks moved liquid. Also home to the hardware layer
every other package uses.

## Files

| File | Purpose |
|---|---|
| `actuation.py` | The `DLLTest.dll` binding, `Drop`, `ChipController`, the arming gate, and a fake rig for off-hardware work |
| `clearance.py` | The off-grid gate — the single place bounds are measured |
| `config.py` | `ChipConfig` and `SweepConfig`. Every value that used to be a magic number in the legacy scripts |
| `geometry.py` | Electrode ↔ pixel mapping |
| `calibration.py` | Camera corner picking and the homography |
| `detector.py` | Droplet detection and blob scoring |
| `sweep.py` | The sweep itself, including `grow_release` — the transport primitive |
| `recorder.py` | Run artifacts: video, stills, JSONL timelines |
| `run_health.py` | Orchestrator: the phased, operator-gated health run |
| `simulate.py` | Synthetic frames for testing without a camera |

## Usage

```bash
.\.venv\Scripts\python.exe -m chiphealth.run_health \
    --chip-id trial01 --arm --camera 0 --frame-size 1920x1080 \
    --step-delay 0.5
```

Writes to `runs/<timestamp>/`: `run.json` (full config and code version),
`summary.md`, `coverage.json`, plus `events.jsonl`, `observations.jsonl`,
`timeline.jsonl` and media. Re-score an existing run offline with
`python rescore.py` — thresholds can be retuned without re-running the chip.

Expect roughly 1798 coarse frames (~15 min of dwell alone) plus camera and
analysis time, then a ~2 min fine pass.

## What this can and cannot tell you

**It cannot read electrode state.** The vendor API has no per-electrode
readback. `InquireVolt` returns **9 global rails** for the whole chip — it
confirms the supply and the USB link, and says nothing about any individual
electrode.

Every verdict is therefore **optical inference**: liquid appeared to move, or
it did not. `unknown` in a coverage table means never tested, not healthy.

## The hardware layer

`ChipController` is what every package uses to touch the chip, and it enforces
two things for all callers:

- **Arming.** Without `armed=True`, `SetPower` / `SetVolt` / `ActivateElec` are
  never issued. Dry runs record the intended frame and log it.
- **Clearance.** Every frame passes `require_clearance`, armed or not. This is
  the single choke point every drop on this chip goes through. See
  [The clearance gate](../docs/guides/clearance-gate.md).

`make_backend("auto", ...)` returns `RealBackend` where the DLL loads and
`FakeBackend` otherwise, so the whole stack runs on any machine. **A fake rig
satisfies the rail check identically to a real one** — callers that must not
run fake check for it explicitly.

### Return codes

Only `OpenUSB` has an evidenced convention (`if res:`, as every legacy script
tests it). `SetPower`, `SetVolt`, `InquireVolt` and `ActivateElec` are assigned
and never checked anywhere in the legacy code, and the instrument has shown
`SetVolt` returning 0 while demonstrably working. Do not generalise OpenUSB's
convention to the others — that assumption cost a debugging session.

## Configuration

`config.py`. The DLL path (`DEFAULT_DLL_DIR`, overridable with `--dll-dir`),
array size 128×128, pitch 246.48 µm, rails 45/45/45, ±2 V tolerance, 0.3 s
settle, 0.5 s step delay.

`gap_um` is `None` — **unmeasured**, which is why `droplet_volume_nl()` returns
`None` and no absolute volume is reportable anywhere in this repo.

## Tests

`test_actuation.py`, `test_clearance.py`, `test_geometry.py`,
`test_calibration.py`, `test_detector.py`, `test_sweep.py`, `test_recorder.py`,
`test_run_health.py`, `test_end_to_end.py`. `test_actuation.py` pins the `Drop`
field order by disassembly-verified layout — the vendor PDF documents a
different order, and getting it wrong corrupts the stack at runtime rather than
raising.
