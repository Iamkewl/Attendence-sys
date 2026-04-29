---
applyTo: "**"
---

# Context Navigation Protocol

When you need to understand the codebase, docs, or any files in this project:

1. **Always query the knowledge graph first**: run `graphify query "your question"` via terminal.
2. **Only read raw files** if the user explicitly says "read the file" or "look at the raw file", or if the graph query is insufficient.
3. **Use `graphify-out/wiki/index.md`** as your navigation entry point for browsing code structure.
4. **Read `graphify-out/GRAPH_REPORT.md`** for god nodes and community structure before answering architecture questions.
5. **After modifying code files**, run `python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current.

# Knowledge Vault

A persistent Obsidian knowledge vault exists at `.agents/knowledge/`. You MUST use it.

## On Task Start
1. Read `.agents/knowledge/projects/` for active project context.
2. Read `.agents/knowledge/agents/` for role-specific lessons.
3. Read `.agents/knowledge/decisions/` for ADRs.

## On Task Completion
1. Write a task record to `.agents/knowledge/Tasks/`.
2. Promote stable knowledge to projects/, decisions/, agents/, or references/.

## Rules
- Append-only. One insight per note. Use templates in `.agents/knowledge/templates/`.

# Agent System
- Skills: `.agents/skills/<role>/SKILL.md`
- Protocol: `.agents/_agent_protocol.md`
- MCP: `openspace` (task execution), `obsidian-memory` (vault access)
