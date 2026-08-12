"""A synthetic rig: droplet physics crude enough to be honest, real enough to test.

Pure: standard library only.

This exists because there is no ground-truth faulty region on the real chip
(spec/objectives.md §1.4 q11). Injected faults are the only way to check that
the detector finds what it is supposed to find *before* the chip provides an
answer -- and the only way to run the whole 867-step pipeline on a machine with
no camera and no rig.

What it models, from the researcher's directly observed behaviour: a droplet
passing over a bad electrode **drags**, and when it eventually breaks free it
**leaves part of itself behind** (spec/objectives.md §1.7).

What it does NOT model, and must not be read as: real electrowetting. There is
no contact-angle physics here, no surface tension, no dielectric behaviour. It
is a fixture for exercising the detection logic, not a simulation of the
instrument. A detector that passes against this is not thereby validated
against the chip.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detector import Blob, Observation
from .sweep import AXIS_COL, KIND_RELEASE, Step


@dataclass
class SyntheticRig:
    """Generates observations for a commanded sweep, with injected faults.

    Args:
        dead: electrodes that do not actuate.
        break_lag: how far the droplet trails before it tears free of a sticky
            spot, in electrodes.
        residue_area: size of the fragment left behind when it does.
        jitter: small measurement noise on the observed boundary, so tests are
            not accidentally tuned to a noise-free signal.
    """

    dead: set[tuple[int, int]] = field(default_factory=set)
    break_lag: float = 8.0
    residue_area: float = 2.5
    jitter: float = 0.0

    def __post_init__(self) -> None:
        self._lag = 0.0
        self._residue: set[tuple[int, int]] = set()
        self._noise = _Lcg(12345)
        self.stuck_at: list[tuple[int, int]] = []

    # ── main ─────────────────────────────────────────────────────────────────

    def observe(self, step: Step, frame_index: int, t: float) -> Observation:
        """What the camera would see at this commanded step."""
        if step.kind == KIND_RELEASE:
            # A release only drops the trailing edge; the droplet is not asked
            # to enter anywhere new, so its lag cannot change. Evaluating the
            # frontier here also breaks the model: the frontier is derived as
            # leading_edge - direction*lag, which assumes the leading edge
            # advances every step. Under grow/release it advances every other
            # step, so once lag reached 1 the frontier slid back onto a live
            # cell and unblocked itself -- lag oscillated 0<->1 and no fault was
            # ever detected.
            blobs = [self._primary(step)]
            blobs.extend(self._residue_blobs())
            return Observation(step_idx=step.idx, frame_index=frame_index, t=t,
                               blobs=tuple(blobs))

        blocking = self._frontier_blockers(step)

        if blocking:
            # The window advanced; the liquid could not follow into a dead cell.
            self._lag += 1.0
            if self._lag >= self.break_lag:
                self._tear_free(blocking)
        else:
            # Free running: the droplet closes any gap it had.
            self._lag = max(0.0, self._lag - 1.0)

        blobs = [self._primary(step)]
        blobs.extend(self._residue_blobs())
        return Observation(step_idx=step.idx, frame_index=frame_index, t=t,
                           blobs=tuple(blobs))

    # ── internals ────────────────────────────────────────────────────────────

    def _frontier_blockers(self, step: Step) -> list[tuple[int, int]]:
        """Dead cells in the row the *droplet* is trying to enter next.

        Not the commanded leading edge -- the droplet's own frontier, which
        trails the window by the current lag. This is the difference between a
        blockage that clears as soon as the window moves past it (wrong: a dead
        column would produce a single step of lag and never be detected) and one
        that holds the liquid until it tears free (right).
        """
        r0, r1, c0, c1 = step.covers()
        frontier = int(step.leading_edge - step.direction * self._lag) + step.direction
        if step.axis == AXIS_COL:
            if not (1 <= frontier <= 128):
                return []
            cells = [(r, frontier) for r in range(r0, r1 + 1)]
        else:
            if not (1 <= frontier <= 128):
                return []
            cells = [(frontier, c) for c in range(c0, c1 + 1)]
        return [cell for cell in cells if cell in self.dead]

    def _tear_free(self, blocking: list[tuple[int, int]]) -> None:
        """Break away from the sticky spot, leaving a fragment behind.

        Crude on purpose: any dead cell across the frontier stalls the whole
        droplet, where a real one would deform and partially advance. The
        fixture models drag-then-residue, not electrowetting.
        """
        for cell in blocking:
            self._residue.add(cell)
            self.stuck_at.append(cell)
        self._lag = 0.0

    def _primary(self, step: Step) -> Blob:
        """The driven droplet, trailing the commanded window by the current lag."""
        lag = self._lag + self._noise.uniform(-self.jitter, self.jitter)
        if step.axis == AXIS_COL:
            col = step.col - 0.5 - lag * step.direction
            row = step.row - 0.5
        else:
            col = step.col - 0.5
            row = step.row - 0.5 - lag * step.direction
        return Blob(centroid_row=row + step.h / 2.0,
                    centroid_col=col + step.w / 2.0,
                    area_electrodes=float(step.h * step.w),
                    row=row, col=col,
                    height=float(step.h), width=float(step.w))

    def _residue_blobs(self) -> list[Blob]:
        side = max(1.0, self.residue_area ** 0.5)
        return [Blob(centroid_row=float(row), centroid_col=float(col),
                     area_electrodes=self.residue_area,
                     row=row - side / 2.0, col=col - side / 2.0,
                     height=side, width=side)
                for row, col in sorted(self._residue)]


class _Lcg:
    """Tiny deterministic RNG.

    Not `random` -- keeping the fixture's noise independent of the recorder's
    sampling stream means one cannot perturb the other in a test.
    """

    def __init__(self, seed: int) -> None:
        self.state = seed

    def next(self) -> float:
        self.state = (1103515245 * self.state + 12345) % (2 ** 31)
        return self.state / (2 ** 31)

    def uniform(self, lo: float, hi: float) -> float:
        if hi <= lo:
            return 0.0
        return lo + (hi - lo) * self.next()


def dead_block(block_row: int, block_col: int, block: int = 4) -> set[tuple[int, int]]:
    """Every electrode in one block of the verdict map -- a convenient fault unit."""
    r0 = block_row * block + 1
    c0 = block_col * block + 1
    return {(r, c) for r in range(r0, r0 + block) for c in range(c0, c0 + block)}


def dead_column(col: int, rows: int = 128) -> set[tuple[int, int]]:
    """A whole dead column.

    The failure mode a matrix-addressed array actually tends to have -- a shared
    drive line or IC channel, not a scattered single electrode
    (spec/objectives.md §1.7).
    """
    return {(r, col) for r in range(1, rows + 1)}
