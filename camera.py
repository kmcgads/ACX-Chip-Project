from datetime import datetime
from pathlib import Path
from typing import Union

import cv2
import numpy as np


class CameraInterface:
    """Connection to the researcher's own microscope camera.

    Wide-field additions for the chip-health run (spec/p1_chip_health_design.md
    §8) are layered on top without changing any existing behaviour --
    masterscript3.py, bayesxcam.py and bayesopttest1.py all import this class.

    Two modes:

    * **One-shot** (unchanged) -- ``take_picture`` opens the device, grabs, and
      releases. Correct for stills, and what the legacy scripts rely on.
    * **Streaming** (new) -- ``open_stream`` holds the device open so a live
      loop does not pay a device-open per frame. Use as a context manager.

    Deliberately *not* the vendor camera: no MvCameraControl.dll, no Hikrobot
    MVS, no ONNX detectors (spec/objectives.md §0.2).
    """

    def __init__(self, camera_address: Union[int, str] = 0) -> None:
        self.camera_address = self._validate_camera_address(camera_address)
        self._stream = None          # held-open VideoCapture, when streaming
        self._frame = None           # ElectrodeFrame, once registered
        self._frame_index = 0

    @staticmethod
    def _validate_camera_address(camera_address: Union[int, str]) -> Union[int, str]:
        try:
            return int(camera_address)
        except (ValueError, TypeError):
            return camera_address

    def _open_camera(self, autofocus: bool = True) -> cv2.VideoCapture:
        """Open the device.

        ``autofocus`` defaults to True so existing callers behave exactly as
        before. Measurement runs pass False: refocusing mid-run is a real
        variance source, and the researcher sets focus by hand
        (spec/objectives.md §1.4).
        """
        camera = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
        # cv2.VideoWriter_fourcc exists at runtime; the opencv-python stubs do
        # not declare it, so Pylance reports a false attribute error here.
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # type: ignore[attr-defined]
        camera.set(cv2.CAP_PROP_AUTOFOCUS, 1 if autofocus else 0)
        if not camera.isOpened():
            raise Exception("Unable to connect to camera")
        return camera

    def _close_camera(self, camera) -> None:
        if camera is not None and camera.isOpened():
            camera.release()

    # ── streaming ─────────────────────────────────────────────────────────────

    def open_stream(self, autofocus: bool = False) -> "CameraInterface":
        """Hold the device open across frames.

        A visualization loop cannot pay a device-open per frame, which is what
        ``take_picture`` does. Approved as a lifetime change only -- same
        camera, same CAP_DSHOW/MJPG connection (spec/objectives.md §0.2).
        """
        if self._stream is None:
            self._stream = self._open_camera(autofocus=autofocus)
            self._frame_index = 0
        return self

    def close_stream(self) -> None:
        self._close_camera(self._stream)
        self._stream = None

    @property
    def streaming(self) -> bool:
        return self._stream is not None

    def __enter__(self) -> "CameraInterface":
        return self.open_stream()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close_stream()
        return False

    def read_frame(self) -> tuple[int, np.ndarray]:
        """Grab one frame from the open stream. Returns (frame_index, frame)."""
        if self._stream is None:
            raise RuntimeError("no open stream -- call open_stream() first")
        ok, frame = self._stream.read()
        if not ok:
            raise Exception("Unable to read frame from the open stream")
        idx = self._frame_index
        self._frame_index += 1
        return idx, frame

    def get_frame_size(self) -> tuple[int, int]:
        """Frame dimensions in pixels.

        Also the quickest way to answer "how many pixels per electrode do I
        have?" -- run this on the instrument PC and divide by 128.
        """
        if self._stream is not None:
            _, frame = self.read_frame()
            h, w = frame.shape[:2]
            print(f"Frame size: {w} px wide x {h} px tall")
            return w, h
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
        """Grab and save one still.

        Uses the open stream when there is one, otherwise falls back to the
        original open -> read -> release. Signature and return value unchanged,
        so the legacy callers are unaffected.
        """
        if self._stream is not None:
            _, frame = self.read_frame()
            filename = datetime.now().strftime("microscope_%Y%m%d_%H%M%S.jpg")
            image_path = Path(filename)
            cv2.imwrite(str(image_path), frame)
            return image_path, frame
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

    # ── wide field of view ───────────────────────────────────────────────────
    #
    # Everything below is additive, for the whole-chip health run. The methods
    # above are untouched: masterscript3.py, bayesxcam.py and bayesopttest1.py
    # depend on them.
    #
    # Why detect_drop_color is not reused here: it returns the *single largest*
    # contour (camera.py, `largest = max(contours, ...)`). At whole-chip framing
    # the frame holds the droplet AND any residue left behind, and residue is
    # precisely what the health check is looking for -- so wide-field detection
    # has to return every blob, not the biggest one.

    def set_registration(self, corners_px, chip_rows: int = 128,
                         chip_cols: int = 128):
        """Register the chip in the frame from its four clicked corners.

        Corners in order: top-left, top-right, bottom-right, bottom-left of the
        electrode array. Cacheable and reusable for as long as the camera does
        not move. Electrode pitch is not needed -- corner registration gives the
        whole mapping (spec/p1_chip_health_design.md §2, phase 2).

        The import is deliberately lazy so this module's top-level dependencies
        stay cv2 + numpy and the legacy scripts keep importing cleanly.
        """
        from chiphealth.geometry import ElectrodeFrame  # noqa: PLC0415 (lazy on purpose)

        self._frame = ElectrodeFrame.from_corners(corners_px, chip_rows, chip_cols)
        return self._frame

    @property
    def registration(self):
        """The ElectrodeFrame, or None if the chip has not been registered."""
        return self._frame

    def _require_registration(self):
        if self._frame is None:
            raise RuntimeError(
                "camera is not registered to the chip -- call set_registration() "
                "with the four chip corners first"
            )
        return self._frame

    def min_area_px_for(self, min_electrodes: float = 1.0) -> float:
        """Pixel-area threshold for a blob of a given size in electrodes.

        Replaces the fixed ``min_area=500`` used by detect_drop_color. At
        whole-chip framing one electrode is on the order of 100 px^2, so 500
        would silently discard several electrodes of residue.
        """
        return self._require_registration().min_area_px(min_electrodes)

    def liquid_mask(self, frame: np.ndarray, min_saturation: int = 30,
                    kernel_px: int = 3) -> np.ndarray:
        """Binary mask of coloured liquid.

        Same HSV logic detect_drop_color uses, factored out and with a smaller
        morphology kernel: at wide framing a 5x5 open can erase a one-electrode
        speck, which is the smallest thing worth finding.

        Thresholds are parameters, not constants, because the test substance
        will change from dyed water to other chemicals (spec/objectives.md §1.4).
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        s_ch = hsv[:, :, 1]
        v_ch = hsv[:, :, 2]

        dark_mask  = (v_ch > 20)  & (v_ch <  80) & (s_ch >= max(15, min_saturation // 2))
        mid_mask_s = (v_ch >= 80) & (v_ch < 250) & (s_ch >= min_saturation)
        mask = (dark_mask | mid_mask_s).astype(np.uint8) * 255

        if kernel_px > 0:
            kernel = np.ones((kernel_px, kernel_px), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return mask

    def detect_droplets_wide(self, frame: np.ndarray,
                             min_electrodes: float = 1.0,
                             min_saturation: int = 30,
                             kernel_px: int = 3) -> list[dict]:
        """Every liquid blob in the frame, in electrode coordinates.

        Unlike detect_drop_color this returns *all* blobs -- the droplet being
        driven plus anything left behind. Requires registration.

        Returns dicts with both pixel and electrode geometry, sorted
        largest-first, so the caller can treat the head of the list as the
        droplet under the window.
        """
        ef = self._require_registration()
        mask = self.liquid_mask(frame, min_saturation=min_saturation,
                                kernel_px=kernel_px)
        min_area_px = ef.min_area_px(min_electrodes)

        n, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask,
                                                                       connectivity=8)
        out: list[dict] = []
        for i in range(1, n):  # 0 is background
            area_px = float(stats[i, cv2.CC_STAT_AREA])
            if area_px < min_area_px:
                continue
            x = float(stats[i, cv2.CC_STAT_LEFT])
            y = float(stats[i, cv2.CC_STAT_TOP])
            w = float(stats[i, cv2.CC_STAT_WIDTH])
            h = float(stats[i, cv2.CC_STAT_HEIGHT])
            cx, cy = float(centroids[i][0]), float(centroids[i][1])
            row, col = ef.pixel_to_electrode(cx, cy)
            e_row, e_col, e_h, e_w = ef.bbox_px_to_electrode(x, y, w, h)
            out.append({
                "area_px": area_px,
                "area_electrodes": ef.area_px_to_electrodes(area_px),
                "bbox_px": (x, y, w, h),
                "centroid_px": (cx, cy),
                "centroid_electrodes": (row, col),
                "bbox_electrodes": (e_row, e_col, e_h, e_w),
            })
        out.sort(key=lambda b: b["area_px"], reverse=True)
        return out

    def observe(self, frame: np.ndarray, step_idx: int, frame_index: int,
                t: float, min_electrodes: float = 1.0,
                min_saturation: int = 30):
        """Turn one frame into a detector Observation.

        This is the bridge between the OpenCV layer and the pure decision layer:
        everything downstream works on extracted blobs in electrode
        coordinates, which is what lets the detector be tested with no camera.
        """
        from chiphealth.detector import Blob, Observation  # lazy: keeps top-level deps clean

        blobs = []
        for b in self.detect_droplets_wide(frame, min_electrodes=min_electrodes,
                                           min_saturation=min_saturation):
            row, col = b["centroid_electrodes"]
            e_row, e_col, e_h, e_w = b["bbox_electrodes"]
            blobs.append(Blob(centroid_row=row, centroid_col=col,
                              area_electrodes=b["area_electrodes"],
                              row=e_row, col=e_col, height=e_h, width=e_w))
        return Observation(step_idx=step_idx, frame_index=frame_index, t=t,
                           blobs=tuple(blobs))


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