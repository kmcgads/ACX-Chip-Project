"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
import time
import csv
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List

# Load library
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")

class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]

# ── Fix argtypes so ctypes passes arguments correctly ─────────
microfluidics.SetPower.argtypes    = [ctypes.c_bool]
microfluidics.SetVolt.argtypes     = [c_int] * 9
microfluidics.InquireVolt.argtypes = [POINTER(c_int)] * 9
microfluidics.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
microfluidics.ActivateElec.restype  = c_int


def startup_and_confirm_voltage():
    """
    Runs the full startup sequence in the correct order:
    1. Init USB
    2. Open USB
    3. Power on
    4. Set voltage
    5. Query voltage and confirm it actually matches what was set
    Only returns once voltage is confirmed -- everything else
    in the script depends on this being true first.
    """
    print("--- STARTUP SEQUENCE ---")

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

    input("\n>>> Startup complete and voltage confirmed -- press Enter to load initial drop")


def load_initial_drop():
    """
    Activates the starting electrode at row=5, col=5, size=10x10
    and holds it so the user can physically load the drop onto the chip.
    """
    print("\n--- LOAD INITIAL DROP ---")
    print("Activating starting electrode: row=5, col=5, height=10, width=10")

    num_drops = 1
    drops_array = (Drop * num_drops)(
        Drop(10, 10, 5, 5),
    )

    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    print(f"ActivateElec result: {res}")

    if not res:
        print("*** WARNING: ActivateElec returned 0 -- electrode may not be on ***")

    print("Starting electrode is now active -- place your drop on the chip")
    input("\n>>> Drop loaded and in position -- press Enter to begin movement sequence")


def execute_csv(filepath=r"C:\Users\klmcg\Downloads\drop_movement_instructions.csv"):
    """
    Reads the CSV and executes each row as an ActivateElec call.
    Skips the shutdown row -- handled separately at the end.
    Returns the final drop position (height, width, row, col) so it
    can be held in place after the sequence finishes.
    """
    final_position = None

    with open(filepath, newline='') as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            step   = int(row['step'])
            action = row['action']
            r      = int(row['row'])
            col    = int(row['col'])
            height = int(row['height'])
            width  = int(row['width'])
            notes  = row['notes']

            if action == 'shutdown':
                print(f"Step {step}: shutdown row reached -- ending electrode loop")
                break

            print(
                f"Step {step:03d} | {action:<12} | "
                f"row={r:>4}, col={col:>4}, "
                f"height={height}, width={width}"
                + (f" | {notes}" if notes else "")
            )

            num_drops = 1
            drops_array = (Drop * num_drops)(
                Drop(height, width, r, col),
            )
            microfluidics.ActivateElec(128, 128, num_drops, drops_array)
            time.sleep(0.3)

            final_position = (height, width, r, col)

    return final_position


