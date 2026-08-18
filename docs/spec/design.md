# Acxchip — design

**Status:** partially realised. **Amended 2026-08-12; inventory refreshed 2026-08-18.**
**Scope:** a layered Python API over the ACX/Sigenex AM-DMF instrument, replacing the
flat script collection in `project/` with a tested library.
**Evidence base:** `workspace/analysis.md` §1–§29 (static analysis of the vendor bundle).

> **⚠ What has actually been built, and where this document is now aspirational.**
>
> This was written before any code existed and describes a full five-subsystem layered API
> (`l0_transport` / `l1_primitives` / `l2_subsystems` / `l3` / `l4`). What exists is two flat
> packages, not five layers:
>
> | Package | Lines | Tests | Corresponds to |
> |---|---|---|---|
> | `chiphealth/` + `rescore.py` | 5,157 | 379 | Priority 1 |
> | `microdrop/` | 2,747 | 141 | Priority 2 (partly — see `objectives.md` §2) |
>
> Both are deliberately flatter than the layering below; see `p1_chip_health_design.md` §11 for
> why, and note that the reconciliation described there now has no scheduled trigger.
>
> `microdrop/` is not described anywhere in this document — it was built after it. The layered API
> did not merely go unrealised; a second package has now been added outside it, which is worth
> knowing before treating the structure below as the plan of record.
>
> Where this document and `objectives.md` disagree, **`objectives.md` wins** — that is the rule
> stated in its header, and the §5 deltas listed there have now been applied here.
>
> **The axis is deferred.** `objectives.md` Appendix A, 2026-08-12. Everything below describing
> axis bindings, `l1_primitives/axis.py`, or the axis as a first deliverable is **superseded**.
> The analysis it rests on is unaffected and still accurate.
>
> **Camera bindings are cancelled by policy.** §5.3's `MvCameraControl.dll` binding and §6's
> `CameraPair` are excluded by the camera policy in `objectives.md` §0.2 — the researcher's own
> camera via `cv2.VideoCapture` is the measurement path. Descriptions of the vendor camera stack
> below are retained as *analysis*, not as plan.

---

## 1. The constraint that drives everything: binding reality

The instrument exposes **five subsystems across three completely different ABI styles.**
This is the single most important input to the design, and it does not match the
"one vendor SDK" assumption the current scripts were written under.

| Subsystem | Vendor DLL | Export style | Callable from Python today? | Work needed |
|---|---|---|---|---|
| **Chip** (basic) | `DLLTest.dll` | 7 flat C exports | ✅ **Yes — already used by `project/`** | none; wrap existing calls |
| **Axis** | `MCDLL_NET.dll` | **232 flat C exports, 0 mangled** (verified) | ✅ **Yes — directly `ctypes`-able** | **new bindings**, no shim |
| **Camera** | `MvCameraControl.dll` | **~187 flat C `MV_CC_*`** (+11 C++ `CTlFactory`) | ✅ **Yes — directly `ctypes`-able** | **new bindings**, no shim |
| **Chip** (full) | `MicrofluidicsInterFace.dll` | 57 MSVC-mangled C++ | ❌ No | **C++ shim required** |
| **Temperature** | `TempControlInterFace.dll` | 30 MSVC-mangled C++ | ❌ No | **C++ shim required** |
| **Light** | `LightSystemInterFace.dll` | 15 MSVC-mangled C++ | ❌ No | **C++ shim required** |
| **Magnet** | `MagnetInterFace.dll` | 20 MSVC-mangled C++ | ❌ No | **C++ shim required** |
| Transport base | `InterFace.dll` | mangled C++ (`InterFaceTemplate`, `InterFace`) | ❌ No | consumed *by* the shim |
| Path planning | `PathAlgorithm.dll` / `MultiAgentPathPlanning.dll` | C++/CLI thunks / pure .NET | ❌ No | CLR host or shim — **deferred to L4** |
| Camera (vendor wrapper) | `camHalcon.dll` | mangled C++ `CMvCamera` | ❌ No | **skip it** — bind `MvCameraControl.dll` directly instead |

### Two findings that make this much cheaper than expected

