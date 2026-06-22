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

microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")


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

#ensures that there is a common format for the activation throughout script
def activate(drops):
    n = len(drops)
    arr = (Drop * n)(*drops)
    microfluidics.ActivateElec(128, 128, n, arr)
    time.sleep(0.5)


# ── Constants (movement positions) ───────────────────────────
DROP_ROW        = 55
MAIN_COL        = 5
MAIN_W          = 15
PIECE_START_COL = 30
PIECE_FINAL_COL = PIECE_START_COL + 25  # col=55 (25 pinch steps)
NECK_START      = MAIN_COL + MAIN_W     # col=20
NECK_END        = PIECE_FINAL_COL - 1   # col=54


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

    voltages = [ctypes.c_int(0) for _ in range(9)]
    microfluidics.InquireVolt(*[ctypes.byref(v) for v in voltages])
    actual = [v.value for v in voltages]
    print("Confirmed voltages: " + " ".join(f"V{i+1}={actual[i]}" for i in range(9)))

    expected = [45, 45, 45, 0, 0, 0, 0, 0, 0]
    if actual != expected:
        print("\n*** WARNING: voltage does not match what was set! ***")
        print(f"Expected: {expected}")
        print(f"Actual:   {actual}")
        input("Press Enter to continue anyway, or close this window to stop")
    else:
        print("\nVoltage confirmed correct -- safe to proceed")

    input("\n>>> Startup complete and voltage confirmed -- press Enter to load the drop")

#Note: When making masterscript, there values will need to be adjusted to reflect upon the final dimensions chosen
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
    height & width, while using fixed movement positions (DROP_ROW,
    MAIN_COL, PIECE_START_COL -> PIECE_FINAL_COL) for row/col placement.
    Movement is identical to the original script; volume is driven by the CSV.

    Expected CSV columns: stage, main_height, main_width, piece_height, piece_width
    Valid stage values: load, stretch, split, pinch
    piece_height and piece_width are only required for split and pinch rows.

    Returns (final_main, final_piece) tuples for the neck deactivation step.
    """
    pinch_step_counter = 0
    last_main  = None
    last_piece = None

    with open(filepath, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        rows = list(reader)

    if not rows:
        raise ValueError(f"CSV file is empty: {filepath}")

    for csv_row in rows:
        stage  = csv_row['stage']
        main_h = int(csv_row['main_height'])
        main_w = int(csv_row['main_width'])

        if stage in ("load", "stretch"):
            activate([Drop(main_h, main_w, DROP_ROW, MAIN_COL)])
            print(f"{stage.upper()} -- main height={main_h}, width={main_w}")
            last_main = (main_h, main_w, DROP_ROW, MAIN_COL)

        elif stage == "split":
            piece_h = int(csv_row['piece_height'])
            piece_w = int(csv_row['piece_width'])
            activate([
                Drop(main_h, main_w, DROP_ROW, MAIN_COL),
                Drop(piece_h, piece_w, DROP_ROW, PIECE_START_COL),
            ])
            print(
                f"SPLIT -- main {main_h}x{main_w} at col={MAIN_COL}, "
                f"piece {piece_h}x{piece_w} at col={PIECE_START_COL}"
            )
            last_main  = (main_h, main_w, DROP_ROW, MAIN_COL)
            last_piece = (piece_h, piece_w, DROP_ROW, PIECE_START_COL)

        elif stage == "pinch":
            piece_h = int(csv_row['piece_height'])
            piece_w = int(csv_row['piece_width'])
            pinch_step_counter += 1
            current_col = PIECE_START_COL + pinch_step_counter
            activate([
                Drop(main_h, main_w, DROP_ROW, MAIN_COL),
                Drop(piece_h, piece_w, DROP_ROW, current_col),
            ])
            print(
                f"PINCH step {pinch_step_counter}/25 -- "
                f"piece at col={current_col}, width={piece_w}"
            )
            last_main  = (main_h, main_w, DROP_ROW, MAIN_COL)
            last_piece = (piece_h, piece_w, DROP_ROW, current_col)

        else:
            print(f"*** WARNING: unknown stage '{stage}' -- skipping row")

        time.sleep(0.3)

    if last_main is None or last_piece is None:
        raise ValueError("CSV completed without reaching a split or pinch stage -- cannot determine final drop positions")

    # Snap piece column to PIECE_FINAL_COL for the neck deactivation step
    ph, pw, pr, _ = last_piece
    last_piece = (ph, pw, pr, PIECE_FINAL_COL)

    return last_main, last_piece


def deactivate_neck_and_hold_apart(final_main, final_piece):
    """
    Runs the neck-deactivation sweep (col=54 down to col=20), then
    continuously re-holds both the main drop and the piece in a background
    thread so they stay separated until the user is ready to power off.
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

    # ── Step 3: Run volume CSV ──────────────────────────────────
    print("\nLoading volume instructions from: C:\\Users\\klmcg\\Downloads\\drop_volume_change.csv")
    final_main, final_piece = execute_volume_csv()
    print("\nVolume sequence complete")

    # ── Step 4: Deactivate neck and hold drops apart ───────────
    deactivate_neck_and_hold_apart(final_main, final_piece)

    # ── Step 5: Shutdown ────────────────────────────────────────
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()