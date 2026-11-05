---
name: code
description: Delegate an implementation task to the coder subagent (Opus, high effort). Only invoke when the user explicitly types /code; the orchestrator otherwise delegates to the coder directly.
disable-model-invocation: true
---

Invoke the Agent tool with subagent_type: "coder" and the following prompt (include full relevant context from this conversation — the design/plan to implement, file paths, constraints, the phase spec and fixtures to satisfy, the commit trailers — since the subagent starts fresh). Do not pass `model` or `effort`; the agent definition sets them. The coder's operating rules live in its agent definition (`.claude/agents/coder.md`), so they apply whether or not this skill is used.

$ARGUMENTS

When the coder returns: run the adjacent suites, re-verify its headline claim, update the state-tracking docs, then push and read CI (see `.claude/WORKING_AGREEMENT.md`).
