"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
import time
import csv
import threading
from ctypes import POINTER, c_int, c_void_p, Structure
from dataclasses import dataclass
from typing import List, Tuple

# Load the ACX-provided DLL. Replace with the actual path on your machine.
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")


class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),  # top edge, 0-indexed
        ("col",    ctypes.c_int),  # left edge, 0-indexed
    ]


# ── Argtypes -- enforces correct C signatures so ctypes packs args properly ───
microfluidics.SetPower.argtypes     = [ctypes.c_bool]
microfluidics.SetVolt.argtypes      = [c_int] * 9
microfluidics.InquireVolt.argtypes  = [POINTER(c_int)] * 9
microfluidics.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
microfluidics.ActivateElec.restype  = c_int


# ── Geometry constants (electrode units) ──────────────────────────────────────
MAIN_COL        = 5    # left edge of every reservoir drop
MAIN_W          = 15   # width of every reservoir drop (cols 5–19)
PIECE_START_COL = 30   # column where piece first appears after split
STRETCH_STEPS   = 25   # number of pinch steps; also the column travel distance
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55
NECK_START      = MAIN_COL + MAIN_W                # col=20
NECK_END        = PIECE_FINAL_COL - 1              # col=54

DROP1_ROW = 55   # reservoir row for Drop 1
DROP2_ROW = 105  # reservoir row for Drop 2
DROP3_ROW = 10   # reservoir row for Drop 3

MEETING_ROW = 55   # row all pieces converge to
MEETING_COL = 30   # column all pieces converge to

# Maps CSV 'drop' labels → physical reservoir rows
DROP_LABEL_TO_ROW = {
    "Drop 1 (row=55)":  DROP1_ROW,
    "Drop 2 (row=105)": DROP2_ROW,
    "Drop 3 (row=10)":  DROP3_ROW,
}
DROP_ORDER = ["Drop 1 (row=55)", "Drop 2 (row=105)", "Drop 3 (row=10)"]


@dataclass
class SplitState:
    """Records the final electrode dimensions after a drop has been split."""
    label:   str
    row:     int   # reservoir row
    main_h:  int
    main_w:  int
    piece_h: int
    piece_w: int


# ── Low-level helpers ──────────────────────────────────────────────────────────

def activate(drops: List[Drop]) -> None:
    """
    Sends a list of Drop objects to the device in one ActivateElec call.
    Deduplicates by (row, col) so the SDK never receives the same electrode twice.
    """
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
    """Returns electrode specs to keep every already-split drop active."""
    drops = []
    for s in finished:
        drops.append(Drop(s.main_h,  s.main_w,  s.row, MAIN_COL))
        drops.append(Drop(s.piece_h, s.piece_w, s.row, PIECE_FINAL_COL))
    return drops


# ── Startup ────────────────────────────────────────────────────────────────────

def startup_and_confirm_voltage() -> None:
    """Initialises USB, powers on, sets voltage, and verifies the device responded correctly."""
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

