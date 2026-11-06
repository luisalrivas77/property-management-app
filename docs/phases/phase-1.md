# Phase 1 spec: Vault and ledger

Nov 4, 2026 · spec PR, no implementation

This is the spec PR that ARCHITECTURE.md requires before implementation starts. It fixes the
contract the implementation PR codes against:

| Artifact | Where | Status |
| --- | --- | --- |
| Pydantic schemas | `backend/src/hausbuch/schemas/` | Final for phase 1 |
| Migration (DDL, RLS, grants, append-only triggers) | `backend/migrations/versions/0001_phase1_vault_ledger.py` | Tested against Postgres 16 |
| Rule fixtures | `backend/tests/fixtures/phase1/*.yaml` | Validated against the schemas |
| Rule tests | `backend/tests/rules/` | **Passing** against `hausbuch.rules.*` (5 Nov 2026); fixtures unchanged except the approved loan-schedule spec change (D10) |
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
TOTP (sign-in is passkeys only, decided 5 Nov 2026; the passkey, session and invite tables
arrived with step 3, migration `0003`); the retention auto-delete job.

## Decisions taken in this spec

These fill gaps in ARCHITECTURE.md. Each one is a place to push back in review.

| # | Decision | Why |
| --- | --- | --- |
| D1 | The RLS key is **`workspace_id`**, not "owner id". ARCHITECTURE.md updated to match. | The domain model already has an Owner entity (the two spouses). One household = one workspace, holding both Owners. |
| D2 | Every child references its parent by **composite FK `(workspace_id, id)`**. | RLS hides other workspaces' rows, but a plain FK would still accept another workspace's id. This makes cross-workspace references impossible even if a query bug slips through. |
| D3 | Closed value sets are **text + CHECK**, mirrored by `StrEnum`s; a test keeps them equal. | Adding a value is one `ALTER`; Postgres enums are awkward to change in migrations. |
| D4 | **Current-state tables + event log, written in one transaction**; not full event sourcing. Milestone dates (move-in, purchase) change only through a correcting event. | Simple reads, and principle 4 still holds: every change is an event, history is never rewritten. |
| D5 | **No tenant personal data in event payloads.** `tenant.updated` lists changed field names only. Erasure nulls the tenant's personal columns in place and sets `erased_at`. Erasure (5 Nov 2026): always refused while a tenancy runs (the data performs the lease); after move-out it waits for the 4-year default unless the tenant asks and the landlord honours it (`on_request`); `tenant.erased` records the basis. The deposit account is the landlord's (Luis's Kautionskonto is in his name), so erasure leaves `tenancy.deposit_*` alone. | Events are append-only and GDPR erasure is not; this keeps both. |
| D6 | Tenancy "type" is split into **`rent_model`** (fixed / Staffel / Index) and **`fixed_term_until`** (null = unbefristet). | The spec's "unbefristet / Staffel / Index" mixes two independent things: a Staffelmiete can be unbefristet. |
| D7 | **`notarized_on`** (starts the §23 Spekulationsfrist) and **`purchase_date`** = Übergang von Besitz, Nutzen und Lasten (AfA starts in that month). | The spec names both dates; this pins which is which. |
| D8 | Nebenkosten are **itemized** (`property_acquisition_cost`). Building/land splits are **append-only candidates**; the property selects one. | Principle 2 (traceable AfA basis), and phase 3 can warn when the contract split is far above the BMF result because both are kept. |
| D9 | `document.type` is the classifier's 9 types; an optional **`subtype`** covers what retention and the AfA basis need (Kaufvertrag, lease, Gutachten, …). **One document per file per workspace.** | Keeps the AI layer's output contract unchanged; re-uploads dedup to the same document. |
| D10 | The loan schedule is **computed, not stored**: 30E/360, in arrears, rounded half-up per period, payment unchanged when the rate switches. `follow_up_rate` null = keep the current rate. **Interest accrues per day at the rate in force that day**, so a period straddling `fixed_until` is split at the day after it (changed 5 Nov 2026; the first version charged the whole period at the new rate once its due date passed `fixed_until`, which overcharged the last fixed month). | Deterministic and reproducible (principle 1). Conventions are listed in `loan_schedule.yaml`. |
| D11 | Retention periods end on **31 Dec of (base year + N)**. | The conservative reading of the defaults, never earlier than "date + N years". Matches §147 AO for tax records. |
| D12 | A wrong rent row is **superseded** by a `correction` row (`supersedes_id`), never edited. | Rent changes are dated rows, never edits (domain model). |

## Data model

21 tables: 18 from migration `0001`, and the three `auth_*` tables from `0003` (phase 1 step
3, `docs/phases/phase-1-step-3.md` §6.2). Every table has `workspace_id`, forced RLS
(`workspace_isolation` policy on `current_workspace_id()`), and grants for the `hausbuch_app`
role (least privilege; see `APP_GRANTS` in the migrations). The auth tables also have a
SELECT-only `auth_lookup` policy (see the invariants below).

| Table | Holds | Mutability |
| --- | --- | --- |
| `workspace` | The household (RLS tenant) | Name only |
| `app_user` | Person who signs in | Insert by invite; never updated or deleted by the app |
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
| `auth_passkey` | Passkey (WebAuthn credential): credential id (unique across workspaces), COSE public key, sign count, label | Insert, update (sign count, last use, label), delete; no events (step 3 Q6) |
| `auth_session` | Server-side session: SHA-256 of the cookie token, expiry (absolute and idle), revocation | Insert, update (last seen, revoked), delete of expired rows; no events |
| `auth_invite` | One-time enrollment link: SHA-256 of the URL token, purpose `enrollment`, expiry, use | Insert, update (used), delete; no events |

IBANs (tenant, Kaution account, loan account) are stored as `DE89 **** 3000` plus
HMAC-SHA256 under a key from the secrets manager. The plaintext is accepted on input only and is
never stored or returned.

### Invariants and where they are enforced

| Invariant | Enforced by |
| --- | --- |
| Workspace isolation (read and write) | RLS, forced, on every table |
| Unset workspace means no rows visible, except SELECT on the three auth tables while `app.auth_lookup = 'on'` (transaction-local), set only by `hausbuch.auth.lookup` | RLS: the `auth_lookup` policy is `FOR SELECT` and exists on those three tables only (`tests/db/test_auth_tables.py`) |
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
| `loan_schedule.build_schedule(LoanTerms) -> LoanSchedule` | `loan_schedule.yaml` | 10 cases: full and partial first period, rate switch at a month end, mid-period and around clamped month ends, two never-amortizing errors, the term cap |
| `rent.rent_at(rows, at: date) -> RentScheduleEntry \| None` | `rent_at.yaml` | Corrections by amount and by date; tie-break |
| `timeline.build_history(events, TimelineScope) -> list[TimelineEntry]` | `timeline.yaml` | Done-criterion scenario: purchase, loan, possession (corrected twice), two lettings, a second property; result independent of input order |
| `retention.document_retention(DocumentRetentionInput, RetentionPolicy) -> RetentionResult` | `retention.yaml` | Every class and hold, custom policy |
| `retention.tenant_retention(TenantRetentionInput, RetentionPolicy) -> RetentionResult` | `retention.yaml` | |
| `iban.normalize_iban / mask_iban / iban_hmac` | `iban.yaml` | Test key only; mod-97 and length checks |
| `validation.check_ownership_shares / check_tenancy_parties / check_rent_row / check_correction -> list[str]` | `validation.yaml` | The service-layer invariants above |
| `document_checklist.build_checklist(ChecklistFacts, tax_year, today) -> list[ChecklistItem]` | `document_checklist.yaml` | Spec amendment 3 (6 Nov 2026); 31 cases, each also run with its input lists shuffled; merged as #20 and deployed 6 Nov 2026 |

Error codes (stable API contract): `loan.payment_does_not_cover_interest`, `loan.term_too_long`,
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
uv run pytest -q -rs     # db tests skip without a database
HAUSBUCH_TEST_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest -q
HAUSBUCH_DATABASE_URL=postgresql+psycopg://hausbuch_owner:…@localhost/hausbuch uv run alembic upgrade head
```

Run migrations as the schema owner, not as a superuser. The app connects as `hausbuch_app`
and sets `app.workspace_id` per transaction (`SET LOCAL`).

## Implementation status

As of 5 Nov 2026: steps 1–2 merged to `main` in #4, step 3 in #6, #7 and #8. Each step was built by a coder agent
and reviewed by a reviewer agent; every review finding was fixed or is listed below.

1. **DONE: rules** (`hausbuch.rules.*`): loan schedule, rent in force, History timeline,
   retention, IBAN, validation. Every test in `tests/rules/` passes.
   - Review fix: first-period half-cent rounding.
   - Spec change: per-day interest accrual at the end of the fixed rate (D10).
2. **DONE: service layer.**
   - **Foundation:** settings, Core tables with a drift test against the migration, and
     workspace-scoped transactions. The engine refuses DB roles that bypass RLS.
   - **Event writer:** `append_event` validates the catalog, the scope and corrections before
     any SQL.
   - **Domain services:** owners, properties, units, tenants, tenancies (rent schedule,
     corrections), loans, and History / audit trail.
   - **Document vault:** content-addressed originals, dedup, typing with extraction versions,
     links, and retention dates.
   - **Done criterion:** History built through the services matches all four `timeline.yaml`
     cases (`tests/db/test_history.py`).
3. **DONE: API and first screens**, designed in `docs/phases/phase-1-step-3.md`: the API, passkey
   sign-in, the S3 store, all screens, deploy artefacts and Playwright e2e in CI, all reviewed. Original note: FastAPI endpoints; passkey sign-in (decided 5 Nov 2026);
   an S3 store behind `BlobStore`; the Next.js screens. The API's error handler must build
   422 responses with `errors(include_input=False)`: `hide_input_in_errors` (spec amendment 1)
   only masks the error text, not the structured errors, which can carry IBANs and tax numbers.
4. **IN PROGRESS: deploy and real data.** Follow `deploy/scaleway.md`, record its first-deploy checks, then
   enter the real flat, tenancy, loan and 2026 paperwork; check History.
   Compare the loan schedule with the bank's Tilgungsplan (open question 3).
   - 5 Nov 2026: runbook steps 1–4 done. The VPC and both private networks exist; the
     PostgreSQL 16 instance (DB-DEV-S, encrypted, daily backups) is ready, its public endpoint
     allow-lists one IP; the one-time setup (`deploy/scaleway-db-setup.sql`) passed its check
     (`f | t | t | f | f | f`: PUBLIC cannot connect). Steps 5–7 done the same day: a private
     bucket with versioning and encryption at rest (off by default, switched on before any upload), the API's S3 key (expires after a year, see known gaps), and the
     registry with `api:309b07f0b945` pushed by `publish-images`. Step 8 done (five Opaque secrets in Secret Manager). Step 9 done: migrations to
     `0003_auth_passkeys` from Luis's computer, 21 tables owned by `hausbuch_owner`, all with
     forced RLS and `hausbuch_app` grants. Step 10 done: `hausbuch-api` runs
     `api:309b07f0b945` (250 mVCPU / 512 MB, min = max = 1); `/healthz` answers 200 (its
     `SELECT 1` runs as `hausbuch_app` over the private network) and `/api/auth/me` 401.
     The S3 path was untested until the first upload (6 Nov 2026, below). The domain is `hausbuch.rcubed.fr`
     (bought 5 Nov 2026, the passkey RP ID). Step 11 done: `publish-images` built
     `web:309b07f0b945-16ef9db2` against the API endpoint, and `hausbuch-web` ran it
     (min-scale 0, max 2, HTTPS only); since 5 Nov 2026 it runs `web:2221bd1f9a4e-16ef9db2`
     (#11: the mockup style and the bundled Manrope font; roll back to the 309b07f tag). CNAME `hausbuch.rcubed.fr` → the web container, custom
     domain with Scaleway's certificate; `https://hausbuch.rcubed.fr/login` is 200 and
     `/api/auth/me` 401 from the API. Step 12 done: `hausbuch bootstrap` created the
     workspace and Luis's user, and Luis enrolled a passkey and signed in. First-deploy
     checks (`deploy/scaleway.md`, Results): Q13's platform limit passes (25 MiB reaches the
     API, also through the web proxy); a signed-in 25 MiB upload, Q16 (If-None-Match on the
     bucket), Q15 (SIGTERM timeout) and the allow-list check from another network are not
     run yet. No real data has been entered.
   - 6 Nov 2026: the document roll-up (#13: property and unit pages list documents linked
     to them directly or through their units, tenancies and loans) is live. `hausbuch-api`
     runs `api:8844916ea91b` and `hausbuch-web` runs `web:8844916ea91b-16ef9db2`, both built
     from `8844916`; no migration. Roll back to `api:309b07f0b945` and
     `web:2221bd1f9a4e-16ef9db2`.
   - 6 Nov 2026: first signed-in upload in production. Luis uploaded a real document and
     opened it in the app, so the S3 path works end to end: the API writes the original to
     the bucket before committing its row, and the file came back through a signed URL.
     Still open from the first-deploy checks: a signed-in 25 MiB upload, Q16, Q15 and the
     allow-list check from another network.
   - 6 Nov 2026: the allow-list check passes (the database's public endpoint answers from
     Luis's home IP and gives no response from a phone hotspot), and the signed-in 25 MiB
     upload is dropped by decision (largest real document about 300 KB; results in
     `deploy/scaleway.md`). Still open: Q16 (If-None-Match on the bucket), Q15 (SIGTERM
     timeout) and the real data.
   - 6 Nov 2026: Q15 answered: Scaleway kills a container about 270 s after SIGTERM, well
     above the API's 30 s need, so the shutdown settings stay. Still open: Q16 (the bucket
     refuses the conditional overwrite, but the real-bucket test then failed reading the
     original back with an unnamed error; diagnosis running) and the real data.
   - 6 Nov 2026: Q16 answered: Scaleway honours If-None-Match (412, original intact), but
     answers the next request on that connection with a bodiless 400 and closes it (results
     in `deploy/scaleway.md`). Known gap below; the first-deploy checks are otherwise all
     done. Still open: the real data.
