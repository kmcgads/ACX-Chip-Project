"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.

To use this chip, the user must purchase the hardware from ACX Instruments. ACX provides the required starter software and DLL files with the purchased device.

Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. The placeholder below represents where the ACX-provided DLL would be loaded."""

import ctypes
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

    # Load and hold drop at starting position
    num_drops = 1
    drops_array = (Drop * num_drops)(
        Drop(10, 10, 5, 5),)
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drop loaded -- ready for movement")

    # Move right 100 pixels
    for col in range(5, 106):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(10, 10, 5, col),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input("Drop moved right")

    # Move down 40 pixels
    for row in range(5, 46):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(10, 10, row, 105),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input("Drop moved down")

    # Move left 70 pixels
    for col in range(105, 34, -1):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(10, 10, 45, col),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input("Drop moved left")

    # Move down 40 pixels
    for row in range(45, 86):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(10, 10, row, 35),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input("Drop moved down")

    # Move right 60 pixels
    for col in range(35, 96):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(10, 10, 85, col),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input("Drop moved right")

    # Move down 20 pixels
    for row in range(85, 106):
        num_drops = 1
        drops_array = (Drop * num_drops)(
            Drop(10, 10, row, 95),
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input("Drop moved down")

    # Shutdown
    res = microfluidics.SetPower(False)
    user_input = input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()

    """Below this is the code for the pathway of two drops that merge. This was the second test used. This is different from the above"""

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

    # Load and hold both drops at starting positions
    num_drops = 2
    drops_array = (Drop * num_drops)(
        Drop(10, 10, 5, 5),        # drop 1 starts at row=5, col=5
        Drop(10, 10, 105, 105),    # drop 2 starts at row=105, col=105
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Drops loaded -- ready for movement")

    # Drop 1 moves right 100 pixels (col 5 to 105)
    # Drop 2 moves left 100 pixels (col 105 to 5)
    for i in range(101):
        num_drops = 2
        drops_array = (Drop * num_drops)(
            Drop(10, 10, 5, 5 + i),       # drop 1 moves right
            Drop(10, 10, 105, 105 - i),   # drop 2 moves left
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 1 at col={5+i}, Drop 2 at col={105-i}")

    time.sleep(2)  # pause before next movement

    # Drop 1 moves down 50 pixels (row 5 to 55, stays at col=105)
    # Drop 2 moves up 50 pixels (row 105 to 55, stays at col=5)
    for i in range(51):
        num_drops = 2
        drops_array = (Drop * num_drops)(
            Drop(10, 10, 5 + i, 105),    # drop 1 moves down, stays at col=105
            Drop(10, 10, 105 - i, 5),    # drop 2 moves up, stays at col=5
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 1 at row={5+i}, Drop 2 at row={105-i}")

    time.sleep(2)  # pause before next movement

    # Drop 1 (top, at row=55) moves left 50 pixels (col 105 to 55)
    # Drop 2 (bottom, at row=55) moves right 50 pixels (col 5 to 55)
    for i in range(51):
        num_drops = 2
        drops_array = (Drop * num_drops)(
            Drop(10, 10, 55, 105 - i),   # drop 1 moves left, stays at row=55
            Drop(10, 10, 55, 5 + i),     # drop 2 moves right, stays at row=55
        )
        res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
        user_input = input(f"Drop 1 at col={105-i}, Drop 2 at col={5+i}")

    # Shutdown
    res = microfluidics.SetPower(False)
    user_input = input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()