def hold_final_position(final_position):
    """
    Re-activates and holds the drop at its final position after
    the CSV sequence completes. Waits for user input before
    proceeding to shutdown.
    """
    if final_position is None:
        print("No final position recorded -- skipping hold step")
        return

    height, width, row, col = final_position

    print("\n--- HOLDING FINAL POSITION ---")
    print(f"Holding drop at row={row}, col={col}, height={height}, width={width}")

    num_drops = 1
    drops_array = (Drop * num_drops)(
        Drop(height, width, row, col),
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    print(f"ActivateElec result: {res}")

    print("Drop is being held in place at its final position.")
    input("\n>>> Press Enter when ready to power off")


def main():
    # ── Step 1: Startup and confirm voltage BEFORE anything else
    startup_and_confirm_voltage()

    # ── Step 2: Load the initial drop ──────────────────────────
    load_initial_drop()

    # ── Step 3: Run CSV movement sequence ───────────────────────
    print("\nLoading instructions from: C:\\Users\\klmcg\\Downloads\\drop_movement_instructions.csv")
    print("Starting electrode sequence...\n")
    final_position = execute_csv()
    print("\nCSV sequence complete -- all movement instructions executed")

    # ── Step 4: Hold drop at final position until user is ready
    hold_final_position(final_position)

    # ── Step 5: Shutdown ─────────────────────────────────────────
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()

"""The next section of code deals with two drops being loaded and moved and than merged together using csv"""

import ctypes
import time
import csv
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List

# Load library
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")

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


def startup_and_confirm_voltage():
    print("--- STARTUP SEQUENCE ---")

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

    input("\n>>> Startup complete and voltage confirmed -- press Enter to load both drops")


def load_and_hold_initial_drops():
    """
    Activates both starting electrodes and continuously re-holds
    them in a loop while the user loads the physical drops onto
    the chip. The hold loop only ends once the user presses Enter,
    and it keeps re-sending ActivateElec every 0.5s the entire time
    so the electrodes never lapse while waiting.
    """
    print("\n--- LOAD AND HOLD INITIAL DROPS ---")
    print("Drop 1 starting electrode: row=5,   col=5,   height=10, width=10")
    print("Drop 2 starting electrode: row=105, col=105, height=10, width=10")
    print("Electrodes will stay continuously held while you load the drops.")
    print("Press Enter once both drops are loaded and ready to move.\n")

    import threading

    stop_holding = threading.Event()

    def hold_loop():
        while not stop_holding.is_set():
            num_drops = 2
            drops_array = (Drop * num_drops)(
                Drop(10, 10, 5, 5),
                Drop(10, 10, 105, 105),
            )
            microfluidics.ActivateElec(128, 128, num_drops, drops_array)
            time.sleep(0.5)

    hold_thread = threading.Thread(target=hold_loop, daemon=True)
    hold_thread.start()

    input(">>> Both drops loaded and in position -- press Enter to begin movement sequence")

    stop_holding.set()
    hold_thread.join()

    print("Hold loop stopped -- proceeding to movement sequence")


def execute_csv(filepath=r"C:\Users\klmcg\Downloads\two_drop_movement_instructions.csv"):
    """
    Reads the two-drop CSV and executes each step. Each step pairs
    up the drop=1 and drop=2 rows for the same movement moment and
    sends them together in a single ActivateElec call, matching the
    original script's behavior of moving both drops simultaneously.
    """
    pending = {}
    final_positions = {1: None, 2: None}

    with open(filepath, newline='') as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            step   = row['step']
            action = row['action']
            drop   = row['drop']
            notes  = row['notes']

            if drop == "both" and action == "pause":
                print(f"Step {step}: PAUSE -- {notes}")
                time.sleep(2)
                continue

            if action == "shutdown":
                print(f"Step {step}: shutdown row reached -- ending electrode loop")
                break

            r      = int(row['row'])
            col    = int(row['col'])
            height = int(row['height'])
            width  = int(row['width'])
            drop_num = int(drop)

            pending[drop_num] = Drop(height, width, r, col)
            final_positions[drop_num] = (height, width, r, col)

            if 1 in pending and 2 in pending:
                num_drops = 2
                drops_array = (Drop * num_drops)(
                    pending[1],
                    pending[2],
                )
                microfluidics.ActivateElec(128, 128, num_drops, drops_array)

                print(
                    f"Step {step:>5} | {action:<10} | drop={drop} | "
                    f"D1: row={pending[1].row}, col={pending[1].col} | "
                    f"D2: row={pending[2].row}, col={pending[2].col}"
                    + (f" | {notes}" if notes else "")
                )

                time.sleep(0.3)
                pending = {}

    return final_positions


def hold_final_positions(final_positions):
    pos1 = final_positions.get(1)
    pos2 = final_positions.get(2)

    if pos1 is None or pos2 is None:
        print("Missing final position data -- skipping hold step")
        return

    h1, w1, r1, c1 = pos1
    h2, w2, r2, c2 = pos2

    print("\n--- HOLDING FINAL POSITIONS ---")
    print(f"Drop 1 held at row={r1}, col={c1}, height={h1}, width={w1}")
    print(f"Drop 2 held at row={r2}, col={c2}, height={h2}, width={w2}")

    num_drops = 2
    drops_array = (Drop * num_drops)(
        Drop(h1, w1, r1, c1),
        Drop(h2, w2, r2, c2),
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    print(f"ActivateElec result: {res}")

    print("Both drops are being held in place at their final positions.")
    input("\n>>> Press Enter when ready to power off")


def main():
    startup_and_confirm_voltage()

    load_and_hold_initial_drops()

    print("\nLoading instructions from: C:\\Users\\klmcg\\Downloads\\two_drop_movement_instructions.csv")
    print("Starting electrode sequence...\n")
    final_positions = execute_csv()
    print("\nCSV sequence complete -- all movement instructions executed")

    hold_final_positions(final_positions)

    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()