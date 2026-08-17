# The clearance gate

Nothing in this repo may energise an electrode that is not on the array. This
guide covers what enforces that, where, and the bug that motivated measuring it
in one place. Extracted from `chiphealth/clearance.py`,
`microdrop/splitplan.py` and `chiphealth/actuation.py`.

## Why it exists

Commanding an off-grid rectangle is not a caught error at the hardware layer —
`ActivateElec` takes the call. The failure is silent and physical: part of the
pattern lands nowhere and the droplet does something unplanned with liquid
already on the chip.

Reporting a violation is not the same as refusing one, and until 2026-08-13
nothing refused. Now `allow_violations=True` is the only way past, and it
defaults `False` everywhere.

## Where it runs

Three layers, deliberately overlapping:

| Layer | When | What it catches |
|---|---|---|
| `ChipController.activate` | Every frame, armed or dry | Anything, from any caller — the single choke point |
| `require_clearance(plan)` | Before a plan is handed to an executor | A whole plan that cannot fit, before liquid is loaded |
| `plan_tree` validation | While the plan is built | Which stage and which piece, named |

`ChipController.activate` is the guarantee. Every drop on this chip — the
chip-health resting frame, the registration hold, every sweep step, the fine-
pass probe, every split-tree frame — arrives at that one method, so gating it
makes the claim *"nowhere"* rather than a promise about the call sites someone
remembered.

**Dry runs are gated too.** A dry run exists to prove the plumbing, and a plan
that cannot execute armed has not proved anything.

The plan-level gate still matters despite the per-frame one: without it you
find out several frames in, with liquid already on the chip.

## What it measures

`plan_tree` checks the **stretched footprint mid-split**, not just the settled
pieces. That is the widest the chip ever gets and the moment a collision would
actually happen. It also checks:

- **off-grid** — any box outside rows 1–128 / cols 1–128, naming each short side and by how much
- **overlap** — two live pieces sharing an electrode
- **separation** — two pieces closer than `min_separation` (default 2)

`min_separation` borrows the `SafetyDistance` idea from the vendor path planner,
which was not itself adopted.

## Positions are 1-based

Electrode (1,1) is top-left, matching `chiphealth.geometry` and
`ChipController._validate`.

**This was not always true, and the bug is worth knowing about.** `plan_tree`'s
off-grid test used to read `r0 < 0 or r1 >= rows` — 0-based, and one electrode
out at every edge in opposite directions from the check that actually guards
`ActivateElec`. The visible symptom was `cleared_root()` planning with zero
violations and then having **63 of its 87 frames refused by the controller**.

Both now measure through `chiphealth.clearance`, so the two cannot drift apart
again, and `cleared_root()` moved from (8,12) to (9,13) as a result.

The lesson generalises: two places that both "check bounds" will disagree
eventually unless they call the same function.

## Margin required by a centred stretch

Centring the stretch is not free. An origin-anchored stretch grows only in `+`
and needs no room behind the load position; a centred one grows both ways, and
cumulatively down the tree.

For the default 8-piece plan that is **8 clear electrodes above and below, and
12 either side** of the loaded 20x20. The sweep's load position (row 5, col 10)
does not have that, so `plan_tree(default_root())` reports off-grid violations
**by design** rather than clipping silently.

`required_margin` computes it; `cleared_root` returns the least nudge that fits.

This is also why the load position and the split position are decoupled — see
[the symmetric split guide](symmetric-split.md#the-load-position-is-not-the-split-position).
The requirement is on where the droplet splits, so the operator can keep loading
somewhere reachable and the droplet is walked in.

## Overriding it

`allow_violations=True`, and nothing else. It is accepted at
`ChipController.activate` (per frame or per session), at `require_clearance`,
and as `--allow-clearance-violations` on the protocol CLI.

Every override is logged at ERROR level with the full short-side breakdown, and
recorded in the run report, so a run that went off-grid deliberately cannot
later be read as one that fit.

## See also

- [The symmetric split algorithm](symmetric-split.md)
- [Running the split scripts](running-the-split-scripts.md) — exit code 2 is a clearance refusal
- [`check_geometry()` pattern](../../CONTRIBUTING.md#the-check_geometry-pattern) — the related startup guard in the runner scripts
