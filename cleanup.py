"""The goal of this script is to capture all of the drops on a chip and get them to the edge 
for easier clean up. Once again the DLL is proprietary so it can not be shared.
This is to help prevent damage to the device during trials. See Kailey's documentation for more info"""

import ctypes
from ctypes import Structure
import time
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


def activate(drops, debug_label=""):
    n = len(drops)
    arr = (Drop * n)(*drops)
    print(f"\n--- ACTIVATE CALL: {debug_label} ---")
    for idx, d in enumerate(drops):
        print(
            f"    Drop[{idx}]: row={d.row}, col={d.col}, "
            f"height={d.height}, width={d.width} "
            f"| rows {d.row}–{d.row + d.height - 1}, "
            f"cols {d.col}–{d.col + d.width - 1}"
        )
    microfluidics.ActivateElec(CHIP_ROWS, CHIP_COLS, n, arr)
    time.sleep(STEP_DELAY)


# ── Constants ──────────────────────────────────────────────────────────────────

# Chip dimensions
CHIP_ROWS      = 128
CHIP_COLS      = 128

# Starting position (full chip)
START_ROW      = 1
START_COL      = 1
START_HEIGHT   = CHIP_ROWS      # 128
START_WIDTH    = CHIP_COLS      # 128

# Target piece — 30x30 flush in the top-right corner
TARGET_SIZE    = 30
TARGET_ROW     = START_ROW                          # top edge stays fixed at row=1
TARGET_COL     = CHIP_COLS - TARGET_SIZE + 1        # col=99, right edge flush at col=128

# Deactivation: shrink left edge right and bottom edge up, top-right corner fixed
SHRINK_STEPS   = START_HEIGHT - TARGET_SIZE         # 98 steps (128 → 30)

# Timing
STEP_DELAY     = 0.5           # seconds between each activate call

# Voltage settings
VOLT_1         = 45
VOLT_2         = 45
VOLT_3         = 45
VOLT_4         = 0
VOLT_5         = 0
VOLT_6         = 0
VOLT_7         = 0
VOLT_8         = 0
VOLT_9         = 0


def main():
    microfluidics.InitUSB()
    res = microfluidics.OpenUSB()
    if res:
        input("Open successfully")
    else:
        input("Open failed")

    microfluidics.SetPower(True)
    input("Power on completed")

    microfluidics.SetVolt(VOLT_1, VOLT_2, VOLT_3, VOLT_4, VOLT_5, VOLT_6, VOLT_7, VOLT_8, VOLT_9)
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

    # ── Step 1: Activate entire chip ──────────────────────────────────────────
    activate(
        [Drop(START_HEIGHT, START_WIDTH, START_ROW, START_COL)],
        debug_label="FULL CHIP ACTIVATION"
    )
    input(f"\n>>> Full chip activated ({CHIP_ROWS}x{CHIP_COLS}) -- press Enter to begin deactivation")

    # ── Step 2: Deactivate left and bottom edges toward top-right corner ──────
    # Top edge fixed at row=1, right edge fixed at col=128.
    # Each step: left edge moves right by 1, bottom edge moves up by 1.
    print(f"\nDeactivating toward top-right corner over {SHRINK_STEPS} steps...")
    for i in range(1, SHRINK_STEPS + 1):
        current_width  = START_WIDTH  - i      # 127 → 30
        current_height = START_HEIGHT - i      # 127 → 30
        current_col    = START_COL    + i      # 2   → 99

        activate(
            [Drop(current_height, current_width, TARGET_ROW, current_col)],
            debug_label=f"DEACTIVATE step={i}/{SHRINK_STEPS} size={current_height}x{current_width} col={current_col}"
        )
        print(
            f"  step {i}: active area {current_height}x{current_width} "
            f"| rows {TARGET_ROW}–{TARGET_ROW + current_height - 1}, "
            f"cols {current_col}–{current_col + current_width - 1}"
        )

    input(
        f"\n>>> Deactivation complete. {TARGET_SIZE}x{TARGET_SIZE} piece in top-right corner: "
        f"rows {TARGET_ROW}–{TARGET_ROW + TARGET_SIZE - 1}, "
        f"cols {TARGET_COL}–{TARGET_COL + TARGET_SIZE - 1} "
        f"-- press Enter to begin optional further shrinking (or 's' + Enter to skip)"
    )

    # ── Step 3: Optional further shrink (interactive, one step at a time) ────
    # Top-right corner stays fixed as size decreases.
    current_size = TARGET_SIZE
    current_col  = TARGET_COL
    print(f"\nFurther shrink mode: press Enter to shrink by 1, type 's' + Enter to stop.")
    while current_size > 1:
        user_input = input(f"  [{current_size}x{current_size}] Enter to shrink, 's' to stop: ")
        if user_input.strip().lower() == 's':
            break
        current_size -= 1
        current_col   = CHIP_COLS - current_size + 1
        activate(
            [Drop(current_size, current_size, TARGET_ROW, current_col)],
            debug_label=f"FURTHER SHRINK size={current_size}x{current_size} col={current_col}"
        )
        print(f"  {current_size}x{current_size} at row={TARGET_ROW}, col={current_col}–{current_col + current_size - 1}")

    input(f"\n>>> Final size: {current_size}x{current_size} -- press Enter to shut down")

    # ── Shutdown ──────────────────────────────────────────────────────────────
    microfluidics.ActivateElec(CHIP_ROWS, CHIP_COLS, 0, None)
    time.sleep(STEP_DELAY)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()