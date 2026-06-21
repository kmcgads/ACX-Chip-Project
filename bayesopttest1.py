"""
bayesian_color_optimizer.py
───────────────────────────────────────────────────────────────────────────────
Bayesian optimization over CMY ink volumes to match a target color on a
microfluidic chip, observed through a USB camera.

Every stage requires explicit user confirmation before proceeding:
  1. Target color confirmation
  2. Camera connection and reference picture
  3. ROI selection confirmation
  4. Each optimization trial (suggestion → dispense → measure → result)
  5. Early-stop / continue prompt when convergence is reached
  6. Final summary confirmation before saving the log

Run TEST_MODE=True to validate the optimizer without any hardware.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np
from skopt import Optimizer
from skopt.space import Real


# ══════════════════════════════════════════════════════════════════════════════
# Console formatting helpers
# ══════════════════════════════════════════════════════════════════════════════

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_DIM    = "\033[2m"


def _color_de(de: float) -> str:
    """Colour-code a \DeltaE value: green < 2, yellow < 5, red ≥ 5."""
    if de < 2.0:
        return f"{_GREEN}{de:6.2f}{_RESET}"
    if de < 5.0:
        return f"{_YELLOW}{de:6.2f}{_RESET}"
    return f"{_RED}{de:6.2f}{_RESET}"


def _bar(value: float, width: int = 20) -> str:
    """ASCII progress bar for a value in [0, 1]."""
    filled = round(value * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def _swatch(hex_color: str) -> str:
    """ANSI 24-bit colour block beside the hex string."""
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"\033[48;2;{r};{g};{b}m   {_RESET} {hex_color}"
    except ValueError:
        return hex_color


def _divider(char: str = "─", width: int = 72) -> str:
    return char * width


def _header(title: str, width: int = 72) -> str:
    pad = (width - len(title) - 2) // 2
    return f"{'═' * pad} {_BOLD}{title}{_RESET} {'═' * pad}"


# ══════════════════════════════════════════════════════════════════════════════
# User-input helpers
# ══════════════════════════════════════════════════════════════════════════════

def _pause(prompt: str = "Press ENTER to continue...") -> None:
    """Block until the user presses ENTER."""
    input(f"\n  {_CYAN}▶  {prompt}{_RESET}  ")


def _confirm(prompt: str) -> bool:
    """
    Ask a yes/no question.
    Returns True for 'y' / 'yes', False for 'n' / 'no'.
    Keeps asking until a valid answer is given.
    """
    while True:
        answer = input(f"\n  {_CYAN}?  {prompt} [y/n]: {_RESET}").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("     Please enter y or n.")


def _choose(prompt: str, options: dict[str, str]) -> str:
    """
    Present a numbered menu and return the chosen key.

    Parameters
    ----------
    prompt  : str
        Question shown above the menu.
    options : dict[str, str]
        Mapping of key → description shown to the user.
    """
    keys = list(options.keys())
    print(f"\n  {_CYAN}?  {prompt}{_RESET}")
    for i, (k, desc) in enumerate(options.items(), 1):
        print(f"     {i}) {desc}")
    while True:
        raw = input(f"  Enter choice (1–{len(keys)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        print(f"     Please enter a number between 1 and {len(keys)}.")


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ColorMeasurement:
    """Holds a single color reading from the camera (or simulator)."""
    r: int
    g: int
    b: int

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    @property
    def bgr(self) -> tuple[int, int, int]:
        return (self.b, self.g, self.r)

    @property
    def hex(self) -> str:
        return "#{:02x}{:02x}{:02x}".format(self.r, self.g, self.b)

    def __str__(self) -> str:
        return f"{self.hex}  rgb=({self.r:3d},{self.g:3d},{self.b:3d})"


@dataclass
class OptimizationStep:
    """Records every trial during the optimization run."""
    iteration:   int
    vol_cyan:    float
    vol_magenta: float
    vol_yellow:  float
    result_hex:  str
    delta_e:     float
    is_best:     bool = False
    skipped:     bool = False   # True if the user skipped this trial
    timestamp:   str  = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class OptimizationResult:
    """Final result returned by ColorOptimizer.run()."""
    vol_cyan:     float
    vol_magenta:  float
    vol_yellow:   float
    best_delta_e: float
    converged:    bool
    history:      list[OptimizationStep]
    log_path:     Optional[Path]


# ══════════════════════════════════════════════════════════════════════════════
# CIEDE2000 color distance
# ══════════════════════════════════════════════════════════════════════════════

def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """
    Full CIEDE2000 perceptual color difference.
    lab1 / lab2 must be in standard CIE L*a*b* (L ∈ [0, 100]).
    """
    L1, a1, b1 = float(lab1[0]), float(lab1[1]), float(lab1[2])
    L2, a2, b2 = float(lab2[0]), float(lab2[1]), float(lab2[2])

    C1      = np.sqrt(a1**2 + b1**2)
    C2      = np.sqrt(a2**2 + b2**2)
    C_avg   = (C1 + C2) / 2.0
    C_avg7  = C_avg**7
    G       = 0.5 * (1.0 - np.sqrt(C_avg7 / (C_avg7 + 25.0**7)))
    a1p     = a1 * (1.0 + G)
    a2p     = a2 * (1.0 + G)
    C1p     = np.sqrt(a1p**2 + b1**2)
    C2p     = np.sqrt(a2p**2 + b2**2)
    h1p     = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p     = np.degrees(np.arctan2(b2, a2p)) % 360.0

    dLp = L2 - L1
    dCp = C2p - C1p

    if C1p * C2p == 0.0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180.0:
        dhp = h2p - h1p
    elif h2p - h1p > 180.0:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0

    dHp     = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2.0))
    Lp_avg  = (L1 + L2) / 2.0
    Cp_avg  = (C1p + C2p) / 2.0

    if C1p * C2p == 0.0:
        hp_avg = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        hp_avg = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        hp_avg = (h1p + h2p + 360.0) / 2.0
    else:
        hp_avg = (h1p + h2p - 360.0) / 2.0

    T = (
        1.0
        - 0.17 * np.cos(np.radians(hp_avg - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hp_avg))
        + 0.32 * np.cos(np.radians(3.0 * hp_avg + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hp_avg - 63.0))
    )

    SL      = 1.0 + 0.015 * (Lp_avg - 50.0)**2 / np.sqrt(20.0 + (Lp_avg - 50.0)**2)
    SC      = 1.0 + 0.045 * Cp_avg
    SH      = 1.0 + 0.015 * Cp_avg * T
    Cp_avg7 = Cp_avg**7
    RC      = 2.0 * np.sqrt(Cp_avg7 / (Cp_avg7 + 25.0**7))
    d_theta = 30.0 * np.exp(-((hp_avg - 275.0) / 25.0)**2)
    RT      = -np.sin(np.radians(2.0 * d_theta)) * RC

    return float(np.sqrt(
        (dLp / SL)**2 + (dCp / SC)**2 + (dHp / SH)**2
        + RT * (dCp / SC) * (dHp / SH)
    ))


def _opencv_lab_to_standard(ocv_lab: np.ndarray) -> np.ndarray:
    """Convert OpenCV uint8 LAB → standard CIE L*a*b* floats."""
    return np.array([
        ocv_lab[0] / 2.55,
        float(ocv_lab[1]) - 128.0,
        float(ocv_lab[2]) - 128.0,
    ], dtype=np.float64)


# ══════════════════════════════════════════════════════════════════════════════
# Camera interface
# ══════════════════════════════════════════════════════════════════════════════

class CameraError(RuntimeError):
    """Raised when the camera cannot be opened or read."""


class CameraInterface:
    """
    Thin wrapper around cv2.VideoCapture.

    Use as a context manager so the camera stays open for the whole
    experiment and settings are locked once at startup:

        with CameraInterface(0) as cam:
            color = cam.get_average_color_from_rectangle(...)
    """

    def __init__(
        self,
        camera_address:       Union[int, str] = 0,
        warmup_frames:        int  = 15,
        measure_frames:       int  = 5,
        roi_margin:           int  = 5,
        lock_camera_settings: bool = True,
    ) -> None:
        self.camera_address       = self._parse_address(camera_address)
        self.warmup_frames        = warmup_frames
        self.measure_frames       = measure_frames
        self.roi_margin           = roi_margin
        self.lock_camera_settings = lock_camera_settings
        self._camera: Optional[cv2.VideoCapture] = None

    def __enter__(self) -> "CameraInterface":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def open(self) -> None:
        if self._camera is not None and self._camera.isOpened():
            return
        cam = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
        cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not cam.isOpened():
            raise CameraError(
                f"Unable to open camera at address '{self.camera_address}'"
            )
        if self.lock_camera_settings:
            self._lock_settings(cam)
        for _ in range(self.warmup_frames):
            cam.read()
        self._camera = cam

    def close(self) -> None:
        if self._camera is not None and self._camera.isOpened():
            self._camera.release()
        self._camera = None

    # ── Settings ───────────────────────────────────────────────────────────────

    @staticmethod
    def _lock_settings(cam: cv2.VideoCapture) -> None:
        for prop, val in [
            (cv2.CAP_PROP_AUTO_EXPOSURE, 0.25),
            (cv2.CAP_PROP_AUTOFOCUS,     0),
        ]:
            cam.set(prop, val)

    def set_focus(self, focus: int, autofocus: bool = False) -> None:
        self._require_open()
        self._camera.set(cv2.CAP_PROP_AUTOFOCUS, 1 if autofocus else 0)
        if autofocus is False and focus is not None:
            if not (0 <= focus <= 255):
                raise ValueError(f"Focus must be in [0, 255], got {focus}.")
            self._camera.set(cv2.CAP_PROP_FOCUS, focus)
        for _ in range(30):
            self._camera.read()

    # ── Measurement ────────────────────────────────────────────────────────────

    def get_average_color_from_rectangle(
        self, x: int, y: int, width: int, height: int,
    ) -> ColorMeasurement:
        self._require_open()
        frames = []
        for _ in range(self.measure_frames):
            ok, frame = self._camera.read()
            if not ok:
                raise CameraError("Camera read failed during color measurement.")
            frames.append(frame)

        h_img, w_img = frames[0].shape[:2]
        self._validate_roi(x, y, width, height, w_img, h_img)

        rois = []
        for frame in frames:
            roi = frame[y:y + height, x:x + width]
            m = self.roi_margin
            if m > 0 and roi.shape[0] > 2 * m and roi.shape[1] > 2 * m:
                roi = roi[m:-m, m:-m]
            rois.append(roi.reshape(-1, 3))

        pixels     = np.concatenate(rois, axis=0)
        brightness = pixels.sum(axis=1)
        lo, hi     = np.percentile(brightness, [5, 95])
        mask       = (brightness >= lo) & (brightness <= hi)
        if mask.sum() > 0:
            pixels = pixels[mask]

        avg_bgr = np.median(pixels, axis=0).astype(int)
        b, g, r = int(avg_bgr[0]), int(avg_bgr[1]), int(avg_bgr[2])
        return ColorMeasurement(r=r, g=g, b=b)

    # ── Utilities ──────────────────────────────────────────────────────────────

    def take_picture(self) -> Path:
        self._require_open()
        ok, frame = self._camera.read()
        if not ok:
            raise CameraError("Camera read failed while taking picture.")
        path = Path(datetime.now().strftime("microscope_%Y%m%d_%H%M%S.jpg"))
        cv2.imwrite(str(path), frame)
        return path

    def pick_roi_interactively(self) -> tuple[int, int, int, int]:
        self._require_open()
        for _ in range(5):
            self._camera.read()
        ok, frame = self._camera.read()
        if not ok:
            raise CameraError("Camera read failed during ROI selection.")
        print("\n  Draw a rectangle over the well, then press SPACE or ENTER.")
        roi = cv2.selectROI(
            "Select Well ROI  (SPACE/ENTER = confirm,  C = cancel)",
            frame, showCrosshair=True,
        )
        cv2.destroyAllWindows()
        x, y, w, h = (int(v) for v in roi)
        if w == 0 or h == 0:
            raise ValueError("No ROI selected — width or height is zero.")
        return x, y, w, h

    def test_connection(self) -> bool:
        try:
            with CameraInterface(
                self.camera_address, warmup_frames=3, lock_camera_settings=False
            ) as cam:
                ok, _ = cam._camera.read()
                return ok
        except CameraError:
            return False

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _require_open(self) -> None:
        if self._camera is None or not self._camera.isOpened():
            raise CameraError(
                "Camera is not open. Call open() or use the context manager."
            )

    @staticmethod
    def _parse_address(addr: Union[int, str]) -> Union[int, str]:
        try:
            return int(addr)
        except (ValueError, TypeError):
            return addr

    @staticmethod
    def _validate_roi(
        x: int, y: int, width: int, height: int, w_img: int, h_img: int
    ) -> None:
        if (
            x < 0 or y < 0
            or width <= 0 or height <= 0
            or x + width > w_img
            or y + height > h_img
        ):
            raise ValueError(
                f"ROI ({x},{y},{width},{height}) is outside the "
                f"image ({w_img}×{h_img})."
            )


# ══════════════════════════════════════════════════════════════════════════════
# CMY simulator (realistic dry-run)
# ══════════════════════════════════════════════════════════════════════════════

def simulate_cmy_mix(
    vol_cyan: float,
    vol_magenta: float,
    vol_yellow: float,
    *,
    gamma: float = 1.4,
    cross_coupling: float = 0.08,
    noise_std: float = 3.0,
    rng: Optional[np.random.Generator] = None,
) -> ColorMeasurement:
    """
    Realistic subtractive CMY mixing simulator with gamma nonlinearity,
    cross-channel coupling, and Gaussian sensor noise.
    """
    if rng is None:
        rng = np.random.default_rng()

    r = 255.0 * (1.0 - vol_cyan    ** gamma)
    g = 255.0 * (1.0 - vol_magenta ** gamma)
    b = 255.0 * (1.0 - vol_yellow  ** gamma)

    r -= 255.0 * cross_coupling * (vol_magenta + vol_yellow) / 2.0
    g -= 255.0 * cross_coupling * (vol_cyan    + vol_yellow) / 2.0
    b -= 255.0 * cross_coupling * (vol_cyan    + vol_magenta) / 2.0

    noise = rng.normal(0.0, noise_std, 3)
    rgb   = np.clip(np.array([r, g, b]) + noise, 0, 255).astype(int)
    return ColorMeasurement(r=int(rgb[0]), g=int(rgb[1]), b=int(rgb[2]))


# ══════════════════════════════════════════════════════════════════════════════
# Color helpers
# ══════════════════════════════════════════════════════════════════════════════

def hex_to_color_measurement(hex_color: str) -> ColorMeasurement:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: '{hex_color}'")
    return ColorMeasurement(
        r=int(h[0:2], 16), g=int(h[2:4], 16), b=int(h[4:6], 16)
    )


def _bgr_to_standard_lab(bgr: tuple[int, int, int]) -> np.ndarray:
    patch   = np.uint8([[list(bgr)]])
    ocv_lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[0, 0]
    return _opencv_lab_to_standard(ocv_lab)


def delta_e_ciede2000(a: ColorMeasurement, b: ColorMeasurement) -> float:
    return ciede2000(_bgr_to_standard_lab(a.bgr), _bgr_to_standard_lab(b.bgr))


def random_target_color(seed: Optional[int] = None) -> ColorMeasurement:
    rng = random.Random(seed)
    return ColorMeasurement(
        r=rng.randint(0, 255),
        g=rng.randint(0, 255),
        b=rng.randint(0, 255),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Simplex parameterisation
# ══════════════════════════════════════════════════════════════════════════════

def map_to_simplex(c_raw: float, m_raw: float) -> tuple[float, float, float]:
    """
    Bijection from [0,1]² → 2-simplex (c + m + y = 1, all ≥ 0).
    Eliminates the degenerate 3-D search space of the original code.
    """
    c = float(c_raw)
    m = (1.0 - c) * float(m_raw)
    y = 1.0 - c - m
    return c, m, y


# ══════════════════════════════════════════════════════════════════════════════
# Results printer
# ══════════════════════════════════════════════════════════════════════════════

class ResultsPrinter:
    """
    Prints a formatted progress table after every trial and a full summary
    report at the end of the optimization run.
    """

    def __init__(self, target: ColorMeasurement, n_calls: int) -> None:
        self.target   = target
        self.n_calls  = n_calls
        self._best_de = float("inf")

    # ── Run header ─────────────────────────────────────────────────────────────

    def print_run_header(
        self,
        mode: str,
        n_initial: int,
        convergence_de: float,
        seed: int,
    ) -> None:
        W = 72
        print()
        print(_header("Bayesian CMY Color Optimizer", W))
        print(f"  Mode          : {_BOLD}{mode}{_RESET}")
        print(f"  Target color  : {_swatch(self.target.hex)}   "
              f"rgb=({self.target.r},{self.target.g},{self.target.b})")
        print(f"  Total calls   : {self.n_calls}  "
              f"({n_initial} random seed + "
              f"{self.n_calls - n_initial} GP-guided)")
        print(f"  Converge at   : \DeltaE₀₀ < {convergence_de:.1f}  "
              f"(< 2.0 = visually identical)")
        print(f"  Random seed   : {seed}")
        print(_divider("═", W))
        print()
        self._print_table_header()

    # ── Per-trial row ──────────────────────────────────────────────────────────

    def print_step(self, step: OptimizationStep) -> None:
        """Print one live row immediately after a trial completes."""
        is_new_best = step.is_best
        prev_best   = self._best_de

        if step.skipped:
            print(
                f"  {_DIM}{step.iteration:>3d}/{self.n_calls:<3d}"
                f"  ── skipped by user ──{_RESET}"
            )
            print(f"  {_DIM}{_divider('·', 68)}{_RESET}")
            return

        if is_new_best:
            improvement = (
                f"  {_GREEN}▼ {prev_best - step.delta_e:+.2f}{_RESET}"
                if prev_best != float("inf") else ""
            )
            status = f"{_GREEN}{_BOLD}NEW BEST ★{_RESET}{improvement}"
            self._best_de = step.delta_e
        else:
            gap    = step.delta_e - self._best_de
            status = f"{_DIM}+{gap:.2f} from best{_RESET}"

        c_bar = _bar(step.vol_cyan,    10)
        m_bar = _bar(step.vol_magenta, 10)
        y_bar = _bar(step.vol_yellow,  10)

        print(
            f"  {_BOLD}{step.iteration:>3d}{_RESET}/{self.n_calls:<3d}"
            f"  C={_CYAN}{step.vol_cyan:.2f}{_RESET}{c_bar}"
            f"  M={step.vol_magenta:.2f}{m_bar}"
            f"  Y={step.vol_yellow:.2f}{y_bar}"
        )
        print(
            f"  {'':8s}"
            f"  Result: {_swatch(step.result_hex)}"
            f"   \DeltaE₀₀={_color_de(step.delta_e)}"
            f"   Best={_color_de(self._best_de)}"
            f"   {status}"
        )
        print(f"  {_DIM}{_divider('·', 68)}{_RESET}")

    # ── Convergence notice ─────────────────────────────────────────────────────

    def print_convergence(self, iteration: int, de: float) -> None:
        print()
        print(f"  {_GREEN}{_BOLD}✔  Converged at iteration {iteration}  "
              f"(\DeltaE₀₀ = {de:.2f}){_RESET}")
        print()

    # ── Final summary ──────────────────────────────────────────────────────────

    def print_summary(self, result: OptimizationResult) -> None:
        W = 72
        print()
        print(_header("Optimization Summary", W))

        # Best recipe
        print(f"\n  {_BOLD}Best CMY recipe{_RESET}")
        print(f"    Cyan    : {result.vol_cyan:.4f}  {_bar(result.vol_cyan)}")
        print(f"    Magenta : {result.vol_magenta:.4f}  {_bar(result.vol_magenta)}")
        print(f"    Yellow  : {result.vol_yellow:.4f}  {_bar(result.vol_yellow)}")
        print(f"    \DeltaE₀₀    : {_color_de(result.best_delta_e)}  "
              f"{'← visually identical ✔' if result.best_delta_e < 2.0 else ''}")
        print(f"    Converged early : {result.converged}")

        # Color comparison
        print(f"\n  {_BOLD}Color comparison{_RESET}")
        print(f"    Target  : {_swatch(self.target.hex)}")
        best_step = min(
            (s for s in result.history if not s.skipped),
            key=lambda s: s.delta_e,
        )
        print(f"    Result  : {_swatch(best_step.result_hex)}")

        # \DeltaE progression table
        real_steps = [s for s in result.history if not s.skipped]
        print(f"\n  {_BOLD}\DeltaE₀₀ progression{_RESET}")
        print(f"  {'Iter':>4}  {'\DeltaE₀₀':>7}  {'Best so far':>11}  Trend")
        print(f"  {_divider('-', 50)}")
        running_best = float("inf")
        max_de = max((s.delta_e for s in real_steps), default=1.0)
        for step in result.history:
            if step.skipped:
                print(f"  {step.iteration:>4d}  {'─ skipped ─':>18}")
                continue
            running_best = min(running_best, step.delta_e)
            bar_len = round((step.delta_e / max_de) * 30)
            trend   = f"{_RED}{'▓' * bar_len}{_RESET}" if bar_len > 0 else ""
            marker  = f" {_GREEN}★{_RESET}" if step.is_best else "  "
            print(
                f"  {step.iteration:>4d}  "
                f"{_color_de(step.delta_e)}  "
                f"{_color_de(running_best):>11}  "
                f"{trend}{marker}"
            )

        # Statistics (skipped trials excluded)
        des = [s.delta_e for s in real_steps]
        if des:
            print(f"\n  {_BOLD}Statistics{_RESET}  "
                  f"{_DIM}(skipped trials excluded){_RESET}")
            print(f"    Trials run : {len(des)}  "
                  f"({len(result.history) - len(des)} skipped)")
            print(f"    Best  \DeltaE₀₀ : {_color_de(min(des))}")
            print(f"    Worst \DeltaE₀₀ : {_color_de(max(des))}")
            print(f"    Mean  \DeltaE₀₀ : {_color_de(float(np.mean(des)))}")
            print(f"    Std   \DeltaE₀₀ : {np.std(des):.2f}")

            # Improvement over random baseline (first n_initial real trials)
            baseline_des = des[:5]
            if baseline_des:
                random_de       = float(np.mean(baseline_des))
                improvement_pct = 100.0 * (random_de - result.best_delta_e) / (random_de + 1e-9)
                print(f"\n  {_BOLD}Improvement vs random baseline{_RESET}")
                print(f"    Random-phase mean \DeltaE₀₀ : {random_de:.2f}")
                print(f"    Best \DeltaE₀₀             : {result.best_delta_e:.2f}")
                print(f"    Improvement           : {_GREEN}{improvement_pct:.1f}%{_RESET}")

        if result.log_path:
            print(f"\n  {_BOLD}Experiment log{_RESET} : {result.log_path}")

        print()
        print(_divider("═", W))
        print()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _print_table_header(self) -> None:
        print(
            f"  {'Iter':>7}  "
            f"{'CMY volumes + bars':<62}"
        )
        print(
            f"  {'':>7}  "
            f"{'Result hex':<18}  "
            f"{'\DeltaE₀₀':>8}  "
            f"{'Best':>8}  "
            f"Status"
        )
        print(f"  {_divider('─', 68)}")


# ══════════════════════════════════════════════════════════════════════════════
# Bayesian color optimizer
# ══════════════════════════════════════════════════════════════════════════════

class ColorOptimizer:
    """
    Bayesian optimization over CMY ink volumes to match a target color.

    Every stage requires explicit user confirmation:
      • Before the first trial (run header confirmed)
      • Before each trial  (suggestion shown, user chooses apply / skip / stop)
      • After each trial   (result shown, user presses ENTER to continue)
      • On convergence     (user chooses to stop or keep going)
      • Before saving log  (user confirms)
    """

    SPACE = [
        Real(0.0, 1.0, name="c_raw"),
        Real(0.0, 1.0, name="m_raw"),
    ]

    def __init__(
        self,
        target:               ColorMeasurement,
        camera:               Optional[CameraInterface] = None,
        well_roi:             Optional[tuple[int, int, int, int]] = None,
        n_calls:              int   = 25,
        n_initial_points:     int   = 5,
        settle_seconds:       float = 1.5,
        convergence_delta_e:  float = 2.0,
        log_dir:              Path  = Path("."),
        random_seed:          int   = 42,
        test_mode:            bool  = False,
    ) -> None:
        if not test_mode and (camera is None or well_roi is None):
            raise ValueError(
                "camera and well_roi are required when test_mode=False."
            )

        self.target              = target
        self.camera              = camera
        self.well_roi            = well_roi
        self.n_calls             = n_calls
        self.n_initial_points    = n_initial_points
        self.settle_seconds      = settle_seconds
        self.convergence_delta_e = convergence_delta_e
        self.log_dir             = Path(log_dir)
        self.random_seed         = random_seed
        self.test_mode           = test_mode

        self._rng       = np.random.default_rng(random_seed)
        self._history:  list[OptimizationStep] = []
        self._iteration = 0
        self._best_de   = float("inf")
        self._best_params: list[float] = [0.5, 0.5]

        self._printer = ResultsPrinter(target, n_calls)

    # ── Dispensing ─────────────────────────────────────────────────────────────

    def _dispense(
        self, vol_cyan: float, vol_magenta: float, vol_yellow: float
    ) -> None:
        MAX_PIECE_WIDTH = 25
        w_c = round(vol_cyan    * MAX_PIECE_WIDTH)
        w_m = round(vol_magenta * MAX_PIECE_WIDTH)
        w_y = round(vol_yellow  * MAX_PIECE_WIDTH)
        print(
            f"\n  {_BOLD}Dispensing{_RESET}  "
            f"cyan_width={w_c}  magenta_width={w_m}  yellow_width={w_y}"
        )

        # ── Uncomment when wiring to microfluidics.py ──────────────────────
        # from microfluidics import activate, Drop, DROP1_ROW, DROP2_ROW, DROP3_ROW, PIECE_FINAL_COL
        # activate([
        #     Drop(10, w_c, DROP1_ROW, PIECE_FINAL_COL),
        #     Drop(10, w_m, DROP2_ROW, PIECE_FINAL_COL),
        #     Drop(10, w_y, DROP3_ROW, PIECE_FINAL_COL),
        # ])
        time.sleep(self.settle_seconds)

    # ── Single evaluation ──────────────────────────────────────────────────────

    def _evaluate(
        self, vol_cyan: float, vol_magenta: float, vol_yellow: float
    ) -> tuple[ColorMeasurement, float]:
        """Dispense (or simulate), capture color, return (measurement, \DeltaE)."""
        if self.test_mode:
            color = simulate_cmy_mix(
                vol_cyan, vol_magenta, vol_yellow, rng=self._rng
            )
        else:
            self._dispense(vol_cyan, vol_magenta, vol_yellow)

            # ── User confirms the chip has settled before we capture ───────
            _pause("Chip has settled. Press ENTER to capture the color...")

            x, y, w, h = self.well_roi
            color = self.camera.get_average_color_from_rectangle(
                x=x, y=y, width=w, height=h
            )

        de = delta_e_ciede2000(color, self.target)
        return color, de

    # ── Per-trial user gate ────────────────────────────────────────────────────

    def _show_suggestion_and_ask(
        self,
        iteration: int,
        vol_cyan: float,
        vol_magenta: float,
        vol_yellow: float,
    ) -> str:
        """
        Display the optimizer's suggestion and ask the user what to do.

        Returns
        -------
        "apply"  – run this trial normally
        "skip"   – record a skipped step and move on
        "stop"   – abort the optimization loop immediately
        """
        print()
        print(_divider("─"))
        print(
            f"  {_BOLD}Trial {iteration}/{self.n_calls}{_RESET}  "
            f"{'[RANDOM SEED]' if iteration <= self.n_initial_points else '[GP-GUIDED]'}"
        )
        print(f"  Target  : {_swatch(self.target.hex)}")
        print(f"\n  {_BOLD}Suggested CMY volumes{_RESET}")
        print(f"    Cyan    : {vol_cyan:.4f}  {_bar(vol_cyan)}")
        print(f"    Magenta : {vol_magenta:.4f}  {_bar(vol_magenta)}")
        print(f"    Yellow  : {vol_yellow:.4f}  {_bar(vol_yellow)}")

        return _choose(
            "What would you like to do with this suggestion?",
            {
                "apply": "Apply suggestion  (dispense / simulate → measure → record)",
                "skip":  "Skip this trial   (optimizer will try a different point next)",
                "stop":  "Stop optimization (save results so far and exit)",
            },
        )

    # ── Objective ──────────────────────────────────────────────────────────────

    def _objective(self, params: list[float]) -> tuple[float, bool]:
        """
        Run one full trial with user confirmation at every sub-step.

        Returns
        -------
        (score, skipped)
            score   – \DeltaE₀₀ (or 200.0 if skipped so the GP avoids the region)
            skipped – True if the user chose to skip this trial
        """
        c_raw, m_raw = params
        vol_cyan, vol_magenta, vol_yellow = map_to_simplex(c_raw, m_raw)
        self._iteration += 1

        # ── Step 1: show suggestion, ask user ─────────────────────────────
        action = self._show_suggestion_and_ask(
            self._iteration, vol_cyan, vol_magenta, vol_yellow
        )

        if action == "stop":
            return 200.0, False   # signal handled in run()

        if action == "skip":
            step = OptimizationStep(
                iteration   = self._iteration,
                vol_cyan    = vol_cyan,
                vol_magenta = vol_magenta,
                vol_yellow  = vol_yellow,
                result_hex  = "#000000",
                delta_e     = 200.0,
                is_best     = False,
                skipped     = True,
            )
            self._history.append(step)
            self._printer.print_step(step)
            return 200.0, True

        # ── Step 2: evaluate (dispense / simulate + capture) ──────────────
        try:
            color, de = self._evaluate(vol_cyan, vol_magenta, vol_yellow)
        except CameraError as exc:
            print(f"\n  {_RED}Camera error: {exc}{_RESET}")
            _pause("Press ENTER to skip this trial and continue...")
            step = OptimizationStep(
                iteration   = self._iteration,
                vol_cyan    = vol_cyan,
                vol_magenta = vol_magenta,
                vol_yellow  = vol_yellow,
                result_hex  = "#000000",
                delta_e     = 200.0,
                is_best     = False,
                skipped     = True,
            )
            self._history.append(step)
            self._printer.print_step(step)
            return 200.0, True

        # ── Step 3: record and display result ─────────────────────────────
        is_best = de < self._best_de
        if is_best:
            self._best_de     = de
            self._best_params = params

        step = OptimizationStep(
            iteration   = self._iteration,
            vol_cyan    = vol_cyan,
            vol_magenta = vol_magenta,
            vol_yellow  = vol_yellow,
            result_hex  = color.hex,
            delta_e     = de,
            is_best     = is_best,
        )
        self._history.append(step)
        self._printer.print_step(step)

        # ── Step 4: user reviews result before continuing ─────────────────
        _pause("Review the result above, then press ENTER to continue...")

        return de, False

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(self) -> OptimizationResult:
        """
        Execute the full Bayesian optimization loop with user-controlled
        step-through at every stage.

        Flow
        ────
        1. Print run header → user confirms before first trial
        2. For each trial:
             a. Show suggestion → user chooses apply / skip / stop
             b. Dispense / simulate
             c. (Live mode only) user confirms chip has settled → capture
             d. Show result → user presses ENTER to continue
        3. On convergence → user chooses stop or keep going
        4. After loop → user confirms before saving JSON log
        """
        mode = "DRY-RUN — simulated CMY" if self.test_mode else "LIVE — chip + camera"
        self._printer.print_run_header(
            mode           = mode,
            n_initial      = self.n_initial_points,
            convergence_de = self.convergence_delta_e,
            seed           = self.random_seed,
        )

        # ── Stage 1: user confirms they are ready to start ─────────────────
        if not _confirm("Ready to start the optimization?"):
            print(f"\n  {_YELLOW}Optimization cancelled before it started.{_RESET}\n")
            return OptimizationResult(
                vol_cyan=0.0, vol_magenta=0.0, vol_yellow=0.0,
                best_delta_e=float("inf"), converged=False,
                history=[], log_path=None,
            )

        optimizer = Optimizer(
            dimensions       = self.SPACE,
            base_estimator   = "GP",
            acq_func         = "EI",
            n_initial_points = self.n_initial_points,
            random_state     = self.random_seed,
        )

        converged    = False
        user_stopped = False

        for _ in range(self.n_calls):
            suggestion = optimizer.ask()

            score, skipped = self._objective(suggestion)

            # Check whether the user chose "stop" inside _objective
            # (signalled by score == 200.0 and not skipped)
            if score == 200.0 and not skipped:
                user_stopped = True
                print(
                    f"\n  {_YELLOW}Optimization stopped by user "
                    f"after {self._iteration - 1} trials.{_RESET}"
                )
                break

            optimizer.tell(suggestion, score)

            # ── Convergence check ──────────────────────────────────────────
            if not skipped and score < self.convergence_delta_e:
                self._printer.print_convergence(self._iteration, score)
                if _confirm(
                    f"\DeltaE₀₀ = {score:.2f} is below the convergence threshold "
                    f"({self.convergence_delta_e:.1f}). Stop now?"
                ):
                    converged = True
                    break
                else:
                    print(f"  {_CYAN}Continuing...{_RESET}")

        # ── Stage 5: confirm before saving log ────────────────────────────
        log_path: Optional[Path] = None
        if not user_stopped or _confirm(
            "Save the experiment log for the trials completed so far?"
        ):
            if _confirm("Save the experiment log?"):
                best_c, best_m, best_y = map_to_simplex(*self._best_params)
                log_path = self._save_log(best_c, best_m, best_y, converged)

        best_c, best_m, best_y = map_to_simplex(*self._best_params)

        result = OptimizationResult(
            vol_cyan     = best_c,
            vol_magenta  = best_m,
            vol_yellow   = best_y,
            best_delta_e = self._best_de,
            converged    = converged,
            history      = self._history,
            log_path     = log_path,
        )

        self._printer.print_summary(result)
        return result

    # ── JSON log ───────────────────────────────────────────────────────────────

    def _save_log(
        self,
        best_c: float,
        best_m: float,
        best_y: float,
        converged: bool,
    ) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.log_dir / f"color_opt_{ts}.json"

        record = {
            "metadata": {
                "timestamp":           ts,
                "mode":                "test" if self.test_mode else "live",
                "target_hex":          self.target.hex,
                "target_rgb":          self.target.rgb,
                "n_calls":             self.n_calls,
                "n_initial_points":    self.n_initial_points,
                "convergence_delta_e": self.convergence_delta_e,
                "random_seed":         self.random_seed,
                "well_roi":            self.well_roi,
            },
            "best": {
                "vol_cyan":    best_c,
                "vol_magenta": best_m,
                "vol_yellow":  best_y,
                "delta_e":     self._best_de,
                "converged":   converged,
            },
            "history": [asdict(s) for s in self._history],
        }

        with path.open("w") as fh:
            json.dump(record, fh, indent=2)

        print(f"\n  {_GREEN}Experiment log saved → {path}{_RESET}")
        return path


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    # ── Configuration ──────────────────────────────────────────────────────────
    TEST_MODE           = True      # True = dry-run, no hardware needed
    N_CALLS             = 25        # Total evaluations
    N_INITIAL_POINTS    = 5         # Random seed evaluations before GP fitting
    CONVERGENCE_DELTA_E = 2.0       # Convergence threshold (\DeltaE₀₀)
    RANDOM_SEED         = 42        # Reproducible target + optimizer + noise
    LOG_DIR             = Path("experiment_logs")
    # ──────────────────────────────────────────────────────────────────────────

    # ── Stage 0: choose / confirm target color ─────────────────────────────────
    print()
    print(_header("Target Color Setup", 72))

    color_choice = _choose(
        "How would you like to set the target color?",
        {
            "random": "Random color (seeded — reproducible)",
            "hex":    "Enter a hex color manually  e.g. #FF8040",
        },
    )

    if color_choice == "random":
        target = random_target_color(seed=RANDOM_SEED)
        print(f"\n  Generated target : {_swatch(target.hex)}  {target}")
    else:
        while True:
            raw = input("\n  Enter hex color (e.g. #FF8040): ").strip()
            try:
                target = hex_to_color_measurement(raw)
                print(f"  Target set to    : {_swatch(target.hex)}  {target}")
                break
            except ValueError as exc:
                print(f"  {_RED}Invalid: {exc}{_RESET}  Please try again.")

    if not _confirm(f"Use {_swatch(target.hex)} as the target color?"):
        print(f"\n  {_YELLOW}Exiting — no target confirmed.{_RESET}\n")
        raise SystemExit(0)

    # ── Stage 1: hardware setup (live mode only) ───────────────────────────────
    if TEST_MODE:
        optimizer = ColorOptimizer(
            target              = target,
            n_calls             = N_CALLS,
            n_initial_points    = N_INITIAL_POINTS,
            convergence_delta_e = CONVERGENCE_DELTA_E,
            log_dir             = LOG_DIR,
            random_seed         = RANDOM_SEED,
            test_mode           = True,
        )
        result = optimizer.run()

    else:
        print()
        print(_header("Camera Setup", 72))
        _pause("Press ENTER to connect to the camera...")

        with CameraInterface(
            camera_address       = 0,
            warmup_frames        = 15,
            measure_frames       = 5,
            roi_margin           = 5,
            lock_camera_settings = True,
        ) as camera:

            print(f"  {_GREEN}Camera connected.{_RESET}")

            # Reference picture
            _pause("Press ENTER to take a reference picture...")
            pic_path = camera.take_picture()
            print(f"  Reference picture saved → {pic_path}")

            if not _confirm("Happy with the reference picture? Continue to ROI selection?"):
                print(f"\n  {_YELLOW}Exiting — ROI not confirmed.{_RESET}\n")
                raise SystemExit(0)

            # ROI selection
            _pause("Press ENTER to open the ROI selection window...")
            roi = camera.pick_roi_interactively()
            print(
                f"  ROI selected : x={roi[0]}  y={roi[1]}  "
                f"w={roi[2]}  h={roi[3]}"
            )

            if not _confirm("Confirm this ROI and start the optimization?"):
                print(f"\n  {_YELLOW}Exiting — ROI not confirmed.{_RESET}\n")
                raise SystemExit(0)

            optimizer = ColorOptimizer(
                target              = target,
                camera              = camera,
                well_roi            = roi,
                n_calls             = N_CALLS,
                n_initial_points    = N_INITIAL_POINTS,
                settle_seconds      = 1.5,
                convergence_delta_e = CONVERGENCE_DELTA_E,
                log_dir             = LOG_DIR,
                random_seed         = RANDOM_SEED,
                test_mode           = False,
            )
            result = optimizer.run()