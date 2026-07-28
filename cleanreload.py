"""
cleanreload.py
─────────────────────────────────────────────────────────────────────────────
Runs after the colormix experiment (split/merge/mix) is complete.
The merged drop must be sitting at row=75, col=55 before running this.

  1. hold_reservoirs_and_drop — pins all three reservoir bodies and the merged
                                drop in place simultaneously. Call this before
                                move_to_graveyard to prevent any drift.
  2. move_to_graveyard        — slides the merged drop right then down into the
                                graveyard zone anchored at the bottom-right
                                corner (row=128, col=128). Grows upward by
                                DROP_SIZE each trial.

IMPORTANT — Connection management
──────────────────────────────────
The DLL is loaded ONCE as a module-level singleton (_dll). It is NEVER
closed or re-opened between trials. csvvolcont must not close the device
connection either — the same open connection is shared across all trials.

─── Usage from master script ──────────────────────────────────────────────────

    import cleanreload

    cleanreload.hold_reservoirs_and_drop()      # pin everything before moving
    cleanreload.move_to_graveyard(trial_number=1)

────────────────────────────────────────────────────────────────────────────────
"""

import ctypes
from ctypes import Structure
import time
import os

__all__ = ["hold_reservoirs_and_drop", "move_to_graveyard"]

# ── DLL singleton — loaded once, never closed ─────────────────────────────────

_DLL_DIR  = r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows"
_DLL_PATH = r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll"

os.add_dll_directory(_DLL_DIR)
_dll = ctypes.CDLL(_DLL_PATH)   # loaded once — shared across all trials


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]


def activate(drops, debug_label=""):
    """Send an electrode activation command over the existing open connection."""
    n   = len(drops)
    arr = (Drop * n)(*drops)
    print(f"\n--- ACTIVATE CALL: {debug_label} ---")
    print(f"    Total drops sent to device: {n}")
    for idx, d in enumerate(drops):
        print(
            f"    Drop[{idx}]: "
            f"row={d.row}, col={d.col}, "
            f"height={d.height}, width={d.width} "
            f"| covers rows {d.row}–{d.row + d.height - 1}, "
            f"cols {d.col}–{d.col + d.width - 1}"
        )
    _dll.ActivateElec(128, 128, n, arr)
    time.sleep(0.5)


# ── Constants — must match csvvolcont.py ──────────────────────────────────────

MAIN_H      = 10
MAIN_W      = 15
MAIN_COL    = 2
DROP1_ROW   = 75
DROP2_ROW   = 115
DROP3_ROW   = 25

# Merged drop starting position — matches csvvolcont MEETING_ROW / MEETING_COL
MEETING_ROW = 75    # = DROP1_ROW
MEETING_COL = 55    # = PIECE_FINAL_COL
DROP_SIZE   = 20    # merged drop is 20x20

# Graveyard — bottom-right corner fixed at 128,128, grows upward each trial
GRAVEYARD_WIDTH  = DROP_SIZE
GRAVEYARD_LEFT   = 128 - GRAVEYARD_WIDTH + 1   # col 109
GRAVEYARD_BOTTOM = 128

# Delay between electrode steps (seconds) — increase if drops need more time
STEP_DELAY = 0.5


# ── Step 1: Hold reservoirs and merged drop ───────────────────────────────────

def hold_reservoirs_and_drop() -> None:
    """
    Pins all three reservoir bodies and the merged drop in place simultaneously.
    Call this before move_to_graveyard to prevent any drift.
    The merged drop is held at (MEETING_ROW, MEETING_COL) and will not move.
    """
    print("\n[Hold] Pinning reservoirs and merged drop in place...")
    activate(
        [
            Drop(MAIN_H,    MAIN_W,    DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H,    MAIN_W,    DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H,    MAIN_W,    DROP3_ROW,   MAIN_COL),
            Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, MEETING_COL),
        ],
        debug_label="HOLD reservoirs + merged drop"
    )
    print("  All three reservoirs and merged drop held in place.")


# ── Step 2: Move merged drop to graveyard ─────────────────────────────────────

