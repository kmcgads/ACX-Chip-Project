"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
import time
import csv
import threading
from ctypes import POINTER, c_int, c_void_p, Structure

microfluidics = ctypes.CDLL("C:\\Users\\klmcg\\Downloads\\ACX_pythonSDK v1.2 3\\ACX_pythonSDK\\windows\\DLLTest.dll")

class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]

microfluidics.SetPower.argtypes    = [ctypes.c_bool]
microfluidics.SetVolt.argtypes     = [c_int] * 9
microfluidics.InquireVolt.argtypes = [POINTER(c_int)] * 9
microfluidics.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
microfluidics.ActivateElec.restype  = c_int

def activate(drops):
    n = len(drops)
    arr = (Drop * n)(*drops)
    microfluidics.ActivateElec(128, 128, n, arr)
    time.sleep(0.3)

# ── Constants (movement positions, unchanged from original) ───
MAIN_COL        = 5
MAIN_W          = 15
PIECE_START_COL = 30
STRETCH_STEPS   = 25
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS   # col=55
NECK_START      = MAIN_COL + MAIN_W                 # col=20
NECK_END        = PIECE_FINAL_COL - 1               # col=54

DROP1_ROW   = 55
DROP2_ROW   = 105
DROP3_ROW   = 10

MEETING_ROW = 55
MEETING_COL = 30

ROW_MAP = {
    "Drop 1 (row=55)":  DROP1_ROW,
    "Drop 2 (row=105)": DROP2_ROW,
    "Drop 3 (row=10)":  DROP3_ROW,
}


def startup_and_confirm_voltage():
    """
    Startup sequence: USB init/open, power on, set voltage,
    then query voltage to confirm the device actually responded
    correctly before doing anything else.
    """
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

    v1 = ctypes.c_int(0); v2 = ctypes.c_int(0); v3 = ctypes.c_int(0)
    v4 = ctypes.c_int(0); v5 = ctypes.c_int(0); v6 = ctypes.c_int(0)
    v7 = ctypes.c_int(0); v8 = ctypes.c_int(0); v9 = ctypes.c_int(0)
    microfluidics.InquireVolt(
        ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
        ctypes.byref(v4), ctypes.byref(v5), ctypes.byref(v6),
        ctypes.byref(v7), ctypes.byref(v8), ctypes.byref(v9)
    )
    print(
        f"Confirmed voltages: "
        f"V1={v1.value} V2={v2.value} V3={v3.value} "
        f"V4={v4.value} V5={v5.value} V6={v6.value} "
        f"V7={v7.value} V8={v8.value} V9={v9.value}"
    )

    expected = [45, 45, 45, 0, 0, 0, 0, 0, 0]
    actual   = [v1.value, v2.value, v3.value, v4.value,
                v5.value, v6.value, v7.value, v8.value, v9.value]

    if actual != expected:
        print("\n*** WARNING: voltage does not match what was set! ***")
        print(f"Expected: {expected}")
        print(f"Actual:   {actual}")
        input("Press Enter to continue anyway, or close this window to stop")
    else:
        print("\nVoltage confirmed correct -- safe to proceed")

    input("\n>>> Startup complete and voltage confirmed -- press Enter to begin loading drops")


def held_drops(finished_drops):
    """
    finished_drops is a list of (row, main_h, main_w, piece_h, piece_w)
    tuples for drops that are already split and must stay held.
    """
    drops = []
    for (row, main_h, main_w, piece_h, piece_w) in finished_drops:
        drops.append(Drop(main_h, main_w, row, MAIN_COL))
        drops.append(Drop(piece_h, piece_w, row, PIECE_FINAL_COL))
    return drops


def load_and_hold_drop(row, label, finished_drops):
    """
    Activates the starting 10x20 electrode and continuously
    re-holds it in a background thread while the user physically
    loads the drop. Stops once the user presses Enter.
    """
    print(f"\n--- LOAD AND HOLD: {label} ---")
    print(f"Starting electrode: row={row}, col={MAIN_COL}, height=10, width=20")
    print("Electrode will stay continuously held while you load the drop.\n")

    stop_holding = threading.Event()

    def hold_loop():
        while not stop_holding.is_set():
            activate(held_drops(finished_drops) + [Drop(10, 20, row, MAIN_COL)])

    hold_thread = threading.Thread(target=hold_loop, daemon=True)
    hold_thread.start()

    input(f">>> {label} loaded and in position -- press Enter to begin stretch/split sequence")

    stop_holding.set()
    hold_thread.join()
    print(f"{label} hold loop stopped -- proceeding to sequence")


