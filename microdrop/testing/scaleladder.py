"""
scaleladder.py — the same split at five sizes, to find where it stops working.

THE EXPERIMENT
==============
Run the identical two-stage H,W tree at 20x20, 16x16, 12x12, 8x8 and 6x6, all
at the PROVEN 1.75 stretch, and record which stage parts at each size. One
size per invocation, because each rung needs a hand-loaded droplet.

    python microdrop/testing/scaleladder.py --size 20
    python microdrop/testing/scaleladder.py --size 16     ... and so on

WHY THIS IS WORTH RIG TIME
==========================
A 20x20 tree separated on hardware on 2026-08-13. A 6x6 tree has now failed
three times, most recently at STAGE 0 -- the gentlest split in the repository.
Everything tried in between has been geometry (wider stretch, longer dwell) and
none of it helped. This ladder tests the remaining explanation: that the split
is size-dependent, and that somewhere between 20 and 6 it stops working.

WHAT MAKES IT A CLEAN EXPERIMENT: at a fixed ratio the geometry barely moves
across the ladder. Areal coverage (liquid over activated area) stays between
54% and 60%, and stretched aspect between 1.67 and 1.83 at stage 0:

    size    leaf   liquid  stretch  gap  coverage  aspect s0  aspect s1  frames
    20x20  10x10      400       36   16       56%       1.80       3.60      51
    16x16    8x8      256       28   12       57%       1.75       3.50      39
    12x12    6x6      144       22   10       55%       1.83       3.67      33
      8x8    4x4       64       14    6       57%       1.75       3.50      21
      6x6    3x3       36       10    4       60%       1.67       3.33      15

So geometry is held roughly constant and only SIZE varies. Note the direction of
the anomaly: 6x6 has the BEST coverage and the LOWEST aspect of the five, and it
is the one that fails. If the ladder shows a clean break at some size, that is
evidence for size-dependent physics -- surface tension against available EWOD
force -- and not for anything in the plan.

NO TRANSPORT, DELIBERATELY. The droplet is loaded directly at the split
position and `transport=False`. The sibling runners walk 50 electrodes first;
here that would be a confound, because "the 6x6 could not walk" and "the 6x6
could not split" are different findings. This tests the split alone.

WHAT TO RECORD, AND WHY IT MATTERS MORE THAN USUAL
==================================================
The finding is a COMPARISON across five runs, so it does not exist in any one
of them. Each run therefore appends a line to `runs/scale_ladder.jsonl` with
the size, which gates were answered, and where it stopped. Read the ladder with
`--summary`, which needs no hardware.

Record which stage failed, not just that it failed. Stage 0 halves the height
of a square parent; stage 1 halves the width of an already-halved one and has
double the aspect. "Stage 0 failed" and "stage 0 parted, stage 1 failed" point
in different directions.

⚠ NOT CONFIRMED ON HARDWARE. Every rung below 20x20 is untested, and 20x20
itself separated once and then failed to reproduce on 2026-08-17.

⚠ ALWAYS ARMED. There is no dry run. Opening the chip issues SetPower and
SetVolt, so THE RAILS COME UP BEFORE THE FIRST GATE IS ASKED. `--arm` is
accepted and does nothing. Hardware-free check:
`python -m microdrop.protocol --plan-only --axes HW`

Operator gates, exit codes, and what a camera-free run cannot verify:
docs/guides/running-the-split-scripts.md
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from chiphealth.actuation import ChipController, make_backend
from chiphealth.clearance import ClearanceViolation
from chiphealth.config import DEFAULT_DLL_DIR, DEFAULT_DLL_NAME, ChipConfig
from microdrop import params as P
from microdrop import splitplan as SP
from microdrop.protocol import OperatorAbort, SplitSession

# ── The ladder ────────────────────────────────────────────────────────────────

SIZES = (20, 16, 12, 8, 6)
SPLIT_ROW, SPLIT_COL = 55, 55   # identical for every rung, so position is not
                                # a variable between them
AXES = ("H", "W")               # 2 stages -> 4 equal pieces, at every size

# Derived from the planner at the proven 1.75 and pinned here, so a rung that
# no longer matches refuses rather than quietly running something else.
#   size: (leaf, tree frames, neck gap)
EXPECT = {
    20: ((10, 10), 51, 16),
    16: ((8, 8), 39, 12),
    12: ((6, 6), 33, 10),
    8:  ((4, 4), 21, 6),
    6:  ((3, 3), 15, 4),
}

LADDER_LOG = os.path.join(PROJECT_ROOT, "runs", "scale_ladder.jsonl")

STEP_DELAY_S = P.PROVEN_SETTLE_S    # 0.5s, csvvolcont.py:137
BAR = "=" * 68
RULE = "-" * 60


def banner(title: str) -> None:
    print(f"\n{BAR}\n  {title}\n{BAR}")


def say(tag: str, message: str) -> None:
    print(f"  [{tag}] {message}")


def confirm(question: str, detail: str = "") -> bool:
    """A real y/n gate. Anything but y/n re-asks; `n` stops the run."""
    if detail:
        print(f"\n{detail}")
    print(f"\n  {RULE}")
    try:
        while True:
            answer = input(f"  >>> {question} [y/n] ").strip().lower()
            if answer in ("y", "yes"):
                print(f"  {RULE}")
                return True
            if answer in ("n", "no"):
                print(f"  {RULE}")
                return False
            print("      please answer y or n")
    except (EOFError, KeyboardInterrupt):
        print("\n      no answer -- treating as 'n'.")
        return False


def check_geometry(session: SplitSession, size: int) -> None:
    """Refuse to run unless this rung is the one the ladder expects.

    Every rung must be the SAME mechanic at a different size; a rung that
    silently ran a different geometry would make the comparison meaningless,
    which is the only thing this script is for.
    See CONTRIBUTING.md#the-check_geometry-pattern.
    """
    plan = session.plan
    leaf, frames, gap = EXPECT[size]
    leaves = {(n.height, n.width) for n in plan.leaves}
    problems = []

    if session.approach is not None:
        problems.append("an approach was planned; this ladder loads at the "
                        "split position so transport is not a variable")
    if len(plan.leaves) != 4:
        problems.append(f"{len(plan.leaves)} pieces, expected 4")
    if leaves != {leaf}:
        problems.append(f"leaf sizes {sorted(leaves)}, expected {leaf}")
    if plan.n_frames != frames:
        problems.append(f"{plan.n_frames} frames, expected {frames}")
    if {s.neck_gap for s in plan.steps} != {gap}:
        problems.append(f"neck gaps {sorted({s.neck_gap for s in plan.steps})}, "
                        f"expected {gap}")
    if {s.stage: s.axis for s in plan.steps} != {0: "H", 1: "W"}:
        problems.append("stage axes are not H then W")
    # The comparison rests on every rung using the PROVEN ratio. An override on
    # any rung would make that rung incomparable with the rest.
    n = len(AXES)
    ratios = {s: session.sp.for_stage(s, n).stretch_ratio for s in range(n)}
    if set(ratios.values()) != {P.STRETCH_RATIO}:
        problems.append(f"stretch ratios {ratios}, expected every stage at the "
                        f"proven {P.STRETCH_RATIO} -- the ladder varies SIZE "
                        f"and nothing else")
    settles = {s: session.sp.for_stage(s, n).extra_settle_s for s in range(n)}
    if set(settles.values()) != {0.0}:
        problems.append(f"extra dwell {settles}, expected none -- same reason")
    if plan.violations:
        problems.append(f"{len(plan.violations)} geometry violation(s)")

    if problems:
        raise SystemExit(
            f"\n  REFUSING TO RUN the {size}x{size} rung: it is not the\n"
            "  geometry this ladder compares.\n"
            + "".join(f"    - {p}\n" for p in problems)
            + "\n  Re-check with `python -m microdrop.protocol --plan-only "
              "--axes HW`\n  and update EXPECT at the top of this file.\n")


def record(size: int, outcome: str, log: list, note: str) -> None:
    """Append one rung's result. The finding is the comparison, not the run."""
    os.makedirs(os.path.dirname(LADDER_LOG), exist_ok=True)
    row = {
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "size": size,
        "leaf": list(EXPECT[size][0]),
        "outcome": outcome,
        "gates": [{"asked": q, "answered": a} for q, a in log],
        "note": note,
        "stretch_ratio": P.STRETCH_RATIO,
        "step_delay_s": STEP_DELAY_S,
    }
    with open(LADDER_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    say("Log", f"appended to {LADDER_LOG}")


def summary() -> int:
    """Print the ladder so far. No hardware, no chip."""
    if not os.path.exists(LADDER_LOG):
        print(f"\n  No runs recorded yet at {LADDER_LOG}\n")
        return 0
    rows = [json.loads(l) for l in open(LADDER_LOG, encoding="utf-8") if l.strip()]
    banner("SCALE LADDER SO FAR")
    print(f"  {'size':>6} {'leaf':>7} {'outcome':22s} when")
    for r in sorted(rows, key=lambda x: -x["size"]):
        leaf = f'{r["leaf"][0]}x{r["leaf"][1]}'
        print(f'  {r["size"]}x{r["size"]:<3} {leaf:>7} {r["outcome"]:22s} {r["when"]}')
    done = {r["size"] for r in rows}
    missing = [s for s in SIZES if s not in done]
    print(f"\n  rungs still to run: "
          f"{', '.join(f'{s}x{s}' for s in missing) if missing else 'none'}")
    print("\n  Reading it: a clean break -- everything at and above some size\n"
          "  parting, everything below failing -- is evidence for size-dependent\n"
          "  physics rather than for anything in the plan. Scattered results\n"
          "  point at the rig instead: rail voltage under load, droplet volume\n"
          "  at load, or the unmeasured plate gap.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run one rung of the scale ladder: the same H,W split tree "
                    "at a chosen size, at the proven 1.75 stretch. ALWAYS "
                    "ARMED. NOT CONFIRMED ON HARDWARE.")
    ap.add_argument("--size", type=int, choices=SIZES,
                    help="droplet edge in electrodes")
    ap.add_argument("--summary", action="store_true",
                    help="print the ladder recorded so far and exit. No hardware.")
    ap.add_argument("--arm", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.summary:
        return summary()
    if args.size is None:
        ap.error("give --size (one of "
                 f"{', '.join(str(s) for s in SIZES)}) or --summary")

    size = args.size
    leaf, frames, gap = EXPECT[size]

    banner(f"SCALE LADDER — {size}x{size} — NOT CONFIRMED ON HARDWARE")
    print(f"  load     {size}x{size} AT the split position, row {SPLIT_ROW}, "
          f"col {SPLIT_COL} (no walk, deliberately)")
    print(f"  stage 0  axis H   {size}x{size} -> two {size // 2}x{size}")
    print(f"  stage 1  axis W   -> four {leaf[0]}x{leaf[1]}")
    print(f"  stretch  {P.STRETCH_RATIO} on every stage (proven), neck gap {gap}")
    print(f"  cost     {frames} frames, ~{frames * STEP_DELAY_S:.0f}s of dwell")
    print(f"  purpose  find the size at which this stops working. Record WHICH "
          f"STAGE fails.")

    cfg = ChipConfig()
    backend = make_backend("auto", DEFAULT_DLL_DIR, DEFAULT_DLL_NAME,
                           cfg.rows, cfg.cols)
    is_fake = type(backend).__name__ == "FakeBackend"
    say("Rig", f"{type(backend).__name__}"
        + ("  <- NOT the hardware" if is_fake else f"  ({DEFAULT_DLL_DIR})"))
    if is_fake:
        print("\n  The vendor DLL did not load, so this run would energise\n"
              "  nothing while looking exactly like one that did.\n"
              "  Use the Windows interpreter:\n"
              "    .\\.venv\\Scripts\\python.exe "
              "microdrop\\testing\\scaleladder.py --size " f"{size}\n")
        return 4

    chip = ChipController(backend, cfg.rows, cfg.cols, cfg.volts,
                          armed=True,
                          step_delay_s=STEP_DELAY_S,
                          volt_tolerance=cfg.volt_tolerance,
                          volt_settle_s=cfg.volt_settle_s,
                          power_settle_s=cfg.power_settle_s)

    session = SplitSession(
        chip=chip,
        root=SP.DropNode(id="d", parent=None, stage=0, height=size, width=size,
                         row=SPLIT_ROW, col=SPLIT_COL),
        axes=AXES,
        cfg=cfg,
        sp=P.SplitParams(),          # proven ratio, no overrides. The point.
        transport=False,             # load at the split position; see docstring
        confirm=confirm,
        announce=lambda m: say("Split", m),
    )
    check_geometry(session, size)
    session.notes.append(
        f"SCALE LADDER rung {size}x{size}. Same mechanic as every other rung, "
        f"proven {P.STRETCH_RATIO} stretch, no dwell override, no transport. "
        f"The finding is the comparison across rungs, not this run.")

    with chip:
        banner("PHASE 0 — power and rails")
        check = chip.verify_voltage()
        for line in check.summary().splitlines():
            say("Volts", line)
        if not check.ok:
            print("\n  Rails do not match what was commanded. Refusing.\n")
            return 3
        if not confirm("Is the voltage connection verified and good?"):
            print("\n  Stopped before loading.\n")
            return 1

        banner(f"SPLIT — {size}x{size}")
        try:
            report = session.run()
        except OperatorAbort as exc:
            stage = "stage 0" if len(session.log) <= 2 else "stage 1"
            banner(f"DID NOT SEPARATE — stopped at {stage}")
            print(f"  at: {exc}\n")
            record(size, f"FAILED at {stage}", session.log, str(exc))
            print(session.report())
            return 1
        except ClearanceViolation as exc:
            banner("REFUSED — geometry does not fit")
            print(exc)
            return 2

    banner(f"COMPLETE — {size}x{size} parted into four {leaf[0]}x{leaf[1]}")
    record(size, "SEPARATED", session.log, "all gates confirmed")
    print(report)
    print(f"\n  Next rung: "
          + (f"--size {SIZES[SIZES.index(size) + 1]}"
             if SIZES.index(size) + 1 < len(SIZES) else "none, ladder complete")
          + "   |   see the whole ladder with --summary\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
