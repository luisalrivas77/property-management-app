# Phase 1 spec: Vault and ledger

Nov 4, 2026 · spec PR, no implementation

This is the spec PR that ARCHITECTURE.md requires before implementation starts. It fixes the
contract the implementation PR codes against:

| Artifact | Where | Status |
| --- | --- | --- |
| Pydantic schemas | `backend/src/hausbuch/schemas/` | Final for phase 1 |
| Migration (DDL, RLS, grants, append-only triggers) | `backend/migrations/versions/0001_phase1_vault_ledger.py` | Tested against Postgres 16 |
| Rule fixtures | `backend/tests/fixtures/phase1/*.yaml` | Validated against the schemas |
| Rule tests | `backend/tests/rules/` | Skipped until `hausbuch.rules.*` exists, then must pass unchanged |
| Schema tests | `backend/tests/db/`, `backend/tests/test_fixtures_valid.py` | Passing |

**Done when** (from ARCHITECTURE.md): the current flat, its tenancy, its loan and all 2026
paperwork are in, and History shows purchase → move-in correctly. `timeline.yaml` encodes that
second half as a fixture.

## Scope

**In:** workspace, app user (no credentials yet), owners and ownership shares, properties with
itemized acquisition costs and building/land split candidates, units, tenants, tenancies with
parties and an append-only rent schedule, loans with a computed repayment schedule, the
document vault (content-addressed originals, manual typing, versioned extractions, links), the
event log, and the History timeline.

**Out (later phases):** accounts, transactions, allocations, rules learned from reviews and the
review queue (phase 2); AI provenance columns (phase 2); tax rule sets and Anlage V (phase 3);
auth credential tables (passkey / TOTP, with the API PR); the retention auto-delete job.

## Decisions taken in this spec

These fill gaps in ARCHITECTURE.md. Each one is a place to push back in review.

| # | Decision | Why |
| --- | --- | --- |
| D1 | The RLS key is **`workspace_id`**, not "owner id". ARCHITECTURE.md updated to match. | The domain model already has an Owner entity (the two spouses). One household = one workspace, holding both Owners. |
| D2 | Every child references its parent by **composite FK `(workspace_id, id)`**. | RLS hides other workspaces' rows, but a plain FK would still accept another workspace's id. This makes cross-workspace references impossible even if a query bug slips through. |
| D3 | Closed value sets are **text + CHECK**, mirrored by `StrEnum`s; a test keeps them equal. | Adding a value is one `ALTER`; Postgres enums are awkward to change in migrations. |
| D4 | **Current-state tables + event log, written in one transaction**; not full event sourcing. Milestone dates (move-in, purchase) change only through a correcting event. | Simple reads, and principle 4 still holds: every change is an event, history is never rewritten. |
| D5 | **No tenant personal data in event payloads.** `tenant.updated` lists changed field names only. Erasure nulls the tenant's personal columns in place and sets `erased_at`. | Events are append-only and GDPR erasure is not; this keeps both. |
| D6 | Tenancy "type" is split into **`rent_model`** (fixed / Staffel / Index) and **`fixed_term_until`** (null = unbefristet). | The spec's "unbefristet / Staffel / Index" mixes two independent things: a Staffelmiete can be unbefristet. |
| D7 | **`notarized_on`** (starts the §23 Spekulationsfrist) and **`purchase_date`** = Übergang von Besitz, Nutzen und Lasten (AfA starts in that month). | The spec names both dates; this pins which is which. |
| D8 | Nebenkosten are **itemized** (`property_acquisition_cost`). Building/land splits are **append-only candidates**; the property selects one. | Principle 2 (traceable AfA basis), and phase 3 can warn when the contract split is far above the BMF result because both are kept. |
| D9 | `document.type` is the classifier's 9 types; an optional **`subtype`** covers what retention and the AfA basis need (Kaufvertrag, lease, Gutachten, …). **One document per file per workspace.** | Keeps the AI layer's output contract unchanged; re-uploads dedup to the same document. |
| D10 | The loan schedule is **computed, not stored**: 30E/360, in arrears, rounded half-up per period, payment unchanged when the rate switches. `follow_up_rate` null = keep the current rate. **Interest accrues per day at the rate in force that day**, so a period straddling `fixed_until` is split at the day after it (changed 5 Nov 2026; the first version charged the whole period at the new rate once its due date passed `fixed_until`, which overcharged the last fixed month). | Deterministic and reproducible (principle 1). Conventions are listed in `loan_schedule.yaml`. |
| D11 | Retention periods end on **31 Dec of (base year + N)**. | The conservative reading of the defaults, never earlier than "date + N years". Matches §147 AO for tax records. |
| D12 | A wrong rent row is **superseded** by a `correction` row (`supersedes_id`), never edited. | Rent changes are dated rows, never edits (domain model). |

