"""
run_experiment.py
─────────────────
Wrapper that runs the Bayesian-optimized mixing sequence across multiple trials.

Each trial follows this sequence:
    Step 1: BlindOptimizer.ask()  — determines next widths, writes to colormixcsv
    Step 2: csvvolcont.main()     — CSV-controlled split/merge/mix sequence
    Step 3: CameraInterface       — takes picture and reports average color
    Step 4: BlindOptimizer.tell() — scores measured hex, updates model
    Step 5: cleanreload           — moves merged drop off chip, reloads reservoirs

The optimizer runs for up to N_CALLS trials, stopping early if DeltaE < 2.0.

Place this file in the same folder as all scripts:
    C:\\Users\\klmcg\\SULIProj\\ACX-CHIP-PROJECT\\

Run with:
    python run_experiment.py
"""

import sys
import os

# Ensure the script finds its siblings regardless of working directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import csvvolcont
from camera import CameraInterface
import cleanreload
from bayesopttest1 import BlindOptimizer, random_vivid_target_color


def run_camera() -> str:
    """
    Runs the camera sequence and returns the measured hex color string.
    Returns e.g. '#ff8040'
    """
    print("\n" + "=" * 60)
    print("CAMERA SEQUENCE STARTING")
    print("=" * 60)
    print("Starting camera script...")

    cam = CameraInterface(camera_address=0)

    frame_w, frame_h = cam.get_frame_size()

    print("Taking picture...")
    image_path, frame = cam.take_picture()
    print(f"Picture saved to: {image_path}")

    color_result = cam.detect_drop_color(frame)

    print(f"Average RGB color: {color_result['rgb']}")
    print(f"Average BGR color: {color_result['bgr']}")
    print(f"HEX color: {color_result['hex']}")

    return color_result['hex']   # returned to Bayesian optimizer


def main():
    print("=" * 60)
    print("BAYESIAN OPTIMIZATION EXPERIMENT")
    print("  Each trial: ask → csvvolcont → camera → tell → cleanreload")
    print("=" * 60)

    # ── Optimizer setup ───────────────────────────────────────────────────────
    target = random_vivid_target_color(seed=42)
    opt    = BlindOptimizer(target)
    print(f"\nTarget color: {target.hex}  rgb=({target.r}, {target.g}, {target.b})")
    print(f"Running up to {opt.n_calls} trials  |  "
          f"Trial 1 is random, rest are GP-guided\n")

    # ── Trial loop ────────────────────────────────────────────────────────────
    for trial in range(opt.n_calls):
        print(f"\n{'─' * 60}")
        print(f"TRIAL {trial + 1} of {opt.n_calls}")
        print(f"{'─' * 60}")

        # Step 1: Bayesian optimizer picks widths and writes to colormixcsv
        try:
            w1, w2, w3 = opt.ask()
        except Exception as e:
            print(f"\n[ERROR] Optimizer ask() failed: {e}")
            sys.exit(1)

        # Step 2: Mixing sequence reads colormixcsv and runs
        print(f"\n[Step 2] Starting csvvolcont...")
        try:
            csvvolcont.main()
        except Exception as e:
            print(f"\n[ERROR] csvvolcont failed: {e}")
            print("Stopping experiment.")
            sys.exit(1)

        # Step 3: Camera measures the mixed color
        print(f"\n[Step 3] Starting camera...")
        try:
            measured_hex = run_camera()
        except Exception as e:
            print(f"\n[ERROR] Camera sequence failed: {e}")
            print("Stopping experiment.")
            sys.exit(1)

        # Step 4: Feed result back to Bayesian optimizer
        print(f"\n[Step 4] Scoring result with Bayesian optimizer...")
        try:
            converged = opt.tell(measured_hex)
        except Exception as e:
            print(f"\n[ERROR] Optimizer tell() failed: {e}")
            sys.exit(1)

        # Step 5: Clean up chip and reload reservoirs for next trial
        print(f"\n[Step 5] Starting cleanreload...")
        try:
            cleanreload.move_piece_out()
            cleanreload.reload_reservoirs()
        except Exception as e:
            print(f"\n[ERROR] cleanreload failed: {e}")
            sys.exit(1)

        # Stop early if color is close enough
        if converged:
            print(f"\n[CONVERGED] Stopping after trial {trial + 1}.")
            break

    # ── Final result ──────────────────────────────────────────────────────────
    result = opt.get_result()

    print("\n" + "=" * 60)
    print("OPTIMIZATION COMPLETE")
    print(f"  Best widths:  piece_1={result.width_1}  "
          f"piece_2={result.width_2}  piece_3={result.width_3}")
    print(f"  Best DeltaE:  {result.best_delta_e:.2f}")
    print(f"  Converged:    {result.converged}")
    print(f"  Target hex:   {result.target_hex}")
    print("=" * 60)

    opt.save_log(result)


if __name__ == "__main__":
    main()