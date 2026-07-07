"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded.

FIXES vs original colormix1.py
─────────────────────────────────────────────────────────────────────────────
1. _held_pause() — drops were NOT being refreshed during every input() wait.
   A single activate() fires once (~0.5 s of electrode life) then nothing.
   On a 0.3–0.5 s refresh-needed chip the drop drifts off before the user
   even reads the prompt.  _held_pause() runs a daemon thread that keeps
   calling ActivateElec every 0.5 s until the user presses Enter, then
   stops cleanly before the next activate sequence.

2. Wider stretch — original went from width=20 to width=35 (15 steps).
   Increased to width=20 → width=45 (25 steps).  A longer, thinner neck
   gives the voltage gradient more room to pinch the connection before the
   pattern step fires, producing a cleaner split.

3. Larger piece at split point — PIECE_START_W 10 → 15.  A wider piece has
   more volume to "snap into" when the neck breaks, rather than being
   reabsorbed by the main drop.

4. ActivateElec return-value check — silently-failing DLL calls now print
   a warning so electrode faults are visible in the terminal output.
"""

import ctypes
from ctypes import POINTER, c_int, c_void_p, Structure
import threading
import time

# Load the ACX-provided DLL. Replace this path with the actual DLL location.
microfluidics = ctypes.CDLL(path_from_acx)

# ── Argtypes ──────────────────────────────────────────────────────────────────
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


# ── Movement constants ────────────────────────────────────────────────────────
MAIN_COL        = 5    # left edge of every main (reservoir) drop
MAIN_H          = 10   # height of every drop region (rows)
MAIN_W          = 15   # width of every main drop (cols 5–19)
PIECE_START_COL = 30   # column where the piece first appears after split
PIECE_START_W   = 15   # FIX: was 10 — wider piece pulls away from neck more cleanly
PIECE_END_W     = 5    # piece width after pinching is complete
STRETCH_STEPS   = 25   # steps the piece moves right during pinch
NECK_START      = MAIN_COL + MAIN_W        # col=20 — right edge of main drop
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55
NECK_END        = PIECE_FINAL_COL - 1              # col=54

# FIX: wider stretch — original loaded at width=20 and stretched to 35 (15 steps).
# Now stretches to 45 (25 steps) so the neck is much thinner before patterning.
LOAD_W          = 20   # initial electrode width when drop is placed
STRETCH_TARGET  = 45   # width at end of stretch (was 35)
STRETCH_RANGE   = STRETCH_TARGET - LOAD_W   # 25 steps

DROP1_ROW = 55
DROP2_ROW = 105
DROP3_ROW = 10

MEETING_ROW = 55
MEETING_COL = 30


# ── Low-level activation ──────────────────────────────────────────────────────

# One lock shared by every path that touches ActivateElec.
# Without this, the HoldThread and the main split/mix sequence can call
# ActivateElec at the same instant — the DLL receives two simultaneous
# writes and the losing call silently overwrites the winning one mid-motion.
# By drop 3 the held pattern is complex enough that this race becomes
# frequent and causes the split to fail.
_dll_lock = threading.Lock()


def _hw_activate(drops: list) -> None:
    """
    Fire one ActivateElec call under the DLL lock so the HoldThread and the
    main sequence never touch the hardware at the same instant.
    Prints a warning if the DLL returns a non-zero error code.
    """
    n   = len(drops)
    arr = (Drop * n)(*drops)
    with _dll_lock:
        ret = microfluidics.ActivateElec(128, 128, n, arr)
    if ret != 0:
        print(f"  *** WARNING: ActivateElec returned {ret} — electrode fault ***")


def activate(drops, debug_label=""):
    """
    Send drops to the device, print a breakdown of every electrode region,
    then sleep 0.5 s to let electrodes settle.
    """
    print(f"\n--- ACTIVATE: {debug_label} ---")
    print(f"    Drops sent: {len(drops)}")
    for idx, d in enumerate(drops):
        print(
            f"    Drop[{idx}]: row={d.row}, col={d.col}, "
            f"h={d.height}, w={d.width} "
            f"| rows {d.row}–{d.row + d.height - 1}, "
            f"cols {d.col}–{d.col + d.width - 1}"
        )
    _hw_activate(drops)
    time.sleep(0.5)


def _activate_mix(drops):
    """
    Fire one ActivateElec call at mix speed (0.3 s) with no debug output.
    Used inside mix_drop() for smooth, continuous motion — the slower 0.5 s
    of activate() would make the mix sluggish.
    """
    _hw_activate(drops)
    time.sleep(0.3)


def mix_drop(all_mains, merge_row, merge_col, merge_h, merge_w):
    """
    Six-pass mix routine — all movement goes to the RIGHT of the merge point
    so the mixed drop never swings back toward the reservoir drops on the left.

    The split is horizontal: each reservoir drop stretches rightward, the piece
    is separated to the right, and all three pieces meet to the right of the
    reservoirs before merging.  Mixing also stays in that right-side zone.

    Passes:
      1 — right 30 → back
      2 — up 20 while sweeping right 30 → back diagonally
      3 — right 30, then down 20 → back diagonally
      4 — right 30 (fast double-pass for extra mixing)
      5 — diagonal up-right 15 → back
      6 — clockwise rectangle 30 wide × 20 tall to the right
    """
    H, W = merge_h, merge_w
    r, c = merge_row, merge_col

    # Pass 1: right 30 → back
    print("  Mix pass 1: right 30...")
    for i in range(1, 31):
        _activate_mix(all_mains + [Drop(H, W, r, c + i)])
    for i in range(30, -1, -1):
        _activate_mix(all_mains + [Drop(H, W, r, c + i)])

    # Pass 2: diagonal up-right 20 → back
    print("  Mix pass 2: diagonal up-right 20...")
    for i in range(1, 21):
        _activate_mix(all_mains + [Drop(H, W, r - i, c + i)])
    for i in range(20, -1, -1):
        _activate_mix(all_mains + [Drop(H, W, r - i, c + i)])

    # Pass 3: right 30, then down 20, back diagonally
    print("  Mix pass 3: right-then-down...")
    for i in range(1, 31):
        _activate_mix(all_mains + [Drop(H, W, r, c + i)])
    for i in range(1, 21):
        _activate_mix(all_mains + [Drop(H, W, r + i, c + 30)])
    for i in range(20, -1, -1):
        _activate_mix(all_mains + [Drop(H, W, r + i, c + i)])   # diagonal back to start

    # Pass 4: right 30 fast double-pass (extra shear for mixing)
    print("  Mix pass 4: right 30 double-pass...")
    for _ in range(2):
        for i in range(1, 31):
            _activate_mix(all_mains + [Drop(H, W, r, c + i)])
        for i in range(30, -1, -1):
            _activate_mix(all_mains + [Drop(H, W, r, c + i)])

    # Pass 5: diagonal down-right 15 → back
    print("  Mix pass 5: diagonal down-right 15...")
    for i in range(1, 16):
        _activate_mix(all_mains + [Drop(H, W, r + i, c + i)])
    for i in range(15, -1, -1):
        _activate_mix(all_mains + [Drop(H, W, r + i, c + i)])

    # Pass 6: clockwise rectangle 30 wide × 20 tall (entirely to the right)
    print("  Mix pass 6: clockwise rectangle 30×20 to the right...")
    for i in range(1, 31):                                    # right
        _activate_mix(all_mains + [Drop(H, W, r,      c + i)])
    for i in range(1, 21):                                    # down
        _activate_mix(all_mains + [Drop(H, W, r + i,  c + 30)])
    for i in range(30, -1, -1):                               # left (back toward merge col)
        _activate_mix(all_mains + [Drop(H, W, r + 20, c + i)])
    for i in range(20, -1, -1):                               # up back to start
        _activate_mix(all_mains + [Drop(H, W, r + i,  c)])

    print("  Mix complete — drop back at merge point.")


# ── Continuous hold during user pauses ───────────────────────────────────────

class _HoldThread:
    """
    Daemon thread that repeatedly calls _hw_activate(drops) every 0.5 s.

    Without this, a single activate() fires once and the drop loses charge
    in ~0.5 s.  Any input() call that takes more than that will find the
    drop already gone when the user presses Enter.
    """
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
    """
    Continuously refresh `drops` while waiting for the user to press Enter,
    then stop refreshing before returning so the caller can call activate()
    without a concurrent-activation conflict.
    """
    _hold.start(drops)
    input(prompt)
    _hold.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────

def held_drops(held_rows):
    """
    Builds the list of Drop objects needed to keep previously-split drops
    held in place while a new split sequence runs.
    """
    drops = []
    for r in held_rows:
        drops.append(Drop(MAIN_H, MAIN_W,      r, MAIN_COL))
        drops.append(Drop(MAIN_H, PIECE_END_W, r, PIECE_FINAL_COL))
    return drops


# ── Split sequence ────────────────────────────────────────────────────────────

def load_and_hold_drop(row, label, held_rows):
    """
    Activate the starting electrode and hold it continuously while the user
    places the drop.  Uses _held_pause so the electrode stays live no matter
    how long the user takes.
    """
    drops = held_drops(held_rows) + [Drop(MAIN_H, LOAD_W, row, MAIN_COL)]
    activate(drops, debug_label=f"{label} LOAD")
    _held_pause(drops,
                f"\n>>> {label} — electrode ACTIVE at row={row}, col={MAIN_COL} "
                f"({MAIN_H}×{LOAD_W}) — LOAD YOUR DROP NOW, then press Enter to stretch: ")


def split_and_move(row, label, held_rows):

    # ── Step 1: Load ──────────────────────────────────────────────────────────
    load_and_hold_drop(row, label, held_rows)

    # ── Step 2: Stretch — width=20 → width=45 (25 steps, was 15) ─────────────
    # Longer stretch = thinner neck = cleaner split.
    print(f"{label} stretching from width={LOAD_W} to width={STRETCH_TARGET} "
          f"({STRETCH_RANGE} steps)...")
    for i in range(1, STRETCH_RANGE + 1):
        activate(
            held_drops(held_rows) + [Drop(MAIN_H, LOAD_W + i, row, MAIN_COL)],
            debug_label=f"{label} STRETCH width={LOAD_W + i}"
        )

    # Hold at maximum stretch — continuous refresh during user confirmation.
    drops_stretched = held_drops(held_rows) + [Drop(MAIN_H, STRETCH_TARGET, row, MAIN_COL)]
    _held_pause(drops_stretched,
                f">>> {label} stretched to width={STRETCH_TARGET} — "
                f"confirm thin neck visible, then press Enter to split pattern: ")

    # ── Step 3: Pattern — main snaps back to MAIN_W, piece appears at PIECE_START_COL ──
    # PIECE_START_W is now 15 (was 10) so the piece has more volume to anchor itself.
    drops_pattern = held_drops(held_rows) + [
        Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
        Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
    ]
    activate(drops_pattern, debug_label=f"{label} SPLIT PATTERN")
    _held_pause(drops_pattern,
                f">>> {label} split patterned "
                f"(main {MAIN_W}w @ col={MAIN_COL}, piece {PIECE_START_W}w @ col={PIECE_START_COL}) "
                f"— confirm both regions visible, press Enter to move piece: ")

    # ── Step 4: Move piece right, pinching PIECE_START_W → PIECE_END_W ───────
    print(f"{label} moving piece {STRETCH_STEPS} steps right, "
          f"pinching {PIECE_START_W} → {PIECE_END_W}...")
    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = max(PIECE_END_W, round(
            PIECE_START_W - (PIECE_START_W - PIECE_END_W) * i / STRETCH_STEPS
        ))
        activate(
            held_drops(held_rows) + [
                Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
                Drop(MAIN_H, current_width, row, current_col),
            ],
            debug_label=f"{label} MOVE step={i} col={current_col} w={current_width}"
        )
        print(f"  {label} piece at col={current_col}, width={current_width}")

    drops_at_final = held_drops(held_rows) + [
        Drop(MAIN_H, MAIN_W,      row, MAIN_COL),
        Drop(MAIN_H, PIECE_END_W, row, PIECE_FINAL_COL),
    ]
    _held_pause(drops_at_final,
                f">>> {label} piece at col={PIECE_FINAL_COL} — "
                f"confirm piece separated, press Enter to deactivate neck: ")

    # ── Step 5: Deactivate neck column-by-column ──────────────────────────────
    # Sweeps from col=54 back to col=20, shrinking the bridge by 1 each step.
    print(f"{label} deactivating neck ({NECK_END}→{NECK_START})...")
    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START
        if bridge_width > 0:
            activate(
                held_drops(held_rows) + [
                    Drop(MAIN_H, MAIN_W,       row, MAIN_COL),
                    Drop(MAIN_H, bridge_width, row, NECK_START),
                    Drop(MAIN_H, PIECE_END_W,  row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} NECK col={release_col} bridge={bridge_width}"
            )
        else:
            activate(
                held_drops(held_rows) + [
                    Drop(MAIN_H, MAIN_W,      row, MAIN_COL),
                    Drop(MAIN_H, PIECE_END_W, row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} NECK FINAL — drops fully separated"
            )
        print(f"  {label} col={release_col} released, bridge remaining={bridge_width}")

    drops_split = held_drops(held_rows) + [
        Drop(MAIN_H, MAIN_W,      row, MAIN_COL),
        Drop(MAIN_H, PIECE_END_W, row, PIECE_FINAL_COL),
    ]
    _held_pause(drops_split,
                f">>> {label} fully split — confirm piece stable at col={PIECE_FINAL_COL}, "
                f"then press Enter to continue: ")


# ── Merge sequence ────────────────────────────────────────────────────────────

def move_pieces_to_meet():
    """
    Phase A: align all pieces to MEETING_ROW.
    Phase B: sweep all pieces left to MEETING_COL.
    Merge: collapse to one combined drop.
    All input() pauses use _held_pause to keep drops active during the wait.
    """
    all_mains = [
        Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP3_ROW, MAIN_COL),
    ]

    drops_before_phase_a = all_mains + [
        Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),
        Drop(MAIN_H, PIECE_END_W, DROP2_ROW,   PIECE_FINAL_COL),
        Drop(MAIN_H, PIECE_END_W, DROP3_ROW,   PIECE_FINAL_COL),
    ]
    _held_pause(drops_before_phase_a,
                f"\n>>> All three drops split — press Enter to move pieces to "
                f"meeting point row={MEETING_ROW}, col={MEETING_COL}: ")

    # ── Phase A: row alignment ─────────────────────────────────────────────────
    row_steps = max(abs(DROP2_ROW - MEETING_ROW), abs(MEETING_ROW - DROP3_ROW))
    print(f"Phase A — aligning all pieces to row={MEETING_ROW} ({row_steps} steps)...")

    for i in range(1, row_steps + 1):
        piece2_row = max(DROP2_ROW - i, MEETING_ROW)
        piece3_row = min(DROP3_ROW + i, MEETING_ROW)
        activate(
            all_mains + [
                Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),
                Drop(MAIN_H, PIECE_END_W, piece2_row,  PIECE_FINAL_COL),
                Drop(MAIN_H, PIECE_END_W, piece3_row,  PIECE_FINAL_COL),
            ],
            debug_label=f"PHASE A step={i} piece2={piece2_row} piece3={piece3_row}"
        )

    drops_phaseA_done = all_mains + [
        Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),
        Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),
        Drop(MAIN_H, PIECE_END_W, MEETING_ROW, PIECE_FINAL_COL),
    ]
    _held_pause(drops_phaseA_done,
                f">>> All three pieces on row={MEETING_ROW} — "
                f"press Enter to sweep left to col={MEETING_COL}: ")

    # ── Phase B: column sweep left ─────────────────────────────────────────────
    col_steps = PIECE_FINAL_COL - MEETING_COL
    print(f"Phase B — sweeping pieces left to col={MEETING_COL} ({col_steps} steps)...")

    for i in range(1, col_steps + 1):
        current_col = PIECE_FINAL_COL - i
        activate(
            all_mains + [Drop(MAIN_H, PIECE_END_W, MEETING_ROW, current_col)],
            debug_label=f"PHASE B step={i} col={current_col}"
        )
        print(f"  all pieces now at col={current_col}")

    drops_phaseB_done = all_mains + [Drop(MAIN_H, PIECE_END_W, MEETING_ROW, MEETING_COL)]
    _held_pause(drops_phaseB_done,
                f">>> All pieces at col={MEETING_COL} — "
                f"press Enter to merge: ")

    # ── Merge ──────────────────────────────────────────────────────────────────
    merge_h = MAIN_H * 3
    merged  = all_mains + [Drop(merge_h, PIECE_END_W, MEETING_ROW, MEETING_COL)]
    activate(merged, debug_label="MERGE — all three pieces combined")
    _held_pause(merged,
                f">>> Merged drop at row={MEETING_ROW}, col={MEETING_COL} — "
                f"confirm merge visible, press Enter to mix: ")

    # ── Mix ────────────────────────────────────────────────────────────────────
    print(f"\nMixing merged drop at row={MEETING_ROW}, col={MEETING_COL} "
          f"(6 passes, 0.3 s/step)...")
    mix_drop(all_mains, MEETING_ROW, MEETING_COL, merge_h, PIECE_END_W)

    _held_pause(merged,
                f">>> Mix complete — drop at row={MEETING_ROW}, col={MEETING_COL}. "
                f"Press Enter to finish: ")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    microfluidics.InitUSB()
    res = microfluidics.OpenUSB()
    if res:
        input("USB open — press Enter to continue: ")
    else:
        input("USB FAILED to open — press Enter to abort: ")
        return

    microfluidics.SetPower(True)
    input("Power on — press Enter to set voltage: ")

    microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
    input("Voltage set — press Enter to verify: ")

    voltages = [ctypes.c_int(0) for _ in range(9)]
    microfluidics.InquireVolt(*[ctypes.byref(v) for v in voltages])
    print("Voltages: " + " ".join(str(v.value) for v in voltages))
    input("Voltage confirmed — press Enter to begin splits: ")

    split_and_move(row=DROP1_ROW, label="Drop 1 (row=55)",  held_rows=[])
    input(">>> Drop 1 holding — press Enter to start Drop 2: ")

    split_and_move(row=DROP2_ROW, label="Drop 2 (row=105)", held_rows=[DROP1_ROW])
    input(">>> Drop 2 holding — press Enter to start Drop 3: ")

    split_and_move(row=DROP3_ROW, label="Drop 3 (row=10)",  held_rows=[DROP1_ROW, DROP2_ROW])

    move_pieces_to_meet()

    input(">>> Sequence complete — press Enter to shut down: ")

    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off — press Enter to close USB: ")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()
