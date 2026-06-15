"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List
import time

# Load library
microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")

# Define structure Drop
class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width", ctypes.c_int),
        ("row", ctypes.c_int),
        ("col", ctypes.c_int),
    ]



# load function

# Use function
def main():
    # initialization USB
    microfluidics.InitUSB()
    add = 0
    res = microfluidics.OpenUSB()
    if res:
        user_input = input("Open successfully: ")
    else:
        user_input = input("Open failed: ")

    buffer_size = 256
    buffer = (ctypes.c_uint8 * buffer_size)()

    res = microfluidics.SetPower(True)
    user_input = input("Power on completed")

   
    # Voltage set to 75 on first three, 0 on the rest
    res = microfluidics.SetVolt(75, 75, 75, 0, 0, 0, 0, 0, 0)
    user_input = input("Setting voltage is completed")

    v1 = ctypes.c_int(1)
    v2 = ctypes.c_int(2)
    v3 = ctypes.c_int(3)
    v4 = ctypes.c_int(4)
    v5 = ctypes.c_int(5)
    v6 = ctypes.c_int(6)
    v7 = ctypes.c_int(7)
    v8 = ctypes.c_int(8)
    v9 = ctypes.c_int(9)
    res = microfluidics.InquireVolt(ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3), ctypes.byref(v4),
                                   ctypes.byref(v5), ctypes.byref(v6), ctypes.byref(v7), ctypes.byref(v8),
                                   ctypes.byref(v9))
    print(res)
    print(v1, v2, v3, v4, v5, v6, v7, v8, v9)

    # Display current voltage settings clearly
    print(f"Current voltage settings:")
    print(f"  V1: {v1.value} | V2: {v2.value} | V3: {v3.value}")
    print(f"  V4: {v4.value} | V5: {v5.value} | V6: {v6.value}")
    print(f"  V7: {v7.value} | V8: {v8.value} | V9: {v9.value}")
    user_input = input("Query voltage command completed")
  # ── DROP 1 (top) ──────────────────────────────────────────────

    # Load drop 1 at row=55, col=5
    num_drops = 1
    drops_array = (Drop * num_drops)(
        Drop(20, 20, 55, 5),
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drop 1 loaded at row=55 -- ready for splitting")

    time.sleep(2)

    # Stretch drop 1 to the right pixel by pixel
    for i in range(1, 6):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(20, 20 + i, 55, 5),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 1 stretching, width={20+i}")

    time.sleep(2)

    # Split drop 1 into 15x15 main and 5x5 piece
    num_drops = 2
    drops_array = (Drop * num_drops)(
        Drop(15, 15, 55, 5),
        Drop(5, 5, 55, 21),
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drop 1 split -- 5x5 piece forming")

    time.sleep(2)

    # Move drop 1 5x5 piece to the right 30 pixels
    for i in range(31):
        num_drops = 2
        drops_array = (Drop * num_drops)(
            Drop(15, 15, 55, 5),
            Drop(5, 5, 55, 21 + i),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 1 piece moving right, now at col={21+i}")

    time.sleep(2)

    # ── DROP 2 (bottom) ───────────────────────────────────────────

    # Load drop 2 at row=85, col=5
    num_drops = 1
    drops_array = (Drop * num_drops)(
        Drop(20, 20, 85, 5),
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drop 2 loaded at row=85 -- ready for splitting")

    time.sleep(2)

    # Stretch drop 2 to the right pixel by pixel
    for i in range(1, 6):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(20, 20 + i, 85, 5),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 2 stretching, width={20+i}")

    time.sleep(2)

    # Split drop 2 into 15x15 main and 5x5 piece
    num_drops = 2
    drops_array = (Drop * num_drops)(
        Drop(15, 15, 85, 5),
        Drop(5, 5, 85, 21),
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drop 2 split -- 5x5 piece forming")

    time.sleep(2)

    # Move drop 2 5x5 piece to the right 30 pixels
    for i in range(31):
        num_drops = 2
        drops_array = (Drop * num_drops)(
            Drop(15, 15, 85, 5),
            Drop(5, 5, 85, 21 + i),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 2 piece moving right, now at col={21+i}")

    time.sleep(2)

    # ── MIX ───────────────────────────────────────────────────────

    # Move both 5x5 pieces toward each other vertically to mix
    # piece 1 at row=55 moves down, piece 2 at row=85 moves up
    # they meet at row=70
    for i in range(16):
        # move top piece down
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(5, 5, 55 + i, 51),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)

        time.sleep(0.5)

        # move bottom piece up
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(5, 5, 85 - i, 51),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Piece 1 at row={55+i}, Piece 2 at row={85-i}")

    user_input = input("5x5 pieces have met and are mixing at row=70, col=51")

    # Shutdown
    res = microfluidics.SetPower(False)
    user_input = input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()