"""
bayesian_color_optimizer.py
Bayesian optimization over CMY ink volumes to match a target color.
TEST_MODE = True  → dry-run, no hardware needed.
"""

from __future__ import annotations

import json
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


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ColorMeasurement:
    r: int
    g: int
    b: int

    @property
    def rgb(self): return (self.r, self.g, self.b)

    @property
    def bgr(self): return (self.b, self.g, self.r)

    @property
    def hex(self): return "#{:02x}{:02x}{:02x}".format(self.r, self.g, self.b)

    def __str__(self): return f"{self.hex}  rgb=({self.r},{self.g},{self.b})"


@dataclass
class OptimizationStep:
    iteration:   int
    vol_cyan:    float
    vol_magenta: float
    vol_yellow:  float
    result_hex:  str
    delta_e:     float
    is_best:     bool = False
    skipped:     bool = False
    timestamp:   str  = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class OptimizationResult:
    vol_cyan:     float
    vol_magenta:  float
    vol_yellow:   float
    best_delta_e: float
    converged:    bool
    history:      list[OptimizationStep]
    log_path:     Optional[Path]


# ── CIEDE2000 color distance ───────────────────────────────────────────────────

def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """Full CIEDE2000 perceptual color difference (L in [0, 100])."""
    L1, a1, b1 = float(lab1[0]), float(lab1[1]), float(lab1[2])
    L2, a2, b2 = float(lab2[0]), float(lab2[1]), float(lab2[2])

    C1, C2   = np.sqrt(a1**2 + b1**2), np.sqrt(a2**2 + b2**2)
    C_avg    = (C1 + C2) / 2.0
    C_avg7   = C_avg**7
    G        = 0.5 * (1.0 - np.sqrt(C_avg7 / (C_avg7 + 25.0**7)))
    a1p, a2p = a1 * (1.0 + G), a2 * (1.0 + G)
    C1p      = np.sqrt(a1p**2 + b1**2)
    C2p      = np.sqrt(a2p**2 + b2**2)
    h1p      = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p      = np.degrees(np.arctan2(b2, a2p)) % 360.0

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

    dHp    = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2.0))
    Lp_avg = (L1 + L2) / 2.0
    Cp_avg = (C1p + C2p) / 2.0

    if C1p * C2p == 0.0:
        hp_avg = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        hp_avg = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        hp_avg = (h1p + h2p + 360.0) / 2.0
    else:
        hp_avg = (h1p + h2p - 360.0) / 2.0

    T = (1.0
         - 0.17 * np.cos(np.radians(hp_avg - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hp_avg))
         + 0.32 * np.cos(np.radians(3.0 * hp_avg + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hp_avg - 63.0)))

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
    return np.array([ocv_lab[0] / 2.55, float(ocv_lab[1]) - 128.0, float(ocv_lab[2]) - 128.0], dtype=np.float64)


# ── Camera interface ───────────────────────────────────────────────────────────

class CameraError(RuntimeError):
    pass


