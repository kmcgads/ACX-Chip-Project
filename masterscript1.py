"""
color_match_experiment.py
──────────────────────────────────────────────────────────────────────────────
8-trial Bayesian optimization experiment that mixes three CMY ink drops on a
microfluidic chip to match a randomly chosen target color.

Volume parameterization
───────────────────────
The optimizer picks two continuous values in [0,1]² which are mapped through
a 2-simplex bijection to produce three volume fractions (f_c, f_m, f_y) that
always sum to 1.  Each fraction is then scaled to an electrode AREA:

    area_c = round(f_c × MERGE_H × MERGE_W)      ← sum = 100 electrodes
    area_m = round(f_m × MERGE_H × MERGE_W)
    area_y = 100 − area_c − area_m               ← guaranteed ≥ 1 by clamping

Each piece's HEIGHT and WIDTH are computed dynamically by vol_to_shape() —
they are NOT preset.  Pieces are roughly square and always fit within the
20×20 stash drop.  Because area_c + area_m + area_y = 100, the three pieces
combine into exactly MERGE_H × MERGE_W (10 × 10) electrodes when merged.

All electrode paths on the chip are hardcoded — the optimizer controls
ONLY how the merged volume is partitioned between the three colors.

Trial sequence
──────────────
  1.  Hold stash drops (20×20) at home positions
  2.  Optimizer picks volume fractions via 2-simplex → areas → piece shapes
  3.  Split a piece of each color (shape derived from area, not preset)
  4.  Move all three pieces to merge zone and combine into 10×10
  5.  Oscillate merged drop to mix the inks
  6.  Move merged drop to camera position
  7.  Capture average hex color via OpenCV
  8.  Evaluate CIEDE2000 dE, then slide drop off chip edge to unload
  9.  Replenish stash drops from reservoir wells
  10. Repeat up to N_TRIALS (stops early when dE < CONVERGENCE_DELTA_E)
"""

from __future__ import annotations

import ctypes
import json
import math
import random
import threading
import time
from ctypes import POINTER, Structure, c_int, c_void_p
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from skopt import Optimizer
from skopt.space import Real


# ══════════════════════════════════════════════════════════════════════════════
# Configuration  ← edit these before running
# ══════════════════════════════════════════════════════════════════════════════

N_TRIALS            = 8      # maximum optimization trials
N_INITIAL_POINTS    = 3      # random explorations before GP fitting
CONVERGENCE_DELTA_E = 2.0    # stop early if dE drops below this (< 2 = visually identical)
RANDOM_SEED         = 42
LOG_DIR             = Path("experiment_logs")

# Camera — set to match your USB camera index
CAMERA_ADDRESS = 0

# Voltage settings
VOLT_ON  = [45, 45, 45, 0, 0, 0, 0, 0, 0]
VOLT_OFF = [0,  0,  0,  0, 0, 0, 0, 0, 0]


# ══════════════════════════════════════════════════════════════════════════════
# Chip geometry  (row, col — top-left corner of each drop)
# ══════════════════════════════════════════════════════════════════════════════

# Stash drops: (name, home_row, home_col), all 20×20
STASH = [
    ("cyan",    4,   4),
    ("magenta", 4,  56),
    ("yellow",  4, 104),
]
MAIN_H, MAIN_W = 20, 20

# Reservoir wells for replenish — fluid moves from here to stash home
RESERVOIRS = {
    "cyan":    (0,   4),
    "magenta": (0,  56),
    "yellow":  (0, 104),
}

# Merged drop target size — pieces always combine to exactly this
MERGE_H, MERGE_W = 10, 10
MERGE_ROW, MERGE_COL = 68, 62   # where pieces meet and combine

# Mix parameters
MIX_CYCLES, MIX_AMP = 3, 6     # oscillate ±MIX_AMP cols, MIX_CYCLES times

