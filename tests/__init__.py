# Test package for the chiphealth build.
#
# Runs with the standard library only -- pytest is not installed on the
# development machine (docs/spec/p1_build_status.md). From project/:
#
#     python3 -m unittest discover -s tests -v

from typing import TypeVar

_T = TypeVar("_T")


def not_none(value: _T | None, msg: str = "expected a value, got None") -> _T:
    """Assert a value is not None, and return it narrowed to its real type.

    `assertIsNotNone` is the natural thing to write and does the right thing at
    runtime, but it is an ordinary method call rather than a TypeGuard, so a
    type checker gets nothing from it: the very next line still reads as an
    attribute access on None. Tests that were already checking correctly still
    showed up as errors.

    This does both jobs -- fails the test with a message, and narrows the type
    -- so the check stays meaningful instead of being silenced by an ignore
    comment. `raise` rather than bare `assert`, so that `python -O` cannot
    strip it.
    """
    if value is None:
        raise AssertionError(msg)
    return value
