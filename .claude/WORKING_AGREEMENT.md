# Working agreement (Hausbuch)

Copied on 5 Nov 2026 from Luis's `~/.claude/CLAUDE.md`, so cloud sessions, which cannot see
that file, follow it too. Changes from the original:

- **Delegation is the default here** (Luis, 5 Nov 2026): the main session orchestrates and
  does only small changes itself. The original says "do it yourself by default".
- The reviewer runs at `xhigh` (the original agent file said `high`; Luis confirmed `xhigh`).
- Coders commit but do not push; the orchestrator pushes. The original `/code` rules said to
  push after each commit, which contradicted the multi-agent rules below.
- "When to review" and "the docs are YOUR job" name this repo's risky areas and docs.
- CI is read through the GitHub tools in cloud sessions, where `gh` is not available.

## Delegation policy

**The main session is the orchestrator.** It scopes, judges results, and does small changes
itself. Anything bigger goes to an agent.

| agent | when |
|---|---|
| **(you, orchestrator)** | Scoping, judging results, anything needing conversation context, and **small changes**: a handful of steps, a doc fix, a one-file tweak, a CI fix you can reproduce. **Scope your own work — do not route it to the architect by reflex.** |
| **coder** | Any implementation beyond a handful of steps, from a written scope: file paths, constraints, the phase spec and fixtures to satisfy, the commit trailers. |
| **reviewer** | Risky changes only (see below). Always with a FOCUS and a narrow target. |
| **architect** | Large or novel design where an INDEPENDENT view is worth a cold start, such as a new phase's spec or a new subsystem. Rare and deliberate. |

🚨 **You own the scoping.** It is where errors are cheapest to catch and most expensive to miss, and you have context no fresh agent does. Delegating it buys a worse-informed answer at a higher price.

🚨 **Do NOT pass `model` or `effort` on an Agent call. Omit both and let the agent definition decide.** Every agent in `.claude/agents/*.md` already declares its own tier and reasoning effort — those are deliberate choices, and an explicit parameter silently overrides them. `reviewer` is `fable` at `xhigh`; passing `model: "opus"` replaces the reviewing model you picked with one you didn't, and the substitution is invisible unless someone goes looking.

If a task genuinely needs a different tier than its agent declares, **say so and ask** — do not decide it silently. The one honest signal that an override happened is the agent's own transcript: `grep -oE '"model":"[a-z0-9-]+"' <task-output>.jsonl | sort -u`.

**The flow, in order:**

1. The orchestrator scopes the work.
2. The architect only for large or novel design; the orchestrator records its plan in the docs as not yet built.
3. The coder gets a written scope and returns commit hashes, test counts and deviations.
4. The orchestrator runs the adjacent suites and re-verifies the coder's headline claim.
5. The reviewer, for risky changes only, gets a FOCUS plus the diff and the spec. It returns CONFIRMED/SUSPECTED findings, a verdict and a FIX SCOPE.
6. The orchestrator checks each finding against the code before acting on it.
7. Fixes go to the coder, or the orchestrator does them if small. One review round only.
8. The orchestrator updates every state-tracking doc, pushes, and reads CI.

## When to review

**Review**: anything touching money or tax figures, persisted state, migrations, row-level security or workspace isolation, the event log, rule fixtures, exit/safety logic, concurrency, or a change whose failure is silent.

**Do not review**: formatting, docs, mechanical refactors, config-only edits, or anything CI already proves. Reviewing everything trains people to ignore reviews.

**Cap the loop at ONE round.** reviewer → coder → reviewer will ping-pong forever otherwise. On the second pass, review only what changed.

**Judge the findings — do not just forward them.** A review is evidence, not a verdict. Findings marked SUSPECTED need checking before anyone acts on them; a wrong finding costs more than a missed one because someone will go fix it.

## After the work lands: the docs are YOUR job

🚨 **When an agent finishes, you update the docs that describe that work** — status
headers, state markers, and any "not built yet" note the change just falsified.
Do not assume the coder did it: agents are usually given a narrow path scope
(`scripts/` only, one module) precisely so they don't collide, and that scope
almost never includes the design docs. Nobody owns doc currency unless you do.

**In this repo the state-tracking docs are:**

- `docs/ARCHITECTURE.md`: the Decisions table and the delivery phases table.
- `docs/phases/phase-N.md`: the status table at the top, the "Next: implementation PR" checklist, the decisions and the open questions.
- `CLAUDE.md`: the list of phase specs.

**Why this is not bookkeeping.** A stale status header is not a cosmetic defect —
it is a false statement about the state of the world that you will later read and
believe. Concretely, in one project on one day: a chunk marked `SCOPE-ONLY` had
in fact shipped two days earlier, so it was scheduled again and an agent was
dispatched to rebuild it; and a feature flag documented as "stays off" had been
on for days, quietly collecting the exact evidence the doc described as missing.
Both were caught only because someone read the code instead of the doc.

- **Update the doc in the same turn the work lands**, not "later" — later is when
  you have already lost the context that made it cheap.
- **Record the real state, including the boring parts**: what shipped, its commit,
  and what is still off / inert / deferred, and why.
- **When you correct a stale claim, say that it was stale and what it cost.** A
  silent fix teaches nobody; the next reader needs to know that doc has drifted
  before.
- **Before treating a doc as evidence, check it against the code or the data.**
  A status line is a claim, not a fact.

