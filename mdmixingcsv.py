"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments.
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path.
The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
from ctypes import Structure
import time
import os
import csv

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


# ── Constants ─────────────────────────────────────────────────────────────────

MAIN_H          = 10
MAIN_W          = 15

# Drop 1 and 2 — main body starts at col=5
MAIN_COL        = 5
DROP1_ROW       = 55
DROP2_ROW       = 105

# Drop 3 — starts at row=5, col=10
DROP3_ROW       = 5
DROP3_COL       = 10

# Piece geometry
PIECE_START_COL = 30
STRETCH_STEPS   = 25
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS  # col=55

# Meeting point
MEETING_ROW     = DROP1_ROW        # row=55
MEETING_COL     = PIECE_FINAL_COL  # col=55

# Piece width constraints
MIN_PIECE_W     = 1
MAX_PIECE_W     = 14

# CSV configuration
CSV_FILE        = r"C:\Users\klmcg\SULIProj\ACX-Chip-Project\volume_control.csv"
CSV_HEADERS     = [
    'run_id',
    'drop1_start_w', 'drop1_end_w',
    'drop2_start_w', 'drop2_end_w',
    'drop3_start_w', 'drop3_end_w',
    'result'
]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def generate_csv_template():
    """Creates a blank CSV template with headers and 3 example rows."""
    with open(CSV_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for i in range(1, 4):
            writer.writerow({
                'run_id':        i,
                'drop1_start_w': 10,  'drop1_end_w': 5,
                'drop2_start_w': 10,  'drop2_end_w': 5,
                'drop3_start_w': 10,  'drop3_end_w': 5,
                'result':        ''
            })
    print(f"CSV template created at: {CSV_FILE}")
    print("Fill in the width values (1–14px) before running.\n")


def load_csv():
    """Returns all rows from the CSV as a list of dicts."""
    with open(CSV_FILE, 'r', newline='') as f:
        return list(csv.DictReader(f))


def save_result(run_id, result):
    """Writes a result value back to the matching run_id row in the CSV."""
    rows = load_csv()
    for row in rows:
        if row['run_id'] == str(run_id):
            row['result'] = result
    with open(CSV_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Result '{result}' saved to run_id={run_id} in CSV.")


def validate_widths(run_id, **widths):
    """
    Checks all piece widths are integers within MIN_PIECE_W–MAX_PIECE_W.
    Returns True if valid, False if any width is out of range.
    """
    valid = True
    for name, val in widths.items():
        if not (MIN_PIECE_W <= val <= MAX_PIECE_W):
            print(f"  [run {run_id}] ERROR: {name}={val} is out of range "
                  f"({MIN_PIECE_W}–{MAX_PIECE_W}px). Skipping this run.")
            valid = False
    return valid


# ── Hardware helpers ──────────────────────────────────────────────────────────

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


def held_drops(held_items):
    """
    held_items: list of (row, main_col, piece_end_w) tuples.
    Returns Drop objects for each held main body and its piece.
    """
    drops = []
    for r, mc, pew in held_items:
        drops.append(Drop(MAIN_H, MAIN_W, r, mc))
        drops.append(Drop(MAIN_H, pew,    r, PIECE_FINAL_COL))
    return drops


# ── Experiment steps ──────────────────────────────────────────────────────────

def split_and_move(row, label, held_items, start_col, piece_start_w, piece_end_w):
    """
    Loads, stretches, splits, and moves a drop's piece to PIECE_FINAL_COL.

    row           — the row this drop lives on
    label         — human-readable name for debug output
    held_items    — list of (row, main_col, piece_end_w) tuples for held drops
    start_col     — left edge of this drop's main body
    piece_start_w — width of piece when first split off (1–14px, from CSV)
    piece_end_w   — final width of piece after moving (1–14px, from CSV)
    """
    neck_start     = start_col + MAIN_W
    neck_gap       = PIECE_START_COL - neck_start
    # Stretch must reach from start_col to the right edge of the piece at split
    stretch_target = PIECE_START_COL + piece_start_w - start_col
    stretch_steps  = stretch_target - 20   # initial load width is always 20

    # ── Step 1: Load ──────────────────────────────────────────────────────────
    activate(
        held_drops(held_items) + [Drop(MAIN_H, 20, row, start_col)],
        debug_label=f"{label} LOAD"
    )
    input(f"\n>>> {label} loaded at row={row}, col={start_col} -- press Enter to begin stretch")

    # ── Step 2: Stretch to reach piece position ───────────────────────────────
    print(f"{label} stretching from width=20 to width={stretch_target} ({stretch_steps} steps)...")
    time.sleep(2)
    for i in range(1, stretch_steps + 1):
        activate(
            held_drops(held_items) + [Drop(MAIN_H, 20 + i, row, start_col)],
            debug_label=f"{label} STRETCH width={20 + i}"
        )
    input(f">>> {label} fully stretched to width={stretch_target} -- press Enter to split")

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
                    Drop(MAIN_H, piece_start_w, row, PIECE_START_COL),
                ],
                debug_label=f"{label} SPLIT gap={gap} bridge_w={bridge_width}"
            )
        else:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,        row, start_col),
                    Drop(MAIN_H, piece_start_w, row, PIECE_START_COL),
                ],
                debug_label=f"{label} SPLIT gap fully open"
            )
        print(f"  gap={gap} px open, bridge_width={bridge_width}")
    input(f">>> {label} split complete -- press Enter to move piece")

    # ── Step 4: Move piece right, pinching from piece_start_w to piece_end_w ─
    print(f"{label} moving piece {STRETCH_STEPS}px right, "
          f"pinching {piece_start_w} → {piece_end_w} wide...")
    time.sleep(2)
    for i in range(1, STRETCH_STEPS + 1):
        current_col   = PIECE_START_COL + i
        current_width = round(
            piece_start_w - (piece_start_w - piece_end_w) * i / STRETCH_STEPS
        )
        activate(
            held_drops(held_items) + [
                Drop(MAIN_H, MAIN_W,        row, start_col),
                Drop(MAIN_H, current_width, row, current_col),
            ],
            debug_label=f"{label} MOVE step={i} col={current_col} width={current_width}"
        )
        print(f"  {label} piece at col={current_col}, width={current_width}")
    input(f">>> {label} piece at col={PIECE_FINAL_COL} -- press Enter to begin neck deactivation")

    # ── Step 5: Deactivate neck ───────────────────────────────────────────────
    neck_end = PIECE_FINAL_COL - 1
    print(f"{label} deactivating neck from col={neck_end} to col={neck_start}...")
    time.sleep(2)
    for release_col in range(neck_end, neck_start - 1, -1):
        bridge_width = release_col - neck_start
        if bridge_width > 0:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,       row, start_col),
                    Drop(MAIN_H, bridge_width, row, neck_start),
                    Drop(MAIN_H, piece_end_w,  row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} DEACTIVATE col={release_col} bridge={bridge_width}"
            )
        else:
            activate(
                held_drops(held_items) + [
                    Drop(MAIN_H, MAIN_W,      row, start_col),
                    Drop(MAIN_H, piece_end_w, row, PIECE_FINAL_COL),
                ],
                debug_label=f"{label} DEACTIVATE col={release_col} FINAL"
            )
        print(f"  {label} col={release_col} released, bridge remaining={bridge_width}")

    input(f">>> {label} fully split -- press Enter to continue")
    time.sleep(1)


