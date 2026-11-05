---
name: architect
description: Delegate an architecture/design question to the architect subagent (Fable, high effort). Only invoke when the user explicitly types /architect.
disable-model-invocation: true
---

Invoke the Agent tool with subagent_type: "architect" and the following prompt (include full relevant context from this conversation and the codebase, since the subagent starts fresh). Do not pass `model` or `effort`; the agent definition sets them.

$ARGUMENTS

## After the architect returns

Once the architect's scoping/plan comes back, update the project's own architecture and phase docs to record the scoped-but-not-yet-built work — do this yourself (Edit tool), don't delegate it to the architect subagent. In this repo that means `docs/ARCHITECTURE.md` (only if the design changes the architecture; include its Decisions table) and the relevant phase spec in `docs/phases/` (its "Next" checklist and open questions). Mark the work explicitly as not yet implemented — no fabricated commit hash or test results. This keeps the docs a running record of scoped work even before a coder picks it up, so a later "update the docs" pass isn't reconstructing history from git log alone.

If the user says not to touch the docs, skip this step.
