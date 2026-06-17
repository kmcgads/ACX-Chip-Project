"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
import time
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List

# Load library
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")

class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]

def activate(drops):
    n = len(drops)
    arr = (Drop * n)(*drops)
    microfluidics.ActivateElec(128, 128, n, arr)
    time.sleep(0.5)

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

    # ── Constants ─────────────────────────────────────────────
    DROP_ROW        = 55    # top edge of drop
    MAIN_COL        = 5     # main drop left edge
    MAIN_H          = 10    # main drop height -- 10 rows tall as requested
    MAIN_W          = 15    # main drop width  -- cols 5–19
    PIECE_START_COL = 30    # piece starts here after split
    PIECE_START_W   = 10    # piece width at start of column movement
    PIECE_END_W     = 5     # piece width at end of column movement (pinches to 5)
    STRETCH_STEPS   = 25    # piece moves 25 pixels to the right

    # ── Step 1: Load initial drop ─────────────────────────────
    # Height=10 to match the main drop form requested
    activate([Drop(MAIN_H, 20, DROP_ROW, MAIN_COL)])
    input(f"Drop loaded -- 10 rows tall, 20 wide at row={DROP_ROW} col={MAIN_COL}")
    time.sleep(2)

    # ── Step 2: Stretch full drop wider before splitting ──────
    for i in range(1, 16):
        activate([Drop(MAIN_H, 20 + i, DROP_ROW, MAIN_COL)])
        input(f"Stretching drop, width={20+i}")
    time.sleep(2)

    # Drop now 10 tall x 35 wide, cols 5–39

    # ── Step 3: Pattern both drops ────────────────────────────
    # Main:  10x15 at col=5  (cols 5–19)
    # Gap:   cols 20–29
    # Piece: 10x10 at col=30 (cols 30–39) -- starts wide before pinching
    activate([
        Drop(MAIN_H, MAIN_W,       DROP_ROW, MAIN_COL),
        Drop(MAIN_H, PIECE_START_W, DROP_ROW, PIECE_START_COL),
    ])
    input(f"Split initiated -- {MAIN_H}x{MAIN_W} main at col={MAIN_COL}, {MAIN_H}x{PIECE_START_W} piece at col={PIECE_START_COL}")
    time.sleep(2)

    # ── Step 4: Move piece 25 pixels right, pinching from 10 wide to 5 wide ──
    # Over 25 steps, piece moves col=30 → col=55
    # Width shrinks from 10 → 5 evenly across the 25 steps
    # Each step: width = 10 - round(5 * i/25)  (goes from 10 down to 5)
    #
    # Piece height stays at MAIN_H (10) so it stays aligned with main drop
    # Width reduction per step: 5 width units over 25 steps = 0.2 per step
    # We use round() so it steps down cleanly at even intervals

    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = round(PIECE_START_W - (PIECE_START_W - PIECE_END_W) * i / STRETCH_STEPS)

        activate([
            Drop(MAIN_H, MAIN_W,         DROP_ROW, MAIN_COL),     # main held, never changes
            Drop(MAIN_H, current_width,  DROP_ROW, current_col),   # piece moves and pinches
        ])
        input(
            f"Step {i}/25 -- piece at col={current_col}, "
            f"width={current_width} "
            f"(pinching {PIECE_START_W} → {PIECE_END_W})"
        )

    # Piece is now at col=55, width=5, height=10
    # Neck spans cols 20–54 (35 pixels, the gap + stretch path)
    PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS   # col=55
    NECK_START      = MAIN_COL + MAIN_W                 # col=20 (right edge of main)
    NECK_END        = PIECE_FINAL_COL - 1               # col=54 (left edge of piece)

    time.sleep(2)
    input(
        f"Piece fully moved to col={PIECE_FINAL_COL} at width={PIECE_END_W} -- "
        f"beginning deactivation sweep from col={NECK_END} back to col={NECK_START}"
    )

    # ── Step 5: Deactivate neck column by column ──────────────
    # Sweep from col=54 (closest to piece) back to col=20 (closest to main)
    # Each step the bridge shrinks by 1 column on its right side
    # Bridge height = MAIN_H (10) to fully clear every row in that column
    # This is what actually tells the device to stop powering each column
    #
    # Each iteration the device sees:
    #   main:   10x15 at col=5        -- always fully held
    #   bridge: 10 tall x shrinking   -- right edge steps left each time
    #   piece:  10x5  at col=55       -- always fully held
    #
    # When bridge_width hits 0, the neck is fully gone

    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START  # shrinks from 34 down to 0

        if bridge_width > 0:
            activate([
                Drop(MAIN_H, MAIN_W,        DROP_ROW, MAIN_COL),        # main held
                Drop(MAIN_H, bridge_width,  DROP_ROW, NECK_START),       # neck shrinking, full 10 rows
                Drop(MAIN_H, PIECE_END_W,   DROP_ROW, PIECE_FINAL_COL),  # piece held
            ])
        else:
            # Bridge gone -- both drops fully independent
            activate([
                Drop(MAIN_H, MAIN_W,      DROP_ROW, MAIN_COL),
                Drop(MAIN_H, PIECE_END_W, DROP_ROW, PIECE_FINAL_COL),
            ])

        input(
            f"col={release_col} released (all {MAIN_H} rows) -- "
            f"bridge remaining={bridge_width} cols"
        )

    input("Neck fully deactivated across all rows and columns -- drops independent")
    time.sleep(1)

    # ── Shutdown ──────────────────────────────────────────────
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()

if __name__ == "__main__":
    main()
    
"""The code below is for a multiple drop split off so multiple pieces are splitting at once"""

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