5. **DONE: document checklist** (spec amendment 3, 6 Nov 2026): the Unterlagen tab per
   property. Built by a coder (rule, migration `0004`, API, tab, e2e), reviewed, merged as
   #20 (`aa298bb`) and live since 6 Nov 2026: Luis ran migration `0004` from his computer
   (`alembic current` showed `0004_grundsteuerbescheid (head)`), then `hausbuch-api` moved
   to `api:aa298bb0752f` and `hausbuch-web` to `web:aa298bb0752f-16ef9db2`. Roll back to
   `api:8844916ea91b` and `web:8844916ea91b-16ef9db2`; the old images work with `0004`,
   which only adds a document subtype. Merging it also fixed the auth enrollment tests,
   which had a fixed 5 Nov 2026 clock and failed on every branch from 6 Nov 09:30 UTC.
6. **DONE: retry after Scaleway's 412 quirk** (Q16, 6 Nov 2026). Scaleway answers the first
   request on a connection after a refused conditional PUT with a `400` that does not come
   from S3 (`text/html`, `connection: close`, no `x-amz-request-id`), so a racing upload or
   another request on that pooled connection failed. `make_s3_client` now retries exactly
   that response once, immediately; genuine S3 400s carry `x-amz-request-id` and are never
   retried. Reviewed (ship, no fix scope), merged as #21 (`655bf40`), live as
   `api:655bf40bd355` (roll back to `api:aa298bb0752f`; no migration). Known limit: the
   quirk landing on a request's second attempt (after a 503) still raises, rare and loud.
   Live check passed the same day: Luis ran the real-bucket tests from the fixed code and
   `test_real_bucket_honours_if_none_match` (refused overwrite, then read back) passed, as
   did every other real-bucket test.

