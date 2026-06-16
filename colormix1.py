import ctypes
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List
import time

# Load library
microfluidics = ctypes.CDLL("C:\\Users\\klmcg\\Downloads\\ACX_pythonSDK v1.2 3\\ACX_pythonSDK\\windows\\DLLTest.dll")

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

# ── Constants ─────────────────────────────────────────────────
MAIN_COL        = 5
MAIN_H          = 10
MAIN_W          = 15
PIECE_START_COL = 30
PIECE_START_W   = 10
PIECE_END_W     = 5
STRETCH_STEPS   = 25
NECK_START      = MAIN_COL + MAIN_W
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55
NECK_END        = PIECE_FINAL_COL - 1              # col=54

DROP1_ROW   = 55
DROP2_ROW   = 105
DROP3_ROW   = 10

# ── New shared meeting point for all three pieces ──────────────
MEETING_ROW = 55
MEETING_COL = 30


def held_drops(held_rows):
    drops = []
    for r in held_rows:
        drops.append(Drop(MAIN_H, MAIN_W,      r, MAIN_COL))
        drops.append(Drop(MAIN_H, PIECE_END_W, r, PIECE_FINAL_COL))
    return drops


def load_and_hold_drop(row, label, held_rows):
    """
    Activates the starting 10x20 electrode for this drop and holds it
    so the user can physically load the drop before stretching begins.
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

    # ── Step 2: Stretch -- no input() inside loop ──────────────
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

    # ── Step 4: Move piece -- no input() inside loop ──────────
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

    # ── Step 5: Deactivate neck -- no input() inside loop ─────
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
    All three pieces (10x5 each, located at row=55/105/10, col=55)
    travel toward a single shared meeting point: row=55, col=30.

    Drop 1 piece: starts row=55,  col=55 -- already on meeting row,
                  just needs to move LEFT in columns to col=30
    Drop 2 piece: starts row=105, col=55 -- needs to move UP in rows
                  to row=55, then left in columns to col=30
    Drop 3 piece: starts row=10,  col=55 -- needs to move DOWN in rows
                  to row=55, then left in columns to col=30

    To keep all three moving together step-by-step and arriving at
    the same time, we break this into two phases:
      Phase A: drops 2 and 3 move row-wise to row=55 (drop 1 already there)
      Phase B: all three move column-wise from col=55 to col=30
    """

    input(f"\n>>> All three drops split -- press Enter to move pieces toward "
          f"meeting point row={MEETING_ROW}, col={MEETING_COL}")

    # ── Phase A: align all pieces onto row=55 ───────────────────
    row_steps = abs(DROP2_ROW - MEETING_ROW)  # 105-55 = 50, same as 55-10=45 -- use the larger of the two
    row_steps = max(abs(DROP2_ROW - MEETING_ROW), abs(MEETING_ROW - DROP3_ROW))  # 50

    print(f"Phase A -- aligning all pieces onto row={MEETING_ROW}...")
    for i in range(1, row_steps + 1):
        # Drop 1 piece stays fixed -- already on meeting row
        piece1_row = MEETING_ROW

        # Drop 2 piece moves up from row=105 toward row=55
        piece2_row = DROP2_ROW - i
        if piece2_row < MEETING_ROW:
            piece2_row = MEETING_ROW

        # Drop 3 piece moves down from row=10 toward row=55
        piece3_row = DROP3_ROW + i
        if piece3_row > MEETING_ROW:
            piece3_row = MEETING_ROW

        activate([
            Drop(MAIN_H, MAIN_W,      DROP1_ROW, MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, piece1_row, PIECE_FINAL_COL),
            Drop(MAIN_H, MAIN_W,      DROP2_ROW, MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, piece2_row, PIECE_FINAL_COL),
            Drop(MAIN_H, MAIN_W,      DROP3_ROW, MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, piece3_row, PIECE_FINAL_COL),
        ],
        debug_label=f"PHASE A step={i} piece1={piece1_row} piece2={piece2_row} piece3={piece3_row}"
        )
        print(f"  piece1 row={piece1_row}, piece2 row={piece2_row}, piece3 row={piece3_row}")

    input(f">>> All three pieces aligned on row={MEETING_ROW} -- press Enter to begin column convergence")
    time.sleep(1)

    # ── Phase B: move all three pieces left from col=55 to col=30 ─
    col_steps = PIECE_FINAL_COL - MEETING_COL  # 55-30 = 25

    print(f"Phase B -- moving all pieces left to col={MEETING_COL}...")
    for i in range(1, col_steps + 1):
        current_col = PIECE_FINAL_COL - i
        activate([
            Drop(MAIN_H, MAIN_W,      DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, MEETING_ROW, current_col),
            Drop(MAIN_H, MAIN_W,      DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, MEETING_ROW, current_col),
            Drop(MAIN_H, MAIN_W,      DROP3_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, MEETING_ROW, current_col),
        ],
        debug_label=f"PHASE B step={i} all pieces col={current_col}"
        )
        print(f"  all three pieces now at col={current_col}")

    input(f">>> All three pieces met at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to merge")
    time.sleep(1)

    # ── Merge into one combined drop ────────────────────────────
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

    v1 = ctypes.c_int(1)
    v2 = ctypes.c_int(2)
    v3 = ctypes.c_int(3)
    v4 = ctypes.c_int(4)
    v5 = ctypes.c_int(5)
    v6 = ctypes.c_int(6)
    v7 = ctypes.c_int(7)
    v8 = ctypes.c_int(8)
    v9 = ctypes.c_int(9)
    microfluidics.InquireVolt(
        ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
        ctypes.byref(v4), ctypes.byref(v5), ctypes.byref(v6),
        ctypes.byref(v7), ctypes.byref(v8), ctypes.byref(v9)
    )
    print(f"Voltages: {v1.value} {v2.value} {v3.value} {v4.value} {v5.value} {v6.value} {v7.value} {v8.value} {v9.value}")
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
    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()

if __name__ == "__main__":
    main()