# Reference — the axis motion stage (`MCDLL_NET.dll`)

**Verbatim extract from [`workspace/analysis.md`](../../workspace/analysis.md),
sections 15 and 22.** Pulled 2026-08-21 for reference. Nothing below is
rewritten, summarised or updated — this is the disassembly record as it was
written, and the analysis log remains the source of truth. If the two ever
disagree, the log wins.

## Why this material is filed separately

The axis was the **original Priority 2** and was dropped as an active priority
on 2026-08-12. The decision, its rationale, and what would reopen it live in
[`docs/spec/objectives.md` Appendix A](../spec/objectives.md) (lines 643-727).
This file holds the *evidence* that appendix rests on, so it can be cited
without reading a 663-line log.

**The expensive part of this work is finished.** 232 exports narrowed to the 13
the vendor app uses, transport identified as raw Ethernet over WinPcap, and a
named, falsifiable failure mechanism for the ~56% init failure rate. Picking it
back up costs re-reading, not re-deriving.

## What is a finding and what is a hypothesis

Worth reading before citing anything below:

- **Findings** — the export list, the pcap import list, the disassembled
  addresses and call sequences, the flag decodings, the file contents. These
  come from static analysis of binaries that will not change.
- **Hypothesis** — §15.4's account of *why* the axis fails ~56% of the time.
  Nothing was ever observed running: no crash dumps, no error-path
  disassembly, no observation of the app under failure. §15.4 says so itself.
- The log also warns against over-reading the failure rate: the chip,
  temperature and camera paths succeeded at high rates over the same period,
  so "the app is buggy" is not attributable to those.

**Cheapest test, and it is not software.** If the promiscuous-receive
contention mechanism is right, moving the controller to a dedicated
point-to-point NIC should drop the failure rate sharply. That is a cabling
change, and it is worth trying before any of this work is reopened.

## Related sections not extracted here

- **§13** — the original axis finding and the researcher field note on app
  reliability (analysis.md lines 208-227)
- **§21** — `AxisInterFace.dll`: it exists, it is **not loaded**, and the
  retry-3-times logic is in the executable rather than a driver layer
  (lines 425-447). Relevant because a Python rewrite gets no retry for free.

---

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

---

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
