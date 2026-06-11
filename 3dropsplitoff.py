"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
import time
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List

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

    res = microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
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
    user_input = input("Query voltage command completed")

    
    # Load and hold main drop at starting position (20x20 at center)
    num_drops = 1
    drops_array = (Drop * num_drops)(
        Drop(20, 20, 55, 55),)
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drop loaded -- ready for splitting")

    time.sleep(2)

    # Step 1 — initiate split, main drop shrinks to 15x15, 5x5 piece forms at edge
    num_drops = 2
    drops_array = (Drop * num_drops)(
        Drop(15, 15, 55, 55),   # main drop shrinks to 15x15 to reflect lost volume
        Drop(5, 5, 55, 71),     # 5x5 piece appears at right edge of 15x15 drop
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Split initiated -- main drop is now 15x15, 5x5 piece forming")

    time.sleep(2)

    # Step 2 — move the 5x5 piece away to the right while main drop stays fixed at 15x15
    for i in range(31):
        num_drops = 2
        drops_array = (Drop * num_drops)(
            Drop(15, 15, 55, 55),      # main drop stays fixed at 15x15
            Drop(5, 5, 55, 71 + i),    # 5x5 piece moves right 30 pixels
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"5x5 piece moving right, now at col={71+i}")

    # Shutdown
    res = microfluidics.SetPower(False)
    user_input = input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()