"""
cleanreload.py
─────────────────────────────────────────────────────────────────────────────
Runs after the colormix experiment (split/merge/mix) is complete.
The merged drop must be sitting at row=55, col=55 before running this.

  1. move_to_graveyard  — slides the merged drop right then down into the
                          graveyard zone anchored at the bottom-right corner
                          (row=128, col=128). The graveyard grows upward by
                          DROP_SIZE each trial to hold accumulated liquid.
  2. reload_reservoirs  — re-activates all three main bodies at 10×15 to
                          restore them after liquid lost during splitting.

─── Usage from master script ──────────────────────────────────────────────────

    import cleanreload

    cleanreload.move_to_graveyard(trial_number=1)
    cleanreload.reload_reservoirs()

────────────────────────────────────────────────────────────────────────────────
"""

import ctypes
from ctypes import Structure
import time
import os

__all__ = ["move_to_graveyard", "reload_reservoirs"]

# ── DLL load ──────────────────────────────────────────────────────────────────

os.add_dll_directory(r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows")
microfluidics = ctypes.CDLL(
    r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll"
)


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]


def activate(drops, debug_label=""):
    n = len(drops)
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
    microfluidics.ActivateElec(128, 128, n, arr)
    time.sleep(0.5)


# ── Constants ─────────────────────────────────────────────────────────────────

MAIN_H      = 10
MAIN_W      = 15
MAIN_COL    = 2
DROP1_ROW   = 55
DROP2_ROW   = 105
DROP3_ROW   = 10

# Merged drop starting position after experiment
MEETING_ROW = 55    # row where pieces merged (top edge)
MEETING_COL = 55    # col where pieces merged
DROP_SIZE   = 20    # merged drop is 20x20

# Graveyard — bottom-right corner fixed at 128,128, grows upward each trial
GRAVEYARD_WIDTH  = DROP_SIZE              # 20 cols wide
GRAVEYARD_LEFT   = 128 - GRAVEYARD_WIDTH + 1   # col 109
GRAVEYARD_BOTTOM = 128                    # row 128


# ── Step 1: Move merged drop to graveyard ─────────────────────────────────────

def move_to_graveyard(trial_number: int) -> None:
    """
    Moves the 20x20 merged drop from (row=55, col=55) into the graveyard zone
    at the bottom-right corner (anchored at row=128, col=128).

    The graveyard grows upward by DROP_SIZE each trial:
      Trial 1 → rows 109–128, cols 109–128  (20x20)
      Trial 2 → rows  89–128, cols 109–128  (40x20)
      Trial 3 → rows  69–128, cols 109–128  (60x20)

    All three main reservoir bodies stay held throughout.

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
    drop_landing_row = graveyard_top    # new drop lands at top of graveyard zone

    print(f"\n[Graveyard | Trial {trial_number}]")
    print(f"  Drop start : row={MEETING_ROW}, col={MEETING_COL}, {DROP_SIZE}x{DROP_SIZE}")
    print(f"  Landing    : row={drop_landing_row}, col={GRAVEYARD_LEFT}")
    print(f"  Graveyard after merge: rows {graveyard_top}–{GRAVEYARD_BOTTOM}, "
          f"cols {GRAVEYARD_LEFT}–128  ({graveyard_height}x{GRAVEYARD_WIDTH})")

    # ── Phase 1: Slide drop RIGHT — col 55 → 109 ─────────────────────────────
    print(f"\n[Step 1 — Phase 1] Moving drop right: col {MEETING_COL} → {GRAVEYARD_LEFT}...")
    activate(
        mains + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, MEETING_COL)],
        debug_label=f"GRAVEYARD start: {DROP_SIZE}x{DROP_SIZE} at row={MEETING_ROW} col={MEETING_COL}"
    )
    input(f"\n>>> Drop at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to move right")
    time.sleep(1)

    for col in range(MEETING_COL + 1, GRAVEYARD_LEFT + 1):
        activate(
            mains + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, col)],
            debug_label=f"MOVE RIGHT col={col}"
        )
        print(f"  row={MEETING_ROW}, col={col}")

    input(f"\n>>> Drop at col={GRAVEYARD_LEFT} -- press Enter to move down")
    time.sleep(1)

    # ── Phase 2: Slide drop DOWN — row 55 → graveyard top ────────────────────
    print(f"\n[Step 1 — Phase 2] Moving drop down: row {MEETING_ROW} → {drop_landing_row}...")

    for row in range(MEETING_ROW + 1, drop_landing_row + 1):
        activate(
            mains + [Drop(DROP_SIZE, DROP_SIZE, row, GRAVEYARD_LEFT)],
            debug_label=f"MOVE DOWN row={row}"
        )
        print(f"  row={row}, col={GRAVEYARD_LEFT}")

    input(f"\n>>> Drop at row={drop_landing_row}, col={GRAVEYARD_LEFT} -- press Enter to merge into graveyard")
    time.sleep(1)

    # ── Phase 3: Merge — hold full graveyard area ─────────────────────────────
    print(f"\n[Step 1 — Phase 3] Merging into graveyard "
          f"(rows {graveyard_top}–{GRAVEYARD_BOTTOM}, cols {GRAVEYARD_LEFT}–128)...")
    activate(
        mains + [Drop(graveyard_height, GRAVEYARD_WIDTH, graveyard_top, GRAVEYARD_LEFT)],
        debug_label=f"HOLD GRAVEYARD trial={trial_number} size={graveyard_height}x{GRAVEYARD_WIDTH}"
    )
    print(f"  Graveyard held: {graveyard_height}x{GRAVEYARD_WIDTH} at rows {graveyard_top}–{GRAVEYARD_BOTTOM}.")
    input("\n>>> Graveyard held -- press Enter to reload reservoirs")


# ── Step 2: Reload reservoirs to 10×15 ────────────────────────────────────────

def reload_reservoirs():
    """
    Re-activates each main body at 10×15 one at a time, then holds all three.
    The reservoir well is co-located with the main drop (same row, col=2) —
    activating the electrode draws fresh fluid back to full 10×15.
    """
    print("\n[Step 2] Reloading reservoirs to 10×15...")
    input(">>> Ensure fresh fluid is available at each well -- press Enter to reload")

    time.sleep(1)

    for label, row in [("Drop 1", DROP1_ROW), ("Drop 2", DROP2_ROW), ("Drop 3", DROP3_ROW)]:
        activate(
            [Drop(MAIN_H, MAIN_W, row, MAIN_COL)],
            debug_label=f"RELOAD {label} row={row}"
        )
        print(f"  {label} (row={row}): restored to {MAIN_H}×{MAIN_W}")
        time.sleep(0.5)

    activate(
        [
            Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
            Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
            Drop(MAIN_H, MAIN_W, DROP3_ROW, MAIN_COL),
        ],
        debug_label="HOLD ALL 3 at 10×15"
    )
    print("  All three reservoirs restored to 10×15.")
    input(">>> Reservoirs reloaded -- press Enter to finish")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    try:
        trial = int(input("Enter trial number (1, 2, 3 ...): ").strip())
    except ValueError:
        print("Invalid trial number.")
        return

    move_to_graveyard(trial_number=trial)
    reload_reservoirs()
    print("\n=== Done: drop in graveyard, reservoirs at 10×15 ===")


if __name__ == "__main__":
    main()