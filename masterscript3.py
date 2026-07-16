"""
run_experiment.py
─────────────────
Wrapper that runs the CSV-controlled mixing sequence (csvvolcont) followed by
the camera capture (camera). Place this file in the same folder as both scripts:
    C:\\Users\\klmcg\\SULIProj\\ACX-CHIP-PROJECT\\

Run with:
    python run_experiment.py

Step 1: csvvolcont.main() — CSV-controlled split/merge/mix sequence
Step 2: CameraInterface   — takes picture and reports average color

Note: camera.py has no main() function, so this wrapper imports CameraInterface
directly and runs the same sequence as camera.py's __main__ block.
"""

import sys
import os

# Ensure the script finds its siblings regardless of working directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import csvvolcont
from camera import CameraInterface


def run_camera():
    """Runs the same sequence as camera.py's __main__ block."""
    print("\n" + "=" * 60)
    print("CAMERA SEQUENCE STARTING")
    print("=" * 60)
    print("Starting camera script...")

    cam = CameraInterface(camera_address=0)

    print("Taking picture...")
    image_path, frame = cam.take_picture()
    print(f"Picture saved to: {image_path}")

    color_result = cam.get_average_color_from_rectangle(
        frame=frame,
        x=200,
        y=150,
        width=100,
        height=100,
    )

    print(f"Average RGB color: {color_result['rgb']}")
    print(f"Average BGR color: {color_result['bgr']}")
    print(f"HEX color: {color_result['hex']}")


def main():
    print("=" * 60)
    print("EXPERIMENT SEQUENCE START")
    print("  Step 1: csvvolcont  →  Step 2: camera")
    print("=" * 60)

    # ── Step 1: Mixing sequence ───────────────────────────────────────────────
    print("\n[1/2] Starting csvvolcont...")
    try:
        csvvolcont.main()
    except Exception as e:
        print(f"\n[ERROR] csvvolcont failed with exception: {e}")
        print("Camera sequence will NOT run. Exiting.")
        sys.exit(1)

    # ── Step 2: Camera sequence ───────────────────────────────────────────────
    print("\n[2/2] csvvolcont complete. Starting camera...")
    try:
        run_camera()
    except Exception as e:
        print(f"\n[ERROR] Camera sequence failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("EXPERIMENT SEQUENCE COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()