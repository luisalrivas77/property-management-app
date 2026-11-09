# Hausbuch

Single-household landlord workspace (built SaaS-ready) that turns bank statements,
invoices and letters into a traceable property ledger, a draft Anlage V per tax year,
and a running profit view through to sale.

## Reference spec

The architecture spec below is the source of truth for scope, domain model, pipeline,
AI layer, tax rules, stack and delivery phases. Read it before designing or changing
anything, and check every change against its six architecture principles.

@docs/ARCHITECTURE.md

## Working agreement and agents

The main session orchestrates: it scopes, judges results and does small changes itself, and
hands bigger work to the agents in `.claude/agents/` (coder, reviewer, architect). How and when,
plus the reporting and chat-writing rules, are in the working agreement:

@.claude/WORKING_AGREEMENT.md

## Phase specs

Each phase's spec (schemas, migrations, rule fixtures, decisions) lives in `docs/phases/`.
Read the current phase's spec before implementing; rule fixtures are the contract and change
only in a spec PR, never to make an implementation pass.

- Phase 1, vault and ledger: `docs/phases/phase-1.md` (closed 6 Nov 2026)
- Phase 2, AI inbox with master-data pre-fill first: `docs/phases/phase-2.md` (spec merged 6 Nov 2026 in PR #24; steps 1 and 3 merged and deployed 6 Nov 2026; invoice reading fix D35–D37 merged and deployed 7 Nov 2026 (PR #28); step 4 matching and inbox merged and deployed 7 Nov 2026 (PR #30); step 5 capture, evals and scrub merged and deployed 7 Nov 2026 (PR #32); step 2 tenant documents, settings switches and erasure scrub merged and deployed 7 Nov 2026 (PR #35); loan payout and purchase price categories (migration 0009) and the re-check of waiting inbox lines deployed 8 Nov 2026 (PRs #61, #62); the loan's interest-only phase before its first annuity (migration 0010) deployed 8 Nov 2026 (PR #64); loan contract pre-fill of the contract date, follow-up rate and interest-only box, and a page turn button in capture, deployed 8 Nov 2026 (PR #66); the review page renamed to "Zu prüfen" with sections, a sidebar count and each bank line's status, deployed 8 Nov 2026 (PR #69); learned rules that tell several kinds of payment from one IBAN apart by the last confirmed amount (migration 0011) deployed 8 Nov 2026 (PR #71); the AI categorization fix (every call had been refused since step 4 went live) with a re-ask button, deployed 8 Nov 2026 (PR #73); a Mietverwaltung category, the loan named on the model's loan suggestions and a learned rule for a split answer (migration 0012), deployed 9 Nov 2026 (PR #75); a model's answer for a bank line of 0 € booked without review (D52), deployed 9 Nov 2026 (PR #77); see its status table)
- Phase 3, tax year (rule set 2026, Anlage V draft, checklist, 15 % tracker, exports): `docs/phases/phase-3.md` (spec and step 0 (schemas, migration 0013, rule sets, fixtures) merged 9 Nov 2026 in PR #79 and migration 0013 applied; steps 1 (tax rules, Anlage V draft, Steuern screen) and 2 (tax inputs, certificate pre-fill, checklist entries) merged and deployed 9 Nov 2026 in PR #81; steps 3 (exports) and 4 (closing) not started; see its status table)

Design plans for later phases (not specs; only what the Decisions table records is agreed):

- Household finances, phases 6–9 (all accounts, crypto, Anlage KAP and SO, financial health):
  `docs/design/household-finances.md` (agreed 6 Nov 2026, Luis's answers in §10; not specced)

## UI mockups

Fourteen screen mockups (PNG + static HTML) live in `docs/mockups/`: 01–06 for the landlord module, 07–12 (with 09b and 09c, the Anlagen tabs) for the household phases 6–9. Before building or changing a
screen, read `docs/mockups/README.md` and look at the matching PNG. They set layout, content,
flow and the visual style (tokens and classes in `frontend/src/app/globals.css`); labels stay
German; their numbers are sample data, never fixtures.

## Layout and commands

- `backend/`: Python 3.12, managed with uv. Schemas in `src/hausbuch/schemas/`, pure rules in
  `src/hausbuch/rules/`, hand-written SQL migrations in `migrations/versions/`, YAML rule
  fixtures in `tests/fixtures/`.
- From `backend/`:
  - `uv sync --python 3.12`
  - `uv run ruff check . && uv run ruff format --check .`
  - `uv run pytest -q -rs` (db tests need `HAUSBUCH_TEST_ADMIN_URL`, a superuser URL)
  - `uv run alembic upgrade head` (needs `HAUSBUCH_DATABASE_URL`; run as the schema owner,
    not a superuser)

## Working rules

- If a change conflicts with the spec, stop and flag it rather than silently diverging.
- If a decision changes the architecture, update `docs/ARCHITECTURE.md` in the same PR
  (including the Decisions table).
- Each delivery phase starts with a short spec PR (schemas, rule fixtures, eval cases)
  before implementation code.