1. **The axis is directly reachable.** `MCDLL_NET.dll` has 232 undecorated flat-C exports
   (`MCF_Open_Net`, `MCF_Get_Position_Net`, …). No shim needed — this is the same binding
   style the existing scripts already use for `DLLTest.dll`. See analysis §15.
2. **The camera is directly reachable.** `MvCameraControl.dll` is the Hikrobot MVS SDK with a
   flat C `MV_CC_*` API. `camHalcon.dll` is only ACX's thin C++ wrapper over it (§25) and can be
   bypassed entirely. Hikrobot also publish an official Python binding (`MvImport`) we can
   model our struct definitions on.

### The expensive part, isolated

Only the **four `*InterFace` subsystems** need new native work, and they all need the *same*
thing, so it is one shim, not four:

- MSVC-mangled C++ with vtables → not `ctypes`-addressable
- Parameters are `QString` / `QByteArray` / `QVector<QVector<int>>` / `QRect` → non-trivial ABI
- Replies arrive as **Qt signals**, requiring a running `QCoreApplication` event loop
- All four derive from `InterFaceTemplate` in `InterFace.dll`, whose transport is a `QThread`

**Proposal:** one small C++/Qt shim DLL, `acxshim.dll`, exporting flat `extern "C"` functions
and translating Qt signals into a polled, thread-safe reply queue. Built once, bound by `ctypes`
like everything else. Design detail in §6; this is **ADR-0002**.

---

## 2. What we gain over the current Python layer

From analysis §16, the capability gap between `DLLTest.dll` (what `project/` uses) and
`MicrofluidicsInterFace.dll` (what the vendor app uses):

| Capability | `DLLTest.dll` | `MicrofluidicsInterFace.dll` |
|---|---|---|
| Actuate electrodes | `ActivateElec` | 4 variants incl. `QVector<QRect>` |
| Voltage set/read | ✅ | ✅ + typed `Recv` signals |
| Power on/off | ✅ | ✅ + `RecvPowerState` |
| Frame / timing control | ❌ | `SendSetFrame`, `SendSetTime`, `SendSetSelectFrame`, `SendOpenFrame` |
| Polarity | ❌ | `SendSetPolarity`, `SendReadPolarity` |
| Frequency control | ❌ | `SendSetVerFreq`, `Send{Open,Close}DownFreq` |
| Chip type | ❌ | `SendSetChipType` |
| IC / model / version query | ❌ | `SendReadICState`, `SendReadModel`, `SendReadVersion` |
| Device reset | ❌ | `SendResetting` |
| **Status polling** | ❌ | `SendPolling`, `SendPollingV2`, `RecvPolling` |
| Error signalling | return codes | `RecvCRCError` + per-op typed signals |

**The load-bearing one is status polling.** The Python layer currently cannot ask the chip how
it is doing — only tell it what to do. Analysis §1 found the closed loop is gated on human
`input()` calls at nearly every step; §16 notes that may be *because* no programmatic health
check exists. Closing that gap is the strongest argument for the shim, and it is what makes
genuine autonomy possible rather than cosmetic.

---

## 3. Layer architecture

```
L4  experiment/     autonomous campaign: optimiser ↔ chip ↔ camera ↔ cleanup
      ↑             (replaces masterscript3.py)
L3  choreography/   multi-step, multi-subsystem sequences
      ↑             split · stretch · merge · mix · graveyard · .Acx read/write
L2  subsystems/     one coherent subsystem, stateful, safe
      ↑             chip · axis · camera · temp · light · magnet
L1  primitives/     one operation = one vendor call, stateless, typed
      ↑
L0  transport/      DLL loading, struct/ABI defs, backend selection, logging
```

Rules:
- A layer may only call the layer directly beneath it.
- **L0–L1 contain no policy** — no retries, no waits, no interpretation. They marshal and return.
- Every layer is independently testable, with L0 providing a fake backend so L1+ can be
  tested with no hardware present (§7).

### On the "single node" framing

The requested Layer-1 shape ("check one electrode") maps cleanly onto axis and camera, but
**not onto the chip** — and that is a hardware fact, not a design choice:

