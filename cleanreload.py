"""
cleanreload.py
─────────────────────────────────────────────────────────────────────────────
GRAVEYARD TRANSFER ONLY.

Runs after the colormix experiment (split/merge/mix) is complete.
The merged drop must be sitting at row=75, col=55 before running this.

  1. hold_reservoirs_and_drop — pins all three reservoir bodies and the merged
                                drop in place simultaneously. Call this before
                                move_to_graveyard to prevent any drift.
  2. move_to_graveyard        — slides the merged drop right, then UP into the
                                graveyard zone anchored at the TOP-RIGHT corner
                                (row=1, col=128). Nothing shrinks here — the
                                drop is just parked and held in the corner.
  3. shrink_graveyard         — SEPARATE command. Opens the 30x30 graveyard pad
                                (rows 1–30, cols 99–128) and deactivates inward
                                one electrode at a time, corner anchored at
                                row=1 col=128, all the way down to 1x1 — then
                                stops. Same technique as cleanup.py.

IMPORTANT — Connection management
──────────────────────────────────
The DLL is loaded ONCE as a module-level singleton (_dll). It is NEVER
closed or re-opened between trials. csvvolcont must not close the device
connection either — the same open connection is shared across all trials.

─── Usage from master script ──────────────────────────────────────────────────

    import cleanreload

    cleanreload.hold_reservoirs_and_drop()          # pin everything first
    cleanreload.move_to_graveyard(trial_number=1)   # transfer only

    # later / on demand — the shrink command:
    cleanreload.shrink_graveyard()                   # 30x30 → 1x1, Enter per step
    cleanreload.shrink_graveyard(interactive=False)  # run all steps unattended

────────────────────────────────────────────────────────────────────────────────
"""

import ctypes
from ctypes import Structure
import time
import os

__all__ = ["hold_reservoirs_and_drop", "move_to_graveyard", "shrink_graveyard"]

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
    _dll.ActivateElec(CHIP_ROWS, CHIP_COLS, n, arr)
    time.sleep(STEP_DELAY)


# ── Chip geometry ─────────────────────────────────────────────────────────────

CHIP_ROWS = 128
CHIP_COLS = 128

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

# ── Graveyard — FIXED 30x30 pad in the UPPER-RIGHT corner ─────────────────────
# Corner anchor is row=1, col=128 (top-right). Size never changes between
# trials — every trial's drop gets merged into the same 30x30 pad.
GRAVEYARD_SIZE   = 30
GRAVEYARD_TOP    = 1                                  # flush to top edge
GRAVEYARD_RIGHT  = CHIP_COLS                          # flush to right edge, col 128
GRAVEYARD_LEFT   = CHIP_COLS - GRAVEYARD_SIZE + 1     # col 99
GRAVEYARD_BOTTOM = GRAVEYARD_TOP + GRAVEYARD_SIZE - 1 # row 30

# Landing position for the incoming 20x20 drop — flush into the top-right corner
DROP_LANDING_ROW = GRAVEYARD_TOP                      # row 1
DROP_LANDING_COL = CHIP_COLS - DROP_SIZE + 1          # col 109

# ── Shrink-in defaults (cleanup.py technique) ────────────────────────────────
# The shrink box is a square anchored flush in the top-right corner. Shrinking
# keeps that corner (row=1, col=128) fixed: the bottom edge moves up by 1 and
# the left edge moves right by 1 on each step. Starts at the 30x30 graveyard pad
# and goes all the way down to a single electrode.
SHRINK_START_SIZE  = GRAVEYARD_SIZE   # 30x30 at rows 1–30, cols 99–128
SHRINK_TARGET_SIZE = 1                # stop at 1x1 (row 1, col 128)

# Delay between electrode steps (seconds) — increase if drops need more time
STEP_DELAY = 0.5


def _mains():
    """The three reservoir bodies — always held."""
    return [
        Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP3_ROW, MAIN_COL),
    ]


def _corner_box(size: int) -> Drop:
    """Square of the given size anchored flush in the top-right corner."""
    return Drop(size, size, GRAVEYARD_TOP, CHIP_COLS - size + 1)


# ── Step 1: Hold reservoirs and merged drop ───────────────────────────────────

