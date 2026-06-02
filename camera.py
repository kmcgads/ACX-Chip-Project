"""
Camera interface for handling camera operations including image capture and barcode reading.
"""

import tempfile
import threading
from pathlib import Path
from typing import Optional, Union

import cv2
#Adding in matlib so that I am able to visualize and measure the drops in the chip later"
from matplotlib import pyplot as plt

class CameraInterface:
    """Interface for camera operations."""

    def __init__(self, camera_address: Union[int, str] = 1) -> None:
        """
        Initialize the camera interface.

        Args:
            camera_address: The camera address, either a number for windows or a device path in Linux/Mac.
        """
        self.camera_address = self._validate_camera_address(camera_address)
        self.camera_lock = threading.Lock()
