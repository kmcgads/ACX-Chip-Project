from datetime import datetime
from fileinput import filename
import tempfile
from pathlib import Path
from typing import Optional, Union

import cv2

class CameraInterface:
    def __init__(self, camera_address: Union[int, str] = 0) -> None:
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
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
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
            #still working on next step below
    def take_picture(self, focus: Optional[int] = None, autofocus: Optional[bool] = None) -> Path:
            #potential add in a number for focus or decide if we want autofocus when first pic is taken
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
    def _adjust_focus_settings_unlocked(
        self, camera: cv2.VideoCapture, focus: Optional[int] = None, autofocus: Optional[bool] = None,) -> None:
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
            # Discard 30 frames to allow focus to stabilize
            for _ in range(30):
                camera.read()
        else:
            # Discard 5 frames in case the camera needs a moment for startup
            for _ in range(5):
                camera.read()
        if __name__ == "__main__":
            print("Starting camera test...")

try:
        
        #try:
               # for i in range(-1, 2):
                  #  camera = CameraInterface(camera_address=i)
                   # if camera.test_connection():
                    #    print(f"Camera found at address {i}")
            
        #except Exception as e:
         #       print(f"Camera not found at address {i}: {e}")
                
                  
        ##  Change 1 to 0, 2, or another value if needed
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
except Exception as e:
        print(f"ERROR: {e}")
                    