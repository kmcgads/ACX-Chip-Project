"""Failure detection: drag, residue, no-movement, unreachable.

Pure: standard library only. No OpenCV, no numpy, no hardware.

This module consumes **already-extracted blob observations in electrode
coordinates**, not raw pixels. Blob extraction needs OpenCV and lives in
``camera.py``; keeping the decision logic separate is what lets it be developed
and tested with neither a rig nor cv2 present, and is what ``rescore.py`` re-runs
over recorded video when the thresholds improve.

The three signatures were **observed on this rig by the researcher**, not
inferred: a droplet passing over a bad electrode visibly drags, and sometimes
leaves part of itself behind (docs/spec/objectives.md §1.7).

Every verdict is *observed versus commanded*. Nothing here interprets an
observation on its own, because there is no per-electrode readback to check it
against -- the commanded frame is the only ground truth available
(workspace/analysis.md §2, §16).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from . import DETECTOR_VERSION
from .sweep import AXIS_COL, KIND_RELEASE, Step, block_of, leading_edge_blocks

KIND_DRAG = "drag"
KIND_NO_MOVEMENT = "no_movement"
KIND_RESIDUE = "residue"
KIND_UNREACHABLE = "unreachable"

ALL_KINDS = (KIND_DRAG, KIND_NO_MOVEMENT, KIND_RESIDUE, KIND_UNREACHABLE)


@dataclass(frozen=True)
class Blob:
    """One detected liquid region, in electrode coordinates."""

    centroid_row: float
    centroid_col: float
    area_electrodes: float
    row: float   # bbox top
    col: float   # bbox left
    height: float
    width: float

    @property
    def row_max(self) -> float:
        return self.row + self.height

    @property
    def col_max(self) -> float:
        return self.col + self.width


@dataclass(frozen=True)
class Observation:
    """What the camera saw at one step."""

    step_idx: int
    frame_index: int
    t: float
    blobs: tuple[Blob, ...] = ()

    def primary(self) -> Blob | None:
        """The largest blob -- taken to be the droplet being driven."""
        return max(self.blobs, key=lambda b: b.area_electrodes, default=None)


@dataclass(frozen=True)
class Event:
    """A detected trouble event. One of these becomes one dataset record."""

    kind: str
    step_idx: int
    frame_index: int
    t: float
    row: float
    col: float
    block_row: int
    block_col: int
    severity: float
    detail: str
    stage: str = "coarse"
    detector_version: int = DETECTOR_VERSION
    label_source: str = "auto"  # only rescore.py ever promotes this

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ObserveResult:
    """Outcome of one step: events plus the measurements worth keeping."""

    events: list[Event] = field(default_factory=list)
    lag: float | None = None
    tested_blocks: set[tuple[int, int]] = field(default_factory=set)
    primary_area: float | None = None
    clean: bool = True


def commanded_boundary(step: Step) -> float:
    """Position of the commanded leading edge, in electrode units.

    Electrode ``k`` spans ``[k-0.5, k+0.5]``, so the outer face of the leading
    electrode sits half a cell beyond its index.
    """
    if step.axis == AXIS_COL:
        return step.col + step.w - 0.5 if step.direction > 0 else step.col - 0.5
    return step.row + step.h - 0.5 if step.direction > 0 else step.row - 0.5


def observed_boundary(step: Step, blob: Blob) -> float:
    """Position of the liquid's actual contact line along the travel axis."""
    if step.axis == AXIS_COL:
        return blob.col_max if step.direction > 0 else blob.col
    return blob.row_max if step.direction > 0 else blob.row


def compute_lag(step: Step, blob: Blob) -> float:
    """How far the liquid trails the commanded edge, in electrodes.

    Positive means the liquid is behind where it was told to be. 0-1 is normal
    transport latency; negative means the liquid ran ahead, which happens when a
    droplet relaxes forward and is not a fault.
    """
    return step.direction * (commanded_boundary(step) - observed_boundary(step, blob))


