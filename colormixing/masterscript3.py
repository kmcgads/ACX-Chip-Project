"""
run_experiment.py
─────────────────
Bayesian-optimized closed-loop color mixing on a DMF chip.

Each trial:
  1. Bayesian suggests reservoir widths → writes to colormixcsv.xlsx
  2. csvvolcont mixes the drop on chip
  3. Camera measures the resulting color
  4. Bayesian scores it (DeltaE vs target) and updates its model
  5. Drop held, transferred to the graveyard (upper-right corner), then the
     graveyard is shrunk in from 30x30 down to 1x1
  Repeats up to N_CALLS times, stops early if DeltaE < 2.0.

NOTE — graveyard behavior
─────────────────────────
csvvolcont no longer holds an always-on graveyard zone. The graveyard exists
only while cleanreload is running it, in two separate commands:
    cleanreload.move_to_graveyard(trial_number=n)   # transfer: right, then up
    cleanreload.shrink_graveyard()                  # 30x30 → 1x1, Enter per step
shrink_graveyard() prompts for Enter on every one of its 29 steps by default;
pass interactive=False to run it unattended.

Usage: python run_experiment.py
"""

import sys
import os
import openpyxl
from openpyxl.cell.cell import MergedCell

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import csvvolcont
import cleanreload
from camera import CameraInterface
from bayesopttest1 import BlindOptimizer, random_vivid_target_color

# ── Config ────────────────────────────────────────────────────────────────────

CAMERA_INDEX   = 1      # Change if microscope is on a different index
RANDOM_SEED    = None   # None = new random target color each run
N_CALLS        = 5      # Max number of trials

# Graveyard shrink: True = press Enter for each of the 29 shrink steps,
# False = run all steps back-to-back on cleanreload.STEP_DELAY
SHRINK_INTERACTIVE = True

# Must match the path csvvolcont.load_piece_widths() reads from
COLOR_MIX_XLSX = r"C:\Users\klmcg\OneDrive\Documents\colormixcsv.xlsx"

# ── Helpers ───────────────────────────────────────────────────────────────────

def wait(msg: str) -> None:
    """Block until the user presses Enter."""
    print(f"\n  {'─' * 59}")
    input(f"  >>> {msg} — press Enter to continue")
    print(f"  {'─' * 59}\n")


def pct(width: int, total: int) -> str:
    """Return width as a percentage string of total."""
    return f"{width / total * 100:.1f}%" if total > 0 else "—"


def capture_color(cam: CameraInterface) -> str:
    """Capture a frame and return the measured hex color string."""
    print("  [Camera] Taking picture...")
    image_path, frame = cam.take_picture()
    print(f"  [Camera] Saved: {image_path}")
    result = cam.detect_drop_color(frame)
    print(f"  [Camera] RGB: {result['rgb']}  |  Hex: {result['hex']}")
    return result['hex']


def write_widths_to_xlsx(w1: int, w2: int, w3: int, path: str = COLOR_MIX_XLSX) -> None:
    """Write Bayesian-suggested widths to row 2 of colormixcsv.xlsx.

    Row 2 columns 1-3 must be three ordinary cells. If any of them is inside a
    merged range, openpyxl hands back a MergedCell whose `value` is read-only,
    and the bare assignment this used to do died on
    `'MergedCell' object attribute 'value' is read-only` -- mid-loop, naming
    neither the file nor the cell. Checked up front instead, so the run stops
    with something actionable before it writes a partial row.
    """
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    if ws is None:
        raise ValueError(f"{path}: no active worksheet")
    cells = []
    for column in (1, 2, 3):
        cell = ws.cell(row=2, column=column)
        if isinstance(cell, MergedCell):
            raise ValueError(
                f"{path}: row 2, column {column} is inside a merged range and "
                f"cannot be written. Unmerge row 2, columns 1-3.")
        cells.append(cell)
    for cell, width in zip(cells, (w1, w2, w3)):
        cell.value = width
    wb.save(path)
    print(f"  [XLSX] Widths written → piece_1={w1}, piece_2={w2}, piece_3={w3}")


