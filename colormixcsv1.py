import ctypes
import time
import csv
import threading
from ctypes import POINTER, c_int, c_void_p, Structure
from dataclasses import dataclass
from typing import List, Tuple, Optional

microfluidics = ctypes.CDLL(
    r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll"
)


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]


microfluidics.SetPower.argtypes     = [ctypes.c_bool]
microfluidics.SetVolt.argtypes      = [c_int] * 9
microfluidics.InquireVolt.argtypes  = [POINTER(c_int)] * 9
microfluidics.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
microfluidics.ActivateElec.restype  = c_int


# ── Geometry constants ─────────────────────────────────────────────────────────
MAIN_COL        = 5
MAIN_W          = 15
PIECE_START_COL = 30
STRETCH_STEPS   = 25
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS   # 55
NECK_START      = MAIN_COL + MAIN_W                 # 20
NECK_END        = PIECE_FINAL_COL - 1               # 54

DROP1_ROW   = 55
DROP2_ROW   = 105
DROP3_ROW   = 10

MEETING_ROW = 55
MEETING_COL = 30

# Maps CSV 'drop' labels → physical row positions
DROP_LABEL_TO_ROW = {
    "Drop 1 (row=55)":  DROP1_ROW,
    "Drop 2 (row=105)": DROP2_ROW,
    "Drop 3 (row=10)":  DROP3_ROW,
}
DROP_ORDER = ["Drop 1 (row=55)", "Drop 2 (row=105)", "Drop 3 (row=10)"]


@dataclass
class SplitState:
    """Final electrode state after a drop has been loaded and split."""
    label:   str
    row:     int
    main_h:  int
    main_w:  int
    piece_h: int
    piece_w: int


# ── Low-level helpers ──────────────────────────────────────────────────────────

def activate(drops: List[Drop]) -> None:
    # Deduplicate by (row, col) so the SDK never receives the same electrode twice.
    seen = set()
    unique: List[Drop] = []
    for d in drops:
        key = (d.row, d.col)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    n   = len(unique)
    arr = (Drop * n)(*unique)
    microfluidics.ActivateElec(128, 128, n, arr)
    time.sleep(0.3)


def held_drops(finished: List[SplitState]) -> List[Drop]:
    """Return electrode specs to hold every already-split drop in place."""
    drops = []
    for s in finished:
        drops.append(Drop(s.main_h,  s.main_w,  s.row, MAIN_COL))
        drops.append(Drop(s.piece_h, s.piece_w, s.row, PIECE_FINAL_COL))
    return drops


# ── Startup ────────────────────────────────────────────────────────────────────

def startup_and_confirm_voltage() -> None:
    print("--- STARTUP & VOLTAGE CONFIRMATION ---")

    microfluidics.InitUSB()
    print("InitUSB called")

    res_open = microfluidics.OpenUSB()
    print(f"OpenUSB result: {res_open}")
    if not res_open:
        input("USB failed to open -- press Enter to exit")
        raise SystemExit("Stopping: USB did not open successfully")

    res_power = microfluidics.SetPower(True)
    print(f"SetPower result: {res_power}")
    time.sleep(2)

    res_volt = microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
    print(f"SetVolt result: {res_volt}")
    time.sleep(1)

    vs = [ctypes.c_int(0) for _ in range(9)]
    microfluidics.InquireVolt(*[ctypes.byref(v) for v in vs])
    vals = [v.value for v in vs]
    print("Confirmed voltages: " + " ".join(f"V{i+1}={v}" for i, v in enumerate(vals)))

    expected = [45, 45, 45, 0, 0, 0, 0, 0, 0]
    if vals != expected:
        print("\n*** WARNING: voltage does not match what was set! ***")
        print(f"Expected: {expected}")
        print(f"Actual:   {vals}")
        input("Press Enter to continue anyway, or close this window to stop")
    else:
        print("\nVoltage confirmed correct -- safe to proceed")

    input("\n>>> Startup complete -- press Enter to begin loading drops")


# ── Per-drop loading and sequencing ───────────────────────────────────────────

def load_and_hold_drop(row: int, label: str, finished: List[SplitState]) -> None:
    """
    Continuously re-activates the starting 10×20 electrode (plus all
    already-finished drops) while the user physically loads the drop.
    """
    print(f"\n--- LOAD AND HOLD: {label} ---")
    print(f"Starting electrode: row={row}, col={MAIN_COL}, height=10, width=20")
    print("Electrode will stay continuously active while you load the drop.\n")

    stop = threading.Event()

    def hold_loop():
        while not stop.is_set():
            activate(held_drops(finished) + [Drop(10, 20, row, MAIN_COL)])

    t = threading.Thread(target=hold_loop, daemon=True)
    t.start()
    input(f">>> {label} loaded and in position -- press Enter to begin stretch/split")
    stop.set()
    t.join()
    print(f"{label} hold loop stopped -- proceeding to sequence")


