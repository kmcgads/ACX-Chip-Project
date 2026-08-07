"""Path planning for the coarse sweep and the fine pass.

Pure: standard library only. No numpy, no OpenCV, no hardware -- band and step
planning is arithmetic, and keeping it that way means the whole traversal can be
unit-tested and its cost known before a single electrode is energised.

Coarse pass (spec/p1_chip_health_design.md §2, phase 4): a 20x20 activated
window **translates** in a serpentine raster across the chip, carrying the
droplet with it.

Why translation and not cleanup.py's shrink: a shrinking region anchored at a
corner drags liquid *inward to a collection point*. That is a collection
operation -- correct for end-of-run consolidation, wrong for coverage, because
with a single droplet already near the top-left the receding boundary sweeps
mostly over dry chip and observes nothing. The translation pattern itself is not
new: it is what 1pixsplit.py steps 4/7/8 and dropsplitoff.py already do, moving a
piece one column at a time.
"""

from __future__ import annotations


from dataclasses import dataclass, asdict

# Which coordinate a step advances.
AXIS_COL = "col"
AXIS_ROW = "row"

KIND_TRAVEL = "travel"        # sweeping along a band
KIND_BAND_CHANGE = "band"     # moving down to the next band
KIND_TRANSPORT = "transport"  # fine pass: driving liquid to a target
KIND_PROBE = "probe"          # fine pass: testing a block


@dataclass(frozen=True)
class Step:
    """One ``ActivateElec`` call: the commanded window at one instant."""

    idx: int
    row: int
    col: int
    h: int
    w: int
    axis: str        # AXIS_COL | AXIS_ROW -- which coordinate is advancing
    direction: int   # +1 | -1
    kind: str
    band: int

    @property
    def leading_edge(self) -> int:
        """Electrode index of the edge moving into fresh territory.

        This is what the drag metric compares the observed contact line against.
        """
        if self.axis == AXIS_COL:
            return self.col + self.w - 1 if self.direction > 0 else self.col
        return self.row + self.h - 1 if self.direction > 0 else self.row

    @property
    def trailing_edge(self) -> int:
        """Electrode index of the edge leaving territory behind it."""
        if self.axis == AXIS_COL:
            return self.col if self.direction > 0 else self.col + self.w - 1
        return self.row if self.direction > 0 else self.row + self.h - 1

    def covers(self) -> tuple[int, int, int, int]:
        """(row_start, row_end, col_start, col_end), inclusive."""
        return self.row, self.row + self.h - 1, self.col, self.col + self.w - 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["leading_edge"] = self.leading_edge
        return d


def plan_bands(chip_rows: int, window_h: int, first_row: int = 1) -> list[int]:
    """Top rows of each horizontal band.

    ``first_row`` is where the **first band begins**, which is deliberately not
    the same thing as where the droplet is loaded. Conflating the two is what
    used to leave row 1 untested: the operator loads at row 2, so bands started
    at row 2 and nothing ever reached row 1.

    Bands step down by the window height. The final band is **clamped** so the
    bottom edge is covered, deliberately overlapping the previous band rather
    than leaving an untested strip.

    For the real geometry (128 rows, 20-high window, from row 1) this gives
    ``[1, 21, 41, 61, 81, 101, 109]``.
    """
    if window_h <= 0 or chip_rows <= 0:
        raise ValueError("chip_rows and window_h must be positive")
    if window_h > chip_rows:
        raise ValueError(f"window ({window_h}) taller than chip ({chip_rows})")
    if not (1 <= first_row <= chip_rows - window_h + 1):
        raise ValueError(
            f"first_row {first_row} leaves the window off-chip "
            f"(valid 1..{chip_rows - window_h + 1})"
        )

    tops: list[int] = []
    top = first_row
    while top + window_h - 1 <= chip_rows:
        tops.append(top)
        top += window_h
    last_bottom = tops[-1] + window_h - 1
    if last_bottom < chip_rows:
        tops.append(chip_rows - window_h + 1)
    return tops


def uncovered_rows(chip_rows: int, window_h: int, first_row: int = 1) -> list[int]:
    """Rows no band ever covers.

    Empty when bands start at row 1. Anything listed here is recorded as
    ``unknown``, never as ``pass`` -- and it cannot be seen in the block grid,
    since a 4-row block reads ``pass`` on the strength of its other rows.
    """
    covered: set[int] = set()
    for top in plan_bands(chip_rows, window_h, first_row):
        covered.update(range(top, top + window_h))
    return [r for r in range(1, chip_rows + 1) if r not in covered]