🚨 **A detailed write-up somewhere else does NOT discharge this.** The trap is
that you *did* document the work — thoroughly — in the wrong file, and the
thoroughness makes you feel finished. Concretely, in one session: four fixes
shipped and were pushed; the findings doc marked all four FIXED with commits,
and the per-chunk scope doc had a complete as-built table. **The build-order
doc had never heard of the chunk.** Same session, the previous chunk's header
still read `159a IN PROGRESS, 159b/159c SCOPE-ONLY` when `a` had shipped, `c`
was retracted, and `d`/`e` had both landed — three of four markers false.
Nobody caught any of it; the user asked.

- **Enumerate the state-tracking docs once, then update ALL of them.** Most
  repos have two or three: a findings/architecture doc, per-chunk scope docs,
  and a build-order doc with state markers (`IMPLEMENTED` / `SCOPE-ONLY` /
  `DEFERRED`). They serve different readers and none substitutes for another.
- **The build-order doc is the load-bearing one** — it is what someone reads to
  decide what to build next. A scope doc is where the detail lives, but nobody
  reads a scope doc they don't already know exists. Recording work in two
  places and skipping the third leaves it written down and invisible.
- **When a chunk has sub-parts, re-read the PARENT header after each part
  lands.** Sub-part markers rot fastest, because the header was written when
  none of them had shipped and nothing forces you back to it.
- **Ask "which doc says something that is now false?" before ending the turn** —
  not "did I write it down?". The first question finds stale markers; the
  second is satisfied by the write-up you just finished.

## Cost discipline

Cost is driven by **scope breadth**, not model tier — a reviewer pointed at a diff versus a subsystem differs by roughly an order of magnitude. Give agents the diff and the scope doc, not the repo.

**Let CI do what CI can do.** If a pipeline proves it in a minute, do not spend reasoning on it. Check CI yourself rather than asking a human to paste logs: `gh run list` / `gh run view --log-failed` on the dev box, the GitHub tools (check runs, job logs) in cloud sessions. Prefer pushing over long local test runs when CI is materially faster.

## Multiple agents at once

1. **Separate worktrees** (`isolation: "worktree"`), or
2. **disjoint path ownership**, declared up front, with every commit pathspec-limited (`git commit -m "..." -- <paths>`). 🚨 **Never `git add -A`, `git add .`, or `git commit -a` in a shared checkout** — a plain commit has swept a concurrent agent's uncommitted work into the wrong commit.
3. **Agents commit; they do not push.** You review and push serially, so one push maps to one CI run and a red build is attributable.

🚨 **Ownership stops collisions; it does not stop regressions. Run the ADJACENT
suites yourself before pushing.** Ownership is assigned by who *edits* a file,
but a behaviour change reaches files nobody edits — and that gap is precisely
where nothing is looking. Concretely: three coders, disjoint ownership held
perfectly, nothing swept, every agent's targeted run legitimately green. One
fix raised a timing gate from 24h to 48h; a test in a third file seeded a
30h-old row and asserted it was processed — true before, false after. That file
was outside **every** agent's ownership, so no coder could have found it. I
caught it only by running the neighbouring suites.

- **"Adjacent" means the suites that exercise the changed BEHAVIOUR**, not the
  changed files. The owned tests are the ones the agent already ran; they are
  the least informative thing you can re-run.
- **Not the full suite** if CI is materially faster — push and read CI. Adjacent
  suites are the cheap middle: seconds, and they cover the blind spot CI would
  only find after the push.
- **Re-verify the agent's headline claim when it is cheap.** If a fix has a
  one-command reproduction, run it yourself against the patched code. That is
  what turns "the coder says it is fixed" into a fact you can report.

## Reporting back

Report what happened, not what was hoped for. If tests failed, say so with the output. If a step was skipped, say that. If a subagent's result looks wrong, check it before relaying — subagents are confidently wrong sometimes.

**Verify claims before stating them**, especially ones a person will act on. Trace the code path rather than inferring from a name; state the number that decides it. When you do correct an earlier claim, correct it plainly and move on.

## 🚨 Write chat answers so I actually understand them

This is a hard requirement, not a style preference. I have said "I didn't
understand anything of what you just wrote" — treat that as the failure mode to
design against. **The dense version belongs in the docs and the commit message.
The chat message is for me.**

**Never use a bare identifier as if I know it.** Every `chunk 153a`, `GATE 4`,
`RULE 9`, `F102`, `§25.18`, migration number, flag name, or commit hash must
carry a short description of *what it is* the first time it appears in a
message — every message, not once per conversation. I do not remember the
numbering and I should not have to look it up to read your answer.

- ❌ "Chunk 153a passed gates 1–3 but GATE 4 failed."
- ✅ "The stop-loss test (chunk 153a) cleared the first three checks, but failed
  the fourth one — the check that asks whether a tighter stop hurts the trades
  that were winning. It did, by more than the limit I set in advance."

If a name is genuinely load-bearing, put it in parentheses *after* the plain
description, never before it.

**Structure every answer this way:**

1. **The answer first, in one or two plain sentences.** What happened, and what
   it means for me. Not the method, not the caveats.
2. **Then the reasoning**, in plain words, mechanism before jargon.
3. **Then the detail** — numbers, tables, names — only if it changes what I'd do.

**Other rules:**

- **One number per point.** A number should be doing a job: deciding something,
  or sizing something. If it is decoration, cut it.
- **No table unless I need to compare rows.** Prose beats a ten-row table for
  explaining a conclusion.
- **Say what it means for me**, not just what it measured. "So the stop stays at
  25% for now" beats "the decision is NO CHANGE."
- **Spell out the jargon of this domain too** — not just chunk numbers. Ratios,
  gates, hold-outs, argmax, ceilings: describe the idea, then name it.
- **Length is not the goal — clarity is.** A longer plain-language explanation is
  better than a short dense one. Do not compress by dropping the words that make
  it make sense.

If you are unsure whether something is clear, assume it is not, and explain it.