### Known gaps (not yet built)

- There is no correction command for `tenancy.ended`, `tenancy.created` (`signed_on`),
  `tenancy.notice_given` or `loan.disbursed`, and no party changes on a tenancy. Step 4 will
  need them on the first typo.
- No retention auto-delete job, and no sweeper for blobs orphaned by rolled-back uploads.
  The production bucket has versioning on (5 Nov 2026), so whoever builds that job must
  also add a lifecycle rule expiring noncurrent versions (30 days, `deploy/scaleway.md`
  step 5), or deleted documents survive as hidden copies.
- No warning before a Scaleway API key expires (one year after creation; Luis, 5 Nov 2026).
  The API's S3 key lapses around 5 Nov 2027 and uploads then fail. Until phase 5's deadline
  reminders cover it, it is a calendar entry; rotation is in `deploy/scaleway.md` step 6.

### Spec amendment 1 (5 Nov 2026)

Collected from the phase 1 reviews and decided in one spec PR.

1. **Loan term cap.** More than 1,200 payments (100 years) raises the new error code
   `loan.term_too_long`, at payment 1201. A nonsensical payment used to loop for about a second
   and then fail with an uncoded `ValueError`.
2. **No raw tax numbers.** `OwnerCreate.tax_id_ref` rejects any value *containing* a run of
   10–13 digits (spaces, `/`, `.` and `-` ignored), labelled or not, e.g. "IdNr. 12345678901".
   A tax number would otherwise land permanently in the append-only log. The first version
   only rejected digits-only values; a review found labelled forms got through, and Luis
   chose to catch them.
   `OwnerCreate`, `TenantCreate`, `TenancyCreate` and `LoanCreate` set `hide_input_in_errors`,
   so the error's text (`str`/`repr`, what lands in a log) never echoes a tax number or an
   IBAN. This does **not** cover `errors()` or `json()`, which FastAPI's default 422 response
   is built from. The API step must build its error responses with `errors(include_input=False)`.
   (This corrects the first version of this item, which claimed API responses were covered;
   the implementing coder found it.)
