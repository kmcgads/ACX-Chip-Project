from datetime import datetime
from pathlib import Path
from typing import Union

import cv2
import numpy as np


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
        camera = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        if not camera.isOpened():
            raise Exception("Unable to connect to camera")
        return camera

    def _close_camera(self, camera) -> None:
        if camera is not None and camera.isOpened():
            camera.release()

    def get_frame_size(self) -> tuple[int, int]:
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

    def take_picture(self) -> tuple[Path, np.ndarray]:
        camera = None
        try:
            camera = self._open_camera()
            success, frame = camera.read()
            if not success:
                raise Exception("Unable to take picture")
            filename   = datetime.now().strftime("microscope_%Y%m%d_%H%M%S.jpg")
            image_path = Path(filename)
            cv2.imwrite(str(image_path), frame)
            return image_path, frame
        finally:
            self._close_camera(camera)

    def detect_drop_color(self, frame: np.ndarray, min_area: int = 500,
                          min_saturation: int = 30,
                          sample_saturation: int = 80,
                          brightness_lo: int = 10,
                          brightness_hi: int = 92,
                          gamma: float = 2.2,
                          sat_boost: float | None = None,
                          sat_boost_percentile: int = 90) -> dict:
        hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        s_ch = hsv[:, :, 1]
        v_ch = hsv[:, :, 2]

        dark_mask  = (v_ch > 20)  & (v_ch <  80) & (s_ch >= max(15, min_saturation // 2))
        mid_mask_s = (v_ch >= 80) & (v_ch < 250) & (s_ch >= min_saturation)
        color_mask = (dark_mask | mid_mask_s).astype(np.uint8) * 255

        kernel     = np.ones((5, 5), np.uint8)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN,  kernel)

        contours, _ = cv2.findContours(color_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            raise ValueError(
                f"No colored drop found (min_saturation={min_saturation}). "
                "Try lowering min_saturation if the ink is very dilute."
            )

        largest = max(contours, key=cv2.contourArea)
        area    = cv2.contourArea(largest)

        if area < min_area:
            raise ValueError(
                f"Largest colored region is only {area:.0f} px² "
                f"(min_area={min_area}). Likely residue or noise — raise min_area."
            )

        drop_fill = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.drawContours(drop_fill, [largest], -1, 255, thickness=cv2.FILLED)

        vivid_mask      = (drop_fill == 255) & (s_ch >= sample_saturation) & (v_ch > 20) & (v_ch < 250)
        vivid_pixels    = frame[vivid_mask].reshape(-1, 3)
        brightness_vals = v_ch[vivid_mask]
        active_mask     = vivid_mask

        if len(vivid_pixels) == 0:
            broad_mask      = (drop_fill == 255) & (color_mask == 255)
            vivid_pixels    = frame[broad_mask].reshape(-1, 3)
            brightness_vals = v_ch[broad_mask]
            active_mask     = broad_mask

        if len(vivid_pixels) == 0:
            raise ValueError("Drop contour found but no saturated pixels inside it.")

        lo = np.percentile(brightness_vals, brightness_lo)
        hi = np.percentile(brightness_vals, brightness_hi)
        mid_mask   = (brightness_vals >= lo) & (brightness_vals <= hi)
        mid_pixels = vivid_pixels[mid_mask]
        if len(mid_pixels) == 0:
            mid_pixels = vivid_pixels

        sat_vals = s_ch[active_mask][mid_mask] if len(mid_pixels) < len(vivid_pixels) else s_ch[active_mask]
        if len(sat_vals) != len(mid_pixels):
            sat_vals = np.ones(len(mid_pixels))
        weights = sat_vals.astype(float) / sat_vals.sum()
        b_raw   = np.average(mid_pixels[:, 0], weights=weights)
        g_raw   = np.average(mid_pixels[:, 1], weights=weights)
        r_raw   = np.average(mid_pixels[:, 2], weights=weights)

        sat_of_mid = s_ch[active_mask][mid_mask] if len(mid_pixels) < len(vivid_pixels) else s_ch[active_mask]
        if len(sat_of_mid) != len(mid_pixels):
            sat_of_mid = np.ones(len(mid_pixels), dtype=float) * 128
        median_sat = float(np.median(sat_of_mid))
        p_top_sat  = float(np.percentile(sat_of_mid, sat_boost_percentile))
        if sat_boost is not None:
            effective_sat_boost = float(sat_boost)
        elif median_sat > 0 and p_top_sat > median_sat:
            effective_sat_boost = float(np.clip(p_top_sat / median_sat, 1.0, 2.0))
        else:
            effective_sat_boost = 1.0

        if gamma != 1.0 or effective_sat_boost != 1.0:
            pixel_bgr = np.array([[[int(b_raw), int(g_raw), int(r_raw)]]], dtype=np.uint8)
            pixel_hsv = cv2.cvtColor(pixel_bgr, cv2.COLOR_BGR2HSV).astype(float)
            if gamma != 1.0:
                pixel_hsv[0, 0, 2] = min(255.0, 255.0 * (pixel_hsv[0, 0, 2] / 255.0) ** (1.0 / gamma))
            if effective_sat_boost != 1.0:
                pixel_hsv[0, 0, 1] = min(255.0, pixel_hsv[0, 0, 1] * effective_sat_boost)
            pixel_bgr = cv2.cvtColor(pixel_hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
            b_raw, g_raw, r_raw = float(pixel_bgr[0, 0, 0]), float(pixel_bgr[0, 0, 1]), float(pixel_bgr[0, 0, 2])

        b, g, r   = int(round(b_raw)), int(round(g_raw)), int(round(r_raw))
        hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
        x, y, w, h = cv2.boundingRect(largest)

        print(f"Drop detected: {area:.0f} px²  "
              f"vivid pixels (S>={sample_saturation}): {len(vivid_pixels)}  "
              f"after brightness clip ({brightness_lo}–{brightness_hi}%): {len(mid_pixels)}  "
              f"brightness range: {lo:.0f}–{hi:.0f}  "
              f"sat_boost={'auto→'+f'{effective_sat_boost:.2f}' if sat_boost is None else f'{effective_sat_boost:.2f}'}  "
              f"gamma={gamma}  hex={hex_color}")

        return {
            "rgb":          (int(r), int(g), int(b)),
            "bgr":          (int(b), int(g), int(r)),
            "hex":          hex_color,
            "area_px":      int(area),
            "bounding_box": (x, y, w, h),
        }

    def get_average_color_from_rectangle(
        self,
        frame: np.ndarray,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> dict:
        roi = frame[y:y + height, x:x + width]

        if roi.size == 0:
            raise ValueError("Unable to create a rectangle. Check x, y, width, and height values.")

        pixels          = roi.reshape(-1, 3)
        brightness      = np.mean(pixels, axis=1)
        filtered_pixels = pixels[(brightness > 30) & (brightness < 240)]

        if len(filtered_pixels) == 0:
            filtered_pixels = pixels

        b, g, r   = np.median(filtered_pixels, axis=0).astype(int)
        hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)

        return {"rgb": (r, g, b), "bgr": (b, g, r), "hex": hex_color}


if __name__ == "__main__":
    print("Starting camera script...")

    try:
        camera = CameraInterface(camera_address=1)

        frame_w, frame_h = camera.get_frame_size()

        print("Taking picture...")
        image_path, frame = camera.take_picture()
        print(f"Picture saved to: {image_path}")

        color_result = camera.detect_drop_color(frame)

        print(f"Average RGB color: {color_result['rgb']}")
        print(f"Average BGR color: {color_result['bgr']}")
        print(f"HEX color: {color_result['hex']}")

    except Exception as e:
        print(f"ERROR: {e}")