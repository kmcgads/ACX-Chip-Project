# Scratch

AI sandbox for one-off tests (syntax checks, smoke runs, parameter
sweeps, throw-away queries). Contents are gitignored.

Anything that produces **research** must be moved into a proper
numbered experiment folder via `sys_path(operation='create')` before it counts.

Tools: `tool_scratch(operation='write')`, `tool_scratch(operation='run')`,
`tool_scratch(operation='list')`, `tool_scratch(operation='clear')`.
