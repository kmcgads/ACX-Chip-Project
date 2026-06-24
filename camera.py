from datetime import datetime
from pathlib import Path
from typing import Union

import cv2
import numpy as np


"""Sets camera address so it can be found as an integer or string; you may have to try out a few dif values"""
class CameraInterface:
    def __init__(self, camera_address: Union[int, str] = 0) -> None:
        # Confirms that the camera address is right and sets it for the script
        self.camera_address = self._validate_camera_address(camera_address)

    @staticmethod
    def _validate_camera_address(camera_address: Union[int, str]) -> Union[int, str]:
        try:
            return int(camera_address)
        except (ValueError, TypeError):
            return camera_address

    # Confirms camera existence and raises an error if there is a connection issue
    def _open_camera(self) -> cv2.VideoCapture:
        camera = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        # Keeps autofocus on by default
        camera.set(cv2.CAP_PROP_AUTOFOCUS, 1)

        if not camera.isOpened():
            raise Exception("Unable to connect to camera")

        return camera

    # Releases the camera so other programs can use it
    def _close_camera(self, camera) -> None:
        if camera is not None and camera.isOpened():
            camera.release()

    def get_frame_size(self) -> tuple[int, int]:
        """Returns the (width, height) of a captured frame in pixels."""
        camera = None
        try:
            camera = self._open_camera()
            ok, frame = camera.read()
            if not ok:
                raise Exception("Unable to read frame.")
            h, w = frame.shape[:2]
            print(f"Frame size: {w} px wide x {h} px tall")
            return w, h
        finally:
            self._close_camera(camera)

    # Code for actual picture to be taken
    def take_picture(self) -> tuple[Path, np.ndarray]:
        camera = None

        try:
            camera = self._open_camera()

            # Reads one image from the connected camera
            success, frame = camera.read()

            """Pathway that is in if there is an issue with the actual PIC being taken,
            not just the connection to the camera"""
            if not success:
                raise Exception("Unable to take picture")

            # Names file of the pic taken under the microscope
            filename = datetime.now().strftime("microscope_%Y%m%d_%H%M%S.jpg")
            image_path = Path(filename)

            # Saves the captured frame as an image file
            cv2.imwrite(str(image_path), frame)

            # Returns both the saved image path and the actual frame so the same picture can be analyzed
            return image_path, frame

        finally:
            # Releases the camera from use and takes away the USB connection at the end of the sequence
            self._close_camera(camera)

    """Uses the pic collected and gathers a hex color within a rectangle as defined by height and width
    to be used in bayesian optimization later"""
    def get_average_color_from_rectangle(
        self,
        frame: np.ndarray,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> dict:
        """Defines the frame size for what is being averaged and taken into account when the average hex color
        is found, also ensures the rectangle is actually within the media being taken"""

        roi = frame[y:y + height, x:x + width]

        if roi.size == 0:
            raise ValueError("Unable to create a rectangle. Check x, y, width, and height values.")

        """np is now taking the average color from each pixel within the previously defined rectangle"""

        # Reshapes the rectangle into a list of individual BGR pixels
        pixels = roi.reshape(-1, 3)

        # Finds the brightness of each pixel so glare and shadows can be removed
        brightness = np.mean(pixels, axis=1)

        # Removes very dark shadow pixels and very bright glare pixels
        filtered_pixels = pixels[(brightness > 30) & (brightness < 240)]

        # If the filter removes too much, use the original pixels instead
        if len(filtered_pixels) == 0:
            filtered_pixels = pixels

        # Uses the median color instead of the mean so glare and shadows affect the result less
        b, g, r = np.median(filtered_pixels, axis=0).astype(int)

        # Produces hex color back
        hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)

        return {"rgb": (r, g, b), "bgr": (b, g, r), "hex": hex_color}

#Outputs for what will be returned to the user while the script is running
if __name__ == "__main__":
    print("Starting camera script...")

    try:
        camera = CameraInterface(camera_address=0)

        frame_w, frame_h = camera.get_frame_size()

        print("Taking picture...")
        image_path, frame = camera.take_picture()
        print(f"Picture saved to: {image_path}")

        #Dimensions for the rectangle being formed to take the average rgb
        color_result = camera.get_average_color_from_rectangle(
            frame=frame,
            x=200,
            y=150,
            width=100,
            height=100,
        )

        print(f"Average RGB color: {color_result['rgb']}")
        print(f"Average BGR color: {color_result['bgr']}")
        print(f"HEX color: {color_result['hex']}")

    except Exception as e:
        print(f"ERROR: {e}")