def clear_drop_to_graveyard(trial_number: int) -> None:
    """
    Full end-of-trial cleanup, in the order cleanreload expects:
      1. hold_reservoirs_and_drop() — pin everything so nothing drifts
      2. move_to_graveyard()        — transfer only (right, then up to row 1)
      3. shrink_graveyard()         — 30x30 pad → 1x1, separate command
    """
    print("  [Chip] Pinning reservoirs and merged drop...")
    cleanreload.hold_reservoirs_and_drop()

    print("  [Chip] Transferring drop to the graveyard (upper-right corner)...")
    cleanreload.move_to_graveyard(trial_number=trial_number)

    wait("Drop parked in the corner — ready to SHRINK the graveyard in")
    print("  [Chip] Shrinking graveyard 30x30 → 1x1...")
    cleanreload.shrink_graveyard(interactive=SHRINK_INTERACTIVE)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 65)
    print("  BAYESIAN OPTIMIZATION EXPERIMENT — DMF Chip")
    print("=" * 65)

    # ── Setup ──────────────────────────────────────────────────────────────
    target = random_vivid_target_color(seed=RANDOM_SEED)
    cam    = CameraInterface(camera_address=CAMERA_INDEX)
    opt    = BlindOptimizer(target, n_calls=N_CALLS)

    print(f"\n  Target color  : {target.hex}  RGB({target.r}, {target.g}, {target.b})")
    print(f"  Trials        : {opt.n_calls}  |  Trial 1 = PRESET (5,5,5), rest = GP-guided")
    print(f"\n  DeltaE scoring:  0–2 = visually identical ✓  |  2–10 = noticeable  |  10+ = very different")

    wait("Confirm hardware is connected — ready to initialize chip")
    print("  [Setup] Initializing chip...")
    csvvolcont.initialize()
    print("  [Setup] Chip ready.")

    # ── Trial loop ──────────────────────────────────────────────────────────
    for trial in range(opt.n_calls):
        n = trial + 1
        phase = "PRESET (5,5,5)" if trial == 0 else "GP-GUIDED"

        print(f"\n{'═' * 65}")
        print(f"  TRIAL {n} of {opt.n_calls}  [{phase}]")
        print(f"{'═' * 65}")

        # Step 1: Bayesian selects widths
        wait(f"Step 1 — Bayesian width selection ({phase})")
        w1, w2, w3 = opt.ask()
        write_widths_to_xlsx(w1, w2, w3)
        total = w1 + w2 + w3
        print(f"\n  Widths → piece_1={w1} ({pct(w1, total)})  piece_2={w2} ({pct(w2, total)})  piece_3={w3} ({pct(w3, total)})  total={total}")

        # Step 2: Mix on chip
        wait("Step 2 — mix drop on chip")
        print("  [Chip] Running csvvolcont...")
        csvvolcont.main()

        # Step 3: Capture color
        wait("Step 3 — capture mixed color with camera")
        measured_hex = capture_color(cam)

        # Step 4: Score result
        wait(f"Step 4 — score {measured_hex} against target {target.hex}")
        converged = opt.tell(measured_hex)
        result    = opt.get_result()

        # Safely read this trial's DeltaE
        try:
            this_delta_e = opt._history[-1].delta_e
        except (AttributeError, IndexError):
            this_delta_e = float("nan")

        print(f"\n  Target: {target.hex}  |  Measured: {measured_hex}")
        print(f"  DeltaE this trial: {this_delta_e:.2f}  |  Best so far: {result.best_delta_e:.2f}  |  Converged: {converged}")

        if converged:
            print(f"\n  [CONVERGED] DeltaE < 2.0 — match found after {n} trial(s)!")

        # Step 5: Graveyard transfer + shrink — runs even on the final trial so
        # the chip is never left with a drop parked at the meeting point.
        wait("Step 5 — move drop to graveyard")
        clear_drop_to_graveyard(trial_number=n)

        if converged:
            break

        if trial < opt.n_calls - 1:
            wait(f"Trial {n} done — ready for Trial {n + 1}")

    # ── Final result ────────────────────────────────────────────────────────
    result = opt.get_result()

    print(f"\n{'=' * 65}")
    print(f"  EXPERIMENT COMPLETE")
    print(f"{'=' * 65}")
    print(f"  Best widths : piece_1={result.width_1}  piece_2={result.width_2}  piece_3={result.width_3}")
    print(f"  Best DeltaE : {result.best_delta_e:.2f}  |  Converged: {result.converged}")
    print(f"  Target hex  : {result.target_hex}")
    print(f"  Log saved to: optimization_log.csv")
    print(f"{'=' * 65}\n")

    opt.save_log(result)


if __name__ == "__main__":
    main()