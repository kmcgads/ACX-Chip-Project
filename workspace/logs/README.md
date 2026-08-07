# `workspace/logs/` — audit + activity trail

Aggregated logs that every step (and every audit tool)
appends to. Files here are append-only and machine-written;
to read across steps, grep here:

* `audit_report.md` — every `tool_audit_quality_full` run.
* `search_log.md` — every literature / web search
  (`tool_search_*` and `tool_literature_search_and_save`).
* `repair_log.md` — every `tool_workspace_repair` invocation
  (file moves, broken-link fixes).
* `override_log.md` — every `override_completeness_gate=true`
  bypass with the rationale that authorised it. Surfaced at
  pre-submission audit time.
* `task_*.log` — stdout/stderr of `tool_python_exec` /
  `tool_task_run` script invocations.
* `notifications.log` — `sys_notify` messages.
* `version_coherence.md` — drift between scripts and the
  outputs that cite them (from `tool_audit_version_coherence`).
