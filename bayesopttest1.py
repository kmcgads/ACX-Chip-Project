"""
blind_bayesian_optimizer.py
───────────────────────────
Pure Bayesian optimizer for 3-reservoir color matching.
No camera, no chip movement — integrate into your master script.

The optimizer knows nothing about the reservoir colors.
It only sees: suggested widths → measured hex → DeltaE → next suggestion.

Each trial, suggested widths are written to colormixcsv so your existing
mix script can read them. Every result is also appended to optimization_log.csv.

─── Usage in your master script ───────────────────────────────────────────────

    from bayesopttest1 import BlindOptimizer, random_vivid_target_color

    target = random_vivid_target_color(seed=RANDOM_SEED)
    opt    = BlindOptimizer(target, n_calls=N_CALLS)

    for _ in range(opt.n_calls):
        w1, w2, w3 = opt.ask()          # writes widths to colormixcsv
        # ... run csvvolcont, measure with camera ...
        converged = opt.tell("#ff8040")  # pass measured hex back
        if converged:
            break

    result = opt.get_result()           # best widths + full history
    opt.save_log(result)                # optional JSON log

───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import colorsys
import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from skopt import Optimizer
from skopt.space import Real


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

MAX_TOTAL_WIDTH     = 15          # Reservoir 1 + 2 + 3 widths always sum to this
MIN_WIDTH           = 2           # Minimum active width per reservoir (0 = excluded)
MAX_WIDTH           = 13          # Maximum width per reservoir
PRESET_WIDTHS       = (5, 5, 5)  # Trial 1 always starts here (matches CSV default)
N_CALLS             = 5           # Default trials — overridden by master script via n_calls=
N_INITIAL_POINTS    = 1           # Random trials before GP fitting begins
CONVERGENCE_DELTA_E = 2.0         # DeltaE < this = visually identical, stop
RANDOM_SEED         = None        # None = different random target color each run
LOG_DIR             = Path("experiment_logs")

# CSV paths
COLOR_MIX_CSV   = Path("colormixcsv.csv")       # existing mix script CSV
OPT_LOG_CSV     = Path("optimization_log.csv")  # running trial history


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ColorMeasurement:
    r: int
    g: int
    b: int

    @property
    def bgr(self) -> tuple[int, int, int]:
        return (self.b, self.g, self.r)

    @property
    def hex(self) -> str:
        return "#{:02x}{:02x}{:02x}".format(self.r, self.g, self.b)


@dataclass
class OptimizationStep:
    iteration:   int
    width_1:     int
    width_2:     int
    width_3:     int
    prop_1:      float
    prop_2:      float
    prop_3:      float
    result_hex:  str
    delta_e:     float
    is_best:     bool  = False
    timestamp:   str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class OptimizationResult:
    width_1:      int
    width_2:      int
    width_3:      int
    best_delta_e: float
    converged:    bool
    history:      list[OptimizationStep]
    target_hex:   str


# ══════════════════════════════════════════════════════════════════════════════
# Color utilities
# ══════════════════════════════════════════════════════════════════════════════

def random_vivid_target_color(seed: Optional[int] = None) -> ColorMeasurement:
    """
    Generate a random target color guaranteed not to be black, white, or grey.
    Uses HSV to enforce saturation and mid-range brightness.
    """
    import random as _random
    rng        = _random.Random(seed)
    hue        = rng.uniform(0.0, 1.0)
    saturation = rng.uniform(0.45, 1.0)   # avoid grey
    value      = rng.uniform(0.35, 0.85)  # avoid black and white
    r, g, b    = colorsys.hsv_to_rgb(hue, saturation, value)
    return ColorMeasurement(r=int(r * 255), g=int(g * 255), b=int(b * 255))


def hex_to_color(hex_color: str) -> ColorMeasurement:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: '{hex_color}'")
    return ColorMeasurement(r=int(h[0:2], 16), g=int(h[2:4], 16), b=int(h[4:6], 16))


def _bgr_to_lab(bgr: tuple[int, int, int]) -> np.ndarray:
    patch   = np.uint8([[list(bgr)]])
    ocv_lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[0, 0]
    return np.array([ocv_lab[0] / 2.55, float(ocv_lab[1]) - 128.0, float(ocv_lab[2]) - 128.0])


def delta_e_ciede2000(a: ColorMeasurement, b: ColorMeasurement) -> float:
    """Full CIEDE2000 perceptual color distance. < 2.0 = visually identical."""
    lab1, lab2 = _bgr_to_lab(a.bgr), _bgr_to_lab(b.bgr)
    L1, a1, b1 = float(lab1[0]), float(lab1[1]), float(lab1[2])
    L2, a2, b2 = float(lab2[0]), float(lab2[1]), float(lab2[2])

    C1     = np.sqrt(a1**2 + b1**2);   C2     = np.sqrt(a2**2 + b2**2)
    C_avg  = (C1 + C2) / 2;            C_avg7 = C_avg**7
    G      = 0.5 * (1.0 - np.sqrt(C_avg7 / (C_avg7 + 25.0**7)))
    a1p    = a1 * (1 + G);             a2p    = a2 * (1 + G)
    C1p    = np.sqrt(a1p**2 + b1**2);  C2p    = np.sqrt(a2p**2 + b2**2)
    h1p    = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p    = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp    = L2 - L1;                  dCp    = C2p - C1p

    if   C1p * C2p == 0:         dhp = 0.0
    elif abs(h2p - h1p) <= 180:  dhp = h2p - h1p
    elif h2p - h1p > 180:        dhp = h2p - h1p - 360
    else:                        dhp = h2p - h1p + 360

    dHp    = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2))
    Lp_avg = (L1 + L2) / 2;    Cp_avg  = (C1p + C2p) / 2

    if   C1p * C2p == 0:        hp_avg = h1p + h2p
    elif abs(h1p - h2p) <= 180: hp_avg = (h1p + h2p) / 2
    elif h1p + h2p < 360:       hp_avg = (h1p + h2p + 360) / 2
    else:                        hp_avg = (h1p + h2p - 360) / 2

    T  = (1 - 0.17 * np.cos(np.radians(hp_avg - 30))
            + 0.24 * np.cos(np.radians(2 * hp_avg))
            + 0.32 * np.cos(np.radians(3 * hp_avg + 6))
            - 0.20 * np.cos(np.radians(4 * hp_avg - 63)))
    SL = 1 + 0.015 * (Lp_avg - 50)**2 / np.sqrt(20 + (Lp_avg - 50)**2)
    SC = 1 + 0.045 * Cp_avg
    SH = 1 + 0.015 * Cp_avg * T
    Ca7 = Cp_avg**7
    RC  = 2 * np.sqrt(Ca7 / (Ca7 + 25**7))
    RT  = -np.sin(np.radians(60 * np.exp(-((hp_avg - 275) / 25)**2))) * RC

    return float(np.sqrt(
        (dLp/SL)**2 + (dCp/SC)**2 + (dHp/SH)**2 + RT*(dCp/SC)*(dHp/SH)
    ))


# ══════════════════════════════════════════════════════════════════════════════
# Simplex + width helpers
# ══════════════════════════════════════════════════════════════════════════════

def map_to_simplex(r1_raw: float, r2_raw: float) -> tuple[float, float, float]:
    """
    Maps 2 optimizer params (both in [0,1]) to 3 proportions that sum to 1.
    Ensures Reservoir 1 + 2 + 3 always represent a full partition of the mix.
    """
    p1 = float(r1_raw)
    p2 = (1.0 - p1) * float(r2_raw)
    p3 = 1.0 - p1 - p2
    return p1, p2, p3


def proportions_to_widths(
    p1: float, p2: float, p3: float, total: int = MAX_TOTAL_WIDTH
) -> tuple[int, int, int]:
    """
    Convert proportions to integer piece widths summing to `total`.
    Valid per-reservoir values: 0 (excluded) or MIN_WIDTH–MAX_WIDTH.
    Width of 1 is not allowed — anything that would round to 1 snaps to 0.
    The largest active reservoir absorbs any rounding remainder.
    """
    raws = [p1 * total, p2 * total, p3 * total]

    widths = []
    for r in raws:
        if r < 1.5:                          # would round to 0 or 1 → exclude
            widths.append(0)
        else:
            widths.append(min(MAX_WIDTH, max(MIN_WIDTH, round(r))))

    # Adjust the largest width so the total stays at MAX_TOTAL_WIDTH
    diff = total - sum(widths)
    if diff != 0:
        idx = widths.index(max(widths))
        adjusted = widths[idx] + diff
        if adjusted == 1:                    # snap 1 → 0 after adjustment
            adjusted = 0
        widths[idx] = min(MAX_WIDTH, max(0, adjusted))

    return tuple(widths)


# ══════════════════════════════════════════════════════════════════════════════
# CSV helpers
# ══════════════════════════════════════════════════════════════════════════════

COLOR_MIX_HEADERS = ["piece_1 width", "piece_2 width", "piece_3 width"]
OPT_LOG_HEADERS   = ["trial", "piece_1 width", "piece_2 width", "piece_3 width",
                      "target_hex", "measured_hex", "delta_e", "is_best", "timestamp"]


def write_widths_to_csv(w1: int, w2: int, w3: int, path: Path = COLOR_MIX_CSV) -> None:
    """Overwrite colormixcsv with the new suggested widths for this trial."""
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLOR_MIX_HEADERS)
        writer.writerow([w1, w2, w3])
    print(f"  [CSV] Written to {path}: piece_1={w1}  piece_2={w2}  piece_3={w3}")


def append_trial_to_log(step: OptimizationStep, target_hex: str, path: Path = OPT_LOG_CSV) -> None:
    """Append one completed trial row to optimization_log.csv."""
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(OPT_LOG_HEADERS)
        writer.writerow([
            step.iteration,
            step.width_1,
            step.width_2,
            step.width_3,
            target_hex,
            step.result_hex,
            round(step.delta_e, 4),
            step.is_best,
            step.timestamp,
        ])
    print(f"  [CSV] Trial {step.iteration} appended to {path}")


# ══════════════════════════════════════════════════════════════════════════════
# Blind Bayesian Optimizer
# ══════════════════════════════════════════════════════════════════════════════

class BlindOptimizer:
    """
    Bayesian optimizer for 3-reservoir color matching with unknown reservoir colors.

    The optimizer is "blind" — it has no model of what colors the reservoirs
    contain. It learns purely from the DeltaE of each measured hex result.

    Each call to ask() writes the suggested widths to colormixcsv so your
    existing mix script can pick them up. Each call to tell() appends the
    result to optimization_log.csv.

    Interface for master script
    ───────────────────────────
    opt = BlindOptimizer(target, n_calls=N_CALLS)

    w1, w2, w3 = opt.ask()        → get next widths (also writes to colormixcsv)
    converged  = opt.tell(hex)    → feed measured hex back, logs to optimization_log.csv
    result     = opt.get_result() → OptimizationResult after loop ends
    opt.save_log(result)          → save JSON log
    """

    SPACE = [
        Real(0.0, 1.0, name="r1_raw"),
        Real(0.0, 1.0, name="r2_raw"),
    ]

    def __init__(
        self,
        target:               ColorMeasurement,
        n_calls:              int   = N_CALLS,
        n_initial_points:     int   = N_INITIAL_POINTS,
        convergence_delta_e:  float = CONVERGENCE_DELTA_E,
        random_seed:          int   = RANDOM_SEED,
        log_dir:              Path  = LOG_DIR,
        color_mix_csv:        Path  = COLOR_MIX_CSV,
        opt_log_csv:          Path  = OPT_LOG_CSV,
    ) -> None:
        self.target              = target
        self.n_calls             = n_calls          # controlled by master script via N_CALLS
        self.n_initial_points    = n_initial_points
        self.convergence_delta_e = convergence_delta_e
        self.random_seed         = random_seed
        self.log_dir             = Path(log_dir)
        self.color_mix_csv       = Path(color_mix_csv)
        self.opt_log_csv         = Path(opt_log_csv)

        self._skopt = Optimizer(
            dimensions       = self.SPACE,
            base_estimator   = "GP",
            acq_func         = "EI",
            n_initial_points = n_initial_points,
            random_state     = random_seed,
        )

        self._history:      list[OptimizationStep] = []
        self._iteration     = 0
        self._best_de       = float("inf")
        self._best_params:  list[float] = [0.33, 0.5]
        self._pending:      Optional[list[float]] = None
        self._last_widths:  Optional[tuple[int, int, int]] = None   # actual widths sent to chip
        self._converged     = False

    # ── ask ────────────────────────────────────────────────────────────────────

    def ask(self) -> tuple[int, int, int]:
        """
        Ask the optimizer for the next suggested reservoir widths.
        Returns (width_1, width_2, width_3) and writes them to colormixcsv.
        Must be followed by a call to tell() before the next ask().
        """
        self._pending = self._skopt.ask()
        self._iteration += 1

        # Trial 1 always uses the preset widths to match the CSV default (5, 5, 5)
        if self._iteration == 1:
            w1, w2, w3 = PRESET_WIDTHS
            phase = "PRESET"
        else:
            p1, p2, p3 = map_to_simplex(*self._pending)
            w1, w2, w3 = proportions_to_widths(p1, p2, p3)
            phase = "GP-GUIDED"

        print(
            f"\n[Bayesian | Trial {self._iteration}/{self.n_calls} | {phase}]"
            f"  piece_1={w1}  piece_2={w2}  piece_3={w3}"
            f"  (total={w1+w2+w3})"
        )

        # Store the actual widths sent to the chip so tell() logs the right values
        self._last_widths = (w1, w2, w3)

        # Write suggested widths to colormixcsv for your mix script to read
        write_widths_to_csv(w1, w2, w3, path=self.color_mix_csv)

        return w1, w2, w3

    # ── tell ───────────────────────────────────────────────────────────────────

    def tell(self, measured_hex: str) -> bool:
        """
        Feed the camera-measured hex color back to the optimizer.
        Appends the trial result to optimization_log.csv.

        Parameters
        ----------
        measured_hex : str
            Hex color string from camera (e.g. '#ff8040').

        Returns
        -------
        bool
            True if convergence threshold was reached (DeltaE < CONVERGENCE_DELTA_E).
        """
        if self._pending is None:
            raise RuntimeError("Call ask() before tell().")
        if self._last_widths is None:
            raise RuntimeError("ask() did not store widths — internal error.")

        color   = hex_to_color(measured_hex)
        de      = delta_e_ciede2000(color, self.target)
        is_best = de < self._best_de

        if is_best:
            self._best_de     = de
            self._best_params = self._pending

        # Use the actual widths that were sent to the chip (set in ask())
        # NOT a recalculation from GP params — those differ from the preset on trial 1
        w1, w2, w3 = self._last_widths
        p1, p2, p3 = map_to_simplex(*self._pending)

        step = OptimizationStep(
            iteration  = self._iteration,
            width_1=w1, width_2=w2, width_3=w3,
            prop_1=p1,  prop_2=p2,  prop_3=p3,
            result_hex = measured_hex.lower(),
            delta_e    = de,
            is_best    = is_best,
        )
        self._history.append(step)
        self._skopt.tell(self._pending, de)
        self._pending = None

        # Append this trial to the running CSV log
        append_trial_to_log(step, target_hex=self.target.hex, path=self.opt_log_csv)

        marker = " ★ NEW BEST" if is_best else ""
        print(
            f"  Result hex={measured_hex}  DeltaE={de:.2f}"
            f"  Best={self._best_de:.2f}{marker}"
        )

        if de < self.convergence_delta_e:
            self._converged = True
            print(f"  [CONVERGED] DeltaE={de:.2f} < {self.convergence_delta_e}")
            return True

        return False

    # ── result / log ───────────────────────────────────────────────────────────

    def get_result(self) -> OptimizationResult:
        """Return the best result found so far."""
        p1, p2, p3 = map_to_simplex(*self._best_params)
        w1, w2, w3 = proportions_to_widths(p1, p2, p3)
        return OptimizationResult(
            width_1      = w1,
            width_2      = w2,
            width_3      = w3,
            best_delta_e = self._best_de,
            converged    = self._converged,
            history      = self._history,
            target_hex   = self.target.hex,
        )

    def save_log(self, result: OptimizationResult) -> Path:
        """Save a JSON log of all trials and the best result."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.log_dir / f"blind_opt_{ts}.json"
        record = {
            "metadata": {
                "timestamp":           ts,
                "target_hex":          result.target_hex,
                "n_calls":             self.n_calls,
                "n_initial_points":    self.n_initial_points,
                "convergence_delta_e": self.convergence_delta_e,
                "max_total_width":     MAX_TOTAL_WIDTH,
                "random_seed":         self.random_seed,
                "note":                "Reservoir colors unknown — learned from camera hex only",
            },
            "best": {
                "width_1":   result.width_1,
                "width_2":   result.width_2,
                "width_3":   result.width_3,
                "delta_e":   result.best_delta_e,
                "converged": result.converged,
            },
            "history": [asdict(s) for s in result.history],
        }
        with path.open("w") as fh:
            json.dump(record, fh, indent=2)
        print(f"  [Log saved] → {path}")
        return path


