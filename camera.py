import tempfile
from pathlib import Path
from typing import Optional, Union

import cv2
#Adding in matlib so that I am able to visualize and measure the drops in the chip later"
#from matplotlib import pyplot as plt

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
        #Added in cv2.CAP_DSHOW so that it runs better on windows 
        camera = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
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
                with tempfile.NamedTemporaryFile(
                    suffix=".jpg", delete=False
                ) as temp_file:
                    temp_file_path = Path(temp_file.name)
                    cv2.imwrite(str(temp_file_path), frame)
                return temp_file_path
            finally:
                if camera is not None:
                    self._close_camera(camera)