def run_drop_sequence(
    rows_for_drop: List[dict],
    row_position:  int,
    label:         str,
    finished:      List[SplitState],
) -> Tuple[int, int, int, int]:
    """
    Drives one drop through its load → stretch → split → pinch CSV stages,
    then runs the neck-deactivation sweep.
    Returns (main_h, main_w, piece_h, piece_w).
    """
    if not rows_for_drop:
        raise ValueError(
            f"No CSV rows found for '{label}'. "
            "Check that the 'drop' column in the CSV exactly matches the label."
        )

    pinch_counter = 0
    main_h = main_w = piece_h = piece_w = None

    for row in rows_for_drop:
        stage  = row["stage"]
        main_h = int(row["main_height"])
        main_w = int(row["main_width"])

        base = held_drops(finished)

        if stage == "load":
            activate(base + [Drop(main_h, main_w, row_position, MAIN_COL)])
            print(f"{label} LOAD -- {main_h}×{main_w}")

        elif stage == "stretch":
            activate(base + [Drop(main_h, main_w, row_position, MAIN_COL)])
            print(f"{label} STRETCH -- {main_h}×{main_w}")

        elif stage == "split":
            piece_h = int(row["piece_height"])
            piece_w = int(row["piece_width"])
            activate(base + [
                Drop(main_h,  main_w,  row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, PIECE_START_COL),
            ])
            print(f"{label} SPLIT -- main {main_h}×{main_w}, piece {piece_h}×{piece_w}")

        elif stage == "pinch":
            piece_h = int(row["piece_height"])
            piece_w = int(row["piece_width"])
            pinch_counter += 1
            current_col = PIECE_START_COL + pinch_counter
            activate(base + [
                Drop(main_h,  main_w,  row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, current_col),
            ])
            print(f"{label} PINCH {pinch_counter}/{STRETCH_STEPS} -- piece width={piece_w}")

    if piece_h is None or piece_w is None:
        raise RuntimeError(
            f"{label}: sequence ended without a split/pinch stage — "
            "cannot determine piece dimensions."
        )

    # ── Neck deactivation: sweep the bridge from NECK_END back to NECK_START ──
    print(f"{label} deactivating neck...")
    base = held_drops(finished)
    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START
        if bridge_width > 0:
            activate(base + [
                Drop(main_h,  main_w,       row_position, MAIN_COL),
                Drop(main_h,  bridge_width, row_position, NECK_START),
                Drop(piece_h, piece_w,      row_position, PIECE_FINAL_COL),
            ])
        else:
            activate(base + [
                Drop(main_h,  main_w,  row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, PIECE_FINAL_COL),
            ])
    print(f"{label} neck fully deactivated -- drop split complete")

    return main_h, main_w, piece_h, piece_w


# ── CSV-driven main sequence ───────────────────────────────────────────────────

def execute_volume_csv(
    filepath: str = r"C:\Users\klmcg\Downloads\three_drop_volume_change.csv",
) -> List[SplitState]:
    """
    Reads the CSV, then for each drop in order:
      1. Waits for the user to physically load it.
      2. Runs its load/stretch/split/pinch sequence.
      3. Adds it to the "hold forever" list so subsequent drops keep it active.
    Returns a list of SplitState for all three drops.
    """
    # Group rows by drop label
    drops_data: dict[str, List[dict]] = {label: [] for label in DROP_ORDER}

    with open(filepath, newline="") as f:
        for row in csv.DictReader(f):
            label = row.get("drop", "").strip()
            if label in drops_data:
                drops_data[label].append(row)

    finished: List[SplitState] = []

    for label in DROP_ORDER:
        row_position = DROP_LABEL_TO_ROW[label]

        load_and_hold_drop(row_position, label, finished)

        main_h, main_w, piece_h, piece_w = run_drop_sequence(
            drops_data[label], row_position, label, finished
        )

        state = SplitState(
            label=label,
            row=row_position,
            main_h=main_h,
            main_w=main_w,
            piece_h=piece_h,
            piece_w=piece_w,
        )
        finished.append(state)

        input(f">>> {label} fully split and holding -- press Enter to continue")

    return finished


# ── Piece convergence and merge ────────────────────────────────────────────────