def move_pieces_to_meet(d1_ew, d2_ew, d3_ew):
    """
    Moves all three pieces simultaneously to MEETING_ROW, MEETING_COL.
    Drop 1's piece is already at row=55; Drops 2 & 3 travel 50 steps each.
    d1_ew, d2_ew, d3_ew — each drop's final piece width (from CSV).
    """
    steps_to_meet = DROP2_ROW - MEETING_ROW   # 50 steps

    input(f"\n>>> All splits done -- press Enter to move pieces to row={MEETING_ROW}, col={MEETING_COL}")

    print(f"Moving pieces toward row={MEETING_ROW}...")
    time.sleep(1)
    for i in range(1, steps_to_meet + 1):
        piece2_row = DROP2_ROW - i
        piece3_row = DROP3_ROW + i
        activate(
            [
                Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
                Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
                Drop(MAIN_H, MAIN_W, DROP3_ROW, DROP3_COL),
                Drop(MAIN_H, d1_ew,  MEETING_ROW, MEETING_COL),
                Drop(MAIN_H, d2_ew,  piece2_row,  MEETING_COL),
                Drop(MAIN_H, d3_ew,  piece3_row,  MEETING_COL),
            ],
            debug_label=f"MOVE TO MEET step={i} piece2={piece2_row} piece3={piece3_row}"
        )
        print(f"  piece1=row{MEETING_ROW} (held), piece2=row{piece2_row}, piece3=row{piece3_row}")

    input(f">>> All pieces at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to merge")

    # Merge
    time.sleep(1)
    activate(
        [
            Drop(MAIN_H, MAIN_W, DROP1_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W, DROP2_ROW,   MAIN_COL),
            Drop(MAIN_H, MAIN_W, DROP3_ROW,   DROP3_COL),
            Drop(MAIN_H, MAIN_W, MEETING_ROW, MEETING_COL),
        ],
        debug_label="MERGE all three pieces"
    )
    input(f">>> Pieces merged at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to mix")

    # ── Inline mixing sequence ────────────────────────────────────────────────
    all_mains = [
        Drop(MAIN_H, MAIN_W, DROP1_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP2_ROW, MAIN_COL),
        Drop(MAIN_H, MAIN_W, DROP3_ROW, DROP3_COL),
    ]
    H, W = MAIN_H, MAIN_W
    r, c = MEETING_ROW, MEETING_COL

    print("\nRunning mixing sequence...")

    print("  Mix pass 1: right 30...")
    for i in range(1, 31):      activate(all_mains + [Drop(H, W, r, c + i)],       debug_label=f"MIX pass1 right i={i}")
    for i in range(30, -1, -1): activate(all_mains + [Drop(H, W, r, c + i)],       debug_label=f"MIX pass1 back i={i}")

    print("  Mix pass 2: diagonal up-right 20...")
    for i in range(1, 21):      activate(all_mains + [Drop(H, W, r - i, c + i)],   debug_label=f"MIX pass2 out i={i}")
    for i in range(20, -1, -1): activate(all_mains + [Drop(H, W, r - i, c + i)],   debug_label=f"MIX pass2 back i={i}")

    print("  Mix pass 3: right-then-down...")
    for i in range(1, 31):      activate(all_mains + [Drop(H, W, r, c + i)],       debug_label=f"MIX pass3 right i={i}")
    for i in range(1, 21):      activate(all_mains + [Drop(H, W, r + i, c + 30)],  debug_label=f"MIX pass3 down i={i}")
    for i in range(20, -1, -1): activate(all_mains + [Drop(H, W, r + i, c + i)],   debug_label=f"MIX pass3 back i={i}")

    print("  Mix pass 4: right 30 double-pass...")
    for _ in range(2):
        for i in range(1, 31):      activate(all_mains + [Drop(H, W, r, c + i)],   debug_label=f"MIX pass4 right i={i}")
        for i in range(30, -1, -1): activate(all_mains + [Drop(H, W, r, c + i)],   debug_label=f"MIX pass4 back i={i}")

    print("  Mix pass 5: diagonal down-right 15...")
    for i in range(1, 16):      activate(all_mains + [Drop(H, W, r + i, c + i)],   debug_label=f"MIX pass5 out i={i}")
    for i in range(15, -1, -1): activate(all_mains + [Drop(H, W, r + i, c + i)],   debug_label=f"MIX pass5 back i={i}")

    print("  Mix pass 6: clockwise rectangle 30x20...")
    for i in range(1, 31):      activate(all_mains + [Drop(H, W, r,      c + i)],  debug_label=f"MIX pass6 top i={i}")
    for i in range(1, 21):      activate(all_mains + [Drop(H, W, r + i,  c + 30)], debug_label=f"MIX pass6 right i={i}")
    for i in range(30, -1, -1): activate(all_mains + [Drop(H, W, r + 20, c + i)],  debug_label=f"MIX pass6 bottom i={i}")
    for i in range(20, -1, -1): activate(all_mains + [Drop(H, W, r + i,  c)],      debug_label=f"MIX pass6 left i={i}")

    print("  Mix complete.")
    input(">>> Mix complete -- press Enter to unload")


