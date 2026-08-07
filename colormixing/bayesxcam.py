"""
mini_experiment.py
──────────────────
Standalone test loop: camera + Bayesian optimizer only.
No chip, no mixing hardware, no csvvolcont.

What this proves:
  1. Camera can capture a color and return a hex value.
  2. Bayesian optimizer can score that hex against a target (DeltaE).
  3. Bayesian suggests new reservoir widths after each measurement.
  4. The loop runs 5 times, stopping early if DeltaE < 2.0.

Each trial:
  Step 1 — Bayesian suggests widths (what mix WOULD be used on the chip)
  Step 2 — You manually place a color sample in front of the camera
  Step 3 — Camera captures the color and returns hex
  Step 4 — Bayesian scores it: DeltaE vs target, updates its model
  Step 5 — Repeat

Run with:
    python mini_experiment.py
"""

import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from researchos.camera import CameraInterface
from bayesopttest1 import BlindOptimizer, random_vivid_target_color

# ── Config ────────────────────────────────────────────────────────────────────

CAMERA_INDEX = 1   # Change to 0 if microscope is on index 0
                   # Run the index finder below if unsure:
                   #   import cv2
                   #   for i in range(5):
                   #       cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                   #       if cap.isOpened(): print(f"Camera at index {i}")
                   #       cap.release()

N_TRIALS     = 5
RANDOM_SEED  = None   # None = different random target color each run


# ── Camera helper ─────────────────────────────────────────────────────────────

def capture_color(cam: CameraInterface) -> str:
    """Take a picture and return the measured hex color string."""
    print("\n  [Camera] Taking picture...")
    image_path, frame = cam.take_picture()
    print(f"  [Camera] Image saved: {image_path}")

    color_result = cam.detect_drop_color(frame)

    print(f"  [Camera] Measured RGB : {color_result['rgb']}")
    print(f"  [Camera] Measured hex : {color_result['hex']}")
    return color_result['hex']


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 65)
    print("  MINI EXPERIMENT — Camera + Bayesian Optimizer Test")
    print("=" * 65)

    # ── Setup ──────────────────────────────────────────────────────────────
    target = random_vivid_target_color(seed=RANDOM_SEED)
    print(f"\n  Target color  : {target.hex}")
    print(f"  Target RGB    : ({target.r}, {target.g}, {target.b})")
    print(f"  Scoring metric: CIEDE2000 DeltaE  (< 2.0 = visually identical)")
    print(f"  Trials        : {N_TRIALS}  |  Trial 1 = random, rest = GP-guided")

    cam = CameraInterface(camera_address=CAMERA_INDEX)
    opt = BlindOptimizer(target, n_calls=N_TRIALS, n_initial_points=1)

    # ── Trial loop ─────────────────────────────────────────────────────────
    for trial in range(N_TRIALS):
        print(f"\n{'─' * 65}")
        print(f"  TRIAL {trial + 1} of {N_TRIALS}")
        print(f"{'─' * 65}")

        # Step 1: Bayesian suggests widths
        phase = "PRESET (5,5,5)" if trial == 0 else "GP-GUIDED"
        print(f"\n  [Bayesian | {phase}] Suggesting reservoir widths...")
        w1, w2, w3 = opt.ask()

        total = w1 + w2 + w3
        print(f"\n  ┌─ Suggested widths ──────────────────────┐")
        print(f"  │  piece_1 width : {w1:>3}  ({w1/total*100:.1f}% of mix)    │")
        print(f"  │  piece_2 width : {w2:>3}  ({w2/total*100:.1f}% of mix)    │")
        print(f"  │  piece_3 width : {w3:>3}  ({w3/total*100:.1f}% of mix)    │")
        print(f"  │  total         : {total:>3}                     │")
        print(f"  └─────────────────────────────────────────┘")
        print(f"\n  (On the chip these widths would set how much ink")
        print(f"   comes from each reservoir. Here we're just verifying")
        print(f"   the optimizer is producing valid, changing values.)")

        # Step 2: Prompt user to place color sample
        input(f"\n  >>> Place your color sample in front of the camera, then press Enter...")

        # Step 3: Camera captures color
        try:
            measured_hex = capture_color(cam)
        except Exception as e:
            print(f"\n  [ERROR] Camera failed: {e}")
            print("  Stopping experiment.")
            sys.exit(1)

        # Step 4: Bayesian scores the result
        print(f"\n  [Bayesian] Scoring measured color against target...")
        converged = opt.tell(measured_hex)

        # Print scoring breakdown
        result_so_far = opt.get_result()
        print(f"\n  ┌─ Scoring ───────────────────────────────┐")
        print(f"  │  Target hex    : {target.hex:<24}  │")
        print(f"  │  Measured hex  : {measured_hex:<24}  │")
        print(f"  │  DeltaE        : {opt._history[-1].delta_e:<6.2f}  (this trial)      │")
        print(f"  │  Best DeltaE   : {result_so_far.best_delta_e:<6.2f}  (best so far)    │")
        print(f"  │  Converged     : {str(converged):<24}  │")
        print(f"  └─────────────────────────────────────────┘")
        print(f"\n  How DeltaE works:")
        print(f"    0–2   = visually identical  ✓")
        print(f"    2–10  = noticeable difference")
        print(f"    10+   = very different colors")

        if converged:
            print(f"\n  [CONVERGED] DeltaE < 2.0 — color match found after {trial + 1} trial(s)!")
            break

        if trial < N_TRIALS - 1:
            print(f"\n  Bayesian will now use this score to suggest better widths next trial.")

    # ── Final result ────────────────────────────────────────────────────────
    result = opt.get_result()

    print(f"\n{'=' * 65}")
    print(f"  EXPERIMENT COMPLETE")
    print(f"{'=' * 65}")
    print(f"  Best widths found:")
    print(f"    piece_1 = {result.width_1}")
    print(f"    piece_2 = {result.width_2}")
    print(f"    piece_3 = {result.width_3}")
    print(f"  Best DeltaE  : {result.best_delta_e:.2f}")
    print(f"  Converged    : {result.converged}")
    print(f"  Target hex   : {result.target_hex}")
    print(f"\n  Full trial history saved to: optimization_log.csv")
    print(f"{'=' * 65}\n")

    opt.save_log(result)
    return result


if __name__ == "__main__":
    main()