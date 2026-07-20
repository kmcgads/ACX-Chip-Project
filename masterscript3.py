"""
run_experiment.py
─────────────────
Wrapper that runs the CSV-controlled mixing sequence (csvvolcont) followed by
the camera capture (camera) and then the drop unload + reservoir reload (cleanreload).
Place this file in the same folder as all three scripts:
    C:\\Users\\klmcg\\SULIProj\\ACX-CHIP-PROJECT\\

Run with:
    python run_experiment.py

Step 1: csvvolcont.main()  — CSV-controlled split/merge/mix sequence
Step 2: CameraInterface    — takes picture and reports average color
Step 3: cleanreload        — moves merged drop off chip, reloads reservoirs to 10×15

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
import cleanreload


def run_camera():
    """Runs the same sequence as camera.py's __main__ block."""
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


def main():
    print("=" * 60)
    print("EXPERIMENT SEQUENCE START")
    print("  Step 1: csvvolcont  →  Step 2: camera  →  Step 3: cleanreload")
    print("=" * 60)

    # ── Step 1: Mixing sequence ───────────────────────────────────────────────
    print("\n[1/3] Starting csvvolcont...")
    try:
        csvvolcont.main()
    except Exception as e:
        print(f"\n[ERROR] csvvolcont failed with exception: {e}")
        print("Camera and cleanreload sequences will NOT run. Exiting.")
        sys.exit(1)

    # ── Step 2: Camera sequence ───────────────────────────────────────────────
    print("\n[2/3] csvvolcont complete. Starting camera...")
    try:
        run_camera()
    except Exception as e:
        print(f"\n[ERROR] Camera sequence failed: {e}")
        print("cleanreload sequence will NOT run. Exiting.")
        sys.exit(1)

    # ── Step 3: Move out merged drop + reload reservoirs ─────────────────────
    print("\n[3/3] Camera complete. Starting cleanreload...")
    try:
        cleanreload.move_piece_out()
        cleanreload.reload_reservoirs()
    except Exception as e:
        print(f"\n[ERROR] cleanreload failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("EXPERIMENT SEQUENCE COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
    