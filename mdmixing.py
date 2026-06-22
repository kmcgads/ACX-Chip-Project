"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
from ctypes import Structure
import time

# Load library
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]

#helps calla and activate electrodes using the set voltage and keeps track of drops
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
MEETING_ROW = (DROP1_ROW + DROP2_ROW) // 2        # row=80

#Helps define how the maind rops should be held and kept during and after the split
def held_drops(held_rows):
    drops = []
    for r in held_rows:
        drops.append(Drop(MAIN_H, MAIN_W,      r, MAIN_COL))
        drops.append(Drop(MAIN_H, PIECE_END_W, r, PIECE_FINAL_COL))
    return drops


def split_and_move(row, label, held_rows):

    # ── Step 1: Load initial drop ─────────────────────────────
    activate(
        held_drops(held_rows) + [Drop(MAIN_H, 20, row, MAIN_COL)],
        debug_label=f"{label} LOAD"
    )
    input(f"\n>>> {label} loaded at row={row} -- press Enter to begin stretch")

    # ── Step 2: Stretch ───────────────────────────────────────
    print(f"{label} stretching from width=20 to width=35...")
    time.sleep(2)
    for i in range(1, 16):
        activate(
            held_drops(held_rows) + [Drop(MAIN_H, 20 + i, row, MAIN_COL)],
            debug_label=f"{label} STRETCH width={20+i}"
        )
    input(f">>> {label} fully stretched to width=35 -- press Enter to split")

    # ── Step 3: Pattern both drops ────────────────────────────
    time.sleep(2)
    activate(
        held_drops(held_rows) + [
            Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
            Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
        ],
        debug_label=f"{label} SPLIT PATTERN"
    )
    input(f">>> {label} split patterned -- press Enter to move piece")

    # ── Step 4: Move piece ────────────────────────────────────
    print(f"{label} moving piece 25px right, pinching 10 → 5 wide...")
    time.sleep(2)
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

    # ── Step 5: Deactivate neck ───────────────────────────────
    print(f"{label} deactivating neck from col={NECK_END} to col={NECK_START}...")
    time.sleep(2)
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
    steps_to_meet = MEETING_ROW - DROP1_ROW   # 25 steps

    input(f"\n>>> Both drops split -- press Enter to move pieces to meet at row={MEETING_ROW}")

    print(f"Moving pieces toward row={MEETING_ROW}...")
    time.sleep(1)
    for i in range(1, steps_to_meet + 1):
        piece1_row = DROP1_ROW + i
        piece2_row = DROP2_ROW - i
        activate(
            [
                Drop(MAIN_H, MAIN_W,      DROP1_ROW,  MAIN_COL),
                Drop(MAIN_H, PIECE_END_W, piece1_row, PIECE_FINAL_COL),
                Drop(MAIN_H, MAIN_W,      DROP2_ROW,  MAIN_COL),
                Drop(MAIN_H, PIECE_END_W, piece2_row, PIECE_FINAL_COL),
            ],
            debug_label=f"MOVE TO MEET step={i} piece1={piece1_row} piece2={piece2_row}"
        )
        print(f"  piece1 at row={piece1_row}, piece2 at row={piece2_row}")

    input(f">>> Pieces met at row={MEETING_ROW} -- press Enter to merge")

    # Both pieces are now at MEETING_ROW. Merge into a single drop of the same
    # footprint. Adjust MAIN_H here if the hardware expects a larger merged volume.
    time.sleep(1)
    activate(
        [
            Drop(MAIN_H, MAIN_W,     DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W,     DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),
        ],
        debug_label="MERGE combined drop at meeting row"
    )
    input(f">>> Drops merged at row={MEETING_ROW}, col={PIECE_FINAL_COL} -- press Enter to finish")


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

    v1 = ctypes.c_int(0)
    v2 = ctypes.c_int(0)
    v3 = ctypes.c_int(0)
    v4 = ctypes.c_int(0)
    v5 = ctypes.c_int(0)
    v6 = ctypes.c_int(0)
    v7 = ctypes.c_int(0)
    v8 = ctypes.c_int(0)
    v9 = ctypes.c_int(0)
    microfluidics.InquireVolt(
        ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
        ctypes.byref(v4), ctypes.byref(v5), ctypes.byref(v6),
        ctypes.byref(v7), ctypes.byref(v8), ctypes.byref(v9)
    )
    print(f"Voltages: {v1.value} {v2.value} {v3.value} {v4.value} {v5.value} {v6.value} {v7.value} {v8.value} {v9.value}")
    input("Voltage query completed")
    #Now the second drop starts splitting because the first drop is split and in position
    # ── Drop 1: row=55 ────────────────────────────────────────
    split_and_move(row=DROP1_ROW, label="Drop 1 (row=55)", held_rows=[])
    input(">>> Drop 1 holding -- press Enter to start Drop 2")

    # ── Drop 2: row=105 ───────────────────────────────────────
    split_and_move(row=DROP2_ROW, label="Drop 2 (row=105)", held_rows=[DROP1_ROW])

    # ── Move pieces to meet and merge ─────────────────────────
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