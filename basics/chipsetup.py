"""The original code for this chip was written in C++ by ACX Instruments and later adapted for Python using ctypes.
To use this chip, the user must purchase the hardware from ACX Instruments. 
ACX provides the required starter software and DLL files with the purchased device.
Because the DLL is proprietary company software, I cannot share the actual DLL file or its file path. 
The placeholder below represents where the ACX-provided DLL would be loaded."""

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

    #load function + use it
def main():
    # initialization  of USB and confirmation of channel being opened
    microfluidics.InitUSB()
    add = 0
    res = microfluidics.OpenUSB()
    if res:
        user_input = input("Open successfully: ")
    else:
        user_input = input("Open failed: ")
    #Spares space on USB for data to be collected and held
    buffer_size = 256
    buffer = (ctypes.c_uint8 * buffer_size)()
    #Communicates if power is supplied to device
    res = microfluidics.SetPower(True)
    user_input = input("Power on completed")
    """From here on out user input will be needed of hitting th enter bar to continue the script"""
    #Sets voltage of 45(high volt) through the entire chip for use
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
   #prints voltages for confirmation
    print(res)
    print(v1, v2, v3, v4, v5, v6, v7, v8, v9)
    user_input = input("Query voltage command completed")

    #Sets up the drop spots and gets them ready to be loaded into there intial spots by making sure electrodes are on to form the drop
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
    
    #turns of connection between usb and DM lite device
    microfluidics.CloseUSB()
    


if __name__ == "__main__":
    main()