def move_to_graveyard(trial_number: int) -> None:
    """
    Moves the 20x20 merged drop from (row=75, col=55) into the graveyard zone
    at the bottom-right corner (anchored at row=128, col=128).

    The graveyard grows upward by DROP_SIZE each trial:
      Trial 1 → rows 109–128, cols 109–128  (20x20)
      Trial 2 → rows  89–128, cols 109–128  (40x20)
      Trial 3 → rows  69–128, cols 109–128  (60x20)

    All three main reservoir bodies stay held throughout.
    The device connection is NOT closed — it stays open for the next trial.

    Parameters
    ----------
    trial_number : int
        Which trial just completed (1-indexed).
    """
    mains = [
        Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP3_ROW, MAIN_COL),
    ]

    # Graveyard geometry for this trial
    graveyard_top    = GRAVEYARD_BOTTOM - trial_number * DROP_SIZE + 1
    graveyard_height = trial_number * DROP_SIZE
    drop_landing_row = graveyard_top

    print(f"\n[Graveyard | Trial {trial_number}]")
    print(f"  Drop start : row={MEETING_ROW}, col={MEETING_COL}, {DROP_SIZE}x{DROP_SIZE}")
    print(f"  Landing    : row={drop_landing_row}, col={GRAVEYARD_LEFT}")
    print(f"  Graveyard after merge: rows {graveyard_top}–{GRAVEYARD_BOTTOM}, "
          f"cols {GRAVEYARD_LEFT}–128  ({graveyard_height}x{GRAVEYARD_WIDTH})")

    # ── Phase 1: Slide drop RIGHT — col 55 → 109 ─────────────────────────────
    print(f"\n[Graveyard Phase 1] Moving drop right: col {MEETING_COL} → {GRAVEYARD_LEFT}...")
    activate(
        mains + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, MEETING_COL)],
        debug_label=f"GRAVEYARD start: {DROP_SIZE}x{DROP_SIZE} at row={MEETING_ROW} col={MEETING_COL}"
    )
    time.sleep(STEP_DELAY)

    for col in range(MEETING_COL + 1, GRAVEYARD_LEFT + 1):
        activate(
            mains + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, col)],
            debug_label=f"MOVE RIGHT col={col}"
        )
        print(f"  row={MEETING_ROW}, col={col}")

    time.sleep(STEP_DELAY)

    # ── Phase 2: Slide drop DOWN — row 75 → graveyard top ────────────────────
    print(f"\n[Graveyard Phase 2] Moving drop down: row {MEETING_ROW} → {drop_landing_row}...")

    for row in range(MEETING_ROW + 1, drop_landing_row + 1):
        activate(
            mains + [Drop(DROP_SIZE, DROP_SIZE, row, GRAVEYARD_LEFT)],
            debug_label=f"MOVE DOWN row={row}"
        )
        print(f"  row={row}, col={GRAVEYARD_LEFT}")

    time.sleep(STEP_DELAY)

    # ── Phase 3: Merge — hold full graveyard area ─────────────────────────────
    print(f"\n[Graveyard Phase 3] Merging into graveyard "
          f"(rows {graveyard_top}–{GRAVEYARD_BOTTOM}, cols {GRAVEYARD_LEFT}–128)...")
    activate(
        mains + [Drop(graveyard_height, GRAVEYARD_WIDTH, graveyard_top, GRAVEYARD_LEFT)],
        debug_label=f"HOLD GRAVEYARD trial={trial_number} size={graveyard_height}x{GRAVEYARD_WIDTH}"
    )
    print(f"  Graveyard held: {graveyard_height}x{GRAVEYARD_WIDTH} "
          f"at rows {graveyard_top}–{GRAVEYARD_BOTTOM}.")
    time.sleep(STEP_DELAY)


# ── Main (manual use only) ────────────────────────────────────────────────────

def main() -> None:
    hold_reservoirs_and_drop()
    try:
        trial = int(input("Enter trial number (1, 2, 3 ...): ").strip())
    except ValueError:
        print("Invalid trial number.")
        return
    move_to_graveyard(trial_number=trial)
    print("\n=== Done: drop in graveyard ===")


if __name__ == "__main__":
    main()