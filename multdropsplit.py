"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List
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

def activate(drops, debug_label=""):
    """
    Activate the given drops and print exactly what is being sent
    to the device so we can verify every electrode coordinate.
    """
    n = len(drops)
    arr = (Drop * n)(*drops)

    # Print every drop being sent this call
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
    input(f"{label} loaded -- 10 tall x 20 wide at row={row}")
    time.sleep(2)

    # ── Step 2: Stretch full drop wider ───────────────────────
    for i in range(1, 16):
        activate(
            held_drops(held_rows) + [Drop(MAIN_H, 20 + i, row, MAIN_COL)],
            debug_label=f"{label} STRETCH width={20+i}"
        )
        input(f"{label} stretching, width={20+i}")
    time.sleep(2)

    # ── Step 3: Pattern both drops ────────────────────────────
    activate(
        held_drops(held_rows) + [
            Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
            Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
        ],
        debug_label=f"{label} SPLIT PATTERN"
    )
    input(f"{label} split -- main at col={MAIN_COL}, piece at col={PIECE_START_COL}")
    time.sleep(2)

    # ── Step 4: Move piece 25px right, pinching 10 → 5 wide ──
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
        input(f"{label} step {i}/25 -- piece at col={current_col}, width={current_width}")
    time.sleep(2)

    input(f"{label} piece at col={PIECE_FINAL_COL} -- beginning deactivation")

    # ── Step 5: Deactivate neck col by col ────────────────────
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

        input(f"{label} col={release_col} released -- bridge={bridge_width}")

    input(f"{label} fully split -- main col={MAIN_COL}, piece col={PIECE_FINAL_COL}")
    time.sleep(1)


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
    split_and_move(row=55, label="Drop 1 (row=55)", held_rows=[])
    input("Drop 1 holding -- starting Drop 2")

    # ── Drop 2: row=105 ───────────────────────────────────────
    split_and_move(row=105, label="Drop 2 (row=105)", held_rows=[55])

    input("Both drops split and holding")

    # ── Shutdown ──────────────────────────────────────────────
    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()

if __name__ == "__main__":
    main()