- **Axis** — `MCF_Get_Position_Net(card, axis, &pos)` is genuinely per-axis. ✅
- **Camera** — `MV_CC_GetOneFrameTimeout(handle, buf, len, &info, ms)` is genuinely per-frame. ✅
- **Chip** — there is **no per-electrode call**. `ActivateElec(rows, cols, count, Drop*)` sets the
  *entire electrode frame* in one shot, and there is no read-back of individual electrode state
  at all. Analysis §2 confirmed the ABI by disassembly.

So the chip's L1 primitive is *"send one frame"* + *"describe one drop"*, and per-electrode
reasoning lives in a **client-side model** at L2 (`ChipState`), which tracks what we believe is
energised and diffs frames before sending. Any function that appears to "check an electrode" is
answering from that model, never from the device — and its docstring must say so.

---

## 4. Module structure

Implementation lands in `project/` (existing git repo, currently on `main` — implementation
should branch first). New package alongside the legacy scripts; **nothing existing is deleted
in the first pass**, so the working rig keeps running.

```
project/
  acxchip/
    __init__.py
    config.py              # paths, chip geometry, serials — replaces hardcoded literals
    errors.py              # AcxError hierarchy
    l0_transport/
      loader.py            # find + load DLLs, ABI sanity checks
      backend.py           # Backend protocol; RealBackend / FakeBackend
      structs.py           # Drop, MV_CC_* structs, MCF profiles
      logging.py           # structured logging (replaces bare print())
    l1_primitives/
      chip_dll.py          # DLLTest.dll        (7 calls)   — EXISTING binding
      chip_iface.py        # MicrofluidicsInterFace via shim — NEW (shim)
      axis.py              # MCDLL_NET.dll                  — NEW (direct ctypes)
      camera.py            # MvCameraControl.dll            — NEW (direct ctypes)
      temp.py              # TempControlInterFace via shim   — NEW (shim)
      light.py             # LightSystemInterFace via shim   — NEW (shim)
      magnet.py            # MagnetInterFace via shim        — NEW (shim)
    l2_subsystems/
      chip.py  axis.py  camera.py  temp.py  light.py  magnet.py
    l3_choreography/
      droplet_ops.py       # split / stretch / merge / mix
      acx_format.py        # .Acx reader + writer
      cleanup.py           # graveyard handling
    l4_experiment/
      campaign.py  optimiser.py  metrics.py
  tests/
    unit/  contract/  hardware/
  native/
    acxshim/               # C++/Qt shim source + CMakeLists
```

---

## 5. Layer 1 — primitive signatures

Stateless. One vendor call each. Return values, never raise on device-level failure
(they return status); raise only on programmer error.

### 5.1 `l1_primitives/chip_dll.py` — `DLLTest.dll` ✅ *existing binding style*

```python
class Drop(ctypes.Structure):                 # verified layout, analysis §2
    _fields_ = [("height", c_int), ("width", c_int),
                ("row", c_int), ("col", c_int)]

def init_usb()  -> int
def open_usb()  -> int
def close_usb() -> int
def set_power(on: bool) -> int
def set_volt(v: Sequence[int]) -> int                    # exactly 9 ints
def inquire_volt() -> tuple[int, list[int]]              # 9 out-params, §2 ABI
def activate_elec(rows: int, cols: int, drops: Sequence[Drop]) -> int
```

> **ABI hazard — carry forward from §2.** `DLLTest.dll`'s real signatures diverge from the
> official PDF and from `Microfluidics.dll`. A vendor update reverting to the documented
> signatures would be a **stack-corrupting crash, not a Python exception**. `loader.py` must run
> a startup sanity check (`inquire_volt` arity probe) and refuse to proceed on mismatch. → **ADR-0003**

### 5.2 `l1_primitives/axis.py` — `MCDLL_NET.dll` 🆕 *direct ctypes, no shim*

Binding the 13 exports the vendor app uses (§8); the other 219 are out of scope.

