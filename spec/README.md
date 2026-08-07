# `spec/` — what we're building

The governance source of truth for this tool: requirements,
design, and interface contracts. Research OS reads these to keep
the build honest against intent.

* `requirements.md` — what the tool must do (functional +
  non-functional). Start here.
* `design.md` — how it's built: architecture, key modules, the
  data + control flow, trade-offs considered.

Keep these current as the design moves — a decision that changes
the design should land both here AND as an ADR in `decisions/`.
