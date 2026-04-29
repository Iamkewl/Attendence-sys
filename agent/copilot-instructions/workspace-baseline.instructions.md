---
applyTo: "**"
---

# Workspace Baseline

Use only repository-local context and tools available in this environment.

## Task Start
1. Discover relevant files using workspace search.
2. Read the exact modules involved before proposing edits.
3. Verify environment assumptions (OS, GPU, services) when operational tasks are requested.

## Task Execution
1. Keep edits minimal and scoped to the user request.
2. Prefer deterministic, non-interactive commands for setup and validation.
3. After changes, run targeted compile/tests for touched files when feasible.

## Task Completion
1. Report what changed and how it was validated.
2. List unresolved blockers and concrete next actions if anything remains.

## Rules
- Do not assume optional external systems are installed.
- Prefer paths and assets that exist in this workspace.
