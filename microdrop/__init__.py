"""Priority 2 -- droplet splitting (docs/spec/objectives.md §2).

Pure planning only. Nothing in this package loads the DLL, opens the USB
connection or energises an electrode; `splitplan` emits frames and an executor
feeds them to `chiphealth.actuation.ChipController`. That split keeps the
geometry testable on a machine with no rig, the same way `detector.py` and
`sweep.py` are (docs/spec/requirements.md, non-functional).

Parameters come from `csvvolcont.py`, not `1pixsplit.py` -- see `params.py`
for the trace and for what csvvolcont does *not* cover.
"""