```python
def open_net(card: int) -> int                   # MCF_Open_Net
def close_net(card: int) -> int                  # MCF_Close_Net
def get_axis_state(card: int, axis: int) -> tuple[int, int]
def get_position(card: int, axis: int) -> tuple[int, float]
def get_vel(card: int, axis: int) -> tuple[int, float]
def get_input(card: int) -> tuple[int, int]
def set_axis_profile(card: int, axis: int, p: AxisProfile) -> int
def uniaxial(card: int, axis: int, dist: float, max_v: float, max_a: float) -> int
def axis_stop(card: int, axis: int) -> int
def search_home_set/start/stop(card: int, axis: int, ...) -> int
def set_elp_trigger(card: int, axis: int, val: int) -> int
```

> **Environmental dependency.** The axis transport is **raw Ethernet via WinPcap**
> (`MCDLL_NET.dll` → `wpcap.dll` → `Packet.dll`, §15.1). The NPF driver must be installed and
> running, and the process needs the rights to open an adapter. `open_net` failing for
> environmental reasons is expected and must be distinguishable from a device fault. → **ADR-0004**

### 5.3 `l1_primitives/camera.py` — `MvCameraControl.dll` 🆕 *direct ctypes, no shim*

```python
def enum_devices(layer_type: int) -> tuple[int, list[DeviceInfo]]
def create_handle(dev: DeviceInfo) -> tuple[int, Handle]
def destroy_handle(h: Handle) -> int
def open_device(h: Handle) -> int
def close_device(h: Handle) -> int
def start_grabbing(h: Handle) -> int
def stop_grabbing(h: Handle) -> int
def get_one_frame_timeout(h: Handle, buf, size: int, ms: int) -> tuple[int, FrameInfo]
def get_int_value/get_float_value/get_enum_value(h, node: str) -> tuple[int, Any]
def set_float_value(h, node: str, v: float) -> int       # ExposureTime, Gain
def set_int_value(h, node: str, v: int) -> int           # ROI Width/Height/OffsetX/OffsetY
def convert_pixel_type(h, params) -> int
```

Node names come from GenICam, so exposure/gain/ROI are string-addressed rather than fixed
calls — matching what `Cammgr::CamMgrHalcon` does at runtime (§ camera log analysis:
`SetGain`, `SetExposureTime`, `SetROI*`, `GetROIWidthMax`, `GetPayloadSize`).

### 5.4 `l1_primitives/{chip_iface,temp,light,magnet}.py` — via `acxshim.dll` 🆕 *shim*

Uniform pattern, mirroring `InterFaceTemplate` (§20). Every call is fire-and-forget; replies are
polled from the shim's queue.

```python
# chip_iface.py  — MicrofluidicsInterFace.dll (57 exports; binding ~25)
def init(port: str) -> int
def send_electrify(rows: int, cols: int, frame: Sequence[Sequence[int]]) -> int
def send_electrify_rects(rows: int, cols: int, rects: Sequence[Rect], flag: int) -> int
def send_set_voltage(v: Sequence[int]) -> int
def send_read_voltage() -> int
def send_power_on() / send_power_off() -> int
def send_set_polarity(on: bool) -> int
def send_set_frame(a: int, b: int) / send_set_time(a: int, b: int) -> int
def send_set_chip_type(a: int, b: int, c: int) -> int
def send_read_ic_state() / send_polling() -> int
def poll_reply(timeout_ms: int) -> Reply | None          # drains the shim queue

# temp.py — TempControlInterFace.dll
def init(port: str) -> int                               # "COM3" per Config.ini
def send_set_temp(celsius: float, channel: int) -> int
def send_set_pid(a: int, b: int, c: int, d: int) -> int  # ⚠ arg order UNVERIFIED, §10
def send_read_current_temp(channel: int) -> int
def send_port_enable(enable: bool, channel: int) -> int
def poll_reply(timeout_ms: int) -> Reply | None

# light.py  — SendLightEnable(int,int,int,int)
# magnet.py — SendMagnetMove(int,int,int), SendReadMagnetPos/State, SendMagnetReturn(set<int>)
```