def hold_reservoirs_and_drop() -> None:
    """
    Pins all three reservoir bodies and the merged drop in place simultaneously.
    Call this before move_to_graveyard to prevent any drift.
    The merged drop is held at (MEETING_ROW, MEETING_COL) and will not move.
    """
    print("\n[Hold] Pinning reservoirs and merged drop in place...")
    activate(
        _mains() + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, MEETING_COL)],
        debug_label="HOLD reservoirs + merged drop"
    )
    print("  All three reservoirs and merged drop held in place.")


# ── Step 2: Transfer the merged drop to the graveyard (no shrinking) ──────────

def move_to_graveyard(trial_number: int) -> None:
    """
    Moves the 20x20 merged drop from (row=75, col=55) into the graveyard zone
    in the UPPER-RIGHT corner (anchored at row=1, col=128).

    Transfer only — right, then up, then hold. No shrinking happens here;
    call shrink_graveyard() separately when you want to consolidate.

    All three main reservoir bodies stay held throughout.
    The device connection is NOT closed — it stays open for the next trial.

    Parameters
    ----------
    trial_number : int
        Which trial just completed (1-indexed). Logging only — the graveyard
        geometry is identical on every trial.
    """
    mains = _mains()

    print(f"\n[Graveyard | Trial {trial_number}]")
    print(f"  Drop start : row={MEETING_ROW}, col={MEETING_COL}, {DROP_SIZE}x{DROP_SIZE}")
    print(f"  Landing    : row={DROP_LANDING_ROW}, col={DROP_LANDING_COL}")
    print(f"  Graveyard  : rows {GRAVEYARD_TOP}–{GRAVEYARD_BOTTOM}, "
          f"cols {GRAVEYARD_LEFT}–{GRAVEYARD_RIGHT} "
          f"({GRAVEYARD_SIZE}x{GRAVEYARD_SIZE}, fixed, upper-right corner)")

    # ── Phase 1: Slide drop RIGHT — col 55 → 109 ─────────────────────────────
    print(f"\n[Graveyard Phase 1] Moving drop right: col {MEETING_COL} → {DROP_LANDING_COL}...")
    activate(
        mains + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, MEETING_COL)],
        debug_label=f"GRAVEYARD start: {DROP_SIZE}x{DROP_SIZE} at row={MEETING_ROW} col={MEETING_COL}"
    )
    time.sleep(STEP_DELAY)

    for col in range(MEETING_COL + 1, DROP_LANDING_COL + 1):
        activate(
            mains + [Drop(DROP_SIZE, DROP_SIZE, MEETING_ROW, col)],
            debug_label=f"MOVE RIGHT col={col}"
        )
        print(f"  row={MEETING_ROW}, col={col}")

    time.sleep(STEP_DELAY)

    # ── Phase 2: Slide drop UP — row 75 → row 1 (top of chip) ────────────────
    print(f"\n[Graveyard Phase 2] Moving drop up: row {MEETING_ROW} → {DROP_LANDING_ROW}...")

    for row in range(MEETING_ROW - 1, DROP_LANDING_ROW - 1, -1):
        activate(
            mains + [Drop(DROP_SIZE, DROP_SIZE, row, DROP_LANDING_COL)],
            debug_label=f"MOVE UP row={row}"
        )
        print(f"  row={row}, col={DROP_LANDING_COL}")

    time.sleep(STEP_DELAY)

    # ── Phase 3: Hold the drop in the corner ─────────────────────────────────
    print(f"\n[Graveyard Phase 3] Holding drop in the top-right corner "
          f"(rows {DROP_LANDING_ROW}–{DROP_LANDING_ROW + DROP_SIZE - 1}, "
          f"cols {DROP_LANDING_COL}–{GRAVEYARD_RIGHT})...")
    activate(
        mains + [Drop(DROP_SIZE, DROP_SIZE, DROP_LANDING_ROW, DROP_LANDING_COL)],
        debug_label=f"HOLD DROP AT TOP trial={trial_number}"
    )
    print("  Transfer complete. Run shrink_graveyard() to consolidate the pad.")
    time.sleep(STEP_DELAY)


# ── Step 3: Shrink command — pull the blob into the 30x30 pad ────────────────

