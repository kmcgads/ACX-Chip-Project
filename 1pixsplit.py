"""
dispensefive.py
───────────────────────────────────────────────────────────────────────────────
Two-split sequence ending with a 5×3 piece at a destination on the chip.

SPLIT 1 — reservoir (10×15) → 10×3 piece  (horizontal, mirrors dropsplitoff.py)
  Step 1: Load drop 10×20 (wider than reservoir).
  Step 2: Stretch 20 → 35 wide, one column at a time (15 steps).
  Step 3: Pattern — reservoir 10×15 + piece 10×10 in one call.
  Step 4: Move piece 25 cols right, width pinching 10 → 3. No neck loop.
  Result: 10×3 piece at col=52.

SPLIT 2 — 10×3 piece → 5×3 piece  (vertical, same logic rotated 90°)
  Step 5: Stretch 10×3 piece downward to 15×3, one row at a time (5 steps).
  Step 6: Pattern — top 5×3 + bottom 5×3 in one call (5-row gap between them).
  Step 7: Move bottom piece 10 rows down. No neck loop.
  Result: 5×3 piece at row=75.

Step 8: Walk 5×3 piece to DEST_ROW, DEST_COL.

Piece height must match the drop being split for the split to succeed —
this is why split 1 keeps piece height at MAIN_H=10 and split 2 keeps
both halves at HALF_H=5.

The original code for this chip was written in C++ by ACX Instruments and
later adapted for Python using ctypes. ACX provides the required starter
software and DLL files with the purchased device. Because the DLL is
proprietary company software, the placeholder below represents where the
ACX-provided DLL would be loaded.
"""

import ctypes
import time
from ctypes import Structure
import os

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


def activate(drops, label=""):
    """Send one electrode activation call and print what was sent."""
    n   = len(drops)
    arr = (Drop * n)(*drops)
    if label:
        print(f"\n--- {label} ---")
    for idx, d in enumerate(drops):
        print(
            f"  Drop[{idx}]: row={d.row}  col={d.col}  "
            f"h={d.height}  w={d.width}  "
            f"| rows {d.row}–{d.row + d.height - 1}, "
            f"cols {d.col}–{d.col + d.width - 1}"
        )
    microfluidics.ActivateElec(128, 128, n, arr)
    time.sleep(0.5)


def move_drop(h, w, r0, c0, r1, c1, held, label=""):
    """Walk a drop one electrode step at a time; held drops stay active."""
    r, c  = r0, c0
    steps = 0
    while r != r1 or c != c1:
        if   r < r1: r += 1
        elif r > r1: r -= 1
        if   c < c1: c += 1
        elif c > c1: c -= 1
        steps += 1
        activate(
            held + [Drop(h, w, r, c)],
            label=f"{label} step {steps} → ({r},{c})" if label else ""
        )


# ── Split 1 constants — mirrors dropsplitoff.py ───────────────────────────────

DROP_ROW        = 55    # top edge of reservoir
MAIN_COL        = 2     # left edge of reservoir
MAIN_H          = 10    # reservoir height
MAIN_W          = 15    # reservoir width  (cols 2–16)

# Piece height MUST equal MAIN_H for the split to work
PIECE_START_W   = 10    # piece starts 10 wide (same as dropsplitoff.py)
PIECE_END_W     = 3     # piece narrows to 3 wide
STRETCH_STEPS   = 25    # piece travels 25 cols while pinching

# Gap cols 17–26 between reservoir right edge (col 16) and piece (col 27)
PIECE_START_COL = MAIN_COL + MAIN_W + 10   # col 27
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col 52

# ── Split 2 constants — vertical split of the 10×3 piece ─────────────────────

HALF_H          = MAIN_H // 2   # 5 — height of each half after split
S2_STRETCH      = 5             # stretch piece down 5 extra rows (10 → 15 tall)
S2_GAP          = 5             # row gap between top and bottom halves
S2_MOVE_STEPS   = 10            # rows the bottom piece travels downward

# After stretch, piece is 15×3 (rows 55–69).
# Pattern splits it at the midpoint: top rows 55–59, bottom rows 65–69.
S2_TOP_ROW      = DROP_ROW                            # row 55
S2_BOT_ROW      = DROP_ROW + HALF_H + S2_GAP         # row 65  (gap rows 60–64)
S2_BOT_FINAL    = S2_BOT_ROW + S2_MOVE_STEPS          # row 75

# ── Destination ───────────────────────────────────────────────────────────────

DEST_ROW = 30    # where the final 5×3 piece is walked to
DEST_COL = 80


# ── Main ──────────────────────────────────────────────────────────────────────

