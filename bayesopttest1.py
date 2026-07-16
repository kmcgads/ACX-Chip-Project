"""
blind_bayesian_optimizer.py
───────────────────────────
Pure Bayesian optimizer for 3-reservoir color matching.
No camera, no chip movement — integrate into your master script.

The optimizer knows nothing about the reservoir colors.
It only sees: suggested widths → measured hex → DeltaE → next suggestion.

─── Usage in your master script ───────────────────────────────────────────────

    from blind_bayesian_optimizer import BlindOptimizer, random_vivid_target_color

    target = random_vivid_target_color(seed=RANDOM_SEED)
    opt    = BlindOptimizer(target)

    for _ in range(opt.n_calls):
        w1, w2, w3 = opt.ask()          # optimizer suggests widths
        # ... write to CSV, run csvvolcont, measure with camera ...
        converged = opt.tell("#ff8040")  # pass measured hex back
        if converged:
            break

    result = opt.get_result()           # best widths + full history
    opt.save_log(result)                # optional JSON log

───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import colorsys
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

MAX_TOTAL_WIDTH     = 20     # Reservoir 1 + 2 + 3 widths always sum to this
N_CALLS             = 25     # Total optimization trials
N_INITIAL_POINTS    = 5      # Random trials before GP fitting begins
CONVERGENCE_DELTA_E = 2.0    # DeltaE < this = visually identical, stop
RANDOM_SEED         = 42
LOG_DIR             = Path("experiment_logs")


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
    """Convert proportions to integer piece widths summing to `total`. Min width = 1."""
    w1 = max(1, round(p1 * total))
    w2 = max(1, round(p2 * total))
    w3 = max(1, total - w1 - w2)
    return w1, w2, w3


# ══════════════════════════════════════════════════════════════════════════════
# Blind Bayesian Optimizer
# ══════════════════════════════════════════════════════════════════════════════

class BlindOptimizer:
    """
    Bayesian optimizer for 3-reservoir color matching with unknown reservoir colors.

    The optimizer is "blind" — it has no model of what colors the reservoirs
    contain. It learns purely from the DeltaE of each measured hex result.

    Interface for master script
    ───────────────────────────
    opt = BlindOptimizer(target)

    w1, w2, w3 = opt.ask()       → get next suggested widths
    converged  = opt.tell(hex)   → feed measured hex back, returns True if done
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
    ) -> None:
        self.target              = target
        self.n_calls             = n_calls
        self.n_initial_points    = n_initial_points
        self.convergence_delta_e = convergence_delta_e
        self.random_seed         = random_seed
        self.log_dir             = Path(log_dir)

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
        self._pending:      Optional[list[float]] = None   # last ask() params
        self._converged     = False

    # ── ask ────────────────────────────────────────────────────────────────────

    def ask(self) -> tuple[int, int, int]:
        """
        Ask the optimizer for the next suggested reservoir widths.
        Returns (width_1, width_2, width_3) — integers summing to MAX_TOTAL_WIDTH.
        Must be followed by a call to tell() before the next ask().
        """
        self._pending = self._skopt.ask()
        p1, p2, p3   = map_to_simplex(*self._pending)
        w1, w2, w3   = proportions_to_widths(p1, p2, p3)
        self._iteration += 1

        phase = "RANDOM" if self._iteration <= self.n_initial_points else "GP-GUIDED"
        print(
            f"\n[Bayesian | Trial {self._iteration}/{self.n_calls} | {phase}]"
            f"  Reservoir 1={w1}px  Reservoir 2={w2}px  Reservoir 3={w3}px"
            f"  (total={w1+w2+w3}px)"
        )
        return w1, w2, w3

    # ── tell ───────────────────────────────────────────────────────────────────

    def tell(self, measured_hex: str) -> bool:
        """
        Feed the camera-measured hex color back to the optimizer.

        Parameters
        ----------
        measured_hex : str
            Hex color string from camera.py (e.g. '#ff8040').

        Returns
        -------
        bool
            True if convergence threshold was reached (DeltaE < CONVERGENCE_DELTA_E).
            Your master script can use this to break the loop early.
        """
        if self._pending is None:
            raise RuntimeError("Call ask() before tell().")

        color   = hex_to_color(measured_hex)
        de      = delta_e_ciede2000(color, self.target)
        is_best = de < self._best_de

        if is_best:
            self._best_de     = de
            self._best_params = self._pending

        p1, p2, p3 = map_to_simplex(*self._pending)
        w1, w2, w3 = proportions_to_widths(p1, p2, p3)

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
    Standalone loop — prompts you to manually enter the measured hex color
    after each trial. Use this to test the optimizer logic without hardware,
    or replace the input() call with your camera measurement in the master script.
    """
    target = random_vivid_target_color(seed=RANDOM_SEED)
    print(f"\nTarget color: {target.hex}  rgb={target.r},{target.g},{target.b}")
    print(f"Trials: {N_CALLS}  |  Initial random: {N_INITIAL_POINTS}"
          f"  |  Converge at DeltaE < {CONVERGENCE_DELTA_E}\n")

    opt = BlindOptimizer(target)

    for _ in range(opt.n_calls):
        opt.ask()
        hex_input = input("  Enter measured hex color (e.g. #ff8040): ").strip()
        converged = opt.tell(hex_input)
        if converged:
            break

    result = opt.get_result()
    print(f"\nBest widths: R1={result.width_1}  R2={result.width_2}  R3={result.width_3}"
          f"  DeltaE={result.best_delta_e:.2f}  Converged={result.converged}")

    save = input("\nSave log? [y/n]: ").strip().lower()
    if save == "y":
        opt.save_log(result)

    return result


if __name__ == "__main__":
    main()