def run_drop_from_csv(reader_rows, row_position, label, finished_drops):
    """
    Consumes CSV rows belonging to one drop (load/stretch/split/pinch)
    and applies them using ActivateElec, using the ORIGINAL script's
    fixed column positions (MAIN_COL, PIECE_START_COL -> PIECE_FINAL_COL)
    for placement. Volume (height/width) is driven entirely by the CSV.
    Returns the final (main_h, main_w, piece_h, piece_w) state.
    """
    pinch_counter = 0
    main_h = main_w = piece_h = piece_w = None

    for row in reader_rows:
        stage  = row['stage']
        main_h = int(row['main_height'])
        main_w = int(row['main_width'])

        if stage == "load":
            activate(held_drops(finished_drops) + [Drop(main_h, main_w, row_position, MAIN_COL)])
            print(f"{label} LOAD -- height={main_h}, width={main_w}")

        elif stage == "stretch":
            activate(held_drops(finished_drops) + [Drop(main_h, main_w, row_position, MAIN_COL)])
            print(f"{label} STRETCH -- height={main_h}, width={main_w}")

        elif stage == "split":
            piece_h = int(row['piece_height'])
            piece_w = int(row['piece_width'])
            activate(held_drops(finished_drops) + [
                Drop(main_h, main_w, row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, PIECE_START_COL),
            ])
            print(f"{label} SPLIT -- main {main_h}x{main_w}, piece {piece_h}x{piece_w}")

        elif stage == "pinch":
            piece_h = int(row['piece_height'])
            piece_w = int(row['piece_width'])
            pinch_counter += 1
            current_col = PIECE_START_COL + pinch_counter
            activate(held_drops(finished_drops) + [
                Drop(main_h, main_w, row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, current_col),
            ])
            print(f"{label} PINCH step={pinch_counter}/25 -- piece width={piece_w}")

    # ── Run neck deactivation sweep (movement-only, unchanged) ──
    print(f"{label} deactivating neck...")
    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START
        if bridge_width > 0:
            activate(held_drops(finished_drops) + [
                Drop(main_h, main_w,        row_position, MAIN_COL),
                Drop(main_h, bridge_width,  row_position, NECK_START),
                Drop(piece_h, piece_w,      row_position, PIECE_FINAL_COL),
            ])
        else:
            activate(held_drops(finished_drops) + [
                Drop(main_h, main_w,   row_position, MAIN_COL),
                Drop(piece_h, piece_w, row_position, PIECE_FINAL_COL),
            ])
    print(f"{label} neck fully deactivated -- drop split complete")

    return main_h, main_w, piece_h, piece_w