3. **`document.typed` payload** adds:
   - `document_date`: a retention input, so its value is traced;
   - `changed_fields`, which can include `title`. The title's value is never recorded,
     because it often names the tenant (D5).
4. **AfA-basis evidence.** New document subtype `cost_basis_evidence`, for Nebenkosten
   invoices and renovation invoices capitalised as anschaffungsnahe Herstellungskosten. It
   falls in `ownership_records` (life of ownership + 10 years), because the §23 gain is
   computed from it. Before, such an invoice was a 10-year tax record and could have been
   deleted while still needed. Migration `0002` extends the subtype CHECK.
   **Retention also follows the structure** (Luis, after review): a document recorded as the
   evidence of an acquisition cost or a building/land split is an ownership record whatever
   its subtype, because the protection must not depend on remembering a subtype. This is the
   new `DocumentRetentionInput.cost_basis_reference`; the properties it backs count for
   `ownership_end_dates`.
5. **Traceable interest.** `ScheduleEntry` adds two fields, so every interest figure can be
   recomputed from its own row (principle 2):
   - `interest_days`: the period's 30E/360 days;
   - `follow_up_days`: of those, the days after `fixed_until`, charged at the follow-up rate.

### Spec amendment 2 (5 Nov 2026): decimals are strings on the wire

