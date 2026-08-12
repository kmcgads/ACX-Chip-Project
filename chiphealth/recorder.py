"""Run artifacts: video, stills, events, coverage map, summary.

Standard library only. Image and video encoding are **injected** as callables,
so the schema and cadence logic are testable on a machine with no OpenCV
(spec/p1_build_status.md).

The artifact is not a log. Its stated purpose is longitudinal: tracking device
performance over time, and accumulating a labelled dataset to eventually train a
model to recognise sticky-spot behaviour (spec/objectives.md §1.8). That makes
schema stability a design constraint, not housekeeping -- runs compared months
apart must have the same field names and recorded parameters.

Layout (spec/p1_chip_health_design.md §6.1):

    runs/<run_id>/
      run.json           params, chip_id, environment, versions, prompts
      timeline.jsonl     one record per step
      events.jsonl       one record per trouble event
      stills/routine/    5-second cadence
      stills/flagged/    event-associated
      events/            <event_id>_roi.jpg + <event_id>_full.jpg
      video.mkv
      baseline/
      coverage.json      32x32 block verdict map
      summary.md
"""

from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import DETECTOR_VERSION, SCHEMA_VERSION
from .sweep import block_bounds, block_grid_shape


def code_version() -> dict:
    """Which code produced this run. Best effort, and honest when it cannot tell.

    ``schema_version`` and ``detector_version`` are coarse: they only change
    when someone remembers to change them. The git commit is exact, and this
    artifact's whole purpose is being read months later -- at which point
    "which code produced this?" has no other answer.

    **``dirty`` matters as much as the hash.** A commit identifies the code only
    if the working tree was clean; with uncommitted edits the SHA names the code
    the run did *not* use. Recording the hash alone would be worse than
    recording nothing, because it looks authoritative.

    Never raises and never silently omits: git missing, not a repository, or a
    timeout all record ``commit: None`` with the reason, so the artifact says
    "unknown" rather than leaving a reader to assume it was simply not captured.
    """
    root = Path(__file__).resolve().parent.parent

    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(("git", "-C", str(root)) + args,
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        # rstrip("\n") only. A bare .strip() eats the leading space of
        # `git status --porcelain`'s first line -- its format is a two-column
        # status field then a space -- which silently corrupted the first path.
        return r.stdout.rstrip("\n") if r.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "dirty": None,
                "note": "no git commit available -- git missing, not a "
                        "repository, or the command failed"}
    commit = commit.strip()
    status = git("status", "--porcelain")
    if status is None:
        return {"commit": commit, "dirty": None,
                "note": "commit read but working-tree state unknown, so this "
                        "hash may not be the code that ran"}
    lines = [ln for ln in status.splitlines() if ln.strip()]
    out = {"commit": commit, "dirty": bool(lines)}
    if lines:
        out["note"] = ("UNCOMMITTED CHANGES were present, so this commit does "
                       "NOT identify the code that ran")
        # Whole porcelain lines, status column included, so a reader can tell a
        # modified tracked file (" M path") from untracked noise ("?? path").
        # Not sliced: the paths are the point, but so is which kind of change.
        out["dirty_files"] = sorted(lines)[:50]
        if len(lines) > 50:
            out["dirty_files_truncated"] = len(lines) - 50
    return out

# Verdicts, worst-wins. "unknown" is a first-class outcome, not a synonym for
# "fine" -- a region never covered by liquid was never tested, and reporting it
# as healthy would be a lie (spec/objectives.md §1.3).
UNKNOWN = "unknown"
PASS = "pass"
DEGRADED = "degraded"
FAIL = "fail"

_SEVERITY = {UNKNOWN: 0, PASS: 1, DEGRADED: 2, FAIL: 3}

# Which detector kinds condemn a block outright vs. merely degrade it.
_KIND_VERDICT = {
    "drag": DEGRADED,
    "residue": DEGRADED,
    "no_movement": FAIL,
    "unreachable": FAIL,
}