def move_pieces_to_meet(finished: List[SplitState]) -> Tuple[List[SplitState], int, int]:
    """
    Moves all three pieces toward meeting point (row=55, col=30) in two phases:
      Phase A: align rows → all pieces reach MEETING_ROW
      Phase B: move columns → all pieces reach MEETING_COL
    Then merges them into one combined drop.

    Returns (finished, merged_h, merged_w).
    """
    s1, s2, s3 = finished  # Drop 1 (row=55), Drop 2 (row=105), Drop 3 (row=10)

    input(
        f"\n>>> All three drops split -- press Enter to move pieces toward "
        f"meeting point row={MEETING_ROW}, col={MEETING_COL}"
    )

    # ── Phase A: converge all pieces onto MEETING_ROW ─────────────────────────
    # Drop 1 is already on MEETING_ROW; drops 2 and 3 need to move.
    row_steps = max(abs(s2.row - MEETING_ROW), abs(MEETING_ROW - s3.row))

    print(f"Phase A -- aligning all pieces onto row={MEETING_ROW}...")
    for i in range(1, row_steps + 1):
        r1 = MEETING_ROW                          # already there
        r2 = max(s2.row - i, MEETING_ROW)         # moves down: 105 → 55
        r3 = min(s3.row + i, MEETING_ROW)         # moves up:   10  → 55

        activate([
            Drop(s1.main_h,  s1.main_w,  s1.row, MAIN_COL),
            Drop(s1.piece_h, s1.piece_w, r1,     PIECE_FINAL_COL),
            Drop(s2.main_h,  s2.main_w,  s2.row, MAIN_COL),
            Drop(s2.piece_h, s2.piece_w, r2,     PIECE_FINAL_COL),
            Drop(s3.main_h,  s3.main_w,  s3.row, MAIN_COL),
            Drop(s3.piece_h, s3.piece_w, r3,     PIECE_FINAL_COL),
        ])
        print(f"  piece rows: drop1={r1}, drop2={r2}, drop3={r3}")

    input(
        f">>> All three pieces aligned on row={MEETING_ROW} -- "
        "press Enter to begin column convergence"
    )
    time.sleep(1)

    # ── Phase B: move all pieces left from col=55 to col=30 ───────────────────
    # After Phase A all three pieces share (MEETING_ROW, PIECE_FINAL_COL).
    # They are co-located, so we activate a single combined piece electrode
    # whose height is the sum of all three piece heights.
    combined_piece_h = s1.piece_h + s2.piece_h + s3.piece_h
    combined_piece_w = s1.piece_w  # pieces should be the same width; use drop 1's

    col_steps = PIECE_FINAL_COL - MEETING_COL
    print(f"Phase B -- moving combined piece left to col={MEETING_COL}...")

    for i in range(1, col_steps + 1):
        current_col = PIECE_FINAL_COL - i
        activate([
            Drop(s1.main_h,       s1.main_w,      s1.row,      MAIN_COL),
            Drop(s2.main_h,       s2.main_w,      s2.row,      MAIN_COL),
            Drop(s3.main_h,       s3.main_w,      s3.row,      MAIN_COL),
            Drop(combined_piece_h, combined_piece_w, MEETING_ROW, current_col),
        ])
        print(f"  combined piece at col={current_col}")

    input(
        f">>> All pieces met at row={MEETING_ROW}, col={MEETING_COL} -- "
        "press Enter to merge"
    )
    time.sleep(1)

    # ── Merge: activate as one combined drop ───────────────────────────────────
    activate([
        Drop(s1.main_h,       s1.main_w,       s1.row,      MAIN_COL),
        Drop(s2.main_h,       s2.main_w,       s2.row,      MAIN_COL),
        Drop(s3.main_h,       s3.main_w,       s3.row,      MAIN_COL),
        Drop(combined_piece_h, combined_piece_w, MEETING_ROW, MEETING_COL),
    ])
    print(
        f">>> All three pieces merged at row={MEETING_ROW}, col={MEETING_COL} "
        f"(combined {combined_piece_h}×{combined_piece_w})"
    )

    return finished, combined_piece_h, combined_piece_w


# ── Hold final state ───────────────────────────────────────────────────────────

def hold_final_state_forever(
    finished: List[SplitState],
    merged_h: int,
    merged_w: int,
) -> None:
    """
    Continuously re-activates all three main drops and the merged drop
    until the user chooses to power off.
    Does NOT call SetPower(False) -- that is left to main().
    """
    print("\n--- HOLDING FINAL STATE INDEFINITELY ---")
    for s in finished:
        print(f"  Main '{s.label}' held at row={s.row}, col={MAIN_COL}, {s.main_h}×{s.main_w}")
    print(f"  Merged drop held at row={MEETING_ROW}, col={MEETING_COL}, {merged_h}×{merged_w}")
    print("All electrodes continuously active.")
    print("Press Enter ONLY when ready to power off.\n")

    stop = threading.Event()

    def hold_loop():
        while not stop.is_set():
            activate(
                [Drop(s.main_h, s.main_w, s.row, MAIN_COL) for s in finished]
                + [Drop(merged_h, merged_w, MEETING_ROW, MEETING_COL)]
            )

    t = threading.Thread(target=hold_loop, daemon=True)
    t.start()
    input(">>> Press Enter when ready to power off")
    stop.set()
    t.join()
    print("Hold loop stopped by user request")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Startup and voltage check
    startup_and_confirm_voltage()

    # 2. Run all three drops through the CSV sequence
    finished = execute_volume_csv()

    # 3. Converge pieces and merge
    finished, merged_h, merged_w = move_pieces_to_meet(finished)

    # 4. Hold indefinitely
    hold_final_state_forever(finished, merged_h, merged_w)

    # 5. Power off (only runs after user pressed Enter above)
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()