def main():

    # ── Startup ───────────────────────────────────────────────────────────────
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

    v1 = ctypes.c_int(1);  v2 = ctypes.c_int(2);  v3 = ctypes.c_int(3)
    v4 = ctypes.c_int(4);  v5 = ctypes.c_int(5);  v6 = ctypes.c_int(6)
    v7 = ctypes.c_int(7);  v8 = ctypes.c_int(8);  v9 = ctypes.c_int(9)
    microfluidics.InquireVolt(
        ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
        ctypes.byref(v4), ctypes.byref(v5), ctypes.byref(v6),
        ctypes.byref(v7), ctypes.byref(v8), ctypes.byref(v9)
    )
    print(f"Voltages: {v1.value} {v2.value} {v3.value} {v4.value} "
          f"{v5.value} {v6.value} {v7.value} {v8.value} {v9.value}")
    input("Voltage query completed")

    RESERVOIR = Drop(MAIN_H, MAIN_W, DROP_ROW, MAIN_COL)

    # ══════════════════════════════════════════════════════════════════════════
    # SPLIT 1 — reservoir (10×15) → 10×3 piece
    # ══════════════════════════════════════════════════════════════════════════

    # ── Step 1: load drop 10×20 ───────────────────────────────────────────────
    activate(
        [Drop(MAIN_H, 20, DROP_ROW, MAIN_COL)],
        label="STEP 1 — load 10×20"
    )
    input(f"Drop loaded — 10×20 at row={DROP_ROW} col={MAIN_COL}")
    time.sleep(2)

    # ── Step 2: stretch 20 → 35 wide, one column at a time ───────────────────
    for i in range(1, 16):
        activate(
            [Drop(MAIN_H, 20 + i, DROP_ROW, MAIN_COL)],
            label=f"STEP 2 — stretch width={20 + i}"
        )
        input(f"Stretching width={20 + i}")
    time.sleep(2)

    # ── Step 3: pattern reservoir + 10×10 piece ───────────────────────────────
    activate(
        [
            Drop(MAIN_H, MAIN_W,       DROP_ROW, MAIN_COL),
            Drop(MAIN_H, PIECE_START_W, DROP_ROW, PIECE_START_COL),
        ],
        label=f"STEP 3 — pattern: reservoir col={MAIN_COL}, piece col={PIECE_START_COL}"
    )
    input(f"Split 1 patterned — reservoir col={MAIN_COL}, piece col={PIECE_START_COL}")
    time.sleep(2)

    # ── Step 4: move piece right, width pinching 10 → 3 ──────────────────────
    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = round(
            PIECE_START_W - (PIECE_START_W - PIECE_END_W) * i / STRETCH_STEPS
        )
        activate(
            [
                Drop(MAIN_H, MAIN_W,        DROP_ROW, MAIN_COL),
                Drop(MAIN_H, current_width, DROP_ROW, current_col),
            ],
            label=f"STEP 4 — step {i}/{STRETCH_STEPS}  col={current_col}  width={current_width}"
        )
        input(f"Step {i}/{STRETCH_STEPS} — piece col={current_col}, width={current_width}")
    time.sleep(2)

    input(f"Split 1 complete — 10×3 piece at col={PIECE_FINAL_COL}")

    # ══════════════════════════════════════════════════════════════════════════
    # SPLIT 2 — 10×3 piece → 5×3 piece  (vertical)
    # ══════════════════════════════════════════════════════════════════════════

    # ── Step 5: stretch piece downward, one row at a time ────────────────────
    for i in range(1, S2_STRETCH + 1):
        activate(
            [
                RESERVOIR,
                Drop(MAIN_H + i, PIECE_END_W, DROP_ROW, PIECE_FINAL_COL),
            ],
            label=f"STEP 5 — stretch height={MAIN_H + i}"
        )
        input(f"Stretching piece height={MAIN_H + i}")
    time.sleep(2)

    # Piece is now 15×3 (rows 55–69)

    # ── Step 6: pattern — top 5×3 + bottom 5×3 (gap rows 60–64) ─────────────
    activate(
        [
            RESERVOIR,
            Drop(HALF_H, PIECE_END_W, S2_TOP_ROW, PIECE_FINAL_COL),
            Drop(HALF_H, PIECE_END_W, S2_BOT_ROW, PIECE_FINAL_COL),
        ],
        label=(
            f"STEP 6 — pattern: top 5×3 row={S2_TOP_ROW}, "
            f"bottom 5×3 row={S2_BOT_ROW}  (gap rows {S2_TOP_ROW + HALF_H}–{S2_BOT_ROW - 1})"
        )
    )
    input(
        f"Split 2 patterned — top at row={S2_TOP_ROW}, "
        f"bottom at row={S2_BOT_ROW}. Press Enter to move bottom piece"
    )
    time.sleep(2)

    # ── Step 7: move bottom piece downward ───────────────────────────────────
    for i in range(1, S2_MOVE_STEPS + 1):
        current_row = S2_BOT_ROW + i
        activate(
            [
                RESERVOIR,
                Drop(HALF_H, PIECE_END_W, S2_TOP_ROW,   PIECE_FINAL_COL),  # top held
                Drop(HALF_H, PIECE_END_W, current_row,  PIECE_FINAL_COL),  # bottom moves
            ],
            label=f"STEP 7 — step {i}/{S2_MOVE_STEPS}  bottom row={current_row}"
        )
        input(f"Step {i}/{S2_MOVE_STEPS} — bottom piece row={current_row}")
    time.sleep(2)

    input(f"Split 2 complete — 5×3 piece at row={S2_BOT_FINAL}, col={PIECE_FINAL_COL}")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 8 — walk 5×3 piece to destination
    # ══════════════════════════════════════════════════════════════════════════

    move_drop(
        HALF_H, PIECE_END_W,
        S2_BOT_FINAL, PIECE_FINAL_COL,
        DEST_ROW, DEST_COL,
        [RESERVOIR, Drop(HALF_H, PIECE_END_W, S2_TOP_ROW, PIECE_FINAL_COL)],
        label="STEP 8 — WALK"
    )

    activate(
        [
            RESERVOIR,
            Drop(HALF_H, PIECE_END_W, S2_TOP_ROW, PIECE_FINAL_COL),
            Drop(HALF_H, PIECE_END_W, DEST_ROW,   DEST_COL),
        ],
        label=f"PLACED at ({DEST_ROW},{DEST_COL})"
    )
    input(f"5×3 piece placed at row={DEST_ROW}, col={DEST_COL}. Press Enter to power off")

    # ── Shutdown ──────────────────────────────────────────────────────────────
    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()