From the phase 1 step 3 spec PR (`docs/phases/phase-1-step-3.md` §3.4, §12, Q1), before the
HTTP API exists.

1. **Money, rates, shares, areas and rooms reject JSON numbers.** `Money`, `NonNegativeMoney`,
   `PositiveMoney`, `Rate`, `TaxRate`, `BuildingShare`, `Area` and `Rooms`
   (`schemas/common.py`) accept a decimal string (`"1400.00"`) or a `Decimal` and refuse a
   `float` with the fixed message "must be a decimal string, not a number". A float may already
   have been rounded before it reached the server (`0.1 + 0.2`), and before this amendment it
   was silently accepted.
2. **Ints are refused too** (`1400`, and `true`, which Python treats as an int). An int is
   exact, so accepting it would lose nothing, but a client that sends JSON numbers would then
   work for whole euros and fail only on amounts with cents: the bug would pass a test with
   `1400` and surface in production on `1400.50`. Refusing every JSON number makes such a
   client fail on its first request, and keeps the server's behaviour equal to its published
   schema. Nothing in the code base or the fixtures passed an int.
3. **JSON schema `type: string`** (both validation and serialization), with a pattern for the
   canonical form, e.g. `^[0-9]+(\.[0-9]{1,2})?$` for non-negative money. The pattern is
   documentation for clients; the server still validates with Pydantic's Decimal rules
   (digits, decimal places, bounds), so a form such as `"1E+3"` is still accepted as before.
   The generated TypeScript types are therefore `string`, and the frontend never holds money
   in a `number`.
4. **No fixture outcome changes:** every money and rate value in `tests/fixtures/phase1/` is
   quoted. The full suite (497 tests) passes unchanged; `tests/test_money_strings.py` covers
   the aliases and checks that no schema in `hausbuch.schemas` publishes a JSON `number`.

### Spec amendment 3 (6 Nov 2026): document checklist per property

**Status: DEPLOYED (6 Nov 2026), merged as #20 (`aa298bb`); see implementation status item 5.** Luis asked for it while
entering the real data: a list on each property of the documents the tax return needs, with
which ones are in and which are missing. ARCHITECTURE.md had the missing-document checklist
in phase 3, derived from the tax rules; this brings the part phase 1 can already know forward,
and phase 3 extends the same requirement list instead of starting a second one (Decisions,
6 Nov 2026). It reads data only: no new events, no writes besides one new subtype value.

