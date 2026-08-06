# Analysis log

Running narrative of what was done in each numbered analysis step (`workspace/01_...`, `02_...`, …) — the headline finding, the figures and tables it produced, the decision taken at the end. Read top-to-bottom to retrace the project's reasoning; a fresh AI session can resume from this file alone.

## Context-gathering phase (2026-08-05) — pre-plan investigation

Background research before drafting the next-phase research plan (option: better code development on the existing SULI codebase, via Claude Code). Nothing below is a decision yet — it's the evidence base the plan will be drafted from once gathering is done.

### 1. `project/` codebase (git repo, Kailey McGady's SULI project at Argonne National Laboratory)

- README describes a **completed** closed-loop autonomous color-mixing workflow: `ACX DLL Interface → Python Controller → AM-DMF Chip → Camera → OpenCV → Average RGB → Bayesian Optimization → Next Experiment`.
- 13 top-level scripts, no package structure. Key ones:
  - `camera.py` — `CameraInterface` class, OpenCV capture + HSV-based drop-color detection. Best-engineered file in the repo.
  - `csvvolcont.py` — real hardware driver via `ctypes.CDLL`; defines `Drop` struct; `initialize()`/`main()`; reads 3 reservoir widths from an Excel file; runs split→stretch→merge→6-pass-mix choreography.
  - `masterscript3.py` (docstring calls it `run_experiment.py` — stale rename) — orchestrator: Bayesian suggests widths → xlsx → `csvvolcont.main()` mixes → camera measures → Bayesian scores (DeltaE) → `cleanreload` cleans up.
  - `cleanreload.py` — post-trial cleanup; graveyard zone for spent drops; DLL singleton kept open across trials.
  - `bayesopttest1.py` — Bayesian optimizer (skopt-style `ask`/`tell`), CIEDE2000 DeltaE scoring, CSV logging.
  - `chipsetup.py`, `dropsplitoff.py`, `mdmixing.py`, `mdmixwithmerge.py`, `dropandmixtests.py`, `1pixsplit.py` — prototype/test variants of the same electrode choreography, largely superseded by `csvvolcont.py`.
- **Code-quality issues identified** (candidates for the "better code development" phase):
  1. Massive duplication of the split/stretch/merge/mix electrode choreography across `csvvolcont.py`, `mdmixing.py`, `mdmixwithmerge.py`, `dropsplitoff.py`, `1pixsplit.py`.
  2. The "autonomous, minimal human intervention" claim in the README is currently **not true of the code** — `masterscript3.py` and `csvvolcont.py` gate almost every electrode step behind an `input()`/`wait()` prompt. `cleanreload.shrink_graveyard(interactive=False)` is the one exception found so far — first non-interactive path in the repo.
  3. Hardcoded absolute Windows paths throughout (DLL path, Excel path) — not portable/configurable.
  4. Inconsistent USB connection lifecycle across files (some open/close their own connection, others assume a shared singleton).
  5. Dead code left in (`dropandmixtests.py` ~80 commented lines; `dropsplitoff.py` concatenates two prototype generations, second one now commented out in the latest pull).
  6. No tests, no shared config for repeated electrode-geometry constants, no logging framework (bare `print()`), no error handling around hardware calls.
- **Update since first read**: `cleanreload.py` was rewritten — graveyard moved from a growing bottom-right pad to a fixed 30×30 top-right pad, split into `hold_reservoirs_and_drop()` / `move_to_graveyard()` / `shrink_graveyard()`. `csvvolcont.py` refactored reservoirs into a `BASE_DROPS` list auto-prepended in `activate()`. `masterscript3.py` now wires the new cleanreload API in via `clear_drop_to_graveyard()` and exposes a `SHRINK_INTERACTIVE` flag.

### 2. DLL / hardware ABI investigation — RESOLVED

Files: `inputs/raw_data/Microfluidics.dll`, `DLLTest.dll`, `libusb-1.0.dll`, and `ACX inst SDK description v1.1.pdf`.

- **`Microfluidics.dll`**: PE32+ x64, exports a mangled C++ class `Microfluidics` (MSVC ABI), built from `D:\AcxelSoft\trunk\SDK\Microfluidics\x64\Release\Microfluidics.pdb`. Public methods match the **official PDF exactly**: `ActivateElec(int, int, const std::vector<Drop>&)`, `InquireVolt()` (0 args), `SetVolt(9 ints)`, `SetPower(bool)`, `InitUsb()`, `Close()`, `GetOpenState()`, plus async `Regist`/`UnRegist` callback registration (`void(std::vector<int>)`). Depends on `libusb-1.0.dll` directly (`libusb_bulk_transfer`, `libusb_claim_interface`, etc.) — no proprietary protocol.
- **`DLLTest.dll`**: PE32+ x64, exports **flat, undecorated C names**: `ActivateElec, CloseUSB, InitUSB, InquireVolt, OpenUSB, SetPower, SetVolt`. Does **not** import from `Microfluidics.dll` — independent reimplementation, links only against `libusb-1.0.dll` + CRT/MSVCP.
- **This is the DLL every script in `project/` actually loads** (hardcoded path `...ACX_pythonSDK...\DLLTest.dll`).
- **Disassembly-confirmed real signatures** (settles the question definitively):
  - `ActivateElec`: 4 flat args `(int row, int col, int count, Drop* array)` — confirmed by register usage (rcx/rdx/r8/r9) and a loop over the array in 16-byte (4-int) strides using `count` as the bound. **Matches** what the Python code calls: `ActivateElec(128, 128, n, arr)`.
  - `InquireVolt`: **9 output `int*` pointers**, written synchronously after parsing an 18-byte USB response (validated via a `0xAA` header byte from a `libusb_bulk_transfer` call). **Matches** the Python code's `InquireVolt(byref(v1)...byref(v9))` call — despite this contradicting both the official PDF and `Microfluidics.dll`'s zero-arg, callback-based version.
- **Verdict**: the scripts are **not buggy** — they correctly target `DLLTest.dll`'s real (undocumented) ABI, which diverges from the officially documented, `Microfluidics.dll`-matching API. ACX shipped two divergent implementations; the undocumented one is the one that ships and runs.
- **Risk flagged for the plan**: since `DLLTest.dll` isn't the documented/supported artifact, a future ACX update could silently change its ABI back toward the documented signatures, and a cdecl arg-count mismatch is a stack-corrupting crash, not a clean Python exception. Worth a startup sanity check or at least a code comment recording this finding.

### 3. `Config/` and `configs/` — reveals the full instrument, not just the chip

`Config.ini` (two versions: one under `Config/`, `ChipType=1`/`DeviceType=0`; one under `Config/Config_extracted/`, `ChipType=5`/`DeviceType=2` — likely a different chip/device variant) shows the ACX platform is a **five-subsystem instrument**, of which the Python scripts only touch one (chip electrodes) plus a partial second (camera, reimplemented independently):

