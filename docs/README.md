# Documentation

Single entry point for this repository's documentation. Two kinds of document
live here and they serve different readers.

## Guides

Focused, single-topic explanations of how something works and why it is built
that way. Start here if you are working on the code or running the hardware.

| Guide | Read it when |
|---|---|
| [Running the split scripts](guides/running-the-split-scripts.md) | You are at the rig and about to energise a chip |
| [The symmetric split algorithm](guides/symmetric-split.md) | You need to understand or change how a split works |
| [Separation and dwell tuning](guides/separation-and-dwell-tuning.md) | A split is not separating, or you want to change a stretch ratio or dwell |
| [The clearance gate](guides/clearance-gate.md) | Something was refused as off-grid, or you are adding a code path that energises electrodes |
| [Volume equality](guides/volume-equality.md) | You need to state what "equal pieces" does and does not claim |
| [Provenance](guides/provenance.md) | You want to know whether a number is proven or derived, and from what |

## Specification and design record

The original planning and design documents. These are the historical record of
what was specified and decided, and are cited throughout the code as
`docs/spec/objectives.md §N`.

| Document | Contents |
|---|---|
| [spec/README.md](spec/README.md) | Index and reading order for the spec set |
| [spec/objectives.md](spec/objectives.md) | Priority definitions, open questions, the roadmap |
| [spec/requirements.md](spec/requirements.md) | Requirements |
| [spec/design.md](spec/design.md) | Architecture and ADRs |
| [spec/p1_chip_health_design.md](spec/p1_chip_health_design.md) | Priority 1 chip-health design |
| [spec/p1_build_status.md](spec/p1_build_status.md) | Build status, deferred items, bring-up runbook |

Guides describe how the code works **now**. Spec documents record what was
**intended** and when. Where the two disagree, the guides and the code are
authoritative and the spec should be read as history.

## Related

- [Root README](../README.md) — installation, usage, configuration
- [CONTRIBUTING.md](../CONTRIBUTING.md) — tests, the `check_geometry()` pattern, commit conventions
- `workspace/analysis.md` — vendor DLL disassembly notes