def leading_edge_cells(step: "Step") -> set[tuple[int, int]]:
    """Electrodes under this step's leading edge -- the ones it actually tests.

    The window interior is bridged liquid and proves nothing about the
    electrodes beneath it; the contact line is where the evidence is.
    """
    r0, r1, c0, c1 = step.covers()
    edge = step.leading_edge
    if step.axis == AXIS_COL:
        return {(r, edge) for r in range(r0, r1 + 1)}
    return {(edge, c) for c in range(c0, c1 + 1)}


def untested_electrodes(steps: list["Step"], chip_rows: int,
                        chip_cols: int) -> set[tuple[int, int]]:
    """Electrodes no step ever puts under a leading edge.

    The honest measure of coverage, and finer than the block map can express.
    A complete traversal returns the empty set.
    """
    tested: set[tuple[int, int]] = set()
    for step in steps:
        tested |= leading_edge_cells(step)
    return {(r, c)
            for r in range(1, chip_rows + 1)
            for c in range(1, chip_cols + 1)} - tested


def plan_serpentine(chip_rows: int, chip_cols: int, window_h: int, window_w: int,
                    start_row: int, start_col: int,
                    first_band_row: int = 1, prime: bool = True) -> list[Step]:
    """The full coarse traversal, as an explicit list of commanded windows.

    Every step advances exactly one electrode. EWOD transport needs the
    activated region to overlap the droplet, so the window cannot jump -- the
    same one-at-a-time discipline the legacy split scripts use.

    **Why band 0 gets a priming leg.** A band travelling in one direction cannot
    cover both ends of the chip: sweeping right the leading edge is ``col+w-1``,
    so it can never be below ``w``; sweeping left it is ``col``, so it can never
    exceed ``col_max``. Each band therefore misses roughly a window's width of
    columns at one end -- and for bands 1..N those are filled in by the *band
    change*, whose leading edge is a row sweeping across exactly the columns the
    next band will miss. The serpentine heals itself at its own corners.

    Band 0 has no preceding corner. Starting mid-chip at the load column, it
    left 320 electrodes untested (rows 2-21, cols 5-20). ``prime=True`` gives it
    the turn it lacks: run out to ``col_min + w``, back to ``col_min``, then
    away. Costs ~32 steps and closes the gap.

    Together with ``first_band_row=1`` this makes coverage complete -- see
    :func:`untested_electrodes`, which returns the empty set for the default
    geometry. Pass ``prime=False`` for the old, incomplete behaviour.
    """
    col_min = 1
    col_max = chip_cols - window_w + 1
    if not (col_min <= start_col <= col_max):
        raise ValueError(
            f"start_col {start_col} leaves the window off-chip "
            f"(valid {col_min}..{col_max})"
        )

    tops = plan_bands(chip_rows, window_h, first_band_row)
    steps: list[Step] = []
    row, col = start_row, start_col
    idx = 0

    def emit(r: int, c: int, axis: str, direction: int, kind: str, band: int) -> None:
        nonlocal idx
        steps.append(Step(idx=idx, row=r, col=c, h=window_h, w=window_w,
                          axis=axis, direction=direction, kind=kind, band=band))
        idx += 1

    def walk_cols(target: int, band: int) -> None:
        nonlocal col
        while col != target:
            d = 1 if target > col else -1
            col += d
            emit(row, col, AXIS_COL, d, KIND_TRAVEL, band)

    for band_i, top in enumerate(tops):
        # Walk into the band, one row at a time. This may go UP: the droplet is
        # loaded at row 2 but band 0 starts at row 1.
        while row != top:
            d = 1 if top > row else -1
            row += d
            emit(row, col, AXIS_ROW, d, KIND_BAND_CHANGE, band_i)

        if band_i == 0 and prime:
            for target in (min(col_min + window_w, col_max), col_min, col_max):
                walk_cols(target, band_i)
        elif band_i == 0:
            walk_cols(col_min, band_i)
            walk_cols(col_max, band_i)
        else:
            walk_cols(col_max if col <= col_min else col_min, band_i)

    return steps


