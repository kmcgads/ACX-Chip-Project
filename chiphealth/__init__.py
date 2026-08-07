"""Priority 1 — electrode actuation visualization + chip health / coverage check.

Design: spec/p1_chip_health_design.md
Priorities and standing requirements: spec/objectives.md
Vendor DLL findings: workspace/analysis.md

Layout (spec/p1_chip_health_design.md §3):

    config.py      paths, chip geometry, thresholds, run parameters
    geometry.py    electrode <-> pixel mapping                       (pure)
    sweep.py       band / serpentine / fine-pass path planning       (pure)
    detector.py    drag, residue, no-movement verdicts               (pure)
    actuation.py   DLLTest.dll binding, arming gate, fake backend
    recorder.py    run artifacts: video, stills, events, coverage
    run_health.py  orchestrator

`geometry`, `sweep` and `detector` are pure: standard library plus numpy, no
OpenCV and no hardware. That is what lets the detection logic be developed and
tested on a machine with neither (spec/design.md §7).
"""

__all__ = [
    "config",
    "geometry",
    "sweep",
    "detector",
    "actuation",
    "recorder",
]

SCHEMA_VERSION = 1
DETECTOR_VERSION = 1
