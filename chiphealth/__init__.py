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

# Artifact layout. Unchanged at 1: run.json gained a `code_version` field on
# 2026-08-12, but that is additive and the event records are untouched, so
# bumping this would wrongly signal that older artifacts cannot be read.
# Readers should treat `code_version` as absent on runs before that date.
SCHEMA_VERSION = 1

# Which detector logic produced a verdict. Stamped on every event and used to
# name rescore output (`events_v{N}.jsonl`).
#
#   1  scores every commanded frame.
#   2  skips KIND_RELEASE frames (ab25606). A release drops the trailing edge
#      and energises nothing new, so there is no leading edge to measure -- and
#      it lands before the liquid has had a step to reflow, so judging residue
#      there flags liquid that is merely still moving.
#
# Note on comparability, which is narrower than it first looks: re-scoring a
# PRE-caterpillar run with v2 gives identical results, because those runs
# contain no release frames for the new branch to skip. The direction that
# matters is the other one -- v1 logic turned loose on a caterpillar run would
# score every release frame and produce nonsense. This number is what stops
# that going unnoticed.
DETECTOR_VERSION = 2
