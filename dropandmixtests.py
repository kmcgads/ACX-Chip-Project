# """The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
# To use this chip, the user must purchase the hardware from ACX Instruments. 
# ACX provides the required starter software and DLL files with the purchased device.
# Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. 
# The placeholder below represents where the ACX-provided DLL would be loaded."""

# import ctypes
# from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
# from typing import List

# # Load library
# microfluidics = ctypes.CDLL("path_to_ACX_provided_DLL")
# """This script relies on user input of the enter key to continue on after beginning the run to ensure for the best 
# possible quality for the drops and their movement."""

# # Define structure Drop
# class Drop(Structure):
#     _fields_ = [
#         ("height", ctypes.c_int),
#         ("width", ctypes.c_int),
#         ("row", ctypes.c_int),
#         ("col", ctypes.c_int),
#     ]


# # load function + use it
# def main():
#     # initialization USB and confirms established connection
#     microfluidics.InitUSB()
#     add = 0
#     res = microfluidics.OpenUSB()
#     if res:
#         user_input = input("Open successfully: ")
#     else:
#         user_input = input("Open failed: ")
#     #Spares space in USB for device usage 
#     buffer_size = 256
#     buffer = (ctypes.c_uint8 * buffer_size)()
#     #Confirms power is being supplied to device
#     res = microfluidics.SetPower(True)
#     user_input = input("Power on completed")
#     #Sets device voltage of 45 (high volt) across the grid
#     res = microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
#     user_input = input("Setting voltage is completed")

#     v1 = ctypes.c_int(1)
#     v2 = ctypes.c_int(2)
#     v3 = ctypes.c_int(3)
#     v4 = ctypes.c_int(4)
#     v5 = ctypes.c_int(5)
#     v6 = ctypes.c_int(6)
#     v7 = ctypes.c_int(7)
#     v8 = ctypes.c_int(8)
#     v9 = ctypes.c_int(9)
#     res = microfluidics.InquireVolt(ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3), ctypes.byref(v4),
#                                    ctypes.byref(v5), ctypes.byref(v6), ctypes.byref(v7), ctypes.byref(v8),
#                                    ctypes.byref(v9))
#     #prints out voltages for confirmation
#     print(res)
#     print(v1, v2, v3, v4, v5, v6, v7, v8, v9)
#     user_input = input("Query voltage command completed")

#     # Load and hold drop at starting position before sequence starts
#     num_drops = 1
#     drops_array = (Drop * num_drops)(
#         Drop(10, 10, 5, 5),)
#     res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#     user_input = input("Drop loaded -- ready for movement")

#     # Move right 100 pixels
#     for col in range(5, 106):
#         num_drops = 1
#         drops_array = (Drop * num_drops)(
#             Drop(10, 10, 5, col),
#         )
#         res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#         user_input = input("Drop moved right")

#     # Move down 40 pixels
#     for row in range(5, 46):
#         num_drops = 1
#         drops_array = (Drop * num_drops)(
#             Drop(10, 10, row, 105),
#         )
#         res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#         user_input = input("Drop moved down")

#     # Move left 70 pixels
#     for col in range(105, 34, -1):
#         num_drops = 1
#         drops_array = (Drop * num_drops)(
#             Drop(10, 10, 45, col),
#         )
#         res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#         user_input = input("Drop moved left")

#     # Move down 40 pixels
#     for row in range(45, 86):
#         num_drops = 1
#         drops_array = (Drop * num_drops)(
#             Drop(10, 10, row, 35),
#         )
#         res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#         user_input = input("Drop moved down")

#     # Move right 60 pixels
#     for col in range(35, 96):
#         num_drops = 1
#         drops_array = (Drop * num_drops)(
#             Drop(10, 10, 85, col),
#         )
#         res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#         user_input = input("Drop moved right")

#     # Move down 20 pixels
#     for row in range(85, 106):
#         num_drops = 1
#         drops_array = (Drop * num_drops)(
#             Drop(10, 10, row, 95),
#         )
#         res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
#         user_input = input("Drop moved down")

#     # Shutdown and release drop from the grid
#     res = microfluidics.SetPower(False)
#     user_input = input("Power off completed")
#     microfluidics.CloseUSB()


# if __name__ == "__main__":
#     main()

"""Below this is the code for the pathway of two drops that merge. 
This was the second test used. This is different from the above"""

import ctypes
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List
import time

# Load library
microfluidics = ctypes.CDLL(r"C:\Users\klmcg\Downloads\ACX_pythonSDK v1.2 3\ACX_pythonSDK\windows\DLLTest.dll")

# Define structure Drop
class Drop(Structure):
    _fields_ = [
        ("height", ctypes.c_int),
        ("width",  ctypes.c_int),
        ("row",    ctypes.c_int),
        ("col",    ctypes.c_int),
    ]


def activate_one(h, w, row, col):
    """Activate a single drop with a short settle delay."""
    arr = (Drop * 1)(Drop(h, w, row, col))
    microfluidics.ActivateElec(128, 128, 1, arr)
    time.sleep(0.3)


