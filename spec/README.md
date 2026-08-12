# `spec/` — what we're building

The governance source of truth for this project: priorities, requirements,
design, and interface contracts. Research OS reads these to keep the build
honest against intent.

**Start with `objectives.md`.** It sets priority, sequence and scope, and
where it disagrees with `design.md` it wins — the design doc is amended
before the affected piece is built.

* `objectives.md` — **the roadmap.** Standing requirements that apply to
  every priority, the priority list itself, the gate process, and what is
  and is not done (§6). Appendix A holds the deferred axis work.
* `requirements.md` — what the tool must do, functional and
  non-functional, and what it deliberately does not do.
* `design.md` — the original layered architecture for the whole
  five-subsystem instrument. **Partly aspirational**: written before any
  code existed. Read its header note first for what was actually built
  and which sections are superseded.
* `p1_chip_health_design.md` — the design of the one piece that is built:
  the chip-health sweep. Method, detection signatures, artifact schema.
* `p1_build_status.md` — **current state of the code.** Test counts, what
  changed when, bugs found and fixed, the hardware sessions so far, and
  the procedure for the next armed run.

Keep these current as the work moves. A decision that changes the design
should land here, not only in a commit message. (`decisions/` does not
exist in this repo; decisions are recorded inline in the documents above
and appended to `workspace/analysis.md`.)

**Priority numbering changed on 2026-08-12** — axis control was dropped as
an active priority and everything below it moved up one. Older documents
and log entries may still use the original numbering; `objectives.md`
records the mapping.