class CoverageMap:
    """Per-block verdicts over the whole chip. 128x128 at block=4 -> 32x32."""

    def __init__(self, chip_rows: int, chip_cols: int, block: int) -> None:
        self.block = block
        self.rows, self.cols = block_grid_shape(chip_rows, chip_cols, block)
        self._grid = [[UNKNOWN] * self.cols for _ in range(self.rows)]
        # Electrode rows/cols no band ever reaches. These CANNOT be expressed in
        # the block grid: with a 4-electrode block, row 1 being untested is
        # hidden the moment rows 2-4 are swept and the block turns `pass`. So
        # they are carried alongside it and printed in the summary -- otherwise
        # the map would quietly claim coverage it does not have.
        self.never_covered_rows: list[int] = []
        self.never_covered_cols: list[int] = []

    def get(self, br: int, bc: int) -> str:
        return self._grid[br][bc]

    def mark(self, br: int, bc: int, verdict: str) -> None:
        """Worst verdict wins, so a later clean pass cannot erase a fault."""
        if not (0 <= br < self.rows and 0 <= bc < self.cols):
            return
        if _SEVERITY[verdict] > _SEVERITY[self._grid[br][bc]]:
            self._grid[br][bc] = verdict

    def mark_tested(self, blocks) -> None:
        for br, bc in blocks:
            self.mark(br, bc, PASS)

    def mark_event(self, kind: str, br: int, bc: int) -> None:
        self.mark(br, bc, _KIND_VERDICT.get(kind, DEGRADED))

    def counts(self) -> dict[str, int]:
        out = {UNKNOWN: 0, PASS: 0, DEGRADED: 0, FAIL: 0}
        for row in self._grid:
            for v in row:
                out[v] += 1
        return out

    def suspicious_blocks(self) -> list[tuple[int, int]]:
        return [(br, bc) for br in range(self.rows) for bc in range(self.cols)
                if self._grid[br][bc] in (DEGRADED, FAIL)]

    def to_dict(self) -> dict:
        return {
            "block": self.block,
            "rows": self.rows,
            "cols": self.cols,
            "counts": self.counts(),
            "never_covered_rows": list(self.never_covered_rows),
            "never_covered_cols": list(self.never_covered_cols),
            "grid": self._grid,
        }


@dataclass
class RunPaths:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.stills_routine = self.root / "stills" / "routine"
        self.stills_flagged = self.root / "stills" / "flagged"
        self.events_dir = self.root / "events"
        self.baseline = self.root / "baseline"
        self.run_json = self.root / "run.json"
        self.timeline = self.root / "timeline.jsonl"
        self.observations = self.root / "observations.jsonl"
        self.events_jsonl = self.root / "events.jsonl"
        self.rescored = self.root / "rescored"
        self.coverage = self.root / "coverage.json"
        self.summary = self.root / "summary.md"
        self.video = self.root / "video.mkv"

    def mkdirs(self) -> None:
        for d in (self.root, self.stills_routine, self.stills_flagged,
                  self.events_dir, self.baseline):
            d.mkdir(parents=True, exist_ok=True)


