import ctypes
import json
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


# ── CONFIG ────────────────────────────────────────────────────────────────────

N_TRIALS            = 12
N_INITIAL_POINTS    = 3
CONVERGENCE_DELTA_E = 2.0
RANDOM_SEED         = 42
CAMERA_ADDRESS      = 0

VOLT_ON  = [45, 45, 45, 0, 0, 0, 0, 0, 0]
VOLT_OFF = [0,  0,  0,  0, 0, 0, 0, 0, 0]

DLL_PATH = ("Path_from_acx")
LOG_DIR  = Path("experiment_logs")


# ── CHIP GEOMETRY ─────────────────────────────────────────────────────────────

MAIN_H   = 10
MAIN_W   = 15
MAIN_COL = 5

INK_ROWS: dict[str, int] = {
    "ink_a": 55,
    "ink_b": 85,
    "ink_c": 10,
}
STASH_NAMES = ["ink_a", "ink_b", "ink_c"]

PIECE_H       = 10
PIECE_MIN_W   = 2
PIECE_MIN_H   = 2
PIECE_HOLD_W  = 6

LOAD_W          = 20
STRETCH_TARGET  = 45
STRETCH_RANGE   = STRETCH_TARGET - LOAD_W
PIECE_START_COL = 30
PIECE_START_W   = 15
STRETCH_STEPS   = 25
PIECE_FINAL_COL = PIECE_START_COL + STRETCH_STEPS
NECK_START      = MAIN_COL + MAIN_W
NECK_END        = PIECE_FINAL_COL - 1

MERGED_TOTAL_W = 15
MEETING_ROW    = 55
MEETING_COL    = 30

CAM_ROW, CAM_COL = 55, 105
CAM_H = PIECE_H
CAM_W = MERGED_TOTAL_W

MIX_STEP_S = 0.3

EXTRACT_ROW = 55
EXTRACT_COL = 128


# ── TERMINAL UI ───────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"


