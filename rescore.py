"""Offline re-scoring of a saved chip-health run.

    python rescore.py runs/20260807T143537Z
    python rescore.py runs/20260807T143537Z --lag 1.5 --persist 2
    python rescore.py runs/20260807T143537Z --label <event_id> human_confirmed

Why this exists. The detector's thresholds are estimates -- there is no
ground-truth faulty region on this chip yet (docs/spec/objectives.md §1.4), so the
first runs are calibration rather than measurement. When the thresholds improve,
this replays every past run against the new ones: no hardware, no oil, no lost
history. That is what makes improving the detector cheap.

It replays the **saved observations**, not the video, so it needs neither a
camera nor OpenCV. The detector consumes extracted blobs, so replaying blobs
replays the decision layer exactly. Re-extracting from video would only be
needed if the blob extractor itself changed, which is a different and rarer
kind of change.

Labels. The live run only ever writes ``label_source: auto``. A model trained
purely on heuristic labels can at best learn to imitate the heuristic -- so
promoting a label to ``human_confirmed`` or ``human_corrected`` happens here,
and is what makes the dataset worth more than the detector that generated it
(docs/spec/objectives.md §1.8).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from chiphealth import DETECTOR_VERSION
from chiphealth.config import DetectorConfig
from chiphealth.detector import Blob, Detector, Observation
from chiphealth.recorder import CoverageMap
from chiphealth.sweep import Step


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def steps_from_timeline(rows: list[dict]) -> list[tuple[int, Step]]:
    """Reconstruct the exact commanded steps from the timeline.

    The timeline records the full window geometry per step, so the traversal is
    replayed as it happened rather than re-planned -- which matters if the run
    was aborted partway or used non-default geometry.

    Returns ``(seq, Step)`` pairs. The join key is ``seq``, not ``step.idx``:
    fine-pass legs are planned independently and restart their indices at 0, so
    ``step.idx`` repeats within a run.
    """
    return [(r.get("seq", i + 1),
             Step(idx=r["step"], row=r["row"], col=r["col"], h=r["h"], w=r["w"],
                  axis=r["axis"], direction=r["direction"], kind=r["kind"],
                  band=r["band"]))
            for i, r in enumerate(rows)]


def observations_from_rows(rows: list[dict]) -> dict[int, Observation]:
    out: dict[int, Observation] = {}
    for i, r in enumerate(rows):
        blobs = tuple(Blob(**b) for b in r["blobs"])
        out[r.get("seq", i + 1)] = Observation(
            step_idx=r["step"], frame_index=r["frame_index"], t=r["t"], blobs=blobs)
    return out


def rescore(run_dir: Path, cfg: DetectorConfig, block: int,
            chip_rows: int, chip_cols: int) -> dict:
    steps = steps_from_timeline(load_jsonl(run_dir / "timeline.jsonl"))
    obs_by_step = observations_from_rows(load_jsonl(run_dir / "observations.jsonl"))
    if not steps:
        raise SystemExit(f"{run_dir}: no timeline.jsonl -- nothing to replay.")
    if not obs_by_step:
        raise SystemExit(
            f"{run_dir}: no observations.jsonl. This run predates observation "
            f"logging, so it can only be re-scored from video.")

    det = Detector(cfg, block=block, stage="rescore")
    coverage = CoverageMap(chip_rows, chip_cols, block)
    events = []
    replayed = 0
    for seq, step in steps:
        obs = obs_by_step.get(seq)
        if obs is None:
            continue
        replayed += 1
        res = det.observe(step, obs)
        coverage.mark_tested(res.tested_blocks)
        for ev in res.events:
            coverage.mark_event(ev.kind, ev.block_row, ev.block_col)
            events.append(ev)
    return {"events": events, "coverage": coverage, "steps": len(steps),
            "replayed": replayed}


def compare(original: list[dict], new: list) -> dict:
    """Diff against the original labels, keyed by (kind, block)."""
    def key(k, br, bc):
        return f"{k}@{br},{bc}"
    old_keys = {key(e["kind"], e["block_row"], e["block_col"])
                for e in original if e.get("sample") != "negative"}
    new_keys = {key(e.kind, e.block_row, e.block_col) for e in new}
    return {
        "original": len(old_keys),
        "rescored": len(new_keys),
        "gained": sorted(new_keys - old_keys),
        "lost": sorted(old_keys - new_keys),
        "kept": len(old_keys & new_keys),
    }


def promote_label(run_dir: Path, event_id: str, label: str) -> bool:
    """Set one event's ``label_source``, rewriting events.jsonl in place."""
    valid = {"auto", "human_confirmed", "human_corrected"}
    if label not in valid:
        raise SystemExit(f"label must be one of {sorted(valid)}")
    path = run_dir / "events.jsonl"
    rows = load_jsonl(path)
    found = False
    for r in rows:
        if r.get("event_id") == event_id:
            r["label_source"] = label
            found = True
    if found:
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return found


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="rescore.py",
        description="Re-score a saved chip-health run offline. No hardware, no "
                    "camera, no OpenCV.")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--lag", type=float, default=None,
                   help="Drag threshold in electrodes (default from the run).")
    p.add_argument("--persist", type=int, default=None,
                   help="Consecutive steps a lag must hold to count.")
    p.add_argument("--residue", type=float, default=None,
                   help="Minimum residue area in electrodes.")
    p.add_argument("--no-move-steps", type=int, default=None)
    p.add_argument("--block", type=int, default=None)
    p.add_argument("--write", action="store_true",
                   help="Write rescored/events_v<N>.jsonl instead of only reporting.")
    p.add_argument("--label", nargs=2, metavar=("EVENT_ID", "SOURCE"),
                   help="Promote one event's label: auto | human_confirmed | "
                        "human_corrected.")
    args = p.parse_args(argv)

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        raise SystemExit(f"{run_dir} is not a directory")

    if args.label:
        event_id, label = args.label
        ok = promote_label(run_dir, event_id, label)
        print(f"{'set' if ok else 'NOT FOUND'}: {event_id} -> {label}")
        return 0 if ok else 1

    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    saved = meta.get("config", {})
    chip = saved.get("chip", {})
    block = args.block or saved.get("sweep", {}).get("block", 4)

    base = saved.get("detector", {})
    cfg = DetectorConfig(**{k: v for k, v in base.items()
                            if k in DetectorConfig.__dataclass_fields__})
    if args.lag is not None:
        cfg = replace(cfg, lag_electrodes=args.lag)
    if args.persist is not None:
        cfg = replace(cfg, lag_persist_steps=args.persist)
    if args.residue is not None:
        cfg = replace(cfg, residue_min_area_electrodes=args.residue)
    if args.no_move_steps is not None:
        cfg = replace(cfg, no_move_steps=args.no_move_steps)

    result = rescore(run_dir, cfg, block,
                     chip.get("rows", 128), chip.get("cols", 128))
    diff = compare(load_jsonl(run_dir / "events.jsonl"), result["events"])

    print(f"run:        {run_dir.name}  chip={meta.get('chip_id')}")
    print(f"replayed:   {result['replayed']}/{result['steps']} steps")
    print(f"thresholds: lag>={cfg.lag_electrodes} for {cfg.lag_persist_steps} steps, "
          f"residue>={cfg.residue_min_area_electrodes}")
    print(f"events:     {diff['original']} original -> {diff['rescored']} rescored "
          f"({diff['kept']} kept, +{len(diff['gained'])}, -{len(diff['lost'])})")
    print(f"coverage:   {result['coverage'].counts()}")
    if diff["gained"]:
        print(f"  gained: {diff['gained'][:10]}"
              + (" ..." if len(diff["gained"]) > 10 else ""))
    if diff["lost"]:
        print(f"  lost:   {diff['lost'][:10]}"
              + (" ..." if len(diff["lost"]) > 10 else ""))

    if args.write:
        out_dir = run_dir / "rescored"
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"events_v{DETECTOR_VERSION}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for ev in result["events"]:
                rec = ev.to_dict()
                rec.update({"run_id": meta.get("run_id"),
                            "chip_id": meta.get("chip_id"),
                            "rescored": True})
                fh.write(json.dumps(rec) + "\n")
        (out_dir / f"coverage_v{DETECTOR_VERSION}.json").write_text(
            json.dumps(result["coverage"].to_dict(), indent=2), encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