# Camera read position
CAM_H,    CAM_W    = 10, 10
CAM_ROW,  CAM_COL  = 112, 52

# Unload: slide drop from camera position off the bottom edge
UNLOAD_ROW = 128    # one row past chip boundary — removes drop from grid


# ══════════════════════════════════════════════════════════════════════════════
# Volume parameterization
# ══════════════════════════════════════════════════════════════════════════════

def map_to_simplex(x1: float, x2: float) -> tuple[float, float, float]:
    """
    Bijection [0,1]² → 2-simplex (f_c + f_m + f_y = 1, each ≥ 0).
    Ensures the optimizer can freely explore all volume ratios.
    """
    f_c = float(x1)
    f_m = (1.0 - f_c) * float(x2)
    f_y = 1.0 - f_c - f_m
    return f_c, f_m, f_y


def fractions_to_areas(f_c: float, f_m: float, f_y: float) -> tuple[int, int, int]:
    """
    Convert simplex fractions to integer electrode areas (in electrodes) that
    sum to exactly MERGE_H × MERGE_W = 100.  Each area is ≥ 1 electrode.
    """
    total = MERGE_H * MERGE_W      # 100
    a_c = max(1, round(f_c * total))
    a_m = max(1, round(f_m * total))
    a_y = total - a_c - a_m
    if a_y < 1:
        a_y = 1
        if a_c >= a_m:
            a_c -= 1
        else:
            a_m -= 1
    return a_c, a_m, a_y


def vol_to_shape(vol: int) -> tuple[int, int]:
    """
    Compute (height, width) for a piece from its electrode area.
    Produces roughly-square pieces clamped to fit within the 20×20 stash drop.
    Neither dimension is preset — both are derived from the optimizer's area choice.
    """
    h = max(1, min(MAIN_H, round(math.sqrt(vol))))
    w = max(1, min(MAIN_W, round(vol / h)))
    return h, w


# ══════════════════════════════════════════════════════════════════════════════
# Hardware — DLL, Drop, and activate
# ══════════════════════════════════════════════════════════════════════════════

# Because the DLL is proprietary company software, I cannot share the actual
# DLL file or its file path. The placeholder below represents where the
# ACX-provided DLL would be loaded.
_dll = ctypes.CDLL("path_to_ACX_provided_DLL")  # ← replace with actual DLL path


class Drop(Structure):
    """Electrode drop descriptor passed to ActivateElec."""
    _fields_ = [
        ("height", c_int),
        ("width",  c_int),
        ("row",    c_int),
        ("col",    c_int),
    ]


_dll.SetPower.argtypes     = [ctypes.c_bool]
_dll.SetVolt.argtypes      = [c_int] * 9
_dll.InquireVolt.argtypes  = [POINTER(c_int)] * 9
_dll.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
_dll.ActivateElec.restype  = c_int


def _hw_activate(drops: list[Drop]) -> None:
    n   = len(drops)
    arr = (Drop * n)(*drops)
    _dll.ActivateElec(128, 128, n, arr)


def activate(drops: list[Drop], label: str = "") -> None:
    """Send one electrode activation call to the hardware."""
    _hw_activate(drops)
    time.sleep(0.3)


# ══════════════════════════════════════════════════════════════════════════════
# Background hold loop
# ══════════════════════════════════════════════════════════════════════════════

class HoldLoop:
    """
    Daemon thread that continuously re-activates a fixed drop list so they
    stay held on the chip during longer operations.
    Always call stop() before any manual electrode sequence, and start() after.
    """

    def __init__(self) -> None:
        self._drops: list[Drop] = []
        self._stop              = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_drops(self, drops: list[Drop]) -> None:
        self._drops = list(drops)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._drops:
                _hw_activate(self._drops)
            time.sleep(0.3)


# ══════════════════════════════════════════════════════════════════════════════
# Startup / shutdown
# ══════════════════════════════════════════════════════════════════════════════