def _swatch(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"\033[48;2;{r};{g};{b}m   {_RESET} {hex_color}"
    except ValueError:
        return hex_color


def _color_de(de: float) -> str:
    if de < 2.0:   return f"{_GREEN}{de:6.2f}{_RESET}"
    if de < 5.0:   return f"{_YELLOW}{de:6.2f}{_RESET}"
    return f"{_RED}{de:6.2f}{_RESET}"


def _bar(value: float, width: int = 15) -> str:
    n = round(max(0.0, min(1.0, value)) * width)
    return f"[{'█' * n}{'░' * (width - n)}]"


def _header(title: str, width: int = 72) -> str:
    pad = max(0, (width - len(title) - 2) // 2)
    return f"{'═' * pad} {_BOLD}{title}{_RESET} {'═' * pad}"


def _divider(char: str = "─", width: int = 72) -> str:
    return char * width


def _pause(prompt: str = "Press ENTER to continue...") -> None:
    input(f"\n  {_CYAN}▶  {prompt}{_RESET}  ")


def _confirm(prompt: str) -> bool:
    while True:
        answer = input(f"\n  {_CYAN}?  {prompt} [y/n]: {_RESET}").strip().lower()
        if answer in ("y", "yes"):  return True
        if answer in ("n", "no"):   return False
        print("     Please enter y or n.")


# ── VOLUME PARAMETERISATION ───────────────────────────────────────────────────

def map_to_simplex(x1: float, x2: float) -> tuple[float, float, float]:
    f_a = float(x1)
    f_b = (1.0 - f_a) * float(x2)
    f_c = 1.0 - f_a - f_b
    return f_a, f_b, f_c


def fractions_to_widths(f_a: float, f_b: float, f_c: float) -> tuple[int, int, int]:
    total = MERGED_TOTAL_W
    w_a   = max(PIECE_MIN_W, round(f_a * total))
    w_b   = max(PIECE_MIN_W, round(f_b * total))
    w_c   = total - w_a - w_b
    if w_c < PIECE_MIN_W:
        w_c = PIECE_MIN_W
        if w_a >= w_b:
            w_a = max(PIECE_MIN_W, w_a - 1)
        else:
            w_b = max(PIECE_MIN_W, w_b - 1)
    return w_a, w_b, w_c


# ── HARDWARE ──────────────────────────────────────────────────────────────────

_dll = ctypes.CDLL(DLL_PATH)
_dll.SetPower.argtypes     = [ctypes.c_bool]
_dll.SetVolt.argtypes      = [c_int] * 9
_dll.InquireVolt.argtypes  = [POINTER(c_int)] * 9
_dll.ActivateElec.argtypes = [c_int, c_int, c_int, c_void_p]
_dll.ActivateElec.restype  = c_int

_dll_lock = threading.Lock()


class Drop(Structure):
    _fields_ = [
        ("height", c_int),
        ("width",  c_int),
        ("row",    c_int),
        ("col",    c_int),
    ]


def _hw_activate(drops: list[Drop]) -> None:
    n   = len(drops)
    arr = (Drop * n)(*drops)
    with _dll_lock:
        ret = _dll.ActivateElec(128, 128, n, arr)
    if ret != 0:
        print(f"  \033[91m⚠  ActivateElec returned {ret} — electrode fault\033[0m")


def activate(drops: list[Drop], label: str = "") -> None:
    _hw_activate(drops)
    time.sleep(0.5)


def _activate_neck(drops: list[Drop]) -> None:
    _hw_activate(drops)
    time.sleep(1.0)


def _activate_mix(drops: list[Drop]) -> None:
    _hw_activate(drops)
    time.sleep(MIX_STEP_S)


# ── HOLD LOOP ─────────────────────────────────────────────────────────────────

class HoldLoop:
    def __init__(self) -> None:
        self._drops: list[Drop]              = []
        self._stop                            = threading.Event()
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


def _held_pause(hold: HoldLoop, drops: list[Drop], prompt: str) -> None:
    hold.set_drops(drops)
    hold.start()
    input(prompt)
    hold.stop()


# ── STARTUP / SHUTDOWN ────────────────────────────────────────────────────────

def startup() -> None:
    print("--- STARTUP ---")
    _dll.InitUSB()
    if not _dll.OpenUSB():
        raise SystemExit("USB failed to open.")
    _dll.SetPower(True)
    time.sleep(2)
    _dll.SetVolt(*VOLT_ON)
    time.sleep(1)
    voltages = [c_int(0) for _ in range(9)]
    _dll.InquireVolt(*[ctypes.byref(v) for v in voltages])
    actual = [v.value for v in voltages]
    print(f"Voltage confirmed: {actual}")
    if actual != VOLT_ON:
        print(f"  WARNING: voltage mismatch — expected {VOLT_ON}")
        input("  Press Enter to continue anyway, or close to abort...")
    else:
        print("  Voltage OK\n")


def shutdown() -> None:
    _dll.ActivateElec(128, 128, 0, None)
    time.sleep(0.5)
    _dll.SetPower(False)
    _dll.CloseUSB()
    print("Shutdown complete.")


# ── CONNECTION CHECKS ─────────────────────────────────────────────────────────

def confirm_connections(camera: Optional[cv2.VideoCapture] = None) -> None:
    print()
    print(_header("Pre-Experiment Connection Check", 72))

    print(f"\n  {_BOLD}[1/2] Device connection{_RESET}")
    voltages = [c_int(0) for _ in range(9)]
    _dll.InquireVolt(*[ctypes.byref(v) for v in voltages])
    actual = [v.value for v in voltages]
    print(f"  Voltage  : {actual}  (expected {VOLT_ON})")
    if actual == VOLT_ON:
        print(f"  Status   : {_GREEN}✔  voltage matches{_RESET}")
    else:
        print(f"  Status   : {_YELLOW}⚠  mismatch — check channels{_RESET}")
    if not _confirm("Device voltage looks correct — continue?"):
        raise SystemExit("Aborted at device confirmation.")

    print(f"\n  {_BOLD}[2/2] Camera connection{_RESET}")
    if camera is None or not camera.isOpened():
        raise SystemExit("Aborted — camera not available.")
    for _ in range(5):
        camera.read()
    ok, frame = camera.read()
    if not ok:
        raise SystemExit("Aborted — camera read error.")
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = Path(f"camera_check_{ts}.jpg")
    cv2.imwrite(str(snap_path), frame)
    h_px, w_px = frame.shape[:2]
    print(f"  Status   : {_GREEN}✔  camera responding{_RESET}  ({w_px}×{h_px}px)")
    print(f"  Snapshot : {snap_path}")
    if not _confirm("Camera image looks good — continue?"):
        raise SystemExit("Aborted at camera confirmation.")

    print(f"\n  {_GREEN}{_BOLD}All connections confirmed.{_RESET}\n")


# ── MOVEMENT HELPERS ──────────────────────────────────────────────────────────

def _move(h: int, w: int, r0: int, c0: int, r1: int, c1: int,
          held: list[Drop], label: str = "") -> None:
    r, c = r0, c0
    while r != r1 or c != c1:
        if   r < r1: r += 1
        elif r > r1: r -= 1
        if   c < c1: c += 1
        elif c > c1: c -= 1
        activate(held + [Drop(h, w, r, c)], label)


def _main_drops() -> list[Drop]:
    return [Drop(MAIN_H, MAIN_W, INK_ROWS[name], MAIN_COL) for name in STASH_NAMES]


# ── STEP 1 — Load and hold main drops ────────────────────────────────────────

def step1_hold_mains(hold: HoldLoop) -> None:
    print()
    print(_header("Step 1 — Load and Hold Inks", 72))
    mains = _main_drops()

    for j, name in enumerate(STASH_NAMES):
        row = INK_ROWS[name]
        print(f"\n  {_BOLD}Ink {j+1}/3 — {name}{_RESET}  row={row}, col={MAIN_COL}  ({MAIN_H}×{MAIN_W})")
        activate(mains[:j + 1])
        hold.set_drops(mains[:j + 1])
        if j == 0:
            hold.start()
        print(f"  {_GREEN}▶  Electrode ACTIVE. Place {name} now.{_RESET}")
        input(f"     Press Enter once drop is seated: ")
        print(f"  {_GREEN}✔  {name} confirmed.{_RESET}")

    print()
    input("  Confirm ALL THREE drops are stable, then press Enter: ")
    print(f"  {_GREEN}✔  Ink hold confirmed.{_RESET}\n")


# ── STEP 3 — Split pieces ─────────────────────────────────────────────────────

def step3_split_pieces(widths: tuple[int, int, int], hold: HoldLoop) -> list[Drop]:
    hold.stop()
    w_a, w_b, w_c = widths
    target_widths  = [w_a, w_b, w_c]

    print(f"[Step 3] {_BOLD}Splitting pieces{_RESET}  "
          f"ink_a={w_a}  ink_b={w_b}  ink_c={w_c}  "
          f"(sum={sum(widths)})  piece_height={PIECE_H}")

    pieces: list[Drop] = []
    future_hold = HoldLoop()

    for i, name in enumerate(STASH_NAMES):
        row      = INK_ROWS[name]
        target_w = target_widths[i]

        already_mains = [Drop(MAIN_H, MAIN_W, INK_ROWS[STASH_NAMES[j]], MAIN_COL) for j in range(i)]
        base          = already_mains + list(pieces)
        future_mains  = [Drop(MAIN_H, MAIN_W, INK_ROWS[STASH_NAMES[j]], MAIN_COL) for j in range(i + 1, len(STASH_NAMES))]
        all_held      = base + future_mains

        print(f"\n  {_BOLD}{name}{_RESET}  row={row}  target_width={target_w}  piece_height={PIECE_H}")

        # 0. LOAD
        drops_load = all_held + [Drop(MAIN_H, LOAD_W, row, MAIN_COL)]
        activate(drops_load)
        _held_pause(hold, drops_load,
                    f"\n     {name} ACTIVE ({MAIN_H}×{LOAD_W}) — load drop, then press Enter to stretch: ")

        # 1. STRETCH
        print(f"  {name}: stretching {LOAD_W} → {STRETCH_TARGET}...")
        if future_mains:
            future_hold.set_drops(future_mains)
            future_hold.start()
        for step in range(1, STRETCH_RANGE + 1):
            activate(base + [Drop(MAIN_H, LOAD_W + step, row, MAIN_COL)])
        if future_mains:
            future_hold.stop()

        _held_pause(hold, all_held + [Drop(MAIN_H, STRETCH_TARGET, row, MAIN_COL)],
                    f"     {name} stretched — confirm neck visible, press Enter to pattern: ")

        # 2. PATTERN
        drops_pattern = all_held + [
            Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
            Drop(MAIN_H, PIECE_START_W, row, PIECE_START_COL),
        ]
        activate(drops_pattern)
        _held_pause(hold, drops_pattern,
                    f"     {name} patterned — confirm both regions, press Enter to move: ")

        # 3. MOVE + PINCH
        if future_mains:
            future_hold.stop()
        move_target_w = max(target_w, PIECE_HOLD_W)
        print(f"  {name}: moving {STRETCH_STEPS} steps, pinching {PIECE_START_W} → {move_target_w}...")
        for step in range(1, STRETCH_STEPS + 1):
            cur_col = PIECE_START_COL + step
            cur_w   = max(move_target_w, round(
                PIECE_START_W - (PIECE_START_W - move_target_w) * step / STRETCH_STEPS
            ))
            activate(base + future_mains + [
                Drop(MAIN_H, MAIN_W, row, MAIN_COL),
                Drop(MAIN_H, cur_w,  row, cur_col),
            ])

        _held_pause(hold, all_held + [
            Drop(MAIN_H, MAIN_W,        row, MAIN_COL),
            Drop(MAIN_H, move_target_w, row, PIECE_FINAL_COL),
        ], f"     {name} at col={PIECE_FINAL_COL} — confirm piece visible, press Enter to break neck: ")

        # 4. NECK DEACTIVATION
        print(f"  {name}: neck Phase 1 — hard cut (3 s)...")
        _activate_neck(base + future_mains + [
            Drop(MAIN_H,  MAIN_W,        row, MAIN_COL),
            Drop(PIECE_H, move_target_w, row, PIECE_FINAL_COL),
        ])
        time.sleep(2.0)

        print(f"  {name}: neck Phase 2 — sweep col={NECK_END}→{NECK_START}...")
        for release_col in range(NECK_END, NECK_START - 1, -1):
            bridge_width = release_col - NECK_START
            neck_drops = base + future_mains + [Drop(MAIN_H, MAIN_W, row, MAIN_COL)]
            if bridge_width > 0:
                neck_drops += [Drop(MAIN_H, bridge_width, row, NECK_START)]
            neck_drops += [Drop(PIECE_H, move_target_w, row, PIECE_FINAL_COL)]
            _activate_neck(neck_drops)

        # 5. FINAL PINCH
        if move_target_w > target_w:
            print(f"  {name}: final pinch {move_target_w} → {target_w}...")
            for pinch_w in range(move_target_w - 1, target_w - 1, -1):
                activate(base + future_mains + [
                    Drop(MAIN_H,  MAIN_W,  row, MAIN_COL),
                    Drop(PIECE_H, pinch_w, row, PIECE_FINAL_COL),
                ])
        activate(base + future_mains + [
            Drop(MAIN_H,  MAIN_W,   row, MAIN_COL),
            Drop(PIECE_H, target_w, row, PIECE_FINAL_COL),
        ])

        pieces.append(Drop(PIECE_H, target_w, row, PIECE_FINAL_COL))
        print(f"  {_GREEN}✔  {name}: piece ready (row={row}, col={PIECE_FINAL_COL}, h={PIECE_H}, w={target_w}){_RESET}")
        _held_pause(hold, all_held + [
            Drop(MAIN_H,  MAIN_W,   row, MAIN_COL),
            Drop(PIECE_H, target_w, row, PIECE_FINAL_COL),
        ], f"     {name} fully split — press Enter for next ink: ")

    future_hold.stop()
    hold.set_drops(_main_drops())
    hold.start()
    return pieces


# ── STEP 4 — Converge and merge ───────────────────────────────────────────────

def step4_merge(pieces: list[Drop], hold: HoldLoop) -> None:
    hold.stop()
    mains = _main_drops()
    print(f"\n[Step 4] {_BOLD}Merging at ({MEETING_ROW},{MEETING_COL}){_RESET}")

    # Phase A: row alignment
    row_steps = max(abs(p.row - MEETING_ROW) for p in pieces)
    print(f"  Phase A: row alignment ({row_steps} steps → row={MEETING_ROW})...")
    for step in range(1, row_steps + 1):
        moving = []
        for p in pieces:
            if   p.row < MEETING_ROW: new_r = min(p.row + step, MEETING_ROW)
            elif p.row > MEETING_ROW: new_r = max(p.row - step, MEETING_ROW)
            else:                     new_r = MEETING_ROW
            moving.append(Drop(p.height, p.width, new_r, p.col))
        activate(mains + moving)

    aligned = [Drop(p.height, p.width, MEETING_ROW, p.col) for p in pieces]
    print(f"  {_GREEN}Phase A complete.{_RESET}")
    _held_pause(hold, mains + aligned,
                f"     Confirm pieces on row={MEETING_ROW}, press Enter for Phase B: ")

    # Phase B: column sweep
    col_steps = PIECE_FINAL_COL - MEETING_COL
    print(f"  Phase B: column sweep ({col_steps} steps → col={MEETING_COL})...")
    for step in range(1, col_steps + 1):
        cur_col = PIECE_FINAL_COL - step
        activate(mains + [Drop(p.height, p.width, MEETING_ROW, cur_col) for p in aligned])

    print(f"  {_GREEN}Phase B complete.{_RESET}")
    _held_pause(hold, mains + [Drop(p.height, p.width, MEETING_ROW, MEETING_COL) for p in aligned],
                f"     Confirm pieces at col={MEETING_COL}, press Enter to merge: ")

    # Merge
    drops_merged = mains + [Drop(PIECE_H, MERGED_TOTAL_W, MEETING_ROW, MEETING_COL)]
    activate(drops_merged)
    print(f"  {_GREEN}✔  Merged: {PIECE_H}h × {MERGED_TOTAL_W}w at ({MEETING_ROW},{MEETING_COL}){_RESET}")
    _held_pause(hold, drops_merged,
                f"     Confirm merged drop visible, press Enter to mix: ")

    hold.set_drops(_main_drops())
    hold.start()


# ── STEP 5 — Mix ──────────────────────────────────────────────────────────────

def step5_mix(hold: HoldLoop) -> None:
    hold.stop()
    mains = _main_drops()
    H, W  = PIECE_H, MERGED_TOTAL_W
    r, c  = MEETING_ROW, MEETING_COL
    print(f"[Step 5] Mixing — 6 passes from ({r},{c}), {MIX_STEP_S}s/step...")

    # Pass 1: right 30 → back
    print("  Pass 1: right 30...")
    for i in range(1, 31):    _activate_mix(mains + [Drop(H, W, r, c + i)])
    for i in range(30, -1, -1): _activate_mix(mains + [Drop(H, W, r, c + i)])

    # Pass 2: diagonal up-right 20 → back
    print("  Pass 2: diagonal up-right 20...")
    for i in range(1, 21):    _activate_mix(mains + [Drop(H, W, r - i, c + i)])
    for i in range(20, -1, -1): _activate_mix(mains + [Drop(H, W, r - i, c + i)])

    # Pass 3: right 30, down 20, diagonal back
    print("  Pass 3: right-then-down...")
    for i in range(1, 31):    _activate_mix(mains + [Drop(H, W, r, c + i)])
    for i in range(1, 21):    _activate_mix(mains + [Drop(H, W, r + i, c + 30)])
    for i in range(20, -1, -1): _activate_mix(mains + [Drop(H, W, r + i, c + i)])

    # Pass 4: right 30 double-pass
    print("  Pass 4: right 30 double-pass...")
    for _ in range(2):
        for i in range(1, 31):    _activate_mix(mains + [Drop(H, W, r, c + i)])
        for i in range(30, -1, -1): _activate_mix(mains + [Drop(H, W, r, c + i)])

    # Pass 5: diagonal down-right 15 → back
    print("  Pass 5: diagonal down-right 15...")
    for i in range(1, 16):    _activate_mix(mains + [Drop(H, W, r + i, c + i)])
    for i in range(15, -1, -1): _activate_mix(mains + [Drop(H, W, r + i, c + i)])

    # Pass 6: clockwise rectangle 30×20
    print("  Pass 6: clockwise rectangle 30×20...")
    for i in range(1, 31):    _activate_mix(mains + [Drop(H, W, r,      c + i)])
    for i in range(1, 21):    _activate_mix(mains + [Drop(H, W, r + i,  c + 30)])
    for i in range(30, -1, -1): _activate_mix(mains + [Drop(H, W, r + 20, c + i)])
    for i in range(20, -1, -1): _activate_mix(mains + [Drop(H, W, r + i,  c)])

    print(f"  {_GREEN}Mix complete — drop at ({r},{c}).{_RESET}")
    hold.set_drops(_main_drops())
    hold.start()


# ── STEP 6 — Move to camera ───────────────────────────────────────────────────

def step6_move_to_camera(hold: HoldLoop) -> None:
    hold.stop()
    mains = _main_drops()
    print(f"[Step 6] Moving to camera at ({CAM_ROW},{CAM_COL})...")
    _move(MAIN_H, MERGED_TOTAL_W, MEETING_ROW, MEETING_COL, CAM_ROW, CAM_COL, mains)
    activate(mains + [Drop(CAM_H, CAM_W, CAM_ROW, CAM_COL)])
    hold.set_drops(_main_drops())
    hold.start()


# ── COLOR MEASUREMENT ─────────────────────────────────────────────────────────

@dataclass
class ColorMeasurement:
    r: int
    g: int
    b: int

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
                       gamma: float = 2.2) -> dict:
    hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s_ch = hsv[:, :, 1]
    v_ch = hsv[:, :, 2]

    dark_mask  = (v_ch >  20) & (v_ch <  80) & (s_ch >= max(15, min_saturation // 2))
    mid_mask   = (v_ch >= 80) & (v_ch < 250) & (s_ch >= min_saturation)
    color_mask = (dark_mask | mid_mask).astype(np.uint8) * 255

    kernel     = np.ones((5, 5), np.uint8)
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel)
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN,  kernel)

    contours, _ = cv2.findContours(color_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("No colored drop found — try lowering min_saturation.")
    largest = max(contours, key=cv2.contourArea)
    area    = cv2.contourArea(largest)
    if area < min_area:
        raise ValueError(f"Largest region is {area:.0f} px² < min_area={min_area}.")

    drop_fill = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.drawContours(drop_fill, [largest], -1, 255, thickness=cv2.FILLED)

    vivid_mask   = (drop_fill == 255) & (s_ch >= sample_saturation) & (v_ch > 20) & (v_ch < 250)
    vivid_pixels = frame[vivid_mask].reshape(-1, 3)
    if len(vivid_pixels) == 0:
        broad_mask   = (drop_fill == 255) & (color_mask == 255)
        vivid_pixels = frame[broad_mask].reshape(-1, 3)
        vivid_sat    = s_ch[broad_mask]
    else:
        vivid_sat    = s_ch[vivid_mask]

    if len(vivid_pixels) == 0:
        raise ValueError("No saturated pixels inside contour — check lighting.")

    bvals = np.mean(vivid_pixels, axis=1)
    lo    = np.percentile(bvals, brightness_lo)
    hi    = np.percentile(bvals, brightness_hi)
    mask  = (bvals >= lo) & (bvals <= hi)
    px    = vivid_pixels[mask] if mask.sum() > 0 else vivid_pixels
    sat   = vivid_sat[mask]    if mask.sum() > 0 else vivid_sat

    w     = sat.astype(float)
    w_sum = w.sum()
    if w_sum == 0:
        w = np.ones(len(px)); w_sum = float(len(px))
    b_raw = np.average(px[:, 0], weights=w)
    g_raw = np.average(px[:, 1], weights=w)
    r_raw = np.average(px[:, 2], weights=w)

    if gamma != 1.0:
        pixel_bgr = np.array([[[int(b_raw), int(g_raw), int(r_raw)]]], dtype=np.uint8)
        pixel_hsv = cv2.cvtColor(pixel_bgr, cv2.COLOR_BGR2HSV).astype(float)
        pixel_hsv[0, 0, 2] = min(255.0, 255.0 * (pixel_hsv[0, 0, 2] / 255.0) ** (1.0 / gamma))
        pixel_bgr = cv2.cvtColor(pixel_hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        b_raw, g_raw, r_raw = float(pixel_bgr[0, 0, 0]), float(pixel_bgr[0, 0, 1]), float(pixel_bgr[0, 0, 2])

    b, g, r   = int(round(b_raw)), int(round(g_raw)), int(round(r_raw))
    hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
    return {"rgb": (r, g, b), "bgr": (b, g, r), "hex": hex_color, "area_px": int(area)}


# ── STEP 7 — Capture color ────────────────────────────────────────────────────

def step7_read_color(camera: cv2.VideoCapture) -> ColorMeasurement:
    print("[Step 7] Capturing color...")
    frames = []
    for _ in range(5):
        ok, frame = camera.read()
        if not ok:
            raise RuntimeError("Camera read failed.")
        frames.append(frame)
    result = _detect_drop_color(frames[2])
    color  = ColorMeasurement(r=result["rgb"][0], g=result["rgb"][1], b=result["rgb"][2])
    print(f"  Captured: {_swatch(color.hex)}  area={result['area_px']} px²")
    return color


# ── CIEDE2000 ─────────────────────────────────────────────────────────────────

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
    T = (1.0 - 0.17 * np.cos(np.radians(hp - 30))
             + 0.24 * np.cos(np.radians(2 * hp))
             + 0.32 * np.cos(np.radians(3 * hp + 6))
             - 0.20 * np.cos(np.radians(4 * hp - 63)))
    SL  = 1.0 + 0.015 * (Lp - 50)**2 / np.sqrt(20 + (Lp - 50)**2)
    SC  = 1.0 + 0.045 * Cp;  SH = 1.0 + 0.015 * Cp * T
    Cp7 = Cp**7
    RC  = 2.0 * np.sqrt(Cp7 / (Cp7 + 25.0**7))
    RT  = -np.sin(np.radians(60 * np.exp(-((hp - 275) / 25)**2))) * RC
    return float(np.sqrt((dLp/SL)**2 + (dCp/SC)**2 + (dHp/SH)**2 + RT*(dCp/SC)*(dHp/SH)))


def delta_e(a: ColorMeasurement, b: ColorMeasurement) -> float:
    return ciede2000(_to_lab(a), _to_lab(b))


# ── STEP 8 — Evaluate ────────────────────────────────────────────────────────

def step8_evaluate(result: ColorMeasurement, target: ColorMeasurement) -> float:
    de = delta_e(result, target)
    print(f"[Step 8] ΔE₀₀ = {_color_de(de)}  "
          f"| Result: {_swatch(result.hex)}  | Target: {_swatch(target.hex)}")
    return de


# ── STEP 9 — Extract ──────────────────────────────────────────────────────────

def step9_extract(hold: HoldLoop) -> None:
    hold.stop()
    mains = _main_drops()
    print(f"[Step 9] Extracting → ({EXTRACT_ROW},{EXTRACT_COL})...")
    _move(CAM_H, CAM_W, CAM_ROW, CAM_COL, EXTRACT_ROW, EXTRACT_COL, mains)
    activate(mains)
    print(f"  {_BOLD}★  Remove the drop from the chip now.{_RESET}")
    input("     Press Enter once drop is removed: ")
    print(f"  {_GREEN}✔  Drop removed.{_RESET}")
    hold.set_drops(_main_drops())
    hold.start()


# ── STEP 10 — Reload ──────────────────────────────────────────────────────────

def step10_reload(hold: HoldLoop) -> None:
    print()
    print(_header("Step 10 — Reload Inks", 72))
    hold.stop()
    hold.set_drops(_main_drops())
    hold.start()

    for j, name in enumerate(STASH_NAMES):
        row = INK_ROWS[name]
        print(f"\n  {_BOLD}Ink {j+1}/3 — {name}{_RESET}  row={row}, col={MAIN_COL}")
        print(f"  {_GREEN}▶  Electrode ACTIVE. Reload {name} now.{_RESET}")
        input(f"     Press Enter once seated: ")
        print(f"  {_GREEN}✔  {name} reloaded.{_RESET}")

    print()
    input("  Confirm ALL THREE inks reloaded, then press Enter: ")
    print(f"  {_GREEN}✔  All inks reloaded.\n{_RESET}")


# ── TARGET COLOR HELPERS ──────────────────────────────────────────────────────

def random_target_color(seed: Optional[int] = None) -> ColorMeasurement:
    rng = random.Random(seed)
    return ColorMeasurement(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))


def hex_to_color(hex_str: str) -> ColorMeasurement:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Expected 6 hex digits, got: {hex_str!r}")
    return ColorMeasurement(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ── TRIAL RECORD ──────────────────────────────────────────────────────────────

@dataclass
class TrialRecord:
    trial:      int
    frac_a:     float;   frac_b:  float;   frac_c:  float
    width_a:    int;     width_b: int;     width_c: int
    result_hex: str
    target_hex: str
    delta_e:    float
    is_best:    bool
    timestamp:  str = field(default_factory=lambda: datetime.now().isoformat())


# ── MAIN EXPERIMENT LOOP ──────────────────────────────────────────────────────

def run_experiment(target: ColorMeasurement, camera: Optional[cv2.VideoCapture]) -> None:
    hold       = HoldLoop()
    history:   list[TrialRecord] = []
    best_de    = float("inf")
    best_fracs = (1/3, 1/3, 1/3)

    print()
    print(_header("ACX DMF Color Match Experiment", 72))
    print(f"  Target     : {_swatch(target.hex)}  rgb=({target.r},{target.g},{target.b})")
    print(f"  Trials     : up to {N_TRIALS}  ({N_INITIAL_POINTS} random + {N_TRIALS - N_INITIAL_POINTS} GP)")
    print(f"  Converge at: ΔE₀₀ < {CONVERGENCE_DELTA_E}")
    print(_divider("═", 72))

    optimizer = Optimizer(
        dimensions       = [Real(0.0, 1.0, name="x1"), Real(0.0, 1.0, name="x2")],
        base_estimator   = "GP",
        acq_func         = "EI",
        n_initial_points = N_INITIAL_POINTS,
        random_state     = RANDOM_SEED,
    )

    step1_hold_mains(hold)
    confirm_connections(camera=camera)

    converged = False

    for trial in range(1, N_TRIALS + 1):
        print(f"\n{_divider('─', 72)}")
        tag = f"{_CYAN}[SEED]{_RESET}" if trial <= N_INITIAL_POINTS else f"{_GREEN}[ GP ]{_RESET}"
        print(f"  Trial {_BOLD}{trial}{_RESET}/{N_TRIALS}  {tag}")

        suggestion    = optimizer.ask()
        f_a, f_b, f_c = map_to_simplex(suggestion[0], suggestion[1])
        w_a, w_b, w_c = fractions_to_widths(f_a, f_b, f_c)

        print(f"\n[Step 2] {_BOLD}Volume assignment{_RESET}")
        print(f"  Fractions  ink_a={f_a:.3f}  ink_b={f_b:.3f}  ink_c={f_c:.3f}")
        print(f"  Widths     ink_a={w_a:>2d}   ink_b={w_b:>2d}   ink_c={w_c:>2d}  (sum={w_a+w_b+w_c})")
        print(f"  Bars       A={_bar(f_a)}  B={_bar(f_b)}  C={_bar(f_c)}")

        pieces = step3_split_pieces((w_a, w_b, w_c), hold)
        step4_merge(pieces, hold)
        step5_mix(hold)
        step6_move_to_camera(hold)
        result  = step7_read_color(camera)
        de      = step8_evaluate(result, target)
        is_best = de < best_de
        if is_best:
            best_de    = de
            best_fracs = (f_a, f_b, f_c)

        optimizer.tell(suggestion, de)
        history.append(TrialRecord(
            trial=trial,
            frac_a=f_a, frac_b=f_b, frac_c=f_c,
            width_a=w_a, width_b=w_b, width_c=w_c,
            result_hex=result.hex, target_hex=target.hex,
            delta_e=de, is_best=is_best,
        ))

        best_marker = f"  {_GREEN}{_BOLD}★ NEW BEST{_RESET}" if is_best else ""
        print(f"\n  Best so far: ΔE₀₀ = {_color_de(best_de)}{best_marker}")

        if de < CONVERGENCE_DELTA_E:
            print(f"\n  {_GREEN}{_BOLD}✔  ΔE₀₀ = {de:.2f} — visually identical!{_RESET}")
            if _confirm(f"ΔE₀₀ = {de:.2f} < {CONVERGENCE_DELTA_E}. Stop now?"):
                converged = True
                step9_extract(hold)
                break
            print(f"  {_CYAN}Continuing...{_RESET}")

        step9_extract(hold)
        if trial < N_TRIALS:
            step10_reload(hold)

    hold.stop()

    # Final summary
    bw = fractions_to_widths(*best_fracs)
    print(f"\n{_header('EXPERIMENT COMPLETE', 72)}")
    print(f"\n  Best ΔE₀₀   : {_color_de(best_de)}"
          + (f"  ← {_GREEN}visually identical ✔{_RESET}" if best_de < 2.0 else ""))
    print(f"  Best fracs  : ink_a={best_fracs[0]:.3f}  ink_b={best_fracs[1]:.3f}  ink_c={best_fracs[2]:.3f}")
    print(f"  Best widths : ink_a={bw[0]}  ink_b={bw[1]}  ink_c={bw[2]}  (sum={sum(bw)})")
    print(f"  Target      : {_swatch(target.hex)}")
    print(f"  Converged   : {converged}  |  Trials run: {len(history)}/{N_TRIALS}")

    if history:
        print(f"\n  {_BOLD}ΔE₀₀ progression{_RESET}")
        print(f"  {'Trial':>5}  {'ΔE₀₀':>7}  {'Best':>7}  Trend")
        print(f"  {_divider('-', 50)}")
        running_best = float("inf")
        max_de_val   = max(r.delta_e for r in history) or 1.0
        for rec in history:
            running_best = min(running_best, rec.delta_e)
            bar_len      = round(rec.delta_e / max_de_val * 25)
            trend        = f"{_RED}{'▓' * bar_len}{_RESET}"
            marker       = f" {_GREEN}★{_RESET}" if rec.is_best else ""
            print(f"  {rec.trial:>5d}  {_color_de(rec.delta_e)}  {_color_de(running_best)}  {trend}{marker}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"experiment_{ts}.json"
    with path.open("w") as fh:
        json.dump({
            "target":       str(target),
            "best_delta_e": best_de,
            "best_fracs":   {"ink_a": best_fracs[0], "ink_b": best_fracs[1], "ink_c": best_fracs[2]},
            "best_widths":  {"ink_a": bw[0], "ink_b": bw[1], "ink_c": bw[2]},
            "converged":    converged,
            "n_trials_run": len(history),
            "config": {
                "N_TRIALS":            N_TRIALS,
                "N_INITIAL_POINTS":    N_INITIAL_POINTS,
                "CONVERGENCE_DELTA_E": CONVERGENCE_DELTA_E,
                "MERGED_TOTAL_W":      MERGED_TOTAL_W,
                "MEETING_ROW":         MEETING_ROW,
                "MEETING_COL":         MEETING_COL,
                "CAM_ROW":             CAM_ROW,
                "CAM_COL":             CAM_COL,
            },
            "history": [asdict(r) for r in history],
        }, fh, indent=2)
    print(f"\n  Log saved : {path}")
    print(_divider("═", 72) + "\n")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()

    print()
    print(_header("Target Color Setup", 72))
    raw = input(f"\n  {_CYAN}?  Enter hex color (e.g. #FF8040) or Enter for random: {_RESET}").strip()

    if raw:
        try:
            target = hex_to_color(raw)
        except ValueError as exc:
            print(f"  {_RED}Invalid hex: {exc}  Using random.{_RESET}")
            target = random_target_color(seed=None)
    else:
        target = random_target_color(seed=None)

    print(f"  Target : {_swatch(target.hex)}  rgb=({target.r},{target.g},{target.b})")
    if not _confirm(f"Use {target.hex} as the target color?"):
        raise SystemExit(0)

    _pause("Press Enter to open camera...")
    cap = cv2.VideoCapture(CAMERA_ADDRESS, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    if not cap.isOpened():
        raise SystemExit(f"Camera index {CAMERA_ADDRESS} failed to open.")
    print(f"  {_GREEN}Camera opened.{_RESET}")
    for _ in range(15):
        cap.read()

    try:
        run_experiment(target, camera=cap)
    finally:
        cap.release()
        shutdown()