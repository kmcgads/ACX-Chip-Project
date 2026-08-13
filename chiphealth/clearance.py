"""The clearance gate: refuse to load or move a drop that does not fit.

Pure -- standard library only, no DLL, no numpy, no OpenCV. Raising this
exception must not require a rig, because the whole point is to raise it
*before* one is energised.

WHY THIS EXISTS
===============
Up to 2026-08-13 an out-of-bounds drop had three different fates depending on
which layer noticed it first:

    microdrop.splitplan.plan_tree   appended a Violation to a list nobody was
                                    obliged to read, and returned the plan
    ChipController._validate        raised a bare ValueError naming one drop,
                                    mid-run, after N frames had already fired
    nothing at all                  for the chip-health resting frame, the
                                    registration window and the sweep plan,
                                    which were never bounds-checked as plans

So a plan could report "no geometry violations", start executing, and abort
part-way through with liquid on the chip and the run folder half written.
This module makes that one outcome: measured up front, refused up front, and
overridable only by a human who says so in writing.

INDEX CONVENTION -- 1-BASED
===========================
Electrode (1, 1) is the top-left of the active array and (rows, cols) is the
bottom-right. That is what ``geometry.py`` documents, what
``ElectrodeFrame.contains`` enforces, and what ``ChipController._validate``
checks before every ``ActivateElec``.

It is worth being blunt about this, because ``splitplan.plan_tree`` used to
disagree: its off-grid test was ``r0 < 0 or r1 >= rows``, i.e. 0-based. The two
conventions differ by exactly one electrode at each edge, and the difference is
not symmetric -- 0-based accepts row 0, which the hardware layer refuses, and
rejects row 128, which the hardware layer accepts. The visible consequence was
that ``splitplan.cleared_root()`` planned with zero reported violations and
then had 63 of its 87 frames rejected by ``ChipController``. Everything now
measures against this module so that cannot recur.

WHAT "CLEARANCE" MEANS HERE
===========================
Purely geometric: does every electrode this operation will energise lie on the
array. It is NOT a statement about liquid. A drop can be fully on-grid and
still be a bad idea -- too near another drop (``splitplan``'s
``min_separation``), too thin at full stretch, or loaded where the operator
cannot reach. Those are separate checks and stay where they are.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)

#: Index of the first electrode on each axis. See INDEX CONVENTION above.
FIRST_INDEX = 1

#: Order matters only for readability of the message.
SIDES = ("top", "bottom", "left", "right")


#: Inclusive ``(row0, row1, col0, col1)``, the shape every layer already uses.
Box = tuple[int, int, int, int]


def as_boxes(items: Iterable[object]) -> list[Box]:
    """Normalise drops / steps / nodes / raw tuples to inclusive boxes.

    Duck-typed on purpose, so this module can sit below every layer without
    importing any of them: ``actuation.Drop``, ``sweep.Step`` and
    ``splitplan.DropNode`` all already report their extent, the first two as
    ``covers()`` and the third as ``bounds()``. Both spellings are accepted so
    that no caller has to translate, because a caller that has to translate is
    a caller that can translate wrongly.
    """
    out: list[Box] = []
    for item in items:
        if isinstance(item, tuple) and len(item) == 4:
            r0, r1, c0, c1 = item
        elif hasattr(item, "covers"):
            r0, r1, c0, c1 = item.covers()  # type: ignore[attr-defined]
        elif hasattr(item, "bounds"):
            r0, r1, c0, c1 = item.bounds()  # type: ignore[attr-defined]
        else:
            raise TypeError(
                f"{item!r} is not a drop, step, node or (row0, row1, col0, col1) "
                f"tuple, so its clearance cannot be measured"
            )
        out.append((int(r0), int(r1), int(c0), int(c1)))
    return out


@dataclass(frozen=True)
class Clearance:
    """How far an operation overhangs the array, per side, in electrodes.

    ``shortfall`` always has all four keys; 0 means that side is fine. Reported
    per side rather than as a single boolean because "which way do I move it,
    and by how much" is the only question the operator actually has, and a bare
    "off-grid" has made someone re-derive that by hand every time.
    """

    what: str
    bounds: Box
    grid: tuple[int, int]
    shortfall: dict[str, int]
    n_boxes: int = 1

    @property
    def ok(self) -> bool:
        return not any(self.shortfall.values())

    def short_sides(self) -> dict[str, int]:
        """Only the sides that are actually short. Empty when ``ok``."""
        return {s: n for s in SIDES if (n := self.shortfall[s]) > 0}

    def describe(self) -> str:
        rows, cols = self.grid
        r0, r1, c0, c1 = self.bounds
        lo, hi_r, hi_c = FIRST_INDEX, rows, cols
        head = (f"{self.what} spans rows {r0}-{r1}, cols {c0}-{c1} "
                f"across {self.n_boxes} frame(s); the array is {rows}x{cols}, "
                f"i.e. rows {lo}-{hi_r} and cols {lo}-{hi_c}")
        if self.ok:
            return head + " -- clear."
        lines = [head + " -- SHORT ON:"]
        for side, n in self.short_sides().items():
            lines.append(f"  {side}: short by {n} electrode(s)")
        return "\n".join(lines)


class ClearanceViolation(ValueError):
    """A drop was going to be loaded or moved somewhere it does not fit.

    Deliberately NOT a ``ChipError``. A clearance failure is a planning fault,
    detectable with no rig attached and no USB handle open, and every place
    that raises it does so before anything is energised. Making it a hardware
    error would put it in the same ``except`` clause as a dead USB link, which
    is the one thing it is never caused by.

    It subclasses ``ValueError`` because that is what ``ChipController.activate``
    raised for an off-grid drop before this module existed. Anything already
    catching ValueError around an activate call keeps working and simply gets a
    better message; this gate widens where the check runs, not what it throws.

    The measured :class:`Clearance` is attached as ``.clearance`` so a caller
    that wants the numbers does not have to parse the message.
    """

    def __init__(self, clearance: Clearance) -> None:
        self.clearance = clearance
        super().__init__(
            f"{clearance.describe()}\n"
            f"Nothing was energised. Move the operation inside the array, or "
            f"pass allow_violations=True to proceed anyway -- the vendor DLL is "
            f"then handed coordinates outside the electrode array and its "
            f"behaviour there is unspecified, so the frames it does apply may "
            f"not be the ones planned."
        )


def measure(items: Iterable[object], rows: int, cols: int,
            what: str = "operation") -> Clearance:
    """Per-side overhang of everything in ``items`` against a ``rows``x``cols`` array.

    Takes the union over every box, so one call covers a whole plan: the answer
    is the clearance the operation needs as a whole, not a per-frame verdict
    the caller would have to reduce itself.

    An empty ``items`` is clear by definition -- ``deactivate_all`` sends a
    zero-drop frame and must not be gated.
    """
    boxes = as_boxes(items)
    if not boxes:
        return Clearance(what=what, bounds=(0, -1, 0, -1), grid=(rows, cols),
                         shortfall={s: 0 for s in SIDES}, n_boxes=0)

    r0 = min(b[0] for b in boxes)
    r1 = max(b[1] for b in boxes)
    c0 = min(b[2] for b in boxes)
    c1 = max(b[3] for b in boxes)

    return Clearance(
        what=what,
        bounds=(r0, r1, c0, c1),
        grid=(rows, cols),
        shortfall={
            "top": max(0, FIRST_INDEX - r0),
            "bottom": max(0, r1 - (rows + FIRST_INDEX - 1)),
            "left": max(0, FIRST_INDEX - c0),
            "right": max(0, c1 - (cols + FIRST_INDEX - 1)),
        },
        n_boxes=len(boxes),
    )


def require(items: Iterable[object], rows: int, cols: int,
            what: str = "operation", allow_violations: bool = False
            ) -> Clearance:
    """The gate. Raise :class:`ClearanceViolation` unless everything fits.

    ``allow_violations`` is the ONLY way past, it defaults to False everywhere
    it is threaded, and taking it logs at ERROR with the full per-side
    shortfall. There is deliberately no environment variable and no config
    field for it: an override that can be left switched on in a file is an
    override that stops being a decision.
    """
    c = measure(items, rows, cols, what)
    if c.ok:
        return c
    if allow_violations:
        log.error("CLEARANCE OVERRIDE (allow_violations=True) -- proceeding "
                  "with an operation that does not fit:\n%s", c.describe())
        return c
    raise ClearanceViolation(c)


def fits(items: Iterable[object], rows: int, cols: int) -> bool:
    """Boolean form, for callers that want to branch rather than catch."""
    return measure(items, rows, cols).ok