## Data model

18 tables. Every table has `workspace_id`, forced RLS (`workspace_isolation` policy on
`current_workspace_id()`), and grants for the `hausbuch_app` role (least privilege; see
`APP_GRANTS` in the migration).

| Table | Holds | Mutability |
| --- | --- | --- |
| `workspace` | The household (RLS tenant) | Name only |
| `app_user` | Person who signs in | Read-only to the app |
| `owner` | Legal owner, Steuer-ID reference, marginal rate | Mutable + events |
| `property` | Address, kind, build year, both purchase dates, price, selected split, ownership end | Mutable + events |
| `property_ownership` | Owner shares as exact fractions | Mutable + events |
| `property_acquisition_cost` | Grunderwerbsteuer, notary, land registry, broker, other | Mutable + events |
| `property_price_split` | Building share candidates by source, with evidence and BMF inputs | **Append-only** |
| `unit` | Label, m², rooms, MEA fraction | Mutable + events |
| `tenant` | Personal data, IBAN as mask + keyed HMAC | Mutable + events; erased in place |
| `tenancy` | Dates, rent model, term, Kaution and its account | Mutable + events |
| `tenancy_tenant` | Tenancy parties and their roles | Mutable + events |
| `rent_schedule` | Dated Kaltmiete + NK-Vorauszahlung rows | **Append-only** |
| `loan` | Lender, terms, Zinsbindung, follow-up rate, account | Mutable + events |
| `document_file` | Original bytes' SHA-256, size, media type, storage key | **No update**; delete reserved for retention |
| `document` | Source, type/subtype, title, dates, tax year | Mutable + events |
| `document_extraction` | Versioned typed fields (manual in phase 1) | **No update** |
| `document_link` | Document → exactly one owner/property/unit/tenant/tenancy/loan | Insert/delete + events |
| `event` | The domain log | **Append-only**, no truncate |

IBANs (tenant, Kaution account, loan account) are stored as `DE89 **** 3000` plus
HMAC-SHA256 under a key from the secrets manager. The plaintext is accepted on input only and is
never stored or returned.

### Invariants and where they are enforced

| Invariant | Enforced by |
| --- | --- |
| Workspace isolation (read and write) | RLS, forced, on every table |
| No cross-workspace references | Composite FKs |
| No overlapping tenancies on a unit (inclusive dates, open end = forever) | `EXCLUDE USING gist` |
| At most one primary tenant | Partial unique index |
| Shares ≤ 1, amounts ≥ 0 / > 0, rates in [0, 1), dates in order | CHECK |
| Appraisal split has evidence; BMF split has its inputs | CHECK |
| Selected split belongs to the same property | Composite FK |
| Rent correction stays within its tenancy, at most once per row | Composite FK + UNIQUE |
| Event type prefix = entity type; corrected at most once | CHECK + UNIQUE |
| Erased tenant has no personal data | CHECK |
| Document link has exactly one target, no duplicates | CHECK + `UNIQUE NULLS NOT DISTINCT` |
| Append-only tables | Trigger (`forbid_mutation`); holds even for the table owner |
| Ownership shares sum to exactly 1 | Service layer · `validation.yaml` |
| Exactly one primary tenant (at least one) | Service layer · `validation.yaml` |
| Rent rows fall inside the tenancy | Service layer · `validation.yaml` |
| A correction repeats the corrected event's type and entity | Service layer · `validation.yaml` |
| Event payload matches its type's schema; effective date required/forbidden per type | `Event` validator (`schemas/events.py`) |

## Events

29 event types, catalogued in `EVENT_CATALOG` with payload schema, effective-date rule and
meaning. Envelope: `seq` (recording order), `type`, entity, `property_id` / `unit_id` scope,
`effective_date` (the domain date; null = not a milestone), actor (`user` / `system` / `ai`),
`correlation_id` (one command), `corrects_event_id`, `payload`.