def startup() -> None:
    """Initialize USB, power on, set and confirm voltage."""
    print("--- STARTUP ---")
    _dll.InitUSB()
    print("InitUSB called")

    if not _dll.OpenUSB():
        raise SystemExit("USB failed to open.")
    print("USB opened")

    _dll.SetPower(True)
    print("Power on")
    time.sleep(2)

    _dll.SetVolt(*VOLT_ON)
    print(f"Voltage set: {VOLT_ON}")
    time.sleep(1)

    voltages = [c_int(0) for _ in range(9)]
    _dll.InquireVolt(*[ctypes.byref(v) for v in voltages])
    actual = [v.value for v in voltages]
    print(f"Voltage confirmed: {actual}")

    if actual != VOLT_ON:
        print(f"  WARNING: mismatch — expected {VOLT_ON}")
        input("  Press Enter to continue anyway, or close this window to abort...")
    else:
        print("  Voltage OK\n")


def shutdown() -> None:
    """Power down and close USB."""
    _dll.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    _dll.SetPower(False)
    _dll.CloseUSB()
    print("Shutdown complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Movement helper
# ══════════════════════════════════════════════════════════════════════════════

def _move(h: int, w: int, r0: int, c0: int, r1: int, c1: int,
          held: list[Drop], label: str = "") -> None:
    """
    Move a drop (h×w) from (r0,c0) to (r1,c1) one electrode step at a time,
    re-activating all `held` drops at each step so they stay on-chip.
    """
    r, c = r0, c0
    while r != r1 or c != c1:
        if   r < r1: r += 1
        elif r > r1: r -= 1
        if   c < c1: c += 1
        elif c > c1: c -= 1
        activate(held + [Drop(h, w, r, c)])


def _stash_drops() -> list[Drop]:
    return [Drop(MAIN_H, MAIN_W, row, col) for _, row, col in STASH]


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Load and hold stash drops
# ══════════════════════════════════════════════════════════════════════════════

def step1_hold_stash(hold: HoldLoop) -> None:
    """
    Activate all three stash drops at home positions and hold them continuously.
    Waits for the operator to confirm all colors are loaded before proceeding.
    """
    print("[Step 1] Holding stash drops...")
    drops = _stash_drops()
    for d in drops:
        activate([d], f"HOLD ({d.row},{d.col})")
    hold.set_drops(drops)
    hold.start()

    input("  Load cyan at (4,4), magenta at (4,56), yellow at (4,104) — press Enter when ready...")
    print("  Stash held: cyan(4,4)  magenta(4,56)  yellow(4,104)\n")


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Split a piece from each stash color
# ══════════════════════════════════════════════════════════════════════════════

def step3_split_volumes(volumes: tuple[int, int, int], hold: HoldLoop) -> list[Drop]:
    """
    Split one piece from each stash drop.

    Each piece is split DOWNWARD from the 20×20 stash drop:
      1. Stretch the stash drop height down by piece_height rows (fluid bridge)
      2. Pattern: activate stash at home + piece at the bottom of the bridge
      3. Move piece step-by-step down to MERGE_ROW (same column as stash)

    Piece dimensions come entirely from vol_to_shape(area_i) — neither height
    nor width is hardcoded.  The three areas sum to MERGE_H × MERGE_W = 100,
    so the pieces combine into exactly 10 × 10 when merged.

    Returns three Drop objects, one per color, at row=MERGE_ROW and their
    stash column, ready to be swept horizontally to MERGE_COL.
    """
    hold.stop()
    shapes = [vol_to_shape(v) for v in volumes]
    a_c, a_m, a_y = volumes
    print(f"[Step 3] Splitting — cyan={a_c}el  magenta={a_m}el  yellow={a_y}el  "
          f"(total={a_c+a_m+a_y} electrodes = {MERGE_H}×{MERGE_W})")
    for (name, _, _), (ph, pw) in zip(STASH, shapes):
        print(f"  {name}: shape {ph}h × {pw}w = {ph*pw} electrodes")

    pieces: list[Drop] = []

    for i, ((name, s_row, s_col), vol, (ph, pw)) in enumerate(zip(STASH, volumes, shapes)):
        other_stash   = [Drop(MAIN_H, MAIN_W, r, c)
                         for j, (_, r, c) in enumerate(STASH) if j != i]
        pieces_so_far = list(pieces)
        base          = other_stash + pieces_so_far
        piece_row     = s_row + MAIN_H   # row immediately below stash drop

        # 1. Stretch stash drop downward, one row at a time, to bridge the gap
        for step in range(1, ph + 1):
            activate(base + [Drop(MAIN_H + step, MAIN_W, s_row, s_col)],
                     f"STRETCH {name} +{step}r")

        # 2. Pattern: separate main body from the piece
        activate(base + [
            Drop(MAIN_H, MAIN_W, s_row,    s_col),   # main stays at home
            Drop(ph,     pw,     piece_row, s_col),   # piece below
        ], f"PATTERN {name}")

        # 3. Walk piece downward to merge row; neck pinches and breaks naturally
        _move(ph, pw, piece_row, s_col, MERGE_ROW, s_col,
              base + [Drop(MAIN_H, MAIN_W, s_row, s_col)],
              label=f"{name} piece -> merge row")

        pieces.append(Drop(ph, pw, MERGE_ROW, s_col))

    hold.set_drops(_stash_drops())
    hold.start()
    return pieces


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Merge pieces into one 10×10 drop
# ══════════════════════════════════════════════════════════════════════════════

def step4_merge(pieces: list[Drop], hold: HoldLoop) -> None:
    """
    Sweep each piece horizontally to MERGE_COL so they overlap and combine,
    then activate the combined region as a single MERGE_H×MERGE_W drop.
    """
    hold.stop()
    stash = _stash_drops()
    print(f"[Step 4] Merging at ({MERGE_ROW},{MERGE_COL})...")

    for i, piece in enumerate(pieces):
        others = stash + [p for j, p in enumerate(pieces) if j != i]
        _move(piece.height, piece.width,
              piece.row, piece.col,
              MERGE_ROW, MERGE_COL,
              others, label=f"piece {i} -> merge col")
        pieces[i] = Drop(piece.height, piece.width, MERGE_ROW, MERGE_COL)

    # All three pieces now overlap — collapse to one 10×10 drop
    activate(stash + [Drop(MERGE_H, MERGE_W, MERGE_ROW, MERGE_COL)], "MERGE FINAL 10x10")

    hold.set_drops(_stash_drops())
    hold.start()


# ══════════════════════════════════════════════════════════════════════════════
# Step 5 — Mix merged drop by oscillating it
# ══════════════════════════════════════════════════════════════════════════════

def step5_mix(hold: HoldLoop) -> None:
    """
    Oscillate the merged 10×10 drop left/right MIX_CYCLES times by ±MIX_AMP
    columns to ensure the three inks are well mixed.
    Drop returns to MERGE_COL when done.
    """
    hold.stop()
    stash = _stash_drops()
    print(f"[Step 5] Mixing: {MIX_CYCLES} cycles ±{MIX_AMP} cols at ({MERGE_ROW},{MERGE_COL})...")

    r, c = MERGE_ROW, MERGE_COL
    for _ in range(MIX_CYCLES):
        _move(MERGE_H, MERGE_W, r, c,           r, c + MIX_AMP, stash, "mix →")
        _move(MERGE_H, MERGE_W, r, c + MIX_AMP, r, c - MIX_AMP, stash, "mix ←")
        _move(MERGE_H, MERGE_W, r, c - MIX_AMP, r, c,           stash, "mix center")

    hold.set_drops(_stash_drops())
    hold.start()


# ══════════════════════════════════════════════════════════════════════════════
# Step 6 — Move to camera read position
# ══════════════════════════════════════════════════════════════════════════════

def step6_move_to_camera(hold: HoldLoop) -> None:
    """Move the 10×10 merged drop from merge zone to camera read position."""
    hold.stop()
    stash = _stash_drops()
    print(f"[Step 6] Moving to camera at ({CAM_ROW},{CAM_COL})...")

    _move(MERGE_H, MERGE_W, MERGE_ROW, MERGE_COL, CAM_ROW, CAM_COL, stash, "→ camera")
    activate(stash + [Drop(CAM_H, CAM_W, CAM_ROW, CAM_COL)], "HOLD AT CAMERA")

    hold.set_drops(_stash_drops())
    hold.start()


# ══════════════════════════════════════════════════════════════════════════════
# Step 7 — Capture color via OpenCV
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ColorMeasurement:
    r: int;  g: int;  b: int

    @property
    def hex(self) -> str:
        return "#{:02x}{:02x}{:02x}".format(self.r, self.g, self.b)

    @property
    def bgr(self) -> tuple[int, int, int]:
        return (self.b, self.g, self.r)

    def __str__(self) -> str:
        return f"{self.hex}  rgb=({self.r},{self.g},{self.b})"


def _detect_drop_color(frame: np.ndarray,
                       min_area: int = 500,
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
      min_saturation (default 30) — broad threshold for contour detection.
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

    print(f"  Drop detected: {area:.0f} px²  "
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


def step7_read_color(camera: cv2.VideoCapture) -> ColorMeasurement:
    """
    Capture the color of the merged drop at the camera position.
    Takes 5 frames for stability, then uses the middle frame for detection.
    Automatically detects the colored drop using HSV saturation — no
    hardcoded coordinates needed. Works for all ink colors; excludes the
    gray electrode grid and chip background automatically.
    """
    print("[Step 7] Capturing color...")

    frames = []
    for _ in range(5):
        ok, frame = camera.read()
        if not ok:
            raise RuntimeError("Camera read failed during color capture.")
        frames.append(frame)

    # Use the middle frame — camera has had time to settle and auto-exposure
    # has stabilized after the drop arrived at the camera position.
    result = _detect_drop_color(frames[2])
    color  = ColorMeasurement(r=result["rgb"][0], g=result["rgb"][1], b=result["rgb"][2])
    print(f"  Captured: {color}")
    return color


# ══════════════════════════════════════════════════════════════════════════════
# CIEDE2000 color distance
# ══════════════════════════════════════════════════════════════════════════════

def _to_lab(color: ColorMeasurement) -> np.ndarray:
    patch = np.uint8([[list(color.bgr)]])
    ocv   = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[0, 0]
    return np.array([ocv[0] / 2.55, float(ocv[1]) - 128.0, float(ocv[2]) - 128.0])


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    L1, a1, b1 = float(lab1[0]), float(lab1[1]), float(lab1[2])
    L2, a2, b2 = float(lab2[0]), float(lab2[1]), float(lab2[2])
    C1, C2     = np.sqrt(a1**2 + b1**2), np.sqrt(a2**2 + b2**2)
    C_avg      = (C1 + C2) / 2.0;  C7 = C_avg**7
    G          = 0.5 * (1.0 - np.sqrt(C7 / (C7 + 25.0**7)))
    a1p, a2p   = a1 * (1.0 + G), a2 * (1.0 + G)
    C1p        = np.sqrt(a1p**2 + b1**2);  C2p = np.sqrt(a2p**2 + b2**2)
    h1p        = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p        = np.degrees(np.arctan2(b2, a2p)) % 360.0
    dLp        = L2 - L1;  dCp = C2p - C1p
    dhp        = (0.0             if C1p * C2p == 0        else
                  h2p - h1p       if abs(h2p - h1p) <= 180 else
                  h2p - h1p - 360 if h2p - h1p > 180       else
                  h2p - h1p + 360)
    dHp        = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2.0))
    Lp         = (L1 + L2) / 2.0;  Cp = (C1p + C2p) / 2.0
    hp         = (h1p + h2p         if C1p * C2p == 0          else
                  (h1p + h2p) / 2.0 if abs(h1p - h2p) <= 180   else
                  (h1p + h2p + 360) / 2.0 if h1p + h2p < 360   else
                  (h1p + h2p - 360) / 2.0)
    T   = (1.0 - 0.17 * np.cos(np.radians(hp - 30))
               + 0.24 * np.cos(np.radians(2*hp))
               + 0.32 * np.cos(np.radians(3*hp + 6))
               - 0.20 * np.cos(np.radians(4*hp - 63)))
    SL  = 1.0 + 0.015 * (Lp - 50)**2 / np.sqrt(20 + (Lp - 50)**2)
    SC  = 1.0 + 0.045 * Cp;  SH = 1.0 + 0.015 * Cp * T
    Cp7 = Cp**7
    RC  = 2.0 * np.sqrt(Cp7 / (Cp7 + 25.0**7))
    RT  = -np.sin(np.radians(60 * np.exp(-((hp - 275) / 25)**2))) * RC
    return float(np.sqrt(
        (dLp/SL)**2 + (dCp/SC)**2 + (dHp/SH)**2 + RT*(dCp/SC)*(dHp/SH)))


def delta_e(a: ColorMeasurement, b: ColorMeasurement) -> float:
    return ciede2000(_to_lab(a), _to_lab(b))


# ══════════════════════════════════════════════════════════════════════════════
# Step 8 — Evaluate result and unload drop
# ══════════════════════════════════════════════════════════════════════════════

def step8_evaluate_and_unload(result: ColorMeasurement, target: ColorMeasurement,
                               hold: HoldLoop) -> float:
    """
    Compute CIEDE2000 color distance, then slide the merged drop from the
    camera position (112,52) off the bottom chip edge (128,52) to unload it.
    """
    de = delta_e(result, target)
    print(f"[Step 8] Result: {result}   Target: {target}   dE={de:.2f}")

    hold.stop()
    stash = _stash_drops()

    # Picture has been taken — now unload: slide drop off the bottom edge
    _move(CAM_H, CAM_W, CAM_ROW, CAM_COL, UNLOAD_ROW, CAM_COL, stash, "unload off edge")
    activate(stash, "UNLOAD COMPLETE")

    hold.set_drops(_stash_drops())
    hold.start()
    return de


# ══════════════════════════════════════════════════════════════════════════════
# Step 9 — Replenish stash drops from reservoirs
# ══════════════════════════════════════════════════════════════════════════════

def step9_replenish(hold: HoldLoop) -> None:
    """
    Restore each stash drop to 20×20 by moving fresh fluid from the reservoir
    well down to the stash home position.
    Update RESERVOIRS at the top of this file to match your chip layout.
    """
    hold.stop()
    print("[Step 9] Replenishing stash drops...")

    for name, s_row, s_col in STASH:
        res_row, res_col = RESERVOIRS[name]
        other_stash = [Drop(MAIN_H, MAIN_W, r, c)
                       for n2, r, c in STASH if n2 != name]

        # Walk reservoir fluid down to stash position
        _move(MAIN_H, MAIN_W, res_row, res_col, s_row, s_col,
              other_stash, label=f"REPLENISH {name}")
        # Re-assert full size at home
        activate(other_stash + [Drop(MAIN_H, MAIN_W, s_row, s_col)],
                 f"STASH {name} RESTORED")

    hold.set_drops(_stash_drops())
    hold.start()


# ══════════════════════════════════════════════════════════════════════════════
# Target color
# ══════════════════════════════════════════════════════════════════════════════

def random_target_color(seed: Optional[int] = None) -> ColorMeasurement:
    """seed=None = truly random each run; integer seed = reproducible."""
    rng = random.Random(seed)
    return ColorMeasurement(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))


