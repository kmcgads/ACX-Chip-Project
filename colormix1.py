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
MAIN_H          = 20
MAIN_SNAP_H     = 15
MAIN_W          = 15
PIECE_START_COL = 30
PIECE_START_W   = 15
STRETCH_STEPS   = 25
NECK_START      = MAIN_COL + MAIN_W
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS
NECK_END        = PIECE_FINAL_COL - 1
LOAD_W          = 20
STRETCH_TARGET  = 45
STRETCH_RANGE   = STRETCH_TARGET - LOAD_W

DROP1_ROW   = 55
DROP2_ROW   = 85
DROP3_ROW   = 10
MEETING_ROW = 55
MEETING_COL = 30

_dll_lock = threading.Lock()


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
    print(f"Loaded from CSV: Drop1={h1}×{w1}, Drop2={h2}×{w2}, Drop3={h3}×{w3}")
    return h1, w1, h2, w2, h3, w3


# ── Low-level activation ──────────────────────────────────────────────────────

def _hw_activate(drops: list) -> None:
    n   = len(drops)
    arr = (Drop * n)(*drops)
    with _dll_lock:
        microfluidics.ActivateElec(128, 128, n, arr)


def activate(drops):
    _hw_activate(drops)
    time.sleep(0.5)


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
        drops.append(Drop(h,           w,      r, PIECE_FINAL_COL))
    return drops


# ── Split sequence ────────────────────────────────────────────────────────────

def load_and_hold_drop(row, label, held_pairs, piece_w, piece_h):
    drops = held_drops(held_pairs) + [Drop(MAIN_H, LOAD_W, row, MAIN_COL)]
    activate(drops)
    _held_pause(drops,
                f"\n>>> {label} — electrode ACTIVE at row={row}, col={MAIN_COL} "
                f"({MAIN_H}×{LOAD_W}) — load 20×20 drop, then press Enter to stretch: ")


def split_and_move(row, label, held_pairs, piece_w, piece_h):

    # 1. Load
    load_and_hold_drop(row, label, held_pairs, piece_w, piece_h)

    # 2. Stretch
    print(f"{label} stretching {LOAD_W} → {STRETCH_TARGET}...")
    for i in range(1, STRETCH_RANGE + 1):
        activate(held_drops(held_pairs) + [Drop(MAIN_H, LOAD_W + i, row, MAIN_COL)])

    _held_pause(held_drops(held_pairs) + [Drop(MAIN_H, STRETCH_TARGET, row, MAIN_COL)],
                f">>> {label} stretched to {STRETCH_TARGET} — confirm neck, press Enter to pattern: ")

    # 3. Pattern
    drops_pattern = held_drops(held_pairs) + [
        Drop(MAIN_SNAP_H, MAIN_W,        row, MAIN_COL),
        Drop(piece_h,     PIECE_START_W, row, PIECE_START_COL),
    ]
    activate(drops_pattern)
    _held_pause(drops_pattern,
                f">>> {label} patterned — main=15×15, piece={piece_h}×{PIECE_START_W}, press Enter to move: ")

    # 4. Move + pinch
    print(f"{label} moving piece, pinching {PIECE_START_W} → {piece_w}...")
    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = max(piece_w, round(
            PIECE_START_W - (PIECE_START_W - piece_w) * i / STRETCH_STEPS
        ))
        activate(held_drops(held_pairs) + [
            Drop(MAIN_SNAP_H, MAIN_W,        row, MAIN_COL),
            Drop(piece_h,     current_width, row, current_col),
        ])

    _held_pause(held_drops(held_pairs) + [
        Drop(MAIN_SNAP_H, MAIN_W,  row, MAIN_COL),
        Drop(piece_h,     piece_w, row, PIECE_FINAL_COL),
    ], f">>> {label} piece at col={PIECE_FINAL_COL} — confirm {piece_h}×{piece_w} piece separated, press Enter for neck: ")

    # 5. Neck deactivation
    print(f"{label} deactivating neck {NECK_END} → {NECK_START}...")
    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START
        neck_drops = held_drops(held_pairs) + [Drop(MAIN_SNAP_H, MAIN_W, row, MAIN_COL)]
        if bridge_width > 0:
            neck_drops += [Drop(MAIN_SNAP_H, bridge_width, row, NECK_START)]
        neck_drops += [Drop(piece_h, piece_w, row, PIECE_FINAL_COL)]
        activate(neck_drops)

    _held_pause(held_drops(held_pairs) + [
        Drop(MAIN_SNAP_H, MAIN_W,  row, MAIN_COL),
        Drop(piece_h,     piece_w, row, PIECE_FINAL_COL),
    ], f">>> {label} fully split — reservoir=15×15, piece={piece_h}×{piece_w}, press Enter to continue: ")


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

    print("  Mix pass 6: clockwise rectangle 30×20...")
    for i in range(1, 31):      _activate_mix(all_mains + [Drop(H, W, r,      c + i)])
    for i in range(1, 21):      _activate_mix(all_mains + [Drop(H, W, r + i,  c + 30)])
    for i in range(30, -1, -1): _activate_mix(all_mains + [Drop(H, W, r + 20, c + i)])
    for i in range(20, -1, -1): _activate_mix(all_mains + [Drop(H, W, r + i,  c)])

    print("  Mix complete.")


# ── Merge sequence ────────────────────────────────────────────────────────────

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
    ], f"\n>>> All splits done — press Enter to move to meeting point: ")

    # Phase A: row alignment
    row_steps = max(abs(DROP2_ROW - MEETING_ROW), abs(MEETING_ROW - DROP3_ROW))
    print(f"Phase A — aligning to row={MEETING_ROW} ({row_steps} steps)...")
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
    ], f">>> All on row={MEETING_ROW} — press Enter to sweep left: ")

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
    _held_pause(merged, f">>> Merged {merge_h}×{merged_w} — confirm visible, press Enter to mix: ")

    print(f"\nMixing at row={MEETING_ROW}, col={MEETING_COL}...")
    mix_drop(all_mains, MEETING_ROW, MEETING_COL, merge_h, merged_w)
    _held_pause(merged, f">>> Mix complete — press Enter to unload: ")

    # ── Unload sequence ───────────────────────────────────────────────────────

    # Move merged drop col 30 → 100 (row stays at 55)
    print("Unloading — moving to (55, 100)...")
    for c in range(MEETING_COL + 1, 101):
        activate(all_mains + [Drop(merge_h, merged_w, 55, c)])

    _held_pause(all_mains + [Drop(merge_h, merged_w, 55, 100)],
                ">>> Mixed drop at (55, 100) — confirm, press Enter to unload: ")

    # Move merged drop col 100 → 128
    print("Moving to unload position (55, 128)...")
    for c in range(101, 129):
        activate(all_mains + [Drop(merge_h, merged_w, 55, c)])

    # Deactivate merged drop, keep reservoirs holding
    activate(all_mains)
    _held_pause(all_mains, ">>> Merged drop unloaded at (55, 128) — reservoirs holding, press Enter to finish: ")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    microfluidics.InitUSB()
    if microfluidics.OpenUSB():
        input("USB open — press Enter: ")
    else:
        input("USB FAILED — press Enter to abort: ")
        return

    microfluidics.SetPower(True)
    input("Power on — press Enter to set voltage: ")
    microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)

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