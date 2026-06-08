from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np


class CameraInterface:
    def __init__(self, camera_address: Union[int, str] = 1) -> None:
        self.camera_address = self._validate_camera_address(camera_address)

    @staticmethod
    def _validate_camera_address(camera_address: Union[int, str]) -> Union[int, str]:
        try:
            return int(camera_address)
        except (ValueError, TypeError):
            return camera_address

    def _open_camera(self) -> cv2.VideoCapture:
        camera = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
        #camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not camera.isOpened():
            raise Exception("Unable to connect to camera")
        return camera

    def _close_camera(self, camera: cv2.VideoCapture) -> None:
        if camera is not None and camera.isOpened():
            camera.release()

    def test_connection(self) -> bool:
        try:
            camera = self._open_camera()
            self._close_camera(camera)
            return True
        except Exception:
            return False

    def take_picture(self, focus: Optional[int] = None, autofocus: Optional[bool] = None) -> Path:
        camera = None
        try:
            camera = self._open_camera()
            if focus is not None or autofocus is not None:
                self._adjust_focus_settings_unlocked(camera, focus, autofocus)
            success, frame = camera.read()
            if not success:
                raise Exception("Unable to read from camera")
            filename = datetime.now().strftime("microscope_%Y%m%d_%H%M%S.jpg")
            image_path = Path(filename)
            cv2.imwrite(str(image_path), frame)
            return image_path
        finally:
            if camera is not None:
                self._close_camera(camera)

    def get_average_color_from_rectangle(
        self, x: int, y: int, width: int, height: int,
        focus: Optional[int] = None, autofocus: Optional[bool] = None,
    ) -> dict:
        camera = None  
        try:
            camera = self._open_camera()
            if focus is not None or autofocus is not None:
                self._adjust_focus_settings_unlocked(camera, focus, autofocus)
            success, frame = camera.read() 
            if not success:
                raise Exception("Unable to read from camera")
            roi = frame[y:y + height, x:x + width]
            if roi.size == 0:
                raise ValueError("Rectangle is outside the image area")
            avg_bgr = np.mean(roi, axis=(0, 1))
            b, g, r = avg_bgr.astype(int)
            hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
            return {"rgb": (r, g, b), "bgr": (b, g, r), "hex": hex_color}
        finally:
            if camera is not None:
                self._close_camera(camera)

    def _adjust_focus_settings_unlocked(
        self, camera: cv2.VideoCapture,
        focus: Optional[int] = None, autofocus: Optional[bool] = None,
    ) -> None:
        if camera is None or not camera.isOpened():
            raise Exception("Camera is not connected")

        focus_changed = False

        if autofocus is not None:
            current_autofocus = camera.get(cv2.CAP_PROP_AUTOFOCUS)
            if current_autofocus != (1 if autofocus else 0):
                camera.set(cv2.CAP_PROP_AUTOFOCUS, 1 if autofocus else 0)
                focus_changed = True

        if not autofocus and focus is not None:
            if focus < 0 or focus > 255:
                raise ValueError("Focus value must be between 0 and 255.")
            current_focus = camera.get(cv2.CAP_PROP_FOCUS)
            if current_focus != focus:
                camera.set(cv2.CAP_PROP_FOCUS, focus)
                focus_changed = True

        if focus_changed:
            for _ in range(30):
                camera.read()
        else:
            for _ in range(5):
                camera.read()

if __name__ == "__main__":
    print("Starting camera test...")
    try:
        camera = CameraInterface(camera_address=1)

        print("Testing camera connection...")
        if camera.test_connection():
            print("Camera connected successfully.")
        else:
            print("Camera connection failed.")
            exit()

        print("Taking picture...")
        image_path = camera.take_picture()
        print(f"Picture saved to: {image_path}")

        color_result = camera.get_average_color_from_rectangle(x=200, y=150, width=100, height=100)
        print(f"Average RGB color: {color_result['rgb']}")
        print(f"Average BGR color: {color_result['bgr']}")
        print(f"HEX color: {color_result['hex']}")

    except Exception as e:
        print(f"ERROR: {e}")

                    