def shrink_graveyard(start_size: int = SHRINK_START_SIZE,
                     target_size: int = SHRINK_TARGET_SIZE,
                     interactive: bool = True) -> None:
    """
    Shrink command. Opens the 30x30 graveyard pad anchored flush in the
    top-right corner, then deactivates inward one electrode at a time down to
    1x1 and stops — same technique as cleanup.py.

    The top-right corner (row=1, col=128) stays fixed the whole time, so the
    final 1x1 is the single electrode at row=1, col=128.

    Parameters
    ----------
    start_size : int
        Size of the starting box (default 30 → rows 1–30, cols 99–128).
    target_size : int
        Final size (default 1 → single electrode at row 1, col 128).
    interactive : bool
        Default True — every shrink step waits for you to press Enter
        ('s' + Enter aborts and leaves the box at its current size).
        Pass interactive=False to run all steps back-to-back on STEP_DELAY.
    """
    if start_size < target_size:
        print(f"[Shrink] start_size ({start_size}) < target_size ({target_size}) — nothing to do.")
        return

    mains = _mains()
    steps = start_size - target_size

    print(f"\n[Shrink] Opening {start_size}x{start_size} pad at the top-right corner "
          f"(rows {GRAVEYARD_TOP}–{start_size}, "
          f"cols {CHIP_COLS - start_size + 1}–{GRAVEYARD_RIGHT})")
    activate(
        mains + [_corner_box(start_size)],
        debug_label=f"GRAVEYARD PAD {start_size}x{start_size} at top-right corner"
    )
    time.sleep(STEP_DELAY)

    print(f"\n[Shrink] Shrinking in toward the top-right corner over {steps} steps "
          f"({start_size} → {target_size})...")
    for i in range(1, steps + 1):
        if interactive:
            if input(f"  [{start_size - i + 1}x{start_size - i + 1}] "
                     f"Enter to shrink, 's' to stop: ").strip().lower() == "s":
                print("  Shrink stopped early by user.")
                return

        size = start_size - i
        box  = _corner_box(size)
        activate(
            mains + [box],
            debug_label=f"SHRINK step={i}/{steps} size={size}x{size} "
                        f"row={box.row} col={box.col}"
        )
        print(
            f"  step {i}: active area {size}x{size} "
            f"| rows {box.row}–{box.row + size - 1}, "
            f"cols {box.col}–{box.col + size - 1}"
        )

    # ── Final hold, then stop ────────────────────────────────────────────────
    print(f"\n[Shrink] Holding final {target_size}x{target_size} "
          f"(rows {GRAVEYARD_TOP}–{GRAVEYARD_TOP + target_size - 1}, "
          f"cols {CHIP_COLS - target_size + 1}–{GRAVEYARD_RIGHT})...")
    activate(
        mains + [_corner_box(target_size)],
        debug_label=f"HOLD FINAL {target_size}x{target_size}"
    )
    print(f"  Shrink complete — stopped at {target_size}x{target_size} "
          f"in the upper-right corner.")
    time.sleep(STEP_DELAY)


# ── Main (manual use only) ────────────────────────────────────────────────────

def main() -> None:
    print("cleanreload — graveyard transfer")
    print("  1) transfer drop to graveyard (top-right corner)")
    print("  2) shrink graveyard  (30x30 → 1x1, Enter per step)")
    print("  3) shrink graveyard  (unattended, no prompts)")
    print("  4) transfer, then shrink (Enter per step)")
    choice = input("Select 1-4: ").strip()

    if choice in ("1", "4"):
        hold_reservoirs_and_drop()
        try:
            trial = int(input("Enter trial number (1, 2, 3 ...): ").strip())
        except ValueError:
            print("Invalid trial number.")
            return
        move_to_graveyard(trial_number=trial)
        print("\n=== Done: drop parked in graveyard corner ===")

    if choice == "2":
        shrink_graveyard()
    elif choice == "3":
        shrink_graveyard(interactive=False)
    elif choice == "4":
        input("\n>>> Press Enter to begin shrinking the graveyard in")
        shrink_graveyard()

    if choice not in ("1", "2", "3", "4"):
        print("Invalid selection.")


if __name__ == "__main__":
    main()