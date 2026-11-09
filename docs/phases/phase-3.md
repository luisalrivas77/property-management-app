# Phase 3 spec: Steuerjahr — rule set 2026, Anlage V draft, checklist, 15 % tracker, exports

Nov 9, 2026 · spec draft (architect), checked by the orchestrator; Luis's questions in §17 all answered 9 Nov 2026 (Q1, Q2, Q3, Q6, Q7, Q8), the orchestrator's settled the same day; step 0 (schemas, migration, rule sets, fixtures) written 9 Nov 2026 (commits `e16002b`, `d139528`, `b4390ad`, review fixes `a1613cb`), merged with this spec the same day in PR #79 and deployed (migration 0013 applied to production; its tables stay empty until step 2). Steps 1 (rules, draft, Steuern screen) and 2 (inputs, certificate pre-fill, checklist entries) were built in parallel the same day, each reviewed once, and merged and deployed together in PR #81 (no migration; images `c19ed7bce5d9`, API and web, health check 200, the new route answers 401 without a login). Step 3 (exports: the ELSTER field sheet PDF, the yearly ZIP archive, the export job and history, the §23 date on screen) was built the same day, reviewed once (two minor findings fixed in `8d51fbb`), and merged and deployed in PR #84 (no migration; images `05c1239bf7d1`); its archive limit is 100 MiB, not 1 GiB (D77). Step 4 (closing against Luis's hand calculation) is not started. Writing the fixtures corrected several worked examples below; the biggest is that a cost the 15 % test moves into the AfA basis enters it **gross** (D73, confirmed in review 9 Nov 2026). The review round of the same day added D74–D76 (a `herstellung` election counts toward the 15 %, the window ends on the same calendar day three years later, and the date tested is when the work was done) and put the flat's unit row on Z13.
No implementation. Every tax rule below was checked against the law, the
official Anlage V 2025 form or a BFH decision on 9 Nov 2026 (§3 lists the sources); where a
point could not be verified from an official source it is an open question, not a decision.

Phase 3 is pure code in the sense of principle 1: no model computes, rounds or allocates a
euro. The one place a model-read number enters a tax line (the WEG's spending from the
Erhaltungsrücklage, read from the Hausgeldabrechnung) goes through the phase 2 pre-fill flow,
so Luis confirms it against its page and quote before the rule sums it (D57).

This is the spec PR ARCHITECTURE.md requires before implementation. It fixes the contract the
implementation steps code against:

| Artifact | Where | Status |
| --- | --- | --- |
| Pydantic schemas | `backend/src/hausbuch/schemas/tax.py` (new: `RuleSet`, `FormMapping`, `DraftFacts`, `AnlageVDraft`, `TaxInput*`, `TaxExport`); additions to `enums.py`, `checklist.py`, `events.py`, `proposal.py`, `tenancy.py`, `property.py` (§7) | Written in step 0 (merged in PR #79, `e16002b`) |
| Migration | `0013_tax_year` (§6): `tenancy.rent_due_day`, `tenancy.operating_costs_terms`, `property.living_area_m2`, tables `tax_input` and `tax_export` | Written in step 0 (`e16002b`), tested up and down against Postgres 16; applied to production 9 Nov 2026 with the spec PR's deploy (PR #79, earlier than planned, because Luis asked to merge and deploy step 0); tables empty until step 2 |
| Rule sets (data) | `backend/src/hausbuch/rules/tax/rulesets/2025.yaml` (form published, verified), `2026.yaml` (form **provisional**, §4.3), loaded by `rules/tax/registry.py`, pinned by `tests/rules/test_tax_rulesets.py` | Written in step 0 (merged in PR #79, `d139528`) |
| Rule fixtures | `backend/tests/fixtures/phase3/*.yaml`, 12 files (§13), loaded by `tests/fixture_models_phase3.py` and checked by `tests/test_fixtures_valid.py` | Written in step 0 (merged in PR #79, `b4390ad`); the form mapping is pinned by the Python test `test_tax_rulesets.py` instead of a `form_mapping.yaml` fixture; no skipped rule-test stubs were added (the rule tests arrive with the rules in steps 1–2) |
| Eval cases | `backend/tests/evals/recordings/zinsbescheinigung.json`, `hausgeldabrechnung.json` (synthetic, recorded from Luis's computer, D66) | Recorded 9 Nov 2026 by the orchestrator with the evals key (`6716ba2`); they replay in CI |
| Rule tests | `backend/tests/rules/test_tax_*.py` | `test_tax_rulesets.py` written in step 0 (`d139528`); the rule tests in `tests/rules/tax/` came with step 1: every case of the eleven rule fixtures passes, plus review tests in `test_tax_rule_review_fixes.py` |
| Step 1 (rule set, draft, Steuern screen) | `rules/tax/`, `services/tax.py`, `api/routes/tax.py`, `/tax/[year]` | Merged and deployed 9 Nov 2026 (PR #81): the rules (`feat/p3-rules`, reviewed: F1 a `repairs` line with a stray `herstellung` input now moves when the limit is crossed, F2 the crossing year always warns, F3 a spread repair paid after the sale is deducted in full; fixes `f337fc9`, `5227ad4`), the draft service, `GET …/anlage-v` and `GET …/anlage-v/sources`, and the Steuern screen with the sources drawer |
| Step 2 (inputs, elections, checklist, reading the two certificates) | `services/tax_inputs.py`, `rules/prefill.py` additions, `rules/document_checklist.py` entries | Merged and deployed 9 Nov 2026 with step 1 (PR #81; built on `feat/p3-inputs`, PR #82 closed into it): `tax_inputs` service and routes, the two certificates' pre-fill, `cost_receipt` and `market_rent` checklist entries, the input controls; reviewed once, no findings. The rules `erhaltungsaufwand.py` and `verbilligt.py` came with step 1 |
| Step 3 (exports) | `services/tax_export.py`, `services/tax_export_files.py`, job `tax_export`, `api/routes/tax_exports.py`, the Übergabe card on `/tax/[year]` | Merged and deployed 9 Nov 2026 (PR #84, images `05c1239bf7d1`): field sheet PDF and archive ZIP built by the job, stored content-addressed with an immutable `tax_export` row and event; export state read from the job while no row exists; download through a signed API route (HMAC under the session key, 300 s, session still required, not a storage-signed URL, since the local store has none); the §23 date on the Steuern page and the property header. Reviewed once: a non-ASCII signature answered 500 (now 404) and a blob comment was wrong (`8d51fbb`) |
| Step 4 (real-data closing, form 2026 confirmation) | `rulesets/2026.yaml` status flip | Not started |

**Known gaps after steps 1–3 (9 Nov 2026), none changes a figure in the fixtures:**

- Step 1's done-test half "Luis's real 2026 draft shows every confirmed line and each opens its sources" waits for Luis to open the Steuern page; not yet checked.
- `cost_receipt` items follow the booking year, not the §5.1 tax year (`services/checklist.checklist_facts` takes an `allocation_year` resolver; step 1's `tax_year_of` is not wired in). Only a recurring repair paid within ten days of the year end could land in the other year's list.
- An allocation-scoped input stays on its `allocation_id`; if the line is re-allocated, the answer must be given again on the new line (the old row is history; the draft never reads it).
- An `exclude_from_15pct` with reason `extension` on a `repairs` line without a `herstellung` election leaves an Erweiterung deductible on Z55 (an Erweiterung is Herstellungskosten by nature). Luis chose a guard on 9 Nov 2026 (D78), merged in PR #85; rows recorded before it are not re-checked (the table went live the same day).
- The `market_rent` input's `document_id` is optional although D61 calls the Mietspiegel the evidence.
- Two schema models are both called `SourceRef` (`schemas/ai.py`, `schemas/tax.py`); OpenAPI uses module-qualified names. Rename one.
- No Playwright flow for the Steuern page yet.
- Step 3: the archive copies tenant documents (leases) into an export kept 10 years after the tax year, longer than a tenant's erasure hold (end of tenancy + 4 years), and the `tax_export` row's foreign key to `document_file` would block deleting that blob. How erasure treats an archive is open (§17).
- Step 3: the archive is built in memory, so it is capped at 100 MiB (D77); a bigger year is split by hand.
- Step 3: an export whose job never runs (worker down) shows "wird erstellt …" until reload, with no age limit; the same as the other polls in the app.
- Step 3: a job retried after a failure between storing the file and committing leaves an unreferenced blob (the bytes carry the creation time, so the retry does not reuse it); rare, cleaned by retention.
- Step 3: the property header computes the full draft (up to twice: this year, else last year) to show the §23 date; a cheap date-only endpoint would do.
- Step 3: the archive names originals by phase 2's download name `<date> <title>.<ext>` (no type, unlike §12's wording), and the README lists them, so a lease title can show a tenant's name there; the sheet and both CSVs carry none. The name check is in `tests/api/test_tax_exports.py` (§6's `tests/test_tax_export_privacy.py` was not created).
- Tax inputs written in one transaction share `recorded_at`; `services/tax._tax_inputs` orders ties by the supersedes chain (`65bbb52`, a CI flake found on the combined branch).

**Done when** (ARCHITECTURE.md, phase 3): the 2026 draft reproduces a hand-calculated return
to the euro, and every line opens its sources. §14 turns that into fixtures:
`anlage_v_2026.yaml` is the hand calculation (synthetic numbers), and every line of
`AnlageVDraft` carries `sources` that the API and the screen resolve to transactions,
documents and records.

## 1. Scope

**In**

- Rule set 2026 as versioned data (`RuleSet`, YAML per tax year), including the form mapping
  category → Hausbuch line code → Zeile and ELSTER Kennzahl per form year (§4).
- `draft(facts, tax_year, rule_set) -> AnlageVDraft`, pure, with every line carrying its
  sources (§5.11). Rules: Zuflussprinzip with the ten-day rule and its Fälligkeit
  requirement (§5.1), AfA linear (§5.2), anschaffungsnahe Herstellungskosten and the 15 %
  tracker (§5.3), Erhaltungsaufwand and the §82b EStDV spread (§5.4), Schuldzinsen,
  Geldbeschaffungskosten and the Zinsbescheinigung reconciliation (§5.5), Hausgeld and the
  Erhaltungsrücklage (§5.6), Umlagen and the income lines (§5.7), verbilligte Vermietung
  (§5.8), the §23 date (§5.9), the lines booked before the handover (§5.10).
- User tax inputs as append-only rows with events: §82b spread, renovation treatment,
  exclusion from the 15 % test, ortsübliche Miete, and the two verified certificate figures
  (§6, §7).
- The missing-document checklist extended by rule-derived entries (§8).
- The Steuern screen (mockup `04-tax-year`), sidebar entry, sources drawer, open items (§11).
- ELSTER field sheet (PDF) and the yearly archive (ZIP: field sheet, CSVs, originals), each
  stored as a snapshot with the rule set version (§12).

**Out (named so nobody builds them by accident)**

- Anlage KAP and SO, the household forms (phase 8); the marginal rate, tax effect, projection
  and the §23 gain and sale lever (phase 4; §5.9 ships only the date); Betriebskostenabrechnung
  (phase 4).
- Degressive AfA (§7 Abs. 5a EStG, new residential buildings, construction or binding contract
  1 Oct 2023 – 30 Sep 2029) and §7b: a used flat completed before 2023 qualifies for neither;
  the form lines exist in the mapping (Z33 method code 2, Z36–41) and stay empty.
- Vermietung an Angehörige (Z10, Z19), Umsatzsteuer lines (Z27–28, Z79, Z84), Renten (Z52–54),
  Zuschüsse (Z29 first part, Z89), AfA on movables (Z42–45), Mieten für frühere Jahre and kept
  deposits (Z25): not in Luis's situation; the mapping names them so a later year can fill
  them, the draft never does.
- Filing to ELSTER (ERiC) stays a non-goal; the field sheet is typed over.
- Finanzamt copies of documents (annotations flattened into the page, XFA forms): the archive
  lists which documents need one; making them is open question 8.

## 2. Decisions taken in this spec

Numbering continues from phase 2's D52 (the 0.00 € auto-confirm, merged 9 Nov 2026). Each one is a place to push back in review.

| # | Decision | Why |
| --- | --- | --- |
| D53 | **Rule sets are YAML data files per tax year** under `rules/tax/rulesets/`, loaded into a frozen `RuleSet` (§4). The form mapping is keyed by a Hausbuch line code (`income.rent_total`, `wk.interest`, …) with the Zeile and the ELSTER Kennzahl per form year. A tax year whose form is not published yet gets a copy of the latest published mapping marked `provisional`; the screen and every export say so. | Principle 5. The 2026 Anlage V is not published as of 9 Nov 2026 (§4.3); line numbers moved in 2023 and again in 2025, Kennzahlen are the stable part. |
| D54 | **The tax year of a payment is derived by §11 EStG with the Fälligkeit requirement** (§5.1): the year the bank booked it, unless the payment is recurring, belongs economically to the adjacent year, and was *both* booked *and* due within the ten days around the year end (BFH X R 2/21, 16 Feb 2022). The payment date is the bank's booking date; the due date comes from the loan schedule, the tenancy's new `rent_due_day` (D67), or the first day of the service period. | ARCHITECTURE's rule row knew the ten days but not the Fälligkeit condition; D31 said the needed data was "all stored", which was true except for the due date. |
| D55 | **The AfA basis comes from the property record only**: (purchase price + Σ acquisition cost rows) × selected building share. `purchase_price` and `acquisition_cost` allocations are payment evidence and never summed (settles the open part of D44). The draft warns when a cost row has no paying allocation, an `acquisition_cost` allocation has no cost row, or Σ `purchase_price` allocations ≠ the recorded price. `loan_fees` (D35) never enter the basis. | Principle 2 with one source per figure; nothing counts twice. |
| D56 | **The 15 % tracker** counts confirmed `repairs` and `renovation` allocations performed in the three years from the handover (the work's date, D76; whatever their treatment, D74; the window per D75), net of VAT (the linked invoice's `amount_net`, else gross ÷ 1.19 marked *estimated*), against 15 % of the building share of the acquisition costs. Exclusions the law names (Erweiterungen, jährlich anfallende Erhaltungsarbeiten; Schadensbeseitigung per BFH IX R 6/16) are a per-allocation input `exclude_from_15pct` with a reason, not a new category. When the limit is crossed, every counted cost since the handover becomes Herstellungskosten: the draft of each earlier year changes and says so (`limit.prior_year_amendment`). | §6 Abs. 1 Nr. 1a EStG; the taxonomy stays fixed (D24); a flag with a reason is auditable in History. |
| D57 | **Contributions to the Erhaltungsrücklage are not Werbungskosten; the WEG's spending from it is**, in the year the WEG spends it, on the Erhaltungsaufwand line (the 2025 form's Z55 reads "einschließlich Entnahmen aus der Erhaltungsrücklage"). The amount is the Hausgeldabrechnung's `ruecklage_spent` (owner's share), proposed by pre-fill with page and quote and confirmed by Luis as a `tax_input` of kind `ruecklage_spent`. Until it exists the line is provisional and the checklist shows the Abrechnung as expected. Closes phase 2 open question 7. | BFH IX R 19/24 of 14 Jan 2025 (BStBl II 2025, 291), confirming IX R 119/83. Principle 1: the model reads, Luis verifies, code sums. |
| D58 | **User tax decisions and verified certificate figures live in one append-only table `tax_input`** (kinds `spread_82b`, `renovation_treatment`, `exclude_from_15pct`, `market_rent`, `ruecklage_spent`, `interest_certified`, `pre_handover_income` (D63)), each row with its scope (allocation, unit, loan, year), a typed `value`, `supersedes_id` and `withdrawn`; event `property.tax_input_recorded`. | Principle 4; a draft is reproducible from the ledger plus these rows plus the rule set. |
| D59 | **The draft sums confirmed allocations only.** Proposed sets, unmatched lines and allocations in an `undecided` category (`uncategorized`, `other_expense`, an unelected `renovation`) are excluded and reported as a count and a euro total ("n Zeilen in Zu prüfen nicht enthalten"). A line whose source document is still expected (Zinsbescheinigung, Hausgeldabrechnung) is flagged `provisional`. Drafts are computed on read and never stored; **exports are snapshots** (`tax_export` row + content-addressed file, D65). | "Only confirmed allocations reach the ledger" (ARCHITECTURE pipeline); what was handed to the Finanzamt stays reproducible. |
| D60 | **Cents everywhere; whole euros only on the field sheet**: income lines rounded down, cost lines rounded up, totals from the rounded lines, the owners' Zurechnung (Z86) by largest remainder with the odd euro to the first owner. Provisional until the Anleitung zur Anlage V 2025 confirms the rounding convention (open question 4). | The form's amount fields are whole euros ("EUR ,–"); "zu Ihren Gunsten" is the common guidance, found in secondary sources only. |
| D61 | **Verbilligte Vermietung** compares the agreed Warmmiete (Kaltmiete + Nebenkostenvorauszahlung) with the ortsübliche Warmmiete: the Mietspiegel's net cold rent per m² entered by Luis (`market_rent` input, dated, the Mietspiegel stored as evidence; the lowest value of the applicable range is allowed, since every value inside the range counts as ortsüblich) times the area, plus the year's umlagefähige costs from the ledger (`hausgeld_umlagefaehig`, `grundsteuer`, `insurance`), as R 21.3 EStR builds it. For a flat let by rooms the comparison is made once for the flat (Σ the rooms' agreed Warmmiete against the flat's `living_area_m2`, D71), for an MFH per unit. Luis's flat is in a city with a qualified Mietspiegel (Fürth, 2025 edition valid from 1 Aug 2025; checked 9 Nov 2026, Q7): ≥ 66 % nothing; 50–66 % warning "Totalüberschussprognose nötig"; < 50 % warning with the Kürzung percentage proposed for Z87. The draft never cuts Werbungskosten itself: the form says the Finanzamt does, from Z87. | §21 Abs. 2 EStG as amended 2021 (the 50 % threshold); R 21.3 EStR compares Warmmieten. ARCHITECTURE's row knew only the 66 % threshold (§16). |
| D62 | **§82b spread** is an input per allocation (`spread_82b`, 2–5 years): the year paid takes amount ÷ years (half-up to the cent, the last year the remainder), later years take their share on the "aus Jahr Y−k" lines; the remainder is deducted in full in the year ownership or the letting ends (§82b Abs. 2 EStDV). Changeable by a superseding row while the assessment is open; the app records, it does not judge finality. | §82b EStDV; one property has one owner group, so Abs. 3 (same spread for all co-owners) holds by construction. |
| D63 | **Income booked before the handover** (`booking_date < property.purchase_date`) is listed in a "Vor dem Übergang" block with its sum. Where the owner has recorded how it counts (a `pre_handover_income` tax input per property, value `rent`), those lines go on the rent line instead: added to the flat's Z13 row, each line a source of it, and the block is empty. Without that input they stay off the form with warning `income.pre_handover`. **Luis decided on 9 Nov 2026 (Q1): `rent`.** The rent share the seller forwarded was paid only for months in which the leases already named Luis as landlord (it began the month after the first instalment), so it is his rental income; only the share he received counts, not the part the seller kept under the contract. Costs before the handover count as vorab entstandene Werbungskosten (loan interest, Geldbeschaffungskosten, bank fees). | ARCHITECTURE's phase 3 input: the treatment was a tax question to settle with evidence. Whoever lets in their own name earns the rent; the readings "price reduction" and "interest for paying early" fit only months in which the seller was still the landlord, and there were none with a forwarded share. Declaring it as income is also the reading the Finanzamt has no reason to dispute. |
| D64 | **The checklist catalogue gains two data entries** (§8): `cost_receipt` per confirmed `repairs`, `renovation`, `loan_fees` or `professional_fees` allocation of the year (satisfied by its `document_id`), and `market_rent` per unit let in the year (satisfied by a `market_rent` input in force). Two new scope types (`allocation`, `unit`), two new satisfiers; no new code paths in the catalogue walk. | Phase 1 amendment 3 built the catalogue so "phase 3 adds entries, not code paths". |
| D65 | **Exports are built by a job and stored content-addressed**: a `tax_export` row (property, year, kind `field_sheet` or `archive`, rule set version, form status, the draft JSON) plus a `document_file` row for the bytes, served by the existing signed URL; event `property.anlage_v_exported`. The archive carries originals untouched (principle 6) and a `README` naming the documents known to need a Finanzamt copy (XFA, ink or stamp annotations). | Jobs, not requests (a ZIP of a year's documents is large); the vault's `document` table stays for originals, so no new document source or subtype. |
| D66 | **Evals: none for the rules, two new recordings for reading**: synthetic `zinsbescheinigung` and `hausgeldabrechnung` documents join `tests/evals/recordings/` (the extraction kinds exist since phase 2 but were never recorded), because step 2 reads `interest_paid` and `ruecklage_spent` from them into `tax_input` proposals. Luis's real Zinsbescheinigung is evaluated from his computer when it arrives (D29). | Principle 1: the rules are deterministic and fixtured; only the reading of the two certificates involves a model. |
| D67 | **`tenancy.rent_due_day`** (default 3, 1–28): the day of the month the rent is due, §556b Abs. 1 BGB's default being the third working day. The ten-day rule needs it (D54); phase 2's lease extraction does not read it yet, so it is typed on the tenancy form and can join the lease pre-fill later. | The one piece of data the §11 rule needs that phase 2 did not store; corrects D31's "all stored". |
| D68 | **§23 in phase 3 is a date**: `tax_free_from = notarized_on + 10 years + 1 day` on the Steuern screen and in the export's metadata, and each export snapshot keeps the year's AfA claimed, which the phase 4 gain calculation reads back (§23 Abs. 3 S. 4 EStG). | ARCHITECTURE lists §23 among the v1 rules and the sale lever in phase 4; splitting keeps step 1 small. |
| D69 | **Everything is "direkte Zuordnung"**: the whole object is let and the rental account is dedicated, so the draft fills the direct columns and leaves the "verhältnismäßige Zuordnung" columns (Z34, 37, 40, 43–44, 47, 50, 53, 56, 59, 62, 65, 68, 71, 74, 77, 81) empty. A partly self-used object is out of scope for v1. | ARCHITECTURE's Account note (dedicated account, no private lines). |
| D70 | **A 0.00 € allocation is not evidence.** Phase 2's D52 lets a model answer for a bank line of exactly 0.00 € be confirmed without review in categories such as `repairs`, `renovation`, `acquisition_cost`, `professional_fees`, `loan_fees`, `other_income`, `other_expense`. Such a row changes no sum, but every phase 3 rule that reads the *presence* of a category must ignore it: it creates no `cost_receipt` checklist item (§8), is not counted in and does not open the 15 % tracker's list (§5.3), raises no `renovation.undecided`, `income.other_review`, `income.pre_handover`, `afa.acquisition_cost_without_row` or `afa.purchase_price_mismatch` warning, needs no `renovation_treatment` input and is not an "open line" in `excluded_open` (§5.11). `DraftFacts` is built from confirmed allocations with `amount <> 0`, so a zero row is never a line's source either; it stays visible on the transaction and in History only. Fixture cases `zero_amount_is_not_evidence` in `checklist_phase3.yaml`, `anschaffungsnah.yaml` and `anlage_v_2026.yaml`. | A confirmation nobody looked at (D52) must not become a document requirement, a tracker entry or a warning a person then has to clear; the sums are unaffected either way. |
| D71 | **One Z13 row per Wohnung, not per room** (Luis, 9 Nov 2026, Q2). Units let as rooms of one flat are summed into one `income.rent_units` row: the flat's rents (and, per D63, the forwarded share before the handover) next to the flat's Wohnfläche. The flat's area is a new nullable `property.living_area_m2` (the Wohnfläche of the purchase contract or the Wohnflächenberechnung, typed or pre-filled later); when it is empty the row uses the sum of the units' m² and the draft warns `income.living_area_missing`, because rooms' areas leave out kitchen, bath and hall. A property whose units are separate flats (an MFH) keeps one row per unit. The Steuern screen still shows each room's rent under the row. | The Finanzamt knows one Wohnung under one Aktenzeichen; rows per room would read as several flats. |
| D72 | **How each lease agrees the running costs is a tenancy field** `operating_costs_terms`: `prepayment` (Vorauszahlung with a yearly settlement, the default), `flat_rate` (Pauschale, no settlement) or `included` (one all-in rent, Nebenkosten not agreed separately). Luis's earlier room leases charge a separately named Pauschale; the later lease states one all-in Warmmiete with the Betriebs- und Nebenkosten "abgegolten" (Luis, 9 Nov 2026), so it is `included` and the terms are mixed. `prepayment` and `flat_rate` amounts go on Z20; an `included` tenancy's whole rent is booked as `rent_cold` and stays on Z13. The Z24 box ("nicht gesondert vereinbart") is ticked when every tenancy let in the year is `included`; when the terms are mixed it stays unticked and the draft warns `umlagen.mixed_terms`, naming the all-in tenancies, until the Anleitung is read (Q4). Typed on the tenancy form; the lease pre-fill can read it later. Phase 4's Betriebskostenabrechnung reads the same field (no settlement for `flat_rate` or `included`), which settles ARCHITECTURE's phase 4 input on the Pauschale. | The terms differ per lease, so they belong on the tenancy, not the property; the tax is the same whichever line carries the amount, only the form's presentation changes. |
| D73 | **Basis additions are gross** (step 0, 9 Nov 2026; confirmed by the review the same day, BMF 26 Jan 2026 Rn. 54, 68). A cost that becomes Herstellungskosten (a `renovation` elected `herstellung`, or every counted cost once the 15 % limit is crossed) enters the AfA basis at its gross amount, VAT included. Only the 15 % *test* is net (§6 Abs. 1 Nr. 1a EStG says so); the cost that is then capitalised is what was paid. The spec's §5.3 example had added the net figures (5,000.00 and 36,000.00); the fixtures add 5,950.00 and 42,840.00 (`anschaffungsnah.yaml` `crossing_in_second_year`, `afa.yaml` `limit_crossing_additions_gross`). | §9b Abs. 1 EStG: input VAT is part of the Anschaffungs- or Herstellungskosten unless it can be deducted, and a residential landlord's lettings are VAT-exempt (§4 Nr. 12 UStG), so it cannot. Adding net would understate the AfA by 19 % of the additions every year. BMF letter of 26 Jan 2026 on anschaffungsnahe Herstellungskosten, Rn. 54 and 68. |
| D74 | **A `herstellung` election counts toward the 15 % test** (9 Nov 2026, from the step 0 review). The test covers Instandsetzung and Modernisierung, including a wesentliche Verbesserung; only Erweiterungen (§255 Abs. 2 S. 1 HGB, input `exclude_from_15pct` with reason `extension`) and jährlich übliche Erhaltungsarbeiten (reason `annual_maintenance`) are outside it. A `herstellung`-elected cost keeps its one basis addition (reason `herstellung`, gross, year paid, §5.4) and is never added a second time as `limit_exceeded` when the limit is crossed. Fixture cases `herstellung_counted`, `herstellung_extension_excluded`, `herstellung_counted_not_re_added`. | BMF 26 Jan 2026 Rn. 55, 60; BFH IX R 25/14 (14 Jun 2016). The spec had excluded the election from the count, which would let a capitalised Modernisierung hide how close the other costs are to the limit. |
| D75 | **The three-year window ends on the same calendar day three years after the handover, inclusive** (9 Nov 2026, from the step 0 review): handover 1 Jul 2026 → last day 1 Jul 2029. The handover is an event during the day, so for computing the end the period starts the next day (§187 Abs. 1 BGB; the handover day itself still counts as inside the window, `window_start = purchase_date`) and ends with the expiry of the day whose number matches (§188 Abs. 2 BGB); a day missing in the end month moves the end to its last day (§188 Abs. 3). A Kaufvertrag fixing the Übergang to the start of a day ("0:00 Uhr") would be the §187 Abs. 2 case and end one day earlier; Hausbuch uses the Abs. 1 reading because its handover is the event of full payment. | §108 AO with §§187, 188 BGB; BMF 26 Jan 2026 Rn. 62. The spec had `purchase_date + 3 years − 1 day`, one day short. |
| D76 | **The date tested against the window is when the work was done** (9 Nov 2026, from the step 0 review): the allocation's `service_period` end when set, else its booking date. Work performed by the end of the window counts even when paid after it. A `repairs` or `renovation` cost without a service period, booked within 90 days after the window's last day and so not counted, raises warning `anschaffungsnah.performance_date_check` (severity warning, params `booking_date`, `amount`) on the draft of the year paid, asking Luis to check when the work was done and set the period. | BMF 26 Jan 2026 Rn. 63: costs for work performed within the three years count, whether paid or not. A late invoice is common; 90 days covers the usual invoicing and payment terms. |
| D77 | **An archive is capped at 100 MiB, not 1 GiB** (9 Nov 2026, found while checking step 3): over it, `tax_export.too_large` and the year is split by hand; checked before the job is queued (the originals' sizes) and again on the finished ZIP. | The ZIP is built in memory (the originals, the buffer and its copy) in an API container with 512 MB; at 1 GiB the job would be killed before the check fires. One flat's year of documents is far below 100 MiB. |
| D78 | **An Erweiterung leaves the 15 % count only as Herstellungskosten** (9 Nov 2026, Luis): an `exclude_from_15pct` input with reason `extension` is accepted only on a `renovation` allocation with a current `renovation_treatment` of `herstellung`; while it is current, an `erhaltung` answer or a withdrawal of that election is refused (`tax_input.extension_needs_herstellung`); the exclusion is withdrawn first. The form offers "Erweiterung" only on such a line and explains the booking elsewhere. Rules and fixtures unchanged. | An Erweiterung is Herstellungskosten by nature (§255 Abs. 2 S. 1 HGB); without the guard a cost could leave the 15 % count and still be deducted at once on Z55. Luis chose refusing the combination over a warning, since no case exists where it is right. |

## 3. Sources checked (9 Nov 2026)

What each rule rests on, and what could not be verified from an official source.

| Rule | Verified | Source | Not verified → §17 |
| --- | --- | --- | --- |
| Zuflussprinzip, ten-day rule | §11 Abs. 1 S. 1–2, Abs. 2 S. 1–2 EStG (regelmäßig wiederkehrend, kurze Zeit vor Beginn / nach Beendigung des Kalenderjahres); "kurze Zeit" = up to 10 days (H 11 EStH "Allgemeines", example 22 Dec – 10 Jan); payment **and** Fälligkeit must fall in the short period (BFH X R 2/21, 16 Feb 2022, BStBl II 2022, 448; VIII R 1/20; a Fälligkeit shifted by §108 Abs. 3 AO does not extend it, VIII R 34/12) | [§11 EStG](https://www.gesetze-im-internet.de/estg/__11.html); [PwC on X R 2/21](https://blogs.pwc.de/de/steuern-und-recht/article/232154/update-faelligkeitserfordernis-bei-regelmaessig-wiederkehrenden-einnahmen-und-ausgaben/); [NWB on the ten-day rule](https://www.nwb-experten-blog.de/zehn-tage-regel-des-%C2%A7-11-estg-und-wieder-war-der-bfh-am-zuge/) | Whether a payment's Fälligkeit for rent is the lease's day or the third working day (D67 takes the lease's day; Q6) |
| AfA linear | §7 Abs. 4 S. 1 Nr. 2: a) completed after 31 Dec 2022 → 3 %; b) 1925–2022 → 2 %; c) before 1 Jan 1925 → 2.5 %. §7 Abs. 1 S. 4: in the acquisition year the annual AfA is reduced by 1/12 for each full month *preceding* the acquisition month. §9a EStDV: year of acquisition = year of delivery (Übergang). Degressive §7 Abs. 5a: 5 %, window 1 Oct 2023 – 30 Sep 2029 (out of scope) | [§7 EStG](https://www.gesetze-im-internet.de/estg/__7.html); [§9a EStDV](https://www.gesetze-im-internet.de/estdv_1955/__9a.html) | AfA in the year of a sale and nachträgliche HK accruing from 1 Jan of their year (R 7.4 Abs. 9 EStR, not fetched; Q11) |
| AfA basis, Nebenkosten split | Anschaffungsnebenkosten (Grunderwerbsteuer, Notar, Grundbuch, Makler) are split between land and building in the same ratio as the price; only the building part enters the AfA basis | secondary sources agree ([steuerschroeder](https://www.steuerschroeder.de/aufteilung_kaufpreises_immobilien.html)); the BMF Arbeitshilfe page was unreachable (HTTP 503) | Confirm in the Arbeitshilfe's Anleitung when reachable (Q11); the BMF Arbeitshilfe itself was updated Jan 2025 and again early 2026 ([KPMG](https://kpmg.com/de/de/themen/2026/03/bmf-kaufpreisaufteilung.html)) |
| Anschaffungsnahe Herstellungskosten | §6 Abs. 1 Nr. 1a EStG: Instandsetzung and Modernisierung within three years after the Anschaffung, net of VAT, above 15 % of the building's Anschaffungskosten → Herstellungskosten; excluded: Erweiterungen (§255 Abs. 2 S. 1 HGB) and jährlich üblicherweise anfallende Erhaltungsarbeiten. Schönheitsreparaturen count (BFH IX R 25/14, IX R 15/15, IX R 22/15 of 14 Jun 2016; BMF 20 Oct 2017); damage caused after acquisition does not (IX R 6/16); the test is per selbständiger Gebäudeteil | [§6 EStG](https://www.gesetze-im-internet.de/estg/__6.html); [Haufe on BMF 20 Oct 2017](https://www.haufe.de/steuern/finanzverwaltung/bmf-anschaffungsnahe-herstellungskosten-bei-gebaeuden_164_429956.html); [BFH IX R 25/14](https://rewis.io/urteile/urteil/eck-14-06-2016-ix-r-2514/) | The start of the three years: "Anschaffung" is the Übergang (§9a EStDV's delivery); H 6.4 EStH's "obligatorisches Rechtsgeschäft" rule belongs to Abbruchkosten, not to this test ([H 6.4](https://www.steuerschroeder.de/steuergesetze/esth/6.4)), but no Hinweis states it for §6 Abs. 1 Nr. 1a explicitly (Q11) |
| Herstellungskosten gross (D73, added in step 0) | §9b Abs. 1 EStG: the Vorsteuer belongs to the Anschaffungs- or Herstellungskosten unless it can be deducted; a residential landlord cannot (VAT-exempt lettings, §4 Nr. 12 UStG), so capitalised costs are gross | [§9b EStG](https://www.gesetze-im-internet.de/estg/__9b.html); BMF 26 Jan 2026 Rn. 54, 68 (checked in the step 0 review) | — (confirmed 9 Nov 2026) |
| 15 % test: what counts, the window, the date tested (D74–D76, from the step 0 review) | A wesentliche Verbesserung counts, only Erweiterungen and yearly routine maintenance are out (Rn. 55, 60; BFH IX R 25/14); the window is computed by §108 AO with §§187 Abs. 1, 188 Abs. 2 BGB (Rn. 62); work performed within it counts, paid or not (Rn. 63) | BMF letter of 26 Jan 2026 on anschaffungsnahe Herstellungskosten, as cited by the reviewer | — |
| §82b EStDV | Größerer Erhaltungsaufwand on a building mostly used for living may be spread evenly over 2–5 years; on sale or end of letting the rest is deducted in that year; co-owners spread alike | [§82b EStDV](https://www.gesetze-im-internet.de/estdv_1955/__82b.html) | — |
| Schuldzinsen, Geldbeschaffungskosten | Interest deductible, Tilgung not (§9 Abs. 1 S. 3 Nr. 1 EStG); financing costs are Werbungskosten (D35); a marktübliches Disagio is deductible when paid (§11 Abs. 2 S. 4 EStG; BMF 20 Oct 2003: ≤ 5 % at ≥ 5 years Zinsbindung) | [§11 EStG](https://www.gesetze-im-internet.de/estg/__11.html) | No Disagio field exists; the 5 % check is not built (Q10) |
| Hausgeld, Erhaltungsrücklage | Payments into the Rücklage are not Werbungskosten when paid; they are when the WEG spends them on Erhaltung (BFH IX R 19/24, 14 Jan 2025, BStBl II 2025, 291, confirming IX R 119/83 of 26 Jan 1988). The 2025 form puts the Entnahmen on Z55 | [KPMG on IX R 19/24](https://kpmg.com/de/de/home/themen/2025/02/bfh-steuerabzug-hausgeld-erhaltungsruecklage.html); [Deubner](https://www.deubner-steuern.de/themen/einkommensteuer/werbungskosten/zahlungen-erhaltungsruecklage.html); the form text (below) | — |
| Umlagen | Nebenkosten prepayments and settlement back-payments are income, refunds reduce it: form Z20 (laufende Neben-/Betriebskosten) and Z21 (Nachzahlungen / Erstattungen, negative allowed) | the official form 2025 (below) | Z24 "Neben-/Betriebskosten wurden nicht gesondert vereinbart" and the Nebenkostenpauschale (Q3) |
| Verbilligte Vermietung | §21 Abs. 2 EStG: < 50 % of the ortsübliche Marktmiete → split into a paid and an unpaid part; ≥ 66 % with Dauervermietung → fully paid; 50–66 % → Totalüberschussprognose (since VZ 2021, Jahressteuergesetz 2020); Warmmiete compared with Warmmiete (R 21.3 EStR) | [§21 EStG](https://www.gesetze-im-internet.de/estg/__21.html); [LfSt Bayern Merkblatt](https://www.lfst.bayern.de/fileadmin/RESSOURCEN/INFORMATIONEN/Steuerinfos/Zielgruppen/Haus_und_Grund/22-11-22_Merkblatt_verbilligte_Vermietung.pdf) | Which Mietspiegel value and how the Nebenkosten part of the ortsübliche Warmmiete is set (Q7) |
| §23 | Private sale within ten years between Anschaffung and Veräußerung is taxable; the gain = price − Anschaffungskosten − Werbungskosten, the Anschaffungskosten reduced by the AfA claimed (Abs. 3 S. 4); Freigrenze €1,000 | [§23 EStG](https://www.gesetze-im-internet.de/estg/__23.html) | Phase 4 |
| Anlage V 2025, line numbers and Kennzahlen | Read from the official form "080 – Anlage V (2025)", version `2025AnlV101NET – September 2025`, BMF Vordruckkommission ESt (§4.2) | [form PDF (Buhl mirror of the BMF form)](https://www.buhl.de/steuer/wp-content/uploads/sites/31/2026/02/Anlage_V_2025.pdf); official catalogue: [formulare-bfinv.de](https://www.formulare-bfinv.de/) | The Anleitung zur Anlage V 2025 could not be fetched programmatically (the Bavarian form page points to ELSTER and the FFW catalogue, both JavaScript-only); Q4 |
| Anlage V 2026 | Not found published as of 9 Nov 2026 (the 2025 form appeared in January 2026, dated September 2025) | searches of BMF, ELSTER, LfSt Bayern | Q5: re-check in ELSTER in early 2027; until then 2026 uses 2025's layout as `provisional` |

## 4. The rule set as data

### 4.1 Shape (`schemas/tax.py`)

```python
class FormLine(Schema):
    key: str                      # Hausbuch line code, stable across years ("wk.interest")
    zeile_from: int | None        # first line of a block (46 for Schuldzinsen Z46–48), else None
    zeile: int | None             # the printed line carrying the amount and Kennzahl (48); None = no own line
    kennzahl: str | None          # ELSTER Kennzahl printed in the box ("33"); None = none
    label: str                    # German label as on the form
    kind: Literal["header", "income", "cost", "afa", "total", "result", "flag"]
    rounding: Literal["down", "up", "none"]      # D60; income down, cost up

class FormMapping(Schema):
    form_year: TaxYear
    status: Literal["published", "provisional"]
    version_stamp: str            # "2025AnlV101NET – September 2025"
    source_url: str
    lines: dict[str, FormLine]    # by key
    category_lines: dict[AllocationCategory, str | None]   # None = not on the form (§4.2)
    tracked: list[AllocationCategory]   # None categories shown as "erfasst, nicht abziehbar"
    open: list[AllocationCategory]      # None categories excluded and counted (D59)
    spread_prior_years: int = 4   # Z61–72: keys wk.maintenance_spread_prior_1 ("aus Y−1") … _4

class AfaRateRule(Schema):
    completed_from: Year | None; completed_to: Year | None; rate: Rate  # inclusive bounds

class RuleSet(Schema):
    tax_year: TaxYear
    version: str                  # "2026.1"; stamped on every export
    short_period_days: int = 10   # §11, H 11 EStH
    afa_rates: list[AfaRateRule]
    anschaffungsnah_share: Rate = Decimal("0.15"); anschaffungsnah_years: int = 3
    default_vat_rate: Rate = Decimal("0.19")      # net estimate when no invoice net (D56)
    spread_82b_min_years: int = 2; spread_82b_max_years: int = 5
    reduced_rent_full: Rate = Decimal("0.66"); reduced_rent_split: Rate = Decimal("0.50")
    speculation_years: int = 10
    form: FormMapping
```

`rules/tax/registry.py: rule_set(tax_year) -> RuleSet` loads `rulesets/<year>.yaml`; a year
without a file raises `tax.no_rule_set`. A rule set whose `category_lines` misses a category,
or whose `lines` lack a key the draft writes, fails to load with `form_mapping.incomplete`
(`tests/rules/test_tax_rulesets.py` checks both, and pins the 2025 Zeilen and Kennzahlen; the
planned `form_mapping.yaml` fixture became that Python test in step 0, because a mapping is
data checked against itself rather than a rule's input and output).

### 4.2 Form mapping 2025 (verified from the form) and 2026 (provisional copy)

Header and income lines:

| Key | Zeile | Kz | Filled from |
| --- | --- | --- | --- |
| `header.address` | 4–5 | — | `property.address` |
| `header.aktenzeichen` | 6 | — | the Grundsteuerbescheid extraction's `reference`, else empty with a checklist pointer |
| `header.contract_date` / `header.transfer_date` / `header.completed` | 7 | — | `notarized_on` / `purchase_date` / `build_year` |
| `header.sold` | 8 | 53 | `ownership_ended_on` (empty while owned) |
| `header.living_area` | 11 | 54 | `property.living_area_m2`, else Σ `unit.floor_area_m2` with warning `income.living_area_missing` (D71) |
| `income.rent_units` | 13–14 | — | one row per Wohnung: for an ETW let by rooms, the flat (`property.living_area_m2`, Σ `rent_cold` of its units); for an MFH, one row per unit (D71). The first row prints on Z13 (`zeile_from`, "Bezeichnung der Wohneinheit"), rows 2..n on Z14 (`zeile`, "weiterer Wohneinheiten"), so the flat's single row is on Z13 |
| `income.rent_total` | 15 | 01 | Σ of the unit rows |
| `income.umlagen` | 20 | 04 | Σ `operating_costs_prepayment` of `prepayment` and `flat_rate` tenancies (D72) |
| `income.umlagen_not_agreed` | 24 | 13 | box, ticked when every tenancy let in the year is `included` (D72); the box carries Kz 13 on the 2025 form (corrected in step 0: this row said none) |
| `income.umlagen_settlement` | 21 | 11 | Σ `operating_costs_settlement`, signed (refund negative) |
| `income.other` | 29 → 31 | 08 | Σ `other_income`, each with warning `income.other_review` |
| `income.total` | 32 | — | Z15 + Z20 + Z21 + Z31 (+ the unused lines) |

Werbungskosten lines (direct column, D69):

| Key | Zeile (direct → abzugsfähig) | Kz | Filled from |
| --- | --- | --- | --- |
| `wk.afa` | 33 → 35 | 30 | §5.2; method code `1 = linear`, rate in %, `2 = laut Erläuterung` in the first year (the sheet attaches the calculation), `1 = wie Vorjahr` after |
| `wk.interest` | 46 → 48 | 33 | Σ `loan_interest`; Einzelangaben = lender names |
| `wk.financing_costs` | 49 → 51 | 34 | Σ `loan_fees` |
| `wk.maintenance` | 55 | 36 | `repairs` and `renovation` elected `erhaltung` without a spread, plus `ruecklage_spent` (D57) |
| `wk.maintenance_spread_total` / `wk.maintenance_spread_current` | 57 / 58 → 60 | 57 / 38 | §82b: this year's total and its share (D62) |
| `wk.maintenance_spread_prior_4` … `_1` | 61–72 (Y−4 … Y−1) → 63, 66, 69, 72 | 39–42 | shares of earlier years' spreads; one key per prior year (`_1` = aus Y−1 on Z70–72, Kz 42; `_4` = aus Y−4 on Z61–63, Kz 39) |
| `wk.allocable_costs` | 73 → 75 | 52 | `hausgeld_umlagefaehig`, `grundsteuer`, `insurance` ("Umgelegte Kosten": Grundsteuer, Wasser, Heizung, Hausversicherungen, Hauswart …) |
| `wk.non_allocable_costs` | 76 → 78 | 48 | `hausgeld_verwaltung`, `property_management`, `bank_fees` ("Verwaltungskosten, Bank- und Kontoführungsgebühren – ohne Erhaltungsrücklage –") |
| `wk.other` | 80 → 82 | 49 | `professional_fees` |
| `wk.total` | 83 | — | sum of the abzugsfähig lines |
| `result.surplus` | 85 | — | Z32 − Z83 |
| `result.owner_a` / `result.owner_b` | 86 | 20 / 21 | by `property_ownership` shares (D60) |
| `reduced_rent.cut_percent` | 87 | 50 | §5.8, only below 50 % |

Not on the form (`category_lines` = `None`, shown as "erfasst, nicht abziehbar" or not at
all): `loan_principal` (shown), `hausgeld_ruecklage` (shown), `deposit_in`, `deposit_out`,
`owner_transfer_in`, `owner_transfer_out`, `loan_disbursement`, `purchase_price`,
`acquisition_cost` (basis via the record, D55). Open until decided (excluded, D59):
`renovation` without a `renovation_treatment` input, `other_expense`, `uncategorized`.

`categories.yaml` and `rules/categories.py` do not change: `renovation` and
`hausgeld_ruecklage` stay `undecided` there, and the rule set of each year resolves them
(principle 5). The comment in `categories.py` saying phase 3 "does not change the treatment"
stays true.

### 4.3 The 2026 form

Not published as of 9 Nov 2026 (§3). `2026.yaml` starts as a copy of 2025's mapping with
`status: provisional`, `form_year: 2025`, and the §82b prior-year lines relative to the tax
year. The screen shows "Formular 2026 noch nicht veröffentlicht; Zeilen nach dem Formular
2025" and every export prints the same. When ELSTER publishes the 2026 Anlage V, a data PR
(step 4) updates the Zeilen and Kennzahlen, sets `published`, and `test_tax_rulesets.py` gains
the 2026 rows. The `anlage_v_2026.yaml` fixture asserts on line **keys** and amounts, never on
Zeilen, so the data PR changes no fixture outcome.

## 5. Rules (`rules/tax/`)

Pure functions, no I/O, no model; errors carry a code (§13 lists them). Each rule returns
lines, warnings and sources; `draft` assembles them (§5.11).

### 5.1 Tax year of a payment (`tax_year.py`)

`tax_year_of(payment: PaymentFacts, rule_set) -> TaxYearResult` where `PaymentFacts` holds
`booking_date`, `value_date`, `recurring`, `service_period`, `due_on` and the category, and the
result holds `year`, `rule` (`paid` | `short_period_shift`) and `notes`.

1. The payment date is the bank's `booking_date` (the Gutschrift or Lastschrift the statement
   shows; the Wertstellung is an interest convention). `year = booking_date.year`.
2. The economic year `E` exists only when `recurring` is true and the service period lies
   within one calendar year; `E` is that year. No period, or a period across the year end
   (`Hausgeld 12/26–02/27`), means no shift.
3. The short period is `short_period_days` = 10: 22–31 December and 1–10 January.
   A payment booked 22–31 Dec of `E−1` counts in `E` when `due_on` lies between 22 Dec `E−1`
   and 10 Jan `E`; a payment booked 1–10 Jan of `E+1` counts in `E` when `due_on` lies
   between 22 Dec `E` and 10 Jan `E+1`. Otherwise the year paid.
4. `due_on` is resolved by the service before the rule runs (the rule never guesses):
   `loan_interest` / `loan_principal` → the schedule entry the matcher used (D39: nearest
   due date within ±10 days of the booking); `rent_cold` / `operating_costs_prepayment` → day
   `tenancy.rent_due_day` of the service period's first month (D67); `hausgeld_*` → the first
   day of the service period (Q10); any other recurring payment → the first day of the
   service period; non-recurring → none.
5. `value_date` in another year than `booking_date` → warning `year_end_straddle` on that
   line (information; the year does not change).

Worked examples (fixture `tax_year.yaml`):

| Case | Booked | Period | Due | Year | Why |
| --- | --- | --- | --- | --- | --- |
| `rent_january_paid_december` | 2026-12-29 | 01/2027 | 2027-01-03 | 2027 | both inside the short period, belongs to 2027 |
| `rent_december_paid_late` | 2027-01-05 | 12/2026 | 2026-12-03 | 2027 | paid in the period, but due long before it (X R 2/21) |
| `rent_january_paid_too_early` | 2026-12-20 | 01/2027 | 2027-01-03 | 2026 | 20 Dec is 12 days before the year end |
| `hausgeld_january_debited_december` | 2026-12-28 | 01/2027 | 2027-01-01 | 2027 | shift |
| `annuity_january_debited_december` | 2026-12-30 | 01/2027 (from the schedule entry) | 2027-01-01 (schedule) | 2027 | shift; the service sets the period from the schedule entry the matcher used, so the rule sees a recurring payment for January (step 0: this row showed no period, which by rule 2 would mean no shift) |
| `repair_paid_after_year_end` | 2027-01-02 | 11/2026 | — | 2027 | not recurring |
| `settlement_backpayment_january` | 2027-01-08 | 2026 | — | 2027 | not recurring |
| `period_across_year_end` | 2026-12-28 | 12/2026–02/2027 | 2026-12-01 | 2026 | no single economic year |
| `straddle_warning` | 2026-12-31 (value 2027-01-02) | — | — | 2026 + `year_end_straddle` | booking date decides |

### 5.2 AfA linear (`afa.py`)

`afa(facts: AfaFacts, tax_year, rule_set) -> AfaLine`, with `AfaFacts` = purchase price, cost
rows (kind, amount, document), selected split (share, source, evidence document), build year,
purchase date, ownership end, and basis additions per year (§5.3, renovation `herstellung`).

- `basis = round((purchase_price + Σ cost rows) × building_share, 2)`; the Nebenkosten are
  split by the same share (§3).
- Rate from `afa_rates` by `build_year`: ≤ 1924 → 2.5 %, 1925–2022 → 2 %, ≥ 2023 → 3 %.
- Months: in the purchase year `12 − (purchase_date.month − 1)` (the handover month counts in
  full, §7 Abs. 1 S. 4); 12 in every later year; the year ownership ends: months through
  `ownership_ended_on.month` (Q11, phase 4 confirms).
- `amount = round((basis + Σ additions of years ≤ tax_year) × rate × months ÷ 12, 2)`, the
  additions accruing from 1 January of their year (R 7.4 Abs. 9 EStR, Q11). Additions are
  **gross** amounts (D73, §9b Abs. 1 EStG); only the 15 % test of §5.3 is net.
- Sources: the property (price), every cost row and its document, the selected split and its
  evidence, the addition allocations. Method code for Z33: `2` (laut Erläuterung) in the first
  year and in any year the basis changed, else `1`.
- Warnings: `afa.no_split` (no selected split: line empty, checklist points to
  Kaufpreisaufteilung); `afa.cost_row_unpaid` (a cost row whose `paid_by` list of confirmed
  `acquisition_cost` allocations is empty: information, it may have been paid privately;
  params `kind`, `amount`); `afa.acquisition_cost_without_row` (a non-zero `acquisition_cost`
  allocation in no row's `paid_by`: the amount is **not** added; params `amount`);
  `afa.purchase_price_mismatch` (only when at least one non-zero `purchase_price` allocation
  exists and Σ of their magnitudes ≠ price, so a price paid privately raises nothing;
  information; params `price`, `paid`).

Worked example (`afa.yaml`, case `etw_1998_handover_july`): price 300,000.00, cost rows
19,500.00 + 4,200.00 + 1,300.00, share 0.720000 → basis 234,000.00; 2 % → 4,680.00 a year;
handover 1 Jul 2026 → 6/12 → **2,340.00** for 2026, 4,680.00 for 2027. Other cases: build
year 1920 (2.5 %), 2023 (3 %); handover 31 Jan (12/12), 15 Dec (1/12); a Balkonanbau (an
Erweiterung) elected `herstellung`, 12,000.00 in 2027 → 2027 AfA 4,920.00 (the step 0
fixture had a kitchen, which is a separate movable asset, Q15); the 15 % crossing of §5.3 with gross
additions (`limit_crossing_additions_gross`); no split → empty line + warning.

### 5.3 Anschaffungsnahe Herstellungskosten, the 15 % tracker (`anschaffungsnah.py`)

`limit_status(facts, tax_year, rule_set) -> LimitStatus` with `basis_building_cost` (the AfA
basis of §5.2 before additions), `limit = 15 %` of it, `window_start = purchase_date`,
`window_end` = the same calendar day three years after `purchase_date`, inclusive (D75:
handover 1 Jul 2026 → 1 Jul 2029), the counted allocations with gross, net,
`net_source` (`invoice` | `estimated`), `used_net`, `ratio_percent` (= `used_net ÷ limit ×
100`, one decimal, half-up: the share of the 15 % allowance used, not of the building cost),
`exceeded`, `exceeded_in_year`, `basis_additions`. The status describes the whole window as
known today, so it is the same whichever year is drafted.

- Counted: confirmed `repairs` and `renovation` with a non-zero amount (D70), whatever the
  treatment (`erhaltung`, undecided or `herstellung`, D74), whose tested date is in the window
  and with no `exclude_from_15pct` input in force. The tested date is when the work was done
  (D76): the `service_period` end when set, else the `booking_date`. Net = the linked invoice extraction's
  `amount_net` when present, else `gross ÷ (1 + default_vat_rate)` rounded, flagged.
- The test is strict: `used_net > limit`. Equal does not trigger.
- When exceeded in year X: every counted allocation of the window not elected `herstellung`
  becomes a basis addition (reason `limit_exceeded`) in the year it was paid (§5.2), at its
  **gross** amount (D73), and leaves `wk.maintenance`; a `herstellung`-elected cost already has
  its own addition (§5.4) and is not added again (D74); the
  drafts of X and later window years carry warning `limit.exceeded`, the drafts of years
  before X that had a cost moved carry `limit.prior_year_amendment` (params
  `exceeded_in_year`, `amount` = that year's gross moved). While the window is open and the
  limit not reached, the status shows the risk ("Fenster bis 01.07.2029 offen; bisher 14,2 %").
- A `repairs` or `renovation` cost without a service period, not counted, booked within 90 days
  after `window_end` → warning `anschaffungsnah.performance_date_check` on the draft of the year
  paid (D76).

Worked example (`anschaffungsnah.yaml`): limit 35,100.00; 2026: painter 1,190.00 gross
(invoice net 1,000.00) + bathroom tiles 4,760.00 (net 4,000.00) → used 5,000.00, 14.2 %, not
exceeded. Case `crossing_in_second_year`: 2027 adds a roof of 42,840.00 gross (36,000.00 net)
→ 41,000.00 net > 35,100.00 → 2027 exceeded (116.8 % of the allowance); 2026's two costs move
to the basis at 5,950.00 gross (2026 amendment warning with that amount), the roof at
42,840.00. The 2027 AfA is computed on 234,000.00 + 5,950.00 + 42,840.00 = 282,790.00 × 2 % =
**5,655.80**, and the amended 2026 AfA on (234,000.00 + 5,950.00) × 2 % × 6/12 = **2,399.50**
(`afa.yaml` `limit_crossing_additions_gross`). Corrected in step 0: this example added the net
figures (5,000.00 and 36,000.00), which §9b Abs. 1 EStG does not allow (D73). Cases `equal_is_not_exceeded`,
`outside_window_not_counted` (paid 2029-07-02), `estimated_net_without_invoice`,
`excluded_annual_maintenance`, `herstellung_counted` (10,000.00 net → 28.5 %),
`herstellung_extension_excluded`, `herstellung_counted_not_re_added` (46,000.00 net, 131.1 %,
only the roof becomes a `limit_exceeded` addition), `window_dates` (a cost on 1 Jul 2029 is
inside), `performed_in_window_paid_after` (service period to 30 Jun 2029, paid 20 Aug →
counted), `no_period_booked_after_window_warns` (booked 31 Jul 2029 → not counted, warning).

### 5.4 Erhaltungsaufwand and the §82b spread (`erhaltungsaufwand.py`)

- `repairs` → `wk.maintenance` in its tax year; `renovation` with `renovation_treatment =
  erhaltung` the same; `herstellung` → basis addition; no input → open (D59, warning
  `renovation.undecided` with the amount).
- The sources of `wk.maintenance` are the allocations, their invoices and, for an elected
  `renovation`, its `renovation_treatment` input (an input that decides a line is a source).
- `spread_82b(allocation, years)` (D62): year paid gets `round_half_up(amount ÷ years)`, each
  later year the same, the last year the remainder; lines `wk.maintenance_spread_total` (Z57,
  the total) and `wk.maintenance_spread_current` (Z58–60, "davon abzuziehen") in the year
  paid, then `wk.maintenance_spread_prior_k` with k = years since the payment. Ownership end → the remainder in that year. A withdrawn input moves
  the amount back to `wk.maintenance` in the year paid (the app records the change; whether
  the assessment is still open is Luis's call).

Worked example (`erhaltungsaufwand.yaml`): 7,000.00 spread over 3 years paid 2026 → 2026:
2,333.33 (Z57 total 7,000.00, davon 2,333.33); 2027: 2,333.33 ("aus 2026", `_prior_1`); 2028:
2,333.34 (`_prior_2`). Case `spread_sale_in_second_year` → 2027 takes 4,666.67 on `_prior_1`.
Case `spread_withdrawn_back_to_full` → `wk.maintenance` 7,000.00 in 2026. The fixture's
property was bought in 2020, so the 15 % window is closed and does not interfere.

### 5.5 Schuldzinsen, Geldbeschaffungskosten, Zinsbescheinigung (`schuldzinsen.py`)

- `wk.interest` = Σ `loan_interest` of the tax year (§5.1 applies: a January annuity debited
  on 30 December counts in January's year); Einzelangaben = the lenders. Interest before the
  handover counts (vorab entstandene Werbungskosten, D63).
- `wk.financing_costs` = Σ `loan_fees` (D35: notary and Grundbuch for the Grundschuld, bank
  fees for the loan). A Disagio would be booked here too; the 5 % check is not built (Q10).
- `loan_principal` is tracked, never deducted.
- Reconciliation: for every loan with an `interest_certified` input for the year (D66, read
  from the Zinsbescheinigung and confirmed), the certified amount is compared with Σ
  `loan_interest` of that loan in the year; a difference → warning `interest.certificate_mismatch`
  (params `certified`, `booked`, `difference` as a magnitude; the screen lists the lines the
  ten-day rule moved, the usual cause). The ledger stays the source; the figure is never
  replaced by the certificate, and the certificate's input is compared, not summed, so it is
  not a source of `wk.interest`. Einzelangaben list the lenders of the loans with interest in
  the year, sorted.

Worked example (`schuldzinsen.yaml`): interest-only debit 233.33 in July, then five annuities
with interest 700.00, 699.13, 698.25, 697.37, 696.48 → **3,724.56**; Tilgung 300.00, 300.87,
301.75, 302.63, 303.52 → 1,508.77 tracked; certificate 3,724.56 → no warning; certificate
3,724.00 → warning with difference 0.56. Case `january_annuity_moved`: the January 2027
annuity debited on 30 Dec 2026 counts in 2027 (§5.1); a bank certifies interest by its own
interest period, so the fixture's 2026 certificate (696.48 for December alone) leaves it out
too and matches, and 2027 carries its 695.60 interest and 304.40 Tilgung. Should a real
certificate count it in 2026 instead, the mismatch warning shows the moved line.

### 5.6 Hausgeld and the Erhaltungsrücklage (`hausgeld.py`)

- `hausgeld_umlagefaehig` → `wk.allocable_costs`; `hausgeld_verwaltung` →
  `wk.non_allocable_costs`; both in the year paid (§5.1).
- `hausgeld_ruecklage` → tracked, not deductible (D57).
- `ruecklage_spent` input for year Y (the Hausgeldabrechnung Y, which arrives in Y+1) →
  `wk.maintenance` of Y with the Abrechnung as source; it counts for its own year only.
  Missing for an ETW year → the line is `provisional` (shown as 0.00 when nothing else is on
  it), warning `hausgeld.abrechnung_pending`, checklist item `hausgeldabrechnung` expected
  until 30 Jun Y+1. An input of 0.00 (the WEG spent nothing) is an answer: no provisional
  flag.
- The owner's settlement with the WEG (Nachzahlung or Guthaben in Y+1) is a bank line
  allocated to `hausgeld_*` by a rule or by hand (D51's `as_last_split` fits) and counts in
  Y+1; the Abrechnung's `settlement_result` is shown for comparison only.

Worked example (`hausgeld.yaml`): six months of 320.00 split 180.00 / 40.00 / 100.00 →
1,080.00 allocable, 240.00 non-allocable, 600.00 tracked; Abrechnung 2026 with
`ruecklage_spent` 350.00 confirmed → `wk.maintenance` 2026 += 350.00; without it →
provisional + warning.

### 5.7 Umlagen and the income lines (`einnahmen.py`)

- `rent_cold` → one `income.rent_units` row per Wohnung (D71: the flat for an ETW let by rooms, with its rooms listed under it; one per unit for an MFH), total on
  `income.rent_total`.
- `operating_costs_prepayment` → `income.umlagen`; a Nebenkostenpauschale (`flat_rate`) is
  income all the same; an `included` tenancy has its whole rent on Z13 (D72, Z24 box).
- `operating_costs_settlement` → `income.umlagen_settlement`, sign kept (a refund to the
  tenant is negative).
- `other_income` → `income.other` with warning `income.other_review` per allocation (params
  `amount`, `counterparty_iban` masked), whatever its date: an `other_income` line before the
  handover keeps its warning even when §5.10 moves it to the rent row; income booked before
  the handover goes to the flat's rent row when the property's `pre_handover_income` input
  says `rent`, else to the "Vor dem Übergang" block (D63, §5.10).
- Mixed terms (D72): warning `umlagen.mixed_terms` with params `tenancies` (the ids of the
  `included` tenancies, sorted and joined by commas; the fixture has one). No `living_area_m2` → the row's area is Σ the units' m² and the
  draft warns `income.living_area_missing` (D71).
- Unit rows: an ETW or EFH has one row for the property (`area_source: living_area`, or `units` when the area is missing); an MFH
  one row per unit (`area_source: units`).
- `deposit_in` / `deposit_out`: nothing (a kept deposit, Z25, is out of scope).

### 5.8 Verbilligte Vermietung (`verbilligt.py`)

Per Wohnung (D71: the flat for an ETW let by rooms, each unit for an MFH): agreed Warmmiete
= Σ `cold_rent + operating_costs_prepayment` of its tenancies in force on 1 January (or each
tenancy's start); an `included` tenancy's whole rent is warm already (D72). Ortsübliche
Warmmiete = the `market_rent` input in force (`cold_rent_per_m2`, the Mietspiegel's net cold
rent; "in force" = not superseded or withdrawn, with `valid_from` ≤ 31 Dec of the year, the
same test as the checklist's §8, so a value valid from mid-year counts for that year) × the
Wohnung's area + the year's umlagefähige costs from the ledger as a monthly figure (Σ confirmed
`hausgeld_umlagefaehig`, `grundsteuer`, `insurance` ÷ months owned);
`ratio` = agreed ÷ ortsüblich. ≥ 66 %: nothing. 50 % ≤ ratio < 66 %: warning `reduced_rent.prognosis_needed`.
< 50 %: warning `reduced_rent.partial` (params `cut_percent`) and `reduced_rent.cut_percent` = round((1 − ratio) ×
100) proposed for Z87 (the Finanzamt cuts, the draft does not, D61). No input: warning
`reduced_rent.no_market_rent` (information) and the checklist item `market_rent` missing.

Worked example (`verbilligte_vermietung.yaml`): 62.50 m² at 12.80 €/m² cold → 800.00, plus 200.00 umlagefähige costs a month → 1,000.00;
agreed 1,030.00 → 103 %, nothing; agreed 620.00 → 62 %, prognosis; agreed 450.00 → 45 %,
cut 55 %. The 200.00 are the §14 flat's 1,200.00 of umlagefähige costs over its 6 months owned.
Case `market_rent_valid_from_mid_year`: a value of 11.20 €/m² valid from 1 Sep → 700.00 +
200.00 = 900.00; agreed 1,030.00 → 114.4 %, nothing.

### 5.9 §23 date (`spekulation.py`)

`tax_free_from(notarized_on, rule_set) = notarized_on + 10 years + 1 day` (both ends are the
obligatory contracts). Shown on the Steuern screen and written into each export's metadata
with the AfA claimed that year (D68). The gain and the lever are phase 4.

### 5.10 Lines before the handover (`vorab.py`)

`split(facts: DraftFacts, tax_year, rule_set) -> RuleLines`, like the other line rules (§7).
Income allocations (`rent_cold`, `operating_costs_prepayment`, `operating_costs_settlement`,
`other_income`) with `booking_date < purchase_date`: with the property's
`pre_handover_income` input = `rent` → one unit row for the flat with `amount` =
`pre_handover` = their sum (each allocation and the input are its sources, listed under
"davon vor dem Übergang"); `draft` merges it into the flat's `income.rent_units` row. Without
the input → `pre_handover` block: their ids, and warning `income.pre_handover` (params
`amount`, `count`) (D63, Q1 decided 9 Nov 2026). `vorab.yaml` `rent_input_moves_to_rent_row`
and `anlage_v_2026.yaml` carry both: case A without the input (the block as below), and case
`pre_handover_as_rent` with it, where the 850.00 move onto the rent row and the warning is
gone (the two `income.other_review` warnings stay, §5.7). Cost allocations before the handover stay in their
lines. A property whose `purchase_date` was corrected after lines were booked is handled by
the same test on the current value (append-only history keeps the old one).

### 5.11 Assembling the draft (`draft.py`)

`draft(facts: DraftFacts, tax_year, rule_set) -> AnlageVDraft`.

`DraftFacts` (built by `services/tax.py`, no tenant names, D5): the property with units,
ownership shares, selected split, cost rows and the Grundsteuerbescheid reference; loans with
their schedules' due dates; tenancies with rent rows, `rent_due_day` and unit; confirmed
allocations of the property's scope with a non-zero amount (D70) with their transaction's dates, counterparty IBAN hash,
document id and invoice net; `tax_input` rows; the checklist facts; statement coverage
(`covered_through` per account that holds any of the property's allocations); counts and sums
of proposed and unmatched lines of the year.

Steps: assign each allocation its tax year (§5.1); keep the ones of `tax_year`; route by
`category_lines` and the inputs; run §5.2–5.10; sum lines; apply the owners' shares; build
`sources` per line (`SourceRef {kind: allocation | transaction | document | property |
price_split | acquisition_cost | loan | tax_input | rule, id, amount}`); collect warnings
(`code`, `severity` info | warning, params, refs); attach the checklist items as
`missing_documents`; compute coverage (`months_owned` from the handover month to December or
the end of ownership; `months_covered` = months whose last day ≤ `covered_through`).

Output `AnlageVDraft`: `rule_set {tax_year, version, form_year, form_status}`, `header`,
`lines[]` (`key, zeile, kennzahl, label, amount, amount_rounded, sources, notes, provisional`),
`unit_rows[]`, `totals {income, costs, result}`, `owners[] {owner_id, share, amount,
amount_rounded}`, `tracked_not_deductible[]`, `moved_to_other_year[]`, `pre_handover[]`,
`excluded_open {count, sum}`, `warnings[]`, `missing_documents[]`, `limit_15`, `coverage`,
`tax_free_from`.

Rounding (D60) happens once, in `rounding.py`, producing `amount_rounded`; the cent figures are
the draft.

## 6. Data model: migration `0013_tax_year`

| Table / column | Holds | Mutability |
| --- | --- | --- |
| `tenancy.rent_due_day smallint NOT NULL DEFAULT 3 CHECK (1..28)` | D67 | Update + `tenancy.updated` |
| `property.living_area_m2 numeric(7,2) NULL CHECK (> 0)` | D71, the flat's Wohnfläche for Z11 and Z13 | Update + `property.updated` |
| `tenancy.operating_costs_terms text NOT NULL DEFAULT 'prepayment' CHECK in (`prepayment`, `flat_rate`, `included`)` | D72 | Update + `tenancy.updated` |
| `tax_input` | `property_id` NOT NULL, `kind` CHECK in (`spread_82b`, `renovation_treatment`, `exclude_from_15pct`, `market_rent`, `ruecklage_spent`, `interest_certified`, `pre_handover_income`), `tax_year smallint` (CHECK ≥ 2000), `unit_id`, `loan_id`, `allocation_id`, `document_id` (all nullable FKs with `workspace_id`), `value jsonb NOT NULL` (typed per kind, §7), `note text`, `supersedes_id` (FK self), `withdrawn boolean NOT NULL DEFAULT false`, `recorded_at`, `recorded_by` | **Append-only** (`forbid_mutation` trigger like `transaction`); per-kind scope CHECKs: `spread_82b`, `renovation_treatment`, `exclude_from_15pct` need `allocation_id`; `market_rent` needs the property (the flat, D71) or a `unit_id` (an MFH unit); `ruecklage_spent` needs `tax_year` and `document_id`; `interest_certified` needs `loan_id`, `tax_year` and `document_id`; `pre_handover_income` needs the property only (value `rent`, D63); `withdrawn` needs `supersedes_id` |
| `tax_export` | `property_id`, `tax_year`, `kind` CHECK in (`field_sheet`, `archive`), `rule_set_version text`, `form_year smallint`, `form_status text`, `draft jsonb NOT NULL` (the `AnlageVDraft`), `file_sha256` (FK `document_file (workspace_id, sha256)`), `size_bytes`, `created_at`, `created_by` | **Immutable** (`forbid_mutation`); deleted by the retention sweeper with the year's tax records (10 years after `tax_year`) |

RLS, `workspace_id`, grants and the `tables.py` mirror as every table since 0001. No change to
`event.entity_type`: the new events sit on `property` (and `tenancy`). No change to
`allocation.category` or any CHECK of 0012 (D56 chose an input over a category). Downgrade:
drops the two tables and the three columns; refused while any `tax_export` row exists (an export is
a record of what was handed over and is never rewritten). As built in step 0 the guard is
stricter: it also refuses while any `tax_input` row exists, any `property.living_area_m2` is
set, or any tenancy has a `rent_due_day` other than 3 or `operating_costs_terms` other than
`prepayment`, because dropping them would lose what a person typed.

`tax_input.document_id` references `document` like `allocation.document_id` does (no
`ON DELETE`), so a future retention sweep that deletes a Zinsbescheinigung or
Hausgeldabrechnung must first handle the inputs that cite it (the year's inputs share the
document's 10-year hold, so in practice they expire together).

**Events added**

| Type | Payload | Effective date |
| --- | --- | --- |
| `property.tax_input_recorded` | `tax_input_id`, `kind`, `tax_year`, scope ids, `value` (amounts and codes only), `supersedes_id`, `withdrawn` | forbidden |
| `property.anlage_v_exported` | `tax_export_id`, `tax_year`, `kind`, `rule_set_version`, `form_year`, `form_status`, `file_sha256`, `size_bytes`, `result` (the cent result), `document_count` (archive) | forbidden |
| `tenancy.updated` (existing `Updated` shape) | gains fields `rent_due_day`, `operating_costs_terms` | forbidden |
| `property.updated` (existing `Updated` shape) | gains field `living_area_m2` | forbidden |

**Invariants (new) and where they are enforced**

| Invariant | Enforced by |
| --- | --- |
| A tax input row is never changed; a correction supersedes it | trigger + schema (`supersedes_id` on create only) |
| Each kind has its scope | CHECKs above + `TaxInputCreate` validator |
| An export's draft equals what `draft()` returned at that moment, under the version it names | the service stores the draft it just computed in the same transaction as the file; `anlage_v_2026.yaml` `export_roundtrip` case |
| The field sheet never contains a tenant's name, IBAN or the counterparty text | `services/tax_export_files.py` builds it from `AnlageVDraft` only, which has none (D5); `tests/api/test_tax_exports.py` (names, IBANs, purpose texts) and `tests/test_tax_export_files.py` (full IBANs) |
| A rule set names a line for every category | load-time check (`FormMapping` validator) + `tests/rules/test_tax_rulesets.py` |

## 7. Schemas (new and changed modules; the spec PR writes them)

Money stays a decimal string on the wire (phase 1 amendment 2); all models extend `Schema`.

```python
# schemas/tax.py (besides §4.1)
class TaxInputKind(StrEnum): SPREAD_82B, RENOVATION_TREATMENT, EXCLUDE_FROM_15PCT, MARKET_RENT, RUECKLAGE_SPENT, INTEREST_CERTIFIED, PRE_HANDOVER_INCOME
class Spread82b(Schema): years: int = Field(ge=2, le=5)
class RenovationTreatment(Schema): treatment: Literal["erhaltung", "herstellung"]
class ExcludeFrom15Pct(Schema): reason: Literal["annual_maintenance", "extension", "damage_after_acquisition"]
class MarketRent(Schema): valid_from: date; cold_rent_per_m2: PositiveMoney; source: Literal["mietspiegel", "gutachten", "portal", "other"]
class RuecklageSpent(Schema): amount: NonNegativeMoney            # tax_year + document_id on the row
class InterestCertified(Schema): amount: NonNegativeMoney         # loan_id + tax_year + document_id on the row
class PreHandoverIncome(Schema): treatment: Literal["rent"]       # D63
class TaxInputCreate(Schema): property_id; kind; tax_year | None; unit_id | None; loan_id | None; allocation_id | None;
    document_id | None; value: Spread82b | … | PreHandoverIncome;
    note | None; supersedes_id | None; withdrawn: bool = False      # validator: value type and scope by kind
class TaxInput(TaxInputFact): recorded_at; recorded_by            # not Record: the table names its timestamp recorded_at
# The warning codes are a StrEnum WarningCode with a fixed WARNING_SEVERITY map (info | warning),
# decided in step 0; a DraftWarning whose severity differs from the map fails validation.

class PaymentFacts(Schema): allocation_id; booking_date; value_date | None; amount; category; recurring; service_period | None; due_on | None
class TaxYearResult(Schema): year: TaxYear; rule: Literal["paid", "short_period_shift"]; notes: list[str]
class AfaFacts / AfaLine / LimitStatus / DraftFacts / SourceRef / DraftLine / DraftWarning / AnlageVDraft   # §5
class TaxExportKind(StrEnum): FIELD_SHEET, ARCHIVE
class TaxExportCreate(Schema): tax_year; kind
class TaxExport(Record): property_id; tax_year; kind; rule_set_version; form_year; form_status; file_sha256; size_bytes; created_by
```

Changed: `tenancy.py` (`TenancyCreate.rent_due_day: int = 3`, `Tenancy`, the API patch);
`checklist.py` (`RequirementCode` + `COST_RECEIPT`, `MARKET_RENT`; `ChecklistScopeType` +
`ALLOCATION`, `UNIT`; `ChecklistFacts` + `allocations: list[ChecklistAllocation]`,
`market_rents: list[ChecklistMarketRent] | None = None`, where None means a phase 1 caller
that yields no `market_rent` item; `ChecklistScope` + `category`, `booking_date`);
`events.py` (the two payloads, `tenancy.updated` field); `proposal.py` (`ProposalRecord.
entity_type` + `"tax_input"`); `property.py` (`living_area_m2`); `enums.py` gains
`OperatingCostsTerms` (D72; this line said "none planned", written before D72 added the
CHECK'd set).

The line rules share one signature, `<module>.lines(facts: DraftFacts, tax_year, rule_set) ->
RuleLines` (`vorab.split` too; `verbilligt.check` takes the same arguments and returns a
`ReducedRentResult`); `RuleLines` holds lines, unit rows, tracked amounts, basis additions,
pre-handover ids, warnings and `excluded_open`, which `draft` merges.

## 8. Document checklist extension (`rules/document_checklist.py`, data entries)

| Code | Scope | Satisfied by | Due | Applies |
| --- | --- | --- | --- | --- |
| `cost_receipt` | one per confirmed allocation of the year (by the §5.1 year) with category `repairs`, `renovation`, `loan_fees` or `professional_fees` and a non-zero amount (D70) | the allocation's `document_id` (new satisfier `ALLOCATION_DOCUMENT`) | always | — |
| `market_rent` | one per Wohnung (D71) with a tenancy overlapping the year | a `market_rent` input of that Wohnung (property, or unit for an MFH) with `valid_from` ≤ 31 Dec of the year, not withdrawn (new satisfier `TAX_INPUT`) | always | — |

Existing entries keep their rules; `zinsbescheinigung` and `hausgeldabrechnung` now also feed
§5.5 and §5.6. Statuses, order and the "owned in the year" test are unchanged; the two new
entries follow `hausgeldabrechnung`, `cost_receipt` ordered by booking date. Fixture
`checklist_phase3.yaml` (§13); the phase 1 fixture's outcomes do not change (its facts carry
no allocations, and `market_rents` is None for a phase 1 caller, so the new entries yield no
items even for a let unit).

## 9. Reading the two certificates (the one model touchpoint)

Both extraction kinds exist since phase 2 (`ZinsbescheinigungExtraction.interest_paid`,
`HausgeldabrechnungExtraction.ruecklage_spent`, `settlement_result`, …); phase 2's pre-fill
only typed and linked these documents. Step 2 extends `rules.prefill.build_proposal`:

| Kind | Records proposed | Fields and their source |
| --- | --- | --- |
| **Zinsbescheinigung** | `typing` (`tax_year`); `link:loan` (by lender and contract number, else the property's only loan, else picked in the review); `tax_input:interest_certified` {loan, tax_year, amount ← `interest_paid`} | each with page and quote; applied through `services/tax_inputs.create` |
| **Hausgeldabrechnung** | `typing` (`tax_year`); `link:property` (by address); `tax_input:ruecklage_spent` {tax_year, amount ← `ruecklage_spent`}; the settlement result and the yearly totals stay in the extraction and are shown beside the Hausgeld lines | same |

The user confirms, edits or clears the value in the existing review screen; nothing reaches
`tax_input` without apply (D14). Evals: `make_docs.py` gains the two synthetic documents,
`record.py` records them (D66); checks: `tax_year`, `contract_number`, `interest_paid`;
`tax_year`, `settlement_result`, `ruecklage_contribution`, `ruecklage_spent`.

## 10. API

| Route | Returns | Notes |
| --- | --- | --- |
| `GET /api/properties/{id}/anlage-v?tax_year=Y` | `AnlageVDraft` | 422 outside the checklist's `available_years`; computed on read |
| `GET /api/properties/{id}/tax-inputs?tax_year=Y&kind=` | `list[TaxInput]` | current rows (not superseded, not withdrawn) plus `include_history=true` |
| `POST /api/properties/{id}/tax-inputs` | 201 `TaxInput` | `TaxInputCreate`; event; `supersedes_id` must be a current row of the same kind and scope (`tax_input.not_current`) |
| `PATCH /api/tenancies/{id}` | existing | gains `rent_due_day` |
| `POST /api/properties/{id}/anlage-v/exports` | 202 `{export_id}` | `TaxExportCreate`; job `tax_export(workspace_id, export_id)`, lock `export:{id}` |
| `GET /api/tax-exports/{id}` | `TaxExport` + `download_url` (signed, short-lived) + `status` | status from the job (`queued`, `ready`, `failed` with `error_code`) |
| `GET /api/properties/{id}/anlage-v/exports?tax_year=Y` | `list[TaxExport]` | newest first |
| `GET /api/properties/{id}/checklist?tax_year=Y` | existing | gains the two entries |

Error codes: `tax.no_rule_set`, `tax.year_not_available`, `tax_input.scope_invalid`,
`tax_input.not_current`, `tax_input.allocation_not_confirmed`, `tax_export.failed`,
`tax_export.too_large` (archive over 100 MiB, D77: split by hand), `form_mapping.incomplete`.

## 11. Screens

- **Sidebar** gains **Steuern** after Konten (the household design's order; the current
  sidebar has no Steuern entry). Route `/tax` → the current year; `/tax/[year]` with a
  property selector (hidden with one property) and tabs **Anlage V** (built), **KAP**, **SO**
  (disabled, "ab Phase 8").
- **Anlage V tab**, layout per `docs/mockups/04-tax-year.png`, German labels, numbers from the
  backend only: header ("Steuerjahr 2026 · Anlage V (Entwurf)", address); the progress card
  ("Monate abgestimmt 5 von 6", "Eigentum seit 07/2026 · Dezember-Auszug fehlt · 2 Zeilen in
  Zu prüfen nicht enthalten") and the three figures; the **Einnahmen** card and the
  **Werbungskosten** card with one row per line (label, sublabel such as "6 × 850,00 € ·
  Jul–Dez" or "234.000,00 € × 2 % × 6/12", amount, "n Zahlungen" / "Kaufvertrag + Aufteilung"
  link opening the **sources drawer**); the grey footer "Erfasst, aber dieses Jahr nicht
  abziehbar: Erhaltungsrücklage 600,00 € · Tilgung 1.508,77 € · ins Jahr 2027 verschoben:
  1.030,00 €"; right column: **Offene Punkte** (new card the mockup lacks: every warning with
  its action link — "Renovierung 4.760,00 € einordnen", "Ortsübliche Miete eintragen",
  "Zinsbescheinigung weicht ab"), **Unterlagen für diese Erklärung** (the phase 1 checklist
  component with the new entries), **15 %-Grenze** (used, limit, window end, estimated
  marker), **Übergabe** (two buttons, the export history with date, version and download),
  and the footer "Ein Entwurf zum Prüfen, keine Steuerberatung. Jede Zahl führt zu ihren
  Zahlungen und Belegen." A provisional form mapping shows a banner (§4.3).
- **Sources drawer**: the line's allocations (date, amount, category, masked counterparty,
  period) linking to the transaction and the Beleg; for the AfA line the price, each cost row
  and the split with its evidence; for a certificate figure the document and its quote.
- **Inputs**: on an allocation in the review panel (`components/allocations.tsx`): "Auf 2–5
  Jahre verteilen (§ 82b EStDV)", "Renovierung ist: Erhaltungsaufwand / Herstellungskosten",
  "Zählt nicht zur 15 %-Grenze (Grund)"; on the unit page: "Ortsübliche Miete (kalt, €/m², netto laut Mietspiegel),
  gültig ab, Quelle, Beleg"; on the tenancy form: "Miete fällig am Tag" (default 3). The
  Offene Punkte card links to each.
- **Property page**: the Unterlagen tab shows the new entries automatically; the property
  header shows "§ 23: steuerfrei ab TT.MM.JJJJ".

## 12. Exports (`services/tax_export.py`, job `tax_export`)

- **Field sheet** ("ELSTER-Ausfüllhilfe Anlage V 2026", PDF): the header lines; a table
  Zeile | Kennzahl | Bezeichnung | Betrag (Cent) | Eintrag (volle Euro) | Quellen (count); the
  unit rows (the first on Z13, further ones on Z14, §4.2); the AfA-Erläuterung as an annex (price, cost rows, split and its source,
  rate, months, additions) because Z33 asks for it in the first year; warnings and open
  items; "Formularstand" (form year and status); rule set version, export id and SHA-256;
  the disclaimer. No names, no IBANs, no purpose texts (§6 invariant).
- **Archive** (ZIP `anlage-v-<year>-<property label>.zip`): `ausfuellhilfe.pdf`;
  `anlage-v-<year>.csv` (one row per draft line with key, Zeile, Kennzahl, cents, rounded);
  `zahlungen.csv` (every allocation of the year: booking date, amount, category, line key,
  period, Beleg file name; counterparty as masked IBAN); `dokumente/` with the originals of
  every document the checklist matched for the year and every Beleg, named `<date> <type>
  <title>.<ext>` (phase 2's download names), untouched; `README.txt` with the metadata and
  the list of documents that need a Finanzamt copy: XFA forms (`ai.documents.is_adobe_only_form`)
  and PDFs whose pages carry `Ink`, `FreeText` or `Stamp` annotations (pypdf reads the
  annotation subtypes). Making the copies is open question 8.
- PDF writing needs a library the backend lacks (pypdf only reads and merges). Choice for
  the spec PR: **reportlab** (BSD, no system packages in the container; added in step 0. It
  pulls in pillow, which installs as a prebuilt binary wheel, so "pure Python" holds for
  reportlab itself only);
  the alternative is an HTML sheet printed by the browser, which saves the dependency but
  cannot be stored as the snapshot's bytes (Q12).
- Both exports write the `tax_export` row and the file in one transaction with the event;
  the file is content-addressed in `document_file` under the workspace prefix, served by the
  existing signed URL code.

## 13. Rule contracts and fixtures (`tests/fixtures/phase3/`)

Pure functions in `hausbuch.rules.tax.*`, no I/O, no model calls; errors carry a `code`.
Synthetic numbers only; `tests/fixture_models_phase3.py` loads every file through the §7
schemas (a fixture that does not match the schemas fails before any rule is implemented).

| Function | Fixture | Cases |
| --- | --- | --- |
| `tax_year.tax_year_of(payment, rule_set) -> TaxYearResult` | `tax_year.yaml` | the nine cases of §5.1, plus `due_missing_no_shift` (recurring, no due date → year paid, note) |
| `afa.afa(facts, tax_year, rule_set) -> AfaLine` | `afa.yaml` | `etw_1998_handover_july` (2,340.00 / 4,680.00), `build_1920_rate_2_5`, `build_2023_rate_3`, `handover_january_full_year`, `handover_december_one_twelfth`, `nebenkosten_in_basis` and `nebenkosten_in_basis_none` (basis with and without the three cost rows, two cases), `herstellung_addition_2027`, `limit_crossing_additions_gross` (2026 2,399.50, 2027 5,655.80, D73), `no_split_empty_line`, `cost_row_unpaid_warning`, `acquisition_cost_without_row_not_added`, `purchase_price_mismatch_warning` |
| `anschaffungsnah.limit_status(facts, tax_year, rule_set) -> LimitStatus` | `anschaffungsnah.yaml` | `under_limit_14_percent`, `crossing_in_second_year` (prior-year amendment, basis additions by year paid), `equal_is_not_exceeded`, `outside_window_not_counted`, `estimated_net_without_invoice`, `excluded_annual_maintenance`, `herstellung_counted`, `herstellung_extension_excluded`, `herstellung_counted_not_re_added` (D74), `window_dates` (2026-07-01 → 2029-07-01, D75), `zero_amount_is_not_evidence` (a confirmed 0.00 `repairs` row: not listed, not counted), `performed_in_window_paid_after`, `no_period_booked_after_window_warns` (D76) |
| `erhaltungsaufwand.lines(...)` | `erhaltungsaufwand.yaml` | `repairs_full_deduction`, `renovation_elected_erhaltung`, `renovation_elected_herstellung`, `renovation_undecided_excluded`, `spread_three_years` (2,333.33 / 2,333.33 / 2,333.34), `spread_sale_in_second_year`, `spread_withdrawn_back_to_full` |
| `schuldzinsen.lines(...)` | `schuldzinsen.yaml` | `interest_only_phase_plus_annuities` (3,724.56 / 1,508.77), `certificate_matches`, `certificate_mismatch_0_56`, `january_annuity_moved` (debited 30 Dec, certificate counts it in the new year), `financing_costs_before_handover`, `two_loans_two_einzelangaben` |
| `hausgeld.lines(...)` | `hausgeld.yaml` | `six_months_split`, `ruecklage_tracked_not_deducted`, `ruecklage_spent_confirmed_350`, `abrechnung_pending_provisional`, `settlement_backpayment_next_year` |
| `einnahmen.lines(...)` | `umlagen.yaml` | `rent_and_prepayments_six_months`, `settlement_backpayment_positive`, `refund_negative`, `pauschale_is_umlagen`, `included_rent_on_z13`, `all_included_ticks_z24`, `mixed_terms_warns`, `two_rooms_one_flat_row`, `mfh_two_units_two_rows`, `living_area_missing_falls_back`, `other_income_with_review_warning`, `deposit_not_income` |
| `verbilligt.check(...)` | `verbilligte_vermietung.yaml` | `ratio_103_nothing`, `ratio_62_prognosis`, `ratio_45_cut_55`, `no_market_rent_info`, `market_rent_valid_from_mid_year` |
| `vorab.split(...) -> RuleLines` | `vorab.yaml` | `seller_forwarded_rent_excluded` (2 × 425.00 → block 850.00), `interest_before_handover_deducted`, `purchase_date_corrected_later`, `rent_input_moves_to_rent_row` (with the `pre_handover_income` input: a unit row of 850.00) |
| `spekulation.tax_free_from(...)` | `spekulation.yaml` | `ten_years_plus_one_day`, `leap_day_contract` (29 Feb 2024 → 1 Mar 2034) |
| `registry.rule_set(year)` and `FormMapping` | `tests/rules/test_tax_rulesets.py` (a Python test, not a YAML fixture; step 0) | every category once per form year (2025 published, 2026 provisional); every line key the draft writes exists; Zeilen and Kennzahlen of 2025 as in §4.2; a mapping missing a category fails with `form_mapping.incomplete`; `spread_prior_years` relative to the tax year |
| `document_checklist.build_checklist` (extended) | `checklist_phase3.yaml` | `cost_receipt_missing_for_repair`, `cost_receipt_present`, `cost_receipt_only_for_named_categories`, `market_rent_missing_for_let_unit`, `market_rent_present`, `market_rent_withdrawn_is_missing`, `zero_amount_is_not_evidence` (a confirmed 0.00 `repairs` row without a document → no item), `phase1_facts_unchanged` (phase 1 facts → the same items as before) |
| `draft.draft(facts, tax_year, rule_set) -> AnlageVDraft` | `anlage_v_2026.yaml` | §14 |

Error codes added: `tax.no_rule_set`, `tax.year_not_available`, `tax.due_date_unresolved`
(service, when a loan allocation has no schedule entry: the line keeps the year paid and a
note), `form_mapping.incomplete`, `tax_input.scope_invalid`, `tax_input.not_current`,
`tax_input.allocation_not_confirmed`, `tax_export.failed`, `tax_export.too_large`.

Warning codes (data in the draft, German text in the frontend): `year_end_straddle`,
`afa.no_split`, `afa.cost_row_unpaid`, `afa.acquisition_cost_without_row`,
`afa.purchase_price_mismatch`, `limit.exceeded`, `limit.prior_year_amendment`,
`limit.estimated_net`, `anschaffungsnah.performance_date_check` (D76), `renovation.undecided`, `interest.certificate_mismatch`,
`hausgeld.abrechnung_pending`, `income.other_review`, `income.pre_handover`,
`income.living_area_missing` (D71), `umlagen.mixed_terms` (D72),
`reduced_rent.no_market_rent`, `reduced_rent.prognosis_needed`, `reduced_rent.partial`,
`draft.open_lines_excluded`, `draft.form_provisional`, `draft.allocation_without_property`,
`archive.finanzamt_copy_needed` (Q8). Severity per code is fixed in `WARNING_SEVERITY`
(step 0): **info** for `year_end_straddle`, `afa.cost_row_unpaid`,
`afa.purchase_price_mismatch`, `limit.estimated_net`, `hausgeld.abrechnung_pending`,
`reduced_rent.no_market_rent`, `draft.form_provisional`, `archive.finanzamt_copy_needed`;
**warning** for the rest.

## 14. Done criterion as fixtures

**`anlage_v_2026.yaml`** is the hand-calculated return. Its facts (all synthetic; the mockup's
figures are sample data and are not used, per `docs/mockups/README.md`):

- Property "Wohnung Beispielweg", Beispielweg 7, 12345 Musterstadt, Grundsteuer Aktenzeichen
  999/123/45678, ETW, completed 1998, Kaufvertrag 10 Mar 2026, handover 1 Jul 2026, price
  300,000.00, `living_area_m2` 62.50; cost rows Grunderwerbsteuer 19,500.00 (paid from the
  rental account 20 Apr), Notar 4,200.00 (paid privately 25 Mar: no allocation), Grundbuch
  1,300.00 (paid 12 May); split from the contract 0.720000; one unit "WE 7", 62.50 m²; owners
  A and B 1/2 each; every phase 1 document present (the checklist's phase 1 part is the
  existing `build_checklist`'s output for these facts); drafted on 15 Jan 2027, after the
  Zinsbescheinigung arrived on 12 Jan.
- Tenancy from 1 Jul 2026, Kaltmiete 850.00, Nebenkostenvorauszahlung 180.00, due day 3.
- Loan "Beispielbank" 240,000.00 at 3.5 %, 1,000.00 a month, paid out 29 Jun, first annuity
  1 Aug, interest-only before.
- Confirmed allocations of 2026 that reach no form line (added in step 0 so the draft is
  tested against a realistic purchase year): owner top-up 85,000.00 on 15 Apr
  (`owner_transfer_in`); Grunderwerbsteuer −19,500.00 on 20 Apr and Grundbuch −1,300.00 on
  12 May (`acquisition_cost`, each in its cost row's `paid_by`); loan payout +240,000.00 on
  29 Jun (`loan_disbursement`); purchase price −300,000.00 on 30 Jun (`purchase_price`, equal
  to the price, so no mismatch warning).
- Confirmed allocations of 2026: rent 2 Jul – 2 Dec 6 × (850.00 + 180.00); rent for 01/2027
  booked 29 Dec (850.00 + 180.00); seller's forwarded rent share 425.00 on 5 May and 5 Jun
  (`other_income`); deposit 1,700.00 on 2 Jul; Hausgeld 1 Jul – 1 Dec 6 × 320.00 split
  180.00 / 40.00 / 100.00; interest-only debit 233.33 on 30 Jul; annuities 1 Aug – 1 Dec with
  interest 700.00, 699.13, 698.25, 697.37, 696.48 and Tilgung 300.00, 300.87, 301.75, 302.63,
  303.52; Notar Grundschuld 480.00 on 15 Jun (`loan_fees`); Grundsteuer 60.00 on 17 Aug and
  16 Nov; painter 1,190.00 on 21 Sep (`repairs`, invoice net 1,000.00); bathroom tiles
  4,760.00 on 6 Oct (`renovation`, invoice net 4,000.00); Sondereigentumsverwaltung 6 × 25.00;
  bank fees 9 × 4.90 on the last banking day of each month from 30 Apr (the last on 30 Dec); a confirmed 0.00 line on 30 Jun booked `other_expense` by
  the model (D52) and one on 31 Oct booked `repairs`, both without a document; two proposed
  lines in December summing −89.90 (not confirmed).
- Inputs: `renovation_treatment = erhaltung` for the tiles (case A; case B has none);
  `interest_certified` 3,724.56 for 2026; no `ruecklage_spent` yet (the Abrechnung arrives in
  2027); no `market_rent`.
- Coverage: reconciled imports through 30 Dec 2026, the last bank line. Corrected in step 0:
  this said 30 Nov, but the facts confirm December lines (the rent of 29 Dec, the bank fee of
  30 Dec), and a line reaches confirmation only from a reconciled statement (phase 2), so the
  statements must cover them. December is still not fully covered (its last day, 31 Dec, is
  after `covered_through`), so the result stays 5 of 6 months.

Expected draft 2026, case A (cents):

| Line | Key | Amount | Sources |
| --- | --- | --- | --- |
| Z13/15 (Kz 01) | `income.rent_total` | 5,100.00 | 6 allocations |
| Z20 (Kz 04) | `income.umlagen` | 1,080.00 | 6 allocations |
| Z32 | `income.total` | **6,180.00** | |
| Z33/35 (Kz 30) | `wk.afa` | 2,340.00 | 9: property, split and its Kaufvertrag, 3 cost rows and their 3 documents (234,000.00 × 2 % × 6/12), method code 2 |
| Z46/48 (Kz 33) | `wk.interest` | 3,724.56 | 6 allocations; Einzelangaben "Beispielbank"; certificate matches |
| Z49/51 (Kz 34) | `wk.financing_costs` | 480.00 | 1 allocation + invoice |
| Z55 (Kz 36) | `wk.maintenance` | 5,950.00 | 2 allocations + 2 invoices + the tiles' `renovation_treatment` input; `provisional` (Rücklage-Entnahme pending) |
| Z73/75 (Kz 52) | `wk.allocable_costs` | 1,200.00 | 6 Hausgeld parts + 2 Grundsteuer |
| Z76/78 (Kz 48) | `wk.non_allocable_costs` | 434.10 | 6 Hausgeld parts + 6 Verwaltung + 9 bank fees |
| Z83 | `wk.total` | **14,128.66** | |
| Z85 | `result.surplus` | **−7,948.66** | |
| Z86 (Kz 20 / 21) | owners A / B | −3,974.33 / −3,974.33 | |

Rounded for the field sheet (D60): income 6,180; costs 2,340 + 3,725 + 480 + 5,950 + 1,200 +
435 = 14,130; result **−7,950**; owners −3,975 each.

Also asserted: the Z13 unit row for the flat (62.50 m², from `living_area_m2`, 5,100.00);
tracked not deductible Rücklage 600.00 and Tilgung 1,508.77; moved to 2027: 1,030.00 (one
bank line, two allocations of 850.00 and 180.00, rule `short_period_shift`); pre-handover
block 850.00 (two lines);
excluded open: 2 lines, −89.90; coverage 5 of 6 months; limit 15 %: 5,000.00 of 35,100.00
(14.2 %), window to 1 Jul 2029 (D75), not estimated; `tax_free_from` 11 Mar 2036; warnings exactly
`afa.cost_row_unpaid` (Notar), `hausgeld.abrechnung_pending`, `income.other_review` ×2,
`income.pre_handover`, `reduced_rent.no_market_rent`, `draft.open_lines_excluded`,
`draft.form_provisional`; missing documents: `hausgeldabrechnung` expected (due 30 Jun 2027),
`zinsbescheinigung` present, `market_rent` missing, `cost_receipt` present ×3 (painter, tiles,
Notar Grundschuld) and none for the two 0.00 rows (D70), which also count nowhere, raise no
warning and are not among the excluded open lines.

The fixture's case ids are `case_a`, `case_b_renovation_undecided`, `pre_handover_as_rent`
and `export_roundtrip`.

Case B (`case_b_renovation_undecided`, no renovation input): Z55 1,190.00; Z83 9,368.66
(rounded 9,370); Z85 −3,188.66 (−3,190); owners −1,594.33 each (−1,595); warning
`renovation.undecided` with 4,760.00; excluded open 3 lines, −4,849.90 (the tiles join the
two proposed lines; step 0 added this figure); the 15 % tracker still counts 4,000.00 net
(undecided counts).

Case `pre_handover_as_rent` (added in step 0, D63 with Luis's answer to Q1): case A plus the
`pre_handover_income` input. The flat's row and Z15 become 5,950.00 (of which 850.00 before
the handover; sources the eight rent allocations and the input); income 7,030.00; result
−7,098.66 (rounded −7,100); owners −3,549.33 each (−3,550); the block and its warning are
gone, the two `income.other_review` warnings stay.

Case `export_roundtrip`: the draft serialised and loaded again equals itself, and the field
sheet rows (key, Zeile of the amount, Kennzahl, cents, rounded; 14 rows, the flat's single
unit row on Z13, the block's first line, §4.2) match the table above under rule set `2026.1`, form 2025 provisional.

"Every line opens its sources" is asserted by the fixture (each line's `sources` non-empty
except `income.total`, `wk.total`, `result.*`) and by the sources drawer (step 1's done
criterion).

## 15. Implementation steps

Each step shippable; review per `.claude/WORKING_AGREEMENT.md` (money, persisted state,
migrations, events, fixtures).

| Step | Delivers | Migration | Done when | Size | Review |
| --- | --- | --- | --- | --- | --- |
| 0 **Spec PR** | this doc; `schemas/tax.py` and the §7 changes; migration 0013 written and tested against Postgres 16; `rulesets/2025.yaml`, `2026.yaml`; the 12 fixtures and `fixture_models_phase3.py` (no skipped rule-test stubs: the rule tests come with the rules); the form mapping pinned by `tests/rules/test_tax_rulesets.py`; `tables.py` mirror; reportlab dependency; ARCHITECTURE edits §16 | written, not deployed | schema tests green (table count + exclusion list), fixtures validate, `uv run pytest` green. **Done 9 Nov 2026** (`e16002b` schemas and migration, `d139528` rule sets, `b4390ad` fixtures); reviewed once: arithmetic, 2025 form mapping and migration correct, D73 confirmed, five findings fixed in `a1613cb` (D74–D76, unit row on Z13, the kitchen example); merged in PR #79 and migration 0013 applied to production the same day; images `0cce7818549b` (API and web) deployed, health check 200 | M: 1 coder | **Yes**: FOCUS the 2025 Zeilen/Kennzahlen against the form, the worked examples' arithmetic, the migration's CHECKs and trigger |
| 1 **Rule set, draft, Steuern screen** | `rules/tax/*` (§5), `services/tax.py` (facts, due dates, coverage), `GET …/anlage-v`, sidebar entry, `/tax/[year]` with lines, sources drawer, tracked/moved/pre-handover blocks, Offene Punkte, 15 % card, checklist card | **0013** applied (tables empty) | `tax_year`, `afa`, `anschaffungsnah`, `schuldzinsen`, `hausgeld`, `umlagen`, `vorab`, `spekulation`, `anlage_v_2026` fixtures pass (the form mapping is already pinned in step 0); Luis's real 2026 draft shows every confirmed line and each opens its sources | L: 2 coders (rules + service; screen) | **Yes** (money): FOCUS the ten-day rule's boundaries, the AfA months, nothing counted twice (D55). **Done 9 Nov 2026**: reviewed, three findings fixed, merged and deployed in PR #81; the real-data check waits for Luis |
| 2 **Inputs and reading** | `services/tax_inputs.py` + routes + events; the input controls (§11); `rent_due_day`; §82b, renovation, exclusion, market rent; pre-fill of Zinsbescheinigung and Hausgeldabrechnung into `tax_input` proposals; the two recordings; checklist extension; verbilligte Vermietung | — | `erhaltungsaufwand`, `verbilligte_vermietung`, `checklist_phase3` fixtures pass; the two recordings check in CI; a Hausgeldabrechnung upload proposes `ruecklage_spent` with its quote | M: 1–2 coders | **Yes** (persisted state, events): FOCUS append-only inputs, the proposal apply path, no value reaches `tax_input` without apply. **Done 9 Nov 2026**: reviewed (no findings), recordings made, merged and deployed with step 1 in PR #81 |
| 3 **Exports** | field sheet PDF, archive ZIP, `tax_export` rows, job, signed download, export history, `README` with the Finanzamt-copy list, §23 date on the screen | — | `export_roundtrip` passes; a ZIP opens with every linked document; the sheet carries no name or IBAN (privacy test) | M: 1 coder | Small (persisted files, privacy of the sheet). **Done 9 Nov 2026**: reviewed (two minor findings fixed), merged and deployed in PR #84 |
| 4 **Closing** | Luis's hand calculation of the 2026 return compared with the draft to the euro (outside the repo); the 2026 form mapping confirmed when ELSTER publishes it (data PR: `2026.yaml` → `published`, the 2026 rows in `test_tax_rulesets.py`) | — | the two numbers agree; the form status is `published` | S | No |

Phase 3 is done after step 4's comparison; steps 2 and 3 can ship in either order after 1.

## 16. ARCHITECTURE.md edits this spec requires

Conflicts, stated plainly, and the edits (made in the spec PR):

1. **Tax rules engine → Zuflussprinzip row** says "recurring payments within 10 days of year
   end go to the year they belong to". The law and BFH X R 2/21 also require the payment to be
   *due* within those days. Edit the row: "… within 10 days of year end, *and due within the
   same days* (BFH X R 2/21), go to the year they belong to"; data: "booking date, recurrence
   flag, service period, due date (loan schedule, the tenancy's rent due day, the period)".
2. **Decisions → Allocation tax year (6 Nov 2026)** and phase 2's D31 say the ten-day rule's
   inputs were all stored. Add to the row: "The due date was missing; phase 3 adds
   `tenancy.rent_due_day` (D67) and takes the loan's from the schedule".
3. **Tax rules engine → Verbilligte Vermietung row** knows only the 66 % threshold. Edit:
   "Rent below 50 % of the local comparable (warm) rent → the letting is split and only the
   paid share's costs count (Z87); 50–66 % → warning that a Totalüberschussprognose is
   needed; ≥ 66 % → full deduction (§21 Abs. 2 EStG as of 2021)".
4. **Tax rules engine → Hausgeld split row**: add "(BFH IX R 19/24: contributions to the
   Erhaltungsrücklage are deductible only when the WEG spends them; the Hausgeldabrechnung's
   Entnahme, confirmed by the user, goes to the Erhaltungsaufwand line)".
5. **Tax rules engine → AfA row**: data column gains "building share of the purchase price
   *and of the Nebenkosten rows*; `purchase_price` and `acquisition_cost` allocations are
   evidence only (D55)".
6. **Tax rules engine → Shape**: "A `RuleSet` … form mapping" gains "keyed by stable line
   codes with the Zeile and the ELSTER Kennzahl per form year; a year whose form is not yet
   published reuses the previous mapping marked provisional (phase 3 spec D53)".
7. **Domain model → Tenancy**: key fields gain "rent due day".
8. **Delivery phases → phase 3 row**: "Rule set 2026" → "Rule set 2026 (form mapping
   provisional until ELSTER publishes the 2026 Anlage V)"; status column: "Spec drafted
   9 Nov 2026 (`docs/phases/phase-3.md`)".
9. **Input for the phase 3 spec** paragraphs (Finanzamt copies; rent before the handover;
   purchase price paid from the account; the phase 1 checklist extension): append "→ phase 3
   spec D63/Q1, D55, D64/§8, D65/Q8".
10. **Decisions table**: new rows "Phase 3 spec (9 Nov 2026)": rule sets as YAML data with
    per-year form mapping (D53); §11 with Fälligkeit and `rent_due_day` (D54, D67); AfA basis
    from the record only (D55); Rücklage per BFH IX R 19/24 (D57); `tax_input` and `tax_export`
    tables (D58, D65); drafts computed, exports snapshotted (D59).

All ten were made in step 0's doc commit on `docs/phase-3-step0`. The delivery table has no
status column, so item 8's status ("spec drafted 9 Nov 2026, step 0 written the same day, not
merged") opens the row's Delivers cell; the "Done when" cell keeps the phase's goal. Item 5's
row and item 10's Decisions row also record D73 (gross basis additions), confirmed in the
review, and the Decisions row names D74–D76.

## 17. Open questions

1. **Decided 9 Nov 2026 (Luis): the forwarded rent share before the handover is rental
   income** (D63). The seller forwarded Luis's share only for months in which the leases
   already named him as landlord, so the readings "reduction of the purchase price" and
   "capital income for paying early" (which fit only while the seller was the landlord) do not
   arise; only the share he received counts. Still open, and only if it ever happens: how a
   payment under the seller's 24-month rent guarantee counts (BFH V R 2/09 treats such a
   payment as damages, for VAT); the draft lists it under `income.other` with its warning until
   then.
2. **Decided 9 Nov 2026 (Luis): one Z13 row for the whole flat**, not one per room (D71).
3. **Decided 9 Nov 2026 (Luis): the running-cost terms per lease** (D72). A separately named
   Pauschale goes on Z20 with the Z24 box unticked; a lease with one all-in rent keeps its
   whole rent on Z13. The later lease states an all-in Warmmiete, so that tenancy is
   `included` and the terms are mixed (Z24 unticked, warning `umlagen.mixed_terms`); Luis sets
   the field when phase 3 is built.
4. **Rounding (orchestrator, 9 Nov 2026): D60 stands as provisional.** The Anleitung zur
   Anlage V is read in ELSTER in step 4 together with the 2026 form; a different convention
   changes only `rounding.py` and the rounded column of `anlage_v_2026.yaml`, in a spec
   amendment.
5. **The 2026 form (orchestrator, 9 Nov 2026):** re-checked in ELSTER in January 2027 (step 4);
   until then `2026.yaml` is `provisional` (D53, §4.3).
6. **Decided 9 Nov 2026 (Luis): the due day differs per lease**, so `rent_due_day` stays a
   tenancy field (D67): the earlier room leases make rent due on the 1st, the later lease by the
   3rd (the default). Any due day from the 1st to the 10th gives the same result under the
   ten-day rule: a January rent paid in the last days of December is due inside the window and
   counts for the new year; a December rent is due outside it, so a late one paid in January
   counts in the year it arrives. Setting 1 on the earlier tenancies is for accuracy only.
7. **Decided 9 Nov 2026: the ortsübliche Miete** (D61). The flat is in Fürth, which has a
   qualified Mietspiegel (2025 edition, valid from 1 Aug 2025, [fuerth.de](https://www.fuerth.de/fileadmin/redaktion/02-Bauen-Mobilitaet/02-Dokumente-Allgemein/Mietspiegel-Stadt-Fuerth-2025.pdf)).
   Luis enters its net cold rent per m² for the flat (the low end of the range is allowed) once
   phase 3 is built; the app adds the running costs from the ledger. The 50–66 %
   Totalüberschussprognose stays a warning; phase 4's projection may compute it later.
8. **Decided 9 Nov 2026 (Luis): Finanzamt copies by hand for 2026, with a warning.** The
   draft and the archive warn `archive.finanzamt_copy_needed` for each document that needs one
   (an XFA form, or ink or stamp annotations not drawn into the page), saying how: open it in
   Adobe Reader and print it to PDF. No renderer is added; automation is revisited in phase 5.
9. **§82b controls (orchestrator, 9 Nov 2026): built in step 2**, as specced; cheap, and the
   first large repair invoice needs them.
10. **Hausgeld due day and the Disagio (orchestrator, 9 Nov 2026):** as specced (first of the
    service month; no Disagio check). Revisit only for a loan with a Disagio.
11. **EStR/EStH confirmations:** still open; read in step 4 with the Anleitung. The fixtures
    encode the readings in §5; a different official reading changes a fixture in a spec
    amendment, never in an implementation PR.
12. **PDF library (orchestrator, 9 Nov 2026): reportlab** (BSD, no system libraries, fits
    the small API container; its dependency pillow comes as a binary wheel, found in step 0).
13. **`other_income` (orchestrator, 9 Nov 2026): on Z29 with the `income.other_review` warning**,
    as specced; the forwarded rent share before the handover goes to the rent row instead (D63).
14. **Exports (orchestrator, 9 Nov 2026): kept out of the `document` table** (D65); they are
    listed on the Steuern screen, not under Dokumente.
15. **Einbauküche (from the step 0 review, 9 Nov 2026; not decided).** A fitted kitchen is not
    part of the building but a separate movable asset (BFH IX R 14/15): it is depreciated over
    10 years on its own, on Z42 (Kz 41, "AfA auf bewegliche Wirtschaftsgüter") rather than in
    the building's AfA, and it is not an Instandsetzung for the 15 % test. Phase 3 has no
    record or input for such an asset, so how a kitchen bought for the flat is handled (a new
    input kind, a category, or a manual line) is open. The step 0 fixture that used a kitchen
    as a building addition was changed to a Balkonanbau.
16. **Tenant documents in the archive (from step 3, 9 Nov 2026; not decided).** The yearly
    archive copies the year's originals, leases included, and is kept with the tax records
    (10 years after the tax year), while a tenant's data is held until the end of the tenancy
    plus 4 years. Open: whether the archive leaves tenant documents out (and lists them by
    date and type only), whether tenant erasure also removes or rebuilds affected archives,
    or whether the tax-record retention takes precedence for documents the Finanzamt may ask
    for. The `tax_export` row references its file by foreign key, so any removal needs a
    decision on the row too.
