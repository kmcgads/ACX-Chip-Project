import ctypes
from ctypes import POINTER, c_int, c_void_p, c_char_p, Structure
from typing import List

# Load library
microfluidics = ctypes.CDLL("C:\\Users\\klmcg\\Downloads\\ACX_pythonSDK v1.2 3\\ACX_pythonSDK\\windows\\DLLTest.dll")  

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

    num_drops = 4
    drops_array = (Drop * num_drops)(
        Drop(10, 10, 10, 10),
        Drop(10, 10, 10, 30),
        Drop(10, 10, 30, 10),
        Drop(10, 10, 30, 30)
    )
    res = microfluidics.ActivateElec(128, 128, num_drops, drops_array)
    user_input = input("Electrode actuation completed")

    res = microfluidics.SetPower(False)
    user_input = input("Power off completed")

    microfluidics.CloseUSB()
    


if __name__ == "__main__":
    main()