def move_piece_out():
    """Moves merged drop from col=55 to chip edge (col=128)."""
    print(f"\nMoving merged drop from col={MEETING_COL} to col=128...")
    for col in range(MEETING_COL, 128):
        activate(
            [
                Drop(MAIN_H, MAIN_W, DROP1_ROW,   MAIN_COL),
                Drop(MAIN_H, MAIN_W, DROP2_ROW,   MAIN_COL),
                Drop(MAIN_H, MAIN_W, DROP3_ROW,   DROP3_COL),
                Drop(MAIN_H, MAIN_W, MEETING_ROW, col),
            ],
            debug_label=f"MOVE OUT piece at col={col}"
        )
        print(f"  piece at col={col}")


def run_experiment(d1_sw, d1_ew, d2_sw, d2_ew, d3_sw, d3_ew):
    """Runs the full 3-drop split, meet, mix, and eject sequence."""

    # Drop 1: row=55, col=5
    split_and_move(
        row=DROP1_ROW, label="Drop 1 (row=55)",
        held_items=[], start_col=MAIN_COL,
        piece_start_w=d1_sw, piece_end_w=d1_ew
    )
    input(">>> Drop 1 holding -- press Enter to start Drop 2")

    # Drop 2: row=105, col=5
    split_and_move(
        row=DROP2_ROW, label="Drop 2 (row=105)",
        held_items=[(DROP1_ROW, MAIN_COL, d1_ew)],
        start_col=MAIN_COL,
        piece_start_w=d2_sw, piece_end_w=d2_ew
    )
    input(">>> Drop 2 holding -- press Enter to start Drop 3")

    # Drop 3: row=5, col=10
    split_and_move(
        row=DROP3_ROW, label="Drop 3 (row=5)",
        held_items=[(DROP1_ROW, MAIN_COL, d1_ew), (DROP2_ROW, MAIN_COL, d2_ew)],
        start_col=DROP3_COL,
        piece_start_w=d3_sw, piece_end_w=d3_ew
    )

    # All three pieces converge, merge, mix
    move_pieces_to_meet(d1_ew, d2_ew, d3_ew)

    # Eject merged drop to chip edge
    move_piece_out()
    input(">>> Drop unloaded at chip edge -- press Enter to continue")


