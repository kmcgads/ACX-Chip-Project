"""
cleanreload.py
─────────────────────────────────────────────────────────────────────────────
Runs after the colormix experiment (split/merge/mix) is complete.
The merged drop must be sitting at row=55, col=55 before running this.

  1. move_piece_out    — slides the merged drop col=55 → col=128 (off chip).
  2. reload_reservoirs — re-activates all three main bodies at 10×15 to
                         restore them after liquid lost during splitting.

The original code for this chip was written in C++ by ACX Instruments and
adapted for Python using ctypes. The DLL is provided with the purchased device.
"""

import ctypes
from ctypes import Structure
import time
import os

__all__ = ["move_piece_out", "reload_reservoirs"]

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
MEETING_ROW = 55   # row where pieces merged (top edge)
MEETING_COL = 55   # col where pieces merged

# Move-out shrink parameters
MOVE_START_SIZE = 25   # starting height and width of merged drop
MOVE_END_SIZE   = 1    # final height and width at chip edge (col=128)
MOVE_STEPS      = 128 - MEETING_COL   # 73 steps


# ── Step 1: Move merged drop off chip ─────────────────────────────────────────

def move_piece_out():
    """
    Phase 1: Travels the merged drop from col=55 to col=128 as a full 25×25.
    Phase 2: Pinches it down from 25×25 → 1×1 in place at col=128.

    row=55 stays as the top edge throughout.
    All three main bodies stay held throughout.
    Press Ctrl+C to stop early once the drop is fully off chip.
    """
    mains = [
        Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP3_ROW, MAIN_COL),
    ]

    # ── Phase 1: Travel at full 25×25 to col=128 ─────────────────────────────
    print(f"\n[Step 1 — Phase 1] Traveling merged drop col={MEETING_COL} → 128 at {MOVE_START_SIZE}×{MOVE_START_SIZE}...")
    print("  (Press Ctrl+C to stop early once the drop is fully off chip)")

    activate(
        mains + [Drop(MOVE_START_SIZE, MOVE_START_SIZE, MEETING_ROW, MEETING_COL)],
        debug_label=f"MOVE OUT start: {MOVE_START_SIZE}×{MOVE_START_SIZE} at col={MEETING_COL}"
    )
    input(f"\n>>> Merged drop at col={MEETING_COL}, {MOVE_START_SIZE}×{MOVE_START_SIZE} -- press Enter to travel to edge")
    time.sleep(1)

    for i in range(1, MOVE_STEPS + 1):
        current_col = MEETING_COL + i
        activate(
            mains + [Drop(MOVE_START_SIZE, MOVE_START_SIZE, MEETING_ROW, current_col)],
            debug_label=f"MOVE OUT travel step={i} col={current_col}"
        )
        print(f"  col={current_col}, drop={MOVE_START_SIZE}×{MOVE_START_SIZE}")

    input(f"\n>>> Drop at col=128 -- press Enter to begin pinch")
    time.sleep(1)

    # ── Phase 2: Pinch from 25×25 → 1×1 at col=128 ───────────────────────────
    print(f"\n[Step 1 — Phase 2] Pinching drop from {MOVE_START_SIZE}×{MOVE_START_SIZE} → {MOVE_END_SIZE}×{MOVE_END_SIZE} at col=128...")

    pinch_steps = MOVE_START_SIZE - MOVE_END_SIZE  # 24 steps
    for i in range(1, pinch_steps + 1):
        current_size = MOVE_START_SIZE - i
        activate(
            mains + [Drop(current_size, current_size, MEETING_ROW, 128)],
            debug_label=f"PINCH step={i} size={current_size}×{current_size}"
        )
        print(f"  pinch step={i}, drop={current_size}×{current_size}")

    # Drop fully pinched off — hold only the three main bodies
    activate(mains, debug_label="MOVE OUT complete — drop pinched off")
    print("  Merged drop unloaded.")
    input("\n>>> Merged drop off chip -- press Enter to reload reservoirs")


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
    move_piece_out()
    reload_reservoirs()
    print("\n=== Done: drop unloaded, reservoirs at 10×15 ===")


if __name__ == "__main__":
    main()