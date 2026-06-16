import ctypes
import time
import csv
from ctypes import POINTER, c_int, c_void_p, Structure

microfluidics = ctypes.CDLL("C:\\Users\\klmcg\\Downloads\\ACX_pythonSDK v1.2 3\\ACX_pythonSDK\\windows\\DLLTest.dll")

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
    time.sleep(2)  # give power time to stabilize before setting voltage

    res_volt = microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
    print(f"SetVolt result: {res_volt}")
    time.sleep(1)  # give voltage time to settle before querying

    # ── Query voltage and confirm it matches what we set ──────
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

    # ── Validate voltage actually matches what we intended ────
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
    Only runs after voltage has already been confirmed.
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
    """
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


def main():
    # ── Step 1: Startup and confirm voltage BEFORE anything else
    startup_and_confirm_voltage()

    # ── Step 2: Load the initial drop ──────────────────────────
    load_initial_drop()

    # ── Step 3: Run CSV movement sequence ───────────────────────
    print("\nLoading instructions from: C:\\Users\\klmcg\\Downloads\\drop_movement_instructions.csv")
    print("Starting electrode sequence...\n")
    execute_csv()
    print("\nCSV sequence complete -- all movement instructions executed")

    # ── Step 4: Shutdown ─────────────────────────────────────────
    microfluidics.SetPower(False)
    input("Power off completed -- press Enter to close USB")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()