> ⚠ **`send_set_pid` argument order is not established** (§10) — four ints, three format slots
> in `SetPid%1 %2 %3`. It must ship raising `NotImplementedError` until confirmed by
> disassembly or vendor docs. Do not guess: wrong PID routing on a 4-zone heater is a
> physical-safety issue, not just a bug.

---

## 6. Layer 2 — subsystem signatures

Stateful, safe, policy-bearing. Context managers own connection lifetime — fixing the
inconsistent open/close discipline flagged in §1.

```python
class Chip:                       # wraps chip_dll (L1) and later chip_iface
    def __enter__/__exit__
    @property state -> ChipState                    # client-side model, §3
    def power(self, on: bool) -> None
    def set_voltages(self, v: Sequence[int]) -> None
    def read_voltages(self) -> list[int]
    def apply_frame(self, drops: Sequence[Drop]) -> None   # diffs vs state, then sends
    def hold(self, drops, duration_s: float) -> None
    def reset(self) -> None                                # iface only
    def health(self) -> ChipHealth                         # iface only — the §2 gap

class Axis:
    def __enter__/__exit__                                 # open_net / close_net + retry
    def home(self, axes=("x","y","z")) -> None
    def move_to(self, x=None, y=None, z=None, *, wait=True) -> None
    def position(self) -> Position
    def goto_preset(self, name: str) -> None               # Config.ini presets, §3
    def stop(self) -> None

class Camera:                      # one instance per physical camera
    @classmethod def discover(cls) -> list[CameraInfo]
    @classmethod def open_by_serial(cls, serial: str) -> "Camera"
    def __enter__/__exit__
    def configure(self, *, exposure_us=None, gain=None, roi=None) -> None
    def grab(self, timeout_ms: int = 1000) -> Frame        # → numpy array
    def stream(self, n: int | None = None) -> Iterator[Frame]

class CameraPair:                  # HighSerial / LowSerial, §3 + camera log analysis
    def grab_both(self, timeout_ms=1000) -> tuple[Frame, Frame]

class TempController:
    def set_setpoint(self, channel: int, celsius: float) -> None
    def read_temperature(self, channel: int) -> float
    def enable(self, channel: int, on: bool) -> None
    def wait_until_stable(self, channel, target, tol=0.5, timeout_s=300) -> bool

class Light:   def enable(self, a, b, c, d) -> None
class Magnet:  def move(self, x, y, z) -> None ; def position(self) -> Position
```

### The shim, concretely

`native/acxshim/` — C++/Qt, links the four `*InterFace.dll` + `InterFace.dll` + Qt5Core.

- Owns a `QCoreApplication` on a dedicated thread (required: replies are Qt signals).
- Exports flat `extern "C"`: `acx_<subsys>_init`, one wrapper per `Send*`, plus a single
  `acx_<subsys>_poll(reply*, timeout_ms)`.
- Connects every `Recv*`/`signal_Recv*` to a slot that pushes a tagged POD struct onto a
  mutex-guarded queue. Python polls; no callbacks cross the FFI boundary.
- Built with MSVC matching the vendor toolchain (Qt 5.14.2 / VC14 — §18).

**Delivery note:** the shim requires a Windows box with MSVC + Qt 5.14.2 headers. If that is not
available, L1/L2 for chip-full, temp, light and magnet stay stubbed behind the fake backend, and
we ship **axis + camera + chip-basic** — which is already a large improvement and needs no
native build at all. **This is the main scheduling risk and needs your input (§9).**

---

## 7. Testing strategy

**No hardware, and no Windows, on this development machine.** The DLLs are Windows x64 PE
binaries; this workspace is Linux/WSL. Nothing can be loaded here. That is not a blocker if we
design for it from the start — and designing for it produces a better library anyway. → **ADR-0001**

Three test tiers:

| Tier | Runs where | Against | Gate |
|---|---|---|---|
| `tests/unit/` | anywhere, CI | `FakeBackend` | every commit |
| `tests/contract/` | anywhere, CI | recorded ABI fixtures | every commit |
| `tests/hardware/` | Windows instrument PC only | real DLLs + rig | manual, `-m hardware` |