class CameraInterface:
    """Thin wrapper around cv2.VideoCapture. Use as a context manager."""

    def __init__(self, camera_address: Union[int, str] = 0, warmup_frames: int = 15,
                 measure_frames: int = 5, roi_margin: int = 5, lock_camera_settings: bool = True):
        try:
            self.camera_address = int(camera_address)
        except (ValueError, TypeError):
            self.camera_address = camera_address
        self.warmup_frames        = warmup_frames
        self.measure_frames       = measure_frames
        self.roi_margin           = roi_margin
        self.lock_camera_settings = lock_camera_settings
        self._camera: Optional[cv2.VideoCapture] = None

    def __enter__(self):
        self.open(); return self

    def __exit__(self, *_):
        self.close()

    def open(self):
        if self._camera is not None and self._camera.isOpened():
            return
        cam = cv2.VideoCapture(self.camera_address, cv2.CAP_DSHOW)
        cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not cam.isOpened():
            raise CameraError(f"Unable to open camera at '{self.camera_address}'")
        if self.lock_camera_settings:
            cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            cam.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        for _ in range(self.warmup_frames):
            cam.read()
        self._camera = cam

    def close(self):
        if self._camera is not None and self._camera.isOpened():
            self._camera.release()
        self._camera = None

    def set_focus(self, focus: int, autofocus: bool = False):
        self._require_open()
        self._camera.set(cv2.CAP_PROP_AUTOFOCUS, 1 if autofocus else 0)
        if not autofocus and focus is not None:
            if not (0 <= focus <= 255):
                raise ValueError(f"Focus must be in [0, 255], got {focus}.")
            self._camera.set(cv2.CAP_PROP_FOCUS, focus)
        for _ in range(30):
            self._camera.read()

    def get_average_color_from_rectangle(self, x: int, y: int, width: int, height: int) -> ColorMeasurement:
        self._require_open()
        frames = []
        for _ in range(self.measure_frames):
            ok, frame = self._camera.read()
            if not ok:
                raise CameraError("Camera read failed during color measurement.")
            frames.append(frame)

        h_img, w_img = frames[0].shape[:2]
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > w_img or y + height > h_img:
            raise ValueError(f"ROI ({x},{y},{width},{height}) outside image ({w_img}x{h_img}).")

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
        return ColorMeasurement(r=int(avg_bgr[2]), g=int(avg_bgr[1]), b=int(avg_bgr[0]))

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
        print("Draw a rectangle over the well, then press SPACE or ENTER.")
        roi = cv2.selectROI("Select Well ROI (SPACE/ENTER=confirm, C=cancel)", frame, showCrosshair=True)
        cv2.destroyAllWindows()
        x, y, w, h = (int(v) for v in roi)
        if w == 0 or h == 0:
            raise ValueError("No ROI selected.")
        return x, y, w, h

    def _require_open(self):
        if self._camera is None or not self._camera.isOpened():
            raise CameraError("Camera is not open. Call open() or use the context manager.")


# ── CMY simulator ──────────────────────────────────────────────────────────────

def simulate_cmy_mix(vol_cyan: float, vol_magenta: float, vol_yellow: float, *,
                     gamma: float = 1.4, cross_coupling: float = 0.08,
                     noise_std: float = 3.0, rng=None) -> ColorMeasurement:
    """Subtractive CMY mixing with gamma nonlinearity, cross-channel coupling, and noise."""
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


# ── Color helpers ──────────────────────────────────────────────────────────────

def hex_to_color_measurement(hex_color: str) -> ColorMeasurement:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: '{hex_color}'")
    return ColorMeasurement(r=int(h[0:2], 16), g=int(h[2:4], 16), b=int(h[4:6], 16))


def delta_e_ciede2000(a: ColorMeasurement, b: ColorMeasurement) -> float:
    def to_lab(bgr):
        patch = np.uint8([[list(bgr)]])
        ocv   = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[0, 0]
        return _opencv_lab_to_standard(ocv)
    return ciede2000(to_lab(a.bgr), to_lab(b.bgr))


def random_target_color(seed: Optional[int] = None) -> ColorMeasurement:
    """seed=None uses system time — truly random each run."""
    rng = random.Random(seed)
    return ColorMeasurement(r=rng.randint(0, 255), g=rng.randint(0, 255), b=rng.randint(0, 255))


def map_to_simplex(c_raw: float, m_raw: float) -> tuple[float, float, float]:
    """[0,1]² → CMY 2-simplex (c + m + y = 1, all >= 0)."""
    c = float(c_raw)
    m = (1.0 - c) * float(m_raw)
    return c, m, 1.0 - c - m


# ── Bayesian color optimizer ───────────────────────────────────────────────────

