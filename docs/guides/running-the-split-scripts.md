# Running the split scripts

Operator runbook for `microdrop/run_8piece_split.py` and
`microdrop/run_16piece_split.py`, and for the general-purpose
`python -m microdrop.protocol` behind them.

**Neither split script is currently confirmed on hardware.** See
[Separation and dwell tuning](separation-and-dwell-tuning.md) for what changed
and why.

## Before you start

| | |
|---|---|
| Interpreter | `.\.venv\Scripts\python.exe` — **Windows only**. The vendor DLL is Windows x64 and will not load under WSL or Linux. |
| Hardware | Chip connected. Both runners refuse to run against the fake backend. |
| Hardware-free check | `python -m microdrop.protocol --plan-only` — opens no USB handle, asks nothing, energises nothing. |

## The two runners are always armed

Neither runner has a dry run. **Running either one energises the chip.**

Opening the chip issues `SetPower(True)` and `SetVolt`, so the rails come up
*before the first gate is asked*. The phase 0 voltage gate happens with 45V
already commanded. Answering `n` there stops the run before any electrode is
activated — but not before the supply is live.

`--arm` is accepted by both and does nothing. It is kept only so that typing
the flag they used to need does not abort a run with `unrecognized arguments`
while a chip is loaded.

For a rehearsal that touches no hardware, use `--plan-only` on the protocol
module instead. The runners cannot do it.

## No camera, and what that costs

Nothing under `microdrop/` imports cv2, numpy or any calibration file.
Positions are electrode indices commanded straight through `ActivateElec`, so
no homography is involved and none is needed.

The consequence: **this API has no per-electrode readback.** There is no way to
ask the chip what it did. The operator's y/n answers at each gate are the only
evidence that anything actuated at all. A run of confident yeses proves nothing
except that someone pressed `y`.

This is why the gates are real y/n prompts that re-ask on anything else, rather
than press-Enter-to-continue. A prompt you can dismiss by leaning on the
keyboard is not a check.

## The gates

`run_8piece_split.py` — 5 gates:

| # | Gate | Expect |
|---|---|---|
| 1 | Voltage confirmed good | rails read back 45/45/45 |
| 2 | Droplet loaded and holding | 20x20 at row 5, col 55, filling the rectangle |
| 3 | Arrival, nothing left behind | 20x20 at row 55, col 55 |
| 4 | Stage 0 done | 2 pieces |
| 5 | Stage 1 done | 4 pieces |
| 6 | Stage 2 done | **8 pieces** — the widened stage; look hardest here |

`run_16piece_split.py` — 7 gates, the same plus:

| # | Gate | Expect |
|---|---|---|
| 6 | Stage 2 done | **8 pieces** — widened, *not* slowed: the dwell control |
| 7 | Stage 3 done | **16 pieces** — widened *and* slowed |

Answering `n` at any gate stops the run and prints the report so far.

## Exit codes

Identical across both runners:

| Code | Meaning |
|---|---|
| 0 | Completed |
| 1 | Operator stopped it — `n` at a gate |
| 2 | Clearance violation — geometry does not fit the array |
| 3 | Rails do not match what was commanded |
| 4 | Fake backend — the vendor DLL did not load |

A `SystemExit` with a `REFUSING TO RUN` message means `check_geometry()`
rejected the plan before anything was energised. See
[the check_geometry pattern](../../CONTRIBUTING.md#the-check_geometry-pattern).

## Why the runners have no flags

Every parameter in both runners is hardcoded — position, axis order, piece
count, stretch ratios, dwell. There is no flag to change any of it.

That is the point. Each file exists to be a reproducible record of one specific
configuration. The moment it can be pointed at a different geometry it stops
being a record of anything. `check_geometry()` enforces this: it re-derives the
plan at startup and refuses to run if the planner no longer produces the exact
frame counts, leaf sizes, neck gaps and dwell the file claims.

Use `python -m microdrop.protocol` when you want to vary something.

## CLI reference

| Flag | 8-piece | 16-piece | `microdrop.protocol` |
|---|---|---|---|
| `--arm` | accepted, ignored | accepted, ignored | required to energise |
| `--plan-only` | — | — | ✅ no USB handle, no prompts |
| `--axes WHWH` | hardcoded `WHW` | hardcoded `WHWH` | ✅ |
| `--stretch-stage N:R` | hardcoded `2:2.2` | hardcoded `2:2.2`, `3:2.2` | ✅ repeatable |
| `--settle-stage N:S` | — | hardcoded `3:0.5` | ✅ repeatable |
| `--at ROW,COL` | hardcoded `55,55` | hardcoded `55,55` | ✅ split position |
| `--load-at ROW,COL` | hardcoded `5,55` | hardcoded `5,55` | ✅ load position |
| `--no-walk` | — | — | ✅ load at the split position |
| `--step-delay S` | hardcoded 0.5s | hardcoded 0.5s | ✅ global dwell |
| `--backend auto\|real\|fake` | — | — | ✅ |
| `--yes` | — | — | ✅ auto-answer gates, **plumbing only** |
| `--allow-clearance-violations` | — | — | ✅ |
| `--dll-dir PATH` | — | — | ✅ |
| `--poke ROW,COL` / `--size HxW` | — | — | ✅ hold one rectangle |
| `--dump` | — | — | ✅ print the exact ActivateElec args |
| `--log-frames` | — | — | ✅ log every call |
| `--allow-fake-arm` | — | — | ✅ arm against the fake rig |

`--yes` removes the only verification the pipeline has and is recorded in the
run report so such a run cannot later be mistaken for a verified one.

## Reading the report

Every run prints a report at the end containing the plan description, the
volume-equality claim and its assumptions, an explicit *NOT VERIFIED THIS RUN*
section, any run notes, and every operator question with its answer.

The report is the only artifact that outlives the terminal. Run notes exist so
that a transcript of confident yeses cannot later read as though a geometry had
been proven — any non-proven dwell, stretch override, auto-answer or clearance
override is stamped there.

## See also

- [The symmetric split algorithm](symmetric-split.md)
- [Separation and dwell tuning](separation-and-dwell-tuning.md)
- [The clearance gate](clearance-gate.md)
