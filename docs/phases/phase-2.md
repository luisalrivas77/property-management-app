# Phase 2 spec: AI inbox, with master-data pre-fill first

Nov 6, 2026 · spec draft (architect), checked by the orchestrator (prices, citations vs structured
outputs, data residency and PDF limits against the live API docs); awaiting Luis's decisions in
§16 (all five settled 6 Nov 2026; Luis's real camt export checked the same day, D33–D34).
Merged 6 Nov 2026 (PR #24); steps 1 and 3 merged the same day (PRs #26, #25; status table
below; this header said "no implementation" until step 3 landed its backend). On 7 Nov 2026 Luis's first real notary
invoice led to D35–D37 (PR #28, invoice reading fix, deployed the same day). Step 4 (matching and the inbox) merged and deployed on 7 Nov 2026 (PR #30, status table). Step 5 merged and deployed the same day (PR #32). Step 2 (tenant documents; the privacy note to the tenants was given on 7 Nov 2026, §11.2) merged and deployed the same day (PR #35, D42, D43). Next: step 4's real-data test, under way (the leases are in through pre-fill and the tenants' IBANs entered, 7 Nov 2026; the rental account opened at 0,00 € and the April–July camt imported the same day): enter the loan with "Vor der ersten Rate nur Zinsen" ticked (D46, live 8 Nov 2026, PR #64), after which its waiting interest debits should book themselves (D45), answer the loan payout and the purchase price with the two new categories (D44), then the rest of the year, which must reach ≥ 80 % auto-matched. Phase 1 is closed (Luis, 6 Nov 2026):
documents upload, and the real data will be entered through the pre-fill this phase builds
first.

This is the spec PR ARCHITECTURE.md requires before implementation. It fixes the contract the
implementation steps code against:

| Artifact | Where | Status |
| --- | --- | --- |
| Pydantic schemas | `backend/src/hausbuch/schemas/` (`ai.py`, `proposal.py`, `bank.py`, `allocation.py`; additions to `enums.py`, `events.py`, `api.py`, `document.py`) | Written in step 0 (PR #24, 6 Nov 2026), reviewed (one round, findings F1–F10 fixed or decided as D31–D32); `schemas/extraction.py` added (§4.1 names it) |
| Migrations | `0005_jobs_ai_proposals`, `0006_accounts_statements`, `0007_allocations_rules` | Written in step 0 (PR #24), tested against Postgres 16 (29 tables); all three applied to production on 6 Nov 2026 with the deploy of steps 1 and 3 (they are numbered in sequence, so 0007's tables for step 4 exist already and stay empty until then) |
| Rule fixtures | `backend/tests/fixtures/phase2/*.yaml` | Written in step 0 (PR #24): 14 files; `camt.yaml` and `csv_camt.yaml` deferred to the start of step 3, built from anonymised copies of Luis's real exports (checked 6 Nov 2026, D33) |
| Eval cases | `backend/tests/evals/` (recorded) + `HAUSBUCH_EVAL_DIR` (real, outside the repo) | **Recorded 7 Nov 2026** (eight synthetic documents, nine with the room lease added by PR #49, `hausbuch-evals` key from Luis's computer, about $0.25 for two runs; 63 checks pass in CI). The first run found two misreadings the synthetic fixtures had never exercised live: invoices and Bescheide returned the sender with its address (`Notar …, Marktplatz 1, …` instead of the name), and the Darlehensvertrag's signing date, stated only as "Musterstadt, den 20.02.2026" by the signatures, came back unknown. Fixed in the field descriptions (`schemas/extraction.py`: `sender` is the name without the address; `signed_on` names the "Ort, den <Datum>" form, lease and loan), then recorded again; merged (PR #37) and deployed the same day with images `b306050c1413`, so the live reader uses the fixed descriptions (no migration). Real-document evals (`hausbuch evals run`, golden files) still wait for Luis's real-data test |
| Download names (7 Nov 2026) | `api/routes/documents.py` `download_filename` | Merged and deployed 7 Nov 2026 (PR #39; images `729bffed25e8`, no migration): a document with a title (confirmed from pre-fill or typed by hand) downloads as `<document date> <title><ext>`; without a title the uploaded name, then the hash prefix. Only the download header changes; the stored original and `original_filename` stay as uploaded (principle 6) |
| Kaufvertrag reading fix (7 Nov 2026) | `rules/extraction_parse.py` `_fraction`, `rules/prefill.py` address keys; `extraction_parse.yaml` (2 cases), `prefill.yaml` (2 cases) | Merged and deployed 7 Nov 2026 (PR #43; images `cbb7ffa87d2b`, no migration), from Luis's real Kaufvertrag: a decimal Miteigentumsanteil (`56,18/1.000`) was read as 18/1000 and is now kept exact and unreduced (5618/100000); the flat already typed as `…str` was not found because the contract writes `…straße` and `…str.`, so a new property and a unit with only the share were proposed. Street, house number and postal code now compare by a key (street: casefolded, spaces, hyphens and dots removed, a trailing `str` spelled out; house number: spaces removed, `-` and `/` significant), which also decides that a respelled address is unchanged. Reviewed (one round): a first version also dropped hyphens from house numbers, so `1-2` silently matched `12`; fixed with its own fixture. Known: the apply step does not itself skip an `unchanged` property record (the client sends them skipped); an abbreviation mid-name (`Str. des 17. Juni`) still proposes a new property, visibly |
| Kaufvertrag without a postal code (7 Nov 2026) | `rules/prefill.py` `_address_of`, `_match_property`, `_resolve_property`; `prefill.yaml` (3 cases, commit `c9a7440`) | Merged and deployed 7 Nov 2026 (PR #45; images `e7202e268298`, no migration). The PR #43 row above did not fix Luis's case, though it was reported as fixed: his Kaufvertrag states no postal code, so the AI's postal code was a guess and the match still failed, proposing a new property and unit again (found when Luis retried). A postal code now counts only when its value is in the quote; without one, street + house number (+ city when stated) decide, and only when exactly one property fits; two candidates stay a new property. Verified on the real document 7 Nov 2026, once the house number was corrected (next row) |
| Field descriptions and shares as percent (7 Nov 2026) | frontend only: `components/form.tsx` `InfoToggle`, `components/format.ts` `formatShare`, `components/input.ts` `toFraction`, every form and the proposal review | Merged and deployed 7 Nov 2026 (PR #46; images `1436ae4e22f8`, no backend change, no migration). Every field has an ⓘ beside its label opening a German description of what the value is, where to find it and what Hausbuch does with it; each claim about the app was checked against the code, unbuilt features are named as planned. Shares stay exact fractions in storage and the API; screens add the percentage (`85/1000 (8,5 %)`), decimal shares show as typed, and share inputs accept decimals and `%`. Follow-up (PR #48, images `e53d066d24d4`): the house number's help says the flat number (WE 03) belongs in the unit's name; Luis's flat had `12 WE03` as its house number, which, not the postal code, kept his Kaufvertrag from matching after PR #45. With `12` it matched and proposed no unit (7 Nov 2026, Luis on the live app) |
| Room lease unit (7 Nov 2026) | `schemas/extraction.py` `LeaseExtraction.unit_label`; eval document `mietvertrag_zimmer` (new), `mietvertrag` re-recorded | Merged and deployed 7 Nov 2026 (PR #49; images `7fc7f0f34664`, no migration). Luis's room lease names the room (`Zimmernummer: Zimmer 3`) and the flat's floor (`Wohnungsnummer (Etage): 1.OG links`); the field asked for the flat's label, so the AI did not return the room and pre-fill found no unit among `Zimmer 1`–`3`. The field now asks for the room when a single room is let, else the flat's label. Recorded live with the `hausbuch-evals` key (about $0.01): `Zimmer 2` and `WE 3` read correctly, 66 eval checks pass. The lease is linked to the tenancy, not the property, by design: the checklist's lease item is scoped to the tenancy. Verified on Luis's real lease the same day: re-read, it found the unit `Zimmer 3` |
| Lease deposit form (7 Nov 2026) | `rules/prefill.py` `_deposit_form`, `_Builder._deposit_form`; `prefill.yaml` (5 cases, header) | Merged and deployed 7 Nov 2026 (PR #51; images `fe44e05ad340`, no migration). Luis's room lease asks for "eine Kaution in Höhe von 1.000,00 €" to be transferred, with no form word; the proposal left the form at `none` next to 1000.00 and applying failed ("deposit_form 'none' exactly when deposit_amount is 0"), with no form field to fix it. Now derived `cash_account` (low confidence); `bar` only as a word. Reviewed (one round): low confidence for the inferred form, Barzahlung-type compounds, and an amount in words asking for the form were fixed before merging; the re-read over an existing tenancy is open question 11. Verified on Luis's real lease the same day: the tenancy was created |
| Lease Nebenkostenpauschale (7 Nov 2026) | `schemas/extraction.py` `LeaseExtraction.operating_costs_prepayment`, `heating_costs_prepayment`; eval `mietvertrag_zimmer` reworded, both leases re-recorded | Merged and deployed 7 Nov 2026 (PR #53; images `c6467c3eef43`, no migration). Luis's room leases state one flat rate ("Nebenkosten inkl. Betriebs-, Heiz-, Warmwasser, Strom 50,00 Euro", no settlement); the AI took it on one lease and answered unknown on a second of the same template. The field now names a Nebenkostenpauschale too, and heating only when stated as its own amount; recorded twice, 66 checks pass each time. The Pauschale is not flagged as such (no settlement); that is input for the phase 4 spec (ARCHITECTURE, delivery phases). Not yet verified on a real lease |
| Steuerjahr hidden where it does nothing (7 Nov 2026) | frontend `documents/[id]/page.tsx` `NO_TAX_YEAR` | Merged and deployed 7 Nov 2026 (PR #54; images `c62d9ad05d2c`, no backend change). The empty Steuerjahr field is hidden on the document page for purchase contract, price split evidence, appraisal, loan contract, lease, handover protocol and deposit record: their retention runs from ownership or tenancy end dates and their checklist items ignore the tax year. A stored year stays visible; every other document keeps the field. The pre-fill suggestion already omitted an unstated year |
| Hand-fillable review and lease unit by key (7 Nov 2026) | `rules/prefill.py` `_unit_by_label`, `_lease_unit`; `rules/extraction_parse.py` `_without_page_reference`; `components/proposals.tsx` `CREATE_FIELDS`, `rowsOf`, unit and tenancy selects; `prefill.yaml` (8 cases), `extraction_parse.yaml` (4 cases) | Merged and deployed 7 Nov 2026 (PR #56; images `f9df2734bb10`, no migration, no API change). From Luis's scanned WG-room lease, which names the room `Zimmer Nr. 1 (WE 3, Zimmer 1)`: the exact label match (casefold, spaces) found no unit among `Zimmer 1`–`3`, the tenancy's unit was shown but not editable, so the suggestion could not be applied at all; the Nebenkosten row was not shown because the lease states only an all-inclusive Warmmiete of 500 € (Kaltmiete correctly "nicht gefunden"); and the tenant's name came back as `<name> (Seite 1)`. Now (1) the lease's unit compares by label key (letter and digit tokens, leading zeros and nr/no/nummer dropped): an equal key first, else the one unit whose key occurs inside the label as whole tokens, at most medium confidence; only a unit key with both a word and a number takes part in that second step, and a label naming a Zimmer never lands on a unit without one (review F1, F2: a bare `1` matched `Wohnung 1. OG`, and a room lease read before the rooms exist would have been booked on the flat; a tenancy cannot be moved to another unit). The Kaufvertrag's unit record keeps the exact match. (2) A trailing `(Seite n …)` / `(S. n …)` is stripped from a value before parsing; a value that is only that is missing (review F4). The extract prompt is unchanged, so the recordings stand. (3) **Rule from Luis, 7 Nov 2026: the AI pre-fills what it can, the user can type every other field.** A record that creates shows every field of its create model; one the proposal lacks is an empty input ("Nicht im Vorschlag; bei Bedarf von Hand eintragen."), sent only when typed and recorded as `edited`. A tenancy's unit and a Kaution's tenancy are picked from the workspace's units / tenancies, preselected with the match. Records that patch or correct still show only the proposed fields (the record's own page edits the rest); ownership shares stay fixed. Reviewed (one round): F1, F2, F4 and F5 (rooms one decimal) fixed before merging; F3 (`Zimmer 1 und 2` books one room) left, one unit per tenancy is the model. Verified on Luis's real room lease the same day: re-read on the live app, it worked |
| Loan payout and purchase price categories (7 Nov 2026) | `schemas/allocation.py` `LOAN_DISBURSEMENT`, `PURCHASE_PRICE`; `rules/categories.py`, `rules/review.py`; migration `0009_payout_price_categories`; `categories.yaml`, `thresholds.yaml`, `review_routing.yaml`; `ai/prompts/categorize.md`; `labels.ts` | **Merged and deployed 8 Nov 2026** (PR #61, D44; images `222b64c8e917`; migration **0009** applied to production before the image, `alembic current` = `0009_payout_price_categories (head)`; `/healthz` ok). Fixtures `118d465` first (4 fixture checks failed on the old code). "Darlehensauszahlung" is pass_through, "Kaufpreiszahlung" capitalized; neither has a threshold. Reviewed (one round, verdict merge, no defect); its process note was that the taxonomy changed without the spec, so D44 and §8.2 were added to the same PR. Not tested: the success path of 0009's downgrade (as for 0008's) |
| Dokumente photo button (7 Nov 2026) | frontend `documents/page.tsx` | Merged and deployed 8 Nov 2026 (PR #60; images `222b64c8e917`, no backend change): "Dokument fotografieren" on the Dokumente page opens the same capture page as the Posteingang's "Beleg fotografieren" (it was reachable only from the Posteingang) |
| Re-check waiting lines (7 Nov 2026) | `jobs/matching.py` `run_rematch_waiting`, `jobs/enqueue.py` `enqueue_rematch_waiting`, `services/matching.py` `waiting_lines`, `write()`; hooks in `services/{rules_learned,loans,tenants,tenancies,accounts}.py`; inbox note | **Merged and deployed 8 Nov 2026** (PR #62, D45; images `885e663bff66`, no migration). A learned rule, a loan, a tenant IBAN, a tenancy's parties, rent rows or later move-out, or an owner account now books matching lines already waiting in the inbox, with rules and matchers only (no model). Reviewed (one round, verdict merge): F1 a waiting job could start between the dedup lookup and the change's commit and read the old state; fixed (`FOR KEY SHARE`, `7347dec`, not reproducible in a test). F2 a later move-out queued no re-check; fixed with a db test. Accepted: a wrong rule books every waiting line of that payer at once, and those are corrected by hand (D45) |
| Loan interest-only phase (8 Nov 2026) | `schemas/loan.py` `interest_only_before_first_payment`; `rules/loan_schedule.py` `_first_period`; `rules/matching.py` `_interest_only_split`, `match_loan`; migration `0010_loan_interest_only`; `LoanForm.tsx`, `proposals.tsx` | **Merged and deployed 8 Nov 2026** (PR #64, D46; images `afda0d9106bc`; migration **0010** applied to production before the image, `alembic current` = `0010_loan_interest_only (head)`; `/healthz` ok). Fixtures first (failing on the old code). A loan with "Vor der ersten Rate nur Zinsen" ticked books the bank's interest-only debits between the payout and the first annuity as `loan_interest` from the amount stated in the purpose text. Checked against Luis's real July and October texts from his computer only (amounts and ranges read correctly; the texts stay out of the repo). Reviewed (one round): F1 two loans at one bank could claim each other's debits by lender name; fixed (a loan's own account decides) with two fixture cases. Accepted: F2 no image rollback after a loan is created on this image; F3 adjacent stated ranges sharing a day go to review. Not done yet: entering Luis's real loan |
| Rule tests | `backend/tests/rules/` | All run since step 4 (601 tests, none skipped, 7 Nov 2026; this row said "skipped until each rule module lands" after they had landed) |
| Step 1 (pre-fill) | `ai/`, `jobs/`, `rules/prefill.py`, `rules/extraction_parse.py`, `services/proposals.py`, proposal routes, upload options and the Vorschlag section on `/documents/[id]` | Merged and deployed 6 Nov 2026 (PR #26; images `fbe8c69e6a26`), backend and UI; reviewed (one round: F1 tenant mark not sticky across re-uploads, F2 queueing lock wedged a document on retry, F3 a job left running by a stopped instance was never retried, F4–F6 small; all fixed, `f6f9364`…`7b90445`). **AI switched on in production on 7 Nov 2026** (`HAUSBUCH_AI_ENABLED=true`, key from the `hausbuch` Console workspace; the default stays off). Tenant documents stay blocked (`ai_tenant_documents` off until step 2). Recorded evals done 7 Nov 2026 (Eval cases row; the step 1 "not yet" here was stale until then); nothing sets status `running` (the UI shows `queued` until ready); a split PDF links only its first chunk's extract call; a non-transient bug after the model calls is retried like an outage (up to four paid reads before `failed`, review F7, left as is); a proposal blocked at upload has no records, so it can be neither applied nor rejected (it stays as the explanation). Deviations: an extra `GET /documents/{id}/proposals`; events are written by the service, not the worker; a duplicate upload answers with the pending proposal; apply defaults a unit label and a loan's property; the price split is recorded but not selected; the UI never sends `ai=true` (the server default decides), sends "behalten" as decision `skipped` and unchanged records as `skip`. The UI would like: whether AI is on (so the upload can hide the option), coded errors from apply (they come back as English text), and `floor_area_m2` optional on a unit (a Kaufvertrag rarely states it). A stalled job rescued by the worker's sweep uses up one of its three retries |
| Step 3 backend (accounts and statements) | `rules/statements.py`, `services/bank/`, `services/accounts.py`, `services/statements.py`, routes; `camt.yaml`, `csv_camt.yaml` | Merged and deployed 6 Nov 2026 (PR #25; images `fbe8c69e6a26`): parsers, import, dedup, duplicates, reconciliation, transactions list; reviewed (one round, three findings fixed in `4b8215e`). Not yet: PDF statements (wait for step 1's extract job), matching (the `_on_reconciled` hook is a no-op until step 4). The UI (Konten: accounts, imports with the balance dialog for CSV, Umsätze, possible duplicates; `e2e/accounts.spec.ts`) landed in the same PR (`f83da9e`, `232bc11`, `77cb956`). Possible API additions the UI noted, none blocking: the difference on `StatementImport` (the UI shows both balances instead, since it must not subtract), an `account_id` filter on possible duplicates, the file name on an import |
| Invoice reading fix (D35–D37, 7 Nov 2026) | `rules/prefill.py`, `ai/prompts/`, `services/documents.py`, `/documents/[id]` typing form; `prefill.yaml` (5 new cases) | Merged and deployed 7 Nov 2026 (PR #28; images `0b3aef32fe47`, no migration): financing invoices (Grundschuld, Hypothek, Darlehen) propose no Nebenkosten row and no subtype; model-written title; invoice tax year derived; hand-entry fields show the AI reading and an edit becomes a version of the same kind, with a shown-version check. Reviewed (two reviewers, one round: Bescheid-typed Grundschuld bills caught too; stale form after a re-read refused). Not yet: eval recordings for the changed prompts (Luis's key) |
| Step 4 (matching, allocations, rules, inbox) | `rules/{periods,allocations,categories,matching,review}.py`, `services/{matching,allocations,rules_learned,review}.py`, `jobs/matching.py`, `ai/prompts/{categorize,explain}.md`, `api/routes/allocations.py`, `/inbox` (Posteingang) and the allocation panel, the Umsätze link | **Merged and deployed 7 Nov 2026** (PR #30; images `d82e7964ebd1`, no migration: 0007 was applied on 6 Nov 2026). Rules `1210bed`, `e491b31` (D38, D39); services, jobs, AI and API `06589ee`…`19dd999`; UI `2a1bc2f`…`bc0305d`, `1c17498`. Reviewed twice, one round each: the pure rules (F1 IBAN-only rules pre-empting matchers → D38; F2 loan entry by booking month → D39; F3 year-end rent → a service guard), then the services (`b7b07fe`: the double-booking guards serialised by an advisory lock per tenancy or loan; a name pattern alone learns no rule on a matcher-owned counterparty; 404 for an unknown property; `3356df2`: D40). The rule box starts ticked for a one-row answer with an IBAN unless its category never auto-confirms (`no_default_rule_categories`, `08f3fcf`), unticked for several rows (the browser cannot tell whether a split fits a split method). Not yet: the done-criterion test (first real month confirmed through the inbox, second month ≥ 80 % auto-matched). Known gaps: an inbox item carries ids only, so the page loads every account's lines; the Umsätze list shows no allocation status; a learned rule cannot be switched off from the API or the UI (`rules_learned.disable_rule` exists); confirmed rows of an import that later stops reconciling stay and are not flagged (needs a spec decision); a rule hit that was only proposed still counts a hit; a tenant name glued inside a word, or a name part under 3 letters, is not removed from the prompt; a re-fired reconciled hook asks the model again for lines still unmatched (cost only). Small items from the rules review left as they are: an owner transfer of 0.00, the absolute value in the document amount match, a Hausgeld rule when the Rücklage is 0 |
| Step 2 (settings switches, Kautionsnachweis, erasure scrub, Adobe forms; D42, D43) | `/settings` KI section and `GET`/`PATCH /api/workspace/settings` (`services/workspace_settings.py`, `api/routes/workspace.py`); `rules/prefill.deposit_record`; `services/erasure_scrub.py` called from `erase_tenant`; migration `0008`; `ai/documents.is_adobe_only_form` | **Merged and deployed 7 Nov 2026** (PR #35; images `c07c24e49d24`; migration **0008** applied to production before the image, `alembic current` = `0008_rule_matcher_erasure (head)`; `/healthz` ok with the worker; the new settings route answers 401 unauthenticated). Commits `07364f2` (fixtures) … `58e8099`. Settings `92ba4ea`: the switches went into the existing `/settings` page (passkeys, members) rather than a new page; a switch shows the saved state and flips when the PATCH answers. Kaution `185fa9d`. Scrub `809e368`, `b988722` (migration **0008**: an enabled rule still needs an IBAN hash or a pattern, only a disabled rule may have an empty matcher; its downgrade is refused while such a rule exists), `0553773` (a read in flight during an erase stores its `ai_call` row without output and raw text: `_send` reads its proposal `FOR SHARE`, the erase holds it `FOR UPDATE`). Blocked documents `3663cd3`, `001ac7c`: once the switch is on a blocked proposal can be re-read ("Neu auslesen", or a re-upload), superseding it; never two undecided proposals on one document; with the switch off a re-upload returns the blocked proposal unchanged (keeps the classifier's typing) while an explicit re-run supersedes it and blocks again; the classifier's tenant mark is now stored on the row whenever it says tenant data or a tenant subtype, so it is sticky like the uploader's and a re-run with the switch off is blocked at creation with no model call. Adobe forms `61455ec` (D43). Deviations from D42: a `blocked_tenant_data` proposal of an in-scope document is superseded too (it is still decidable), its `error_code` cleared. Reviewed (one round, verdict merge): F1 an apply that kept an existing tenant record unchanged took no lock on the tenant, so it could commit after the erase computed its scope and keep the tenant's values until the 90-day sweep; fixed in `1414919` (`tenants.require_unerased_tenants`, FOR KEY SHARE, ConflictError when erased). F2 the Adobe check joined all pages' text, so a real XFA form saying "please wait" failed; fixed in `58e8099` (placeholder only on a one-page PDF). F3 accepted, not fixed: the match job's `ai_call` rows are not erase-guarded, so a categorize answer quoting the payer may be stored after an erase; the transaction row keeps the same name for 10 years by spec (§3.5), so nothing new is retained. Known: an erase racing an apply on the same document can deadlock, and Postgres aborts one side (the user retries); outside D42's scope are a `ready` proposal on a document never linked or applied, and a rule whose pattern names the tenant with neither the tenant's IBAN hash nor a tenancy scope. Done 7 Nov 2026 (this row said "not yet" after it had happened): tenant documents switched on, and all of Luis's room leases entered through pre-fill, one unit per room, after the fixes in PRs #49, #51, #53 and #56; Luis added the tenants' IBANs by hand the same day, so the rent matcher can recognise their payments |
| Step 5 (capture, evals commands, scrub) | `/capture` and `components/capture.tsx`, manifest shortcut; `hausbuch evals report` / `evals run` (`hausbuch/evals/`); `jobs/scrub.py`; `ai/documents.has_text_layer` | **Merged and deployed 7 Nov 2026** (PR #32; images `0b1424815563`, no migration). Capture `7af178d`…`8cda395`: photos collect as pages (up to 20, reorder, remove) and upload as one PDF (D30 amended; pdf-lib 1.17.1, loaded on upload only; photos downscaled to 2000 px, EXIF rotation applied, camera metadata dropped); a PDF without a text layer is read without citations (`32b24b9`). Evals `365c730`, `d3bfc39` (D41; the request builders moved to `hausbuch.evals.reading`, byte-identical to the job's). Scrub `0f0f3ff`, daily at 03:15 UTC. Reviewed (one round: F1 a blocked proposal is still decidable, so not final for the scrub; F2 no test that a bad owner URL prints no password; F3 a misleading comment; fixed in `f37aaa6`). Not yet: Luis's real-document golden files; the first production scrub run (check for a `scrub_proposals` row in `procrastinate_jobs` after 03:15 UTC); iOS Safari not tried (duplicate detection of a repeated capture relies on identical JPEG bytes). Known: a captured page is not straightened like a scanner app's; capture duplicates `uploadDocument` and the proposal polling (both could take a `source` option and be exported); PyYAML (for golden files) is a dev dependency only; `evals run` sends whatever is in the folder, tenant documents included (allowed since the privacy note of 7 Nov 2026, §11.2) |

**Done when** (ARCHITECTURE.md, phase 2, extended 6 Nov 2026): the real flat, its loan, its
tenancy and the 2026 paperwork are entered **through pre-fill** with Luis only verifying
values; then a month's statement imports with ≥ 80 % of lines auto-matched, zero
allocation-sum errors, every statement reconciled to its balances, and a repeat answer is
never asked twice. §13 turns the four numbers into fixtures.

## 1. Scope

**In:** the job queue (Procrastinate inside the API process); the Claude client with
structured validation, provenance storage and the privacy gate; document classification and
per-type extraction; **pre-fill proposals** (Vorschläge) for property, Nebenkosten, loan,
tenant, tenancy and rent, with a review screen that shows page + quote per value and applies
through the existing commands; bank accounts and statement import (camt.052 / camt.053 XML,
including Sparkasse's ZIP of daily reports, CSV-CAMT, PDF Kontoauszug) behind `BankSource`; dedup and balance reconciliation; transactions and
append-only allocations with service period and recurrence; the matching pipeline (rules →
deterministic matchers → document match → LLM); the review queue (inbox) with reasoning and
one question; learned rules storing split methods; dead-letter "couldn't read this"; PWA
camera capture; sign-in and passkey events in the Event table; the eval record.

**Out (later):** FinTS and email-in (phase 5); tax rule sets, Anlage V, the §11 ten-day rule
(phase 3: phase 2 only stores what it needs); Betriebskostenabrechnung (phase 4); OCR
engines (none: Claude reads the file, §5.2); offline capture (no service worker; the capture
page needs the network); zero-data-retention or EU inference with Anthropic (not offered on
the first-party API today, §11); retention auto-delete (still a phase 1 known gap, now with
two more tables to cover, §4.4); multi-page photo stitching (each photo is one document).

## 2. Decisions taken in this spec

Numbering continues from phase 1's D12. Each one is a place to push back in review.

| # | Decision | Why |
| --- | --- | --- |
| D13 | **Pre-fill is step 1 of phase 2**, not a phase 1 amendment. The real data is entered through it. | Luis is waiting on it; phase 1's done criterion ("the current flat … are in") is met by this step rather than by typing. |
| D14 | **Nothing an AI job produces reaches a domain table.** A job writes an `ai_call` row, a `document_extraction` version (source `ai`) and a `proposal`; only `apply` (a user command) calls the existing `create_*` / `update_*` / `correct_*` services, in one transaction with one `correlation_id`. | Principle 1 and 3; all phase 1 validation, events and RLS stay as they are. |
| D15 | **Every proposed value carries a source: page number + quoted text, and a code-derived confidence.** Extraction uses the Claude **citations** feature (one line per field, parsed in code) because citations and JSON structured outputs cannot be combined in one request (API returns 400, checked 6 Nov 2026). Code validates each parsed value with the Pydantic field type and checks the value is recoverable from the cited text; confidence is a function of those checks, never a number the model reports. | The review must be "a glance": the quote is API-extracted and guaranteed to point into the document. Changes an ARCHITECTURE guardrail (§15). |
| D16 | **Tenant documents are not sent to the model until Luis switches it on.** Workspace setting `ai_tenant_documents` (default `false`); uploads carry `contains_tenant_data`; the classifier's result is a second gate (lease / handover / deposit / tenant letter found on a document not marked → extraction stops, output discarded). | ARCHITECTURE: terms confirmed before tenant documents are sent. Findings and the decision Luis must take are in §11. |
| D17 | **Default model tiers:** `claude-haiku-5-5` classifies and explains; `claude-sonnet-5-5` extracts, pre-fills and categorizes; `claude-opus-5-5` only for a match the Sonnet call marked ambiguous. `inference_geo` is a setting, default `us` (Luis, 6 Nov 2026: one named processing location for the privacy note, +10 %). | Prices 6 Nov 2026: Haiku $0.10/$0.50, Sonnet $2/$10, Opus $4/$20 per MTok; Haiku is 5× dearer over 100k prompt tokens, which a 30-page contract exceeds, so it never reads whole contracts. |
| D18 | **AI provenance is a table (`ai_call`), not event payload.** Events record `ai_call_id`, type, confidence and field *names*; the table holds model, prompt version, input hash, token counts, the parsed output and the raw text. | D5: outputs quote tenant names; the event log is append-only and outlives erasure. `ai_call` rows follow their document's retention and are scrubbed by tenant erasure (§4.4). |
| D19 | **Proposal values are personal data at rest.** `proposal.fields` and `.decisions` are nulled 90 days after apply/reject (sweeper, with the retention job) and immediately by `erase_tenant` for proposals that created or patched that tenant. The eval record keeps per-field decisions and, for non-personal fields only, proposed and final values. | Measurement without a second copy of tenant data. |
| D20 | **Procrastinate's schema is applied by migration `0005` as `hausbuch_owner`**, its tables are not RLS'd and not mirrored in `tables.py`; every job payload carries `workspace_id` and opens its own `workspace_transaction` as the app role. | Step 3 spec §3.7 already fixed this; the drift test and the 21-table count exclude `procrastinate_*`. |
| D21 | **Reconciliation works on balance anchors**: an opening balance entered once per account, and a declared closing balance per import, read from the file when it states one (camt XML, PDF Kontoauszug) and typed by Luis from online banking otherwise (the Sparkasse CSV, which carries none). An import whose computed balance differs from the declared one is `unreconciled` and none of its lines reach matching. *Corrected 6 Nov 2026:* this row first said the CSV was the primary format; Luis's real export showed the camt XML is available and carries balances (D33), so typing a balance is the CSV fallback only. | Phase 2 input point 3 assumed every statement states balances; the CSV does not (§7.2). |
| D22 | **Line hash:** the bank's reference where present (camt `AcctSvcrRef`, else `NtryRef`; Luis's export carries a distinct `AcctSvcrRef` on all 22 entries; CSV `Kundenreferenz (End-to-End)` only when non-empty and not `NOTPROVIDED`), else a canonical tuple of the line plus its ordinal among identical lines in the file. **Cross-format duplicates** (same account, amount, booking date, counterparty IBAN or normalized name, different hash) go to review, never auto-booked or dropped. | Phase 2 input point 2, made concrete. |
| D23 | **Allocations are append-only rows with `supersedes_id`**, like `rent_schedule`; a correction is a new row set per transaction. Status lives on the row (`proposed`, `confirmed`, `superseded`) and is the one column a trigger lets change, forward only. Considered and not chosen: a separate mutable `allocation_proposal` table so `allocation` holds confirmed rows only (closer to "only confirmed allocations reach the ledger"); rejected because a proposal and its confirmation would then be two rows with the same amounts to keep equal, and every reader (tax, performance) must filter on `status = confirmed` either way. The ledger views and phase 3 read `confirmed` rows only. The validator enforces Σ allocations = transaction amount on every write. | Principle 4; D12's pattern; one table, one filter. |
| D24 | **Categories are a fixed `AllocationCategory` enum (§8.2); tax treatment per category is rule data** (`rules/categories.py`, fixture `categories.yaml`), not a column the user edits. Phase 3 maps categories to Anlage V lines. | ARCHITECTURE: renaming a category never changes a return. |
| D25 | **Learned rules store a matcher and a split method**, never amounts: `single_category`, `by_rent_schedule`, `by_loan_schedule`, `by_wirtschaftsplan`, `owner_transfer`. | Phase 2 input point 5. |
| D26 | **LLM categorizations are never auto-confirmed in the first month** (`auto_accept_llm` workspace setting, default `false`); deterministic matches and rule hits auto-confirm at confidence 1. The ≥ 80 % criterion is measured on the second imported month. | Principle 3: thresholds exist, but the first month trains the rules. |
| D27 | **Service period** is set at match time by `rules.periods.service_period(purpose, booking_date, recurring)`: a month or range parsed from the purpose text, else the booking month for recurring lines, else null. | Phase 2 input point 4. |
| D28 | **Sign-in and passkey actions become events on entity type `user`** (the `app_user` id): payloads hold passkey id and label, never IP, user agent or credential material. The auth tables themselves stay event-free (step 3 Q6). | Decided 5 Nov 2026, deferred to this spec. |
| D29 | **Eval set:** recorded responses for synthetic documents live in the repo and run in CI; Luis's real documents live outside the repo (`HAUSBUCH_EVAL_DIR` on his computer) and run by hand before a prompt or model change. | Real documents cannot be committed, even anonymized by hand; ARCHITECTURE's "in CI" holds for the recorded set. |
| D30 | **Photos are one document each, JPEG only from the capture page.** HEIC is stored (phase 1 accepts it) but marked "couldn't read this": Claude reads PDF, JPEG, PNG, WebP and GIF. **Amended 7 Nov 2026 (Luis): several photos can be one document.** The capture page collects pages (add, remove, reorder, at most 20) and combines them into one PDF in the browser (pdf-lib; each photo downscaled to 2000 px on the long edge, EXIF rotation applied, JPEG about 0.85), uploaded as one document with source `photo`; that PDF is the immutable original and the separate photos are not kept. A single photo still uploads on its own. No schema change: a document stays one file. Such a PDF has no text layer, so it is sent to the model without citations, as a photo is (`ai.documents.has_text_layer`): its values carry no page and quote and parse at low confidence; the same holds for any scanned PDF. | Smallest capture flow; conversion needs a native library the container does not have. |
| D31 | **An allocation stores no tax year** (review F5, 6 Nov 2026). The year a payment counts in is an output of that year's rule set (the §11 ten-day rule needs booking and value date, service period and the recurring flag, all stored), computed by phase 3's draft, not a column frozen at match time. A reader that needs a year filter before phase 3 uses the transaction's `booking_date`. | Principle 5: 2026 stays reproducible if the rules change, and no append-only row holds a value a later rule could contradict. Amends ARCHITECTURE's Allocation row. |
| D32 | **Events carry no matcher patterns** (review F2). `rule.created` / `rule.disabled` record the IBAN HMAC, the amount sign and whether name or purpose patterns exist; the patterns themselves stay on the mutable `matching_rule` row, which tenant erasure can scrub (step 2 adds that scrub). | D5: a typed pattern like `schmidt` is personal data and the event log is append-only. |
| D33 | **Sparkasse's camt export is camt.052 v08, and it is the primary statement format.** Format value `camt` (not `camt053`) covers both camt.052 (`BkToCstmrAcctRpt`/`Rpt`) and camt.053 (`BkToCstmrStmt`/`Stmt`); one parser reads either root, since `Ntry` and `Bal` are the same. Balances `OPBD`/`CLBD` come from the file, so nothing is typed. | Checked 6 Nov 2026 against Luis's real export (§16 Q3): every daily report has both balances, they chain, and the entry count (22) equals the CSV's line count. The spec, enum and migration 0006 had assumed camt.053; 0006 was never applied anywhere but test databases, so it was edited in place. |
| D34 | **A camt import may be a ZIP of daily reports.** The ZIP is the stored original (principle 6; document type `statement`). Members are read in memory, never extracted to disk: flat `.xml` names only (no `/`, `\` or `..`), at most 400 members and 20 MiB uncompressed in total, no nested archives; anything else rejects the whole upload. Reports are sorted by their `CLBD` date (*amended in step 3:* the `CLBD` date is the report's own booking day, while the `OPBD` date is the previous report's day, so `CLBD` is the one that identifies the report); each must satisfy `OPBD + Σ Ntry = CLBD` and each `OPBD` must equal the previous report's `CLBD`, and any break sends the whole import to review. The import's `file_opening` is the first report's `OPBD`, its `declared_closing` the last report's `CLBD`, `balance_source = file`, so `reconcile` (§7.2) is unchanged. XML is parsed with entity resolution and network access off. Step 3 adds `application/zip` to the accepted upload types. | Sparkasse delivers its camt export this way (one report per booking day, 17 files for Apr–Nov 2026). Days without bookings have no file; the chain check still holds because the balance does not move. |
| D35 | **A financing invoice is not a Nebenkosten Beleg** (added 7 Nov 2026, after Luis's first real notary invoice). A notary or Grundbuchamt invoice for a Grundschuld, Hypothek or loan is a financing cost (Geldbeschaffungskosten): deductible as Werbungskosten in the year paid, never part of the AfA basis. `rules.prefill` decides it in code, not by trusting the classifier: when the invoice's `subject` or `title` (or, for a document typed `bescheid`, its `title`: a Grundbuchamt Kostenrechnung may be typed either way; review F1, 7 Nov 2026) names a financing term (`grundschuld`, `hypothek`, `darlehen`; casefolded, anywhere in the text, so `Grundschuld-/Hypothekenbestellung` matches) the proposal holds no `cost:` record and no subtype in `typing`, `typing` carries a note saying why (and that a purchase share on the same bill, if any, is entered by hand as Nebenkosten), `link:property` stays, and `link:loan` is added when the property has exactly one loan. Terms like `finanzierung`, `kaufvertrag` or `zwangsvollstreckungsunterwerfung` are deliberately not used: a Kaufvertrag bill can mention its Finanzierungsvollmacht or the buyer's Unterwerfung, and a Grundschuld bill often names the Kaufvertrag it finances, so no keyword reliably marks a mixed bill (notaries bill each deed separately, so mixed bills are rare). When an existing cost row already has this document as its Beleg, the note says that row belongs in the loan costs and should be removed from the Nebenkosten (the proposal never deletes). A false match (a Kaufvertrag bill whose subject names a Grundschuld) costs one hand entry with the note explaining it; a missed one would put a deductible cost into the AfA basis, so the list errs toward matching. The classify prompt excludes these invoices from `cost_basis_evidence`; the code guard is the backstop for when it does not. | Luis's notary invoice "Grundschuld-/Hypothekenbestellung mit ZV-Unterwerfung" was classified `cost_basis_evidence` and proposed as a Nebenkosten row, which would have put a deductible cost into the 2 %/3 % AfA basis. Step 4 books the payment as `loan_fees`. |
| D36 | **An invoice's title is a short description, and its tax year is derived** (7 Nov 2026). The model writes `title` itself, in German, two to six words naming what the document is for (`Notarrechnung Kaufvertrag`, `Notarrechnung Grundschuldbestellung`, `Grunderwerbsteuerbescheid`), citing the passage it is based on; it is the one field not copied word for word, so it parses at medium confidence. `InvoiceExtraction` gains `subject` (the Betreff or Gegenstand as written), which D35 reads. For an invoice, `typing.tax_year` is derived in code from the year of `due_on`, else of `document_date` (`derived_from` names the one used; none stated → not proposed). It is the document's filing year (retention, checklist); the year the cost counts in stays the payment's, set by phase 3's rule set (D31). | Luis: the title copied the heading ("Kostenrechnung zur Rechnungsnr. …") and Steuerjahr was never filled for an invoice, whose field set had no tax year. A model guess at a year an invoice does not state would be worse than a derived one. |
| D37 | **The hand-entry fields show what the AI read, and saving never hides it** (7 Nov 2026). On `/documents/[id]`, when the current extraction is an AI kind, the "Felder" section is filled from it through one fixed mapping (`services/documents.py`): Betrag gesamt ← `amount_total` (invoice, Bescheid), `amount` (Kautionsnachweis), `interest_paid` (Zinsbescheinigung); Absender ← `sender` (invoice, Bescheid), `lender` (Darlehensvertrag, Zinsbescheinigung); Referenz ← `reference` (invoice, Bescheid, Grundsteuerbescheid), `contract_number` (Darlehensvertrag, Zinsbescheinigung); Zeitraum ← `period_start`/`period_end` (Kontoauszug); Notizen ← `subject` (invoice). A field the kind has no place for is not shown for that document. Saving with the section unchanged writes no extraction version. Changing a value writes the next version **of the same kind** with source `manual`: every other field is copied as it was, a changed field becomes the typed value with no source, checks `[validates]`, confidence high. Clearing a shown field makes it missing. The service never writes a `manual`-kind version over an AI one: the API's hand fields are always translated into the current kind, and a non-empty hand field that kind has no place for is refused (`document.extraction_kind_mismatch`), so no client can bury a reading under an empty hand version. Hand fields carry the extraction version they were shown from (`shown_version`, required with `fields`; 0 = none yet); a save whose version is no longer current is refused before anything is written (`document.extraction_stale`), so a form left open across a re-read, a second tab or an old frontend fails loudly instead of writing stale values as typed ones (review F1, 7 Nov 2026). The page reloads the document when a re-read finishes. | Found 7 Nov 2026: saving the typing form after an AI reading sent six empty hand fields, which became a new current version of nulls over the reading; step 4's document match and the Hausgeld rule read the current version. |
| D38 | **A review answer never becomes an IBAN-only rule for a counterparty a matcher owns** (step 4 review F1, 7 Nov 2026). `infer_rule` returns no `single_category` rule when the line's counterparty HMAC is an owner account, a tenant's or a loan account's; the split-method rules (`by_rent_schedule`, `by_loan_schedule`, `by_wirtschaftsplan`, `owner_transfer`) stay, since they are exact-only. The "Künftig immer so zuordnen" box is then not offered (the user can type a purpose pattern instead; a counterparty-name pattern alone is refused with `rule.not_learnable`, since the lender's or tenant's name is on every line: step 4 review round 2, 7 Nov 2026). | Reproduced: a one-off 25 € bank fee from the loan account confirmed as `bank_fees` became a rule that booked the next 1,400 € annuity wholly as deductible `bank_fees`, principal included, at confidence 1; a Kaution from the tenant became `rent_cold` income the same way. |
| D39 | **A loan payment is split by the schedule entry whose due date is nearest the booking date, within 10 days either side** (step 4 review F2, 7 Nov 2026), not "the entry due in the booking month": a bank that books on 31 May for 1 June must get June's entry. No entry within the window → no match; two entries equally near → no match. The service sends a second line to an entry that already has a confirmed allocation to review (Sondertilgung or holiday shift). | Reproduced: −1,400 € booked 31 May was split with May's interest and principal at confidence 1. |
| D40 | **A model's answer never auto-confirms a matcher category or a split** (Luis, 7 Nov 2026, from step 4 review round 2). Even with `auto_accept_llm` on, an LLM set goes to review (confidence 0.5, a question) when any part is `rent_cold`, `operating_costs_prepayment`, `loan_interest`, `loan_principal`, a `hausgeld_*` category or an owner transfer, or when it has more than one part. The guard sits in `services.matching`; `review_routing.yaml` and the thresholds are unchanged, and with the setting off the model's own confidence and question stand. | The model only sees a line when the rent, loan and owner matchers failed, so a model rent or annuity answer has no tenancy or loan and escapes the double-booking guards; a model-chosen euro split breaks principle 1 (AI explains, code calculates). |
| D41 | **Step 5: the eval record, the real-document evals and the scrub** (orchestrator, 7 Nov 2026, settling what D19 and D29 left open). (1) The eval record is the `proposal.applied` event: it already holds, per record and field, the `FieldDecision` without values, and events are kept, so accuracy survives the D19 scrub. No proposed or final values are kept anywhere after the scrub (D19's "proposed and final values for non-personal fields" is dropped: §3.5 forbids values in event payloads, and nothing else would hold them). `hausbuch evals report` reads those events with the owner URL (like `bootstrap`), read-only, and prints per extraction kind and per field: proposed, accepted, edited, cleared, skipped, and the accepted share; plus the accepted share over money and date fields against the 90 % target. (2) `hausbuch evals run` sends the PDFs and images in `HAUSBUCH_EVAL_DIR` through the same classify and extract requests as the `document_read` job (live, the `hausbuch-evals` key, about $0.02–0.05 per document) and compares each field with a golden file `<name>.expected.yaml` beside the document: kind plus field values. `--write-expected` writes the golden file from the current reading once Luis has checked it. Nothing is written into the repo; by default only match / differs / missing is printed per field, values only with `--show-values` (so the output can be pasted into a chat). Synthetic recordings stay with `tests/evals/record.py`. (3) `scrub_proposals`, a daily Procrastinate periodic task: nulls `fields` and `decisions` and sets `scrubbed_at` on proposals in a final status (`applied`, `rejected`, `superseded`, `failed`) whose `decided_at`, or `updated_at` when not decided, is more than 90 days old. Superseded and failed proposals are included: they hold the same personal data. `blocked_tenant_data` is not final (step 5 review F1, 7 Nov 2026; this point first listed it): it can still be applied or rejected, so scrubbing it would leave an inbox item nobody can resolve; it is scrubbed once decided or superseded. No event (a proposal row is mutable state, not domain history); one log line with the count. (4) The bucket lifecycle rule stays a phase 1 known gap: it belongs with the document retention job, which step 5 does not build. | D19's 90-day rule needed a durable place for the decisions, and D29's hand-run real evals needed something to compare against. |
| D42 | **Step 2: the settings switch, the deposit proof and the erasure scrub** (orchestrator, 7 Nov 2026, after the privacy note; most of step 2, the lease and handover pre-fill and their apply, already shipped with step 1). (1) **Settings:** `GET /api/workspace/settings` returns `WorkspaceSettings`; `PATCH /api/workspace/settings` takes `WorkspaceSettingsPatch`, writes only the values that change and appends one `workspace.settings_changed` event (entity `workspace`, the changed names and new booleans); a patch that changes nothing writes no event. Any signed-in member may change them (one household). A new page `/settings` (Einstellungen, linked at the foot of the sidebar) shows both switches with the explanations of §11.2 and D26/D40; the blocked-proposal message on `/documents/[id]` links to it. A setting is never changed by a direct database write. (2) **Kautionsnachweis:** `rules.prefill` proposes a `deposit_received` record (`amount`, `received_on`, `tenancy_id` derived) plus `link:tenancy` when amount and date are both stated and exactly one tenancy is found: the one whose `deposit_amount` equals the amount, else the workspace's only tenancy; none or several → typing only, with a note. A receipt already recorded on that tenancy with the same amount and date → `unchanged`. Apply calls `tenancies.record_deposit_received`. New `prefill.yaml` cases: found by amount, found as the only tenancy, ambiguous, already recorded. (3) **Erasure scrub** (D18, D19, D32), inside `erase_tenant`'s transaction: the documents in scope are those of proposals whose `applied_entities` name the tenant, plus the documents linked to the tenant or to any tenancy they were ever a party of. Every proposal of those documents, any status, has `fields` and `decisions` nulled and `scrubbed_at` set; a pending one (queued, running, ready) is first marked `superseded` with its event, so it can no longer be applied. Every `ai_call` row of those documents, and of transactions whose counterparty IBAN hash is the tenant's (read before the tenant row is nulled), has `output` and `raw_text` nulled. Every learned rule whose matcher holds the tenant's IBAN hash or which is scoped to one of those tenancies is disabled (`rule.disabled`) and its matcher's IBAN hash and name and purpose patterns are nulled. `tenant.erased` gains the counts `proposals_scrubbed`, `ai_calls_scrubbed`, `rules_disabled` (default 0, so older events still read). Untouched: the originals, `document_extraction` rows (their document's retention, §3.5), transactions and events. | The setting existed as a column with no way to change it except a database write that would skip its event; the deposit proof was typing only; D18, D19 and D32 promised the erasure scrub to this step. |
| D43 | **An Adobe-only form (dynamic XFA) is never sent to the model** (Luis, 7 Nov 2026, added to step 2). Such a PDF holds the document as Adobe form instructions; its ordinary PDF layer is a single "Please wait …" placeholder page, which is all the model, browsers and other viewers see. `ai.documents.is_adobe_only_form(data)` says yes when the catalog has `/NeedsRendering true`, or when the AcroForm carries `/XFA` and the page text is Adobe's placeholder ("Please wait" or "eventually replaced"); a static XFA form with a real PDF layer is read as usual. `document_read` checks it before any model call: the proposal is `failed` with `error_code = document.adobe_form`, not retried, no `ai_call` row. The German message: "Dieses PDF ist ein Adobe-Formular und lässt sich nur in Adobe Reader anzeigen. Öffnen Sie es dort, drucken Sie es als PDF und laden Sie die Kopie hoch." The original stays in the vault unchanged (principle 6). Tests use a synthetic PDF built in code, never a real form. | Luis's real Darlehensvertrag (checked 7 Nov 2026, structure only) is such a form, made with Adobe Experience Manager: one placeholder page, the contract in a 1 MB XFA template plus 66 KB of data, no digital signature, only Reader usage rights (`/UR3`). Reading it would cost a call and return nothing; a printed copy is a normal PDF the model reads with citations. |
| D44 | **Two categories for the purchase: `loan_disbursement` (Darlehensauszahlung, pass_through) and `purchase_price` (Kaufpreiszahlung, capitalized)** (Luis, 7 Nov 2026, from his real April–July statements). The rental account received the loan's payout from the lender and paid the purchase price to the seller from it; neither fitted the taxonomy (`other_income` would have counted borrowed money as rent income). The payout is neither income nor a cost; the principal is on the loan record. The price enters the AfA basis through the property record (price and price split), so **phase 3 must never sum `purchase_price` allocations into the basis** a second time; the allocation only records that and when it was paid. Both have no threshold (one-offs, always a person's answer, the remember box starts unticked). Migration `0009_payout_price_categories` widens the category CHECK on `allocation` and `matching_rule`; its downgrade is refused while a row uses either. Fixtures `categories.yaml`, `thresholds.yaml`, `review_routing.yaml` gained the two rows in PR #61, which is this decision's spec change. | §8.2; phase 3 spec input (AfA basis). |
| D45 | **A learned rule or a change to matcher master data re-checks the lines waiting in the inbox** (Luis, 7 Nov 2026: "build the rule re-apply for waiting payments", then "add the re-check for loans and IBANs too"). Before, a rule or a new loan only helped lines imported afterwards. Job `rematch_waiting(workspace_id, trigger)`, trigger `<kind>:<id>` with kind rule, loan, tenant, tenancy or account (ids only, never an IBAN or hash), queued in the same transaction as the change by: `create_rule`; `create_loan`, `update_loan` (not for the contract number or masked IBAN alone), `correct_disbursement`; `update_tenant` when an IBAN is set; a tenancy's `set_parties`, every rent row, and `correct_move_out` to a later date; an owner account on create or when flagged. It runs only the deterministic order (rules, matchers, document match; never the model, so D40 holds) on lines still waiting: a reconciled import, not a duplicate, no confirmed allocation. Each line is re-read `FOR UPDATE` and skipped unless still waiting, so a line a person, rule or matcher confirmed is never touched. One waiting job per trigger covers a burst of edits; the lookup holds that job `FOR KEY SHARE` until the change commits. `matching.write()`: an auto-confirming set equal to the line's proposed rows is now written as a confirmed set superseding the proposal (it was skipped as unchanged, so a rule could never book a line whose model proposal happened to match). **Accepted trade-off:** a wrong learned rule now books every waiting line of that payer at once (confirmed, method `rule`), and a later corrected rule re-checks only waiting lines, so those must be corrected by hand; the remember box stays off by default for one-off categories (D38). Reviewed in PR #62 (verdict merge; F1 and F2 fixed). | §8.1; the inbox reloads after a save that learned a rule. |
| D46 | **A loan paid out in parts has an interest-only phase before its first annuity** (Luis, 8 Nov 2026, from his real statements: the loan was paid out on 30 Jun and 2 Oct 2026 and the bank debits only the interest on the drawn amount until the first full instalment in December, so the amount changes each month and never matched the schedule). Loan field `interest_only_before_first_payment` (default false; migration `0010_loan_interest_only`, downgrade refused while a loan has it set). (1) **Schedule:** with the flag, period 1 runs 30 days from one month before `first_payment_on` on the full principal (without it, payment 1 would carry the interest since the first payout); a loan paid out after that day keeps `days360(disbursed_on, first_payment_on)`. This amends the phase 1 convention (D10, `loan_schedule.yaml`). (2) **Matcher:** a debit from the loan strictly between `disbursed_on` and `first_payment_on` whose purpose states the interest (`Zinsen <amount>`, one distinct amount, equal to the debit) and no `Tilgung` above zero is booked whole as `loan_interest`, exact confidence, over the range the text states (`Für dd.mm.yyyy - dd.mm.yyyy`) else the booking month. It is the bank's own figure read by code, so principle 1 and D40 hold; anything else goes on through the order to review. A learned `by_loan_schedule` rule does not use this path. (3) **Which loan** (review F1): a line from a loan's own account is matched only against that loan; the lender-name fallback applies only when the account is no loan's, so two loans at one bank never claim each other's debits (this also closed the same gap on the annuity path). Enter `disbursed_on` as the first partial payout and `principal` as the total. **Accepted:** a later payout part is not modelled (the `loan.disbursed` milestone carries the full principal on the first date); rolling the API image back after a loan was created on the new image breaks reading that loan's event, whose payload carries the new field (as with D42's `tenant.erased`); two consecutive stated ranges sharing a boundary day would send the second to review through the D39 overlap guard (Luis's texts run 1st–30th, so they do not). | §8.1; phase 1 D10 schedule convention; `matchers.yaml`, `loan_schedule.yaml` cases added in PR #64. |
| D47 | **A Darlehensvertrag pre-fills the contract date, the follow-up rate and the interest-only phase; the contract number carries no label; a captured page can be turned** (Luis, 8 Nov 2026, after photographing his real contract: "it missed the interest rate after the initial 10 year period", the Kontonummer came with its label, the interest-only box stayed empty and no Vertragsdatum was proposed; "Yes fix them"). (1) **Vertragsdatum:** `signed_on` was extracted but never proposed for a loan (a gap in `rules/prefill.py`, unlike the lease); it is now proposed on a create when stated, and the field asks for the later date when the parties sign on different days (a contract is concluded by the last signature). (2) **Anschlusszins:** new extraction field `follow_up_rate`: an agreed rate after the Zinsbindung, or the variable rate the loan continues at ("Der veränderliche Sollzinssatz beträgt derzeit …"), never the Mindestzins or the Effektivzins. This replaces §6.2's "`follow_up_rate` null": the value is still Luis's assumption for the projection (Decisions, "Refinancing"), and the contract's stated variable rate is only its starting value, verified in the review like every other. (3) **Interest-only phase (D46):** new extraction field `interest_only_clause`, the quoted sentence that only interest is paid until repayment starts; code, not the model, sets `interest_only_before_first_payment` true when it says "nur (die) (Soll)zinsen", shown as derived from that clause (as the rent model is from the rent clause); otherwise nothing is proposed and the box stays as it is. (4) **Contract number and dates:** when no Darlehens- or Vertragsnummer is printed, the loan account's Kontonummer is the number (a Sparkasse names the loan by it); a leading label (Kontonummer, Darlehensnummer, Vertragsnummer, Nr.) or a trailing parenthetical naming where it was found is removed in code, as a trailing page reference already is (`_without_page_reference`, PR #56); a date with a two-digit year, as handwritten by a signature ("30.3.26"), is read as 20yy below 70, else 19yy. (5) **Capture (§9):** each page in the strip has a turn button (90° clockwise, re-encoded as before), because a phone pointed down at paper on a table guesses the orientation and can store it sideways. **Not changed:** the camera itself stays the phone's own app; its shutter is outside the page (an in-page camera is a later option). | §6.2 Darlehensvertrag row; §9; `prefill.yaml`, `extraction_parse.yaml` cases in the same PR. |

## 3. Data model

Three migrations, each in the step that first needs it. All follow 0001's conventions
(`workspace_id`, `UNIQUE (workspace_id, id)`, composite FKs, text + CHECK, forced RLS,
explicit `hausbuch_app` grants). New tables are also added to `tables.py` and the schema
tests' table lists (21 → 29, excluding `procrastinate_*`).

### 3.1 Migration `0005_jobs_ai_proposals` (step 1)

| Table / change | Holds | Mutability |
| --- | --- | --- |
| Procrastinate schema | `procrastinate_*` tables, enum and functions, from the installed package's SQL (pinned version); `GRANT SELECT, INSERT, UPDATE, DELETE` on its tables, `USAGE` on sequences, `EXECUTE` on its functions to `hausbuch_app` | Procrastinate's own; not RLS'd (D20) |
| `workspace` + columns | `ai_tenant_documents boolean NOT NULL DEFAULT false`, `auto_accept_llm boolean NOT NULL DEFAULT false` | Update + `workspace.settings_changed` event |
| `ai_call` | `job` (classify / extract / match / explain), `document_id` (nullable, ON DELETE CASCADE), `transaction_id` (nullable, 0007 adds the FK), `model`, `prompt_version`, `inference_geo`, `input_sha256` (of the request body minus the key), `input_tokens`, `output_tokens`, `cache_read_tokens`, `stop_reason`, `output jsonb` (parsed), `raw_text`, `error_code`, `duration_ms`, `created_at` | **No update** (immutable trigger); delete by retention and erasure scrub (`output`, `raw_text` nulled in place: a second trigger exception like `tenant` erasure) |
| `document_extraction` + columns | `kind text NOT NULL DEFAULT 'manual'` CHECK in (`manual`, `purchase_contract`, `loan_contract`, `lease`, `handover_protocol`, `deposit_record`, `invoice`, `bescheid`, `grundsteuerbescheid`, `wirtschaftsplan`, `hausgeldabrechnung`, `zinsbescheinigung`, `statement`); `ai_call_id uuid` nullable | unchanged (append-only) |
| `proposal` | `document_id`, `kind` (same set as extraction kinds), `status` (`queued`, `running`, `ready`, `applied`, `rejected`, `superseded`, `failed`, `blocked_tenant_data`), `error_code`, `extraction_id`, `ai_call_id`, `target_mode` (`create` / `patch`), `targets jsonb` (existing entity refs when `patch`), `fields jsonb` (nullable: the records and their fields, §6.3), `decisions jsonb` (nullable: written once by apply), `applied_entities jsonb`, `created_at`, `updated_at`, `decided_at`, `decided_by` | Mutable (status, decisions) + events |
| `event.entity_type` CHECK | adds `user`, `proposal`, `account`, `statement`, `transaction`, `rule`, `workspace` (all of phase 2 at once, so 0006/0007 do not touch the CHECK again) | — |

### 3.2 Migration `0006_accounts_statements` (step 3)

| Table | Holds | Mutability |
| --- | --- | --- |
| `account` | `label`, `bank`, `iban_masked`, `iban_hmac`, `source` (`upload` now; `fints`, `aggregator` later), `opening_balance`, `opening_balance_on` (the anchor, D21), `is_owner_account boolean` (the owners' private accounts, recognized by IBAN for owner transfers, phase 2 input point 1; they never hold transactions) | Mutable + events |
| `statement_import` | `account_id`, `document_id` (the uploaded file is a document: source `upload`, type `statement`), `format` (`csv_camt`, `camt`, `pdf`; D33), `lines_total`, `lines_new`, `lines_skipped`, `first_booking_on`, `last_booking_on`, `declared_opening` / `declared_closing` (nullable), `balance_source` (`file`, `user`, `none`), `computed_closing`, `status` (`imported`, `reconciled`, `unreconciled`, `failed`), `error_code`, `ai_call_id` (PDF only) | Mutable (status, declared balances) + events |
| `transaction` | `account_id`, `import_id`, `line_hash bytea` UNIQUE with `(workspace_id, account_id)`, `booking_date`, `value_date`, `amount`, `currency` (`EUR` CHECK), `counterparty_name`, `counterparty_iban_masked`, `counterparty_iban_hmac`, `purpose`, `booking_text` (Buchungstext), `bank_reference`, `end_to_end_ref`, `mandate_ref`, `raw jsonb` (the line as parsed), `is_pending boolean` (CSV `Info = Umsatz vorgemerkt` lines are skipped at import, never stored; the column exists for camt `PDNG`), `excluded_as_duplicate_of uuid` (nullable, write-once: set when a cross-format duplicate is resolved as `merged`; an excluded line counts for no balance and gets no allocations) | **No update, no delete** (immutable as imported); the one exception is setting `excluded_as_duplicate_of` from null, allowed by a column-limited trigger |
| `possible_duplicate` | `transaction_id`, `other_transaction_id`, `status` (`open`, `kept_both`, `merged`), `decided_at` | Mutable + events; `merged` sets `other_transaction_id.excluded_as_duplicate_of = transaction_id` in the same command (the later-imported line is the excluded one) |

### 3.3 Migration `0007_allocations_rules` (step 4)

| Table | Holds | Mutability |
| --- | --- | --- |
| `allocation` | `transaction_id`, `amount` (signed, same sign convention as the transaction), `category`, `property_id`, `unit_id`, `tenancy_id`, `loan_id` (nullable scope), `service_period_start` / `_end` (nullable), `recurring boolean`, `method` (`rule`, `matcher_rent`, `matcher_loan`, `matcher_owner`, `matcher_hausgeld`, `document_match`, `llm`, `user`), `confidence numeric(4,3)`, `status` (`proposed`, `confirmed`, `superseded`), `supersedes_id`, `rule_id`, `ai_call_id`, `document_id` (the Beleg), `reasoning`, `question`, `recorded_at`, `confirmed_at`, `confirmed_by` | **Append-only** rows (D23); `status` flips only `proposed → confirmed` and `→ superseded` (an UPDATE allowed on those two columns only, by trigger) |
| `matching_rule` | `matcher jsonb` (`counterparty_iban_hmac`, optional `counterparty_name_pattern` and `purpose_pattern` regexes, optional `amount_sign`; each value typed by CHECK), `split_method` (D25), `category` (for `single_category`), `recurring boolean` (copied from the confirmed set, review F3), `scope` (property / tenancy / loan id), `created_from_allocation_id`, `enabled boolean`, `hit_count` | Mutable + events |
| `ai_call.transaction_id` | FK added | — |

### 3.4 Invariants (new) and where they are enforced

| Invariant | Enforced by |
| --- | --- |
| A proposal is applied or rejected at most once; `decisions` written exactly on apply | CHECK (`status = 'applied'` ⇔ `decisions IS NOT NULL`) + service |
| Σ allocation amounts (status ≠ superseded) = transaction amount | Service layer on every write · `allocation_sum.yaml`; a deferred CHECK is not possible across rows, so the service runs it inside the same transaction before commit |
| One transaction per `(account, line_hash)` | UNIQUE |
| Transactions and `ai_call` rows are never updated (except the erasure scrub columns) | Trigger (`forbid_mutation`, with a column-limited exception for the scrub) |
| Lines of an unreconciled import never get allocations | Service (`match_import` refuses) · `review_routing.yaml` |
| A rule never stores an amount | `MatchingRule` schema has no money field; `rules.yaml` |
| Procrastinate tables are the only non-RLS tables | `tests/db/test_migration.py` (the RLS checks live there) extended with the explicit exclusion list |

### 3.5 Events added in phase 2

All on `EVENT_CATALOG`, actor `user`, `system` (jobs) or `ai` (`actor_id` = model id);
`correlation_id` groups one command or one job run. Payloads carry ids, counts, amounts,
hashes, types and field *names*; never `counterparty_name`, `purpose`, a tenant name or a
model's text. Transaction rows themselves are 10-year tax records that erasure does not
scrub, as the statement original is; AI extraction rows (`document_extraction`, source
`ai`) follow their document's retention and are outside the D19 scrub.

| Type | Payload | Effective date |
| --- | --- | --- |
| `workspace.settings_changed` | changed setting names and new values (booleans only) | forbidden |
| `document.classified` | `ai_call_id`, `type`, `subtype`, `confidence`, `contains_tenant_data` | forbidden |
| `document.extracted` | `ai_call_id`, `extraction_version`, `kind`, `field_names` | forbidden |
| `document.read_failed` | `error_code`, `attempts` | forbidden |
| `proposal.created` / `proposal.superseded` / `proposal.rejected` | `document_id`, `kind`, `record_keys` | forbidden |
| `proposal.applied` | `document_id`, `kind`, per record: key, mode, entity ref, per field: `FieldDecision` (no values) | forbidden |
| `user.signed_in` … `user.enrolled` (§10) | passkey / invite / user ids, label, count | forbidden |
| `account.created` / `account.updated` | label, bank, `iban_masked`, `opening_balance`, `opening_balance_on` / `Updated` | forbidden |
| `statement.imported` | `account_id`, `document_id`, `format`, `lines_total`, `lines_new`, `lines_skipped`, first/last booking date, `transaction_ids` | optional (last booking date) |
| `statement.reconciled` / `statement.unreconciled` | `declared_closing`, `computed_closing`, `difference`, `balance_source` | forbidden |
| `statement.duplicate_resolved` | `possible_duplicate_id`, both transaction ids, `status` | forbidden |
| `transaction.allocated` | `allocation_ids`, per allocation: category, amount, scope ids, period, recurring (no tax year, D31: this row said "tax year" until 7 Nov 2026, after the schema had dropped it); `method`, `confidence`, `rule_id`, `ai_call_id`, `auto` (bool) | required (booking date) |
| `transaction.reallocated` | same shape, `supersedes` allocation ids | required |
| `rule.created` / `rule.disabled` | `matcher` (IBAN hash and patterns), `split_method`, `category`, scope ids, `created_from_allocation_id` | forbidden |

## 4. Schemas (new modules; the spec PR writes them)

Money, rates and shares stay decimal strings on the wire (spec amendment 2). All models
extend `Schema` (frozen, extra forbidden). Only the load-bearing shapes are listed; `*Create`
and read shapes follow phase 1's pattern.

### 4.1 `schemas/ai.py`

```python
class AiJob(StrEnum): CLASSIFY = "classify"; EXTRACT = "extract"; MATCH = "match"; EXPLAIN = "explain"

class ClassifyOutput(Schema):          # structured output (JSON schema), Haiku
    type: DocumentType
    subtype: DocumentSubtype | None
    contains_tenant_data: bool         # second gate (D16)
    language: Literal["de", "en", "other"]
    confidence: Literal["high", "medium", "low"]   # enum, not a number: the model is bad at numbers

class SourceRef(Schema):               # where a value came from (D15)
    page: int = Field(ge=1)            # 1-indexed, from page_location.start_page_number
    quote: str                         # cited_text, verbatim

class ExtractedValue(Schema):          # one field of a per-type extraction
    value: JsonValue | None
    source: SourceRef | None
    derived_from: list[str] = []       # field names, when code computed it (e.g. building_share)
    checks: list[Literal["cited", "value_in_quote", "validates", "derived"]]
    confidence: Literal["high", "medium", "low"]   # from checks: high = cited+value_in_quote+validates

class AiCall(Record): job, model, prompt_version, inference_geo, input_sha256, input_tokens,
    output_tokens, cache_read_tokens, stop_reason, duration_ms, error_code,
    document_id | None, transaction_id | None     # output/raw_text are not in the read shape
```

Per-type extraction models (`schemas/extraction.py`), each a flat object of
`ExtractedValue`s, so the review screen renders any kind generically. Field lists are the
pre-fill tables in §6.2; `PurchaseContractExtraction`, `LoanContractExtraction`,
`LeaseExtraction`, `HandoverProtocolExtraction`, `DepositRecordExtraction`,
`InvoiceExtraction` (Nebenkosten and later invoices), `BescheidExtraction`,
`GrundsteuerbescheidExtraction`, `WirtschaftsplanExtraction`, `HausgeldabrechnungExtraction`,
`ZinsbescheinigungExtraction`, `StatementExtraction` (PDF Kontoauszug: balances + lines).
`DocumentExtraction.fields` becomes `ManualDocumentFields | <one of these>` discriminated by
`kind`.

### 4.2 `schemas/proposal.py`

```python
class ProposalRecord(Schema):
    """One record the proposal would create or patch."""
    key: str                           # "property", "unit", "cost:notary", "tenant:1", "tenancy", "rent", "typing", "link:property"
    entity_type: EntityType | Literal["acquisition_cost", "price_split", "rent_schedule", "typing", "link", "ownership"]
    mode: Literal["create", "patch", "correction", "unchanged"]
    target_id: UUID | None             # when patch/correction/unchanged
    fields: dict[str, ExtractedValue]  # field name as in the *Create / *Patch schema
    current: dict[str, JsonValue] | None   # the existing record's values when target_id is set
    notes: list[str]                   # e.g. "Übergang laut §5: Tag der vollständigen Kaufpreiszahlung"

class Proposal(Record): document_id, kind, status, error_code, target_mode, records: list[ProposalRecord],
    extraction_id | None, ai_call_id | None, created_at, updated_at, decided_at

class FieldDecision(StrEnum): ACCEPTED = "accepted"; EDITED = "edited"; CLEARED = "cleared"; SKIPPED = "skipped"

class ProposalApplyRecord(Schema):
    key: str
    skip: bool = False                 # do not create / patch this record
    values: dict[str, JsonValue]       # the final values, validated by the matching *Create/*Patch model in the service
    decisions: dict[str, FieldDecision]

class ProposalApplyBody(Schema):
    records: list[ProposalApplyRecord]
    effective_date_corrections: dict[str, date] = {}   # for milestone dates routed through correct_* (§6.5)
```

### 4.3 `schemas/bank.py`

```python
class AccountCreate(Schema): label, bank, iban: IbanInput, opening_balance: Money, opening_balance_on: date, is_owner_account: bool = False
class Account(MutableRecord): label, bank, iban_masked, source, opening_balance, opening_balance_on, is_owner_account

class StatementFormat(StrEnum): CSV_CAMT = "csv_camt"; CAMT = "camt"; PDF = "pdf"

class StatementLine(Schema):           # the BankSource output, format-independent
    booking_date: date; value_date: date | None; amount: Money; currency: str
    counterparty_name: str | None; counterparty_iban: str | None   # plaintext here only; masked+HMAC on store
    purpose: str; booking_text: str | None
    bank_reference: str | None; end_to_end_ref: str | None; mandate_ref: str | None
    pending: bool = False
    raw: dict[str, JsonValue]

class ParsedStatement(Schema):
    format: StatementFormat; account_iban: str | None
    opening_balance: Money | None; closing_balance: Money | None   # None for CSV (D21)
    lines: list[StatementLine]
    warnings: list[str]

class BankSource(Protocol):           # services/bank/__init__.py; implementations: csv_camt.py, camt.py, pdf.py (via extraction)
    def parse(self, data: bytes, media_type: str) -> ParsedStatement: ...

class StatementImport(MutableRecord): account_id, document_id, format, lines_total, lines_new, lines_skipped,
    first_booking_on, last_booking_on, declared_opening, declared_closing, balance_source, computed_closing, status, error_code
class DeclareBalanceBody(Schema): closing_balance: Money; opening_balance: Money | None = None
class Transaction(Record): account_id, import_id, line_hash: Sha256Hex, booking_date, value_date, amount, currency,
    counterparty_name, counterparty_iban_masked, purpose, booking_text, bank_reference, end_to_end_ref
```

### 4.4 `schemas/allocation.py`

```python
class AllocationCategory(StrEnum): ...   # §8.2
class TaxTreatment(StrEnum): INCOME = "income"; DEDUCTIBLE = "deductible"; NOT_DEDUCTIBLE = "not_deductible"
    CAPITALIZED = "capitalized"; PASS_THROUGH = "pass_through"; UNDECIDED = "undecided"
class AllocationMethod(StrEnum): RULE, MATCHER_RENT, MATCHER_LOAN, MATCHER_OWNER, MATCHER_HAUSGELD, DOCUMENT_MATCH, LLM, USER
class SplitMethod(StrEnum): SINGLE_CATEGORY, BY_RENT_SCHEDULE, BY_LOAN_SCHEDULE, BY_WIRTSCHAFTSPLAN, OWNER_TRANSFER

class AllocationDraft(Schema):          # what a matcher, a rule, the model or the user proposes
    amount: Money; category: AllocationCategory
    property_id | unit_id | tenancy_id | loan_id: UUID | None
    service_period_start: date | None; service_period_end: date | None; recurring: bool
    document_id: UUID | None

class AllocationSet(Schema):            # one transaction's proposal, validated: Σ amount == transaction.amount
    transaction_id: UUID; allocations: list[AllocationDraft] = Field(min_length=1)
    method: AllocationMethod; confidence: Decimal (0..1); reasoning: str | None; question: str | None
    rule_id | ai_call_id: UUID | None

class RuleMatcher(Schema): counterparty_iban_hmac: str | None; counterparty_name_pattern: str | None; purpose_pattern: str | None; amount_sign: Literal["credit", "debit"] | None
class MatchingRuleCreate(Schema): matcher: RuleMatcher; split_method: SplitMethod; category: AllocationCategory | None; recurring: bool; property_id / tenancy_id / loan_id

class LlmMatchOutput(Schema):           # structured output of the categorize job (no citations needed)
    allocations: list[LlmAllocation]    # category, amount (string), service_period (text), recurring
    confidence: Literal["high", "medium", "low"]; reasoning: str; question: str | None; ambiguous: bool
```

### 4.5 Settings (`settings.py`)

`HAUSBUCH_AI_ENABLED` (default `false`: production turns it on), `HAUSBUCH_ANTHROPIC_API_KEY`
(secret, required when enabled), `HAUSBUCH_AI_MODEL_CLASSIFY` (`claude-haiku-5-5`),
`HAUSBUCH_AI_MODEL_EXTRACT` (`claude-sonnet-5-5`), `HAUSBUCH_AI_MODEL_HARD`
(`claude-opus-5-5`), `HAUSBUCH_AI_INFERENCE_GEO` (`us` default | `global`), `HAUSBUCH_AI_MAX_PAGES`
(100), `HAUSBUCH_AI_TIMEOUT_S` (120), `HAUSBUCH_EVAL_DIR` (optional). Dependencies added in
the spec PR at once (as step 3 did): `anthropic`, `procrastinate` (psycopg connector; needs
`psycopg[pool]`), `pypdf` (page count and splitting only), `lxml` (camt.052 / camt.053, D33).

## 5. Jobs and the AI layer

### 5.1 Queue

Procrastinate (3.10.0, Sep 2026; Python ≥ 3.10, PostgreSQL ≥ 13) on the same database.
`api/app.py`'s `Worker` protocol is implemented by `jobs/worker.py` wrapping
`procrastinate.App.run_worker_async(concurrency=settings.worker_concurrency)` and `stop()`;
`main.py` passes it instead of `None`. Tasks (`jobs/tasks.py`), each idempotent and holding
a `lock`, so two jobs for the same document or import never run at once:

| Task | Lock | Retries | Dead letter |
| --- | --- | --- | --- |
| `document_read(workspace_id, document_id, proposal_id)` classify → extract → build proposal | `doc:{id}` | 3, exponential (10 s, 60 s, 5 min); never on 4xx from the API except 429/529 | `proposal.status = failed`, `error_code`; inbox "Konnte nicht gelesen werden" |
| `statement_parse(workspace_id, import_id)` (PDF only: extraction; CSV/XML parse inline in the request) | `import:{id}` | 3 | `statement_import.status = failed` |
| `match_import(workspace_id, import_id)` and `match_transaction(workspace_id, transaction_id)` | `tx:{id}` | 3 | allocation set absent → inbox "Nicht zugeordnet" |
| `rematch_on_evidence(workspace_id, document_id)` (new Wirtschaftsplan / invoice: re-run matching on open or rule-split transactions it affects) | `doc:{id}` | 3 | logged, inbox note |
| `scrub_proposals(timestamp)` periodic daily at 03:15 UTC (D19, D41): every provisioned workspace in its own transaction | — | none (a missed run moves to the next day) | logged by exception class; the other workspaces continue |

*Amended in step 1 (review F2, F3, 6 Nov 2026):* this paragraph first said each task was also
keyed by a `queueing_lock` so a double enqueue collapses. Procrastinate allows one *waiting*
job per queueing lock, and a retry moves a running job back to waiting, so a retry while a
re-run's job was already waiting failed inside Procrastinate and left the document's reads
stuck for good. There is no queueing lock: a second job for the same document is harmless,
because it finds no pending proposal and sends nothing. A job left running by an instance
that stopped (every deploy restarts the container) is retried by a sweep in the worker, since
Procrastinate's own stalled-worker pruning removes the worker but not its job.

A job opens `workspace_transaction(workspace_id)` as the app role, exactly like a request;
jobs never run in the request thread; requests enqueue and return 202 with the id to poll.
`GET /healthz` gains `worker: ok | stopped` (no DB details).

### 5.2 Reading documents

No OCR engine. The worker sends the original as a `document` block: PDF as base64 (text
layer and page images are both used by the API; 1,500–3,000 text tokens per page plus image
tokens), JPEG/PNG/WebP as an `image` block. Limits (API docs 6 Nov 2026): 128 MB per
request, 600 pages (100 when the request's context window is under 1M tokens; all three
models here have 1M); the worker counts pages
with `pypdf` and sends at most `HAUSBUCH_AI_MAX_PAGES`, splitting longer files into one
request per chunk with the page offset added to every citation. The document is passed as
data, under a system prompt that says instructions inside the document are content, not
commands; the user turn carries only the field list. Classification sends the first three
pages only.

### 5.3 Calls and validation

- **Classify** (Haiku, structured output `ClassifyOutput`, `max_tokens` 300).
- **Extract** (Sonnet, **citations enabled**, no structured output, D15). Prompt: one line per
  field, `field_key: value` or `field_key: unbekannt`, German document, values in the
  document's own form (the code normalizes `1.234,56 €`, `01.03.2027`, `3,65 %`). The parser
  reads the text blocks, keys the lines, attaches the `page_location` citations (page,
  `cited_text`) and builds `ExtractedValue`s; an unknown key is ignored, a missing required
  key is `value = None`. Each value is validated with the target field's Pydantic type;
  `value_in_quote` checks that the digits of an amount/date/IBAN or the normalized string
  occur in the cited text. A response that yields no parsable line is retried once with the
  same prompt; the second failure is `failed` with `error_code = ai.unparsable`.
- **Match / categorize** (Sonnet, structured output `LlmMatchOutput`; Opus when the Sonnet
  answer has `ambiguous = true`). Input: the transaction, the property's open items, the
  tenancies' masked IBANs (only the one being matched unmasked to the last four), the loans,
  the five most similar confirmed allocations (by counterparty hash and text), and the
  category list with one-line definitions. Never another tenant's name.
- **Explain** (Haiku) only when a deterministic matcher lands in review (e.g. rent amount
  off by a few euros): produces the one-sentence reasoning and the question from a template
  input; template text is the fallback.
- Every call: `ai_call` row written in the job's transaction before the result is used;
  `prompt_version` = the prompt file's hash in `hausbuch/ai/prompts/`; `input_sha256` over the
  request minus the key; `inference_geo` from the response's `usage`.
- Structured outputs: JSON schema from the Pydantic model via the SDK's `messages.parse`;
  `stop_reason = refusal` or `max_tokens` → one retry, then `failed`. Enum values are compared
  case-insensitively (documented API caveat).

### 5.4 Cost per document (Sonnet 5.5, standard pricing, 6 Nov 2026)

A page costs about 2,500 text tokens + 1,600 image tokens. Kaufvertrag 30 pages ≈ 125k input
tokens → $0.25 + output ≈ $0.02 → **about $0.27**; Mietvertrag 12 pages ≈ $0.11;
Darlehensvertrag 15 pages ≈ $0.14; a 2-page invoice or Bescheid ≈ $0.02; classification ≈
$0.002. Luis's initial set (≈ 15 documents) ≈ **$1.5**; a monthly statement (≈ 40 lines, of
which ≈ 10 reach the LLM) ≈ $0.10. `inference_geo = us` multiplies by 1.1. No Batch API
for pre-fill (Luis is waiting at the form); it may serve `rematch_on_evidence` later.

### 5.5 Privacy gate (D16) in the job

1. `document_read` refuses to send when `settings.ai_enabled` is false → `failed`,
   `error_code = ai.disabled` (this line first spelled it `ai_disabled`; the code uses the dotted
   spelling of §12 everywhere).
2. When the upload was marked `contains_tenant_data` and `workspace.ai_tenant_documents` is
   false → `blocked_tenant_data`; the detail page explains and offers manual typing.
   *Amended in step 1 (review F1):* the mark is sticky per document: a proposal inherits it
   from **any** earlier proposal of the same document, so uploading the same bytes again
   without the mark does not unlock a read. The gate runs when the proposal is created
   (nothing is enqueued) and again in the job, from the row.
3. After classification, if `contains_tenant_data` or type/subtype ∈ {lease,
   handover_protocol, deposit_record, letter from a tenant} and the setting is false → stop,
   `blocked_tenant_data`; the classification's `ai_call` row is kept (it already happened);
   the document is typed with the classifier's result as a *proposal* only.

## 6. Pre-fill (Vorschläge)

### 6.1 Flow

Upload (`POST /documents`, new form fields `ai: bool` default `settings.ai_enabled`,
`contains_tenant_data: bool` default false) → a `proposal` row (`queued`) and `document_read`
enqueued → 201 with the document and `proposal_id` → the document page polls
`GET /proposals/{id}` (2 s) → `ready` renders the review screen → `POST /proposals/{id}/apply`
→ 201 with the created/patched entity refs and the document linked to each. `POST
/documents/{id}/proposals` re-runs on demand (a pending proposal becomes `superseded`); `POST
/proposals/{id}/reject` records the rejection (eval signal) without writes. The upload route
is unchanged for `ai = false`.

### 6.2 What each document type proposes

Field names are the `*Create` / `*Patch` field names. "Quote" means the review screen shows
page + cited text; "derived" means code computed it from cited values and shows the formula.

| Kind (subtype) | Records proposed | Fields and their source |
| --- | --- | --- |
| **Kaufvertrag** (`contract` / `purchase_contract`) | `typing`; `property` (create, or patch by address); `unit` (ETW: one); `price_split` (`purchase_contract` candidate) when stated; `ownership` when buyers match owners; `link:property` | `kind` (Wohnungseigentum → `etw`), `address.*`, `build_year` (often absent), `notarized_on` (Urkundendatum), `purchase_date` (the Besitzübergang clause; when it says "Tag der vollständigen Kaufpreiszahlung" the value is null and the clause is quoted in `notes`), `purchase_price`; unit `label` (WE Nr.), `mea` (…/1000stel), `floor_area_m2` if stated; `building_share` derived = (price − land share)/price when the contract allocates a land value; ownership shares from "je zur Hälfte"; typing `document_date = notarized_on`, `title` (never in events) |
| **Nebenkosten** invoices and Bescheide (`invoice` or `bescheid` / `cost_basis_evidence`) | `typing` (an invoice's `tax_year` derived, D36); `cost:<kind>` (create, or patch when a row of that kind exists without a Beleg); `link:property`. A **financing** invoice (Grundschuld, Hypothek, Darlehen; D35) gets no `cost:` record and no subtype, a note on `typing`, and `link:loan` when the property has one loan | `kind` by sender (Notar → `notary`, Amtsgericht/Grundbuchamt → `land_registry`, Finanzamt Grunderwerbsteuer → `real_estate_transfer_tax`, Makler → `broker`, else `other`), `amount` (gross total), `paid_on` null (the statement sets it in step 4), `document_id` = this document, `note` = reference number |
| **Darlehensvertrag** (`contract` / `loan_contract`) | `typing`; `loan` (create, or patch by lender + contract number); `link:loan` | `lender`, `contract_number`, `principal` (Nennbetrag), `nominal_rate` (Sollzins p.a.), `monthly_payment` (the Rate; derived = principal × (rate + anfängliche Tilgung)/12 when only the Tilgungssatz is stated), `fixed_until` (Sollzinsbindung), `follow_up_rate` when stated (the agreed or current variable rate after the Zinsbindung, D47), `disbursed_on` and `first_payment_on` when stated (else null: the statement knows), `account_iban` (Darlehenskonto), `signed_on` (the latest signature, on a create), `interest_only_before_first_payment` true when the interest-only clause says so (D47) |
| **Mietvertrag** (`contract` / `lease`) — **step 2, gated** | `typing`; `tenant:n` per Mieter (create, or patch by exact name); `tenancy` (create on the unit matched by address and the lease's unit label, compared by key, not exact text, amended 7 Nov 2026, PR #56; or patch by unit + start date); `rent` (initial rent); `link:tenancy` | tenant `full_name`, `email`, `phone`, `iban` when the contract states them; tenancy `start_date` (Mietbeginn), `signed_on`, `fixed_term_until`, `rent_model` (Staffel/Index clauses → `staffel`/`index`, else `fixed`), `deposit_amount`, `deposit_form` (Barkaution → `cash_account`, Bürgschaft → `surety`/`bank_guarantee`; "bar" only as a whole word, so "vereinbart" is not cash; amended 7 Nov 2026, PR #51: a deposit amount above 0 with no form named is derived `cash_account` from `deposit_amount` and `deposit_clause` at low confidence, and an amount stated in words proposes the form as missing, so a tenancy is never proposed with an amount next to `none`), parties: first named = `primary`; rent `cold_rent`, `operating_costs_prepayment` = Betriebskosten + Heizkosten prepayments (derived sum, both quoted), `effective_from = start_date`, `document_id` = this document |
| **Übergabeprotokoll** (`letter` / `handover_protocol`) — step 2 | `typing`; `link:tenancy` (by address + date near `start_date`) | `document_date`; meter readings stay in the extraction only |
| **Kautionsnachweis** (`letter` / `deposit_record`) — step 2 | `typing`; `deposit_received` action (amount, received_on) when a payment is stated; `link:tenancy` | `amount`, `received_on` |
| **Grundsteuerbescheid** (`bescheid` / `grundsteuerbescheid`) | `typing`; `link:property` (by address) | `document_date`, `tax_year` (first year the Bescheid applies to); extraction keeps Aktenzeichen, yearly amount and due dates for phase 3 |
| **Wirtschaftsplan**, **Hausgeldabrechnung**, **Zinsbescheinigung** | `typing`; `link:property` / `link:loan` | `document_date`, `tax_year`; extraction keeps the Hausgeld split (gesamt, umlagefähig, nicht umlagefähig, Erhaltungsrücklage; monthly), the settlement result, the year's interest: §8's matchers read them |
| Anything else | `typing` only (type, subtype, document_date, title; no tax_year, since no field set states one) | — |

Not in this slice: owner creation (two rows, typed once), BMF Arbeitshilfe inputs, Inventar in
the price, Sondertilgungen, Staffel steps beyond the first rent row (the extraction keeps
them; a later rent row is a user action).

### 6.3 Building the proposal (pure code, `rules/prefill.py`)

`build_proposal(kind, extraction, context) -> list[ProposalRecord]` where `context` holds the
workspace's existing properties (addresses), units, owners (names), loans (lender, number),
tenancies (unit, start) and tenants (names) as plain facts. It decides `create` vs `patch` vs
`unchanged` per record, routes milestone dates to `correction`, computes derived values and
writes `notes`. Fixture `prefill.yaml` (cases in §12). No I/O, no model.

**Known limit (Luis, 7 Nov 2026: noted, not to be built now).** A document finds its property
by address only (street, house number, postal code; else the workspace's only property), and a
lease then finds its unit by the label within that property. Two properties at the same
address (two flats in one building) cannot be told apart: `_match_property` takes the one with
the smallest id without asking. A lease for a room in the other flat then finds no unit, so its
tenancy cannot be placed (visible); a second Kaufvertrag in the same building is proposed as a
`patch` of the first property instead of a `create` (shown with the current values beside it,
but easy to miss). The fix, if it is ever needed: match on the flat number (WE) as well, and
ask when it is still ambiguous. Rooms rented separately are modelled as one unit per room under
the flat's property, labelled as the leases name them (MEA left empty: nothing calculates with
it). Since PR #56 the label need not be written exactly as in the lease (`Zimmer 1` is found in
`Zimmer Nr. 1 (WE 3, Zimmer 1)`), and an unmatched unit is picked in the review.

### 6.4 Review screen (`/documents/[id]`, new section "Vorschlag")

Mockup style, one card per record ("Immobilie", "Einheit WE 3", "Nebenkosten: Notar",
"Darlehen", "Mieter 1", "Mietverhältnis", "Anfangsmiete", "Typisierung"), each row: label,
proposed value (editable input of the form's type), source badge "S. 12" that opens the
original at that page (PDF viewer `#page=12`) and shows the quote on hover/tap, a
confidence dot (high / medium / low from `checks`), and for `patch` mode the current value
beside it with "übernehmen / behalten". Values with `value = None` show the note. A
record that creates shows every field of its create model, the ones the proposal lacks as
empty inputs to type by hand; a tenancy's unit and a Kaution's tenancy are picked from lists
(amended 7 Nov 2026, PR #56: the AI pre-fills what it can, the user can fill the rest). A
checkbox per record "nicht anlegen". Footer: "Übernehmen" (apply), "Verwerfen" (reject),
"Neu auslesen". Nothing is saved before "Übernehmen". Failures show the error code's German
text and a "Von Hand eingeben" link to the phase 1 forms.

### 6.5 Apply (`services/proposals.py: apply_proposal`)

One `CommandContext`, one `correlation_id`. Per record in dependency order (property →
unit → ownership → price split → costs → loan → tenants → tenancy (with `InitialRent`) →
typing → links): validate `values` with the record's `*Create` or `*Patch` model (422 with
`loc` prefixed by the record key, same whitelist as phase 1), call the phase 1 service
(`create_property`, `update_property`, `correct_notarized_on`, `correct_purchase_date`,
`add_acquisition_cost`, `update_acquisition_cost`, `record_price_split`, `set_ownership`,
`create_loan`, `update_loan`, `create_tenant`, `update_tenant`, `create_tenancy`,
`type_document` (source `ai` for the extraction already recorded; a `manual` version on top
only when Luis edited extraction fields), `link_document`), then write `decisions` and
`applied_entities`, status `applied`, and the `proposal.applied` event. A failure anywhere
rolls everything back (the proposal stays `ready`). `type_document` gains a `source`
argument (default `MANUAL`) so the AI path does not fork it.

## 7. Bank statements

### 7.1 Formats and assumptions (Sparkasse, dedicated rental account)

Checked 6 Nov 2026 against Luis's real exports from Sparkasse online banking's "Umsätze
exportieren" (kept outside the repo in `~/hausbuch-samples/`; only their structure was read;
step 3 builds the parser fixtures from anonymised copies):

- **camt.052 v08 XML, primary** (D33, D34): a ZIP holding one `BkToCstmrAcctRpt` report per
  booking day (namespace `urn:iso:std:iso:20022:tech:xsd:camt.052.001.08`, one `Rpt` per file,
  UTF-8). Every report has `Bal` `OPBD` and `CLBD`; they chain day to day from 0,00 € on the
  account's first booking. Every entry is `Sts` `BOOK` with one `NtryDtls/TxDtls`; it always
  carries `BookgDt`, `ValDt`, `Amt`, `CdtDbtInd`, `AcctSvcrRef` (distinct per entry),
  `BkTxCd` (`Domn` and `Prtry`), `AddtlNtryInf` (the Buchungstext) and `RltdPties`; usually
  `RmtInf/Ustrd` (21 of 22) and a counterparty `IBAN` and `BICFI` (19 of 22: bank fees and
  the Rechnungsabschluss have none); sometimes `EndToEndId` (5) and `MndtId` (2); never
  `NtryRef` or structured remittance. The parser reads these; entries not `BOOK` are skipped.
  The counterparty is `Cdtr` on a debit and `Dbtr` on a credit; the other side is the
  account's own IBAN (true on all 19 entries that name a counterparty).
- **CSV-CAMT V2** (fallback; Luis's export has 22 lines, as many as the XML has entries): `;`-separated, Windows-1252 (*amended in step 3:* the parser decodes as UTF-8 when the bytes are valid UTF-8, else Windows-1252; ISO-8859-15, which this line first named, differs from it only in a few symbols such as €), header row, dates `dd.mm.yy`,
  comma decimals (at most 12 integer digits, the range of a stored amount; a longer one makes the file `statement.unparsable`), 17 columns: `Auftragskonto; Buchungstag; Valutadatum; Buchungstext;
  Verwendungszweck; Glaeubiger ID; Mandatsreferenz; Kundenreferenz (End-to-End);
  Sammlerreferenz; Lastschrift Ursprungsbetrag; Auslagenersatz Ruecklastschrift;
  Beguenstigter/Zahlungspflichtiger; Kontonummer/IBAN; BIC (SWIFT-Code); Betrag; Waehrung;
  Info`. **No balances.** Lines with `Info = Umsatz vorgemerkt` are pending and skipped.
- **camt.053 XML** (other banks, or a single statement file): same parser and fields under
  `BkToCstmrStmt`/`Stmt`; a single `.xml` upload of either message works without a ZIP.
- **PDF Kontoauszug**: read by the extract job as `StatementExtraction` (alter/neuer
  Kontostand + lines); every amount is a model reading, so **the reconciliation is the
  guard**: an unreconciled PDF import goes to review as a whole (phase 2 input point 3).
- CSV-MT940 and the Sparkasse app's own export: not supported unless the CAMT export is
  unavailable (open question 3).

Format detection: media type + sniff (`PK\x03\x04` → ZIP of camt reports; `<Document` with
`BkToCstmrAcctRpt` or `BkToCstmrStmt` → camt; a header row starting with `Auftragskonto;` →
csv_camt; `application/pdf` → pdf). The uploaded file is a
document (type `statement`, `tax_year` = the year of the last line), so the vault, dedup on
bytes and retention apply unchanged; `POST /accounts/{id}/imports` wraps `upload_document`.

### 7.2 Dedup and reconciliation (pure rules, `rules/statements.py`)

- `line_hash(account_id, line, ordinal) -> sha256`: D22. Canonical tuple = `booking_date |
  value_date | amount | counterparty_iban (normalized) | counterparty_name (casefold,
  whitespace-collapsed) | purpose (same) | booking_text | ordinal`. The ordinal is the
  line's index among lines of the file with an identical tuple (0, 1, …), so two equal
  payments on one day both survive and a re-upload of the same file skips both.
- `likely_duplicates(new_line, existing_candidates) -> list[id]`: same account, amount,
  booking date, and same IBAN hash or normalized name, different hash **and different
  format** (two CSV lines that differ only by ordinal are not duplicates).
- `reconcile(anchor: Money, stored_before: Money, stored_through: Money, declared_closing:
  Money | None, file_opening: Money | None) -> ReconcileResult(status, computed_opening,
  computed_closing, difference)`, where the service passes, **after inserting the file's new
  lines**, `stored_before` = Σ stored, non-excluded transactions with `booking_date <
  file.first_booking_on` and `stored_through` = Σ with `booking_date ≤ file.last_booking_on`.
  `computed_opening = anchor + stored_before`, `computed_closing = anchor + stored_through`;
  `reconciled` iff `declared_closing` is given and equals `computed_closing` and (when the file
  states an opening) `file_opening == computed_opening`; `unreconciled` with the difference
  otherwise; `pending_balance` when no closing is declared yet. Summing the stored window
  rather than the file's new lines is what makes an overlapping export reconcile: lines the
  previous import already stored are skipped on insert but still counted. An anchor dated
  after a file's first line is `unreconciled` with `error_code = statement.before_anchor`.
- Import flow: parse → hash → insert new transactions (skip known hashes, count them) →
  record `possible_duplicate` rows → declared balances from the file or, for CSV, `pending`
  until `POST /imports/{id}/balances` → `reconcile` → status and event → when reconciled,
  enqueue `match_import`.

## 8. Matching, allocations, review queue

### 8.1 Order (cheapest first), `rules/matching.py` + `services/matching.py`

1. **Learned rules** (`matching_rule`, enabled, matcher hit on IBAN hash and patterns):
   apply the split method → confidence 1.0, auto-confirm.
2. **Deterministic matchers** (pure, fixture `matchers.yaml`), confidence 1.0 when exact:
   - owner transfer: counterparty IBAN hash ∈ owner accounts → `owner_transfer_in/out`,
     `pass_through`;
   - rent: counterparty IBAN hash = a tenant's, amount = `cold_rent + operating_costs_prepayment`
     in force on the service period → two allocations (`rent_cold`, `operating_costs_prepayment`),
     `recurring = true`, period by D27; amount off → review with the difference as the
     question ("Mieter hat 1.380 € statt 1.400 € überwiesen. Mietminderung oder Teilzahlung?");
   - loan annuity: counterparty IBAN hash = a loan's account or lender name + amount =
     `monthly_payment` → `loan_interest` / `loan_principal` from the schedule entry due in
     the entry whose due date is nearest the booking date within ±10 days (D39; `rules.loan_schedule`), `recurring = true`;
   - loan interest only (D46): for a loan with `interest_only_before_first_payment`, a debit after `disbursed_on` and before `first_payment_on` whose purpose states `Zinsen <amount>` equal to it and no `Tilgung` above zero → one `loan_interest` of the whole amount, period = the stated `Für … - …` range, else the booking month, `recurring = true`. A line from a loan's own account is matched only against that loan; the lender name is a fallback when the account is no loan's (D46);
   - Hausgeld: a rule with `by_wirtschaftsplan` (created from the first review answer) splits
     by the current Wirtschaftsplan extraction into `hausgeld_umlagefaehig`,
     `hausgeld_verwaltung`, `hausgeld_ruecklage` (the last `undecided` until phase 3 reads
     the Hausgeldabrechnung).
3. **Document match**: an invoice/Bescheid extraction with `amount_total` = |amount| ± 0.01,
   `document_date` within ±10 days, and the IBAN or the reference number found in the
   purpose → category from the document kind (Grundsteuer → `grundsteuer`, Nebenkosten
   invoice → `acquisition_cost` with the cost row's `paid_on` set on confirm; a financing
   invoice (D35, decided by the same test `rules.prefill` uses, made public for step 4) →
   `loan_fees` on the property's loan, no cost row), the document as Beleg; confidence 0.95 (auto-confirm per threshold).
4. **LLM categorization** (§5.3), confidence by the model's `high/medium/low` mapped to
   0.9/0.6/0.3; auto-confirm only when `auto_accept_llm` and ≥ the category threshold
   (`thresholds.yaml`: defaults 0.9 for recurring cost categories, never for `other_*`,
   `repairs` or anything `capitalized`).

Every confirmation from review may become a rule ("Künftig immer so zuordnen" checkbox,
default on when the counterparty has an IBAN (not offered when the counterparty is an owner, tenant or loan account and the answer is a single category, D38), **default off when the confirmed category never auto-confirms** (`repairs`, `renovation`, anything capitalized: one confirmation to a tradesman must not silently become a standing auto-confirm; review F6, 6 Nov 2026)), with the split method inferred from the
confirmed allocations (one category → `single_category`; the matcher's split →
`by_rent_schedule` etc.). A rule hit on a later line is never asked again (`repeat_answer`
fixture).

### 8.2 Categories (`AllocationCategory`) and default tax treatment

`rent_cold` (income), `operating_costs_prepayment` (income), `operating_costs_settlement`
(income; negative = refund), `deposit_in` / `deposit_out` (pass_through), `owner_transfer_in`
/ `owner_transfer_out` (pass_through), `loan_interest` (deductible), `loan_principal`
(not_deductible), `loan_fees` (deductible), `loan_disbursement` (pass_through; D44),
`hausgeld_umlagefaehig` (deductible),
`hausgeld_verwaltung` (deductible), `hausgeld_ruecklage` (undecided), `grundsteuer`
(deductible), `insurance` (deductible), `repairs` (deductible; Erhaltungsaufwand),
`renovation` (undecided; 15 % rule in phase 3), `purchase_price` (capitalized; D44),
`acquisition_cost` (capitalized),
`professional_fees` (deductible), `bank_fees` (deductible), `other_income` (income),
`other_expense` (undecided), `uncategorized` (undecided). Fixture `categories.yaml` lists the
mapping; phase 3 adds the Anlage V line per year.

### 8.3 Review queue (`GET /inbox`)

One list, newest first, each item with a kind, a one-line reasoning and one question: pending
proposals, `failed` / `blocked_tenant_data` reads, unreconciled or pending-balance imports,
possible duplicates, transactions with a `proposed` allocation set or none. Actions call the
item's own endpoint (`PUT /transactions/{id}/allocations` with an `AllocationSet` from the
user, `POST /possible-duplicates/{id}` keep-both/merge, `POST /imports/{id}/balances`, …).
Confirming writes `allocation` rows (`confirmed`, method `user` or the proposer's), the
`transaction.allocated` event, and optionally a rule.

## 9. PWA capture

`/capture` (mobile): `<input type="file" accept="image/jpeg,image/png" capture="environment"
multiple>`, one upload per photo (`source = photo`, `ai` on), a list of the created
documents with their proposal status. The manifest's `start_url` stays `/`; a home-screen
shortcut "Beleg fotografieren" points at `/capture`. No service worker (out of scope).

## 10. Sign-in and passkey events (D28)

Entity type `user`, actor `user`, no `effective_date`: `user.signed_in` (passkey id),
`user.signed_out`, `user.sessions_revoked` (count), `user.passkey_enrolled` (passkey id,
label), `user.passkey_renamed` (passkey id), `user.passkey_removed` (passkey id),
`user.invited` (invite id, invited user id), `user.enrolled` (invite id). Sign-in appends the
event inside the workspace transaction after session resolution (the workspace is known by
then). The server log keeps what it logs today. Failed sign-ins stay in the log (no
workspace, no entity).

## 11. Privacy, terms and keys

### 11.1 What Anthropic's own pages say (all fetched 6 Nov 2026)

- **Commercial Terms of Service** (effective 17 Jun 2025, [anthropic.com/legal/commercial-terms](https://www.anthropic.com/legal/commercial-terms)):
  apply to API keys; "Anthropic may not train models on Customer Content from Services";
  the customer owns outputs; EEA customers contract with Anthropic Ireland, Limited; the
  DPA is incorporated by reference.
- **Data Processing Addendum** (effective 24 Feb 2025, [anthropic.com/legal/data-processing-addendum](https://www.anthropic.com/legal/data-processing-addendum)):
  customer is controller, Anthropic processor; EU transfers under the Standard Contractual
  Clauses (Module Two); subprocessor list at [anthropic.com/subprocessors](https://www.anthropic.com/subprocessors)
  with notice and a 15-day objection window; deletion within 30 days of termination. No
  statement of processing location.
- **Retention** (commercial policy page dated 1 Jul 2026, [privacy.claude.com](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-personal-data)):
  API inputs and outputs are deleted "within 30 days of receipt or generation"; content
  flagged by trust-and-safety systems may be kept up to 2 years. The API data-retention
  docs ([platform.claude.com](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention))
  add: retained data "is never used for model training without your express permission";
  **zero data retention (ZDR)** exists but only by arrangement with sales, per organization;
  Opus 5.5, Sonnet 5.5 and Haiku 5.5 are not "Covered Models", so PDF support, citations
  and structured outputs with them would all be ZDR-eligible.
- **Data residency** ([platform.claude.com](https://platform.claude.com/docs/en/manage-claude/data-residency)):
  `inference_geo` offers **only `global` and `us`**; workspace geo (storage at rest) is
  **`us` only**. There is no EU option on the first-party API. Third-party guides mention
  Claude on AWS Bedrock in Frankfurt or Google Vertex EU regions as an EU route (then AWS or
  Google is the processor); **not verified against AWS/Google pages here**, and the
  structured-outputs and citations features are documented as available on those platforms
  with base64 PDFs only.

### 11.2 What this means, and what Luis decides

ARCHITECTURE's security section asks for "no training on inputs and EU data processing where
offered". No training: met by the Commercial Terms. EU processing: **not offered** on the
first-party API; data is processed in the US (or wherever `global` routes), transferred
under the SCCs of the DPA, and kept up to 30 days. For property and loan documents (no
tenant data) the orchestrator and Luis already accepted this for pre-fill. For
**Mietvertrag, Übergabeprotokoll, Kautionsnachweis and tenant letters** Luis is the
controller of the tenant's data (GDPR) and must decide, before `ai_tenant_documents` is
switched on:

1. **Accept**: use the first-party API under the Commercial Terms + DPA (SCCs, 30-day
   deletion, no training), set `inference_geo = us` (fixed location, 1.1× price) or
   `global`, and add Anthropic to the processor list in the app's privacy note ("what we
   store and why", ARCHITECTURE Privacy). Cheapest; one Console account on the Commercial
   Terms (Anthropic Ireland for an EEA customer).
2. **EU route**: Claude through AWS Bedrock `eu-central-1` or Google Vertex EU, after
   checking on the provider's pages that Sonnet 5.5 is offered there with PDF input; adds a
   second cloud vendor and its DPA; the `BankSource`-style `ModelClient` interface in
   `hausbuch/ai/client.py` keeps it swappable (one implementation in this phase).
3. **Decline**: tenant documents stay typed by hand (phase 1 forms), everything else is
   pre-filled. The setting stays `false`.

**Decided (Luis, 6 Nov 2026): option 1, accept.** Step 2 still ships with the setting at `false`; Luis switches it on once Anthropic is named as a processor in the privacy note to the tenant. The gate and the `contains_tenant_data` flag stay, so the choice remains reversible. **Privacy note given (Luis, 7 Nov 2026):** the tenants have been told about the AI processing of their data, so the precondition is met. The setting is still `false` in production because nothing can switch it yet: the settings route and the settings UI with this section's text are part of step 2 (only the column, the schemas and the `workspace.settings_changed` event exist). It is switched on through that UI once step 2 ships, never by a direct database write (the change needs its event). Also to record in
`docs/ARCHITECTURE.md` (§15): the subprocessor notice address (the Console account's email).

### 11.3 Keys and deploy (`deploy/scaleway.md`, new step 13)

Create a Console **workspace** named `hausbuch` with a monthly spend limit of $25 and one
API key scoped to it, plus a second workspace `hausbuch-evals` ($10 limit) whose key stays on
Luis's computer for eval runs (Luis, 6 Nov 2026: two keys, so either can be revoked alone); set `default_inference_geo` per the decision above. Store the key in
Secret Manager as `hausbuch-anthropic-api-key`, paste it into the API container's secret
environment variables as `HAUSBUCH_ANTHROPIC_API_KEY` (containers cannot read Secret Manager
directly, runbook finding), set `HAUSBUCH_AI_ENABLED=true`, redeploy, and check
`GET /healthz` reports the worker. Rotation: new key in the Console → new secret version →
container variable → redeploy → delete the old key. The key is never in the database, the
repository, a log or the frontend; `Settings.repr` hides it like the other secrets. Migration
`0005` runs from Luis's computer before the step 1 image, as `0004` did.

## 12. Rule contracts and fixtures (`tests/fixtures/phase2/`)

Pure functions in `hausbuch.rules.*`, no I/O, no model calls; errors carry a `code`.

| Function | Fixture | Cases |
| --- | --- | --- |
| `prefill.build_proposal(kind, extraction, context) -> list[ProposalRecord]` | `prefill.yaml` | `kaufvertrag_new_property` (create property + unit + split + ownership), `kaufvertrag_existing_property` (patch by address; price equal → `unchanged`; notarized_on differs → `correction`), `kaufvertrag_uebergang_by_payment` (null date + note), `kaufvertrag_no_land_share` (no split record), `notar_invoice_new_cost`, `grunderwerbsteuer_bescheid_patch_existing_cost_without_beleg`, `darlehen_rate_derived_from_tilgungssatz`, `darlehen_patch_by_contract_number`, `mietvertrag_two_tenants_one_existing`, `mietvertrag_unit_by_address`, `mietvertrag_prepayment_sum`, `grundsteuer_typing_and_link`, `unknown_kind_typing_only`; added 7 Nov 2026 (D35, D36): `notar_invoice_grundschuld_is_financing` (classifier wrong, one loan linked), `notar_invoice_grundschuld_by_title_only` (no subtype, no loan), `notar_invoice_grundschuld_already_entered_as_nebenkosten` (no cost record, note to remove the row, two loans: none linked), `notar_invoice_kaufvertrag_mentions_financing_power_stays_nebenkosten` (negative; tax year from `due_on`); each also run with `context` lists shuffled |
| `extraction_parse.parse_lines(text_blocks_with_citations, kind) -> dict[str, ExtractedValue]` | `extraction_parse.yaml` | German amount/date/percent/IBAN normalization, `unbekannt`, duplicate keys (last wins), unknown keys ignored, value not in quote → `medium`, no citation → `low`, page offset for split PDFs |
| `statements.line_hash(...)` | `line_hash.yaml` | bank reference wins; `NOTPROVIDED` ignored; two identical lines → ordinals 0 and 1; same line re-imported → same hash; whitespace/case in purpose irrelevant |
| `statements.likely_duplicates(...)` | `duplicates.yaml` | CSV vs camt same payment → duplicate; two CSV lines same day → not; different amount → not; resolution `merged` excludes the later-imported line, `kept_both` keeps both counting |
| `statements.reconcile(...)` | `reconcile.yaml` | CSV with declared closing ok / off by 0.01; camt with opening mismatch; no declared closing → `pending_balance`; overlapping export (second file repeats three stored lines: stored window sums, reconciles); after a `merged` duplicate the excluded line is out of both sums; file before the anchor |
| `bank.csv_camt.parse` / `bank.camt.parse` | `csv_camt.yaml`, `camt.yaml` (built from Luis's real exports, anonymized by replacing names/IBANs) | header variants, pending lines skipped, comma decimals, `dd.mm.yy` vs `dd.mm.yyyy`, credit/debit sign; camt: both roots, ZIP member rules and limits (D34), per-report sum and day-to-day chain breaks, an entity declaration refused |
| `periods.service_period(purpose, booking_date, recurring)` | `service_period.yaml` | `Miete 01/2027`, `Miete Januar 2027`, `01.2027`, `2027-01`, `Hausgeld 01/27-03/27`, `Nachzahlung NK 2026` (year), none + recurring → booking month, none + not recurring → null |
| `matching.match_owner_transfer / match_rent / match_loan / match_document` | `matchers.yaml` | rent exact, rent short by 20 € (review with question), rent for two months in one transfer (period range), annuity split at the fixed-rate switch month, owner top-up, Grundsteuer quarterly vs Bescheid, invoice ±0.01 within window, invoice outside window |
| `matching.apply_rule(rule, transaction, context)` | `rules.yaml` | single category; `by_loan_schedule` after a rate change (new amounts, same rule); `by_wirtschaftsplan` with a new plan; disabled rule ignored; rule stores no amount (schema test) |
| `allocations.check_sum(transaction_amount, drafts)` | `allocation_sum.yaml` | exact, off by 0.01 → `allocation.sum_mismatch`, empty → `allocation.empty`, sign mismatch |
| `categories.tax_treatment(category)` | `categories.yaml` | every category once |
| `review.route(item) -> Routing` | `review_routing.yaml` | threshold table per category; `auto_accept_llm` false → review; deterministic 1.0 → auto; unreconciled import → whole import to review, no lines matched |
| `thresholds` (data) | `thresholds.yaml` | one row per category |

Error codes added: `allocation.sum_mismatch`, `allocation.empty`, `statement.unreconciled`, `statement.opening_mismatch` (wins over `unreconciled`; both differences are reported), `statement.before_anchor`,
`statement.unknown_format`, `proposal.already_decided`, `proposal.not_ready`, `ai.disabled`,
`ai.blocked_tenant_data`, `document.adobe_form` (D43), `ai.unparsable`, `ai.refused`, `ai.too_many_pages`,
`ai.unsupported_media_type`.

Added by step 1 (PR #26): `ai.api_error` (a 4xx from the API other than 429, not retried),
`ai.unavailable` (429, 5xx or network after the last retry), `ai.unreadable` (a PDF that cannot
be opened: damaged, or encrypted with a password), `ai.internal_error` (not a model error: a
bug or the database, after the retries ran out).

Added by step 3 (PR #25): `statement.unparsable` (the file cannot be read as its format),
`statement.archive_rejected` (a ZIP breaking D34's member rules, or a corrupt member),
`statement.entity_declared` (an XML DOCTYPE), `statement.report_unbalanced` (a daily report
whose `OPBD + Σ Ntry ≠ CLBD`), `statement.chain_break` (a report's `OPBD` is not the previous
`CLBD`), `statement.account_mismatch` (the file names another IBAN), `statement.unsupported_currency`,
`statement.empty`, `statement.owner_account` (an import onto an owners' private account),
`statement.balance_from_file` (declaring a balance for a file that states its own),
`statement.duplicate_accounts_differ`, `statement.duplicate_excludes_both`.

Added 7 Nov 2026 (D37): `document.extraction_kind_mismatch` (a non-empty hand-entry field the AI reading's kind has no place for), `document.extraction_stale` (hand-entry fields shown from a version that is no longer current).

Added 7 Nov 2026 (step 4, service and API level): `allocation.transaction_mismatch` (the set names another line), `allocation.import_not_reconciled`, `allocation.line_not_open`, `allocation.scope_mismatch` (a unit, tenancy or loan of another property, or without its property), `allocation.excluded_transaction` (a line merged away as a duplicate), `rule.not_learnable` (a rule was asked for where none can be learned, D38), `rule.invalid_pattern` (a pattern that does not compile or is longer than 200 characters).

`report_unbalanced` and `chain_break` are faults of the file and stay on the import; no anchor
or declaration clears them (open question 10).

## 13. Done criterion as fixtures and evals

- **`month_statement.yaml`** (synthetic, modelled on the real account's shape: rent in, Hausgeld
  out, annuity out, Grundsteuer quarterly, insurance, one owner top-up, one repair invoice,
  one bank fee, one unknown line; 12 lines): run through rules + matchers + document match
  with the rules learned from a first-month answer set → **≥ 80 % of lines auto-matched**
  (10 of 12), **every allocation set sums** (`allocation_sum`), the import **reconciles**
  against its declared closing. The first-month set is the same lines with no rules →
  expected routing lists what lands in review.
- **`repeat_answer.yaml`**: a review answer creates a rule; the next month's identical
  counterparty is matched by that rule with confidence 1 and nothing is asked.
- **Pre-fill accuracy** (eval, not a fixture): per document kind, the share of fields with
  decision `accepted` over fields proposed, from `proposal.decisions`; reported by `hausbuch
  evals report` from the `proposal.applied` events (D41: per field the `FieldDecision`, no
  values; this line listed confidence, checks and values until D41 dropped them). Target for the real set: ≥ 90 % accepted on
  amounts and dates; a drop after a prompt or model change blocks the change.
- **Recorded evals** (`tests/evals/`): synthetic PDFs (a mock Kaufvertrag, Darlehensvertrag,
  Mietvertrag, invoice, Bescheid, Wirtschaftsplan, PDF Kontoauszug; generated by a script in
  `tests/evals/make_docs.py`) with recorded API responses (the SDK's request/response
  captured once per prompt version) and expected extractions; CI runs the parser and
  `build_proposal` on them on every push. `tests/evals/record.py` re-records them (needs the key; run from
  Luis's computer, D29). `hausbuch evals run` compares live readings of Luis's real documents
  with his golden files (D41).

## 14. Implementation steps

Pre-fill first; each step shippable; review per `.claude/WORKING_AGREEMENT.md` ("When to
review": money, persisted state, migrations, RLS, events, fixtures, silent failures).

| Step | Delivers | Migration | Done when | Size / cost | Review |
| --- | --- | --- | --- | --- | --- |
| 0 **Spec PR** | this doc final; schemas §4; migrations 0005–0007 written and tested against Postgres 16; fixtures §12–13 (rule tests skipped); deps; `tables.py` mirror; ARCHITECTURE edits §15 | all three written, none deployed | schema tests green (29 tables + exclusion), fixtures validate, `uv run pytest` green | M: 1 coder; API cost 0 | **Yes** (migrations, RLS, grants, event CHECK, fixtures): FOCUS procrastinate grants cannot bypass RLS; `ai_call` scrub trigger; no amount field on rules |
| 1 **Pre-fill for property, Nebenkosten, loan, WEG and Bescheid documents** | Procrastinate worker in the lifespan; `ai/client.py` (Anthropic SDK, prompts, provenance, gate); `document_read` task; `rules/prefill.py`, `rules/extraction_parse.py`; proposals service + routes; review screen; settings; deploy step 13; auth events (§10, small) | **0005** | Luis uploads his Kaufvertrag, four Nebenkosten documents, Darlehensvertrag, Grundsteuerbescheid and Wirtschaftsplan and enters the flat, unit, split, costs and loan by verifying values only; every proposed value shows page + quote; `prefill.yaml` and `extraction_parse.yaml` pass; recorded evals pass in CI; History shows purchase → loan correctly | L: 3 coder chunks in parallel (worker + client + task; rules + proposals + API; frontend) ≈ 3 sessions; API ≈ $2 for the real set, ≈ $5 for prompt iteration | **Yes**: FOCUS apply is one transaction through existing services only; no model output reaches a domain table; tenant gate cannot be bypassed by the classifier result; key never logged |
| 2 **Mietvertrag, Übergabeprotokoll, Kaution** | (lease and handover pre-fill and apply shipped with step 1; the rest is D42) lease/handover/deposit extraction kinds; tenant + tenancy + rent proposals; `ai_tenant_documents` setting UI with the §11.2 text; erasure scrub of proposals and `ai_call` | — | after Luis's §11.2 decision: his Mietvertrag enters tenant(s), tenancy and first rent by verification; `tenant.erased` nulls the proposal and call rows (`tests/db`); gate tests | M: 1 coder; API < $1 | **Yes** (personal data, erasure): FOCUS D5 in the new events; scrub completeness |
| 3 **Accounts and statements** | `account`, `BankSource` + camt (single XML or ZIP) + csv_camt (+ PDF via extraction), import, dedup, duplicates, balance anchors, reconciliation, transactions list, inbox items for imports | **0006** | Luis's real exports (one per format) import; a re-upload adds zero lines; the month reconciles against the online-banking balance; `line_hash`, `duplicates`, `reconcile`, parser fixtures pass | M: 1–2 coders (parsers + rules; service + UI); API ≈ $0.05 per PDF statement | **Yes** (money, persisted state): FOCUS hash stability; no amount parsed through float; reconciliation blocks matching; `transaction.raw` never holds a plaintext IBAN (only the service enforces it); `resolve_duplicate` requires both lines on one account and refuses mutual exclusion (both copies leaving the balance); ZIP limits and flat names enforced before any XML is parsed, entity resolution off |
| 4 **Matching, allocations, rules, inbox** | matchers, document match, LLM categorize + explain, thresholds, allocation writes with sum check, rules, `match_import`, `rematch_on_evidence`, inbox UI, allocation editor | **0007** | `matchers.yaml`, `rules.yaml`, `allocation_sum.yaml`, `review_routing.yaml`, `month_statement.yaml`, `repeat_answer.yaml` pass; first real month fully confirmed through the inbox; second real month ≥ 80 % auto-matched, 0 sum errors | L: 3 coder chunks (rules; services + jobs + LLM; inbox UI) ≈ 3 sessions; API ≈ $0.20/month | **Yes** (money, events, silent mis-split): FOCUS sum invariant on every path; rules never store amounts; auto-confirm only above threshold |
| 5 **PWA capture, evals CLI, sweeper** | `/capture`; `hausbuch evals run/report` (D41); `scrub_proposals` periodic task (D41); the bucket lifecycle rule stays with the retention job (D41) | — | a photo from the phone becomes a typed document with a proposal; `evals report` prints per-kind accuracy from the real set | S: 1 coder | No (CI covers it), except the sweeper (persisted deletes) → small review |

Phase 2 is done after step 4's second-month measurement; step 5 can ship any time after 1.

## 15. ARCHITECTURE.md edits this spec requires

Conflicts, stated plainly, and the exact edits (made in the spec PR):

1. **Delivery phases, phase 2 row**, "Delivers": prepend "AI pre-fill of property, Nebenkosten,
   loan, tenant, tenancy and rent records from uploaded documents, each value with page +
   quote (step 1, so the real data is entered by verifying, not typing);"; "Done when":
   prepend "the real flat, loan and tenancy were entered through pre-fill;". Phase 1 row:
   add "(closed 6 Nov 2026; the real data enters through phase 2's pre-fill)".
2. **AI layer, Guardrails, first bullet** ("Structured outputs validated against JSON
   Schema…"): add "Extraction of document fields uses citations instead (page + quoted text
   per value) because the API does not combine citations with JSON-schema output; the lines
   are parsed and validated against the Pydantic field types in code, and a value that is
   not recoverable from its quote is marked low-confidence (phase 2, D15)."
3. **AI layer, Extract fields row**: output becomes "per-type JSON with page + quote per
   value"; add a row "Pre-fill proposal: extraction + existing records → records to create or
   patch; tier: none (code)".
4. **Security, first bullet** ("EU data processing where offered"): add "Checked 6 Nov 2026:
   the Claude API offers US or global inference only, no EU option; data is processed under
   the DPA's SCCs and deleted within 30 days; tenant documents are sent only after the
   controller's explicit decision (phase 2 §11)".
5. **Stack table, AI row**: "Claude API (Haiku 5.5 classify/explain, Sonnet 5.5 extract and
   match, Opus 5.5 for ambiguous matches), citations for extraction, structured outputs for
   classification and matching; one `ModelClient` interface so an EU-hosted route (Bedrock,
   Vertex) can replace it".
6. **Delivery phases, "Input for the phase 2 spec", point 3**: add "Reconciliation uses an
   opening anchor per account and a closing balance declared per import: read from the file
   for camt XML (Sparkasse's camt.052 export states both balances) and PDF statements, typed
   for the CSV export, which carries none. D21, D33."
7. **Decisions table, new rows (6 Nov 2026):**
   - *Pre-fill in phase 2* — "Master data is entered by AI pre-fill from documents, verified
     per value against page + quote, applied through the existing commands only; phase 1
     closed" — what it changes: phase 2 step 1; `proposal` and `ai_call` tables; D13–D15.
   - *Tenant documents and the model* — "Not sent until the landlord enables
     `ai_tenant_documents` after deciding between first-party US processing under the DPA,
     an EU-hosted route, or manual entry" — what it changes: a workspace setting and a
     classifier gate; §11.2.
   - *Extraction via citations* — "Field extraction uses citations (page + quote), parsed and
     validated in code; structured outputs stay for classify and match" — what it changes:
     guardrail wording.
   - *Statement balances* — "Reconciliation on balance anchors (D21)" — what it changes:
     `account.opening_balance`, `statement_import.declared_*`.
   - *Sign-in events* — "On entity `user` in the Event table; no IP or user agent" (closes
     the 5 Nov deferral).
   - *Eval set* — "Recorded synthetic evals in CI; real documents evaluated from Luis's
     computer" — refines "an eval set of real (anonymized) statements … in CI".
8. **System architecture paragraph on workers**: unchanged in substance; add "(phase 2:
   Procrastinate, schema applied by migration 0005, tables not RLS'd, D20)".

No principle changes. Principle 1 is touched only by D15's derived values (building share,
monthly payment from Tilgungssatz, prepayment sum): all computed by code from cited inputs and
shown as derived, never produced by the model.

## 16. Open questions

1. ~~**Luis: tenant documents**~~ **Decided 6 Nov 2026: option 1** (first-party API, US
   processing under the DPA; §11.2). The privacy note was given on 7 Nov 2026; the setting is switched
   on through step 2's settings UI.
2. ~~**Luis: `inference_geo`**~~ **Decided 6 Nov 2026: `us`** for all documents.
3. ~~**Luis: Sparkasse exports**~~ **Decided 6 Nov 2026: camt XML** (option 1). Luis
   confirmed his online banking offers a camt export. Still needed before step 3: one real
   camt file, from which the parser fixture is built and the message (camt.052 or camt.053),
   version (02 or 08) and presence of `OPBD`/`CLBD` balances are confirmed; if it carries no
   balances, the PDF statement supplies them (§7.1). Also check how far back the export reaches
   (one vendor claims 90 days); older months then come in as PDF statements.
   **Checked 6 Nov 2026 against Luis's real export** (`rental_account_bank_export-camt52v8.CSV`,
   kept outside the repo; only its structure was read): despite "camt52v8" in the name it is the
   **CSV-CAMT layout**, not XML: `;`-separated, the 17 columns of §7.1 exactly, dates `dd.mm.yy`,
   umlauts only (cp1252 and ISO-8859-15 agree), newest line first, every line `Umsatz gebucht`,
   **no balances**. 22 lines from 1 Apr 2026 (the account's first booking, so the export reached
   the whole history) to 6 Nov 2026.
   **Then checked the XML export** (`rental_account_bank_export-camt52v8.zip`, same place; Luis
   downloaded it twice, and the `.zip` and `.ZIP` copies differ only in each report's creation
   timestamp `CreDtTm`): a ZIP of 17 **camt.052 v08** reports, one per booking day from 1 Apr to
   6 Nov 2026, 22 entries in all, as many as the CSV has lines. Every report states `OPBD` and `CLBD`;
   in all 17, opening + entries = closing, and each opening equals the previous closing, from
   0,00 € on the first day. **Settled: the XML is the primary format, its balances are read from
   the file and nothing is typed** (D33, D34); the export reaches the account's whole history,
   so the 90-day question does not arise yet. The CSV stays a fallback.
4. ~~**Luis: Anthropic account**~~ **Decided 6 Nov 2026:** workspace `hausbuch` with a $25
   monthly limit (key in Scaleway only) and workspace `hausbuch-evals` with a $10 limit (key
   on Luis's computer); subprocessor notices go to the Console account's email (§11.3).
5. ~~**Kaufvertrag Übergang clause**~~ **Decided 6 Nov 2026:** the proposal leaves
   `purchase_date` empty with the clause quoted and Luis types it during pre-fill; step 4
   adds a cross-check that warns when the matched Kaufpreis (2nd Rate) payment date differs
   from the recorded date. The fix is a correction event, as today.
6. **Review queue thresholds.** `thresholds.yaml` defaults (0.9 recurring, never for
   capitalized/other) are guesses; revisit after the first real month.
7. **Hausgeld `ruecklage` treatment** stays `undecided` until phase 3's Hausgeldabrechnung
   rule; phase 2 only stores the split.
8. **Photos of multi-page documents**: settled 7 Nov 2026 (Luis): the capture page combines
   several photos into one PDF in the browser (D30 amended).
9. **Retention sweeper** (phase 1 known gap) now also owes the proposal / `ai_call` scrub
   (D19); it may land in step 5 or stay a separate small step.
10. **Step 3 review, for a later spec decision (6 Nov 2026).** (a) The
    `statement.unreconciled` event carries the differences but no error code, so History
    cannot say *why* a file with a chain break is unreconciled. (b) A file fault (chain break,
    unbalanced report) has no way out short of deleting the import, which nothing offers yet.
    (c) `lines_skipped` counts both lines already stored and identical lines twice in one file.
    (d) Possible cross-format duplicates are paired on a counterparty IBAN or name; bank fees and
    the Rechnungsabschluss name none, so their CSV and camt copies are not paired (suggested:
    also pair on date, amount and Buchungstext when neither side names a counterparty).
    None blocks Luis's import: his camt ZIP reconciles and nothing needs the CSV.
11. **Lease re-read over an existing tenancy (PR #51 review, 7 Nov 2026, not decided).** The pre-fill
    context carries a tenancy's deposit amount but not its deposit form, and the tenancy patch
    record has no `current` values. So re-reading a lease whose clause names no form, for a tenancy
    whose form was set by hand to a Bürgschaft, proposes `cash_account` (low confidence, ticked by
    default) with nothing stored shown beside it. Suggested: add `deposit_form` to `ExistingTenancy`,
    show `current` on the patch, and derive `cash_account` in a patch only when the stored form is
    `none`; a fixture-shape change, so for the next spec PR. Also open: whether a
    Mietkautionsversicherung maps to `surety`. Not reached by Luis's leases today (each creates its tenancy).