class ColorOptimizer:
    """Bayesian (GP + EI) optimization over CMY volumes to match a target color."""

    SPACE = [Real(0.0, 1.0, name="c_raw"), Real(0.0, 1.0, name="m_raw")]

    def __init__(self, target: ColorMeasurement, camera: Optional[CameraInterface] = None,
                 well_roi: Optional[tuple[int, int, int, int]] = None, n_calls: int = 25,
                 n_initial_points: int = 5, settle_seconds: float = 1.5,
                 convergence_delta_e: float = 2.0, log_dir: Path = Path("."),
                 random_seed: int = 42, test_mode: bool = False):
        if not test_mode and (camera is None or well_roi is None):
            raise ValueError("camera and well_roi are required when test_mode=False.")
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
        self._rng                = np.random.default_rng(random_seed)
        self._history:           list[OptimizationStep] = []
        self._iteration          = 0
        self._best_de            = float("inf")
        self._best_params        = [0.5, 0.5]

    def _evaluate(self, vol_cyan: float, vol_magenta: float, vol_yellow: float):
        if self.test_mode:
            return simulate_cmy_mix(vol_cyan, vol_magenta, vol_yellow, rng=self._rng)
        MAX_PIECE_WIDTH = 25
        w_c = round(vol_cyan    * MAX_PIECE_WIDTH)
        w_m = round(vol_magenta * MAX_PIECE_WIDTH)
        w_y = round(vol_yellow  * MAX_PIECE_WIDTH)
        print(f"  Dispensing: cyan={w_c}  magenta={w_m}  yellow={w_y}")
        # Uncomment to wire to microfluidics.py:
        # from microfluidics import activate, Drop, DROP1_ROW, DROP2_ROW, DROP3_ROW, PIECE_FINAL_COL
        # activate([Drop(10, w_c, DROP1_ROW, PIECE_FINAL_COL),
        #           Drop(10, w_m, DROP2_ROW, PIECE_FINAL_COL),
        #           Drop(10, w_y, DROP3_ROW, PIECE_FINAL_COL)])
        time.sleep(self.settle_seconds)
        x, y, w, h = self.well_roi
        return self.camera.get_average_color_from_rectangle(x=x, y=y, width=w, height=h)

    def _objective(self, params: list[float]) -> float:
        c_raw, m_raw = params
        vol_cyan, vol_magenta, vol_yellow = map_to_simplex(c_raw, m_raw)
        self._iteration += 1
        tag = "[SEED]" if self._iteration <= self.n_initial_points else "[GP]  "
        print(f"  Trial {self._iteration:>2}/{self.n_calls}  {tag}  C={vol_cyan:.3f}  M={vol_magenta:.3f}  Y={vol_yellow:.3f}", end="  ")

        try:
            color = self._evaluate(vol_cyan, vol_magenta, vol_yellow)
        except CameraError as exc:
            print(f"\n  Camera error: {exc} -- skipping")
            self._history.append(OptimizationStep(
                self._iteration, vol_cyan, vol_magenta, vol_yellow, "#000000", 200.0, skipped=True))
            return 200.0

        de      = delta_e_ciede2000(color, self.target)
        is_best = de < self._best_de
        if is_best:
            self._best_de     = de
            self._best_params = params
        print(f"-> {color.hex}  dE={de:.2f}  best={self._best_de:.2f}{'  ***' if is_best else ''}")
        self._history.append(OptimizationStep(
            self._iteration, vol_cyan, vol_magenta, vol_yellow, color.hex, de, is_best))
        return de

    def run(self) -> OptimizationResult:
        print(f"\n--- Bayesian CMY Optimizer ---")
        print(f"Target : {self.target}")
        print(f"Trials : {self.n_calls}  ({self.n_initial_points} random seed + {self.n_calls - self.n_initial_points} GP-guided)")
        print(f"Converge when dE < {self.convergence_delta_e}  (< 2.0 = visually identical)\n")

        optimizer = Optimizer(
            dimensions=self.SPACE, base_estimator="GP", acq_func="EI",
            n_initial_points=self.n_initial_points, random_state=self.random_seed,
        )
        converged = False
        for _ in range(self.n_calls):
            suggestion = optimizer.ask()
            score      = self._objective(suggestion)
            optimizer.tell(suggestion, score)
            if score < self.convergence_delta_e:
                print(f"\n  Converged at trial {self._iteration}  (dE={score:.2f})")
                converged = True
                break

        best_c, best_m, best_y = map_to_simplex(*self._best_params)
        log_path = self._save_log(best_c, best_m, best_y, converged)
        result   = OptimizationResult(best_c, best_m, best_y, self._best_de, converged, self._history, log_path)
        self._print_summary(result)
        return result

    def _print_summary(self, result: OptimizationResult):
        print("\n--- Results ---")
        print(f"  Best CMY : cyan={result.vol_cyan:.4f}  magenta={result.vol_magenta:.4f}  yellow={result.vol_yellow:.4f}")
        print(f"  Best dE  : {result.best_delta_e:.2f}  {'(visually identical)' if result.best_delta_e < 2.0 else ''}")
        print(f"  Target   : {self.target.hex}")
        best_step = min((s for s in result.history if not s.skipped), key=lambda s: s.delta_e)
        print(f"  Result   : {best_step.result_hex}")
        real = [s.delta_e for s in result.history if not s.skipped]
        if real:
            print(f"  Trials   : {len(real)}   mean dE={np.mean(real):.2f}   std={np.std(real):.2f}")
        if result.log_path:
            print(f"  Log      : {result.log_path}")

    def _save_log(self, best_c: float, best_m: float, best_y: float, converged: bool) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.log_dir / f"color_opt_{ts}.json"
        record = {
            "metadata": {
                "timestamp": ts, "mode": "test" if self.test_mode else "live",
                "target_hex": self.target.hex, "target_rgb": self.target.rgb,
                "n_calls": self.n_calls, "n_initial_points": self.n_initial_points,
                "convergence_delta_e": self.convergence_delta_e,
                "random_seed": self.random_seed, "well_roi": self.well_roi,
            },
            "best": {"vol_cyan": best_c, "vol_magenta": best_m, "vol_yellow": best_y,
                     "delta_e": self._best_de, "converged": converged},
            "history": [asdict(s) for s in self._history],
        }
        with path.open("w") as fh:
            json.dump(record, fh, indent=2)
        print(f"\n  Log saved -> {path}")
        return path


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    TEST_MODE           = True   # True = dry-run, no hardware needed
    N_CALLS             = 25     # Max trials (stops early if converged)
    N_INITIAL_POINTS    = 5      # Random trials before GP fitting kicks in
    CONVERGENCE_DELTA_E = 2.0    # Stop when dE drops below this
    RANDOM_SEED         = 42
    LOG_DIR             = Path("experiment_logs")

    target = random_target_color(seed=None)  # truly random each run
    print(f"Target color: {target}")

    if TEST_MODE:
        opt = ColorOptimizer(
            target=target, n_calls=N_CALLS, n_initial_points=N_INITIAL_POINTS,
            convergence_delta_e=CONVERGENCE_DELTA_E, log_dir=LOG_DIR,
            random_seed=RANDOM_SEED, test_mode=True,
        )
        result = opt.run()
    else:
        with CameraInterface(camera_address=0, warmup_frames=15, measure_frames=5,
                             roi_margin=5, lock_camera_settings=True) as camera:
            roi = (100, 100, 50, 50)  # <-- replace with your actual ROI
            opt = ColorOptimizer(
                target=target, camera=camera, well_roi=roi,
                n_calls=N_CALLS, n_initial_points=N_INITIAL_POINTS, settle_seconds=1.5,
                convergence_delta_e=CONVERGENCE_DELTA_E, log_dir=LOG_DIR,
                random_seed=RANDOM_SEED, test_mode=False,
            )
            result = opt.run()