# ══════════════════════════════════════════════════════════════════════════════
# Standalone entry point (manual hex input — for testing without master script)
# ══════════════════════════════════════════════════════════════════════════════

def main() -> OptimizationResult:
    """
    Standalone loop — takes a picture with the camera after each trial
    and feeds the measured hex automatically to the Bayesian optimizer.
    Set CAMERA_INDEX to whichever index your microscope is on.
    """
    from camera import CameraInterface

    CAMERA_INDEX = 1   # change if microscope is on a different index

    target = random_vivid_target_color(seed=RANDOM_SEED)
    print(f"\nTarget color: {target.hex}  rgb={target.r},{target.g},{target.b}")
    print(f"Trials: {N_CALLS}  |  Initial random: {N_INITIAL_POINTS}"
          f"  |  Converge at DeltaE < {CONVERGENCE_DELTA_E}\n")

    cam = CameraInterface(camera_address=CAMERA_INDEX)
    opt = BlindOptimizer(target, n_calls=N_CALLS)

    for _ in range(opt.n_calls):
        opt.ask()   # widths written to colormixcsv automatically

        input("  >>> Place color sample in front of camera, then press Enter to capture...")

        print("  [Camera] Taking picture...")
        image_path, frame = cam.take_picture()
        print(f"  [Camera] Saved: {image_path}")

        color_result = cam.detect_drop_color(frame)
        measured_hex = color_result['hex']
        print(f"  [Camera] Measured hex: {measured_hex}  rgb={color_result['rgb']}")

        converged = opt.tell(measured_hex)   # result appended to optimization_log.csv
        if converged:
            break

    result = opt.get_result()
    print(f"\nBest widths: piece_1={result.width_1}  piece_2={result.width_2}  piece_3={result.width_3}"
          f"  DeltaE={result.best_delta_e:.2f}  Converged={result.converged}")

    save = input("\nSave JSON log? [y/n]: ").strip().lower()
    if save == "y":
        opt.save_log(result)

    return result


if __name__ == "__main__":
    main()