def execute_volume_csv(filepath=r"C:\Users\klmcg\Downloads\three_drop_volume_change.csv"):
    """
    Reads the three-drop volume CSV grouped by the 'drop' column.
    For each drop in order: loads and holds it for physical loading,
    then runs its load/stretch/split/pinch sequence from the CSV,
    while every already-finished drop stays held throughout.
    Returns the final state of all three drops.
    """
    drops_data = {"Drop 1 (row=55)": [], "Drop 2 (row=105)": [], "Drop 3 (row=10)": []}

    with open(filepath, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['drop'] in drops_data:
                drops_data[row['drop']].append(row)
            # merge row (drop == "All three") is handled separately below

    finished_drops = []   # list of (row_position, main_h, main_w, piece_h, piece_w)
    final_states = {}

    for label, rows_for_drop in drops_data.items():
        row_position = ROW_MAP[label]

        # ── Load and hold this drop before running its sequence ──
        load_and_hold_drop(row_position, label, finished_drops)

        main_h, main_w, piece_h, piece_w = run_drop_from_csv(
            rows_for_drop, row_position, label, finished_drops
        )

        final_states[label] = (row_position, main_h, main_w, piece_h, piece_w)
        finished_drops.append((row_position, main_h, main_w, piece_h, piece_w))

        input(f">>> {label} fully split and holding -- press Enter to continue")

    return final_states


def move_pieces_to_meet(final_states):
    """
    Moves all three pieces toward the shared meeting point
    row=55, col=30 and merges them into one combined drop.
    Main drops for all three stay held throughout.
    """
    _, main1_h, main1_w, piece_h, piece_w = final_states["Drop 1 (row=55)"]
    _, main2_h, main2_w, _, _             = final_states["Drop 2 (row=105)"]
    _, main3_h, main3_w, _, _             = final_states["Drop 3 (row=10)"]

    input(f"\n>>> All three drops split -- press Enter to move pieces toward "
          f"meeting point row={MEETING_ROW}, col={MEETING_COL}")

    # ── Phase A: align all pieces onto row=55 ───────────────────
    row_steps = max(abs(DROP2_ROW - MEETING_ROW), abs(MEETING_ROW - DROP3_ROW))

    print(f"Phase A -- aligning all pieces onto row={MEETING_ROW}...")
    for i in range(1, row_steps + 1):
        piece1_row = MEETING_ROW
        piece2_row = max(DROP2_ROW - i, MEETING_ROW)
        piece3_row = min(DROP3_ROW + i, MEETING_ROW)

        activate([
            Drop(main1_h, main1_w, DROP1_ROW, MAIN_COL),
            Drop(piece_h, piece_w, piece1_row, PIECE_FINAL_COL),
            Drop(main2_h, main2_w, DROP2_ROW, MAIN_COL),
            Drop(piece_h, piece_w, piece2_row, PIECE_FINAL_COL),
            Drop(main3_h, main3_w, DROP3_ROW, MAIN_COL),
            Drop(piece_h, piece_w, piece3_row, PIECE_FINAL_COL),
        ])
        print(f"  piece1 row={piece1_row}, piece2 row={piece2_row}, piece3 row={piece3_row}")

    input(f">>> All three pieces aligned on row={MEETING_ROW} -- press Enter to begin column convergence")
    time.sleep(1)

    # ── Phase B: move all three pieces left from col=55 to col=30 ─
    col_steps = PIECE_FINAL_COL - MEETING_COL

    print(f"Phase B -- moving all pieces left to col={MEETING_COL}...")
    for i in range(1, col_steps + 1):
        current_col = PIECE_FINAL_COL - i
        activate([
            Drop(main1_h, main1_w, DROP1_ROW,   MAIN_COL),
            Drop(piece_h, piece_w, MEETING_ROW, current_col),
            Drop(main2_h, main2_w, DROP2_ROW,   MAIN_COL),
            Drop(piece_h, piece_w, MEETING_ROW, current_col),
            Drop(main3_h, main3_w, DROP3_ROW,   MAIN_COL),
            Drop(piece_h, piece_w, MEETING_ROW, current_col),
        ])
        print(f"  all three pieces now at col={current_col}")

    input(f">>> All three pieces met at row={MEETING_ROW}, col={MEETING_COL} -- press Enter to merge")
    time.sleep(1)

    # ── Merge into one combined drop ────────────────────────────
    merged_h = piece_h * 3
    merged_w = piece_w
    activate([
        Drop(main1_h, main1_w, DROP1_ROW,   MAIN_COL),
        Drop(main2_h, main2_w, DROP2_ROW,   MAIN_COL),
        Drop(main3_h, main3_w, DROP3_ROW,   MAIN_COL),
        Drop(merged_h, merged_w, MEETING_ROW, MEETING_COL),
    ])

    final_merge_state = (main1_h, main1_w, main2_h, main2_w, main3_h, main3_w, merged_h, merged_w)
    print(f">>> All three drops merged at row={MEETING_ROW}, col={MEETING_COL}")
    return final_merge_state


def hold_final_state_forever(final_merge_state):
    """
    Continuously re-holds all three main drops and the merged drop
    in a background thread so nothing shuts off once the sequence
    finishes. Only power-off is left to the user, run manually
    and separately -- this function does NOT call SetPower(False).
    """
    main1_h, main1_w, main2_h, main2_w, main3_h, main3_w, merged_h, merged_w = final_merge_state

    print("\n--- HOLDING FINAL STATE INDEFINITELY ---")
    print(f"Main 1 held at row={DROP1_ROW}, col={MAIN_COL}, {main1_h}x{main1_w}")
    print(f"Main 2 held at row={DROP2_ROW}, col={MAIN_COL}, {main2_h}x{main2_w}")
    print(f"Main 3 held at row={DROP3_ROW}, col={MAIN_COL}, {main3_h}x{main3_w}")
    print(f"Merged drop held at row={MEETING_ROW}, col={MEETING_COL}, {merged_h}x{merged_w}")
    print("All electrodes will remain continuously active.")
    print("Press Enter ONLY if you want to manually power off -- otherwise leave running.\n")

    stop_holding = threading.Event()

    def hold_loop():
        while not stop_holding.is_set():
            activate([
                Drop(main1_h, main1_w, DROP1_ROW,   MAIN_COL),
                Drop(main2_h, main2_w, DROP2_ROW,   MAIN_COL),
                Drop(main3_h, main3_w, DROP3_ROW,   MAIN_COL),
                Drop(merged_h, merged_w, MEETING_ROW, MEETING_COL),
            ])

    hold_thread = threading.Thread(target=hold_loop, daemon=True)
    hold_thread.start()

    input(">>> Drops are held in place indefinitely -- press Enter ONLY when ready to power off")

    stop_holding.set()
    hold_thread.join()
    print("Hold loop stopped by user request")


def main():
    # ── Step 1: Startup and voltage confirmation BEFORE anything else
    startup_and_confirm_voltage()

    # ── Step 2: Run all three drops through the CSV sequence ────
    final_states = execute_volume_csv()

    # ── Step 3: Move pieces to meet and merge ───────────────────
    final_merge_state = move_pieces_to_meet(final_states)

    # ── Step 4: Hold final state indefinitely until user chooses to stop
    hold_final_state_forever(final_merge_state)

    # ── Step 5: Shutdown -- only runs if user pressed Enter above ─
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()