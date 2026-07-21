"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
from ctypes import Structure
import time
import os
import openpyxl

# ── DLL load ──────────────────────────────────────────────────────────────────

os.add_dll_directory(r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows")
microfluidics = ctypes.CDLL(r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll")


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]


# helps call and activate electrodes using the set voltage and keeps track of drops
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


# ── CSV loader ────────────────────────────────────────────────────────────────

CSV_PATH = r"C:\Users\klmcg\OneDrive\Documents\colormixcsv.xlsx"

def load_piece_widths(filepath=CSV_PATH):
    """
    Read piece end-widths from row 2 of the Excel file.
    Expected columns: piece_1 width | piece_2 width | piece_3 width
    Height is always 10 (MAIN_H) and is NOT read from the file.
    Returns (piece1_end_w, piece2_end_w, piece3_end_w) as ints.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    piece1_end_w = int(ws.cell(row=2, column=1).value)
    piece2_end_w = int(ws.cell(row=2, column=2).value)
    piece3_end_w = int(ws.cell(row=2, column=3).value)
    print(f"\n[CSV] Loaded piece end-widths from: {filepath}")
    print(f"      piece_1 width={piece1_end_w}, piece_2 width={piece2_end_w}, piece_3 width={piece3_end_w}")
    print(f"      Height is fixed at {MAIN_H} for all drops.\n")
    return piece1_end_w, piece2_end_w, piece3_end_w


# ── Constants ─────────────────────────────────────────────────────────────────

MAIN_H          = 10
MAIN_W          = 15

# Drop 1 and 2 — main body starts at col=5
MAIN_COL        = 2
DROP1_ROW       = 65
DROP2_ROW       = 115
DROP3_ROW       = 10

# Piece geometry (shared by all three drops)
PIECE_START_COL = 30
PIECE_START_W   = 10
STRETCH_STEPS   = 25
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55

# Meeting point — all three pieces converge here
MEETING_ROW     = DROP1_ROW                        # row=55
MEETING_COL     = PIECE_FINAL_COL                  # col=55


# ── Helpers ───────────────────────────────────────────────────────────────────

def held_drops(held_items):
    """
    held_items: list of (row, main_col, piece_end_w) tuples for each drop currently held.
    Returns a list of Drop objects — one main body and one piece per held drop.
    piece_end_w controls the final pinched width of each held piece (read from CSV).
    """
    drops = []
    for r, mc, piece_end_w in held_items:
        drops.append(Drop(MAIN_H, MAIN_W,        r, mc))
        drops.append(Drop(MAIN_H, piece_end_w,   r, PIECE_FINAL_COL))
    return drops


def split_and_move(row, label, held_items, piece_end_w, start_col=None):
    """
    Loads, stretches, splits, and moves a drop's piece to PIECE_FINAL_COL.

    row          — the row this drop lives on
    label        — human-readable name for debug output
    held_items   — list of (row, col, piece_end_w) tuples for drops that must stay active
    piece_end_w  — final pinched width of this drop's piece (read from CSV; height always=MAIN_H)
    start_col    — left edge of this drop's main body (defaults to MAIN_COL)
    """
    if start_col is None:
        start_col = MAIN_COL

    neck_start = start_col + MAIN_W          # col where main body ends
    neck_gap   = PIECE_START_COL - neck_start  # pixels between main and piece

    # ── Step 1: Load initial drop ─────────────────────────────────────────────
    activate(
        held_drops(held_items) + [Drop(MAIN_H, 20, row, start_col)],
        debug_label=f"{label} LOAD"
    )
    input(f"\n>>> {label} loaded at row={row}, col={start_col} -- press Enter to begin stretch")

    # ── Step 2: Stretch ───────────────────────────────────────────────────────
    print(f"{label} stretching from width=20 to width=35...")
    time.sleep(2)
    for i in range(1, 16):
        activate(
            held_drops(held_items) + [Drop(MAIN_H, 20 + i, row, start_col)],
            debug_label=f"{label} STRETCH width={20 + i}"
        )
    input(f">>> {label} fully stretched to width=35 -- press Enter to split")

    # ── Step 3: Gradually open split gap 1 pixel at a time ───────────────────
    time.sleep(2)
    print(f"{label} opening split gap ({neck_gap} steps)...")
    for gap in range(neck_gap + 1):
        bridge_col   = neck_start + gap
        bridge_width = neck_gap   - gap
        if bridge_width > 0:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,        row, start_col),
                    Drop(MAIN_H, bridge_width,  row, bridge_col),
                    Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
                ],
                debug_label=f"{label} SPLIT gap={gap} bridge_col={bridge_col} bridge_w={bridge_width}"
            )
        else:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,        row, start_col),
                    Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
                ],
                debug_label=f"{label} SPLIT gap fully open"
            )
        print(f"  gap={gap} px open, bridge_width={bridge_width}")
    input(f">>> {label} split complete -- press Enter to move piece")

    # ── Step 4: Move piece ────────────────────────────────────────────────────
    # Pinches from PIECE_START_W down to piece_end_w (from CSV) as it moves right
    print(f"{label} moving piece {STRETCH_STEPS}px right, pinching {PIECE_START_W} → {piece_end_w} wide...")
    time.sleep(2)
    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = round(PIECE_START_W - (PIECE_START_W - piece_end_w) * i / STRETCH_STEPS)
        activate(
            held_drops(held_items) + [
                Drop(MAIN_H, MAIN_W,         row, start_col),
                Drop(MAIN_H, current_width,  row, current_col),
            ],
            debug_label=f"{label} MOVE step={i} col={current_col} width={current_width}"
        )
        print(f"  {label} piece at col={current_col}, width={current_width}")
    input(f">>> {label} piece at col={PIECE_FINAL_COL} width={piece_end_w} -- press Enter to begin neck deactivation")

    # ── Step 5: Deactivate neck ───────────────────────────────────────────────
    neck_end = PIECE_FINAL_COL - 1   # col=54
    print(f"{label} deactivating neck from col={neck_end} to col={neck_start}...")
    time.sleep(2)
    for release_col in range(neck_end, neck_start - 1, -1):
        bridge_width = release_col - neck_start

        if bridge_width > 0:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,        row, start_col),
                    Drop(MAIN_H, bridge_width,  row, neck_start),
                    Drop(MAIN_H, piece_end_w,   row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} DEACTIVATE col={release_col} bridge={bridge_width}"
            )
        else:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,       row, start_col),
                    Drop(MAIN_H, piece_end_w,  row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} DEACTIVATE col={release_col} FINAL"
            )
        print(f"  {label} col={release_col} released, bridge remaining={bridge_width}")

    input(f">>> {label} fully split -- press Enter to continue")
    time.sleep(1)


def move_pieces_to_meet(piece1_end_w, piece2_end_w, piece3_end_w):
    """
    Moves all three pieces simultaneously toward MEETING_ROW=55, MEETING_COL=55.
    Drop 1's piece is already at row=55 and stays put.
    Drop 2's piece travels up 50 rows (105 → 55).
    Drop 3's piece travels down 50 rows (5 → 55).

    piece1_end_w / piece2_end_w / piece3_end_w — per-drop widths from CSV.
    """
    steps_to_meet = DROP2_ROW - MEETING_ROW   # 50 steps

    input(f"\n>>> All three drops split -- press Enter to move pieces to meet at row={MEETING_ROW}, col={MEETING_COL}")

    print(f"Moving pieces toward row={MEETING_ROW}...")
    time.sleep(1)
    for i in range(1, steps_to_meet + 1):
        piece2_row = DROP2_ROW - i    # 104 → 55 (moving up)
        piece3_row = DROP3_ROW + i    # 6   → 55 (moving down)
        activate(
            [
                # Main bodies — all three held in place
                Drop(MAIN_H, MAIN_W,       DROP1_ROW, MAIN_COL),
                Drop(MAIN_H, MAIN_W,       DROP2_ROW, MAIN_COL),
                Drop(MAIN_H, MAIN_W,       DROP3_ROW, MAIN_COL),
                # Pieces — each uses its own CSV-loaded end width
                Drop(MAIN_H, piece1_end_w, MEETING_ROW, MEETING_COL),   # Drop 1: already at meeting row
                Drop(MAIN_H, piece2_end_w, piece2_row,  MEETING_COL),   # Drop 2: moving up
                Drop(MAIN_H, piece3_end_w, piece3_row,  MEETING_COL),   # Drop 3: moving down
            ],
            debug_label=f"MOVE TO MEET step={i} piece2={piece2_row} piece3={piece3_row}"
        )
        print(f"  piece1=row{MEETING_ROW} (held), piece2=row{piece2_row}, piece3=row{piece3_row}")

    input(f">>> All pieces at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to merge")

    # Merge — all three pieces now overlap at MEETING_ROW, MEETING_COL
    # Use piece1's end width to represent the merged drop
    time.sleep(1)
    activate(
        [
            Drop(MAIN_H, MAIN_W,       DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W,       DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W,       DROP3_ROW,   MAIN_COL),
            Drop(MAIN_H, piece1_end_w, MEETING_ROW, MEETING_COL),
        ],
        debug_label="MERGE all three pieces at meeting point"
    )
    input(f">>> Three pieces merged at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to begin mixing")

    # ── Mixing sequence ───────────────────────────────────────────────────────
    all_mains = [
        Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP3_ROW, MAIN_COL),
    ]
    H, W = MAIN_H, MAIN_W
    r, c = MEETING_ROW, MEETING_COL

    print("\nRunning mixing sequence...")

    print("  Mix pass 1: right 30...")
    for i in range(1, 31):      activate(all_mains + [Drop(H, W, r, c + i)],      debug_label=f"MIX pass1 right i={i}")
    for i in range(30, -1, -1): activate(all_mains + [Drop(H, W, r, c + i)],      debug_label=f"MIX pass1 back i={i}")

    print("  Mix pass 2: diagonal up-right 20...")
    for i in range(1, 21):      activate(all_mains + [Drop(H, W, r - i, c + i)],  debug_label=f"MIX pass2 out i={i}")
    for i in range(20, -1, -1): activate(all_mains + [Drop(H, W, r - i, c + i)],  debug_label=f"MIX pass2 back i={i}")

    print("  Mix pass 3: right-then-down...")
    for i in range(1, 31):      activate(all_mains + [Drop(H, W, r, c + i)],      debug_label=f"MIX pass3 right i={i}")
    for i in range(1, 21):      activate(all_mains + [Drop(H, W, r + i, c + 30)], debug_label=f"MIX pass3 down i={i}")
    for i in range(20, -1, -1): activate(all_mains + [Drop(H, W, r + i, c + i)],  debug_label=f"MIX pass3 back i={i}")

    print("  Mix pass 4: right 30 double-pass...")
    for _ in range(2):
        for i in range(1, 31):      activate(all_mains + [Drop(H, W, r, c + i)],  debug_label=f"MIX pass4 right i={i}")
        for i in range(30, -1, -1): activate(all_mains + [Drop(H, W, r, c + i)],  debug_label=f"MIX pass4 back i={i}")

    print("  Mix pass 5: diagonal down-right 15...")
    for i in range(1, 16):      activate(all_mains + [Drop(H, W, r + i, c + i)],  debug_label=f"MIX pass5 out i={i}")
    for i in range(15, -1, -1): activate(all_mains + [Drop(H, W, r + i, c + i)],  debug_label=f"MIX pass5 back i={i}")

    print("  Mix pass 6: clockwise rectangle 30x20...")
    for i in range(1, 31):      activate(all_mains + [Drop(H, W, r,      c + i)], debug_label=f"MIX pass6 top i={i}")
    for i in range(1, 21):      activate(all_mains + [Drop(H, W, r + i,  c + 30)],debug_label=f"MIX pass6 right i={i}")
    for i in range(30, -1, -1): activate(all_mains + [Drop(H, W, r + 20, c + i)], debug_label=f"MIX pass6 bottom i={i}")
    for i in range(20, -1, -1): activate(all_mains + [Drop(H, W, r + i,  c)],     debug_label=f"MIX pass6 left i={i}")

    print("  Mix complete.")
    input(f">>> Mix complete -- press Enter to unload")


# def move_piece_out(merged_w):
#     """
#     Moves the merged drop from MEETING_COL (col=55) to the chip edge (col=128).
#     All three main bodies remain held during this step.

#     merged_w — width to use for the merged drop (piece1_end_w from CSV).
#     """
#     print(f"\nMoving merged drop from col={MEETING_COL} to col=128...")
#     for col in range(MEETING_COL, 128):
#         activate(
#             [
#                 Drop(MAIN_H, MAIN_W,    DROP1_ROW,   MAIN_COL),
#                 Drop(MAIN_H, MAIN_W,    DROP2_ROW,   MAIN_COL),
#                 Drop(MAIN_H, MAIN_W,    DROP3_ROW,   MAIN_COL),
#                 Drop(MAIN_H, merged_w,  MEETING_ROW, col),
#             ],
#             debug_label=f"MOVE OUT piece at col={col}"
#         )
#         print(f"  piece at col={col}")


def main():
    # ── Load piece widths from CSV ─────────────────────────────────────────────
    # Reads colormixcsv.xlsx row 2: piece_1 width | piece_2 width | piece_3 width
    # Height is always MAIN_H=10 and is not read from the file.
    piece1_end_w, piece2_end_w, piece3_end_w = load_piece_widths()

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

    # ── Drop 1: row=55, col=2 ─────────────────────────────────────────────────
    split_and_move(
        row=DROP1_ROW,
        label="Drop 1 (row=55)",
        held_items=[],
        piece_end_w=piece1_end_w,
        start_col=MAIN_COL
    )
    input(">>> Drop 1 holding -- press Enter to start Drop 2")

    # ── Drop 2: row=105, col=2 ────────────────────────────────────────────────
    split_and_move(
        row=DROP2_ROW,
        label="Drop 2 (row=105)",
        held_items=[(DROP1_ROW, MAIN_COL, piece1_end_w)],
        piece_end_w=piece2_end_w,
        start_col=MAIN_COL
    )
    input(">>> Drop 2 holding -- press Enter to start Drop 3")

    # ── Drop 3: row=10, col=2 ─────────────────────────────────────────────────
    split_and_move(
        row=DROP3_ROW,
        label="Drop 3 (row=10)",
        held_items=[(DROP1_ROW, MAIN_COL, piece1_end_w), (DROP2_ROW, MAIN_COL, piece2_end_w)],
        piece_end_w=piece3_end_w,
        start_col=MAIN_COL
    )

    # ── Move all three pieces to meet, merge, and mix ─────────────────────────
    move_pieces_to_meet(piece1_end_w, piece2_end_w, piece3_end_w)

    # # ── Move merged drop to chip edge (uses piece1's width for merged drop) ───
    # move_piece_out(merged_w=piece1_end_w)
    # input(">>> Drop unloaded at chip edge — press Enter to finish")

    input(">>> Sequence complete -- press Enter to shut down")

    # # ── Shutdown ──────────────────────────────────────────────────────────────
    # microfluidics.ActivateElec(128, 128, 0, None)
    # time.sleep(0.5)
    # microfluidics.SetPower(False)
    # input("Power off completed")
    # microfluidics.CloseUSB()


if __name__ == "__main__":
    main()