def plan_vertical(chip_rows: int, chip_cols: int, window_h: int, window_w: int,
                  start_row: int, start_col: int,
                  first_band_col: int = 1, prime: bool = True) -> list[Step]:
    """The orthogonal sweep, for ``axes="both"``.

    A horizontal serpentine mainly exercises column-to-column transitions; an
    electrode's row-direction behaviour is only tested at band edges. This
    covers the other axis at double the cost, which is why it is opt-in.

    Implemented by planning a horizontal sweep on the transposed chip and
    swapping each step's coordinates back.
    """
    transposed = plan_serpentine(chip_cols, chip_rows, window_w, window_h,
                                 start_col, start_row,
                                 first_band_row=first_band_col, prime=prime)
    flipped: list[Step] = []
    for s in transposed:
        flipped.append(Step(
            idx=s.idx, row=s.col, col=s.row, h=s.w, w=s.h,
            axis=AXIS_ROW if s.axis == AXIS_COL else AXIS_COL,
            direction=s.direction, kind=s.kind, band=s.band,
        ))
    return flipped


def total_duration_s(steps: list[Step], step_delay_s: float) -> float:
    """Delay-only cost. Camera, USB and analysis time are on top of this."""
    return len(steps) * float(step_delay_s)


# ── block map ────────────────────────────────────────────────────────────────

def block_of(row: float, col: float, block: int) -> tuple[int, int]:
    """Which (block_row, block_col) an electrode falls in. 0-indexed."""
    return int((row - 1) // block), int((col - 1) // block)


def block_bounds(block_row: int, block_col: int, block: int
                 ) -> tuple[int, int, int, int]:
    """(row_start, row_end, col_start, col_end) of a block, inclusive, 1-indexed."""
    r0 = block_row * block + 1
    c0 = block_col * block + 1
    return r0, r0 + block - 1, c0, c0 + block - 1


def block_grid_shape(chip_rows: int, chip_cols: int, block: int) -> tuple[int, int]:
    """Verdict-map dimensions. 128x128 at block=4 gives 32x32 = 1024 blocks."""
    return (chip_rows + block - 1) // block, (chip_cols + block - 1) // block


def blocks_touched(step: Step, block: int) -> set[tuple[int, int]]:
    """Blocks the commanded window overlaps at this step."""
    r0, r1, c0, c1 = step.covers()
    out: set[tuple[int, int]] = set()
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            out.add(block_of(r, c, block))
    return out


def leading_edge_blocks(step: Step, block: int) -> set[tuple[int, int]]:
    """Blocks lying under the leading edge -- the ones this step actually tests.

    The interior of the window is bridged liquid and yields no per-electrode
    information; the contact line is where the evidence is.
    """
    r0, r1, c0, c1 = step.covers()
    edge = step.leading_edge
    out: set[tuple[int, int]] = set()
    if step.axis == AXIS_COL:
        for r in range(r0, r1 + 1):
            out.add(block_of(r, edge, block))
    else:
        for c in range(c0, c1 + 1):
            out.add(block_of(edge, c, block))
    return out


# ── fine pass ────────────────────────────────────────────────────────────────

def manhattan_steps(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Electrode steps to translate a window from a to b, one electrode at a time."""
    return int(round(abs(a[0] - b[0]) + abs(a[1] - b[1])))


def plan_fine_route(start_rc: tuple[float, float],
                    targets: list[tuple[int, int]],
                    max_targets: int | None = None
                    ) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Order fine-pass targets nearest-first from where the sweep ended.

    Greedy nearest-neighbour, replanned from each target. Not optimal, and it
    does not need to be -- liquid is lost along the way, so the value is in
    testing the closest suspicious regions before the probe droplet degrades,
    not in shaving travel.

    Returns ``(ordered, dropped)``. Anything cut by ``max_targets`` is returned
    explicitly so the caller can log it: a silently truncated target list would
    read as "everything was checked" when it was not.
    """
    remaining = list(targets)
    ordered: list[tuple[int, int]] = []
    cur = start_rc
    while remaining:
        nearest = min(remaining, key=lambda t: manhattan_steps(cur, (t[0], t[1])))
        remaining.remove(nearest)
        ordered.append(nearest)
        cur = (float(nearest[0]), float(nearest[1]))

    if max_targets is not None and len(ordered) > max_targets:
        return ordered[:max_targets], ordered[max_targets:]
    return ordered, []


def expected_transport_steps(a: tuple[float, float], b: tuple[float, float],
                             slack: float) -> int:
    """Step budget before transport is declared ``unreachable``.

    Exceeding it is a first-class outcome, not a script failure: a droplet that
    cannot be driven to a location is itself evidence about that path.
    """
    return max(1, int(round(manhattan_steps(a, b) * float(slack))))
