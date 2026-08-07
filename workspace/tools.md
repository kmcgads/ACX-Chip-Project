# Tools log

Chronological record of the tooling stack actually used by this project — Research-OS MCP calls (route, search, audit, finalize), 3rd-party packages (statsmodels, scanpy, DESeq2, Cytoscape, …), external services (Semantic Scholar, PubMed, GTEx, GEO), and any custom scripts the analyses depend on.

Format: one bullet per usage, grouped by analysis step + synthesis stage. The synthesis dashboard surfaces this so a reviewer can audit reproducibility without re-deriving the stack from the scripts.

Each step's `tool_path_finalize` appends a section here from its `conclusions.md` Methods + the per-step provenance sidecars; manual additions are welcome.