- **History** (per property, or per unit including property-level events) = milestones only,
  corrections collapsed, ordered by (effective date, root event seq).
- **Audit trail** = every event in `seq` order, corrections included. Same table, two views.

## Rule contracts

Pure functions in `hausbuch.rules.*`; no I/O, no model calls. Rules signal errors by raising an
exception with a `code` attribute (plus `at_number` for loan errors).

| Function | Fixture | Notes |
| --- | --- | --- |
| `loan_schedule.build_schedule(LoanTerms) -> LoanSchedule` | `loan_schedule.yaml` | 6 cases: full and partial first period, rate switch at a month end to payoff, rate switch mid-period, two never-amortizing errors |
| `rent.rent_at(rows, at: date) -> RentScheduleEntry \| None` | `rent_at.yaml` | Corrections by amount and by date; tie-break |
| `timeline.build_history(events, TimelineScope) -> list[TimelineEntry]` | `timeline.yaml` | Done-criterion scenario: purchase, loan, possession (corrected twice), two lettings, a second property; result independent of input order |
| `retention.document_retention(DocumentRetentionInput, RetentionPolicy) -> RetentionResult` | `retention.yaml` | Every class and hold, custom policy |
| `retention.tenant_retention(TenantRetentionInput, RetentionPolicy) -> RetentionResult` | `retention.yaml` | |
| `iban.normalize_iban / mask_iban / iban_hmac` | `iban.yaml` | Test key only; mod-97 and length checks |
| `validation.check_ownership_shares / check_tenancy_parties / check_rent_row / check_correction -> list[str]` | `validation.yaml` | The service-layer invariants above |

Error codes (stable API contract): `loan.payment_does_not_cover_interest`,
`iban.malformed`, `iban.bad_length`, `iban.bad_checksum`, `ownership.shares_must_sum_to_one`,
`tenancy.no_tenant`, `tenancy.exactly_one_primary`, `rent_schedule.outside_tenancy`,
`event.correction_mismatch`.

The fixtures were cross-checked by running the rule tests against a throwaway reference
implementation that is not part of this PR. All 60 cases passed, so the fixtures agree with
each other and with the conventions written in them.

## Open questions

1. **Follow-up rate default.** D10 reads "defaulting to today's rate" as *the loan's current
   rate*. If it meant the market rate on the switch date, `LoanTerms` needs a dated rate input.
2. **Payment at the rate switch.** D10 keeps the monthly payment. Banks usually agree a new
   annuity, often from a chosen Tilgung %. Worth a `follow_up_payment` field?
3. **Check against your real Tilgungsplan.** The loan fixtures are synthetic. Before phase 1
   closes, compare the first rows with your bank's plan; if the bank uses act/360 or another
   first-period rule, the conventions change, not the code.
4. **Not modelled yet:** Sondertilgungen, Teilauszahlungen, Bereitstellungszinsen, Disagio.
   Disagio is a Geldbeschaffungskosten item for phase 3.
5. **Inventar** (e.g. Einbauküche) inside the purchase price changes the AfA basis. Add it in the
   phase 3 spec?
6. **Tenant with no tenancy** (an applicant) is held as `unlinked`. Should it be deleted on a
   short clock instead?
7. **Retraction.** A correction cannot change an event's entity. An event recorded against the
   wrong tenancy would need a `*.retracted` event type.

## Running it

```bash
cd backend
uv sync --python 3.12
uv run pytest -q -rs     # rule tests skip; db tests skip without a database
HAUSBUCH_TEST_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest -q
HAUSBUCH_DATABASE_URL=postgresql+psycopg://hausbuch_owner:…@localhost/hausbuch uv run alembic upgrade head
```

Run migrations as the schema owner, not as a superuser. The app connects as `hausbuch_app`
and sets `app.workspace_id` per transaction (`SET LOCAL`).

## Next: implementation PR

1. `hausbuch.rules.*` until every test in `tests/rules/` passes with fixtures unchanged.
2. Service layer: each command writes state + events in one transaction, enforcing the
   service-layer invariants.
3. FastAPI endpoints from these schemas; object storage for originals; auth.
4. Enter the real flat, tenancy, loan and 2026 paperwork; check History.