class Detector:
    """Stateful across a run; the per-step decisions themselves are pure.

    State is only what the signatures genuinely require: which electrodes have
    been swept (residue), how long the lag has persisted (drag), and where the
    droplet has been (no-movement).
    """

    def __init__(self, cfg, block: int = 4, stage: str = "coarse") -> None:
        self.cfg = cfg
        self.block = block
        self.stage = stage

        self._swept: set[tuple[int, int]] = set()
        self._current: set[tuple[int, int]] = set()
        self._lag_streak = 0
        self._lag_peak = 0.0
        self._centroids: list[tuple[float, float]] = []
        self._reported: set[tuple[str, int, int]] = set()
        self._last_step: Step | None = None

    # ── main entry ───────────────────────────────────────────────────────────

    def observe(self, step: Step, obs: Observation) -> ObserveResult:
        """Score one step. Returns the events it fired and what it measured."""
        result = ObserveResult()
        primary = obs.primary()

        self._advance_swept(step)

        if step.kind == KIND_RELEASE:
            # A release drops the trailing edge and energises nothing new, so
            # there is no leading edge to measure and nothing was tested. It
            # also lands immediately after the grow, before the liquid has had
            # a step to reflow -- judging residue here would flag liquid that
            # is simply still moving. Recorded, not scored.
            if primary is not None:
                result.primary_area = primary.area_electrodes
            return result

        if primary is None:
            # Nothing visible at all. Not a per-electrode verdict -- could be a
            # lighting or detection failure -- so it is recorded, not scored.
            result.clean = False
            self._lag_streak = 0
            return result

        result.primary_area = primary.area_electrodes

        lag = compute_lag(step, primary)
        result.lag = lag
        result.tested_blocks = leading_edge_blocks(step, self.block)

        drag = self._check_drag(step, obs, lag, result.tested_blocks)
        if drag:
            result.events.append(drag)

        stuck = self._check_no_movement(step, obs, primary)
        if stuck:
            result.events.append(stuck)

        result.events.extend(self._check_residue(step, obs))

        result.clean = not result.events
        self._last_step = step
        return result

    # ── signatures ───────────────────────────────────────────────────────────

    def _check_drag(self, step: Step, obs: Observation, lag: float,
                    blocks: set[tuple[int, int]]) -> Event | None:
        """Drag: the contact line trailing the commanded edge, and staying there.

        Persistence is what separates a genuine sticky spot from a single-frame
        detection wobble, so a threshold breach only fires after it has held for
        ``lag_persist_steps`` consecutive steps.
        """
        if lag < self.cfg.lag_electrodes:
            self._lag_streak = 0
            self._lag_peak = 0.0
            return None

        self._lag_streak += 1
        self._lag_peak = max(self._lag_peak, lag)
        if self._lag_streak != self.cfg.lag_persist_steps:
            # Fire once, at the moment persistence is met -- not on every
            # subsequent step of the same stall.
            return None

        row, col = self._edge_location(step)
        return self._emit(KIND_DRAG, step, obs, row, col, self._lag_peak,
                          f"contact line trailed the commanded edge by "
                          f"{self._lag_peak:.1f} electrodes for "
                          f"{self._lag_streak} consecutive steps")

    def _check_no_movement(self, step: Step, obs: Observation,
                           primary: Blob) -> Event | None:
        """No movement: centroid static while the window keeps translating.

        Distinct from drag -- drag is a partial response, this is none.
        """
        self._centroids.append((primary.centroid_row, primary.centroid_col))
        k = self.cfg.no_move_steps
        if len(self._centroids) <= k:
            return None
        del self._centroids[:-(k + 1)]

        first = self._centroids[0]
        moved = max(abs(first[0] - c[0]) + abs(first[1] - c[1])
                    for c in self._centroids[1:])
        if moved >= self.cfg.no_move_tol_electrodes:
            return None

        row, col = self._edge_location(step)
        return self._emit(KIND_NO_MOVEMENT, step, obs, row, col, float(k),
                          f"droplet centroid moved {moved:.2f} electrodes over "
                          f"{k} steps while the window advanced")

    def _check_residue(self, step: Step, obs: Observation) -> list[Event]:
        """Residue: liquid left inside territory the window has already left.

        The researcher's second observed signature. It persists, so unlike drag
        it can be confirmed in later frames rather than caught live.
        """
        out: list[Event] = []
        for blob in obs.blobs:
            if blob.area_electrodes < self.cfg.residue_min_area_electrodes:
                continue
            cell = (int(round(blob.centroid_row)), int(round(blob.centroid_col)))
            if cell in self._current or cell not in self._swept:
                continue
            ev = self._emit(KIND_RESIDUE, step, obs,
                            blob.centroid_row, blob.centroid_col,
                            blob.area_electrodes,
                            f"{blob.area_electrodes:.1f} electrodes of liquid left "
                            f"behind in already-swept area")
            if ev:
                out.append(ev)
        return out

    def unreachable(self, target: tuple[int, int], step_idx: int, frame_index: int,
                    t: float, spent: int, budget: int) -> Event:
        """Fine pass: transport failed to arrive.

        A first-class outcome, not a script failure -- a droplet that cannot be
        driven somewhere is itself evidence about that path.
        """
        br, bc = block_of(target[0], target[1], self.block)
        return Event(kind=KIND_UNREACHABLE, step_idx=step_idx,
                     frame_index=frame_index, t=t,
                     row=float(target[0]), col=float(target[1]),
                     block_row=br, block_col=bc,
                     severity=float(spent),
                     detail=f"transport did not arrive within {budget} steps "
                            f"({spent} spent)",
                     stage=self.stage)

    # ── internals ────────────────────────────────────────────────────────────

    def _advance_swept(self, step: Step) -> None:
        """Cells the window has left become swept; cells under it do not."""
        r0, r1, c0, c1 = step.covers()
        window = {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
        self._swept |= self._current - window
        self._current = window

    def _edge_location(self, step: Step) -> tuple[float, float]:
        """A representative electrode on the leading edge, for localisation."""
        r0, r1, c0, c1 = step.covers()
        if step.axis == AXIS_COL:
            return (r0 + r1) / 2.0, float(step.leading_edge)
        return float(step.leading_edge), (c0 + c1) / 2.0

    def _emit(self, kind: str, step: Step, obs: Observation,
              row: float, col: float, severity: float, detail: str) -> Event | None:
        """Build an event, deduplicated per (kind, block).

        Without this a single sticky spot would emit on every step the droplet
        spends stalled against it, and the dataset would be dominated by one
        fault.
        """
        br, bc = block_of(row, col, self.block)
        key = (kind, br, bc)
        if key in self._reported:
            return None
        self._reported.add(key)
        return Event(kind=kind, step_idx=step.idx, frame_index=obs.frame_index,
                     t=obs.t, row=row, col=col, block_row=br, block_col=bc,
                     severity=severity, detail=detail, stage=self.stage)

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def swept_cells(self) -> set[tuple[int, int]]:
        return set(self._swept)
