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
- Phase 2, AI inbox with master-data pre-fill first: `docs/phases/phase-2.md` (spec draft)

## UI mockups

Six screen mockups (PNG + static HTML) live in `docs/mockups/`. Before building or changing a
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