# ══════════════════════════════════════════════════════════════════════════════
# Trial record
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrialRecord:
    trial:          int
    frac_cyan:      float
    frac_magenta:   float
    frac_yellow:    float
    area_cyan:      int      # electrode area assigned to cyan
    area_magenta:   int
    area_yellow:    int
    shape_cyan:     str      # e.g. "5×4" (height × width computed by vol_to_shape)
    shape_magenta:  str
    shape_yellow:   str
    result_hex:     str
    target_hex:     str
    delta_e:        float
    is_best:        bool
    timestamp:      str = field(default_factory=lambda: datetime.now().isoformat())


# ══════════════════════════════════════════════════════════════════════════════
# Main experiment loop
# ══════════════════════════════════════════════════════════════════════════════

def run_experiment(target: ColorMeasurement, camera: cv2.VideoCapture) -> None:
    """
    Run up to N_TRIALS Bayesian optimization trials.

    The optimizer searches over [0,1]² → 2-simplex → (f_c, f_m, f_y) fractions
    → integer electrode areas summing to MERGE_H × MERGE_W = 100.
    Piece height and width are computed dynamically per trial by vol_to_shape()
    — neither is set in advance.

    All electrode paths are hardcoded — only the volume partitioning varies.
    """
    hold    = HoldLoop()
    history: list[TrialRecord] = []
    best_de                    = float("inf")
    best_fracs                 = (1/3, 1/3, 1/3)

    print("=" * 62)
    print("  CMY Color Match Experiment")
    print(f"  Target  : {target}")
    print(f"  Trials  : up to {N_TRIALS}  "
          f"({N_INITIAL_POINTS} random + {N_TRIALS-N_INITIAL_POINTS} GP-guided)")
    print(f"  Converge: dE < {CONVERGENCE_DELTA_E}")
    print(f"  Merged drop: {MERGE_H}×{MERGE_W}  at ({MERGE_ROW},{MERGE_COL})")
    print("=" * 62 + "\n")

    # Optimizer: 2D simplex space → 3 volume fractions
    optimizer = Optimizer(
        dimensions       = [Real(0.0, 1.0, name="x1"),
                             Real(0.0, 1.0, name="x2")],
        base_estimator   = "GP",
        acq_func         = "EI",
        n_initial_points = N_INITIAL_POINTS,
        random_state     = RANDOM_SEED,
    )

    # ── Step 1 ────────────────────────────────────────────────────────────────
    step1_hold_stash(hold)

    converged = False

    for trial in range(1, N_TRIALS + 1):
        print(f"\n{'─' * 62}")
        tag = "[SEED]" if trial <= N_INITIAL_POINTS else "[GP]  "
        print(f"  Trial {trial}/{N_TRIALS}  {tag}")

        # ── Step 2: optimizer picks volume fractions → areas → piece shapes ──
        suggestion = optimizer.ask()
        f_c, f_m, f_y = map_to_simplex(suggestion[0], suggestion[1])
        a_c, a_m, a_y = fractions_to_areas(f_c, f_m, f_y)
        (hc, wc), (hm, wm), (hy, wy) = vol_to_shape(a_c), vol_to_shape(a_m), vol_to_shape(a_y)
        print(f"[Step 2] Fractions — cyan={f_c:.3f}  magenta={f_m:.3f}  yellow={f_y:.3f}")
        print(f"         Areas    — cyan={a_c}  magenta={a_m}  yellow={a_y}  "
              f"(sum={a_c+a_m+a_y} = {MERGE_H}×{MERGE_W})")
        print(f"         Shapes   — cyan={hc}×{wc}  magenta={hm}×{wm}  yellow={hy}×{wy}")

        # ── Step 3: split ──────────────────────────────────────────────────
        pieces = step3_split_volumes((a_c, a_m, a_y), hold)

        # ── Step 4: merge ──────────────────────────────────────────────────
        step4_merge(pieces, hold)

        # ── Step 5: mix ────────────────────────────────────────────────────
        step5_mix(hold)

        # ── Step 6: move to camera ─────────────────────────────────────────
        step6_move_to_camera(hold)

        # ── Step 7: read color ─────────────────────────────────────────────
        result = step7_read_color(camera)

        # ── Step 8: evaluate and unload ────────────────────────────────────
        de      = step8_evaluate_and_unload(result, target, hold)
        is_best = de < best_de
        if is_best:
            best_de    = de
            best_fracs = (f_c, f_m, f_y)

        optimizer.tell(suggestion, de)
        history.append(TrialRecord(
            trial=trial,
            frac_cyan=f_c, frac_magenta=f_m, frac_yellow=f_y,
            area_cyan=a_c, area_magenta=a_m, area_yellow=a_y,
            shape_cyan=f"{hc}×{wc}", shape_magenta=f"{hm}×{wm}", shape_yellow=f"{hy}×{wy}",
            result_hex=result.hex, target_hex=target.hex,
            delta_e=de, is_best=is_best,
        ))
        print(f"  dE={de:.2f}   best={best_de:.2f}"
              + ("   *** NEW BEST" if is_best else ""))

        if de < CONVERGENCE_DELTA_E:
            print(f"\n  Converged at trial {trial}  (dE={de:.2f})")
            converged = True
            break

        # ── Step 9: replenish (skip after last trial) ──────────────────────
        if trial < N_TRIALS:
            step9_replenish(hold)

    hold.stop()

    # ── Summary ────────────────────────────────────────────────────────────
    best_areas  = fractions_to_areas(*best_fracs)
    best_shapes = [vol_to_shape(v) for v in best_areas]
    print(f"\n{'=' * 62}")
    print("  EXPERIMENT COMPLETE")
    print(f"  Best dE   : {best_de:.2f}  "
          + ("(visually identical)" if best_de < 2.0 else ""))
    print(f"  Best fracs: cyan={best_fracs[0]:.3f}  "
          f"magenta={best_fracs[1]:.3f}  yellow={best_fracs[2]:.3f}")
    print(f"  Best areas: cyan={best_areas[0]}  magenta={best_areas[1]}  yellow={best_areas[2]}  "
          f"(sum={sum(best_areas)})")
    for (name, _, _), (h, w) in zip(STASH, best_shapes):
        print(f"  Best shape {name}: {h}h × {w}w = {h*w} electrodes")
    print(f"  Target    : {target.hex}")
    print(f"  Converged : {converged}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"experiment_{ts}.json"
    with path.open("w") as fh:
        json.dump({
            "target":        str(target),
            "best_delta_e":  best_de,
            "best_fracs":    {"cyan": best_fracs[0], "magenta": best_fracs[1], "yellow": best_fracs[2]},
            "best_areas":    {"cyan": best_areas[0], "magenta": best_areas[1], "yellow": best_areas[2]},
            "best_shapes":   {"cyan": f"{best_shapes[0][0]}×{best_shapes[0][1]}",
                              "magenta": f"{best_shapes[1][0]}×{best_shapes[1][1]}",
                              "yellow": f"{best_shapes[2][0]}×{best_shapes[2][1]}"},
            "converged":     converged,
            "n_trials_run":  len(history),
            "history":       [asdict(r) for r in history],
        }, fh, indent=2)
    print(f"  Log saved : {path}")
    print("=" * 62 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    startup()

    # One random target color is chosen at the start; all trials try to match it
    target = random_target_color(seed=None)
    print(f"Target color: {target}\n")

    cap = cv2.VideoCapture(CAMERA_ADDRESS, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    for _ in range(15):      # warm up camera before experiment
        cap.read()
    try:
        run_experiment(target, camera=cap)
    finally:
        cap.release()
        shutdown()