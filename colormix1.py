"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
from ctypes import POINTER, c_int, c_void_p, Structure
import threading
import time
import pandas as pd
import os

# ── DLL load ──────────────────────────────────────────────────────────────────

os.add_dll_directory(r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows")
microfluidics = ctypes.CDLL(r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll")

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


# ── Constants ─────────────────────────────────────────────────────────────────

MAIN_COL        = 2
MAIN_H          = 10
MAIN_SNAP_H     = 10
MAIN_W          = 15
NECK_START      = MAIN_COL + MAIN_W   # col 17 — right edge of reservoir

# Stretch: only 10 steps (proven working range)
LOAD_W          = 15
STRETCH_TARGET  = 25                  # 10 steps from LOAD_W=15
STRETCH_RANGE   = STRETCH_TARGET - LOAD_W  # 10

# Piece starts just within reach of the 10-step stretch
PIECE_START_COL = 22                  # inside stretched range (cols 2-26)
PIECE_FINAL_COL = 55                  # final resting position

DROP1_ROW   = 55
DROP2_ROW   = 85
DROP3_ROW   = 10
MEETING_ROW = 55
MEETING_COL = 30

# ── CSV loader ────────────────────────────────────────────────────────────────

def load_volumes_from_csv(csv_path):
    df  = pd.read_excel(csv_path)
    row = df.iloc[0]
    h1 = int(row["drop 1 height"])
    w1 = int(row["drop 1 width"])
    h2 = int(row["drop 2 height"])
    w2 = int(row["drop 2 width"])
    h3 = int(row["drop 3 height"])
    w3 = int(row["drop 3 width"])
    print(f"Loaded from CSV: Drop1={h1}x{w1}, Drop2={h2}x{w2}, Drop3={h3}x{w3}")
    return h1, w1, h2, w2, h3, w3


# ── Low-level activation ──────────────────────────────────────────────────────

def _hw_activate(drops: list) -> int:
    n   = len(drops)
    arr = (Drop * n)(*drops)
    return microfluidics.ActivateElec(128, 128, n, arr)


def activate(drops, delay=0.8):
    _hw_activate(drops)
    time.sleep(delay)


def _activate_mix(drops):
    _hw_activate(drops)
    time.sleep(0.3)


# ── Hold thread ───────────────────────────────────────────────────────────────

class _HoldThread:
    def __init__(self):
        self._drops  = []
        self._stop   = threading.Event()
        self._thread = None

    def start(self, drops):
        self._drops = list(drops)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            if self._drops:
                _hw_activate(self._drops)
            time.sleep(0.5)


_hold = _HoldThread()


def _held_pause(drops, prompt):
    _hold.start(drops)
    input(prompt)
    _hold.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────

def held_drops(held_pairs):
    drops = []
    for r, w, h in held_pairs:
        drops.append(Drop(MAIN_SNAP_H, MAIN_W, r, MAIN_COL))
        drops.append(Drop(h, w, r, PIECE_FINAL_COL))
    return drops


# ── Split sequence ────────────────────────────────────────────────────────────

def split_and_move(row, label, held_pairs, piece_w, piece_h):
    """
    Split strategy:
      1. Load reservoir at MAIN_COL
      2. Stretch reservoir rightward 10 steps (proven working range) → width=25
      3. Pattern: reservoir + bridge + piece (piece starts inside stretched drop at col 22)
      4. Walk piece from col 22 → PIECE_FINAL_COL while holding reservoir
    """

    # 1. Load
    res = Drop(MAIN_H, LOAD_W, row, MAIN_COL)
    activate(held_drops(held_pairs) + [res])
    _held_pause(
        held_drops(held_pairs) + [res],
        f"\n>>> {label} — ACTIVE at row={row} ({MAIN_H}x{LOAD_W}) — load drop, press Enter to stretch: "
    )

    # 2. Stretch: 10 steps, 1.5s each — electrode grows from col 2 with drop inside
    print(f"{label} stretching {LOAD_W} -> {STRETCH_TARGET} ({STRETCH_RANGE} steps at 1.5s each)...")
    for i in range(1, STRETCH_RANGE + 1):
        current_w = LOAD_W + i
        ret = _hw_activate(held_drops(held_pairs) + [Drop(MAIN_H, current_w, row, MAIN_COL)])
        time.sleep(1.5)
        print(f"  stretch step {i}/{STRETCH_RANGE} — width={current_w}, ActivateElec={ret}")

    _held_pause(
        held_drops(held_pairs) + [Drop(MAIN_H, STRETCH_TARGET, row, MAIN_COL)],
        f">>> {label} stretched to width={STRETCH_TARGET} — confirm drop extended, press Enter to pattern: "
    )

    # 3. Pattern: activate reservoir + bridge + piece simultaneously
    #    Drop is currently spanning cols 2-26. We pattern into:
    #      - reservoir: col 2, width 15 (cols 2-16)
    #      - bridge:    col 17, width 5 (cols 17-21) — keeps liquid connected
    #      - piece:     col 22, piece size (cols 22-26)
    bridge_w = PIECE_START_COL - NECK_START  # 22-17 = 5
    drops_pattern = held_drops(held_pairs) + [
        Drop(MAIN_SNAP_H, MAIN_W,    row, MAIN_COL),
        Drop(MAIN_H,      bridge_w,  row, NECK_START),
        Drop(piece_h,     piece_w,   row, PIECE_START_COL),
    ]
    activate(drops_pattern)
    _held_pause(
        drops_pattern,
        f">>> {label} patterned — reservoir at col={MAIN_COL}, piece at col={PIECE_START_COL} "
        f"({piece_h}x{piece_w}), bridge={bridge_w}w — confirm liquid connected, press Enter to walk: "
    )

    # 4. Walk piece from PIECE_START_COL to PIECE_FINAL_COL
    #    Piece electrode moves rightward, dragging liquid away from reservoir
    walk_steps = PIECE_FINAL_COL - PIECE_START_COL
    print(f"{label} walking piece col={PIECE_START_COL} -> col={PIECE_FINAL_COL} ({walk_steps} steps)...")
    for i in range(1, walk_steps + 1):
        current_col = PIECE_START_COL + i
        _hw_activate(held_drops(held_pairs) + [
            Drop(MAIN_SNAP_H, MAIN_W,  row, MAIN_COL),
            Drop(piece_h,     piece_w, row, current_col),
        ])
        time.sleep(1.0)
        if i % 5 == 0 or i == walk_steps:
            print(f"  walk step {i}/{walk_steps} — piece at col={current_col}")

    _held_pause(
        held_drops(held_pairs) + [
            Drop(MAIN_SNAP_H, MAIN_W,  row, MAIN_COL),
            Drop(piece_h,     piece_w, row, PIECE_FINAL_COL),
        ],
        f">>> {label} piece at col={PIECE_FINAL_COL} ({piece_h}x{piece_w}) — confirm separated, press Enter to continue: "
    )


# ── Mix ───────────────────────────────────────────────────────────────────────

def mix_drop(all_mains, merge_row, merge_col, merge_h, merge_w):
    H, W = merge_h, merge_w
    r, c = merge_row, merge_col

    print("  Mix pass 1: right 30...")
    for i in range(1, 31):      _activate_mix(all_mains + [Drop(H, W, r, c + i)])
    for i in range(30, -1, -1): _activate_mix(all_mains + [Drop(H, W, r, c + i)])

    print("  Mix pass 2: diagonal up-right 20...")
    for i in range(1, 21):      _activate_mix(all_mains + [Drop(H, W, r - i, c + i)])
    for i in range(20, -1, -1): _activate_mix(all_mains + [Drop(H, W, r - i, c + i)])

    print("  Mix pass 3: right-then-down...")
    for i in range(1, 31):      _activate_mix(all_mains + [Drop(H, W, r, c + i)])
    for i in range(1, 21):      _activate_mix(all_mains + [Drop(H, W, r + i, c + 30)])
    for i in range(20, -1, -1): _activate_mix(all_mains + [Drop(H, W, r + i, c + i)])

    print("  Mix pass 4: right 30 double-pass...")
    for _ in range(2):
        for i in range(1, 31):      _activate_mix(all_mains + [Drop(H, W, r, c + i)])
        for i in range(30, -1, -1): _activate_mix(all_mains + [Drop(H, W, r, c + i)])

    print("  Mix pass 5: diagonal down-right 15...")
    for i in range(1, 16):      _activate_mix(all_mains + [Drop(H, W, r + i, c + i)])
    for i in range(15, -1, -1): _activate_mix(all_mains + [Drop(H, W, r + i, c + i)])

    print("  Mix pass 6: clockwise rectangle 30x20...")
    for i in range(1, 31):      _activate_mix(all_mains + [Drop(H, W, r,      c + i)])
    for i in range(1, 21):      _activate_mix(all_mains + [Drop(H, W, r + i,  c + 30)])
    for i in range(30, -1, -1): _activate_mix(all_mains + [Drop(H, W, r + 20, c + i)])
    for i in range(20, -1, -1): _activate_mix(all_mains + [Drop(H, W, r + i,  c)])

    print("  Mix complete.")


# ── Merge + unload ────────────────────────────────────────────────────────────

def move_pieces_to_meet(h1, w1, h2, w2, h3, w3):
    all_mains = [
        Drop(MAIN_SNAP_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_SNAP_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_SNAP_H, MAIN_W, DROP3_ROW, MAIN_COL),
    ]

    _held_pause(all_mains + [
        Drop(h1, w1, MEETING_ROW, PIECE_FINAL_COL),
        Drop(h2, w2, DROP2_ROW,   PIECE_FINAL_COL),
        Drop(h3, w3, DROP3_ROW,   PIECE_FINAL_COL),
    ], "\n>>> All splits done — press Enter to move to meeting point: ")

    # Phase A: row alignment
    row_steps = max(abs(DROP2_ROW - MEETING_ROW), abs(MEETING_ROW - DROP3_ROW))
    print(f"Phase A — aligning rows to {MEETING_ROW} ({row_steps} steps)...")
    for i in range(1, row_steps + 1):
        piece2_row = max(DROP2_ROW - i, MEETING_ROW)
        piece3_row = min(DROP3_ROW + i, MEETING_ROW)
        activate(all_mains + [
            Drop(h1, w1, MEETING_ROW, PIECE_FINAL_COL),
            Drop(h2, w2, piece2_row,  PIECE_FINAL_COL),
            Drop(h3, w3, piece3_row,  PIECE_FINAL_COL),
        ])

    _held_pause(all_mains + [
        Drop(h1, w1, MEETING_ROW, PIECE_FINAL_COL),
        Drop(h2, w2, MEETING_ROW, PIECE_FINAL_COL),
        Drop(h3, w3, MEETING_ROW, PIECE_FINAL_COL),
    ], f">>> All on row={MEETING_ROW} — press Enter to sweep to meeting col: ")

    # Phase B: column sweep
    merged_w  = w1 + w2 + w3
    merge_h   = h1 + h2 + h3
    col_steps = PIECE_FINAL_COL - MEETING_COL
    print(f"Phase B — sweeping to col={MEETING_COL} ({col_steps} steps)...")
    for i in range(1, col_steps + 1):
        activate(all_mains + [Drop(merge_h, merged_w, MEETING_ROW, PIECE_FINAL_COL - i)])

    _held_pause(all_mains + [Drop(merge_h, merged_w, MEETING_ROW, MEETING_COL)],
                f">>> All at col={MEETING_COL} — press Enter to merge: ")

    merged = all_mains + [Drop(merge_h, merged_w, MEETING_ROW, MEETING_COL)]
    activate(merged)
    _held_pause(merged, f">>> Merged {merge_h}x{merged_w} — confirm visible, press Enter to mix: ")

    print(f"\nMixing at row={MEETING_ROW}, col={MEETING_COL}...")
    mix_drop(all_mains, MEETING_ROW, MEETING_COL, merge_h, merged_w)
    _held_pause(merged, ">>> Mix complete — press Enter to unload: ")

    # Unload: col 30 → 128
    print("Unloading — moving to (55, 100)...")
    for c in range(MEETING_COL + 1, 101):
        activate(all_mains + [Drop(merge_h, merged_w, 55, c)])

    _held_pause(all_mains + [Drop(merge_h, merged_w, 55, 100)],
                ">>> Drop at (55, 100) — confirm, press Enter to finish unload: ")

    print("Moving to unload position (55, 128)...")
    for c in range(101, 129):
        activate(all_mains + [Drop(merge_h, merged_w, 55, c)])

    activate(all_mains)
    _held_pause(all_mains, ">>> Drop unloaded at (55, 128) — reservoirs holding, press Enter to finish: ")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ret = microfluidics.InitUSB()
    print(f"InitUSB returned: {ret}")
    if microfluidics.OpenUSB():
        input("USB open — press Enter: ")
    else:
        input("USB FAILED — press Enter to abort: ")
        return

    microfluidics.SetPower(True)
    input("Power on — press Enter to set voltage: ")
    ret = microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
    print(f"SetVolt returned: {ret}")
    time.sleep(1)

    voltages = [ctypes.c_int(0) for _ in range(9)]
    microfluidics.InquireVolt(*[ctypes.byref(v) for v in voltages])
    print("Voltages: " + " ".join(str(v.value) for v in voltages))
    input("Voltage confirmed — press Enter to begin: ")

    h1, w1, h2, w2, h3, w3 = load_volumes_from_csv(r"C:\Users\klmcg\OneDrive\Documents\colormixcsv.xlsx")

    split_and_move(row=DROP1_ROW, label="Drop 1", held_pairs=[], piece_w=w1, piece_h=h1)
    input(">>> Drop 1 holding — press Enter for Drop 2: ")

    split_and_move(row=DROP2_ROW, label="Drop 2", held_pairs=[(DROP1_ROW, w1, h1)], piece_w=w2, piece_h=h2)
    input(">>> Drop 2 holding — press Enter for Drop 3: ")

    split_and_move(row=DROP3_ROW, label="Drop 3", held_pairs=[(DROP1_ROW, w1, h1), (DROP2_ROW, w2, h2)], piece_w=w3, piece_h=h3)

    move_pieces_to_meet(h1, w1, h2, w2, h3, w3)

    input(">>> Sequence complete — press Enter to shut down: ")
    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off — press Enter to close USB: ")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()
