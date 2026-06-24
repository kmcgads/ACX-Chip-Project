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

    def detect_drop_color(self, frame: np.ndarray, min_area: int = 500,
                          min_saturation: int = 30,
                          sample_saturation: int = 80,
                          brightness_lo: int = 10,
                          brightness_hi: int = 92,
                          gamma: float = 2.2,
                          sat_boost: float | None = None,
                          sat_boost_percentile: int = 90) -> dict:
        """
        Automatically detects ANY colored ink drop and returns its hex color.
        Works for all hues — excludes black, gray, and white automatically.
        No coordinates needed — works on patterned or non-white backgrounds.

        Black / gray / white exclusion (automatic):
          - Black:      V channel < 20 (too dark to have meaningful color)
          - White/glare: V channel > 250 (fully blown-out, no color info)
          - Gray/background: S channel < min_saturation (gray has near-zero
            saturation in HSV regardless of brightness, so the electrode grid,
            chip background, and any achromatic surface are all excluded)
          This makes the method hue-agnostic: cyan, yellow, magenta, red, blue,
          orange — any vivid ink color is detected; achromatic regions are not.

        Detection strategy — find by SATURATION:
          Ink of any color is highly saturated; background, shadows, and glare
          are low-saturation. The saturation mask finds the drop regardless of
          its hue or the background appearance.

        Two-stage saturation filtering:
          min_saturation (default 50) — broad threshold for contour detection.
            Catches even dilute or mixed inks. Lower if a very pale drop is
            being missed.
          sample_saturation (default 80) — higher threshold applied only when
            sampling pixels for the color reading. Excludes borderline pixels at
            the drop edge that may be partially mixed with the background.

        Brightness clipping:
          Clips only the extreme tails of the brightness distribution within the
          detected drop pixels (default: bottom 10% shadows, top 8% glare).
          For vivid saturated colors the brighter pixels are usually the most
          representative, so the upper clip is intentionally conservative.

        HSV-space correction (gamma + adaptive saturation boost):
          Applied to the final averaged pixel in HSV space so hue is never
          disturbed:
            gamma (default 2.2) — lifts only the V (brightness) channel to
              compensate for camera underexposure against a bright background.
              RGB-space gamma distorts color ratios; HSV-space does not.
            sat_boost (default auto) — multiplies the S (saturation) channel
              to recover chroma lost to camera compression. In auto mode the
              boost is computed from the drop itself: ratio of the 90th-percentile
              saturation to the median saturation, clamped to [1.0, 2.0]. This
              self-calibrates per frame and per color — orange gets less boost
              than pink automatically, with no manual tuning needed.

        Steps:
          1. HSV saturation mask (min_saturation) + V bounds isolate colored region.
          2. Morphological cleanup (close then open) removes noise.
          3. Largest contour = the drop; contours below min_area are rejected.
          4. Re-filter pixels inside the drop at sample_saturation threshold.
          5. Brightness percentile clipping removes extreme shadows and glare.
          6. Saturation-weighted average → single BGR pixel.
          7. HSV-space gamma + saturation boost applied to final pixel.
          8. Return hex, rgb, bgr, area, bounding box.

        Parameters:
          min_area:          minimum drop area in pixels² (default 500).
          min_saturation:    HSV S floor for contour detection, 0–255 (default 30).
                             Dark saturated colors (deep red, dark navy, dark
                             green) have their saturation compressed by the
                             camera — lowering this catches them before they
                             fall below the threshold and cause the wrong region
                             to be detected.
          sample_saturation: HSV S floor for pixel sampling, 0–255 (default 80).
          brightness_lo:     lower brightness percentile clip (default 10).
          brightness_hi:     upper brightness percentile clip (default 92).
          gamma:             V-channel brightness exponent (default 2.2).
                             >1 brightens. Set 1.0 to disable.
          sat_boost:         S-channel saturation multiplier. Default None = auto.
                             Auto mode computes the boost from the drop's own
                             saturation distribution: the ratio of the
                             sat_boost_percentile to the median saturation,
                             clamped to [1.0, 2.0]. Pass a float to override.
          sat_boost_percentile: percentile of the saturation distribution used
                             as the auto-boost target (default 90). Higher values
                             target the most vivid pixels; lower values are more
                             conservative.
        """
        hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        s_ch = hsv[:, :, 1]   # saturation: 0 = gray/white/black, 255 = vivid color
        v_ch = hsv[:, :, 2]   # value:      0 = black, 255 = white/bright

        # Stage 1: broad saturation mask — any vivid color passes; achromatic doesn't.
        # V bounds hard-exclude pure black (V<20) and blown-out specular (V>250).
        # Adaptive floor: dark pixels (V<80) get a lower saturation requirement
        # because the camera compresses saturation for dark colors — deep red,
        # dark navy, dark green all read lower S than they truly are. Bright pixels
        # keep the full min_saturation floor so muted backgrounds don't slip through.
        dark_mask  = (v_ch > 20)  & (v_ch <  80) & (s_ch >= max(15, min_saturation // 2))
        mid_mask_s = (v_ch >= 80) & (v_ch < 250) & (s_ch >= min_saturation)
        color_mask = (dark_mask | mid_mask_s).astype(np.uint8) * 255

        # Morphological cleanup: close fills small holes, open removes stray noise
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

        # Fill the drop contour to create a solid mask
        drop_fill = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.drawContours(drop_fill, [largest], -1, 255, thickness=cv2.FILLED)

        # Stage 2: tighter saturation filter for color sampling.
        # Edge pixels blended with the background have lower saturation and
        # would pull the reading toward the background color — exclude them.
        vivid_mask      = (drop_fill == 255) & (s_ch >= sample_saturation) & (v_ch > 20) & (v_ch < 250)
        vivid_pixels    = frame[vivid_mask].reshape(-1, 3)
        brightness_vals = v_ch[vivid_mask]

        # Fall back to broad mask if the tight threshold removes everything
        if len(vivid_pixels) == 0:
            broad_mask      = (drop_fill == 255) & (color_mask == 255)
            vivid_pixels    = frame[broad_mask].reshape(-1, 3)
            brightness_vals = v_ch[broad_mask]

        if len(vivid_pixels) == 0:
            raise ValueError("Drop contour found but no saturated pixels inside it.")

        # Brightness clipping — removes only the extreme shadow and glare tails
        lo = np.percentile(brightness_vals, brightness_lo)
        hi = np.percentile(brightness_vals, brightness_hi)
        mid_mask   = (brightness_vals >= lo) & (brightness_vals <= hi)
        mid_pixels = vivid_pixels[mid_mask]
        if len(mid_pixels) == 0:
            mid_pixels = vivid_pixels

        # Saturation-weighted average: the most vivid pixels contribute most
        sat_vals = s_ch[vivid_mask][mid_mask] if len(mid_pixels) < len(vivid_pixels) else s_ch[vivid_mask]
        if len(sat_vals) != len(mid_pixels):
            sat_vals = np.ones(len(mid_pixels))
        weights = sat_vals.astype(float) / sat_vals.sum()
        b_raw   = np.average(mid_pixels[:, 0], weights=weights)
        g_raw   = np.average(mid_pixels[:, 1], weights=weights)
        r_raw   = np.average(mid_pixels[:, 2], weights=weights)

        # Adaptive saturation boost: use the drop's own saturation distribution
        # to determine how much the camera has compressed the saturation.
        # The most vivid pixels in the drop (top percentile) are the closest to
        # the true ink color. The ratio of that percentile to the median is the
        # boost needed to recover the lost chroma — computed fresh per frame so
        # the correction self-adjusts for each color and lighting condition.
        sat_of_mid = s_ch[vivid_mask][mid_mask] if len(mid_pixels) < len(vivid_pixels) else s_ch[vivid_mask]
        if len(sat_of_mid) != len(mid_pixels):
            sat_of_mid = np.ones(len(mid_pixels), dtype=float) * 128
        median_sat = float(np.median(sat_of_mid))
        p_top_sat  = float(np.percentile(sat_of_mid, sat_boost_percentile))
        if sat_boost is not None:
            effective_sat_boost = float(sat_boost)   # manual override
        elif median_sat > 0 and p_top_sat > median_sat:
            effective_sat_boost = float(np.clip(p_top_sat / median_sat, 1.0, 2.0))
        else:
            effective_sat_boost = 1.0

        # HSV-space correction: adjust V (brightness) and S (saturation) independently
        # of H (hue) so the color identity is never changed, only its intensity.
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

        # Automatically find the colored drop against the white background
        color_result = camera.detect_drop_color(frame)

        print(f"Average RGB color: {color_result['rgb']}")
        print(f"Average BGR color: {color_result['bgr']}")
        print(f"HEX color: {color_result['hex']}")

    except Exception as e:
        print(f"ERROR: {e}")