def main():
    # ── Initialization ────────────────────────────────────────────
    microfluidics.InitUSB()
    res = microfluidics.OpenUSB()
    if res:
        input("Open successfully: ")
    else:
        input("Open failed: ")

    buffer_size = 256
    buffer = (ctypes.c_uint8 * buffer_size)()

    microfluidics.SetPower(True)
    input("Power on completed")

    microfluidics.SetVolt(45, 45, 45, 0, 0, 0, 0, 0, 0)
    input("Setting voltage is completed")

    v1 = ctypes.c_int(1); v2 = ctypes.c_int(2); v3 = ctypes.c_int(3)
    v4 = ctypes.c_int(4); v5 = ctypes.c_int(5); v6 = ctypes.c_int(6)
    v7 = ctypes.c_int(7); v8 = ctypes.c_int(8); v9 = ctypes.c_int(9)
    microfluidics.InquireVolt(
        ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
        ctypes.byref(v4), ctypes.byref(v5), ctypes.byref(v6),
        ctypes.byref(v7), ctypes.byref(v8), ctypes.byref(v9),
    )
    print(v1, v2, v3, v4, v5, v6, v7, v8, v9)
    input("Query voltage command completed")

    # ── Load both drops ───────────────────────────────────────────
    num_drops = 2
    drops_array = (
             Drop(10, 10, 5,   5),      # drop 1: row=5,   col=5
        Drop(10, 10, 105, 105),    # drop 2: row=105, col=105
    )
    microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    input("Drops loaded -- ready for movement")

    # ── Phase 1: horizontal convergence (col 5→105 and 105→5) ────
    for i in range(101):
        drops_array = (Drop * 2)(
            Drop(10, 10, 5,   5   + i),
            Drop(10, 10, 105, 105 - i),
        )
        microfluidics.ActivateElec(128, 128, 2, drops_array)
        input(f"Drop 1 at col={5+i}, Drop 2 at col={105-i}")

    time.sleep(2)

    # ── Phase 2: vertical convergence (row 5→55 and 105→55) ──────
    for i in range(51):
        drops_array = (Drop * 2)(
            Drop(10, 10, 5   + i, 105),
            Drop(10, 10, 105 - i, 5),
        )
        microfluidics.ActivateElec(128, 128, 2, drops_array)
        input(f"Drop 1 at row={5+i}, Drop 2 at row={105-i}")

    time.sleep(2)

    # ── Phase 3: horizontal convergence to center (col 105→55 and 5→55) ──
    for i in range(51):
        drops_array = (Drop * 2)(
            Drop(10, 10, 55, 105 - i),
            Drop(10, 10, 55, 5   + i),
        )
        microfluidics.ActivateElec(128, 128, 2, drops_array)
        input(f"Drop 1 at col={105-i}, Drop 2 at col={5+i}")

    # ── Drops are now merged at row=55, col=55 ────────────────────
    # Collapse to a single combined drop
    activate_one(10, 10, 55, 55)
    input("Drops merged at row=55, col=55 -- press Enter to begin mix")

    # ── Mix movement ──────────────────────────────────────────────
    # Runs automatically (no input pauses) for smooth motion.

    MERGE_ROW = 55
    MERGE_COL = 55
    H, W = 10, 10

    # ── Mix 1: up 20 rows → back to center ───────────────────────
    print("Mix 1: up 20...")
    for i in range(1, 21):
        activate_one(H, W, MERGE_ROW - i, MERGE_COL)
    for i in range(20, -1, -1):
        activate_one(H, W, MERGE_ROW - i, MERGE_COL)

    # ── Mix 2: right 30 cols → back to center ─────────────────────
    print("Mix 2: right 30...")
    for i in range(1, 31):
        activate_one(H, W, MERGE_ROW, MERGE_COL + i)
    for i in range(30, -1, -1):
        activate_one(H, W, MERGE_ROW, MERGE_COL + i)

    # ── Mix 3: down 20 rows → back to center ──────────────────────
    print("Mix 3: down 20...")
    for i in range(1, 21):
        activate_one(H, W, MERGE_ROW + i, MERGE_COL)
    for i in range(20, -1, -1):
        activate_one(H, W, MERGE_ROW + i, MERGE_COL)

    # ── Mix 4: left 30 cols → back to center ──────────────────────
    print("Mix 4: left 30...")
    for i in range(1, 31):
        activate_one(H, W, MERGE_ROW, MERGE_COL - i)
    for i in range(30, -1, -1):
        activate_one(H, W, MERGE_ROW, MERGE_COL - i)

    # ── Mix 5: diagonal up-right 15x15 → back to center ──────────
    print("Mix 5: diagonal up-right...")
    for i in range(1, 16):
        activate_one(H, W, MERGE_ROW - i, MERGE_COL + i)
    for i in range(15, -1, -1):
        activate_one(H, W, MERGE_ROW - i, MERGE_COL + i)

    # ── Mix 6: clockwise rectangle 20x20 → back to center ────────
    print("Mix 6: clockwise rectangle...")
    for i in range(1, 21):                                  # right
        activate_one(H, W, MERGE_ROW,      MERGE_COL + i)
    for i in range(1, 21):                                  # down
        activate_one(H, W, MERGE_ROW + i,  MERGE_COL + 20)
    for i in range(20, -1, -1):                             # left
        activate_one(H, W, MERGE_ROW + 20, MERGE_COL + i)
    for i in range(20, -1, -1):                             # up
        activate_one(H, W, MERGE_ROW + i,  MERGE_COL)

    input("Mix complete -- drop back at row=55, col=55. Press Enter to shut down.")

    # ── Shutdown ──────────────────────────────────────────────────
    microfluidics.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    microfluidics.SetPower(False)
    input("Power off completed")
    microfluidics.CloseUSB()


if __name__ == "__main__":
    main()