- `[MicroFluics]` — chip; supports USB, serial (COM1), *and* network (`192.168.0.2:60001`) — Python only uses USB.
- `[TempControl]` — 4-zone heater (channels 101–104, e.g. `ACX2425B101`), each with its own temperature-compensation calibration curve, on COM3. Currently `TempPower=false`. No Python script touches this.
- `[Light]` — illumination module, own VID/PID/power levels. Untouched.
- `[Axis]` — full XYZ motion stage: per-axis limits/proportions, named preset positions (`RightButtom`, `LeftButtom`, `RightTop`, `FullView`) as X-Y-Z triplets. Nothing in Python moves anything.
- `[Camera]` — **two** camera serials (Hi/Lo mag). Python's `camera.py` uses a single generic `cv2.VideoCapture(index)`.
- `[Magnet]` — separate actuator, own coordinate bounds/VID/PID. Untouched.
- `Load.ini` names the real host app: **`DMatrix_App.exe`**.
- **`DLLTest.dll`'s 7 exports cover only the chip subsystem** — no known entry point yet for axis/temp/light/magnet. *(Updated: §8 identifies the app-side entry point for every one of these, and §10 analyses the temp one in detail — but none is `ctypes`-friendly the way `DLLTest.dll` is.)*
- Two `log4qt.conf` files found (one under `Config/`, referencing a path under user "Yacine Belgaid" / "Sigenex"; one under `configs/`, referencing Kailey's own local `DMCtrl3.5.2` install) — confirms the vendor's full desktop app (Qt-based, log4qt logging) has been run independently by at least two people.

### 4. `logs/` and `samp_data/` — ~128MB of real operational history, June 2024 → August 2026

- `samp_data/` (34 files): every file is an empty, header-only CSV (`时间,温度,` = "Time, Temperature", UTF-8 BOM). Temperature logging has never captured a data point across two months of files — consistent with `TempPower=false` in the current config snapshot.
- `logs/` (52 brief + 74 detail, with rotation): detail logs carry full vendor source paths confirming the real project layout — `D:\AcxelSoft\trunk\project\dmatrix-editor\dmatrix\{axis, camera\cammgr, ...}` (same `AcxelSoft\trunk\` root as the `Microfluidics.dll` PDB, different subtree).
- **Quantified event counts across all brief logs**:
  - Temperature: 146,233 `ANRP` + 60,130 `RP` poll events — temp control WAS heavily used historically (contradicts the current disabled config snapshot). PID tuning visible directly in logs: channels 101/102 both `P=20, I=18, D=15`.
  - Chip: 3,889 "Electrify Success" events, 80 "SetVolt Success".
  - **Axis (motion stage) is unreliable**: 442 init attempts, only 126 succeeded vs. 250 explicit "Failed to Open the Axis" failures — a real, months-long, unresolved reliability issue (retry-3-times-then-report pattern repeats June–September 2024 at minimum).
  - Camera: comparatively healthy (~127 successful opens). ~~Confirms the vendor's real camera stack is **Halcon** (MVTec commercial machine-vision SDK, class `Cammgr::CamMgrHalcon`)~~ — **superseded by §11**: the class name is `Cammgr::CamMgrHalcon`, but the SDK actually linked is **Hikrobot MVS/GenICam**, not MVTec Halcon. The Python `camera.py` is an independent, from-scratch reimplementation using generic OpenCV/UVC capture, not an approximation of the vendor SDK. Real camera serials seen in logs (`DA3931850`, `DA3931886`) differ from `Config.ini`'s (`DA5746132`/`DA5746141`) — multiple physical units used across sessions/machines over time.

### 5. `PathAlgorithm.dll` — custom droplet path planner, RESOLVED

File: `inputs/raw_data/PathAlgorithm.dll`. Requested as priority investigation (likely custom ACX/Sigenex logic).

- PE32+ x64, but also carries a **CLR header** (`_CorDllMain` import from `mscoree.dll`) — this is a **mixed-mode C++/CLI** DLL, not a plain native DLL. Built 2024-06-12.
- **Exports** (undecorated via MSVC demangling): a `PathAlgorithm` class with ctor/dtor/`operator=` and one real method, `AutoPath_Move(QVector<Path>&, QVector<Drop>, QVector<Drop>, QVector<bool>, QVector<bool>, int, int, int, int)`.
- The four native export RVAs are **`.nep` thunks** (`jmp` into a JIT-check/CLR-dispatch stub) — i.e. the real method body is managed IL, invisible to a native disassembler (`objdump -d` only shows the thunk, not logic). Pulled the actual .NET metadata with `dnfile`/`dncil` (installed into an isolated venv under the scratchpad, not system-wide) to read the IL directly.
- **IL disassembly of `AutoPath_Move` (413 instructions) confirms**: it loops per-droplet, builds `Drop` structs from the two input `QVector<Drop>` arguments (current position / goal position, via a `State` type with `set_X`/`set_Y`), and for each droplet calls out to **`SIPPMapf.Planning_export`** — i.e. **Safe Interval Path Planning (SIPP)** for **Multi-Agent Path Finding (MAPF)**, a standard robotics/AGV collision-avoidance pathfinding algorithm. Each drop's result is checked (success flag) and, if valid, appended into the output `QVector<Path>`.
- **So**: the "MultiAgentPathPlanning" namespace name is literal — this DLL is ACX/Sigenex's droplet router, planning per-droplet electrode-grid paths that avoid collisions between simultaneously-moving drops, on top of a textbook SIPP-MAPF solver (solver itself not further decompiled — out of scope once identified).
- **Not wired into the Python codebase**: `PathAlgorithm.dll` is not referenced anywhere in `project/` (confirmed by grep). It imports only `Qt5Core.dll` + CRT/VCRUNTIME + `mscoree.dll` — no dependency on `DLLTest.dll`/`Microfluidics.dll`/libusb, so it's a pure planning library, not a hardware driver. It's presumably called from the vendor's `DMatrix_App.exe` UI, not from the SULI Python control layer, which instead does its own ad-hoc split/stretch/merge choreography (see §1) with no formal collision-avoidance planning.
- **Practical implication for the plan**: if multi-drop collision avoidance becomes a real need, there's a vendor-native SIPP-MAPF implementation already present in the instrument's own binaries (as a C++/CLI DLL) rather than something that would need to be built from scratch — though calling it from Python would require either a flat-C wrapper (its current export surface is name-mangled C++/CLR, not directly `ctypes`-callable like `DLLTest.dll`) or shelling out to a .NET host.

### 6. Networking DLLs (`Packet.dll`, `Qt5Network.dll`) vs. `Config.ini`'s network mode

Requested to check whether these explain `[MicroFluics]`'s `192.168.0.2:60001` network option found in §3 (i.e., can the app talk to the chip over network instead of USB).

- **`Packet.dll`**: genuine, unmodified **WinPcap 4.1.3** packet-capture driver interface (PDB path confirms: `c:\releases\winpcap_4_1_3\winpcap\packetNtx\...\Packet.pdb`; version string `WinPcap Packet Driver (NPF)`). Standard 32-export surface (`PacketOpenAdapter`, `PacketSendPacket`, `PacketGetAdapterNames`, …). Imports only `WS2_32.dll`/`iphlpapi.dll`/`ADVAPI32.dll`/`VERSION.dll` — no Qt, no link to any other DLL in this set.
- **`Qt5Network.dll`**: stock Qt 5.14.2 networking module (`QTcpSocket`/`QSslSocket`/DNS/crypto stack — `DNSAPI.dll`, `CRYPT32.dll`, `WS2_32.dll` imports). Also stock/unmodified.
- **Neither is imported by any other DLL in `raw_data/`** — not by `DLLTest.dll`, `Microfluidics.dll`, `PathAlgorithm.dll`, or the Qt modules except `Qt5Multimedia.dll` (imports `Qt5Network.dll`, see §7 — that's Qt's internal plumbing, not app networking).
- **Re-confirmed `DLLTest.dll` and `Microfluidics.dll` import lists**: both link only `libusb-1.0.dll` + CRT/MSVCP — **zero networking imports**. The chip-control path the Python code (and, as far as we can tell, the whole instrument's chip driver layer) actually uses is USB-only; there is no code in any DLL we have that implements the `192.168.0.2:60001` network mode.
- ~~there is no code in any DLL we have that implements the `192.168.0.2:60001` network mode~~ — **superseded by §20.1**: `InterFace.dll` (supplied later) implements TCP client *and* TCP server transports over `Qt5Network`, underneath every subsystem interface. The network chip mode is a real, supported code path. The "never actually used" half of the conclusion below still stands.
- **No operational evidence of network chip control**: grepped all of `logs/`, `configs/`, `samp_data/` for socket/TCP/pcap/`60001`/`192.168.0.2` — the only hits are the `Config.ini` files themselves (the static config field), nothing in the ~128MB of operational logs analyzed in §4 shows a live network session ever being opened.
- *(Revisited in §12 with `DMatrix_App.exe` in hand — conclusions below hold; `Packet.dll`'s presence is now fully explained, and a previously unseen outbound WebSocket endpoint was found.)*
- **Working conclusion**: `Packet.dll` (WinPcap/NPF) is almost certainly bundled for low-level network diagnostics/adapter enumeration in the vendor's full `DMatrix_App.exe` (raw frame capture is the wrong tool for application-level chip control anyway — that would go through ordinary sockets, i.e. `Qt5Network.dll`/`WS2_32`, not packet capture). `Qt5Network.dll` is the *plausible* candidate if the network chip-control mode is ever actually exercised, but nothing in the artifacts gathered so far shows it being used for that — the `192.168.0.2:60001` config field looks unused/vestigial in this snapshot of the instrument.

### 7. Standard third-party/framework libraries — presence survey

- `opencv_world460.dll` — OpenCV **4.6.0**, single monolithic build. Consistent with the vendor UI likely also having an OpenCV-based feature somewhere, separate from Python's own bundled OpenCV usage in `camera.py`.
- `opengl32sw.dll` — standard Qt/Mesa software OpenGL rasterizer fallback (ships alongside Qt5Gui/Qt5OpenGL apps that need a software rendering path when no GPU driver is available).
- `Qt5Charts.dll`, `Qt5Core.dll` (5.14.2), `Qt5OpenGL.dll` — stock Qt modules, no anomalies. `Qt5Charts` implies the vendor UI has live plotting (likely for the temperature/voltage telemetry seen in §4's log volume).
- `Qt5Multimedia.dll` / `Qt5MultimediaWidgets.dll` — confirmed via exported class names (`QCamera`, `QCameraViewfinder`, `QVideoWidget`, `QCameraImageCapture`, etc.) to be the **generic Qt Multimedia framework**, i.e. a **UI video preview/recording feature** (viewfinder widget, capture-to-file). This is a **separate code path from the actual scientific camera pipeline**, which §4 established is the vendor-side commercial SDK (`Cammgr::CamMgrHalcon` — **re-identified in §11 as Hikrobot MVS, not MVTec Halcon**), not Qt Multimedia and not Python's OpenCV reimplementation. So the instrument has (at least) three independent camera-adjacent stacks: the vendor measurement SDK (Hikrobot MVS), Qt Multimedia (UI preview/recording), and Python/OpenCV (the SULI control script's own from-scratch reimplementation).
- **Gap noted**: `Qt5Multimedia.dll` imports `Qt5Gui.dll`, which is **not present** in `inputs/raw_data/` (only `Qt5Core`, `Qt5Charts`, `Qt5Network`, `Qt5OpenGL`, `Qt5Multimedia`, `Qt5MultimediaWidgets` were added) — this set of DLLs alone wouldn't be sufficient to actually run the vendor app; flagging in case it's worth pulling in `Qt5Gui.dll` too for completeness, though not blocking for what's been learned so far.

### 8. `DMatrix_App.exe` — the host application's import graph settles the instrument architecture

File: `inputs/raw_data/DMatrix_App.exe` (PE32+ x64 GUI, 8.7 MB, 7 sections, no CLR header, no delay-load imports — so the static import table below is the complete set of load-time dependencies).

**Full non-system import list** (`objdump -p`): `InterFace.dll`, `MicrofluidicsInterFace.dll`, `TempControlInterFace.dll`, `LightSystemInterFace.dll`, `MagnetInterFace.dll`, `MCDLL_NET.dll`, `PathAlgorithm.dll`, `camHalcon.dll`, `log4qt.dll`, `opencv_world460.dll`, `Qt5Core/Gui/Widgets/Charts/Network/WebSockets/SerialPort`, plus MSVC CRT.

This resolves several things that were previously inference:

- **The five subsystems from §3 are five real DLLs, and they share one base class.** Four of them (`Microfluidics`, `TempControl`, `LightSystem`, `Magnet`) are `*InterFace.dll` modules that all derive from **`InterFaceTemplate`** in `InterFace.dll` — the app imports `InterFaceTemplate::InitInterFace` (three overloads: `(QString)`, `(QString,int)`, `(int,int,QString)`), `Close()`, `RecordLog(QString,QString,RecordType)` and `InterFaceStateChange(InterFaceState,QString)`. So the vendor has a uniform transport/logging/state-machine abstraction, and each subsystem is a Qt `QObject` subclass of it with `AnalysisData(QByteArray)` as the reply parser. `RecordLog` is the source of the log lines counted in §4.
- **`PathAlgorithm.dll` IS actively used** — §5 said "presumably called from `DMatrix_App.exe`"; that is now confirmed, the exe imports `PathAlgorithm::AutoPath_Move` plus ctor/dtor. The SIPP-MAPF router is live vendor functionality, not dead code.
- **The chip DLL the app uses is a third one.** The app imports **`MicrofluidicsInterFace.dll`**, *not* `Microfluidics.dll` and *not* `DLLTest.dll`. Its 29 imported symbols are a much richer API than either: `SendElectrify(int,int,QVector<QVector<int>>)`, `SendElectrifyPro(...)`, `SendSetVoltage(int*)`, `SendReadVoltage()`, `SendPowerOn/Off()`, `SendSetPolarity(bool)`, `SendSetChipType(int,int,int)`, `SendSetFrame(int,int)`, `SendSetSelectFrame(int)`, `SendOpenFrame(bool)`, `SendSetTime(int,int)`, `SendSetVerFreq(bool)`, `SendReadICState()`, and matching `Recv*` signals. Note the electrode payload is a `QVector<QVector<int>>`, **not** the flat `Drop[]` array that `DLLTest.dll`/`Microfluidics.dll` take.
  - **So there are three parallel chip-control implementations**: the documented SDK (`Microfluidics.dll`), the shipped Python-facing flat-C one (`DLLTest.dll`, what §2 established the SULI scripts use), and the app's own (`MicrofluidicsInterFace.dll`). Features visible only in the app's version — frame/timing control, polarity, chip-type selection, IC state — have **no equivalent in the 7 exports `DLLTest.dll` gives Python**. That is a concrete capability ceiling on the Python control layer, and it is new information relative to §2/§3.
- **The axis is the odd one out** — see §13.
- **`Qt5Gui.dll` gap from §7 confirmed as real**: the exe imports it directly, and it is still absent from `raw_data/`.

**Present-vs-missing status of the app's own dependencies** (matters for any further static analysis):

| Imported by app | In `raw_data/`? |
|---|---|
| `TempControlInterFace.dll`, `PathAlgorithm.dll`, `opencv_world460.dll`, `Qt5Core/Widgets/Charts/Network/WebSockets/SerialPort` | present |
| `InterFace.dll`, `MicrofluidicsInterFace.dll`, `LightSystemInterFace.dll`, `MagnetInterFace.dll`, `MCDLL_NET.dll`, `camHalcon.dll`, `log4qt.dll`, `Qt5Gui.dll` | **missing** |

Also newly present and now explained: `Utils.dll` is ACX's own small helper library (namespace `Acx::Utils`) with `QExePath` (`GetConfigPath`, `GetLogPath`, `GetCurrentExePath`, `CreatePath`, `CopyDirectoryFiles`) and `QBLog4Helper` (`writelogToLocal(LogType, QString)`) — it wraps `log4qt.dll` and is the mechanism behind the `Config/` and `Logs/` layout seen in §3/§4. `wpcap.dll` (newly added) imports `packet.dll`, which finally explains why `Packet.dll` was in the bundle at all (see §12). `quazip.dll`, `swscale-4.dll`, `Qt5Svg.dll`, `D3Dcompiler_47.dll`, `vcruntime140.dll` are stock support libraries; none is imported by the exe's static table.

### 9. `.Acx` files — RESOLVED: saved electrode/actuation sequences, plain CSV-ish text

Files: `test.Acx` (5 lines), `test1.Acx` (6 lines), `testpath.Acx` (223 lines). All three are plain ASCII text.

The app confirms the role directly — its file-dialog filter strings are **`Path file(*.txt *.Acx)`** and `*.Acx;;*.txt`, i.e. `.Acx` is the vendor's "path file" format and is interchangeable with `.txt` (it is a text format with a branded extension, not a container).

**Format** — one record per line, three hyphen-delimited fields:

```
<recordType>-<payload>-<durationMs>
```

- **`recordType 0` = electrode/droplet frame.** Payload is a `;`-separated list of drops; each drop is 9 comma-separated integers: `x, y, w, h, 1, 0, 0, 0, 0`. Fields 1–4 are position and size; field 5 is always `1` in these samples; fields 6–9 are always `0` (reserved/unused here).
- **`recordType 3` = temperature setpoint frame.** Seen once, in `test1.Acx`: `3-1,25.00,1;1,25.00,1;1,25.00,1;1,25.00,1-1000` — **four** `;`-separated triplets, matching the four temp channels 101–104 from §3, each `<enabled>,<setpoint °C>,<flag>`. This is direct evidence that `.Acx` sequences are **multi-subsystem**, not chip-only.
- **Trailing field is a dwell/duration in ms** — `testpath.Acx` uses 200 ms for 212 of its 223 steps and 1000 ms for 11; `test.Acx`/`test1.Acx` use 1000 throughout.

**Content of the samples**:
- `test.Acx` — one 8×10 drop held at (27,111) for 5 frames. A trivial hold/soak test.
- `test1.Acx` — one 15×13 drop at (32,111), with a temperature frame (all four zones to 25.00 °C) injected as line 4. Shows chip and temp frames interleaved in a single sequence.
- `testpath.Acx` — a real motion script. Early lines are a single 9×9 drop walking the grid one cell per 200 ms frame: (41,1)→(41,59), then (42,59)→(62,59), then (62,60)→(62,90) — an L-shaped path traced by explicit per-frame re-addressing. Later lines carry **60–80 drops per frame** (mostly 1×1, some 2×1/2×2/1×2/2×3) arranged on a regular ~3-cell pitch over roughly x∈[89,113], y∈[37,61], with a handful of drops changing position frame-to-frame while the rest hold station. That is a large static electrode pattern with a few movers — consistent with the multi-drop, collision-avoidance scenario `PathAlgorithm.dll` exists to solve (§5/§8).

**Relation to the Python `Drop` struct**: `project/`'s ctypes `Drop` is 4 ints — `(height, width, row, col)` — matching the 16-byte stride §2 confirmed in `DLLTest.dll::ActivateElec`. The `.Acx` drop record carries **9** fields, so the file format is a superset of what the Python-facing ABI accepts. Field-order mapping between the two (`x,y,w,h` vs `height,width,row,col`) has **not** been established and should not be assumed — nothing was disassembled to pin it down, and `grep` confirms no script in `project/` reads or writes `.Acx` at all.

**Why this matters**: `.Acx` is a ready-made, human-readable, vendor-supported serialization for exactly the kind of electrode choreography the SULI scripts currently hardcode in Python (§1, issue 1). It is a candidate interchange format worth weighing during the code-development phase — with the caveat that the format is only *consumed* by `DMatrix_App.exe`, and the Python layer talks to `DLLTest.dll` directly, so adopting it would mean writing both a reader and an emitter with no vendor library to lean on.

### 10. `TempControlInterFace.dll` — closes the temperature-control gap from §3, but not for Python

File: `inputs/raw_data/TempControlInterFace.dll` (PE32+ x64, 6 sections, 30 exports). PDB path: `D:\AcxelSoft\trunk\CommonOutput\communication\5.14.2\Bin\x64\Release\TempControlInterFace.pdb` — same `AcxelSoft\trunk\` root as §2/§4, in a `CommonOutput\communication` subtree, built against Qt 5.14.2.

**This is the temp-control entry point flagged as missing in §3.** Full API (MSVC-mangled C++, class `TempControlInterFace`, a `QObject` deriving from `InterFaceTemplate`):

| Method | Signature | Purpose |
|---|---|---|
| `InitInterFace` | `bool(QString)` *(virtual)* | open the port — `QString` is the COM port (`COM3` per §3) |
| `Close` | `void()` | close |
| `SendSetTempCmd` | `void(float, int)` | setpoint °C, channel |
| `SendSetPIDCmd` | `void(int,int,int,int)` | P/I/D + channel (**argument order not determined** — see caveat) |
| `SendSetPowerCmd` | `void(int,int)` | power level, channel |
| `SendPortEnableCmd` | `void(bool,int)` | enable/disable a channel |
| `SendReadCurrentTempCmd` | `void(int)` | poll current temp for a channel |
| `SendReadSetTempCmd` | `void(int)` | read back setpoint |
| `SendReadPIDCmd` | `void(int)` | read back PID |
| `SendReadEnableState` | `void(int)` | read channel enable state |
| `SendStopAutoCommand` | `void(int)` | stop automatic/periodic polling |
| `SendCustomCmd` | `bool(QString)` | raw passthrough command |
| `ClearSendQueue` / `GetQueueSize` | `void()` / `int()` | send-queue management |
| `AnalysisData` | `void(QByteArray)` *(private virtual)* | reply parser |
| `SendData` | `void()` *(private)* | queue pump |
| Qt signals | `signal_RecvTemp(int,int)`, `signal_RecvSetTemp(float)`, `signal_RecvPID(PIDInfo)`, `signal_RecvEnable(bool)` | async replies |

**Design**: fully asynchronous and queued — commands are enqueued and drained by a private `SendData()`, replies arrive as `QByteArray` into `AnalysisData()` and are re-emitted as Qt signals. This matches §4's log picture exactly: 146,233 `ANRP` + 60,130 `RP` events are this poll loop running, and `SendSetPIDCmd` is the path by which the `P=20, I=18, D=15` values on channels 101/102 were set. **The app imports 18 of the 30 exports**, including `SendSetPIDCmd`, `SendSetTempCmd`, `SendSetPowerCmd` and `signal_RecvTemp` — so this whole surface is live, not vestigial.

**Wire protocol — only partially recovered.** The DLL's string table is nearly bare: the only command-shaped literals are **`SetPid%1 %2 %3`** (a Qt `QString::arg` template with three substitutions — consistent with P, I, D, channel being addressed separately), plus `RECV:`, `RecvErrorCode`, and the object names `[Com_TempInterFace]` / `TempInterFace`. Everything else is built in code or lives in the missing `InterFace.dll`. **Full protocol recovery is blocked on `InterFace.dll`**, which holds the `InterFaceTemplate` base (and, notably, the actual serial transport — `TempControlInterFace.dll` itself does *not* import `Qt5SerialPort.dll`; only the exe and, presumably, `InterFace.dll` do).

**Caveat on `SendSetPIDCmd`**: four `int` parameters, order undetermined. The mangled name gives types only, and the `SetPid%1 %2 %3` template has three slots against four arguments, so which argument is the channel is a guess. Determining it needs disassembly of the function body (RVA `0x3140`) — **not done**. Do not code against an assumed order.

**Key practical point — this does NOT give Python temperature control cheaply.** Unlike `DLLTest.dll` (flat, undecorated C, directly `ctypes`-callable — §2), `TempControlInterFace.dll` is:
1. MSVC name-mangled C++ with a vtable, so `ctypes` cannot bind it without a wrapper;
2. parameterised with `QString`/`QByteArray`/`PIDInfo` — Qt types with non-trivial ABI;
3. reply-delivery is via Qt **signals**, which need a running `QCoreApplication` event loop;
4. dependent on `InterFace.dll` (missing) and `Qt5Core.dll`.

There is **no flat-C temperature equivalent of `DLLTest.dll` in this artifact set**. Realistic options if temp control is ever wanted from Python: write a small C++ shim exporting flat-C wrappers, drive the COM3 serial protocol directly (needs the protocol, i.e. needs `InterFace.dll`), or ask ACX for a Python SDK covering non-chip subsystems. Worth noting the gap is now *understood* rather than *closed*.

### 11. Camera stack — correction to §4: the SDK is Hikrobot MVS, not MVTec Halcon

§4 concluded from the log class name `Cammgr::CamMgrHalcon` that the vendor camera stack is MVTec Halcon. **The binaries say otherwise, and §4's identification should be treated as superseded.**

- The app imports 21 symbols from `camHalcon.dll`, and every one of them is on class **`CMvCamera`** with `_MV_CC_*` / `_MVCC_*` struct types: `EnumDevices(_MV_CC_DEVICE_INFO_LIST_*)`, `Open(_MV_CC_DEVICE_INFO_*)`, `StartGrabbing`, `GetOneFrameTimeout(..., _MV_FRAME_OUT_INFO_EX_*)`, `ConvertPixelType(_MV_PIXEL_CONVERT_PARAM_T_*)`, `StartRecord(_MV_CC_RECORD_PARAM_T_*)`, `SaveImageToFile`, `SetEnumValue`/`GetIntValue`/`SetFloatValue`, `GetOptimalPacketSize`, `IsDeviceConnected`. `CMvCamera` + `MV_CC_` is the **HIKROBOT / HIKVISION MVS** machine-vision SDK, not Halcon.
- Corroborated by a newly added file: **`XmlParser_MD_VC120_v3_0_MVS_v3_1_0.dll`**, whose PDB path is `G:\Product\MvCameraSDK\GenICam_V3_0_1\bin\Win64_x64\...` and which imports `GCBase_MD_VC120_v3_0_MVS_v3_1_0.dll` / `NodeMapData_MD_VC120_v3_0_MVS_v3_1_0.dll` — the GenICam v3.0.1 component set that ships inside Hikrobot MVS v3.1.0.
- `grep -i halcon` across every binary present hits **only `DMatrix_App.exe`**, and only as the class/log name (`Cammgr::CamMgrHalcon`, `"No matched halcon camera!"`, `"Search halcon camera failed!"`). No MVTec library is present or imported anywhere.

**Conclusion**: "Halcon" is a misleading legacy identifier in ACX's own class naming; the camera SDK actually linked is Hikrobot MVS/GenICam. ~~Two caveats kept explicit: (a) `camHalcon.dll` itself is **missing**…it is possible the wrapper also links MVTec Halcon internally~~ — **caveat (a) is now resolved by §25**: `camHalcon.dll` has since been supplied, is ACX's own code (PDB under `AcxelSoft\trunk\CommonOutput\camHalcon\`), imports only `MVCameraControl.dll`, and contains no MVTec code. The correction below is confirmed, not inferred. As for (b), the §4/§7 substantive point is unchanged and now better supported — the vendor measurement camera runs a **commercial industrial-camera SDK over GenICam**, while Python's `camera.py` is a from-scratch generic OpenCV/UVC reimplementation. The three-independent-camera-stacks observation from §7 stands, with "Halcon" replaced by "Hikrobot MVS".

### 12. Networking revisited — §6 partly confirmed, and one genuinely new finding

Re-examining with the exe in hand:

- ~~**WinPcap is bundled, not used.**~~ **WRONG — superseded by §15.1.** The reasoning here (nothing in the then-available file set imported `wpcap.dll`, and the exe does not import it statically) was sound but the file set was incomplete: `MCDLL_NET.dll`, added later, **does** import `wpcap.dll`. WinPcap is the **axis motion controller's raw-Ethernet transport**, not dead weight. The chain is `DMatrix_App.exe` → `MCDLL_NET.dll` → `wpcap.dll` → `Packet.dll`.
- **`Qt5Network` is used — but for HTTP file upload, not chip control.** The exe's 21 `Qt5Network` imports are entirely `QNetworkAccessManager::post(...)`, `QHttpMultiPart`, `QHttpPart::setBody`/`setBodyDevice`/`setHeader`, `QNetworkRequest::setUrl`/`setRawHeader`, `QNetworkReply::error`. That is a **multipart HTTP POST uploader** — no `QTcpSocket`, no raw socket use at all. So Qt5Network cannot be implementing the `192.168.0.2:60001` mode either.
- ~~**The `192.168.0.2` chip-network mode still looks vestigial.**~~ **PARTLY WRONG — see §20.1.** The observation here is correct as far as it goes (the literal is a config default next to the `IP/IP` key, matching `Ip=192.168.0.2` in both `Config.ini` files), and it is correct that *the exe itself* contains no socket path. But the socket path exists one layer down, in `InterFace.dll`, which was not yet available: it implements TCP client and TCP server transports for every subsystem. "Implemented and selectable but unexercised" replaces "vestigial".
- **NEW: the app has a hardcoded outbound WebSocket endpoint to a public internet host.** The exe imports `QWebSocket` (`open(QUrl)`, `sendTextMessage`, `textFrameReceived`, `connected`/`disconnected`, `close`) and contains the literal **`ws://47.99.63.179:5000`**. This string sits in the config-key table immediately beside three keys not previously seen: **`Sigenex/Flag`**, **`Sigenex/Pop`**, **`Sigenex/Push`** — strongly suggesting a flag-gated cloud push/pop feature pointed at a vendor server, with the `ws://` URL as its default. The exe also embeds `nlohmann::json` v3.11.2, a plausible payload encoder for that channel.
  - `47.99.63.179` is a **public, non-RFC1918 address**; the `47.99.0.0/16` range is generally associated with Alibaba Cloud's Hangzhou region — *that attribution is from general knowledge, not verified by a WHOIS lookup here*. `ws://` is unencrypted.
  - **Not evidence that it is active**: none of `Sigenex/Flag`, `Sigenex/Pop`, `Sigenex/Push` appears in either `Config.ini`, and §6's grep of ~128MB of operational logs found no network session of any kind. Present-and-configurable, with no evidence of having run.
  - **Flagged deliberately.** A vendor desktop app on an Argonne-network machine that embeds an unencrypted WebSocket client and an HTTP multipart uploader aimed at vendor infrastructure is worth the researcher knowing about, independent of the code-development question — both for data-handling/export review and because it is the kind of thing lab cybersecurity would want declared. **No action taken and none recommended here beyond noting it**; whether it matters is a judgement for the researcher and ANL, not something to settle from static strings.

### 13. Axis subsystem — `MCDLL_NET.dll`, and a researcher field note on app reliability

**What the binaries show.** The axis is the one subsystem that does **not** follow the vendor's own `*InterFace.dll` / `InterFaceTemplate` pattern (§8). Instead the exe links a third-party motion-control library, `MCDLL_NET.dll`, with a flat, undecorated C API — 13 imports:

`MCF_Open_Net`, `MCF_Close_Net`, `MCF_Get_Axis_State_Net`, `MCF_Get_Position_Net`, `MCF_Get_Vel_Net`, `MCF_Get_Input_Net`, `MCF_Set_Axis_Profile_Net`, `MCF_Set_ELP_Trigger_Net`, `MCF_Search_Home_Set_Net`, `MCF_Search_Home_Start_Net`, `MCF_Search_Home_Stop_Net`, `MCF_Uniaxial_Net`, `MCF_Axis_Stop_Net`.

- **`MCF_Open_Net` is almost certainly the call behind §4's "Failed to Open the Axis"** — the 250-failures-out-of-442-attempts figure. The name maps directly onto the log message.
- The app wraps this in a class **`DM_Axis`** with a **`AxisWorkUI`**, and — per the RTTI string `QtConcurrent::VoidStoredMemberFunctionPointerCall0<void, DM_Axis>` — drives at least one `DM_Axis` member function on a **QtConcurrent background thread**. The other subsystems go through the queued, signal-based `InterFaceTemplate` machinery instead. So the axis is architecturally the outlier twice over: third-party driver, and a different concurrency model.
- ~~**`MCDLL_NET.dll` is missing from `raw_data/`**~~ — **now supplied and analysed in depth in §15.** Answering the open questions raised here: the vendor is still unidentified (no PDB, no copyright string), and `_Net` denotes **raw Ethernet**, not .NET — the driver talks to the controller with layer-2 frames via WinPcap. §15.4 gives a disassembly-grounded candidate mechanism for the failure rate.

**Researcher field note (Kailey McGady, 2026-08-05) — observation, not a verified finding.**
> "I don't use `DMatrix_App.exe` much myself, and in my experience it's not great — it feels unreliable/buggy. My working hypothesis is that this is connected to the axis motion stage's ~56% init failure rate."

Recorded verbatim as first-hand operator experience. Status and standing:
- **This is not verifiable from the artifacts** and is not being treated as established. It is a hypothesis about causation (axis failures → perceived app unreliability) supported by lived use, and it should carry that weight — real evidence of the kind static analysis cannot produce, but not confirmation.
- **What the files do and don't say about it.** Consistent-with, not proof: the axis genuinely is the least reliable subsystem in the quantified logs (§4), it genuinely is the architectural outlier described above, and a failure in a background-threaded third-party driver is a plausible route to UI-level flakiness. Against over-reading it: §4's counts show the chip, temp and camera paths succeeding at high rates over the same period, so "the app is buggy" is not currently attributable to those; and nothing examined so far establishes *any* causal link between an `MCF_Open_Net` failure and the app's overall behaviour — no crash dumps, no error-path disassembly, no observation of the app under failure.
- **Update (§21, §23, §24):** three things bear on this note. (a) The retry-3-times logic is now located — it is in the exe's `DM_Axis`, not in a driver layer (`Failed to Open the Axis 3 Times`), and `AxisInterFace.dll` exists but is not loaded by this build, so the outlier framing above stands. (b) `DumpCrashes/` arrived **empty**, so the "no crash dumps" gap named below is still open. (c) A **second, independent candidate explanation** for perceived app unreliability surfaced that has nothing to do with the axis: the app hardcodes `./onnx/multi_droplet.onnx` and `./names/multi_droplet.names`, neither of which exists on disk, and `cell_droplet.onnx` emits 3 classes against a 1-label `.names` file (§24). Worth asking whether the researcher uses the `Detect` feature before weighting either explanation.
- **Update (§15):** `MCDLL_NET.dll` has since been supplied and disassembled. It supplies a concrete candidate mechanism — an unfiltered, promiscuous, single-shot WinPcap receive with a ~100 ms per-adapter probe — that would produce exactly this kind of intermittent failure on a shared network. That raises the hypothesis from "plausible" to "has a named mechanism with a falsifiable prediction", but it still does not confirm the causal link to app-level unreliability. See §15.4.
- **What would actually test it** (deferred — for the plan, not now): correlate timestamps of "Failed to Open the Axis" against session end / restart patterns in the brief logs to see whether axis failures coincide with sessions ending abnormally; obtain `MCDLL_NET.dll` and identify the motion-controller vendor and its known failure modes; check whether the retry-3-times-then-report pattern leaves the app in a degraded state or recovers cleanly; and — cheapest and most direct — have the researcher note what "unreliable" concretely looks like next time it happens (hang, crash, wrong position, unresponsive UI), since that detail alone would discriminate between several of these.

### 14. Updated missing-artifact list (blocks further static analysis) — *superseded by §19*

Ordered by how much each would unlock:

1. **`InterFace.dll`** — the `InterFaceTemplate` base for all four `*InterFace` subsystems; holds the serial transport and, with it, the actual wire protocols (§10 is blocked on this).
2. **`MCDLL_NET.dll`** — the axis driver; needed to take the field note in §13 any further.
3. **`MicrofluidicsInterFace.dll`** — the app's chip API; would show what the richer frame/polarity/timing surface (§8) actually does on the wire, and how it relates to `DLLTest.dll`.
4. **`camHalcon.dll`** — would settle §11 definitively (Hikrobot MVS only, or a Halcon layer too).
5. **`Qt5Gui.dll`**, **`log4qt.dll`** — completeness; `Qt5Gui` was already flagged in §7 and is confirmed a direct app import.
6. `LightSystemInterFace.dll`, `MagnetInterFace.dll` — low priority; their APIs are already legible from the app's imports (§8) and neither subsystem is in scope for the Python layer.

### 15. `MCDLL_NET.dll` — the axis motion driver, and a concrete mechanism for the init failures

File: `inputs/raw_data/MCDLL_NET.dll` (PE32+ x64, 2.7 MB, 7 sections, **232 exports**). No PDB path, no vendor string, no copyright banner — vendor not identified from the binary. The `MCF_` prefix and `_Net` suffix are the only branding.

**It is a general-purpose Ethernet motion-control SDK, of which the instrument uses ~6%.** `DMatrix_App.exe` imports 13 of the 232 exports (§8). The rest covers buffered/coordinated motion (`MCF_Buffer_*`, `MCF_Line2/3/4_Net`, `MCF_Arc2_*`, `MCF_Screw3_*`), electronic gearing, PWM, position compare/latch, encoder capture, handwheel jog, servo alarm handling, soft limits, FPGA/ARM firmware download (`MCF_Download_Fpga_Net`, `MCF_DataChange_Arm_Net`), flash read/write, a **laser-marking galvo subsystem** (~35 `MCF_Marking_*` exports incl. XY2-100 state and laser PWM/DAC control), and a **parts-sorting subsystem** (`MCF_Sorting_*`). None of that is instrument-specific — this is an off-the-shelf multi-axis controller library that ACX/Sigenex bought in, and the AM-DMF stage uses a thin slice of it.

#### 15.1 — CORRECTION to §12: WinPcap is not dead weight, it is the axis transport

`MCDLL_NET.dll` imports **`wpcap.dll`**. §12 concluded WinPcap was bundled but unused because nothing in the then-available set imported it; that conclusion is **wrong and is superseded here**. The chain is `DMatrix_App.exe` → `MCDLL_NET.dll` → `wpcap.dll` → `Packet.dll` (NPF driver). Both `Packet.dll` (§6) and `wpcap.dll` (§8) are now fully explained.

The `_Net` suffix on all 232 exports means **raw Ethernet**, not TCP/IP: the seven pcap functions imported are `pcap_findalldevs`, `pcap_freealldevs`, `pcap_open`, `pcap_close`, `pcap_sendpacket`, `pcap_next_ex`, `pcap_geterr`. The motion controller is addressed with **layer-2 frames on a NIC**, which is why the axis needs a packet-capture driver installed at all and why nothing about it ever appeared in the IP-level config.

#### 15.2 — The transport layer, from disassembly

All pcap use is confined to one region (`0x180007aa0`–`0x180007e14`), five small functions:

| Address | Role | Behaviour |
|---|---|---|
| `0x180007aa0` | enumerate adapters | one-shot (guarded by a global flag); `pcap_findalldevs` into a fixed **16-slot** table at `0x18c711860`, each slot 0x100 bytes: name truncated to 127 chars at +0x00, description truncated to 127 chars at +0x80. On `-1` logs `Error in pcap_findalldevs_ex: %s` and returns 0. |
| `0x180007c40` | adapter count | calls the above, returns the count |
| `0x180007c60` | open adapter by index | bounds-checks index, then `pcap_open(name, snaplen=0x10000, flags=0x19, to_ms=-1, auth=NULL, errbuf)`. Stores the result in a **single global handle** at `0x18c714c78`. Logs `open adapt %s ok` / `Interface %s could not open ,errmsg:%s`. |
| `0x180007d20` | close | `pcap_close(handle)` if non-null |
| `0x180007d50` / `0x180007db0` | send / receive | `pcap_sendpacket(handle, txbuf, txlen)`; `pcap_next_ex(handle, &hdr, &data)` — on `<= 0` returns failure immediately, otherwise copies `hdr->caplen` (clamped to 0x1000) into the rx buffer |

`flags = 0x19` decodes as `PCAP_OPENFLAG_PROMISCUOUS | PCAP_OPENFLAG_NOCAPTURE_LOCAL | PCAP_OPENFLAG_MAX_RESPONSIVENESS`.

#### 15.3 — The open/discovery path

`MCF_Open_Net` (RVA `0x1d050`) is a thin wrapper: it calls the generic command dispatcher at `0x18001b0f0` with command `0x0e`, and on success calls it again with command `0x2a`, returning that result. Nonzero is success. The real work is in the discovery loop around `0x18001bdc9`:

```
count = GetAdapterCount()
if (count <= 0) -> bail
for (idx = 0; idx < count; idx++):
    OpenAdapter(idx)                  ; 0x180007c60
    <build + send discovery frame>    ; 0x180026910
    Sleep(10)
    for (i = 0; i < 100; i++):        ; ~100 x 1 ms
        Sleep(1)
        if (reply_flag[card] == 0) -> found, proceed
    -> give up on this adapter, try the next
```

So opening the axis means: **walk every network adapter on the machine in `pcap_findalldevs` order, open each in promiscuous mode, broadcast a probe, and wait ~100 ms for a reply.**

#### 15.4 — Why this is a plausible mechanism for the ~56% failure rate

Four properties of the above are fragile, all of them disassembly-confirmed:

1. **There is no BPF filter anywhere.** `pcap_compile`, `pcap_setfilter` and `pcap_datalink` are *not imported at all* — verified against the full import table. Combined with promiscuous mode, the receive path hands back **whatever Ethernet frame arrives first on that adapter**: ARP, mDNS, DHCP, LLDP, spanning-tree, any other lab traffic.
2. **The receive path is a single unretried call.** `pcap_next_ex` is invoked once; `<= 0` (which includes the timeout return) fails immediately. At the caller (`0x1800227c4`) the only validation is a **length comparison** against the expected reply size — a foreign frame that happens to match on length is not distinguished further at that layer, and one that doesn't match takes the error branch rather than being skipped in favour of the next frame.
3. **`to_ms = -1`.** WinPcap documents `0` as "wait until enough packets arrive"; negative values are undocumented, so the effective read-timeout behaviour is implementation-defined.
4. **Adapter identity is positional.** Selection is by index into `pcap_findalldevs` order, capped at 16 adapters, enumerated once per process. On a machine with virtual adapters (VMware, VirtualBox, Hyper-V, WSL, Npcap Loopback) the ordering is not guaranteed stable across boots or adapter state changes, and every non-matching adapter costs ~110 ms of probe before the real one is reached.

**This is consistent with the researcher's field note in §13 and gives it a mechanism it previously lacked** — an unfiltered promiscuous read on a shared network is exactly the kind of thing that succeeds on a quiet link and fails intermittently on a busy one, which is the shape of a ~56% failure rate that persisted for months without ever being fully deterministic. **It is not proof.** Nothing here has been observed running; the causal claim is still untested. What it does is convert "the axis is flaky" into a specific, falsifiable prediction: *failure rate should track background broadcast/multicast volume on the NIC the controller is attached to, and should drop sharply on a dedicated point-to-point link with no other traffic.*

Cheap tests this suggests, in order of effort (deferred, for the plan):
- Check whether the axis NIC is a dedicated interface or shares a lab/enterprise network — a config/wiring question, no code needed.
- Count the machine's adapters; anything above a handful lengthens the probe and destabilises ordering.
- Correlate the log timestamps of "Failed to Open the Axis" against time of day / network activity.
- Confirm the NPF driver's service state at the times of failure (WinPcap's driver not running yields a clean, total failure rather than an intermittent one, so this mostly *rules out* an alternative).

#### 15.5 — Two additional defects worth recording

- **The close path does not appear to clear the global handle.** `0x180007d20` calls `pcap_close` and returns 1 without a visible store zeroing `0x18c714c78`, which would leave a dangling handle for any subsequent close or send. *Caveat: I did not complete a full cross-reference of writes to that global (the verification command was interrupted), so treat this as likely-but-unconfirmed rather than established.*
- **A single global pcap handle** means one adapter open at a time process-wide — fine for one controller, but it makes the probe loop above destructive: each adapter tried overwrites the previous handle.

### 16. `MicrofluidicsInterFace.dll` — full export surface, and what Python is missing

File: `inputs/raw_data/MicrofluidicsInterFace.dll` (PE32+ x64, 72 KB, **57 exports**). PDB: `D:\AcxelSoft\trunk\CommonOutput\communication\5.14.2\Bin\x64\Release\MicrofluidicsInterFace.pdb` — same `CommonOutput\communication` subtree as `TempControlInterFace.dll` (§10), so the subsystem interfaces are one build unit.

Imports **only** `InterFace.dll` + `Qt5Core.dll` + CRT. Note what is absent: **no `libusb-1.0.dll`**. `DLLTest.dll` and `Microfluidics.dll` both link libusb directly (§2); the app's chip path does not — its transport lives in `InterFaceTemplate` inside the still-missing `InterFace.dll`. So the app and the Python layer reach the same hardware by structurally different routes.

**Protocol confirmed as CRC16-framed with `0xAA` message headers.** The DLL exports a protected `crc16(QByteArray)` and a public `RecvCRCError()`, and its string table contains the literal command IDs:

`AA0101, AA0102, AA0201, AA0301, AA0400, AA0401, AA0500, AAF001, AAF002, AAFA00, AAFB00, AAFD00, AAFD01, AAFE01, AAFF01, AAFF02, AB0301`

This cross-links to §2: the `InquireVolt` disassembly in `DLLTest.dll` validated a **`0xAA` header byte** on the 18-byte USB response. Both implementations speak the same `0xAA`-framed device protocol; they differ in host-side plumbing, not in wire format. Also present: config/parameter tokens `ColCount`, `DelayTime`, `DeviceInstanceId`, and the log literals **`Electrify Success`**, `Electrify Fail`, `PownOn Success`, `PownOff Success`, `ReadVolt Fail`, `IC Error`, `IC Noraml`, `CRC Check Error`, `Recv DisEnable` — which **identifies this DLL as the source of the 3,889 "Electrify Success" events counted in §4** (the `PownOn`/`Noraml` misspellings are the vendor's).

**Full export surface** (57), grouped:

- *Lifecycle*: ctor, dtor, vtable, three `InitInterFace` overloads — `(QString)`, `(QString,int)`, `(int,int,QString)`
- *Electrode actuation*: `SendElectrify(int,int,QVector<QVector<int>>)`, **`SendElectrify(int,int,QVector<QRect>,int)`**, `SendElectrifyPro(int,int,QVector<QVector<int>>)`, `SendElectrifyElec(int,int,QVector<QVector<int>>,int)`
- *Frame/timing*: `SendSetFrame(int,int)`, `SendSetSelectFrame(int)`, `SendOpenFrame(bool)`, `SendSetTime(int,int)`
- *Electrical*: `SendSetVoltage(int*)`, `SendReadVoltage()`, `SendPowerOn()`, `SendPowerOff()`, `SendSetPolarity(bool)`, `SendReadPolarity()`, `SendSetVerFreq(bool)`, `SendOpenDownFreq()`, `SendCloseDownFreq()`
- *Chip/device*: `SendSetChipType(int,int,int)`, `SendReadICState()`, `SendReadModel()`, `SendReadVersion()`, `SendSetVersion(QString)`, `SendResetting()`
- *Polling*: `SendPolling()`, `SendPollingV2()`
- *Firmware update (IAP)*: `SendPrePareIAPFile()`, `SendIAPFile(QString)` — with `RecvIPAPrePare`/`RecvIPAComp` acknowledgements
- *Raw*: `SendData(QByteArray)` — **public here**, unlike `TempControlInterFace`'s private no-arg `SendData()`
- *Receive signals*: `RecvElectrify`, `RecvPowerState`, `RecvReadVoltage`, `RecvSetVoltage`, `RecvICState`, `RecvSetChipType`, `RecvSetTime`, `RecvPolarity`, `RecvDownFreq`, `RecvPolling(QVector<QString>)`, `RecvCRCError`
- *Qt boilerplate*: metaObject/qt_metacall/qt_metacast/qt_static_metacall/staticMetaObject/tr/trUtf8, plus private `AnalysisData(QByteArray)`

**Comparison against what `DLLTest.dll` exposes to Python:**

| Capability | `DLLTest.dll` (Python, 7 exports) | `MicrofluidicsInterFace.dll` (app, 57 exports) |
|---|---|---|
| Connect / disconnect | `InitUSB`, `OpenUSB`, `CloseUSB` | `InitInterFace` ×3 overloads |
| Actuate electrodes | `ActivateElec(row, col, count, Drop*)` | 4 variants incl. a **`QVector<QRect>`** form |
| Set voltage | `SetVolt(9 ints)` | `SendSetVoltage(int*)` |
| Read voltage | `InquireVolt(9 int*)` | `SendReadVoltage()` + `RecvReadVoltage` |
| Power | `SetPower(bool)` | `SendPowerOn()` / `SendPowerOff()` + `RecvPowerState` |
| Frame / timing control | **none** | `SendSetFrame`, `SendSetSelectFrame`, `SendOpenFrame`, `SendSetTime` |
| Polarity | **none** | `SendSetPolarity`, `SendReadPolarity` |
| Frequency control | **none** | `SendSetVerFreq`, `SendOpenDownFreq`, `SendCloseDownFreq` |
| Chip type selection | **none** | `SendSetChipType(int,int,int)` |
| IC / model / version query | **none** | `SendReadICState`, `SendReadModel`, `SendReadVersion`, `SendSetVersion` |
| Device reset | **none** | `SendResetting` |
| Status polling | **none** | `SendPolling`, `SendPollingV2`, `RecvPolling` |
| Firmware update | **none** | `SendPrePareIAPFile`, `SendIAPFile` |
| Error signalling | return codes only | `RecvCRCError` + typed `Recv*` signals per operation |
| Raw escape hatch | **none** | `SendData(QByteArray)` |

Two observations that matter for the code-development phase:

- **The `QVector<QRect>` electrify overload is the closest thing to the `.Acx` drop record found so far** — a rectangle is `x,y,w,h`, which is exactly fields 1–4 of the 9-field `.Acx` drop (§9). That is suggestive of how the app's UI, the `.Acx` format and the wire protocol line up, though the mapping is still not proven and the remaining five fields are unaccounted for.
- **The Python ceiling is now precisely quantified.** Everything in the "none" rows is unreachable from `DLLTest.dll`. Most consequential for an autonomous workflow: no status polling, no IC-state or error read-back beyond return codes, and no device reset — i.e. the Python layer cannot ask the chip how it is doing, only tell it what to do. That is directly relevant to §1's finding that the loop is gated on human `input()` calls; some of that gating may exist *because* there is no programmatic health check available.

Same caveats as §10 apply to calling this from Python: MSVC-mangled C++ with a vtable, Qt types in the signatures, signal-based replies needing an event loop, and a hard dependency on the missing `InterFace.dll`.

### 17. `MediaProcess.dll` and `MathParser_...MVS_v3_1_0.dll` — further confirmation of §11 (Hikrobot MVS)

- **`MediaProcess.dll`** (PE32+ x64, 1.75 MB, 20 exports). All exports are `MV_MP_*`: `MV_MP_Decode`, `MV_MP_ConvertPixelType`, `MV_MP_SaveImage`/`SaveImageToFile`/`SaveImageMemSafe`, `MV_MP_Scaling`, `MV_MP_Clip`, `MV_MP_WhiteBalanceProcess`, `MV_MP_SetRecordParam`/`StopRecord`, `MV_MP_SavePointCloudData(Ex)`, `MV_MP_CreateHandle`/`DestroyHandle`/`GetVersion`. PDB: `g:\Product\Component\MediaProcess\MediaProcess\trunk\bin\win64\MediaProcess.pdb` — same `G:\Product\` root as the MVS GenICam components. Imports `swscale-4.dll` (FFmpeg scaler) and `MSVCR90.dll`; statically bundles libjpeg-turbo, libpng 1.6.34 and zlib 1.2.11. This is the **Hikrobot MVS image-processing/recording component**, and it explains why `swscale-4.dll` is in the bundle.
- **`MathParser_MD_VC120_v3_0_MVS_v3_1_0.dll`** (PE32+ x64, 38 KB, 45 exports). PDB: `G:\Product\MvCameraSDK\GenICam_V3_0_1\bin\Win64_x64\...`. Imports `MSVCP120.dll`/`MSVCR120.dll`. A **GenICam v3.0.1 support component** — the expression evaluator used for GenICam node-map formulas — sibling to the `XmlParser_...` DLL already identified in §11.

Both **independently corroborate §11's correction**: the vendor camera stack is Hikrobot MVS over GenICam, and "Halcon" is only ACX's class name. Note the MVS component set is still incomplete here — `MvCameraControl.dll` and `NodeMapData_MD_VC120_v3_0_MVS_v3_1_0.dll` are referenced but absent.

### 18. MSVC runtime components and manifests — boilerplate, confirmed and logged

No surprises in any of these; recorded so nothing in the bundle is unaccounted for.

| File | Identity | Notes |
|---|---|---|
| `msvcp90.dll` | MS Visual C++ 2008 C++ standard library, x64, 3,181 exports | imports `MSVCR90.dll` |
| `msvcm90.dll` | MS Visual C++ 2008 **managed** C++ support, v9.0.21022.8, 103 exports | imports `mscoree.dll` — the C++/CLI bridge; `file` reports it as a .NET assembly. Consistent with `PathAlgorithm.dll` being mixed-mode C++/CLI (§5) |
| `msvcp120.dll` | MS Visual C++ 2013 C++ standard library, 1,569 exports | imports `MSVCR120.dll`; required by the MVS GenICam DLLs (§17) |
| `msvcp140.dll` | MS Visual C++ 2015+ C++ standard library, 1,515 exports | imports `VCRUNTIME140.dll` + UCRT api-ms-win-crt-*; the runtime for the Qt/ACX components |
| `Microsoft.VC90.CRT.manifest` | side-by-side assembly manifest, `Microsoft.VC90.CRT` v9.0.21022.8 amd64 | declares `msvcr90.dll`, `msvcp90.dll`, `msvcm90.dll` |
| `Microsoft.VC90.DebugCRT.manifest` | side-by-side manifest, `Microsoft.VC90.**DebugCRT**` v9.0.21022.8 amd64 | declares `msvcr90d.dll`, `msvcp90d.dll`, `msvcm90d.dll` with SHA-1 hashes |

Three small things worth flagging rather than skipping:

- **The bundle spans four MSVC generations** — VC9 (2008), VC12 (2013), VC14 (2015+), plus the UCRT. That is a consequence of mixing ACX's own Qt 5.14.2 code (VC14), the Hikrobot MVS/GenICam components (VC12), and the older MVS MediaProcess (VC9).
- **A *debug* CRT manifest is shipped.** `Microsoft.VC90.DebugCRT` is not redistributable and normally only appears if something in the bundle was built against the debug runtime. The debug DLLs themselves (`msvcr90d`/`msvcp90d`/`msvcm90d`) are not in `raw_data/`. Minor, but it hints the vendor's packaging is not a clean release build.
- **`MSVCR90.dll`, `MSVCR120.dll` and `MSVCR140.dll` are all absent** while their C++-library counterparts are present — so the C runtime halves of three of the four generations are still missing from the artifact set.

### 19. Updated missing-artifact list (supersedes §14) — *superseded by §29*

Resolved since §14: `MCDLL_NET.dll` (§15) and `MicrofluidicsInterFace.dll` (§16).

Still blocking, in priority order:

1. ~~**`InterFace.dll`**~~ — **RESOLVED in §20.** It holds `InterFaceTemplate` plus an `InterFace` transport engine with four backends (USB/libusb, serial, TCP client, TCP server) and Modbus-CRC16 framing helpers.
2. **`camHalcon.dll`** — would close §11/§17 definitively.
3. **`Qt5Gui.dll`**, **`log4qt.dll`** — direct app imports, still absent.
4. `MvCameraControl.dll`, `NodeMapData_MD_VC120_v3_0_MVS_v3_1_0.dll` — completes the MVS set.
5. `MSVCR90.dll` / `MSVCR120.dll` / `MSVCR140.dll` — completeness only.
6. `LightSystemInterFace.dll`, `MagnetInterFace.dll` — unchanged, low priority.

### 20. `InterFace.dll` — RESOLVED. The transport base, and it changes the network picture

The file that sat at the top of the missing list in both §14 and §19 arrived in this batch. PDB: `D:\AcxelSoft\trunk\CommonOutput\communication\5.14.2\Bin\x64\Release\InterFace.pdb` — same build unit as the four `*InterFace` subsystem DLLs.

It exports **two** classes:

- **`InterFaceTemplate`** — the base every subsystem interface derives from (§8). Exports `InitInterFace` in four overloads (`()`, `(QString)`, `(QString,int)`, `(int,int,QString)`, plus one taking an `InterFace*`), `Close()`, `Inquire()`, `GetInterFace()`, `RecordLog(QString,QString,RecordType)`, `InterFaceStateChange(InterFaceState,QString)`, and two protected protocol helpers: **`ModbusCRC16(QByteArray)`** and **`SendModbusCmd(uint8, uint16, uint16, uint8, QByteArray, uint16)`**.
- **`InterFace`** — the actual transport engine, a `QThread` subclass (`run()` is a protected virtual override) with `MainPro()`, `PeriodSend()`, `PeriodRecv()`, `QueueSendData(QByteArray)`, `PortSendData(QByteArray)`, `SendData`, `Read`, `Reconnection()`, `StateChange`, `GetInterFaceConnectState()`, `CheckUsb()`, `getInterFaceType(InterFaceType&, const void*)`, `handleSerialError(QSerialPort::SerialPortError)`.

**The protocol question from §10 and §16 is answered: it is Modbus framing with CRC16.** `InterFaceTemplate::SendModbusCmd` and `ModbusCRC16` are the shared primitives; `MicrofluidicsInterFace`'s own `crc16` (§16) and its `AA…`-prefixed command IDs sit on top of this, as does the temp channel's `SetPid%1 %2 %3` (§10). Each subsystem interface is a Qt object owning a threaded transport with a send queue and periodic send/recv — which is exactly the queued-async behaviour inferred in §10 and the source of the `ANRP`/`RP` poll volume in §4.

**`InterFace` implements four transports, private-initialised and selected at runtime:**

| Transport | Methods | Backing library |
|---|---|---|
| USB | `InitUsb`, `OpenUsb`, `USBRecvData`, `CheckUsb` | **`libusb-1.0.dll`** |
| Serial | `InitSerialPort`, `OpenSerialPort`, `handleSerialError` | `Qt5SerialPort.dll` |
| TCP client | `InitTcpClient`, `OpenTcpClientConnect` | `Qt5Network.dll` |
| TCP server | `InitTcpServer`, `OpenTcpServerlisten` | `Qt5Network.dll` |

It also imports `SETUPAPI.dll` — Windows device enumeration, which is how the `DeviceInstanceId` config keys (§12, §16) and VID/PID fields (§3) get resolved to a physical port.

#### 20.1 — CORRECTION to §12 and §6: the network chip-control mode *is* implemented

§6 concluded "there is no code in any DLL we have that implements the `192.168.0.2:60001` network mode", and §12 narrowed it further by showing the exe's `Qt5Network` imports are HTTP-upload-only. Both statements were accurate about the files then available, and **both are now superseded**: `InterFace.dll` contains a full TCP client *and* TCP server path over `Qt5Network`, sitting underneath every subsystem including `MicrofluidicsInterFace`. The `Ip=192.168.0.2` config field is therefore a **live, supported code path**, not a vestigial one.

What has *not* changed: §6's evidence that it has never actually been used here still stands — no network session appears anywhere in ~128 MB of operational logs, and the Python layer (§2) remains USB-only via `DLLTest.dll`. So the correct statement is now "implemented and selectable, but unexercised in this deployment", not "unimplemented". Worth knowing, because it means chip control over Ethernet is a vendor-supported option rather than something that would need building.

### 21. `AxisInterFace.dll` — it exists, it is **not loaded**, and the retry logic is elsewhere

Direct answers to the two questions asked:

**Does it change the "architectural outlier" framing of §13? Partly — but the outlier finding stands for this build.**

- The DLL is real: PE32+ x64, 17.9 KB, imports `InterFace.dll` + `Qt5Core`, and its strings show `AxisInterFace`, `AnalysisData` and a Qt slot `slot_MainPro` — i.e. it *is* an `InterFaceTemplate` subclass built to the same pattern as the other four subsystems. So ACX did write an axis interface following the house architecture; the gap identified in §13 is not a design oversight.
- **But it exports nothing.** Its PE Export Directory is RVA `0x00000000`, size `0` — genuinely empty, verified by contrast against `TempControlInterFace.dll`, which shows a populated export table in the same dump.
- **And `DMatrix_App.exe` does not import it** — confirmed against the app's full import table, which lists `MicrofluidicsInterFace`, `TempControlInterFace`, `LightSystemInterFace`, `MagnetInterFace` and `MCDLL_NET`, but no `AxisInterFace`.
- Its PDB path is from a **different source tree**: `D:\AcxelSoft\trunk\Project\ACXEL\ACXEL\Communication\...`, versus `D:\AcxelSoft\trunk\CommonOutput\communication\5.14.2\...` for the four that *are* used. So it belongs to a sibling product/branch ("ACXEL"), and is shipped inertly in this DMCtrl bundle.

Net effect on §13: the axis remains the architectural outlier **as actually deployed** — `DMatrix_App.exe` still calls `MCDLL_NET.dll`'s flat-C API directly, bypassing the `InterFaceTemplate` abstraction, and still drives it on a QtConcurrent background thread. What changes is the interpretation: this looks less like "ACX never built an axis interface" and more like "an axis interface exists in another branch and this build does not use it."

**Does the retry-3-times logic live here? No — it is in the executable.** `AxisInterFace.dll` contains no retry-related strings. `DMatrix_App.exe` does:

```
Failed to Open the Axis 1 0 2
Failed to Open the Axis 3 Times
Open the Axis 1 0 2 successfully
```

So the retry-3-times-then-report pattern seen in the logs (§4) is implemented in the app's `DM_Axis` class, wrapping `MCF_Open_Net`. The `1 0 2` triple is a fixed parameter set logged verbatim — consistent with `MCF_Open_Net`'s `(cardNo, …)` signature (§15.3) being called with hardcoded arguments rather than anything from `Config.ini`, which has no axis card/adapter field (§3).

### 22. `AxisCache.dat`, `.dat`, `Cache/Version.dat` — contents

**`AxisCache.dat`** (168 bytes, CRLF INI text) — a cached axis geometry/teach-point file:

```
[Axis]
SensorPos=0-0-0
TopZ=0
Throw=0-0-151587081
Z_Index=0
Box_Begin=0-0-151587081
Box_Row=0 / Box_Col=0 / Box_RowSpace=0 / Box_ColSpace=0
Hole0=0-0-0-\x5de6\x31
```

Observations: coordinates are `X-Y-Z` triplets in the same style as `Config.ini`'s axis presets (§3). Everything is zero **except** two Z values of `151587081`, which is **`0x09090909`** — a repeated-filler byte pattern, i.e. almost certainly uninitialised memory serialised out rather than a real coordinate. `Hole0`'s label is an escaped UTF-16 Chinese string (`\x5de6` = 左, "left") — so it reads "left 1".

**Does it relate to the §15 adapter-discovery failure? No.** This file is pure geometry — teach points, box/well grid spacing, a Z index. There is no adapter name, NIC identifier, MAC, card index or connection state in it. It caches *where things are*, not *how to reach the controller*. The all-zeros-plus-`0x09090909` state does suggest the axis was never successfully taught/homed on this machine, which is at least consistent with §4's failure rate, but that is weak circumstantial support, not a mechanism.

**`.dat`** (76 bytes, hidden dotfile, CRLF INI) — a temperature program, not an axis file:

```
[Grounp]        <- vendor's misspelling of "Group"
GrounpNum=1
[Grounp1]
Temp=25.00/25.00
Delay=0/0
LoopNum=1
```

A one-step thermal cycling program: a temperature pair (likely two zones or setpoint/tolerance), a delay pair, and a loop count. This is the persisted form of the same thing `.Acx` record type 3 carries inline (§9), and the `25.00` matches `test1.Acx`'s temperature frame exactly.

**`Cache/Version.dat`** (11 bytes, binary: `de bb a6 bb b2 de bb a6 bc a6 ba`) — **XOR-obfuscated ASCII, key `0x88`**, decoding to **`V3.3:V3.4.2`**. A brute-force over all 256 single-byte XOR and add keys produced exactly one meaningful plaintext, so the decode is solid. Two version strings separated by a colon — plausibly installed-vs-available or firmware-vs-software, though which is which is not determinable from the file. Note neither matches the containing folder name `DMCtrl3.5.2`, so this cache is stale relative to the build it ships beside.

### 23. `DumpCrashes/` — **empty. No crash dumps.**

Reporting this plainly because it was the most promising lead in the batch and it did not pay off: `DumpCrashes/` contains a single empty subdirectory, `DumpCrashes/dump`, and **zero files** — verified including hidden entries. There are no `.dmp`, `.mdmp` or log artefacts of any kind.

What this does and does not tell us:
- **It does not provide evidence for the axis investigation.** The specific gap called out in §13 — "no crash dumps, no error-path disassembly, no observation of the app under failure" — remains open.
- The directory's existence confirms `DMatrix_App.exe` has a crash-dump facility and a configured output location, which is mildly useful: it means that **if the app is crashing, dumps should be landing here**, and their absence is itself informative. Either the app has not hard-crashed on this machine, or dumps were cleared before the folder was copied, or the dump writer is not functioning. Those are distinguishable by asking the researcher whether the folder was emptied, which is worth doing before drawing any inference from the emptiness.
- Practical follow-up for the plan: this is the right place to look *after* a future failure, so it is worth noting the path now.

### 24. `onnx/` + `names/` — a computer-vision inference stage nowhere else in the analysis

This is a genuinely new capability and it is wired into the shipping app.

**Five ONNX models present**, all PyTorch-exported YOLO-family object detectors. Metadata extracted by parsing the ONNX protobuf directly (a minimal reader written for this — the `onnx` Python package is not installed and the scratch venv from §5 no longer exists):

| Model | Size | Producer | IR / opset | Input | Output | Classes |
|---|---|---|---|---|---|---|
| `best1.onnx` | 29.7 MB | pytorch 2.0.0 | 5 / 10 | `images` f32 [1,3,**1280**,1280] | `output0` [1,100800,**6**] | 1 |
| `cell_droplet.onnx` | 29.7 MB | pytorch 2.0.0 | 5 / 10 | `images` f32 [1,3,1280,1280] | `output0` [1,100800,**8**] | **3** |
| `droplet.onnx` | 29.7 MB | pytorch 2.0.0 | 5 / 10 | `images` f32 [1,3,1280,1280] | `output0` [1,100800,6] | 1 |
| `mark.onnx` | 28.5 MB | pytorch 1.13.0 | 7 / 12 | `images` f32 [1,3,**640**,640] | `output0` [1,25200,6] | 1 |
| `single_droplet.onnx` | 28.1 MB | pytorch 1.12.1 | 7 / 12 | `images` f32 [1,3,640,640] | `output0` [1,**800**,6] | 1 |

Architecture is **YOLOv5**: 262–264 nodes dominated by `Conv` ×60 / `Sigmoid` ×60 / `Mul` ×69 (SiLU activation = x·σ(x)), three `MaxPool` (the SPPF block), plus `Concat`/`Split`/`Reshape`/`Transpose`. Anchor counts confirm the input sizes — 25 200 = 3·(80²+40²+20²) for 640 px, and 100 800 = 3·(160²+80²+40²) for 1280 px. Output width is `4 bbox + 1 objectness + N classes`, hence the class counts above. `single_droplet.onnx`'s 800 anchors are anomalous for a standard YOLOv5 head and suggest a cut-down or single-scale variant.

**Class-label files**: `names/mark.names` = `Drop`, `names/cell_droplet.names` = `cell`. Both are 4 bytes, single label, no trailing newline — standard YOLO `.names` format.

**Inference is implemented in `DMatrix_App.exe` via OpenCV DNN, not onnxruntime.** No binary in the bundle imports `onnxruntime`. Instead the exe imports the full OpenCV DNN pipeline from `opencv_world460.dll`: `cv::dnn::readNet`, `blobFromImage`, `Net::setInput`, `Net::forward`, `getUnconnectedOutLayersNames`, `NMSBoxes`. It carries RTTI for two classes, **`Yolo`** and **`YoloMark`**, a `checkBox_Detect` UI control and a `Detect` / `Detect.png` action, and these runtime error strings:

```
Move Drop Detect Error
Wait Drop Detect Error
An abnormality has been detected. Please reinsert or replace the biochip.
```

So the vendor app runs **droplet detection on camera frames and uses the result in the control loop** — verifying that a drop moved, waiting for a drop to arrive, and flagging biochip abnormalities. That is a closed-loop vision capability the SULI Python stack does not have: `camera.py` does HSV colour thresholding only (§1, §4), with no detection model.

**Referenced-vs-present mismatch — a concrete operational defect.** The exe's hardcoded paths are:

```
./onnx/best1.onnx        ./names/cell_droplet.names
./onnx/cell_droplet.onnx ./names/mark.names
./onnx/multi_droplet.onnx  ./names/multi_droplet.names
```

- **`multi_droplet.onnx` and `multi_droplet.names` are referenced by the app but absent from disk.** If the app is launched from this directory, whichever detector uses them cannot load.
- Conversely `droplet.onnx`, `single_droplet.onnx` and `mark.onnx` are present but not referenced by these literals (though `mark.names` *is* referenced, implying `mark.onnx`'s path is composed at runtime rather than hardcoded).
- **`cell_droplet` is internally inconsistent**: the model emits 3 classes but its `.names` file lists only one label (`cell`). Two of its three classes have no name, which at minimum mislabels output and may index out of bounds depending on how `Yolo` reads the file.

None of this is proof of a specific failure, but it is a second, independent candidate explanation for the researcher's "the app feels unreliable" field note (§13) — one that has nothing to do with the axis. A missing model file and a truncated label list are exactly the sort of thing that produces intermittent, hard-to-attribute misbehaviour in a UI. **Worth asking the researcher whether `Detect` is a feature they ever enable**, since if it is off, this is inert.

### 25. `camHalcon.dll` — settled: not Halcon, an ACX wrapper over Hikrobot MVS

Definitive answer to the §11 question. `camHalcon.dll` is **ACX's own code** — PDB `D:\project\AcxelSoft\trunk\CommonOutput\camHalcon\Bin\x64\Release\camHalcon.pdb` — and it imports exactly one non-system DLL: **`MVCameraControl.dll`** (the Hikrobot MVS core, present here as `MvCameraControl.dll`).

Its 30 exports are the `CMvCamera` class the app consumes (§8): `Open`, `Close`, `EnumDevices`, `StartGrabbing`/`StopGrabbing`, `GetImageBuffer`/`FreeImageBuffer`, `GetOneFrameTimeout`, `GetImageForRGB`, `ConvertPixelType`, `DisplayOneFrame`, `CommandExecute`, typed GenICam accessors (`GetIntValue`/`GetFloatValue`/`GetBoolValue`/`GetStringValue`/`GetEnumValue`/`SetEnumValue`), callback registration (`RegisterImageCallBack`, `RegisterEventCallBack`, `RegisterExceptionCallBack`), and transport-specific helpers **`ForceIp`** + `GetGevAllMatchInfo` (GigE Vision) and `GetU3VAllMatchInfo` (USB3 Vision).

**There is no MVTec Halcon code anywhere in the bundle** — no MVTec library is present, imported, or referenced; the only "halcon" strings in the entire file set are inside `DMatrix_App.exe` as ACX's own class and log-message names (`Cammgr::CamMgrHalcon`, `No matched halcon camera!`). §11's correction is now fully confirmed rather than inferred, and the caveat recorded there ("possible the wrapper also links MVTec internally") is **resolved as no**.

### 26. `MultiAgentPathPlanning.dll` — confirms §5's IL-derived inference exactly

PE32+ x64 importing **only `mscoree.dll`** — a pure managed .NET assembly with zero native exports. PDB `D:\AcxelSoft\Code\MAPF\MultiAgentPathPlanning\obj\Release\MultiAgentPathPlanning.pdb` (the `obj\Release` layout is a C# project), and a separate `Code\MAPF` tree from the `trunk\` used by everything else.

§5 inferred, from IL disassembly of `PathAlgorithm.dll` alone, that `AutoPath_Move` calls out to `SIPPMapf.Planning_export` implementing SIPP for multi-agent path finding. **The metadata here confirms every part of that**, and `PathAlgorithm.dll`'s own strings contain `MultiAgentPathPlanning`, `SIPPMapf` and `Planning_export`, closing the link between the two files:

- Types: `SIPPMapf`, `SIPPEnvironment`, `SIPPState`, `SIPPAction`, `AStarSIPP`, `AStar`, generic `PlanResult\`3`, `Neighbor\`3`
- Entry point: **`Planning_export`** — exactly the symbol §5 saw being called
- Safe-interval machinery: `FindSafeInterval`, `FindSafeIntervalByIntersection`, `SafeIntervalsIntersection`, `IntervalIntersection`, `InitIntersectionIntervals`, `InitIntersectionCollisionIntervals`, `SetCollisionIntervals`, `AddToCollision`, `SetCollision`, `GlobalCollision`
- Search internals: `AdmissibleHeuristic`, `GScore`, `FScore`, `Fmin`, `ExploreStates`, `GetNeighbors`, `GetLocation`
- Problem model: `Drops`, `Goals`, `StartsState`, `Obstacles`, `isGoalObs`, `SafetyDistance`/`Safedis`, `DimX`/`DimY`, `Width`/`Height`, `CurrentAgentId`

**Two things beyond what §5 could see:**
1. There is an explicit **`SafetyDistance`** parameter in the model — the planner enforces a minimum separation between droplets, not merely non-collision. That is a physically meaningful knob for AM-DMF (adjacent droplets can merge), and it is exposed as a first-class property.
2. It **writes its own log files** — `AppendLog`, `AppendStateToLog`, `WriteGlobalCollisionToFile`, `AppendAllText`, `CreateDirectory`. So if path planning is ever exercised, it leaves a separate on-disk trail from the log4qt stream analysed in §4. None was found in the bundle, consistent with §5's finding that nothing in the Python layer calls it.

Practical position is unchanged from §5: usable in principle, but reaching it from Python means hosting the CLR or writing a flat-C shim, since the native surface is C++/CLI (`PathAlgorithm.dll`) over a managed assembly.

### 27. Hikrobot MVS camera SDK — cluster now complete

All of the following carry `G:\Product\MvCameraSDK\...` PDB paths, confirming one vendor SDK drop. This closes out the camera picture begun in §11/§17.

| File | Exports | Role |
|---|---|---|
| `MvCameraControl.dll` | 198 | **MVS SDK core** — the API `camHalcon.dll` wraps (§25) |
| `MVGigEVisionSDK.dll` | 55 | GigE Vision transport |
| `MvUsb3vTL.dll` | 40 | USB3 Vision transport layer |
| `MvProducerGEV.cti` | 57 | **GenTL producer**, GigE (a `.cti` is a DLL by GenICam convention) |
| `MvProducerU3V.cti` | 57 | GenTL producer, USB3 |
| `MvCamLVision.dll` | 25 | Camera Link transport |
| `MvRender.dll` | 15 | display/rendering helper (PDB `d:\aibbSVN\MVRender\...GDI\...`) |
| `MvDSS.ax` | 4 | DirectShow video-capture filter (`.ax` = DirectShow filter) |
| `FormatConversion.dll` | 18 | pixel-format conversion (no PDB) |

**GenICam v3.0.1 support set** (all `G:\Product\MvCameraSDK\GenICam_V3_0_1\bin\Win64_x64\`): `GenApi` (471 exports — the node-map engine), `NodeMapData` (182), `XmlParser` (697 KB, §11), `MathParser` (45, §17), `Log` (36), `log4cpp` (382), and the **Camera Link** trio `CLAllSerial` (11), `CLProtocol` (115), `CLSerCOM` (12).

Two conclusions:
- **The camera supports three transports (GigE, USB3, Camera Link) but the instrument uses GigE or USB3.** `camHalcon.dll` exports `ForceIp`/`GetGevAllMatchInfo` (GigE) and `GetU3VAllMatchInfo` (USB3) but nothing Camera Link. So `MvCamLVision.dll`, `CLAllSerial`, `CLProtocol` and `CLSerCOM` are **shipped-but-unused** — the standard full-SDK drop, confirming the medium-priority hypothesis. (Noting the §12/§15 lesson: "unused" here means "no consumer in the current file set", which has been wrong once before.)
- `ForceIp` being available is a small aside worth recording: GigE cameras are configured by IP, so the camera is likely on an Ethernet interface too — a second network-attached device alongside the axis controller (§15).

**FFmpeg** (`avcodec-57` Lavc57.x, `avformat-57` Lavf57.41.100, `avutil-55`, `avfilter-6`, `avdevice-57`, `swscale-4`) — a stock full GPL shared build (`--enable-gpl`, `--enable-gnutls`, `--enable-dxva2`, `--enable-decklink`, …). Role confirmed: video encode/decode and scaling behind `MediaProcess.dll` (§17, which links `swscale-4.dll`) and Qt Multimedia's DirectShow engine (§7) — i.e. the **UI preview/recording path**, not the scientific measurement path.

### 28. Remaining components — confirmed and logged

**Qt plugin folders** (all stock Qt 5.14.2 deployment output, nothing custom):

| Folder | Contents |
|---|---|
| `platforms/` | `qwindows.dll` — the Windows QPA platform plugin (mandatory; the app will not start without it) |
| `styles/` | `qwindowsvistastyle.dll` |
| `imageformats/` | `qgif`, `qicns`, `qico`, `qjpeg`, `qsvg`, `qtga`, `qtiff`, `qwbmp`, `qwebp` |
| `iconengines/` | `qsvgicon.dll` |
| `bearer/` | `qgenericbearer.dll` — network bearer management |
| `mediaservice/` | `dsengine.dll`, `wmfengine.dll`, `qtmedia_audioengine.dll` **plus their `…d.dll` debug builds** — the DirectShow / Media Foundation backends for Qt Multimedia (§7) |
| `translations/` | 22 stock Qt `.qm` files (ar, bg, ca, cs, da, de, en, es, fi, fr, gd, he, hu, it, ja, ko, lv, pl, ru, sk, uk, zh_TW) |

Note the `mediaservice/` folder ships **debug variants alongside release** (`dsengined.dll`, `wmfengined.dll`, `qtmedia_audioengined.dll`) — the same packaging sloppiness already flagged in §18 via the shipped `Microsoft.VC90.DebugCRT` manifest. Also worth a passing note: 22 translations are present and **`zh_TW`** is among them, but there is no `zh_CN` — despite the Chinese strings found in `AxisCache.dat` (§22) and `samp_data`'s headers (§4).

**Runtimes and remaining libraries:**

| File | Identity |
|---|---|
| `msvcr90.dll` (1,404 exports) | MS VC++ 2008 C runtime — the missing half flagged in §18, now present |
| `msvcr120.dll` (1,925 exports) | MS VC++ 2013 C runtime — likewise |
| `concrt140.dll` (291 exports) | MS Concurrency Runtime (VC++ 2015+), companion to `msvcp140` |
| `log4qt.dll` (1,183 exports) | the Qt log4j port behind `Utils.dll`'s `QBLog4Helper` (§8) and the `log4qt.conf` files (§3) — resolves a §14/§19 gap |
| `libEGL.dll` (71) / `libGLESv2.dll` (633) | Qt's ANGLE OpenGL-ES-over-D3D backend (PDB `C:\Users\qt\work\qt\qtbase\lib\...`), companions to `opengl32sw.dll` (§7) and `D3Dcompiler_47.dll` |
| `libmmd.dll` (846) | Intel C++ compiler math runtime — pulled in by one of the Intel-built third-party components |
| `LightSystemInterFace.dll` (15) | subsystem interface, `CommonOutput\communication` build unit — API already read from the app's imports in §8 |
| `MagnetInterFace.dll` (20) | ditto |

### 29. Updated missing-artifact list (supersedes §19)

**Resolved by this batch:** `InterFace.dll` (§20 — the long-standing #1), `camHalcon.dll` (§25), `MvCameraControl.dll` + `NodeMapData` (§27), `log4qt.dll` (§28), `MSVCR90.dll` / `MSVCR120.dll` (§28), plus `MCDLL_NET.dll` and `MicrofluidicsInterFace.dll` already closed in §15/§16.

**Still outstanding:**

1. **`multi_droplet.onnx` and `multi_droplet.names`** — new, and the only gap that is a *functional* defect rather than an analysis limitation: the shipping app references both by hardcoded path and neither exists (§24).
2. **`Qt5Gui.dll`** — still absent despite being a direct app import; flagged since §7, now the last missing hard dependency of the executable.
3. **`MSVCR140.dll`** — `vcruntime140.dll` and `msvcp140.dll` are present; the third piece is not. Completeness only.
4. Crash dumps — `DumpCrashes/` is empty (§23). Not a file to obtain, but the single most valuable thing that could still arrive for the axis investigation.

With `InterFace.dll` in hand, no *analysis* is now blocked on a missing file: the transport and framing for all four subsystem interfaces are readable, and the axis path was already fully disassembled in §15.

### Status
Gathering context only — no plan changes yet. Per researcher instruction (2026-08-05, reaffirmed three times): hold all of the above as background until explicitly asked to fold it into `inputs/research_plan.md`. `inputs/research_plan.md` remains the unmodified template. Note that `sys_boot` reports "an autonomous roadmap exists" at that path; that is the scaffold's presence check firing on an empty file, not a real roadmap.
[2026-08-05T18:39:31.862309+00:00] Context-gathering findings (pre-plan) saved to workspace/analysis.md: (1) project/ codebase survey — duplication, interactive/non-autonomous gating, hardcoded paths; (2) DLLTest.dll vs Microfluidics.dll ABI investigation resolved via disassembly — production scripts correctly target DLLTest.dll's real (undocumented) flat-C ABI, not a bug; (3) Config.ini/configs reveal a 5-subsystem instrument (chip, temp, light, axis, magnet) of which only the chip is reachable via DLLTest.dll; (4) logs/ + samp_data/ (~128MB, June 2024-Aug 2026) quantify real operational history: heavy historical temp-control use with tuned PID params, a chronic ~56% Axis-stage init failure rate, and confirmation the vendor camera stack is Halcon (not OpenCV). Held as background per researcher instruction; not yet folded into inputs/research_plan.md.
[2026-08-05T19:28:00.296424+00:00] Second batch of raw_data additions analyzed and saved to workspace/analysis.md: (5) PathAlgorithm.dll resolved via .NET IL disassembly (dnfile/dncil) as a mixed-mode C++/CLI droplet path planner — AutoPath_Move calls a SIPP-MAPF (Safe Interval Path Planning, Multi-Agent Path Finding) solver per-droplet; not referenced anywhere in the Python codebase; (6) Packet.dll confirmed as stock unmodified WinPcap 4.1.3, Qt5Network.dll confirmed as stock Qt 5.14.2 networking — neither shows any evidence of exercising Config.ini's 192.168.0.2:60001 network chip-control mode in ~128MB of logs, and DLLTest.dll/Microfluidics.dll remain USB-only; (7) standard-library survey — OpenCV 4.6.0, Qt 5.14.2 modules, and confirmation Qt5Multimedia/Qt5MultimediaWidgets are a UI preview/recording path fully separate from the vendor's Halcon scientific camera pipeline; noted Qt5Gui.dll is a missing transitive dependency in the current raw_data set. Held as background per researcher instruction; not yet folded into inputs/research_plan.md.
[2026-08-05T21:00:34.794714+00:00] Third batch of raw_data additions (DMCtrl3.5.2 folder) analyzed; workspace/analysis.md extended with §8-§14 and two prior sections corrected. (8) DMatrix_App.exe import graph settles the instrument architecture: four subsystems (Microfluidics/TempControl/LightSystem/Magnet) are *InterFace.dll modules deriving from InterFaceTemplate in InterFace.dll; PathAlgorithm.dll CONFIRMED actively imported (upgrades §5 from inference); the app uses a THIRD chip implementation, MicrofluidicsInterFace.dll, with a far richer API (frame/timing/polarity/chip-type/IC-state) than the 7 flat-C exports DLLTest.dll gives Python — a concrete capability ceiling on the Python layer. (9) .Acx format RESOLVED as the vendor's plain-text "Path file(*.txt *.Acx)": records are recordType-payload-durationMs; type 0 = electrode frame (';'-separated drops, 9 ints each: x,y,w,h,1,0,0,0,0), type 3 = 4-channel temperature setpoint frame — so .Acx sequences are multi-subsystem; testpath.Acx contains a real 223-step motion script with 60-80 drops/frame. Field-order mapping to the Python 4-int Drop struct NOT established. (10) TempControlInterFace.dll closes the §3 temp-control gap: full 30-export Qt/QObject API documented (SendSetTempCmd(float,int), SendSetPIDCmd 4 ints, Send/Read cmds, Recv signals, queued async design matching the ANRP/RP log volume) — but it is MSVC-mangled C++ over QString/QByteArray with Qt signal delivery and depends on the missing InterFace.dll, so it is NOT ctypes-callable; there is no flat-C temp equivalent of DLLTest.dll. SendSetPIDCmd argument order undetermined. (11) CORRECTION to §4: the vendor camera SDK is Hikrobot/HIKVISION MVS + GenICam v3.0.1 (CMvCamera, MV_CC_* structs, corroborated by XmlParser_MD_VC120_v3_0_MVS_v3_1_0.dll), NOT MVTec Halcon — "Halcon" is only a legacy class name (Cammgr::CamMgrHalcon). (12) Networking revisited: wpcap.dll imports packet.dll, explaining WinPcap's presence; nothing imports wpcap, so it is unused. Qt5Network is used solely for multipart HTTP POST upload (QNetworkAccessManager/QHttpMultiPart), never raw sockets, so it cannot implement the 192.168.0.2:60001 chip mode, which still appears vestigial. NEW: exe imports QWebSocket and embeds a hardcoded unencrypted endpoint ws://47.99.63.179:5000 alongside previously unseen config keys Sigenex/Flag, Sigenex/Pop, Sigenex/Push — a flag-gated vendor cloud push/pop channel; those keys are absent from both Config.ini files and no network session appears in ~128MB of logs, so capability-present/evidence-absent. Flagged for researcher and ANL awareness only; no action taken. (13) Axis subsystem identified as the architectural outlier: not an *InterFace module but third-party MCDLL_NET.dll with flat-C MCF_*_Net API (MCF_Open_Net maps to the logs' "Failed to Open the Axis", 250/442 failures), wrapped in DM_Axis/AxisWorkUI and driven on a QtConcurrent background thread unlike every other subsystem; MCDLL_NET.dll itself is missing from raw_data. (14) Updated missing-artifact list blocking further static analysis, priority-ordered: InterFace.dll, MCDLL_NET.dll, MicrofluidicsInterFace.dll, camHalcon.dll, Qt5Gui.dll/log4qt.dll, then Light/Magnet interfaces. Held as background per standing researcher instruction; inputs/research_plan.md untouched and still the empty template.
[2026-08-05T21:42:45.168266+00:00] Fourth batch of raw_data additions (10 files) analyzed; workspace/analysis.md extended with §15-§19 and §12/§13/§14 corrected. (15) MCDLL_NET.dll RESOLVED as an off-the-shelf general-purpose Ethernet motion-control SDK, 232 exports of which DMatrix_App.exe uses only 13; the rest covers buffered/coordinated motion, gearing, PWM, encoder capture, FPGA/ARM firmware download, a laser-marking galvo subsystem and a parts-sorting subsystem — none instrument-specific. Vendor unidentified (no PDB, no copyright string). CORRECTION to §12: MCDLL_NET.dll imports wpcap.dll, so WinPcap is NOT dead weight — it is the axis controller's raw-Ethernet (layer-2) transport; chain is DMatrix_App.exe -> MCDLL_NET.dll -> wpcap.dll -> Packet.dll, and '_Net' means Ethernet, not .NET. Transport disassembled (0x180007aa0-0x180007e14): one-shot pcap_findalldevs into a 16-slot adapter table, pcap_open(snaplen=0x10000, flags=0x19 = PROMISCUOUS|NOCAPTURE_LOCAL|MAX_RESPONSIVENESS, to_ms=-1), single global pcap handle, pcap_sendpacket, single unretried pcap_next_ex. MCF_Open_Net (RVA 0x1d050) wraps dispatcher cmd 0x0e then 0x2a; discovery loop at 0x18001bdc9 walks EVERY adapter in pcap_findalldevs order, opens each promiscuously, broadcasts a probe and polls ~100 x 1 ms for a reply before moving on. Four disassembly-confirmed fragilities give a candidate mechanism for the ~56% axis init failure rate: (a) NO BPF filter at all — pcap_compile/pcap_setfilter/pcap_datalink are not imported, so the promiscuous receive returns whatever frame arrives first; (b) receive is a single unretried call with only a length check at the caller (0x1800227c4); (c) to_ms=-1 is undocumented in WinPcap; (d) adapter identity is positional in an unstable enumeration order, capped at 16. Prediction stated: failure rate should track background broadcast/multicast volume on the axis NIC and drop on a dedicated point-to-point link. Explicitly NOT confirmed — nothing observed running. Also noted: close path appears not to clear the global handle (verification interrupted, flagged as unconfirmed). (16) MicrofluidicsInterFace.dll documented: 57 exports, imports only InterFace.dll + Qt5Core (NO libusb — transport lives in the missing InterFace.dll, structurally different route to the same hardware than DLLTest.dll's direct libusb path). Protocol confirmed CRC16-framed with 0xAA message headers (command IDs AA0101...AB0301 in the string table), cross-linking to §2's finding that DLLTest.dll's InquireVolt validates a 0xAA header byte — same wire format, different host plumbing. Log literals ('Electrify Success', 'IC Noraml', 'PownOn Success', 'CRC Check Error') identify this DLL as the source of §4's 3,889 Electrify Success events. Full capability comparison table vs DLLTest.dll's 7 Python-facing exports written: Python has no frame/timing control, no polarity, no frequency control, no chip-type selection, no IC/model/version query, no device reset, no status polling, no firmware update, no raw QByteArray escape hatch, and no typed error signals. Most consequential: the Python layer cannot query chip health at all, only command it — plausibly part of why §1's loop is gated on human input() calls. A SendElectrify overload taking QVector<QRect> is the closest match yet to the .Acx 9-field drop record (§9), mapping still unproven. (17) MediaProcess.dll (MV_MP_* exports, PDB under G:\Product\Component\, bundles libjpeg-turbo/libpng 1.6.34/zlib 1.2.11, links swscale-4.dll) and MathParser_MD_VC120_v3_0_MVS_v3_1_0.dll (GenICam v3.0.1 expression evaluator) both independently corroborate §11's correction that the camera stack is Hikrobot MVS/GenICam, not MVTec Halcon; also explains swscale-4.dll's presence. (18) Boilerplate logged in full rather than skipped: msvcp90 (VC9, 3181 exports), msvcm90 (VC9 managed C++/CLI bridge, imports mscoree — consistent with PathAlgorithm.dll being mixed-mode), msvcp120 (VC12, 1569), msvcp140 (VC14/UCRT, 1515), plus both VC90 side-by-side manifests. Three flags raised: the bundle spans four MSVC generations; a non-redistributable *DebugCRT* manifest is shipped (suggesting the vendor's packaging is not a clean release build); and MSVCR90/120/140 are all absent while their C++-library counterparts are present. (19) Missing-artifact list updated — MCDLL_NET.dll and MicrofluidicsInterFace.dll now resolved; InterFace.dll is now unambiguously the single highest-value missing file since both §10 and §16 terminate there. Held as background per standing researcher instruction; inputs/research_plan.md untouched and still the empty template.
[2026-08-05T22:22:28.610806+00:00] Fifth batch of raw_data additions (large DMCtrl3.5.2 drop: ~40 files + 7 Qt plugin folders) analyzed; workspace/analysis.md extended with §20-§29 and §6/§11/§12/§13/§19 corrected. (20) InterFace.dll RESOLVED — the long-standing #1 missing file. Exports two classes: InterFaceTemplate (base for all four subsystem interfaces, with protected ModbusCRC16(QByteArray) and SendModbusCmd helpers) and InterFace, a QThread-derived transport engine with MainPro/PeriodSend/PeriodRecv/QueueSendData/Reconnection. Answers the open protocol question from §10 and §16: the subsystem wire format is Modbus framing with CRC16. CORRECTION to §6 and §12: InterFace.dll implements FOUR transports — USB (libusb-1.0), serial (Qt5SerialPort), TCP client AND TCP server (Qt5Network) — so the 192.168.0.2:60001 network chip mode is a real supported code path, not vestigial as previously concluded; the 'never actually exercised in this deployment' half of §6 still stands (no network session in ~128MB of logs). Also imports SETUPAPI for device enumeration, explaining the DeviceInstanceId config keys. (21) AxisInterFace.dll exists and IS an InterFaceTemplate subclass (strings: AxisInterFace, AnalysisData, slot_MainPro) built in a DIFFERENT source tree (Project\ACXEL\ACXEL\Communication vs CommonOutput\communication) — but its PE Export Directory is RVA 0 size 0 (genuinely zero exports, verified against TempControlInterFace as contrast) and DMatrix_App.exe does not import it. So the §13 'axis is the architectural outlier' framing STANDS for this build; the reinterpretation is 'an axis interface exists in a sibling branch and this build does not use it'. Retry-3-times logic located: it is in the EXE (DM_Axis), strings 'Failed to Open the Axis 1 0 2', 'Failed to Open the Axis 3 Times', 'Open the Axis 1 0 2 successfully' — hardcoded parameter triple, no axis card/adapter field in Config.ini. (22) AxisCache.dat is a cached axis geometry/teach-point INI (SensorPos, TopZ, Throw, Box_*, Hole0 with a Chinese label) — all zeros except two Z values of 151587081 = 0x09090909, an uninitialized-filler pattern suggesting the axis was never successfully taught on this machine; contains NO adapter/NIC/MAC/connection data, so it does NOT relate to the §15 adapter-discovery mechanism. Hidden .dat is a one-step temperature program ([Grounp] typo, Temp=25.00/25.00, Delay, LoopNum) matching .Acx type-3 records. Cache/Version.dat is XOR-0x88 obfuscated ASCII decoding uniquely to 'V3.3:V3.4.2' (brute-forced all 256 XOR/ADD keys; stale vs the DMCtrl3.5.2 folder name). (23) DumpCrashes/ is EMPTY — zero files, one empty 'dump' subdir, hidden entries checked. Negative result reported plainly: the crash-dump gap named in §13 remains open. Its existence does confirm the app has a dump facility and configured path, so absence is itself weakly informative; recommend asking the researcher whether the folder was cleared before copying. (24) NEW CAPABILITY — onnx/ contains five real YOLOv5 ONNX detectors (best1, cell_droplet, droplet, mark, single_droplet; 28-30MB each), metadata extracted by hand-written protobuf parser (onnx package unavailable, §5 scratch venv gone): PyTorch 1.12.1/1.13.0/2.0.0 exports, opset 10/12, inputs 'images' [1,3,1280,1280] or [1,3,640,640], outputs [1,100800,6|8] / [1,25200,6] / [1,800,6], anchor counts confirming input sizes, op profile (Conv/Sigmoid/Mul SiLU + 3 MaxPool SPPF) confirming YOLOv5. Inference IS wired into DMatrix_App.exe via OpenCV DNN (readNet/blobFromImage/setInput/forward/getUnconnectedOutLayersNames/NMSBoxes), NOT onnxruntime; RTTI classes Yolo and YoloMark, checkBox_Detect UI, and runtime strings 'Move Drop Detect Error', 'Wait Drop Detect Error', 'An abnormality has been detected. Please reinsert or replace the biochip.' — i.e. closed-loop droplet detection the SULI Python stack (HSV thresholding only) does not have. OPERATIONAL DEFECT FOUND: the exe hardcodes ./onnx/multi_droplet.onnx and ./names/multi_droplet.names, neither of which exists on disk; and cell_droplet.onnx emits 3 classes against a 1-label .names file. Flagged as a second independent candidate explanation for the researcher's 'app feels unreliable' field note, unrelated to the axis. (25) camHalcon.dll SETTLED: it is ACX's own wrapper (PDB AcxelSoft\trunk\CommonOutput\camHalcon\), exports the CMvCamera class, imports only MVCameraControl.dll, and includes GigE (ForceIp, GetGevAllMatchInfo) and USB3 (GetU3VAllMatchInfo) helpers. No MVTec Halcon code exists anywhere in the bundle — §11's correction is confirmed and its open caveat resolved as no. (26) MultiAgentPathPlanning.dll is a pure managed .NET assembly (imports only mscoree, PDB Code\MAPF\...\obj\Release) confirming EVERY part of §5's IL-derived inference: types SIPPMapf/SIPPEnvironment/SIPPState/SIPPAction/AStarSIPP, entry point Planning_export, safe-interval machinery (FindSafeInterval, IntervalIntersection, GlobalCollision, SetCollisionIntervals), search internals (AdmissibleHeuristic, GScore/FScore/Fmin), problem model (Drops, Goals, Obstacles, SafetyDistance, DimX/DimY, CurrentAgentId). Two things beyond §5: an explicit SafetyDistance parameter (minimum droplet separation, physically meaningful for AM-DMF merge avoidance), and its own file logging (AppendLog, WriteGlobalCollisionToFile). (27) Hikrobot MVS cluster complete — MvCameraControl (198 exports, SDK core), MVGigEVisionSDK, MvUsb3vTL, MvProducerGEV/U3V.cti GenTL producers, MvCamLVision, MvRender, MvDSS.ax DirectShow filter, FormatConversion, plus the GenICam v3.0.1 set (GenApi 471, NodeMapData 182, XmlParser, MathParser, Log, log4cpp) and the Camera Link trio (CLAllSerial/CLProtocol/CLSerCOM). Camera Link components are shipped-but-unused given camHalcon exposes only GigE/USB3 helpers. FFmpeg (avcodec-57/avformat-57 Lavf57.41.100/avutil-55/avfilter-6/avdevice-57/swscale-4) confirmed as a stock full GPL build serving MediaProcess.dll and Qt Multimedia's UI preview/recording path, not the measurement path. (28) Boilerplate logged in full: seven Qt plugin folders (platforms/qwindows, styles, imageformats x9, iconengines, bearer, mediaservice, translations x22) — noting mediaservice ships DEBUG variants alongside release, matching the DebugCRT sloppiness flagged in §18, and that zh_TW is present but zh_CN is not despite Chinese strings elsewhere; runtimes msvcr90/msvcr120/concrt140; log4qt.dll (1183 exports, resolves a §14/§19 gap); libEGL/libGLESv2 (Qt ANGLE); libmmd (Intel C++ math runtime); LightSystemInterFace and MagnetInterFace. (29) Missing-artifact list updated — InterFace.dll, camHalcon.dll, MvCameraControl, NodeMapData, log4qt, MSVCR90/120 all resolved. Still outstanding: multi_droplet.onnx/.names (a functional defect, not an analysis gap), Qt5Gui.dll (last missing hard dependency of the exe), MSVCR140.dll, and crash dumps. No analysis is now blocked on a missing file. Held as background per standing researcher instruction; inputs/research_plan.md untouched and still the empty template.
[2026-08-06T17:15:59.398835+00:00] Researcher-stated priorities captured and written to spec/objectives.md (new file; spec/design.md NOT modified, deltas recorded not applied). Plan only — no code written, no individual script designed. Priority order given by researcher: (P1) electrode actuation visualization + chip health/coverage evaluation, to be designed and signed off before anything else; (P2) real axis control via direct ctypes to MCDLL_NET.dll with honest error reporting against the ~56% init failure rate; (P3) a dedicated minimum-size droplet-splitting script pushing below what 1pixsplit.py/dropsplitoff.py achieve; (P4) remaining subsystems (temp/light/magnet) and L3/L4 layers, order TBD. Two standing requirements apply across all priorities: (a) the rebuild must improve on the existing project/ scripts rather than wrap them — §1's six defects (duplicated Drop struct/DLL load across 13 scripts, hardcoded path at 1pixsplit.py:37, input()-gated non-autonomous flow at chipsetup.py:30-58, bare print() with no surviving record, USB handle leaks on exception, no health readback) are turned into per-item requirements; (b) CAMERA POLICY — new code uses the researcher's own camera via the existing project/camera.py connection (cv2.VideoCapture + CAP_DSHOW + MJPG), modelled after CameraInterface's structure, and must NOT depend on or route through MvCameraControl.dll / camHalcon.dll / any Hikrobot MVS component (§11, §25, §27); the five vendor YOLOv5 ONNX detectors (§24) are explicitly not adopted, detection stays HSV/contour per detect_drop_color. KEY DESIGN CONSTRAINT SURFACED FOR P1: chip health cannot be queried, only inferred optically, because there is no per-electrode readback in ANY vendor API — ActivateElec sets the entire 128x128 frame in one shot with no read-back of electrode state (§2), and InquireVolt returns 9 global rails, not per-electrode data; MicrofluidicsInterFace.dll's SendReadICState/SendPolling (§16) would add a device-reported health channel but is shim-gated and would still not give per-electrode state. Corollary flagged as the #1 open question: a dead electrode is only observable where liquid is present to move, so unenergised-and-dry is optically indistinguishable from working-and-dry — how liquid is present during a coverage sweep determines the entire method and requires researcher input. P1 is therefore scoped to need zero shim and zero native build, using only DLLTest.dll's 7 flat-C exports, with 'unknown' as a first-class coverage outcome distinct from 'fail'. Five open questions raised for P1 (liquid presence method; definition of 'functioning'; coverage granularity per-electrode vs per-block; confirmation of 128x128 geometry and electrode pitch for px->electrode calibration; arming/safety default). Eight deltas to spec/design.md recorded in objectives.md §6, pending researcher approval before applying: §5.3 (MvCameraControl.dll camera binding) CANCELLED and §6 CameraPair CANCELLED per the camera policy; §9 q4 (camera) ANSWERED; §9 q1 (shim feasibility) DEFERRED and now cheaper to defer since P1-P3 all need no native build, with P3 expected to produce evidence on whether missing timing/polarity/frequency control (§16) is the real binding constraint on droplet size; §9 q2 (release scope) effectively answered as the no-native-build subset; §9 q3 (legacy scripts) STILL OPEN; §9 q5 (arming default) STILL OPEN and now the one §9 question blocking P1, because a health sweep energises broadly across the chip; §10's 'L0 + L1 axis only' first step SUPERSEDED by the researcher's ordering. One structural change to camera.py's pattern identified and deferred to the P1 gate rather than assumed: take_picture opens and releases the device per call (camera.py:45-57), which cannot support a live-view loop, so the new camera object must hold the capture open across frames — a change to lifetime only, not to the connection method or the camera. Separately recommended but not adopted: camera.py:23 enables autofocus, a measurement-variance risk already flagged in design.md §9 q4. Axis material for P2 restated from §13/§15/§15.1/§15.4/§21/§22 with the §15.4 promiscuous-receive/adapter-ordering account labelled explicitly as HYPOTHESIS, NOT FINDING (nothing observed running); a diagnostic to test its prediction (failure rate should track background broadcast volume and drop on a dedicated point-to-point link) is offered for the P2 gate, not assumed. Process requirement recorded: researcher requires an explicit confirmation gate before DESIGNING and again before CREATING each individual script, one at a time in priority order, not batched. inputs/research_plan.md remains untouched and still the empty template per the standing hold.
