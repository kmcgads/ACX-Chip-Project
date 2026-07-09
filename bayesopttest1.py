import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np
from skopt import Optimizer
from skopt.space import Real

sys.path.append("C:\\Users\\klmcg\\SULIProj\\ACX-CHIP-PROJECT")
import colormix1

MAX_PIECE_WIDTH = 25   # electrode widths map to this maximum


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


# ── CIEDE2000 ──────────────────────────────────────────────────────────────────

def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
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


# ── Camera ─────────────────────────────────────────────────────────────────────

class CameraError(RuntimeError):
    pass


class CameraInterface:
    def __init__(self, camera_address: Union[int, str] = 0, warmup_frames: int = 15,
                 measure_frames: int = 5, roi_margin: int = 5):
        try:
            self.camera_address = int(camera_address)
        except (ValueError, TypeError):
            self.camera_address = camera_address
        self.warmup_frames  = warmup_frames
        self.measure_frames = measure_frames
        self.roi_margin     = roi_margin
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
        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        cam.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        if not cam.isOpened():
            raise CameraError(f"Unable to open camera at '{self.camera_address}'")
        for _ in range(self.warmup_frames):
            cam.read()
        self._camera = cam

    def close(self):
        if self._camera is not None and self._camera.isOpened():
            self._camera.release()
        self._camera = None

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
            raise CameraError("Camera is not open. Call open() or use context manager.")


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
    import random
    rng = random.Random(seed)
    return ColorMeasurement(r=rng.randint(0, 255), g=rng.randint(0, 255), b=rng.randint(0, 255))


def map_to_simplex(c_raw: float, m_raw: float) -> tuple[float, float, float]:
    c = float(c_raw)
    m = (1.0 - c) * float(m_raw)
    return c, m, 1.0 - c - m


# ── Bayesian optimizer ─────────────────────────────────────────────────────────

class ColorOptimizer:
    SPACE = [Real(0.0, 1.0, name="c_raw"), Real(0.0, 1.0, name="m_raw")]

    def __init__(self, target: ColorMeasurement, camera: CameraInterface,
                 well_roi: tuple[int, int, int, int], n_calls: int = 25,
                 n_initial_points: int = 5, settle_seconds: float = 1.5,
                 convergence_delta_e: float = 2.0, log_dir: Path = Path("."),
                 random_seed: int = 42):
        self.target              = target
        self.camera              = camera
        self.well_roi            = well_roi
        self.n_calls             = n_calls
        self.n_initial_points    = n_initial_points
        self.settle_seconds      = settle_seconds
        self.convergence_delta_e = convergence_delta_e
        self.log_dir             = Path(log_dir)
        self.random_seed         = random_seed
        self._history:    list[OptimizationStep] = []
        self._iteration   = 0
        self._best_de     = float("inf")
        self._best_params = [0.5, 0.5]

    def _evaluate(self, vol_cyan: float, vol_magenta: float, vol_yellow: float) -> ColorMeasurement:
        # Convert fractions to electrode widths
        w_c = max(2, round(vol_cyan    * MAX_PIECE_WIDTH))
        w_m = max(2, round(vol_magenta * MAX_PIECE_WIDTH))
        w_y = max(2, round(vol_yellow  * MAX_PIECE_WIDTH))
        print(f"  Widths: cyan={w_c}  magenta={w_m}  yellow={w_y}")

        # Split a piece from each ink reservoir
        colormix1.split_and_move(row=colormix1.DROP1_ROW, label="Cyan",
                                  held_pairs=[], piece_w=w_c)
        colormix1.split_and_move(row=colormix1.DROP2_ROW, label="Magenta",
                                  held_pairs=[(colormix1.DROP1_ROW, w_c)], piece_w=w_m)
        colormix1.split_and_move(row=colormix1.DROP3_ROW, label="Yellow",
                                  held_pairs=[(colormix1.DROP1_ROW, w_c),
                                              (colormix1.DROP2_ROW, w_m)], piece_w=w_y)

        # Converge, merge, and mix all three pieces
        colormix1.move_pieces_to_meet(w_c, w_m, w_y)

        # Let drop settle then read color from camera
        time.sleep(self.settle_seconds)
        x, y, w, h = self.well_roi
        return self.camera.get_average_color_from_rectangle(x=x, y=y, width=w, height=h)

    def _objective(self, params: list[float]) -> float:
        c_raw, m_raw         = params
        vol_cyan, vol_magenta, vol_yellow = map_to_simplex(c_raw, m_raw)
        self._iteration     += 1
        tag = "[SEED]" if self._iteration <= self.n_initial_points else "[GP]  "
        print(f"  Trial {self._iteration:>2}/{self.n_calls}  {tag}  "
              f"C={vol_cyan:.3f}  M={vol_magenta:.3f}  Y={vol_yellow:.3f}", end="  ")

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
        print(f"Trials : {self.n_calls}  "
              f"({self.n_initial_points} random + {self.n_calls - self.n_initial_points} GP-guided)")
        print(f"Converge when dE < {self.convergence_delta_e}\n")

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
                "timestamp": ts, "target_hex": self.target.hex,
                "target_rgb": self.target.rgb, "n_calls": self.n_calls,
                "n_initial_points": self.n_initial_points,
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
    N_CALLS             = 25
    N_INITIAL_POINTS    = 5
    CONVERGENCE_DELTA_E = 2.0
    RANDOM_SEED         = 42
    LOG_DIR             = Path("experiment_logs")

    target = random_target_color(seed=None)
    print(f"Target color: {target}")

    with CameraInterface(camera_address=0, warmup_frames=15, measure_frames=5, roi_margin=5) as camera:
        roi = camera.pick_roi_interactively()
        opt = ColorOptimizer(
            target=target, camera=camera, well_roi=roi,
            n_calls=N_CALLS, n_initial_points=N_INITIAL_POINTS,
            settle_seconds=1.5, convergence_delta_e=CONVERGENCE_DELTA_E,
            log_dir=LOG_DIR, random_seed=RANDOM_SEED,
        )
        result = opt.run()