class RunRecorder:
    """Writes one run's artifacts.

    Args:
        image_writer: ``(path, frame) -> None``. Injected so the cadence and
            schema can be tested without OpenCV. When ``None``, images are
            counted as skipped rather than silently dropped.
    """

    def __init__(self, cfg, run_id: str, chip_id: str,
                 image_writer=None, rng_seed: int = 0) -> None:
        self.cfg = cfg
        self.run_id = run_id
        self.chip_id = chip_id
        self.paths = RunPaths(Path(cfg.runs_root) / run_id)
        self._write_image = image_writer
        self.rng = random.Random(rng_seed)
        self.rng_seed = rng_seed

        self.coverage = CoverageMap(cfg.chip.rows, cfg.chip.cols, cfg.sweep.block)

        self.n_steps = 0
        self.n_events = 0
        self.n_routine_stills = 0
        self.n_flagged_stills = 0
        self.n_negatives = 0
        self.images_skipped = 0
        self.prompts: list[dict] = []
        self.notes: list[str] = []
        self.calibration: dict | None = None
        self._last_still_t: float | None = None
        self._event_seq = 0
        self._seq = 0

    # ── setup ────────────────────────────────────────────────────────────────

    def start(self, extra: dict | None = None) -> None:
        self.paths.mkdirs()
        meta = {
            "run_id": self.run_id,
            "chip_id": self.chip_id,
            "schema_version": SCHEMA_VERSION,
            "detector_version": DETECTOR_VERSION,
            "code_version": code_version(),
            "rng_seed": self.rng_seed,
            "config": self.cfg.to_dict(),
        }
        if extra:
            meta.update(extra)
        self._write_json(self.paths.run_json, meta)

    # ── steps ────────────────────────────────────────────────────────────────

    def log_step(self, step, result) -> None:
        """One line per activation. Cheap, and it is what rescore replays.

        ``seq`` is a monotonic per-run counter and is the join key for the
        observation stream. ``step.idx`` is NOT unique across a run -- fine-pass
        legs are planned independently and restart from 0 -- so keying on it
        would silently pair coarse-pass steps with fine-pass observations.
        """
        self.n_steps += 1
        self._seq = self.n_steps
        self.coverage.mark_tested(result.tested_blocks)
        self._append_jsonl(self.paths.timeline, {
            "seq": self._seq,
            "step": step.idx,
            "row": step.row,
            "col": step.col,
            "h": step.h,
            "w": step.w,
            "axis": step.axis,
            "direction": step.direction,
            "kind": step.kind,
            "band": step.band,
            "leading_edge": step.leading_edge,
            "lag": result.lag,
            "primary_area": result.primary_area,
            "clean": result.clean,
        })

    def log_observation(self, step, obs) -> None:
        """Persist the extracted blobs for this step.

        This is what makes offline re-scoring possible **without a camera and
        without OpenCV**: the detector consumes blobs, so replaying them replays
        the decision layer exactly. Re-extracting from the video is only needed
        when the blob extractor itself changes, not when a threshold does.

        Cheap: a few hundred bytes per step, against tens of megabytes of video.
        """
        self._append_jsonl(self.paths.observations, {
            "seq": self._seq,
            "step": step.idx,
            "frame_index": obs.frame_index,
            "t": obs.t,
            "blobs": [
                {"centroid_row": b.centroid_row, "centroid_col": b.centroid_col,
                 "area_electrodes": b.area_electrodes, "row": b.row, "col": b.col,
                 "height": b.height, "width": b.width}
                for b in obs.blobs
            ],
        })

    # ── events ───────────────────────────────────────────────────────────────

    def log_event(self, event, full_frame=None, roi=None) -> str:
        """Record a trouble event: structured row plus its images.

        Every trouble event produces a saved image and a data entry -- a core
        requirement, not an afterthought (spec/objectives.md §1.8).
        """
        self._event_seq += 1
        event_id = f"{self.run_id}_e{self._event_seq:04d}"
        self.n_events += 1
        self.coverage.mark_event(event.kind, event.block_row, event.block_col)

        rec = event.to_dict()
        rec.update({
            "event_id": event_id,
            "run_id": self.run_id,
            "chip_id": self.chip_id,
            "schema_version": SCHEMA_VERSION,
            "voltage": list(self.cfg.chip.volts),
            "step_delay_s": self.cfg.sweep.step_delay_s,
            "autofocus": self.cfg.capture.autofocus,
            "block": self.cfg.sweep.block,
            "sample": "auto",
        })
        self._append_jsonl(self.paths.events_jsonl, rec)

        if full_frame is not None:
            self._save(self.paths.events_dir / f"{event_id}_full.jpg", full_frame)
        if roi is not None:
            self._save(self.paths.events_dir / f"{event_id}_roi.jpg", roi)
        return event_id

    # ── stills ───────────────────────────────────────────────────────────────

    def should_capture_still(self, t: float) -> bool:
        """Routine cadence: one still every ``still_interval_s`` (default 5s)."""
        if self._last_still_t is None:
            return True
        return (t - self._last_still_t) >= self.cfg.capture.still_interval_s

    def capture_still(self, t: float, frame, flagged: bool = False) -> Path:
        """Save a still. Routine and flagged go to separate directories.

        Keeping them apart is the point: the researcher reviews the flagged set
        to teach the detector, and mixing 5-second routine frames into it would
        bury the interesting ones.

        A flagged capture does **not** reset the routine cadence. The two
        streams are independent: an event firing must not create a 5-second hole
        in the uniform time series.
        """
        if not flagged:
            self._last_still_t = t
        stem = f"{self.run_id}_t{t:09.3f}".replace(".", "_")
        if flagged:
            self.n_flagged_stills += 1
            path = self.paths.stills_flagged / f"{stem}.jpg"
        else:
            self.n_routine_stills += 1
            path = self.paths.stills_routine / f"{stem}.jpg"
        self._save(path, frame)
        return path

    def maybe_sample_negative(self, step, frame) -> Path | None:
        """Save a clean example, at random, as a matched negative.

        A dataset of only trouble events cannot train a classifier -- there is
        nothing to contrast against (spec/objectives.md §1.8). Sampling is
        seeded so a run is reproducible.
        """
        if frame is None or self.rng.random() >= self.cfg.capture.negative_sample_rate:
            return None
        self.n_negatives += 1
        path = self.paths.events_dir / f"{self.run_id}_neg{self.n_negatives:04d}.jpg"
        self._save(path, frame)
        self._append_jsonl(self.paths.events_jsonl, {
            "event_id": f"{self.run_id}_neg{self.n_negatives:04d}",
            "run_id": self.run_id,
            "chip_id": self.chip_id,
            "schema_version": SCHEMA_VERSION,
            "detector_version": DETECTOR_VERSION,
            "kind": "clean",
            "sample": "negative",
            "label_source": "auto",
            "step_idx": step.idx,
            "row": float(step.row),
            "col": float(step.col),
            "block_row": step.row // self.cfg.sweep.block,
            "block_col": step.col // self.cfg.sweep.block,
            "severity": 0.0,
            "detail": "sampled clean step",
            "stage": "coarse",
        })
        return path

    # ── operator interaction ─────────────────────────────────────────────────

    def log_prompt(self, asked: str, response: str, t: float) -> None:
        """Structured, logged prompts -- not a bare input() with a discarded return.

        Prompts are legitimate only where a physical human action is genuinely
        required: loading oil or sample, adjusting focus (spec/objectives.md §0.1).
        """
        self.prompts.append({"t": t, "asked": asked, "response": response})

    def record_calibration(self, cal: dict) -> None:
        """Store this run's registration: corners, scale, and drift.

        Goes into run.json so a bad region found months later can be told apart
        from a remount artifact, and so run-to-run variance in the longitudinal
        record is explicable rather than mysterious.
        """
        self.calibration = cal

    def note(self, text: str) -> None:
        """Anything the run should say out loud in its summary.

        Used for things that must never be silent -- fine-pass targets dropped
        by the cap, a failed probe split, uncovered rows.
        """
        self.notes.append(text)

    # ── finish ───────────────────────────────────────────────────────────────

    def finalize(self, extra: dict | None = None) -> dict:
        self._write_json(self.paths.coverage, self.coverage.to_dict())
        counts = self.coverage.counts()
        stats = {
            "run_id": self.run_id,
            "chip_id": self.chip_id,
            "steps": self.n_steps,
            "events": self.n_events,
            "routine_stills": self.n_routine_stills,
            "flagged_stills": self.n_flagged_stills,
            "negatives": self.n_negatives,
            "images_skipped": self.images_skipped,
            "coverage": counts,
        }
        if extra:
            stats.update(extra)
        self.paths.summary.write_text(self._summary_md(stats), encoding="utf-8")

        meta = json.loads(self.paths.run_json.read_text(encoding="utf-8"))
        meta["prompts"] = self.prompts
        meta["notes"] = self.notes
        meta["stats"] = stats
        meta["calibration"] = self.calibration
        self._write_json(self.paths.run_json, meta)
        return stats

    def _summary_md(self, stats: dict) -> str:
        c = stats["coverage"]
        total = sum(c.values()) or 1
        lines = [
            f"# Chip health run {self.run_id}",
            "",
            f"- chip: `{self.chip_id}`",
            f"- steps: {stats['steps']}",
            f"- trouble events: {stats['events']}",
            f"- stills: {stats['routine_stills']} routine, "
            f"{stats['flagged_stills']} flagged",
            f"- negatives sampled: {stats['negatives']}",
            "",
            "## Coverage",
            "",
            "| verdict | blocks | share |",
            "|---|---:|---:|",
        ]
        for key in (PASS, DEGRADED, FAIL, UNKNOWN):
            lines.append(f"| {key} | {c[key]} | {100.0 * c[key] / total:.1f}% |")
        lines += [
            "",
            "`unknown` means never tested -- not healthy. Every verdict here is "
            "optical inference; this chip reports no per-electrode state.",
        ]
        if self.coverage.never_covered_rows or self.coverage.never_covered_cols:
            lines += [
                "",
                "## Not reached by any band",
                "",
                "These are hidden by the block grid -- their block reads `pass` "
                "on the strength of its other electrodes:",
                "",
            ]
            if self.coverage.never_covered_rows:
                lines.append(f"- rows: {self.coverage.never_covered_rows}")
            if self.coverage.never_covered_cols:
                lines.append(f"- cols: {self.coverage.never_covered_cols}")
        if self.notes:
            lines += ["", "## Notes", ""] + [f"- {n}" for n in self.notes]
        if stats["images_skipped"]:
            lines += ["", f"> {stats['images_skipped']} images were not written "
                          f"(no image writer configured)."]
        return "\n".join(lines) + "\n"

    # ── io ───────────────────────────────────────────────────────────────────

    def _save(self, path: Path, frame) -> None:
        if self._write_image is None or frame is None:
            self.images_skipped += 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_image(str(path), frame)

    @staticmethod
    def _write_json(path: Path, obj: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _append_jsonl(path: Path, obj: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, default=str) + "\n")