**UI.** A new property tab **Unterlagen** (between Darlehen and Dokumente) with a year
selector, styled like the "Paperwork for this return" card in `docs/mockups/04-tax-year.png`:
one row per item with a status icon, a German label, a one-line hint, and the document(s)
that satisfy it as links (or, for a missing item, a link to the tab where it is fixed).
Footer: "Planungshilfe, keine Steuerberatung." Years offered: purchase year to the current
year, ending at the year ownership ended; default the current year. Never before 2000, the
tax-year floor (no document can carry an earlier tax year); a property bought before 2000
starts at 2000 (review fix, 6 Nov 2026: before it, every checklist request for such a
property failed with 422).

**Rule.** `rules.document_checklist.build_checklist(ChecklistFacts, tax_year, today) ->
list[ChecklistItem]`, pure, fixture `tests/fixtures/phase1/document_checklist.yaml`. The
requirement catalogue is data in that module (each entry: code, scope, accepted document
type/subtype, accepted link targets, whether `tax_year` must match, when it is due), so phase 3
adds entries rather than code paths. Statuses:

- `present`: a matching document is linked (all matches listed, oldest first);
- `missing`: none, and it is due;
- `expected`: none, but not due yet (`due_on` > `today`), e.g. the Zinsbescheinigung before
  it arrives;
- `incomplete`: a document of the right type/subtype is linked to the right target but its
  `tax_year` is null, so it cannot count for a year; the hint says to set the tax year.

"Linked" means a `document_link` to one of the requirement's accepted targets.
Items, for property P and tax year Y (P counts if owned on any day of Y):

| Code | Scope | Satisfied by | Due |
| --- | --- | --- | --- |
| `purchase_contract` | property | subtype `purchase_contract` linked to P | always |
| `price_split` | property | depends on the selected split: none selected → `missing` (fix in Kaufpreisaufteilung); `purchase_contract` → present iff the purchase contract is; `appraisal` → its `evidence_document_id`; `bmf_tool` → present without a document (the result is stored) | always |
| `acquisition_cost_evidence` | one per acquisition cost row | the row's `document_id` | always |
| `grundsteuerbescheid` | property | new subtype `grundsteuerbescheid` (type `bescheid`) linked to P or one of its units (an ETW's Bescheid is issued per flat; revised 6 Nov 2026 after the coder flagged it) with `tax_year` null or ≤ Y | always |
| `loan_contract` | per loan of P disbursed on or before 31 Dec Y | subtype `loan_contract` linked to the loan | always |
| `zinsbescheinigung` | per such loan | type `zinsbescheinigung`, `tax_year` = Y, linked to the loan | 28 Feb Y+1 |
| `lease` | per tenancy of P's units overlapping Y | subtype `lease` linked to the tenancy | always |
| `handover_protocol` | per such tenancy that started on or after P's `purchase_date` | subtype `handover_protocol` linked to the tenancy | always |
| `deposit_record` | per such tenancy with `deposit_form` ≠ `none` | subtype `deposit_record` linked to the tenancy | always |
| `wirtschaftsplan` | property, ETW only | type `wirtschaftsplan`, `tax_year` = Y, linked to P or one of its units | 1 Jan Y |
| `hausgeldabrechnung` | property, ETW only | type `hausgeldabrechnung`, `tax_year` = Y, linked to P or one of its units | 30 Jun Y+1 |

"Always" means due as soon as the item exists. A requirement whose `tax_year` rule is "null or
≤ Y" never yields `incomplete`. Items come out in the table's order, then by scope (loan by
`disbursed_on`, tenancy by `start_date`, cost row by `kind` then amount), so the output is
independent of input order.

**API.** `GET /api/properties/{id}/checklist?tax_year=Y` → `DocumentChecklist {property_id,
tax_year, available_years, items}`. Each item carries the code, status, scope (type, id and
non-personal context: lender, unit label and tenancy start, cost kind and amount), `due_on`
and the satisfying documents. No tenant names (D5). German strings live in the frontend. A
`tax_year` outside `available_years` is 422; an unknown or foreign property is 404 like the
other property routes.

**Migration `0004`.** Adds `grundsteuerbescheid` to the document subtype CHECK and to
`DocumentSubtype` (label "Grundsteuerbescheid"; retention stays `tax_records`). It also
amends `retention.yaml` only if a case needs it; no existing fixture outcome changes.

**Out (later):** marking an item "not applicable" (e.g. a tenancy taken over with the
property and no handover protocol); that is persisted state with events and waits for a
real case. Insurance policies (in the Hausgeld for an ETW). Everything that needs
transactions (invoices per cost line, statements per month): phase 3.
