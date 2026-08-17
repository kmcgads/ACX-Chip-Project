# Contributing

## Running the tests

**pytest is not installed and that is deliberate** — the suite runs on the
standard library. From the project root:

```bash
.\.venv\Scripts\python.exe -m unittest discover -s tests        # all 520
.\.venv\Scripts\python.exe -m unittest discover -s tests -v     # verbose
.\.venv\Scripts\python.exe -m unittest tests.test_splitplan     # one module
```

Expect **520 tests, 2 skipped**. The two skips are the OpenCV-fallback picker
tests, which skip when cv2 *is* installed. No hardware is required; no test
touches a USB handle.

Some tests deliberately exercise refusal paths, so an `ERROR CLEARANCE
OVERRIDE` block in the output is expected and is not a failure.

### What the tests are for

This suite pins **decisions**, not just behaviour. A large fraction of it
exists so that a future change which looks like a simplification fails loudly
instead of quietly producing a geometry nobody checked. Examples:

- `TestCsvvolcontFidelity` fails if someone retunes a ratio, naming the source
  script rather than leaving a surprise for the rig.
- `TestSymmetry` checks mirror invariance of **every frame** at every stage, not
  just the end state — an origin-anchored stretch passes an end-state check and
  fails this one.
- `test_axis_ordering_table` pins the aspect-ratio table, so an ordering change
  that produces slivers fails.

If you change one of these, the test failure is the review. Read what it says
before updating it.

## Documentation-only changes

Prove it mechanically rather than asserting it. Parse before and after, strip
docstrings, compare the ASTs:

```bash
python3 - <<'EOF'
import ast, subprocess, sys
def strip(src):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
               and isinstance(b[0].value.value, str):
                n.body = b[1:] or [ast.Pass()]
    return ast.dump(t)
for path in sys.argv[1:]:
    old = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True).stdout
    new = open(path, encoding="utf-8").read()
    print(("SAME  " if strip(old) == strip(new) else "DIFFERS  ") + path)
EOF
```

This catches a stray logic edit even in a branch no test covers.

## The `check_geometry()` safety pattern

Both split runners hardcode every parameter and then **re-derive the plan at
startup and refuse to run if it does not match**. Follow this pattern for any
new fixed-configuration runner.

```python
def check_geometry(session: SplitSession) -> SP.Approach:
    plan, approach = session.plan, session.approach

    # Structurally impossible states raise immediately.
    if approach is None:
        raise SystemExit("REFUSING TO RUN: ...")

    problems = []
    if len(plan.leaves) != EXPECT_PIECES:
        problems.append(f"{len(plan.leaves)} pieces, expected {EXPECT_PIECES}")
    # ... one check per EXPECT_* constant ...
    if problems:
        raise SystemExit("REFUSING TO RUN: " + "".join(...))

    return approach
```

Rules:

1. **Pin both sides of any change.** If you widen stage 2, assert stage 2 *is*
   widened **and** that stages 0–1 are **not**. A change that leaks into a
   stage proven on hardware must fail.
2. **Run it before opening the chip.** A geometry mismatch should cost a
   message, not a loaded chip.
3. **Name the fix in the refusal.** Print the exact `--plan-only` command that
   regenerates the expected numbers.
4. **Return the narrowed value** where it saves the caller re-establishing an
   invariant — `check_geometry` returns the `Approach` so `main()` never
   touches the optional attribute.

### Labelling hardware verification

**A green test suite is not verification.** A configuration is "verified" only
after a live run an operator confirmed.

When you change anything that affects what reaches the chip:

- Pull the "verified" label from the file header, the banner and `--help`.
- Add a run note, so the **report** carries the caveat. The report is the only
  artifact that outlives the terminal; a transcript of confident yeses must not
  read later as though the geometry had been proven.
- Restore the label only after a live run, and tag the commit
  (`split-8piece-verified`) noting which chip it ran on.

## Writing comments

The comment density in this repo is deliberate and has caught real errors.
Aim it at the right target.

**Keep inline:**

- Why a non-obvious decision was made — why the stretch is centred, why the
  neck erodes centre-out, why a stage was widened and by exactly how much.
- Any documented failure mode or trade-off that would cause a real problem if
  someone "simplified" the code without knowing it.
- Anything that looks like a mistake but is not.

**Do not put inline:**

- Sequential history — *"this was X, then changed to Y, then Z was reverted."*
  That belongs in git history or a guide.
- Restatements of what the code plainly does.
- Multi-paragraph concept explanations. Those become a guide in `docs/guides/`
  with a one-line pointer left behind — **moved, not deleted.**

## Commit conventions

Subject line under ~72 chars, imperative, naming what changed and where.

The body is where the reasoning goes, and it is expected to be substantial.
ALL-CAPS section headers separate concerns. Cover:

- **What changed**, as a list.
- **Why**, including what was rejected and the measurements behind the choice.
- **What it costs** — trade-offs, and anything now less safe or less proven.
- **What is still unverified.** State plainly when a fix buys margin rather
  than addressing a root cause.
- **Test count**: `520 tests, 2 skipped (4 added).`

Include before/after tables for numeric changes:

```
  final-stage neck gap  8               12
  tree frames           87              103
  dwell                 ~94s            ~102s
  stages 0-1            UNCHANGED
```

Do not add a `Co-Authored-By` trailer.

Commits are not pushed automatically. Branch before committing if you are on
`main`.