**`FakeBackend`** implements the same `Backend` protocol as `RealBackend`, simulating a
128×128 electrode grid, three axes with position/limits, two synthetic cameras emitting
generated frames, and four temperature zones with first-order thermal response. It is the
default backend, so `import acxchip` works on any machine and the whole L1–L4 stack is
testable without the rig.

**Contract tests** pin the ABI facts recovered in analysis so a vendor DLL swap is caught
loudly: `Drop` is 16 bytes with field order `(height,width,row,col)`; `inquire_volt` takes 9
pointers; `ActivateElec` takes 4 args. These are the §2 hazard turned into assertions.

Per-layer definition of done: unit tests green, contract tests green, docstrings state which
vendor call each function wraps, and the step's `.md` doc exists.

---

## 8. Documentation convention

Picking one, per your instruction, and using it consistently:

- **`decisions/NNNN-<slug>.md`** — ADRs, existing scaffold template (Context / Decision /
  Consequences). For *why* decisions, written when the decision is made.
- **`docs/steps/NNN-<slug>.md`** — one file per completed step, written **as part of that step**,
  never retroactively. Fixed sections: *What was built* · *Why* · *Vendor DLL mapping* (function →
  vendor export, with the analysis.md § that established it) · *What was tested* · *Known gaps*.
- `docs/spec/design.md` (this file) and `docs/spec/requirements.md` stay current as the design moves.

ADRs already identified: **0001** dev-without-hardware / fake backend · **0002** C++/Qt shim vs
alternatives · **0003** ABI sanity check at load · **0004** WinPcap dependency for the axis ·
**0005** layering rules and the client-side `ChipState` model.

---

## 9. Open questions

**Status updated 2026-08-12.** Three of the five are closed; the disposition is recorded in
`objectives.md` §5.

1. **Shim feasibility.** Do you have (or can you get) a Windows machine with MSVC + Qt 5.14.2 to
   build `acxshim.dll`?
   → **DEFERRED, and cheaper to defer than it looked.** Every active priority is designed to need
   no native build. Priority 2 (droplet size) is expected to produce evidence on whether the
   missing timing/polarity/frequency control is the real binding constraint — a better basis for
   this decision than the guess available today.
2. **Scope of first release.** Everything, or the no-native-build subset?
   → **ANSWERED** by the priority order: the no-native-build subset.
3. **Legacy scripts.** Leave `project/`'s 13 scripts untouched, or port `masterscript3.py` onto
   the new API as the L4 proof and deprecate the rest?
   → **STILL OPEN.** `objectives.md` §0.1 commits to improving on them rather than wrapping them,
   but their fate is undecided. Note the measured constraints: 9 of 13 load the DLL at import
   time, so a dispatcher that imports them crashes on any machine without it.
4. **Camera.** Keep your UVC camera, add the Hikrobot pair, or support both?
   → **ANSWERED: the researcher's own camera only.** `objectives.md` §0.2. Autofocus is now off
   and recorded per run, closing the ΔE variance risk. Revisiting this is the one route by which
   the deferred axis work could return — see `objectives.md` §2.4 q4.
5. **Safety default** — refuse hardware-mutating calls unless explicitly armed.
   → **ANSWERED: yes, and built.** Dry-run is the default; `--arm` or `ACXCHIP_ARM=1` arms a
   session. `chiphealth/actuation.py`. A voltage-confirmation gate (phase 0b) was added on top
   after the first hardware session.

## 10. First step — superseded

> **SUPERSEDED.** This section proposed **L0 + L1 axis only** as the first deliverable, on the
> reasoning that the axis needs no shim and has a known reliability problem worth diagnosing.
>
> The researcher's priority ordering put the electrode/health script first instead, and that is
> what was built (`chiphealth/`, `p1_chip_health_design.md`). As of 2026-08-12 the axis is
> **deferred entirely** (`objectives.md` Appendix A), so this section is dead rather than merely
> reordered. Its deliverable list — `l0_transport/*`, `l1_primitives/axis.py`,
> `tests/contract/test_axis_abi.py`, ADRs 0001 and 0004 — describes no planned work.
>
> Retained so the reasoning is not lost: if the axis is ever reopened, the argument for starting
> there still holds on its own terms.
