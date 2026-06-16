"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
import time
import csv
import threading
from ctypes import POINTER, c_int, c_void_p, Structure

microfluidics =  ctypes.CDLL("path_to_ACX_provided_DLL")

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
    time.sleep(0.5)

# ── Constants (movement positions, unchanged from original) ───
DROP_ROW        = 55
MAIN_COL        = 5
MAIN_W          = 15
PIECE_START_COL = 30
STRETCH_STEPS   = 25
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS   # col=55
NECK_START      = MAIN_COL + MAIN_W                 # col=20
NECK_END        = PIECE_FINAL_COL - 1               # col=54


def startup_and_confirm_voltage():
    """
    Startup sequence: USB init/open, power on, set voltage,
    then query voltage to confirm the device actually responded
    correctly before doing anything else.
    """
    print("--- STARTUP & VOLTAGE TEST ---")

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

    input("\n>>> Startup complete and voltage confirmed -- press Enter to load the drop")


def load_and_hold_start_drop():
    """
    Activates the starting electrode (10 tall x 20 wide at row=55, col=5)
    and continuously re-holds it in a background thread while the user
    physically loads the drop. Stops once the user presses Enter.
    """
    print("\n--- LOAD AND HOLD STARTING DROP ---")
    print(f"Starting electrode: row={DROP_ROW}, col={MAIN_COL}, height=10, width=20")
    print("Electrode will stay continuously held while you load the drop.\n")

    stop_holding = threading.Event()

    def hold_loop():
        while not stop_holding.is_set():
            activate([Drop(10, 20, DROP_ROW, MAIN_COL)])

    hold_thread = threading.Thread(target=hold_loop, daemon=True)
    hold_thread.start()

    input(">>> Drop loaded and in position -- press Enter to begin volume/movement sequence")

    stop_holding.set()
    hold_thread.join()
    print("Hold loop stopped -- proceeding to sequence")


def execute_volume_csv(filepath=r"C:\Users\klmcg\Downloads\drop_volume_change.csv"):
    """
    Reads the volume-change CSV and applies each row's main/piece
    height & width, while using the ORIGINAL script's fixed movement
    positions (DROP_ROW, MAIN_COL, PIECE_START_COL -> PIECE_FINAL_COL)
    for row/col placement. This keeps movement identical to the
    original script while volume is driven entirely by the CSV.
    """
    pinch_step_counter = 0  # tracks which pinch step we are on, for column placement

    with open(filepath, newline='') as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            stage = row['stage']
            main_h = int(row['main_height'])
            main_w = int(row['main_width'])

            if stage == "load":
                activate([Drop(main_h, main_w, DROP_ROW, MAIN_COL)])
                print(f"LOAD -- main height={main_h}, width={main_w}")

            elif stage == "stretch":
                activate([Drop(main_h, main_w, DROP_ROW, MAIN_COL)])
                print(f"STRETCH -- main height={main_h}, width={main_w}")

            elif stage == "split":
                piece_h = int(row['piece_height'])
                piece_w = int(row['piece_width'])
                activate([
                    Drop(main_h, main_w, DROP_ROW, MAIN_COL),
                    Drop(piece_h, piece_w, DROP_ROW, PIECE_START_COL),
                ])
                print(
                    f"SPLIT -- main {main_h}x{main_w} at col={MAIN_COL}, "
                    f"piece {piece_h}x{piece_w} at col={PIECE_START_COL}"
                )

            elif stage == "pinch":
                piece_h = int(row['piece_height'])
                piece_w = int(row['piece_width'])
                pinch_step_counter += 1
                current_col = PIECE_START_COL + pinch_step_counter  # matches original movement path

                activate([
                    Drop(main_h, main_w, DROP_ROW, MAIN_COL),
                    Drop(piece_h, piece_w, DROP_ROW, current_col),
                ])
                print(
                    f"PINCH step {pinch_step_counter}/25 -- "
                    f"piece at col={current_col}, width={piece_w}"
                )

            time.sleep(0.3)

    # Return final main + piece state for the hold-apart step
    final_main  = (main_h, main_w, DROP_ROW, MAIN_COL)
    final_piece = (int(row['piece_height']), int(row['piece_width']), DROP_ROW, PIECE_FINAL_COL)
    return final_main, final_piece


def deactivate_neck_and_hold_apart(final_main, final_piece):
    """
    Runs the original neck-deactivation sweep (movement-only logic,
    unchanged from the original script), then continuously re-holds
    both the main drop and the piece in a background thread so they
    stay visibly separated until the user is ready to proceed.
    """
    main_h, main_w, main_row, main_col = final_main
    piece_h, piece_w, piece_row, piece_col = final_piece

    print("\n--- DEACTIVATING NECK ---")
    for release_col in range(NECK_END, NECK_START - 1, -1):
        bridge_width = release_col - NECK_START

        if bridge_width > 0:
            activate([
                Drop(main_h, main_w, main_row, main_col),
                Drop(main_h, bridge_width, main_row, NECK_START),
                Drop(piece_h, piece_w, piece_row, piece_col),
            ])
        else:
            activate([
                Drop(main_h, main_w, main_row, main_col),
                Drop(piece_h, piece_w, piece_row, piece_col),
            ])

        print(f"col={release_col} released -- bridge remaining={bridge_width}")

    print("Neck fully deactivated -- drops independent")

    # ── Hold both drops apart continuously until user is ready ──
    print("\n--- HOLDING DROPS APART ---")
    print(f"Main drop held at row={main_row}, col={main_col}, {main_h}x{main_w}")
    print(f"Piece held at row={piece_row}, col={piece_col}, {piece_h}x{piece_w}")
    print("Both will stay continuously held and separated.\n")

    stop_holding = threading.Event()

    def hold_apart_loop():
        while not stop_holding.is_set():
            activate([
                Drop(main_h, main_w, main_row, main_col),
                Drop(piece_h, piece_w, piece_row, piece_col),
            ])

    hold_thread = threading.Thread(target=hold_apart_loop, daemon=True)
    hold_thread.start()

    input(">>> Drops are held apart -- press Enter when ready to power off")

    stop_holding.set()
    hold_thread.join()
    print("Hold-apart loop stopped")


def main():
    # ── Step 1: Startup and voltage test ───────────────────────
    startup_and_confirm_voltage()

    # ── Step 2: Load and hold the starting drop ────────────────
    load_and_hold_start_drop()

    # ── Step 3: Run volume CSV with original movement positions ─
    print("\nLoading volume instructions from: C:\\Users\\klmcg\\Downloads\\drop_volume_change.csv")
    final_main, final_piece = execute_volume_csv()
    print("\nVolume sequence complete")

    # ── Step 4: Deactivate neck and hold drops apart ───────────
    deactivate_neck_and_hold_apart(final_main, final_piece)

    # ── Step 5: Shutdown ─────────────────────────────────────────
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()