def load_and_hold_drop(row_position: int, label: str, finished: List[SplitState]) -> None:
    """
    Continuously re-activates the starting 10×20 electrode (plus all already-finished
    drops) in a background thread while the user physically loads the drop.
    Stops once the user presses Enter.
    """
    print(f"\n--- LOAD AND HOLD: {label} ---")
    print(f"Starting electrode: row={row_position}, col={MAIN_COL}, height=10, width=20")
    print("Electrode will stay continuously active while you load the drop.\n")

    stop = threading.Event()

    def hold_loop():
        while not stop.is_set():
            activate(held_drops(finished) + [Drop(10, 20, row_position, MAIN_COL)])

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
    Drives one drop through its CSV stages (load/stretch → split → pinch),
    then runs the neck-deactivation sweep to fully separate main and piece.

    Returns (main_h, main_w, piece_h, piece_w) for the completed split.
    """
    if not rows_for_drop:
        raise ValueError(
            f"No CSV rows found for '{label}'. "
            "Check that the 'drop' column in the CSV exactly matches the label."
        )

    pinch_counter = 0
    main_h = main_w = piece_h = piece_w = None

    # Compute once -- finished list does not change inside this function
    base = held_drops(finished)

    for csv_row in rows_for_drop:
        stage  = csv_row["stage"]
        main_h = int(csv_row["main_height"])
        main_w = int(csv_row["main_width"])

        if stage in ("load", "stretch"):
            # Both stages activate only the main drop; dimensions come from CSV
            activate(base + [Drop(main_h, main_w, row_position, MAIN_COL)])
            print(f"{label} {stage.upper()} -- {main_h}×{main_w}")

        elif stage == "split":
            piece_h = int(csv_row["piece_height"])
            piece_w = int(csv_row["piece_width"])
            activate(base + [
                Drop(main_h,  main_w,  row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, PIECE_START_COL),
            ])
            print(f"{label} SPLIT -- main {main_h}×{main_w}, piece {piece_h}×{piece_w}")

        elif stage == "pinch":
            piece_h = int(csv_row["piece_height"])
            piece_w = int(csv_row["piece_width"])
            pinch_counter += 1
            current_col = PIECE_START_COL + pinch_counter
            activate(base + [
                Drop(main_h,  main_w,  row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, current_col),
            ])
            print(f"{label} PINCH {pinch_counter}/{STRETCH_STEPS} -- piece width={piece_w}")

        else:
            print(f"*** WARNING: unknown stage '{stage}' for {label} -- skipping row")

    if piece_h is None or piece_w is None:
        raise RuntimeError(
            f"{label}: sequence ended without a split/pinch stage -- "
            "cannot determine piece dimensions."
        )

    # Sweep bridge columns off right-to-left to fully separate the drops
    print(f"{label} deactivating neck...")
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
    # Group rows by drop label
    drops_data: dict[str, List[dict]] = {label: [] for label in DROP_ORDER}

    with open(filepath, newline="") as f:
        for csv_row in csv.DictReader(f):
            label = csv_row.get("drop", "").strip()
            if label in drops_data:
                drops_data[label].append(csv_row)

    finished: List[SplitState] = []

    for label in DROP_ORDER:
        row_position = DROP_LABEL_TO_ROW[label]

        load_and_hold_drop(row_position, label, finished)

        main_h, main_w, piece_h, piece_w = run_drop_sequence(
            drops_data[label], row_position, label, finished
        )

        finished.append(SplitState(
            label=label,
            row=row_position,
            main_h=main_h,
            main_w=main_w,
            piece_h=piece_h,
            piece_w=piece_w,
        ))

        input(f">>> {label} fully split and holding -- press Enter to continue")

    return finished


# ── Piece convergence and merge ────────────────────────────────────────────────

def move_pieces_to_meet(finished: List[SplitState]) -> Tuple[int, int]:
    """
    Moves all three pieces to meeting point (MEETING_ROW, MEETING_COL) in two phases,
    then merges them into one combined drop.

    Phase A: row alignment -- Drop 2 moves up (105→55), Drop 3 moves down (10→55),
             Drop 1 is already on MEETING_ROW. Loop runs for the larger of the two
             distances (50 steps); the shorter-traveling drop clamps when it arrives.

    Phase B: column convergence -- all pieces are now co-located at
             (MEETING_ROW, PIECE_FINAL_COL) and move left together as one electrode.

    Returns (merged_h, merged_w) of the combined drop.
    """
    s1, s2, s3 = finished

    input(
        f"\n>>> All three drops split -- press Enter to move pieces toward "
        f"meeting point row={MEETING_ROW}, col={MEETING_COL}"
    )

    # ── Phase A: converge all pieces onto MEETING_ROW ─────────────────────────
    row_steps = max(abs(s2.row - MEETING_ROW), abs(MEETING_ROW - s3.row))

    print(f"Phase A -- aligning all pieces onto row={MEETING_ROW} ({row_steps} steps)...")
    for i in range(1, row_steps + 1):
        r1 = MEETING_ROW                          # Drop 1 already on meeting row
        r2 = max(s2.row - i, MEETING_ROW)         # Drop 2 row decreases: 105 → 55
        r3 = min(s3.row + i, MEETING_ROW)         # Drop 3 row increases: 10 → 55

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

    # ── Phase B: move combined piece left from col=55 to col=30 ───────────────
    # All three pieces are now co-located at (MEETING_ROW, PIECE_FINAL_COL),
    # so they are sent as one electrode. Height is the sum of all three piece
    # heights to represent the combined volume -- adjust if hardware expects different.
    combined_piece_h = s1.piece_h + s2.piece_h + s3.piece_h
    combined_piece_w = s1.piece_w  # all pieces should share the same width

    col_steps = PIECE_FINAL_COL - MEETING_COL
    print(f"Phase B -- moving combined piece left to col={MEETING_COL} ({col_steps} steps)...")

    for i in range(1, col_steps + 1):
        current_col = PIECE_FINAL_COL - i
        activate([
            Drop(s1.main_h,        s1.main_w,       s1.row,      MAIN_COL),
            Drop(s2.main_h,        s2.main_w,        s2.row,      MAIN_COL),
            Drop(s3.main_h,        s3.main_w,        s3.row,      MAIN_COL),
            Drop(combined_piece_h, combined_piece_w, MEETING_ROW, current_col),
        ])
        print(f"  combined piece at col={current_col}")

    input(
        f">>> All pieces met at row={MEETING_ROW}, col={MEETING_COL} -- "
        "press Enter to merge"
    )
    time.sleep(1)

    # ── Merge ──────────────────────────────────────────────────────────────────
    activate([
        Drop(s1.main_h,        s1.main_w,       s1.row,      MAIN_COL),
        Drop(s2.main_h,        s2.main_w,        s2.row,      MAIN_COL),
        Drop(s3.main_h,        s3.main_w,        s3.row,      MAIN_COL),
        Drop(combined_piece_h, combined_piece_w, MEETING_ROW, MEETING_COL),
    ])
    print(
        f">>> All three pieces merged at row={MEETING_ROW}, col={MEETING_COL} "
        f"(combined {combined_piece_h}×{combined_piece_w})"
    )

    return combined_piece_h, combined_piece_w


# ── Hold final state ───────────────────────────────────────────────────────────

def hold_final_state_forever(
    finished: List[SplitState],
    merged_h: int,
    merged_w: int,
) -> None:
    """
    Continuously re-activates all three main drops and the merged drop
    until the user presses Enter to signal readiness to power off.
    """
    print("\n--- HOLDING FINAL STATE INDEFINITELY ---")
    for s in finished:
        print(f"  Main '{s.label}' held at row={s.row}, col={MAIN_COL}, {s.main_h}×{s.main_w}")
    print(f"  Merged drop held at row={MEETING_ROW}, col={MEETING_COL}, {merged_h}×{merged_w}")
    print("All electrodes continuously active. Press Enter ONLY when ready to power off.\n")

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
    print("Hold loop stopped")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. USB init, power on, voltage check
    startup_and_confirm_voltage()

    # 2. Load and split all three drops via CSV
    finished = execute_volume_csv()

    # 3. Converge pieces and merge
    merged_h, merged_w = move_pieces_to_meet(finished)

    # 4. Hold indefinitely until user is ready to shut down
    hold_final_state_forever(finished, merged_h, merged_w)

    # 5. Shutdown (only reached after user pressed Enter above)
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()