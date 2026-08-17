# ACX Chip Project

Digital-microfluidics control software for an ACX 128×128 electrowetting chip:
splitting one droplet into equal pieces, and assessing electrode health across
the array.

## Overview

The chip is driven through a vendor DLL (`DLLTest.dll`) that exposes seven
functions and **no per-electrode readback of any kind**. Nothing in this
codebase can ask the chip what it actually did. That single fact shapes most of
the design:

- Split runs are **operator-gated**. A human at the microscope answering y/n is
  the only evidence that anything actuated.
- Every claim carries what it did *not* verify. Run reports print an explicit
  `NOT VERIFIED THIS RUN` section.
- Anything running off proven values is stamped as such in the report, so a
  transcript cannot later be mistaken for a verified result.

| Package | Purpose |
|---|---|
| [`microdrop/`](microdrop/README.md) | Symmetric droplet splitting — plan a 2ⁿ-piece tree and drive it with operator gates |
| [`chiphealth/`](chiphealth/README.md) | Chip-health sweep — actuation layer, clearance gate, camera detection, run recording |
| [`basics/`](basics/README.md) | Original vendor-style scripts, kept as the provenance record |
| `colormixing/` | Colour-mixing and dispensing scripts, including `csvvolcont.py` — the proven source for split timing |
| `tests/` | 520 tests, standard library only |
| [`docs/`](docs/README.md) | Guides and the specification record |

## Installation and setup

**Windows is required to touch hardware.** The vendor DLL is Windows x64 and
will not load under WSL or Linux. Everything except the hardware layer —
planning, clearance, the whole test suite — runs anywhere.

```bash
python -m venv .venv
.\.venv\Scripts\pip install opencv-python numpy    # chiphealth only; microdrop needs neither
```

Verify without hardware:

```bash
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m microdrop.protocol --plan-only
```

`--plan-only` opens no USB handle, asks nothing and energises nothing. If it
prints a plan and `CLEARANCE OK`, the install is good.

> **pytest is deliberately not installed.** The suite runs on the standard
> library via `unittest`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

### Split a droplet

Two fixed-configuration runners, each a reproducible record of one geometry:

```bash
.\.venv\Scripts\python.exe microdrop\run_8piece_split.py    # 8 pieces of 10x5
.\.venv\Scripts\python.exe microdrop\run_16piece_split.py   # 16 pieces of 5x5
```

> ⚠ **Both are always armed.** There is no dry run. Running either one
> energises the chip, and the rails come up before the first gate is asked.
> Neither geometry is currently confirmed on hardware.

For anything you want to vary, use the general-purpose driver:

```bash
# hardware-free: print the plan, the clearance verdict, the volume claim
python -m microdrop.protocol --plan-only --axes WHWH

# live, with a widened stage 2 and a longer dwell on stage 3
python -m microdrop.protocol --arm --axes WHWH \
    --stretch-stage 2:2.2 --settle-stage 3:0.5
```

Full flag table and the operator runbook:
[Running the split scripts](docs/guides/running-the-split-scripts.md).

### Assess chip health

```bash
.\.venv\Scripts\python.exe -m chiphealth.run_health \
    --chip-id trial01 --arm --camera 0 --frame-size 1920x1080 --step-delay 0.5
```

Writes video, stills and a scored summary to `runs/<timestamp>/`. Re-score an
existing run offline with `python rescore.py`.

## Configuration

### Vendor DLL

Set in `chiphealth/config.py`:

| Setting | Default |
|---|---|
| `DEFAULT_DLL_DIR` | `C:\Users\<user>\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows` |
| `DEFAULT_DLL_NAME` | `DLLTest.dll` |

Override per run with `--dll-dir`. The loader refuses a DLL missing any of the
seven exports it calls rather than failing later at an arbitrary call site.

### Chip constants

`chiphealth/config.py::ChipConfig`. Every value was a magic number repeated
across the legacy scripts before it was centralised here.

| Setting | Value | Source |
|---|---|---|
| `rows` × `cols` | 128 × 128 | Array size |
| `pitch_um` | 246.48 | Measured 2026-08-10 |
| `volts` | 45, 45, 45, then 0×6 | `csvvolcont.py:162-165` |
| `volt_tolerance` | ±2 V | `csvvolcont.py:182-183` |
| `volt_settle_s` | 0.3 | `csvvolcont.py:168` |
| `gap_um` | `None` | **Unmeasured** — blocks any absolute volume figure |

Split parameters live separately in `microdrop/params.py`, each marked PROVEN
or DERIVED. See [Provenance](docs/guides/provenance.md).

### Machine-local files

`calibration.json` (camera corners) and `runs/` (experiment artifacts) are
gitignored. The run artifacts are the experimental record — back them up
elsewhere, just not here.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to run the tests, the
`check_geometry()` safety pattern, and commit conventions.

Two rules worth stating up front:

1. **A green test suite is not hardware verification.** Never label a geometry
   "verified" on the strength of passing tests. Only a live run that an
   operator confirmed does that.
2. **Never remove a documented failure mode from a comment** because the code
   looks obvious without it. Several comments in this repo exist because the
   obvious-looking version was wrong on hardware.

## Documentation

[docs/README.md](docs/README.md) is the index — task-focused guides, plus the
specification and design record in `docs/spec/`.
