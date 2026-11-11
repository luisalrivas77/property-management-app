# Hausbuch

Single-household landlord workspace (built SaaS-ready) that turns bank statements,
invoices and letters into a traceable property ledger, a draft Anlage V per tax year,
and a running profit view through to sale.

## Reference spec

The architecture spec below is the source of truth for scope, domain model, pipeline,
AI layer, tax rules, stack and delivery phases. Read it before designing or changing
anything, and check every change against its seven architecture principles.

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
- Phase 2, AI inbox with master-data pre-fill first: `docs/phases/phase-2.md` (spec merged 6 Nov 2026 in PR #24; steps 1 and 3 merged and deployed 6 Nov 2026; invoice reading fix D35–D37 merged and deployed 7 Nov 2026 (PR #28); step 4 matching and inbox merged and deployed 7 Nov 2026 (PR #30); step 5 capture, evals and scrub merged and deployed 7 Nov 2026 (PR #32); step 2 tenant documents, settings switches and erasure scrub merged and deployed 7 Nov 2026 (PR #35); loan payout and purchase price categories (migration 0009) and the re-check of waiting inbox lines deployed 8 Nov 2026 (PRs #61, #62); the loan's interest-only phase before its first annuity (migration 0010) deployed 8 Nov 2026 (PR #64); loan contract pre-fill of the contract date, follow-up rate and interest-only box, and a page turn button in capture, deployed 8 Nov 2026 (PR #66); the review page renamed to "Zu prüfen" with sections, a sidebar count and each bank line's status, deployed 8 Nov 2026 (PR #69); learned rules that tell several kinds of payment from one IBAN apart by the last confirmed amount (migration 0011) deployed 8 Nov 2026 (PR #71); the AI categorization fix (every call had been refused since step 4 went live) with a re-ask button, deployed 8 Nov 2026 (PR #73); a Mietverwaltung category, the loan named on the model's loan suggestions and a learned rule for a split answer (migration 0012), deployed 9 Nov 2026 (PR #75); a model's answer for a bank line of 0 € booked without review (D52), deployed 9 Nov 2026 (PR #77); a title and date read for every document and a subtype for the property-management contract (D130), widened the same day to a catalogue of about forty document kinds (D131), both with migration 0021, specced 10 Nov 2026 and merged and deployed 10 Nov 2026 (PR #135, images 3515d91440f0, migration 0021 applied); a Verwaltervertrag's Betrag filled with its monthly fee plus MwSt., computed by code, deployed 10 Nov 2026 (PR #137, images bc8526af476b); a building/land split read from a document becomes the AfA basis when none is chosen yet (D133), deployed 10 Nov 2026 (PR #142, images 3cfae4929f83); every invoice and Bescheid split by its cost lines and sorted by tax treatment, with costs paid from another account proposed on their Anlage V line and counted once (D135, principle 7), merged and deployed 11 Nov 2026 (PR #150, images e669516b2fe7; such a repair counts in the 15 % test since phase 3 D137, deployed 11 Nov 2026, PR #154, images 00c7e2d68e08); a Grundbuch bill sent by a Landesjustizkasse sorted as Grundbuch, not Sonstige, by its subject or lines, deployed 11 Nov 2026 (PR #152, images 05f07bd45808); a bill that is only a loan cost gets the subtype „Rechnung zur Finanzierung“, set by code, with plain notes (D138, migration 0022), merged and deployed 11 Nov 2026 (PR #156, images a9baa13e4e07, migration 0022 applied); the flat's Wohnfläche read from a Wohnflächenberechnung (the household's row picked by its share of the building or its WE number, else every flat offered as a one-click choice) or the Kaufvertrag, and the Wohnfläche to-do's upload button (D139), merged and deployed 11 Nov 2026 (PR #162, images 2d1e77f2b25d); a Grunderwerbsteuerbescheid for one buyer's share keeps the typed total: the row takes the notice and a second row waits for the other buyer's notice (D145), merged and deployed 11 Nov 2026 (PR #168, images 9c56247cb5fa); a card that attaches a document to a Nebenkosten row says so, and a Grunderwerbsteuerbescheid gets the Steuerjahr of its due date (D146), merged and deployed 11 Nov 2026 (PR #173, images 89b4e68ed6ea); one pre-filled form per document: every field, read or computed, in the suggestion and saved by its one button, no second typing form beside it (D132), deployed 10 Nov 2026 (PR #141, images d6056418983c); see its status table)
- Phase 3, tax year (rule set 2026, Anlage V draft, checklist, 15 % tracker, exports): `docs/phases/phase-3.md` (spec and step 0 (schemas, migration 0013, rule sets, fixtures) merged 9 Nov 2026 in PR #79 and migration 0013 applied; steps 1 (tax rules, Anlage V draft, Steuern screen) and 2 (tax inputs, certificate pre-fill, checklist entries) merged and deployed 9 Nov 2026 in PR #81; step 3 (ELSTER field sheet and yearly archive) merged and deployed 9 Nov 2026 in PR #84; every gap on every screen a plain to-do with a button or link, and "Was noch fehlt" lists on Anlage V and Übersicht (D136, principle 7), merged and deployed 10 Nov 2026 (PR #148, images ac28bfb6e549); a repair paid from another account counted in the 15 % test, moved into the AfA basis when it crosses the limit (D137), merged and deployed 11 Nov 2026 (PR #154, images 00c7e2d68e08); the Erweiterung guard (PR #85) and a shorter useful life from a Gutachten (migration 0014, PR #88) and the fitted kitchen as its own asset (migration 0015, PR #93) merged and deployed 9 Nov 2026; a seller's forwarded rent: one to-do per payment and an „Als Miete buchen“ button, and „Als Miete zählen“ re-books the lines before the handover as rent (D140), merged and deployed 11 Nov 2026 (PR #161, images c7760f6b8d07); a deposit proven by its bank payment, with „Kaution aus Bankumsatz zuordnen“ splitting one deposit line to the tenancies, and no Kautionsnachweis for a tenancy that ended before the handover (D142), merged and deployed 11 Nov 2026 (PR #166, images 965013722d63); payment proofs a Finanzamt clerk can read in the yearly archive (`zahlungsnachweise.pdf`, payee and purpose for costs only) (D144), merged and deployed 11 Nov 2026 (PR #167, images bc781287de2f); step 4 (closing) not started; see its status table)
- Phase 4, performance (actuals, derived tax effect, projection to sale, §23 check, IRR): `docs/phases/phase-4.md` (spec and step 0 (schemas, migration 0016, tariff 2026, fixtures) merged 9 Nov 2026 in PR #95, migration 0016 applied and deployed the same day (images 6cbb4ed94dd1); step 1 (tariff, income inputs on the Eigentümer page, Steuerwirkung on Steuern) merged and deployed 9 Nov 2026 in PR #97 (images bc0f3768c697); a spec correction (net cash subtracts the tax effect) merged in PR #100; steps 2 (actuals, projection, sale) and 3 (Planung screen) merged and deployed 9 Nov 2026 in PRs #98 and #101 (images 6664c0b228d2); an electricity category and a corrected Pauschale check (D92, migration 0017) deployed 9 Nov 2026 (PR #103, images 2aad79144561); the §23 Freigrenze as rule data and the kitchen AfA added back at a taxable sale (D93, D94) deployed 9 Nov 2026 (PR #105, images 869c0526e295); step 4 (closing against Luis's spreadsheet) not started; step 5 (Betriebskostenabrechnung) waits for the first prepayment lease)
- Phase 4b, Lohn und Einkommensteuer (Anlage N for both owners, whole-return estimate, the yearly questionnaire "Jahresfragen"): `docs/phases/phase-4b.md` (spec merged 10 Nov 2026 in PR #107; step 0 (schemas, migration 0018, rule data, eleven fixtures) written 10 Nov 2026 and merged 10 Nov 2026 in PR #108; step 1 (Anlage N rules, whole-return estimate, questionnaire rules and services, privately paid rental costs, the hook into phase 4's tax effect; review fixes and D113, D114) merged and deployed 10 Nov 2026 in PR #109 with migration 0018 applied (images 7f517c3472aa); step 2 (reading the Lohnsteuerbescheinigung and the Gehaltsabrechnung with the identifier scrub; review fixes) merged and deployed 10 Nov 2026 in PR #111 (images 5db0d59de899); step 3 (Anlage N tab, Gesamt card, Jahresfragen flow, Kinder and Haushalt blocks) merged and deployed 10 Nov 2026 in PR #115 (images 00bf528b429c); step 4 (Anlage N field sheet per owner and the archive's Einkommensteuer pages) merged and deployed 10 Nov 2026 in PR #117 (images aceb0838f80c); the step 3 follow-ups (open questions by text, Steuerklassen sentence, page and quote, payslip months, sources per line, zumutbare Belastung) deployed 10 Nov 2026 (PR #120, images 4627b63ac8bf); step 5 (closing) in 2027; reading the Einkommensteuerbescheid (D129, step 6, migration 0020) specced 10 Nov 2026 in PR #123 and merged and deployed 10 Nov 2026 (PR #128, images d9cadee6555d, migration 0020 applied), with a review fix that keeps a negative zvE's sign, and upload buttons for the pay documents on Eigentümer and Anlage N deployed 10 Nov 2026 (PR #124, images 9db19b32744d); a privately paid loan cost (Geldbeschaffungskosten) as its own class of privately paid costs (D134) deployed 10 Nov 2026 (PR #142, images 3cfae4929f83); the household's own answers stay outside the repo)
- Phase 5, automation (FinTS sync of the rental account, email-in from the household's Gmail label, deadline reminders with a digest email, rule data 2027): `docs/phases/phase-5.md` (spec drafted and revised with Luis's decisions 10 Nov 2026, merged 10 Nov 2026 in PR #119; step 0 (schemas, migration 0019, fixtures) merged 10 Nov 2026 in PR #125 with migration 0019 applied and deployed (images b176fe3e3818); step 4 (email-in from the Gmail label, with the review's fixes: a card when the poll cannot log in, find its label or reach Gmail, and one refused mail no longer blocks the rest) merged and deployed 10 Nov 2026 in PR #129 (images e8b0dcc718e6, no migration); it stays inert until the four inbox secrets are set (runbook step 16); step 3 (in-app reminders: eleven kinds on Übersicht and Zu prüfen with acks, the Grundsteuerbescheid expected from the first full year of ownership, the `grundsteuer_schedule` tax input from the Bescheid; review fixes: the Grundsteuer card outside the draft) merged and deployed 10 Nov 2026 in PR #139 (images edebd21b1ec3, no migration); step 1 (the manual FinTS sync of the rental account: read-only client, sync job with pushTAN approval by polling, the Bankabruf card on the account page; review fixes and D141) merged and deployed 11 Nov 2026 in PR #160 (images 2b2a05dbecae, no migration), the first real sync against the bank refused on 11 Nov 2026 with only the general code 9050 kept, now fixed to keep the bank's reason code (PR #175, images f06fe99e9baf), the first successful sync still to run (Q-O34–Q-O36); step 3b (the digest email) next, waiting for the sending domain's DNS records; the DK product id arrived 11 Nov 2026 and is a container secret, never published)

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