def main():
    # ── Generate CSV template if it doesn't exist ─────────────────────────────
    if not os.path.exists(CSV_FILE):
        print(f"No CSV found at {CSV_FILE}. Generating template...")
        generate_csv_template()
        input("Fill in the CSV widths and press Enter to begin, or Ctrl+C to exit.")

    # ── Hardware init ─────────────────────────────────────────────────────────
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

    v1 = ctypes.c_int(0); v2 = ctypes.c_int(0); v3 = ctypes.c_int(0)
    v4 = ctypes.c_int(0); v5 = ctypes.c_int(0); v6 = ctypes.c_int(0)
    v7 = ctypes.c_int(0); v8 = ctypes.c_int(0); v9 = ctypes.c_int(0)
    microfluidics.InquireVolt(
        ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
        ctypes.byref(v4), ctypes.byref(v5), ctypes.byref(v6),
        ctypes.byref(v7), ctypes.byref(v8), ctypes.byref(v9)
    )
    print(f"Voltages: {v1.value} {v2.value} {v3.value} {v4.value} "
          f"{v5.value} {v6.value} {v7.value} {v8.value} {v9.value}")
    input("Voltage query completed")

    # ── CSV experiment loop ───────────────────────────────────────────────────
    rows = load_csv()
    total = len(rows)
    ran   = 0

    for row in rows:
        run_id = row['run_id']

        # Skip rows that already have a result
        if row['result'].strip():
            print(f"\n[run {run_id}] Already has result '{row['result']}' — skipping.")
            continue

        # Parse widths
        try:
            d1_sw = int(row['drop1_start_w']); d1_ew = int(row['drop1_end_w'])
            d2_sw = int(row['drop2_start_w']); d2_ew = int(row['drop2_end_w'])
            d3_sw = int(row['drop3_start_w']); d3_ew = int(row['drop3_end_w'])
        except ValueError:
            print(f"\n[run {run_id}] ERROR: Non-integer width value — skipping.")
            continue

        # Validate
        if not validate_widths(
            run_id,
            drop1_start_w=d1_sw, drop1_end_w=d1_ew,
            drop2_start_w=d2_sw, drop2_end_w=d2_ew,
            drop3_start_w=d3_sw, drop3_end_w=d3_ew
        ):
            continue

        # Run
        print(f"\n{'='*60}")
        print(f"  RUN {run_id} of {total}")
        print(f"  Drop 1: start_w={d1_sw}, end_w={d1_ew}")
        print(f"  Drop 2: start_w={d2_sw}, end_w={d2_ew}")
        print(f"  Drop 3: start_w={d3_sw}, end_w={d3_ew}")
        print(f"{'='*60}")
        input(f">>> Press Enter to begin run {run_id}")

        run_experiment(d1_sw, d1_ew, d2_sw, d2_ew, d3_sw, d3_ew)

        # Log result
        result = input(f"\n>>> Enter result measurement for run {run_id}: ").strip()
        save_result(run_id, result)
        ran += 1

        input(f">>> Run {run_id} complete. Press Enter for next run (or Ctrl+C to stop).")

    print(f"\nAll CSV rows processed ({ran} run(s) completed).")
    input(">>> Press Enter to shut down")

    # ── Shutdown ──────────────────────────────────────────────────────────────
    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()