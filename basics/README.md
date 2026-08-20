# `basics/`

The original vendor-style scripts. These predate `chiphealth/` and
`microdrop/` and are kept as the **provenance record** — the evidence for what
actually works on this hardware.

> These are reference material, not the maintained path. For new work use
> [`microdrop/`](../microdrop/README.md) for splitting and
> [`chiphealth/`](../chiphealth/README.md) for health runs.

## Files

| File | What it does |
|---|---|
| `chipsetup.py` | Minimal connect / power / set-voltage sequence |
| `cleanup.py` | Sweeps the array to clear liquid off the chip |
| `microtest1.py` | Two-stage split down to a 5×3 piece (renamed from `1pixsplit.py` 2026-08-20; git preserves the history) |
| `dropsplitoff.py` | Horizontal single-split case |
| `dropandmixtests.py` | Drop and mix experiments |
| `mdmixing.py`, `mdmixwithmerge.py` | Mixing routines, with and without a merge step |

## Why they are kept

The vendor DLL's real ABI diverges from its documentation
(`workspace/analysis.md` §2), so **the working scripts are the only reliable
specification.** Several decisions in the maintained packages are traceable
directly to them:

- The `Drop` field order `(height, width, row, col)` — identical across
  `cleanup.py`, `microtest1.py` and `chipsetup.py`, and *not* what the vendor
  PDF says.
- No `argtypes` on any DLL call. These scripts declare none and demonstrably
  work; pinning types they never pinned changes how ctypes marshals every call.
- `if res:` on `OpenUSB` is the only return-code convention with any evidence
  behind it — it is the only call any of these scripts tests.
- The 0.5 s inter-activation delay.

## What was deliberately *not* inherited

`microtest1.py` (formerly `1pixsplit.py`) is **not** the basis for the splitting work
(researcher, 2026-08-13). Its step 3 patterns the reservoir and the piece in
one `ActivateElec` call and asks the liquid to snap apart in a single frame —
its own comments say *"No neck loop"*, twice.

`colormixing/csvvolcont.py` erodes the neck over `gap+1` frames at 0.5 s each.
**The break is walked, not snapped**, and that is the mechanic `microdrop/`
inherits. See [Provenance](../docs/guides/provenance.md).

## Known problems

These are unmaintained and carry the defects the newer packages were written to
fix: hardcoded absolute paths, an `input()` prompt between every actuation
step, magic numbers repeated across files, and discarded return codes. Do not
copy patterns out of them without checking
[`docs/spec/objectives.md`](../docs/spec/objectives.md) §0.1 first.
