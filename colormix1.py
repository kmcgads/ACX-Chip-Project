"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded.
"""

import ctypes
from ctypes import POINTER, c_int, c_void_p, Structure
import time

# Load the ACX-provided DLL. Replace this path with the actual DLL location.
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")

# ── Argtypes -- tells ctypes the exact C signature of each DLL function ──
# Without these, ctypes may mispack arguments and the DLL will behave incorrectly.
microfluidics.SetPower.argtypes     = [ctypes.c_bool]
microfluidics.SetVolt.argtypes      = [c_int] * 9
microfluidics.InquireVolt.argtypes  = [POINTER(c_int)] * 9
microfluidics.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
microfluidics.ActivateElec.restype  = c_int


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]


def activate(drops, debug_label=""):
    """
    Sends a list of Drop objects to the device in a single ActivateElec call,
    then sleeps 0.5s to allow the electrodes to settle.

    Prints a detailed breakdown of every electrode region being activated
    so each call can be verified against expected chip coordinates.

    Args:
        drops:       List of Drop objects to activate simultaneously.
        debug_label: Short string identifying this call in the printed output.
    """
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


# ── Movement constants ────────────────────────────────────────
# These define the fixed electrode geometry used by every split sequence.
# All values are in electrode units on the 128x128 chip grid.

MAIN_COL        = 5    # left edge of every main (reservoir) drop
MAIN_H          = 10   # height of every drop region (rows)
MAIN_W          = 15   # width of every main drop (cols 5–19)
PIECE_START_COL = 30   # column where the piece first appears after split
PIECE_START_W   = 10   # piece width at the moment of split
PIECE_END_W     = 5    # piece width after pinching is complete
STRETCH_STEPS   = 25   # number of steps the piece moves right during pinch
NECK_START      = MAIN_COL + MAIN_W     # col=20 -- right edge of main drop
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55 -- final piece position
NECK_END        = PIECE_FINAL_COL - 1              # col=54 -- rightmost neck column

# ── Drop starting rows (one row per reservoir) ────────────────
DROP1_ROW = 55   # reservoir for Drop 1
DROP2_ROW = 105  # reservoir for Drop 2
DROP3_ROW = 10   # reservoir for Drop 3

# ── Meeting point where all three pieces converge ─────────────
MEETING_ROW = 55   # row all pieces align to before moving left
MEETING_COL = 30   # column all pieces travel to for the merge


def held_drops(held_rows):
    """
    Builds the list of Drop objects needed to keep previously-split drops
    held in place while a new split sequence runs.

    For each row in held_rows, two drops are added:
      - The main (reservoir) drop at MAIN_COL
      - The piece drop at PIECE_FINAL_COL

    Args:
        held_rows: List of row values for drops that have already been split
                   and must stay active.

    Returns:
        List of Drop objects to include in the next activate() call.
    """
    drops = []
    for r in held_rows:
        drops.append(Drop(MAIN_H, MAIN_W,      r, MAIN_COL))
        drops.append(Drop(MAIN_H, PIECE_END_W, r, PIECE_FINAL_COL))
    return drops


def load_and_hold_drop(row, label, held_rows):
    """
    Activates the starting 10x20 electrode for a new drop and pauses
    so the user can physically place the drop on the chip before the
    stretch sequence begins. Any already-split drops in held_rows are
    kept active at the same time.

    Args:
        row:       Chip row for the new drop's starting electrode.
        label:     Human-readable name for this drop (used in print output).
        held_rows: Rows of previously split drops to keep held.
    """
    activate(
        held_drops(held_rows) + [Drop(MAIN_H, 20, row, MAIN_COL)],
        debug_label=f"{label} LOAD"
    )
    input(f"\n>>> {label} -- starting electrode active at row={row}, col={MAIN_COL} "
          f"(10x20) -- LOAD YOUR DROP NOW, then press Enter to begin stretch")
    time.sleep(2)


def split_and_move(row, label, held_rows):

    # ── Step 1: Load and hold for physical loading ────────────
    load_and_hold_drop(row, label, held_rows)

    # ── Step 2: Stretch ───────────────────────────────────────
    print(f"{label} stretching from width=20 to width=35...")
    for i in range(1, 16):
        activate(
            held_drops(held_rows) + [Drop(MAIN_H, 20 + i, row, MAIN_COL)],
            debug_label=f"{label} STRETCH width={20+i}"
        )
    input(f">>> {label} fully stretched to width=35 -- press Enter to split")
    time.sleep(2)

    # ── Step 3: Pattern both drops ────────────────────────────
    activate(
        held_drops(held_rows) + [
            Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
            Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
        ],
        debug_label=f"{label} SPLIT PATTERN"
    )
    input(f">>> {label} split patterned -- press Enter to move piece")
    time.sleep(2)

    # ── Step 4: Move piece ────────────────────────────────────
    # Piece travels right from col=30 to col=55 (25 steps).
    # Width pinches linearly from PIECE_START_W (10) down to PIECE_END_W (5).
    print(f"{label} moving piece 25px right, pinching 10 → 5 wide...")
    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = round(PIECE_START_W - (PIECE_START_W - PIECE_END_W) * i / STRETCH_STEPS)
        activate(
            held_drops(held_rows) + [
                Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
                Drop(MAIN_H, current_width, row, current_col),
            ],
            debug_label=f"{label} MOVE step={i} col={current_col} width={current_width}"
        )
        print(f"  {label} piece at col={current_col}, width={current_width}")
    input(f">>> {label} piece at col={PIECE_FINAL_COL} -- press Enter to begin deactivation")
    time.sleep(2)

    # ── Step 5: Deactivate neck ───────────────────────────────
    # Sweeps from col=54 back to col=20, shrinking the bridge by one
    # column each step. When bridge_width hits 0 the drops are fully separate.
    print(f"{label} deactivating neck from col={NECK_END} to col={NECK_START}...")
    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START

        if bridge_width > 0:
            activate(
                held_drops(held_rows) + [
                    Drop(MAIN_H, MAIN_W,       row, MAIN_COL),
                    Drop(MAIN_H, bridge_width, row, NECK_START),
                    Drop(MAIN_H, PIECE_END_W,  row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} DEACTIVATE col={release_col} bridge={bridge_width}"
            )
        else:
            # Bridge gone -- main and piece are now fully independent
            activate(
                held_drops(held_rows) + [
                    Drop(MAIN_H, MAIN_W,      row, MAIN_COL),
                    Drop(MAIN_H, PIECE_END_W, row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} DEACTIVATE col={release_col} FINAL"
            )
        print(f"  {label} col={release_col} released, bridge remaining={bridge_width}")

    input(f">>> {label} fully split -- press Enter to continue")
    time.sleep(1)


def move_pieces_to_meet():
    """
    Moves all three pieces (10x5 each, at col=55) to a shared meeting
    point at row=55, col=30, then merges them into one combined drop.

    Starting positions:
      Drop 1 piece: row=55,  col=55 -- already on meeting row
      Drop 2 piece: row=105, col=55 -- 50 rows above meeting row
      Drop 3 piece: row=10,  col=55 -- 45 rows below meeting row

    Phase A (row alignment):
      Drop 2 moves up, Drop 3 moves down, both clamped at MEETING_ROW.
      Drop 1 stays fixed. Loop runs for max(50, 45) = 50 steps so all
      three arrive on MEETING_ROW at the same time (Drop 3 clamps 5 steps early).

    Phase B (column convergence):
      All three pieces are now on the same row and column, so they are
      sent as one electrode region moving left from col=55 to col=30
      (25 steps). Main drops remain held throughout both phases.

    Merge:
      A single combined drop of height MAIN_H*3 is activated at the
      meeting point to represent the merged volume.
    """
    input(f"\n>>> All three drops split -- press Enter to move pieces toward "
          f"meeting point row={MEETING_ROW}, col={MEETING_COL}")

    # ── Phase A: align all pieces onto MEETING_ROW ────────────
    # Drop 2 is 50 rows away, Drop 3 is 45 rows away -- loop for the larger distance
    row_steps = max(abs(DROP2_ROW - MEETING_ROW), abs(MEETING_ROW - DROP3_ROW))  # 50

    print(f"Phase A -- aligning all pieces onto row={MEETING_ROW} ({row_steps} steps)...")
    for i in range(1, row_steps + 1):
        piece2_row = max(DROP2_ROW - i, MEETING_ROW)  # moves up, clamps at MEETING_ROW
        piece3_row = min(DROP3_ROW + i, MEETING_ROW)  # moves down, clamps at MEETING_ROW

        activate([
            Drop(MAIN_H, MAIN_W,      DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),  # drop 1 piece stays on meeting row
            Drop(MAIN_H, MAIN_W,      DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, piece2_row,  PIECE_FINAL_COL),
            Drop(MAIN_H, MAIN_W,      DROP3_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, piece3_row,  PIECE_FINAL_COL),
        ],
        debug_label=f"PHASE A step={i} piece2={piece2_row} piece3={piece3_row}"
        )
        print(f"  piece2 row={piece2_row}, piece3 row={piece3_row}")

    input(f">>> All three pieces aligned on row={MEETING_ROW} -- press Enter to begin column convergence")
    time.sleep(1)

    # ── Phase B: move all three pieces left from col=55 to col=30
    # All three pieces occupy the same row and col at this point,
    # so they are sent as a single electrode region.
    col_steps = PIECE_FINAL_COL - MEETING_COL  # 55-30 = 25

    print(f"Phase B -- moving all pieces left to col={MEETING_COL} ({col_steps} steps)...")
    for i in range(1, col_steps + 1):
        current_col = PIECE_FINAL_COL - i
        activate([
            Drop(MAIN_H, MAIN_W,      DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W,      DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W,      DROP3_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, MEETING_ROW, current_col),  # one drop -- all three are at same position
        ],
        debug_label=f"PHASE B step={i} all pieces col={current_col}"
        )
        print(f"  all three pieces now at col={current_col}")

    input(f">>> All three pieces met at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to merge")
    time.sleep(1)

    # ── Merge into one combined drop ──────────────────────────
    # Height is MAIN_H*3 to represent the combined volume of all three pieces.
    # Adjust this value if the hardware expects a different merged geometry.
    activate([
        Drop(MAIN_H,     MAIN_W,      DROP1_ROW,   MAIN_COL),
        Drop(MAIN_H,     MAIN_W,      DROP2_ROW,   MAIN_COL),
        Drop(MAIN_H,     MAIN_W,      DROP3_ROW,   MAIN_COL),
        Drop(MAIN_H * 3, PIECE_END_W, MEETING_ROW, MEETING_COL),
    ],
    debug_label="MERGE -- all three pieces combined at meeting point"
    )
    input(f">>> All three drops merged at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to finish")


def main():
    microfluidics.InitUSB()
    res = microfluidics.OpenUSB()
    if res:
        input("Open successfully")
    else:
        input("Open failed")

    microfluidics.SetPower(True)
    input("Power on completed")

    microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
    input("Voltage set")

    voltages = [ctypes.c_int(0) for _ in range(9)]
    microfluidics.InquireVolt(*[ctypes.byref(v) for v in voltages])
    print("Voltages: " + " ".join(str(v.value) for v in voltages))
    input("Voltage query completed")

    # ── Drop 1: row=55 ────────────────────────────────────────
    split_and_move(row=DROP1_ROW, label="Drop 1 (row=55)", held_rows=[])
    input(">>> Drop 1 holding -- press Enter to start Drop 2")

    # ── Drop 2: row=105 ───────────────────────────────────────
    split_and_move(row=DROP2_ROW, label="Drop 2 (row=105)", held_rows=[DROP1_ROW])
    input(">>> Drop 2 holding -- press Enter to start Drop 3")

    # ── Drop 3: row=10 ────────────────────────────────────────
    split_and_move(row=DROP3_ROW, label="Drop 3 (row=10)", held_rows=[DROP1_ROW, DROP2_ROW])

    # ── Move all three pieces to meet and merge ────────────────
    move_pieces_to_meet()

    input(">>> Sequence complete -- press Enter to shut down")

    # ── Shutdown ──────────────────────────────────────────────
    microfluidics.ActivateElec(128, 128, 0, None)  # deactivate all electrodes
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()