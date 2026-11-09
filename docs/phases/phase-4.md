# Phase 4 spec: Performance — actuals, derived marginal rate, projection to sale, §23 check, IRR

Nov 9, 2026 · spec draft (architect), not yet checked by the orchestrator; Luis's questions in §13 are open. No implementation. Every tax figure below was checked against gesetze-im-internet.de or an official announcement on 9 Nov 2026 (§3 lists the sources and what stayed unverified).

Phase 4 is arithmetic over phase 3's rules (principle 1: no model touches a euro). Its one new piece of tax law is the income tax tariff (§32a EStG) with Soli and church tax, which turns a rental result into a tax effect; everything else (AfA, Zuflussprinzip, §82b, 15 % tracker, §23 date) is reused from `rules/tax/` by running the phase 3 draft over projected payments.

This is the spec PR ARCHITECTURE.md requires before implementation:

| Artifact | Where | Status |
| --- | --- | --- |
| Pydantic schemas | `backend/src/hausbuch/schemas/performance.py` (new: `Tariff`, `IncomeInput*`, `Scenario*`, `Levers`, `YearFigures`, `Actuals`, `Projection`, `SaleResult`); additions to `property.py` (Owner), `events.py`, `enums.py` (§7) | Not written |
| Migration | `0016_performance` (§6): `owner.church_tax`, `owner.tax_state`, tables `income_input`, `projection_scenario`; `owner.marginal_tax_rate` carried over and dropped | Not written |
| Tariff data | `backend/src/hausbuch/rules/tax/tariffs/2026.yaml`, loaded by `rules/tax/registry.tariff(year)` (§4) | Not written |
| Rule fixtures | `backend/tests/fixtures/phase4/*.yaml`, 6 files (§10), loaded by `tests/fixture_models_phase4.py` | Not written |
| Step 1–3 code | `rules/tax/tariff.py`, `rules/performance/*`, `services/income_inputs.py`, `services/performance.py`, routes, the Planung screen | Not started |

**Done when** (ARCHITECTURE.md, phase 4): the projection matches a spreadsheet model for three scenarios. §10.3 makes the three scenarios synthetic fixtures (`projection.yaml`); the fixture *is* the spreadsheet. The second half of the phase 4 row, "one settlement generated from real 2026 data", cannot be met with Luis's leases (§13 Q1), so this spec proposes to take it out of the phase.

## 1. Scope

**In**

- Actuals per property and year from confirmed allocations and the phase 3 draft (§5.3).
- The marginal rate derived from each owner's expected income on the §32a splitting tariff, Soli and church tax, as per-year rule data; a Bescheid's zvE or a typed rate overriding it per year (§4, §5.1, §5.2).
- The projection to a chosen sale year that runs phase 3's `draft()` over synthesized payments, so every tax rule (AfA incl. D79 and the D80 kitchen line, §82b, the 15 % window, §11) applies without new code (§5.4).
- At sale: §23 check with the AfA add-back, profit formula, profit bridge, CAGR, IRR on dated cash flows (§5.5).
- Scenarios (levers) as append-only rows; projections computed on read (§6).
- The Planung screen, mockup `05-performance` (§9).

**Out**

- Betriebskostenabrechnung generator: proposed as an unscheduled optional step (§11, §13 Q1).
- Anlage N and the Hauptvordruck (a later phase may build them on this phase's tariff engine; §7 keeps the door open).
- Totalüberschussprognose for verbilligte Vermietung (phase 3 Q7 said phase 4 "may"; not in this spec: it is the same projection with a different horizon, a later amendment).
- Vorfälligkeitsentschädigung on an early loan repayment at sale (the selling-costs lever can carry an estimate).
- Kinderfreibeträge, Progressionsvorbehalt (Elterngeld), Kirchensteuer-Kappung, other income kinds in the zvE: not modelled (§5.1 names the error direction; the override covers them).

## 2. Decisions taken in this spec

Numbering continues from phase 3's D80 (the Einbauküche as a movable asset, being built). Each one is a place to push back in review.

| # | Decision | Why |
| --- | --- | --- |
| D81 | **The income tax tariff is per-year rule data** `rules/tax/tariffs/<year>.yaml` (§4): §32a zones, splitting, Soli rate, Freigrenze and Milderungszone, church tax rate per Bundesland, and the deduction defaults (Pauschbeträge, social-insurance rates and Beitragsbemessungsgrenzen). `registry.tariff(year)` returns the file of `year` or, for a year without one, the **latest earlier** file marked `reused_from` (unlike `rule_set(year)`, which raises): a projected year always has a tariff. Only 2026 ships; the ledger starts 1 Jan 2026 (Decisions "History range"), so no 2025 tax effect is ever computed. | Principle 5; ARCHITECTURE's marginal-rate decision ("the §32a tariff becomes per-year rule data; a projected year without a published tariff reuses the latest one"). |
| D82 | **The tax effect of a rental result R is a difference of two tariff computations**, `T(zvE_base + R) − T(zvE_base)`, where `T` = Einkommensteuer (splitting) + Soli + church tax and `zvE_base` is the household's taxable income without this property. Never `R × rate`, except when a `marginal_rate_override` is in force for the year, where the effect is `R × rate` with the typed rate (it includes Soli and church tax, as `TaxRate` is documented). The screen shows the **effective rate on the rental result**, `effect ÷ R` (one decimal), and calls it "Steuerwirkung", not Grenzsteuersatz; with R = 0 it shows the tariff's slope at `zvE_base`. | A result of −7,948.66 on a joint zvE of 90,000 saves 2,808.00 (35.3 %); the marginal slope at 90,000 is different from the slope at 82,051, and `R × slope` would be off by tens of euros. The difference is exact and reproducible. Changes ARCHITECTURE's "result × marginal rate" wording (§12). |
| D83 | **Household income lives in an append-only table `income_input`** with kinds `expected_gross_pay` (per owner and year), `deductions_override` (per owner and year), `bescheid_zve` (per year, household-level, Bescheid document required) and `marginal_rate_override` (per year, household-level), with `supersedes_id` and `withdrawn` like `tax_input` (D58). The rate source per year is derived: `override` when a rate row is in force, else `bescheid` when a zvE row is in force, else `estimate`; a year without any row uses the latest earlier year's rows, marked `carried_forward`. Owner-scoped rows raise `owner.income_input_recorded`, household-scoped rows `workspace.income_input_recorded`. | Principle 4; "a Bescheid's zvE replaces the estimate for that year as a new event (append-only, the estimate stays in History)". Joint filing means one zvE and one rate for both owners, so those two kinds are household-level. |
| D84 | **The estimate of taxable income** per owner = `gross − deductions`, floored at 0, where `deductions` defaults to Arbeitnehmer-Pauschbetrag + Sonderausgaben-Pauschbetrag + the employee's deductible social-insurance share computed from the tariff file: pension (RV) 100 %, health (KV incl. the average Zusatzbeitrag) × 0.96, long-term care (PV) 100 %, unemployment (AV) **not** counted. A `deductions_override` row replaces the whole default with a typed amount. The household `zvE_base` = Σ owners. A `bescheid_zve` row gives `zvE_base = zvE_bescheid − R_assessed`, with `R_assessed` the property's draft result of that year (the Bescheid's zvE already contains it). | §9a S. 1 Nr. 1a (1,230), §10c (36), §10 Abs. 3 S. 6 (100 % from 2023), §10 Abs. 1 Nr. 3 S. 4 (−4 % with Krankengeld entitlement). AV is a "sonstige Vorsorgeaufwendung" under the €1,900 cap of §10 Abs. 4 S. 2, which KV + PV already exhaust for an employee, so it adds nothing; a coder must not add it. |
| D85 | **Actuals per property and year are computed on read** from confirmed, non-zero allocations, assigned to years by phase 3's §11 rule (`tax_year_of`), so the Kaltmiete on the Planung screen equals the Anlage V rent line of the Steuern screen. The result comes from `draft()`. Umlagen (NK prepayments, settlements; umlagefähige costs) are a separate pass-through block whose net joins net cash; `purchase_price`, `acquisition_cost`, `loan_disbursement`, `owner_transfer_*`, `deposit_*` are investment or financing flows, never yearly cash (§5.3). The current year is labelled "bisher" and its tax effect "geschätzt". | One number for one thing across screens; "Umlagen are shown as pass-through so cash isn't distorted by settlement timing" (ARCHITECTURE). |
| D86 | **The projection runs phase 3's `draft()` on synthesized payments.** `rules/performance/synthesize.py` builds, per projected year, `AllocationFact` rows for rent, NK, Hausgeld parts, Grundsteuer, insurance, management, bank fees (each recurring cost from its latest confirmed line, annualised by its cadence, §5.4 step 2), upkeep (as `repairs` with a synthetic `exclude_from_15pct` input, reason `annual_maintenance`) and the loan's interest and principal from `build_schedule`; they join the property's real `DraftFacts` (all actual years, all tax inputs), and `draft(facts, year, rule_set_or_latest(year))` gives the year's taxable result and AfA. Synthetic rows carry deterministic UUID5 ids and trace to the scenario row and the lever that produced them (new `SourceKind` values `scenario`, `schedule_entry`, `income_input`). The projection starts on 1 January after today's year; the current year shows actuals only. Projections are never stored. | The alternative (call `afa()`, `build_schedule()`, the §82b helper directly per year) duplicates the routing the draft already does and would miss a rule added later. With the draft, a shorter useful life (D79), a basis addition, a running §82b spread, the 15 % window and the D80 kitchen line flow through unchanged. |
| D87 | **Levers and defaults are a `projection_scenario` row** (append-only, superseded like `tax_input`), per property: value growth, rent growth, cost inflation, sale year, today's value (default: purchase price), upkeep per year (default 600.00), selling costs % (default 3.57), per-loan follow-up rate override (default: the loan record's `follow_up_rate`, which is null = nominal rate, the "today's rate" default of phase 1), vacancy months per year (default 0). One scenario per property is `is_default`. The screen's ± buttons change levers in the request only (query parameters); "Szenario speichern" writes a row. | Principle 4 for what the user sets; drafts and projections computed on read (D59). |
| D88 | **At sale** (sale and transfer on 31 December of the lever year): §23 applies when that date is before `tax_free_from` (phase 3 `spekulation.py`); gain = `V_sale − C_sell − (purchase price + Σ cost rows + Σ basis additions − Σ building AfA claimed)`, the AfA from the actual years' recomputed drafts and the projected years' drafts (the D80 kitchen's own AfA line is not added back: a movable asset held over a year is not a §23 object). Freigrenze: each spouse's half of the gain is tested against `< 1,000` (a cliff, not an allowance); a loss gives no refund (§23 Abs. 3 S. 7 restricts it to §23 gains, shown as "nicht verrechenbar"). Sale tax = `T(zvE_base + R_sale_year + gain) − T(zvE_base + R_sale_year)` under the sale year's tariff (D81 fallback). The projection assumes the sale contract, the transfer and the price's Zufluss all on 31 December of the lever year (listed under `assumptions`); a price paid in January would move the gain to the next tax year under §11, which the user can model by choosing the next sale year. | §23 Abs. 1 S. 1 Nr. 1, Abs. 3 S. 1, S. 4, S. 5 EStG; per-spouse Freigrenze per BMF 5 Oct 2000 Rz. 41 (secondary source, §3). ARCHITECTURE: "a sale inside 10 years adds the claimed AfA back into the taxable gain". |
| D89 | **Profit, bridge, CAGR, IRR** as in ARCHITECTURE: `Profit = (V − C − T − L) + Σ CF − E₀` with `E₀ = purchase price + Σ cost rows − Σ loan principal` (from the records; the owner transfers of the purchase year are shown beside it as evidence) and `CF_y` the net cash after tax of each year (actual years as recorded, the current year as far as booked, projected years from D86). Bridge rows: appreciation `V − purchase price`; loan paid down `L₀ − L`; Σ CF; −Σ cost rows; −C; −T; they sum to the profit by construction. CAGR = `((E₀ + Profit) ÷ E₀)^(1/years) − 1`, years = days from `purchase_date` to the sale date ÷ 365.25. IRR = XIRR on dated flows (−E₀ on `purchase_date`, CF_y on 31 Dec of each year, the net sale proceeds on the sale date), Actual/365, bisection to 1e-6, Decimal; `None` when the flows do not change sign. | Determinism and traceability: each flow carries its sources. |
| D90 | **`owner.marginal_tax_rate` is carried over, then dropped.** Migration 0016 writes one `marginal_rate_override` row for tax year 2026 per workspace when exactly one distinct non-null rate exists among its owners (note "übernommen aus Eigentümer"), writes nothing and logs a notice when two owners disagree, then drops the column. New owner fields `church_tax boolean NOT NULL DEFAULT false` and `tax_state char(2)` (Bundesland code, required when `church_tax`). | ARCHITECTURE: "Owner stores expected income and church-tax flag instead of a typed rate". The event log keeps the old value (`owner.created` / `owner.updated` payloads), so nothing typed is lost. |
| D91 | **Betriebskostenabrechnung leaves phase 4's done criterion** and becomes an unscheduled optional step 5 (§11), built only when a `prepayment` tenancy exists. Pending Luis (§13 Q1). | Every one of Luis's leases is `flat_rate` or `included` (D72), so the generator would produce nothing and "one settlement from real 2026 data" is unreachable. |

## 3. Sources checked (9 Nov 2026)

| Item | Verified | Source | Not verified → §14 |
| --- | --- | --- | --- |
| §32a Abs. 1 tariff 2026 | GFB 12,348; 12,349–17,799: (914.51·y + 1,400)·y; 17,800–69,878: (173.10·z + 2,397)·z + 1,034.87; 69,879–277,825: 0.42·x − 11,135.63; from 277,826: 0.45·x − 19,470.38; y, z = ten-thousandths above 12,348 / 17,799; zvE and tax floored to whole euros (S. 1, S. 6); "ab dem Veranlagungszeitraum 2026" (S. 2) | [§32a EStG](https://www.gesetze-im-internet.de/estg/__32a.html), cross-checked on [lxgesetze](https://lxgesetze.de/estg/32a) | — |
| Splitting | Abs. 5: twice the tax on half the joint zvE | same | Whether the half is floored before the tariff (practice of the BMF Programmablaufplan; the fixture floors it, Q-O1) |
| Soli | §4 S. 1: 5.5 %; S. 2: at most 11.9 % of (base − Freigrenze); S. 3: fractions of a cent dropped; §3 Abs. 3: Freigrenze 40,700 (splitting) / 20,350 | [§4 SolZG](https://www.gesetze-im-internet.de/solzg_1995/__4.html), [§3 SolZG](https://www.gesetze-im-internet.de/solzg_1995/__3.html) (current text) | — |
| Church tax | §51a Abs. 2 S. 1: base is the Einkommensteuer computed with the Kinderfreibeträge; Bavaria: Art. 8 KirchStG caps the rate at 10 %, the 8 % is the churches' Umlagesatz (Erzbistum München: "8 v.H. … Art. 8 KirchStG, Art. 6 DKirchStO"); 8 % BY and BW, 9 % elsewhere | [§51a EStG](https://www.gesetze-im-internet.de/estg/__51a.html), [Art. 8 KirchStG](https://www.gesetze-bayern.de/Content/Document/BayKirchStG-8), [Erzbistum München](https://www.erzbistum-muenchen.de/cms-media/media-73021520.pdf) | The per-state table is from consistent secondary sources; Kinderfreibeträge not modelled (Q-O2) |
| Pauschbeträge | §9a S. 1 Nr. 1a: 1,230; §10c: 36 | [§9a](https://www.gesetze-im-internet.de/estg/__9a.html), [§10c](https://www.gesetze-im-internet.de/estg/__10c.html) | — |
| Vorsorgeaufwendungen | §10 Abs. 3 S. 6: 100 % from 2023; Abs. 1 Nr. 3 S. 4: KV −4 % with Krankengeld entitlement; Abs. 4 S. 1–2: 2,800 / 1,900 cap for other Vorsorge | [§10 EStG](https://www.gesetze-im-internet.de/estg/__10.html) | — |
| Social insurance 2026 | BBG RV 8,450 / 101,400; BBG KV/PV 5,812.50 / 69,750 (Sozialversicherungsrechengrößen-Verordnung 2026, Bundesrat 21 Nov 2025); KV 14.6 % (§241 SGB V); average Zusatzbeitrag 2.9 % (BMG, BAnz AT 10.11.2025 B7); RV 18.6 % (§158 SGB VI), AV 2.6 % (§341 SGB III) | [bundesregierung.de](https://www.bundesregierung.de/breg-de/aktuelles/beitragsbemessungsgrenzen-2386514), [§241 SGB V](https://www.gesetze-im-internet.de/sgb_5/__241.html), [Bundesanzeiger](https://www.bundesanzeiger.de/pub/publication/oUJYVk8GYRXybnb9Y4h/content/oUJYVk8GYRXybnb9Y4h/BAnz%20AT%2010.11.2025%20B7.pdf) | PV 3.6 %: [§55 SGB XI](https://www.gesetze-im-internet.de/sgb_11/__55.html) still prints 3.4 %; the 0.2-point rise of 1 Jan 2025 came by Verordnung, 3.6 % confirmed by secondary sources only (Q-O3); RV and AV rates via a Berlin administrative circular, not the Verordnung text |
| §23 | Abs. 1 S. 1 Nr. 1 (ten years between the contracts), Abs. 3 S. 1 (price − AK − WK), S. 4 (AK reduced by AfA), S. 5 (tax-free below 1,000 per year) | [§23 EStG](https://www.gesetze-im-internet.de/estg/__23.html) | Freigrenze per spouse: BMF 5 Oct 2000 Rz. 41 via [Haufe](https://www.haufe.de/id/kommentar/littmannbitzpust-das-einkommensteuerrecht-estg-23-5-besteuerungsgrenze-23-abs3-s5-estg-HI9400081.html) (secondary) |

## 4. The tariff as data (`rules/tax/tariffs/2026.yaml`, `schemas/performance.py`)

```python
class TariffZone(Schema):
    up_to: int | None            # last whole euro of the zone; None = open
    kind: Literal["zero", "progressive", "linear"]
    a: Decimal | None = None     # progressive: (a·v + b)·v + c with v = (x − offset) / 10 000
    b: Decimal | None = None
    c: Decimal = Decimal("0")
    offset: int | None = None    # progressive: the euro the ten-thousandths count from
    rate: Decimal | None = None  # linear: rate·x − c

class SocialInsurance(Schema):   # employee shares and caps, per year
    pension_rate: Rate; unemployment_rate: Rate; health_rate: Rate; health_extra_rate: Rate
    care_rate: Rate; bbg_pension: PositiveMoney; bbg_health: PositiveMoney
    health_deductible_share: Rate = Decimal("0.96")   # §10 Abs. 1 Nr. 3 S. 4

class Tariff(Schema):
    tax_year: TaxYear; version: str; source_url: str
    zones: list[TariffZone]                            # §32a Abs. 1, contiguous, validated
    soli_rate: Rate = Decimal("0.055"); soli_mitigation_rate: Rate = Decimal("0.119")
    soli_free_limit_joint: PositiveMoney; soli_free_limit_single: PositiveMoney
    church_tax_rates: dict[str, Rate]                  # "BY": 0.08, "BW": 0.08, "BE": 0.09, …
    employee_allowance: NonNegativeMoney               # §9a, 1,230
    special_expenses_allowance: NonNegativeMoney       # §10c, 36 per owner
    social_insurance: SocialInsurance
```

`2026.yaml` carries the §3 figures (employee rates: pension 0.093, unemployment 0.013, health 0.073, extra 0.0145, care 0.018; BBG 101,400 and 69,750; the 16 state codes). `registry.tariff(year)` loads and caches like `rule_set`; `registry.rule_set_or_latest(year)` does the same fallback for the Anlage V rule set and is used by the projection only (the Steuern screen keeps raising `tax.no_rule_set`).

## 5. Rules

Pure functions, no I/O, no model; `Decimal` throughout; errors carry a code (§10).

### 5.1 Tariff and tax effect (`rules/tax/tariff.py`)

- `income_tax(zve, tariff, joint) -> Decimal`: floor `zve` to whole euros; joint → `2 × tax(floor(zve ÷ 2))`; each zone per §32a; result floored to whole euros.
- `soli(est, tariff, joint) -> Decimal`: 0 when `est ≤ Freigrenze`; else `min(est × 5.5 %, (est − Freigrenze) × 11.9 %)`, floored to the cent.
- `church_tax(est, owners, tariff) -> Decimal`: each owner with `church_tax` pays their state's rate on half the joint ESt (`est ÷ 2 × rate`, half-up to the cent); an owner outside a church pays nothing; the two halves are summed, so one member at 8 % gives half the two-member figure and two members in different states each pay their own rate.
- `total_tax(zve, tariff, owners) -> TaxBreakdown {est, soli, church, total}`.
- `tax_effect(zve_base, R, tariff, owners) -> TaxEffect {effect, effective_rate, breakdown_with, breakdown_without}` per D82; `slope(zve_base, …)` for R = 0 (the zone's derivative at `zve_base ÷ 2`, for display only).

Worked examples (fixture `tariff.yaml`): single 2026: 12,348 → 0; 17,799 → 1,034; 17,800 → 1,035; 30,000 → 4,217; 69,878 → 18,213; 100,000 → 30,864; 277,826 → 105,551. Joint: 60,000 → 8,434; 90,000 → 17,670; 200,000 → 61,728. Soli joint on ESt 42,000 → 154.70 (Milderungszone), 48,000 → 868.70, 50,000 → 1,106.70; on 17,670 → 0.00. Church tax 8 % on 17,670 → 1,413.60. Effect (`marginal_rate.yaml`): base 90,000, two owners BY in church, R = −7,948.66 → tax 19,083.60 → 16,275.60, effect **−2,808.00**, effective rate 35.3 %; R = +2,787.00 → +1,013.04 (36.3 %); R = 0 → 0.00 with the slope shown.

### 5.2 Taxable income of the household (`rules/performance/income.py`)

`zve_base(year, inputs: list[IncomeInputFact], owners, tariff) -> ZveEstimate {amount, source: estimate | bescheid | override, carried_forward: bool, per_owner[], sources}` per D83/D84. Deductions default per owner: `1,230 + 36 + min(gross, BBG_RV) × 0.093 + min(gross, BBG_KV) × (0.073 + 0.0145) × 0.96 + min(gross, BBG_KV) × 0.018`. Worked examples (`marginal_rate.yaml`): gross 60,000 → deductions 12,966.00 → 47,034.00; gross 45,000 → 10,041.00 → 34,959.00; the household 81,993.00. Case `bescheid_replaces_estimate`: zvE 80,000 on the Bescheid with an assessed result of −7,948.66 → base 87,948.66. Case `override_rate`: 0.4200 → effect = R × 0.42. Case `carried_forward_2028` (rows for 2026 only, year 2028 → the same figures, flag set). A year with no row at all → `income.missing` and the tax effect is `None` (the screen shows "—  · Einkommen eintragen"); the rest of the figures are still computed.

**Later (not this phase):** a Lohnsteuerbescheinigung or itemised employment Werbungskosten can replace the estimate by a new kind (`gross_pay_certified`, `deductions_itemised`, document required) that wins over `expected_gross_pay` / the default in `zve_base`'s precedence, so a later Anlage N step needs no schema change beyond the enum; the per-figure `source` already exists for it.

### 5.3 Year figures: actuals and projected years alike (`rules/performance/year_figures.py`)

`year_figures(allocations_of_year, draft: AnlageVDraft, effect: TaxEffect | None, schedule_years) -> YearFigures`, one function for both uses (D85, D86):

| Figure | Definition (confirmed, non-zero allocations of the §11 year) |
| --- | --- |
| `rent_in` | Σ `rent_cold` (incl. pre-handover lines moved to the rent row, D63) |
| `other_income` | Σ `other_income` not moved |
| `umlagen_in` / `umlagen_out` / `umlagen_net` | Σ `operating_costs_prepayment` + `operating_costs_settlement` / Σ `hausgeld_umlagefaehig` + `grundsteuer` + `insurance` / in − out |
| `interest`, `principal_repaid` | Σ `loan_interest`, Σ `loan_principal` |
| `running_costs` | Σ `hausgeld_verwaltung` + `hausgeld_ruecklage` + `property_management` + `bank_fees` + `repairs` + `renovation` + `professional_fees` + `loan_fees` + `other_expense` |
| `cash_out` | `interest + principal_repaid + running_costs + umlagen_out` |
| `taxable_result` | `draft.totals.result` (None when the draft has no rule set) |
| `afa` | `draft.afa.amount` (building; the D80 kitchen line listed separately as `afa_movable`) |
| `tax_effect` | §5.1 on `taxable_result`; None without income inputs |
| `net_cash` | `rent_in + other_income + umlagen_in − cash_out + tax_effect` (tax_effect 0 when None, flagged) |
| `invested` | purchase year only: Σ `purchase_price` + `acquisition_cost` magnitudes (evidence, shown beside E₀) |

Every figure carries `sources` (allocation ids; for projected years the scenario and schedule entries). Actuals for a year = `year_figures` over the real facts, draft from `services/tax.anlage_v_draft`, loan schedule years from `build_schedule`. Fixture `actuals.yaml` reuses `anlage_v_2026.yaml` case A's facts and owners of §5.2: 2026 → rent_in 5,100.00 (the 29 Dec rent for January belongs to 2027 under §11), other_income 850.00 (the seller's two forwarded 425.00, undecided in case A: cash, but outside the result), umlagen_in 1,080.00, umlagen_out 1,200.00 (6 × 180 Hausgeld + 2 × 60 Grundsteuer), interest 3,724.56, principal 1,508.77, running_costs 480.00 + 240.00 + 600.00 + 150.00 + 44.10 + 1,190.00 + 4,760.00 = 7,464.10, cash_out 13,897.43, result −7,948.66, tax effect **−2,687.04** (33.8 %) on the household base 81,993.00, net cash 5,100 + 850 + 1,080 − 13,897.43 − 2,687.04 = **−9,554.47**; invested 320,800.00 (300,000 + 19,500 + 1,300); the 1,700.00 deposit, the 85,000.00 owner top-up and the 240,000.00 payout are not cash of the year; the two 0.00 lines are ignored. Case `current_year_partial` flags `so_far`. Case `no_income_inputs` → tax effect None, net cash without it, flag.

### 5.4 Projection (`rules/performance/synthesize.py`, `projection.py`)

`project(facts: DraftFacts, scenario: Levers, owners, inputs, today, loans: list[LoanTerms]) -> Projection {years: list[YearFigures + value, loan_balance, equity], sale: SaleResult, assumptions[]}`.

Per projected year Y from `today.year + 1` to the sale year, with `n = Y − today.year`:

1. **Rent** = Σ rent rows in force on `today` (`rent_at`) × 12 × `(1 + rent_growth)^(n−1)` (the first projected year keeps today's rent), minus `vacancy_months ÷ 12`; `rent_cold` monthly allocations dated the tenancy's `rent_due_day`, `recurring`, service period the month. NK prepayment likewise into `operating_costs_prepayment` (a `flat_rate` or `prepayment` tenancy; `included` has none).
2. **Costs**, per recurring category (`hausgeld_umlagefaehig`, `hausgeld_verwaltung`, `hausgeld_ruecklage`, `grundsteuer`, `insurance`, `property_management`, `bank_fees`): the latest confirmed line before `today` is annualised as `amount × 12 ÷ cadence_months` and repeated at that cadence, where `cadence_months` = the months of the line's service period when set, else the gap in days between the category's two latest lines ÷ 30.4, rounded and snapped to {1, 2, 3, 6, 12}, else 12 (a lone line is taken as yearly). The fixture's Hausgeld parts (180/40/100 monthly), management (25.00) and bank fees (4.90) give 2,160 / 480 / 1,200, 300.00 and 58.80; its two Grundsteuer lines of 60.00 on 17 Aug and 16 Nov (91 days → quarterly) give 240.00. Each category then grows by `(1 + cost_inflation)^(n−1)`; **upkeep** = the lever, as one `repairs` line on 30 June with a synthetic `exclude_from_15pct` (reason `annual_maintenance`), so the 15 % tracker does not capitalise routine upkeep (D56's exclusion is exactly for it). Hausgeld Rücklage contributions stay `hausgeld_ruecklage` (tracked); no `ruecklage_spent` is assumed.
3. **Loan** from `build_schedule(terms)` with the scenario's follow-up rate override, else the loan's; each entry → `loan_interest` and `loan_principal` on `due_on`, recurring, period = the month; `loan_balance` = the year's closing balance.
4. **Draft** = `draft(facts + synthetic rows, Y, rule_set_or_latest(Y))` with `checklist = None`, `today = None`, `coverage = []`, `pending = 0`; warnings are dropped except `draft.form_provisional` → `assumptions`. `taxable_result`, `afa`, `afa_movable` from it.
5. **Tax effect** per §5.1 with `zve_base(Y)` (carried forward, D83).
6. **Value** = `today_value × (1 + value_growth)^n`; **equity** = value − loan balance.
7. `year_figures` over the synthetic rows.

Fixture inputs and checkpoints are in §10.3.

### 5.5 Sale (`rules/performance/sale.py`, `irr.py`)

`sale(projection_years, actual_years, property, levers, tariff, …) -> SaleResult {sale_date, tax_free_from, taxable, value, selling_costs, gain, gain_per_owner, freigrenze_applied, sale_tax, loan_balance, net_proceeds, profit, bridge[], cagr, irr, flows[]}` per D88–D89. `xirr(flows: list[(date, Decimal)]) -> Decimal | None`.

Worked examples (`sale.yaml`, `irr.yaml`): the §10.3 scenario C sale (gain 47,064.16, sale tax 17,422.56, recomputed by the orchestrator 9 Nov 2026; §10.3 agrees); `gain_below_freigrenze` (gain 1,800.00 → 900.00 per spouse < 1,000 → tax 0.00); `gain_at_freigrenze` (2,000.00 → 1,000.00 each → fully taxable); `loss_not_refunded`; `tax_free_after_ten_years` (31 Dec 2036 ≥ 11 Mar 2036 → taxable false, tax 0.00). IRR: `two_flows_10pct` (−1,000.00 on 1 Jan 2026, +1,100.00 on 1 Jan 2027 → 0.100000); `three_flows` (−1,000.00 on 1 Jan 2026, +500.00 on 1 Jul 2026, +600.00 on 1 Jan 2027 → 0.132326); `property_like` (−85,000.00 on 1 Jul 2026, −410.00 on 31 Dec 2026, +1,000.00 on 31 Dec 2027 and 2028, +100,000.00 on 31 Dec 2029 → 0.052471); `no_sign_change` → None. CAGR: E₀ 85,000, profit 20,000, 10.5 years → 0.020329.

### 5.6 Dependency on D80 (the kitchen)

D80 adds a `movable_asset` tax input that lowers the building's basis and adds a 10-year AfA line (Z42). This spec assumes `draft()` returns both lines after D80 (the building on `wk.afa`, the kitchen on its own key); `year_figures` reads them by key and the §23 add-back takes `wk.afa` only. **The orchestrator confirms the line key and the `AfaLine` shape before step 2 starts**; nothing else here depends on it.

## 6. Data model: migration `0016_performance`

| Table / column | Holds | Mutability |
| --- | --- | --- |
| `owner.church_tax boolean NOT NULL DEFAULT false`, `owner.tax_state char(2) CHECK (16 codes) ; CHECK (NOT church_tax OR tax_state IS NOT NULL)` | D90 | Update + `owner.updated` |
| `owner.marginal_tax_rate` | dropped after the carry-over of D90 (the insert needs `NO FORCE ROW LEVEL SECURITY` for its duration, as 0014 does for its check) | — |
| `income_input` | `owner_id` (nullable FK), `kind` CHECK in (`expected_gross_pay`, `deductions_override`, `bescheid_zve`, `marginal_rate_override`), `tax_year smallint NOT NULL CHECK ≥ 2000`, `document_id` (nullable FK), `value jsonb NOT NULL`, `note`, `supersedes_id` (FK self, UNIQUE), `withdrawn`, `recorded_at`, `recorded_by`; CHECKs: `(kind IN (expected_gross_pay, deductions_override)) = (owner_id IS NOT NULL)`; `kind <> 'bescheid_zve' OR document_id IS NOT NULL`; `NOT withdrawn OR supersedes_id IS NOT NULL` | **Append-only** (`forbid_mutation` trigger) |
| `projection_scenario` | `property_id` (FK), `name text`, `is_default boolean`, `levers jsonb NOT NULL` (typed `Levers`, §7), `supersedes_id`, `withdrawn`, `recorded_at`, `recorded_by` | **Append-only**; "in force" = not superseded, not withdrawn; at most one default in force per property (service rule + fixture) |

RLS, `workspace_id`, grants and the `tables.py` mirror as every table since 0001. Downgrade: refused while any `income_input` or `projection_scenario` row exists or any owner has `church_tax` set (as 0013's guard: typed data is not dropped); restores the `marginal_tax_rate` column empty.

**Events added**: `owner.income_input_recorded` (owner-scoped kinds), `workspace.income_input_recorded` (household kinds), `property.scenario_recorded`; payloads: ids, kind, year, value (amounts and codes only), `supersedes_id`, `withdrawn`; `owner.updated` gains `church_tax`, `tax_state`. Effective date forbidden on all three.

## 7. Schemas (`schemas/performance.py`; the spec PR writes them)

```python
class IncomeInputKind(StrEnum): EXPECTED_GROSS_PAY, DEDUCTIONS_OVERRIDE, BESCHEID_ZVE, MARGINAL_RATE_OVERRIDE
class ExpectedGrossPay(Schema): amount: NonNegativeMoney
class DeductionsOverride(Schema): amount: NonNegativeMoney
class BescheidZve(Schema): amount: Money                      # joint zvE as assessed; document_id on the row
class MarginalRateOverride(Schema): rate: TaxRate             # incl. Soli and church tax
class IncomeInputCreate(Schema): owner_id | None; kind; tax_year; document_id | None; value; note | None; supersedes_id | None; withdrawn = False   # validator: scope by kind (as TaxInputCreate)
class IncomeInputFact(IncomeInputCreate): id
class IncomeInput(IncomeInputFact): workspace_id; recorded_at; recorded_by

class LoanLever(Schema): loan_id: UUID; follow_up_rate: Rate | None
class Levers(Schema):
    value_growth: Rate = "0.02"; rent_growth: Rate = "0.02"; cost_inflation: Rate = "0.02"
    sale_year: TaxYear; today_value: PositiveMoney | None = None   # None = purchase price
    upkeep_per_year: NonNegativeMoney = "600.00"; selling_costs_rate: Rate = "0.0357"
    vacancy_months: Decimal = 0 (0–12, one decimal); loans: list[LoanLever] = []
class ScenarioCreate(Schema): property_id; name; is_default; levers: Levers; supersedes_id | None; withdrawn = False
class Scenario(ScenarioCreate): id; workspace_id; recorded_at; recorded_by

class TaxBreakdown(Schema): est: Money; soli: Money; church: Money; total: Money
class TaxEffect(Schema): effect: Money; effective_rate: Percent | None; slope: Percent | None; source: Literal["estimate","bescheid","override"]; carried_forward: bool; with_: TaxBreakdown | None; without: TaxBreakdown | None; sources: list[SourceRef]
class ZveEstimate(Schema): amount: NonNegativeMoney; source; carried_forward; per_owner: list[OwnerZve]; sources
class YearFigures(Schema): year; kind: Literal["actual","actual_partial","projected"]; rent_in; other_income; umlagen_in; umlagen_out; umlagen_net; interest; principal_repaid; running_costs; cash_out; taxable_result: Money | None; afa: Money | None; afa_movable: Money | None; tax_effect: TaxEffect | None; net_cash; invested: Money | None; value: Money | None; loan_balance: Money | None; equity: Money | None; sources: list[SourceRef]
class BridgeRow(Schema): key: Literal["appreciation","loan_paid_down","cash_flow","buying_costs","selling_costs","sale_tax"]; amount: Money; sources
class SaleResult(Schema): sale_date; tax_free_from; taxable: bool; value; selling_costs; afa_claimed; gain: Money | None; gain_per_owner: list[OwnerGain]; freigrenze_applied: bool; sale_tax: Money; loan_balance; net_proceeds; initial_equity; profit; bridge: list[BridgeRow]; cagr: Rate | None; irr: Rate | None; flows: list[DatedFlow]
class Actuals(Schema): property_id; years: list[YearFigures]
class Projection(Schema): property_id; scenario: Levers; scenario_id: UUID | None; tariff: TariffStamp; rule_set: RuleSetStamp; years: list[YearFigures]; sale: SaleResult; assumptions: list[Assumption]
```

`SourceKind` (schemas/tax.py) gains `scenario`, `schedule_entry`, `income_input`, `tariff` (no id, like `rule`). `Owner`/`OwnerCreate` lose `marginal_tax_rate`, gain `church_tax: bool = False`, `tax_state: StateCode | None`; `enums.py` gains `StateCode` (16 codes) and `IncomeInputKind`; `events.py` the three payloads; `EntityType` is unchanged (`owner`, `workspace`, `property` exist). Money stays a decimal string on the wire.

## 8. API

| Route | Returns | Notes |
| --- | --- | --- |
| `GET /api/properties/{id}/performance/actuals` | `Actuals` | every owned year through today's; computed on read |
| `GET /api/properties/{id}/performance/projection?scenario_id=&value_growth=&rent_growth=&sale_year=&…` | `Projection` | the saved scenario (default when none named), each lever overridable by a query parameter without writing anything; 422 `scenario.sale_year_before_today` |
| `GET` / `POST /api/properties/{id}/scenarios` | `list[Scenario]` / 201 `Scenario` | in force rows; `supersedes_id` must be in force (`scenario.not_current`) |
| `GET` / `POST /api/income-inputs?tax_year=&owner_id=` | `list[IncomeInput]` / 201 | `include_history=true`; the Bescheid kind needs its document |
| `GET /api/tax-rate?tax_year=Y&result=R` | `TaxEffect` + `ZveEstimate` | the Steuern screen's "Steuerwirkung" card (optional, step 1) |
| `PATCH /api/owners/{id}` | existing | gains `church_tax`, `tax_state`; loses `marginal_tax_rate` |

Error codes: `tariff.no_file` (no tariff at all), `income.missing` (as a flag, not an error), `scenario.not_current`, `scenario.sale_year_before_today`, `income_input.scope_invalid`, `income_input.not_current`, `projection.no_rule_set` (no rule set file at all).

## 9. Screens

- **Sidebar** gains **Planung** after Steuern (the household design's order). Route `/planning` → the property (selector with more than one) → tab **Immobilie** (built), tab **Haushalt** disabled ("ab Phase 9").
- **Immobilie tab**, layout per `docs/mockups/05-performance.png`, German labels, every figure from the backend: the actuals card ("2026 bisher · Ist", Kaltmiete, Ausgaben, Steuerwirkung (geschätzt), Netto-Cash, Tilgung, with the Umlagen-Saldo as a sub-line); the lever row (Wertsteigerung, Mieterhöhung, **Steuerwirkung** shown as the derived effective rate with its source chip "Schätzung 2026 · 81.993 € zvE" linking to the income inputs, and a pencil to set an override; Verkauf nach); the §23 banner (`tax_free_from` vs the sale date); the four sale KPIs; the equity bars; the bridge; the year table (Jahr, Kaltmiete, Zinsen, Steuerliches Ergebnis, Steuerwirkung, Cash nach Steuern, Wert, Darlehen) with each cell opening its sources; the assumptions footer (upkeep, cost inflation, selling costs, follow-up rate, "Kinderfreibeträge und Progressionsvorbehalt nicht berücksichtigt"); "Szenario speichern" and the scenario list; the disclaimer "Eine Planungsschätzung, keine Steuer- oder Anlageberatung".
- **Eigentümer page**: per owner "Erwartetes Bruttogehalt (Jahr)", "Abzüge (geschätzt: 12.966,00 €, ändern)", "Kirchensteuer ja/nein", "Bundesland"; the household row "zvE laut Bescheid (Jahr, Beleg)" and "Steuersatz manuell (Jahr)"; replaces the rate field.
- **Steuern page**: an optional "Steuerwirkung" line under the result (step 1), same figure as Planung.

## 10. Rule contracts and fixtures (`tests/fixtures/phase4/`)

Synthetic numbers only; `tests/fixture_models_phase4.py` loads every file through the §7 schemas; `test_fixtures_valid.py` gains `phase4`.

| Function | Fixture | Cases |
| --- | --- | --- |
| `tariff.income_tax / soli / church_tax / total_tax` | `tariff.yaml` | `single_zone_boundaries` (the 7 single figures of §5.1), `joint_60k_90k_200k`, `splitting_floors_the_half` (zvE 90,001 → same as 90,000), `soli_below_limit_zero`, `soli_mitigation_42000` (154.70), `soli_full_50000` (1,106.70), `soli_floors_cents`, `church_8pct_on_17670` (1,413.60), `church_one_member_half` (one owner in church → 706.80), `church_two_states` |
| `tariff.tax_effect`, `income.zve_base` | `marginal_rate.yaml` | `deductions_default_60k` (12,966.00), `deductions_default_45k` (10,041.00), `deductions_capped_at_bbg` (gross 120,000), `household_81993`, `effect_negative_result` (−2,808.00, 35.3 %), `effect_positive_result` (+1,013.04), `effect_zero_shows_slope`, `bescheid_replaces_estimate`, `override_rate`, `carried_forward_2028`, `no_inputs_none`, `tariff_reused_for_2030` (`reused_from: 2026`) |
| `year_figures` (actuals) | `actuals.yaml` | `anlage_v_2026_case_a` (§5.3), `current_year_partial`, `no_income_inputs`, `included_tenancy_no_umlagen`, `purchase_flows_not_cash` |
| `synthesize` + `project` | `projection.yaml` | the three scenarios of §10.3, each with its full year table and the sale block; `upkeep_excluded_from_15pct`; `rent_growth_from_second_year`; `vacancy_two_months` |
| `sale`, `xirr`, `cagr` | `sale.yaml`, `irr.yaml` | §5.5 |
| `registry.tariff`, `Tariff` validator | `tests/rules/test_tariffs.py` (Python, like `test_tax_rulesets.py`) | zones contiguous and in order; 2026 figures pinned; a missing year falls back and marks `reused_from`; `rule_set_or_latest` |

### 10.3 The three scenarios (the "spreadsheet")

All three use `anlage_v_2026.yaml` case A's property, loan and 2026 facts, `today = 2026-12-31` (2026 is the last complete actual year, 2027 the first projected one, so `n = 1` in 2027), owners A (gross 60,000) and B (45,000), both BY and in church (household zvE 81,993.00), the fixture's tenancy (`flat_rate`, 850.00 + 180.00 a month), Hausgeld 180/40/100 a month, Grundsteuer 240.00 a year (the two quarterly 60.00 lines annualised, §5.4), management 25.00 and bank fees 4.90 a month, upkeep 600.00, selling costs 3.57 %, follow-up rate null unless stated. Loan years from `build_schedule`: 2027 interest 8,288.01 / principal 3,711.99 / balance 234,779.24; 2028 8,156.00 / 3,844.00 / 230,935.24; 2029 8,019.28 / 3,980.72 / 226,954.52; 2030 7,877.69 / 4,122.31 / 222,832.21; 2031 7,731.10 / 4,268.90 (at 4.5 % follow-up from 1 Aug 2031: 8,466.77 / 3,533.23 / 219,298.98); 2032 at 4.5 %: 9,823.97 / 2,176.03. E₀ = 300,000 + 25,000 − 240,000 = 85,000.00 (the fixture's owner top-up of 85,000.00 is the evidence shown beside it).

**A `baseline_no_growth_tax_free_sale`**: all growth 0 %, sale 2036 (31 Dec 2036 ≥ 11 Mar 2036 → tax-free). Checkpoints 2027: income 12,360.00 (10,200.00 rent + 2,160.00 NK); deductible costs 4,680.00 AfA + 8,288.01 interest + 600.00 upkeep + 2,160.00 + 240.00 umlagefähig + 480.00 + 300.00 + 58.80 (the 1,200.00 Rücklage is tracked, not deducted, D57); result **−4,446.81**; tax effect **−1,518.48** (34.1 %); cash out 17,038.80 (interest, principal 3,711.99, 2,638.80 running costs incl. the Rücklage and upkeep, 2,400.00 umlagefähig); net cash **−6,197.28**; value 300,000.00; equity 65,220.76. 2028: result −4,314.80, effect −1,473.12, net cash −6,151.92. Sale: taxable false, sale tax 0.00, bridge sums to the profit.

**B `growth_and_rate_switch`**: 2 % value, rent and cost growth, follow-up rate 4.5 % on the loan, sale 2036. Checkpoints 2027 = scenario A's (growth starts in the second year); 2028: rent 10,404.00, NK 2,203.20, Hausgeld 2,203.20 / 489.60 / 1,224.00, Grundsteuer 244.80, management 306.00, bank fees 59.98, upkeep 612.00, result **−4,144.38**, effect −1,414.80, cash out 17,139.58, net cash −5,947.18, value 312,120.00 (= 300,000 × 1.02²); 2031 interest 8,466.77 (the switch inside the year, as `build_schedule` splits it).

**C `useful_life_and_sale_inside_ten_years`**: a `useful_life` input of 21 years from 2027 (Gutachten), 3 % value growth, 0 % rent and cost growth, sale 2030. AfA (confirmed against `rules/tax/afa.py` on 9 Nov 2026): 2026 statutory 2,340.00; 2027–2030 **11,031.43** each (book value 231,660.00 over 252 months, then 220,628.57 / 240, 209,597.14 / 228, 198,565.71 / 216); claimed through 2030 46,465.72. 2030: result **−10,387.92** (interest 7,877.69), rental effect −3,488.40, net cash −8,167.20. Sale 31 Dec 2030 < 11 Mar 2036 → taxable; value 337,652.64; selling costs 12,054.20; gain **47,064.16** (= 337,652.64 − 12,054.20 − (325,000.00 − 46,465.72)), 23,532.08 per spouse ≥ 1,000 → fully taxable; sale tax **17,422.56** (= T(81,993 − 10,387.92 + 47,064.16) − T(81,993 − 10,387.92)); net proceeds 337,652.64 − 12,054.20 − 17,422.56 − 222,832.21 = 85,343.67. The same case with the sale in 2037 and no useful-life row is the control (statutory AfA, tax-free).

`marginal_rate.yaml`'s `effect_negative_result` uses a stated base of 90,000 (−2,808.00) and `actuals.yaml` the owners' estimate of 81,993 (−2,687.04) for the same −7,948.66 on purpose: one tests the tariff arithmetic on a round base, the other the full chain from gross pay. The step 0 coder fills the remaining rows of each table by the same arithmetic (a spreadsheet kept outside the repo is fine; the YAML is what counts) and the review checks them line by line.

## 11. Implementation steps

| Step | Delivers | Migration | Done when | Size | Review |
| --- | --- | --- | --- | --- | --- |
| 0 **Spec PR** | this doc; `schemas/performance.py` and the §7 changes; migration 0016 tested up and down; `tariffs/2026.yaml` + `registry.tariff`, `rule_set_or_latest`; the six fixtures and `fixture_models_phase4.py`; `test_tariffs.py`; `tables.py` mirror; ARCHITECTURE edits §12 | written, not deployed | fixtures validate, schema tests green, `uv run pytest` green | M: 1 coder | **Yes**: FOCUS the 2026 tariff figures against §32a, the three scenarios' arithmetic, the migration's carry-over and CHECKs |
| 1 **Tariff, income, owners** (coder A: `rules/tax/tariff.py`, `rules/performance/income.py`, `services/income_inputs.py`, `api/routes/income_inputs.py`, `services/owners.py`, the Eigentümer page) | §5.1, §5.2, D83/D84 inputs with events, owner fields, the "Steuerwirkung" card on Steuern | **0016** applied | `tariff.yaml`, `marginal_rate.yaml` pass; Luis's owners carry incomes and the Steuern page shows the 2026 effect with its source | M | **Yes** (money, persisted state): FOCUS zone boundaries, splitting rounding, Soli Milderungszone, append-only rows |
| 2 **Actuals, projection, sale** (coder B: `rules/performance/year_figures.py`, `synthesize.py`, `projection.py`, `sale.py`, `irr.py`, `services/performance.py`, `services/scenarios.py`, `api/routes/performance.py`) | §5.3–5.5, D85–D89; `year_figures` and `project` take the tariff and inputs as parameters, so B codes against step 0's schemas without A's loader | — | `actuals.yaml`, `projection.yaml`, `sale.yaml`, `irr.yaml` pass; Luis's real projection renders with every cell's sources | L | **Yes** (money): FOCUS nothing counted twice between cash and tax, the synthetic rows' periods and due dates under §11, the §23 add-back, upkeep excluded from the 15 % test |
| 3 **Planung screen** (coder C: `frontend/src/app/(app)/planning/**`, `Sidebar.tsx`) | §9, mockup 05; starts when step 2's routes are merged (the OpenAPI types), the static layout earlier | — | the screen shows the three fixture scenarios through the API in a Playwright flow; levers recalculate; "Szenario speichern" writes a row | M | No (no money computed in the frontend; CI proves the rest) |
| 4 **Closing** | Luis's real projection compared with his own spreadsheet for his levers; the kitchen line (D80) confirmed in the projection; §12 status rows | — | the three fixtures pass and Luis's spreadsheet agrees to the euro on year 1 | S | No |
| 5 **Betriebskostenabrechnung** (optional, unscheduled, D91) | generator for `prepayment` tenancies only; own spec amendment first | — | one settlement reproduces a hand calculation (synthetic) | M | Yes (money) |

Steps 1 and 2 run in parallel on disjoint paths after step 0; both touch `schemas/performance.py` only through step 0's version (a change there is a spec amendment). Step 3 after 2. Phase 4 is done after step 4; step 5 only if Luis wants it (Q1).

## 12. ARCHITECTURE.md edits this spec requires (made in the spec PR)

1. **Performance and projection → Actuals** "tax effect (result × that year's marginal rate)" → "tax effect (the tax with the rental result minus the tax without it, on the derived taxable income; phase 4 spec D82)"; **Per projected year → 3.** "tax effect = −result × marginal rate" → "tax effect = T(zvE + result) − T(zvE)"; the sentence "Annualized return … an IRR on dated cash flows is a phase-4 upgrade" → "and an IRR on dated cash flows (phase 4)".
2. **Marginal rate (decided 6 Nov 2026) → 3.** append "The rate the screen shows is the effective rate on the rental result (effect ÷ result), not the tariff's slope (D82)"; **→ 1.** append "AV contributions are not deducted (the §10 Abs. 4 cap is used up by KV and PV, D84)".
3. **Domain model → Owner**: "expected gross pay per year, church tax yes/no, optional marginal-rate override" → "expected gross pay per year and deductions (`income_input`), church tax yes/no and Bundesland, optional per-year rate override; the typed `marginal_tax_rate` field is dropped (D90)".
4. **Tax rules engine → Private sale (§23) row**: data column gains "AfA claimed from the recomputed drafts; Freigrenze €1,000 per spouse (D88)".
5. **Delivery phases → phase 4 row**: Delivers gains "Spec `docs/phases/phase-4.md` drafted 9 Nov 2026; the Betriebskostenabrechnung generator moved to an optional step pending Luis (D91)"; Done when: "Projection matches a spreadsheet model for 3 scenarios" (drop "one settlement generated from real 2026 data" if Luis agrees).
6. **Input for the phase 4 spec: flat-rate Nebenkosten** paragraph: append "→ phase 4 spec D91/Q1: no settlement is generated for Luis's leases; the generator is an optional step".
7. **Decisions table**: a row "Phase 4 spec (9 Nov 2026)": tariff as per-year data with latest-year fallback (D81); tax effect as a tariff difference (D82); `income_input` and `projection_scenario` tables (D83, D87); projection via the phase 3 draft over synthesized payments (D86); §23 add-back of the building AfA, Freigrenze per spouse (D88); `owner.marginal_tax_rate` dropped (D90); migration 0016.

## 13. Questions for Luis

Plain language; each with what it means, why it matters, and the recommended answer.

1. **Drop the "Nebenkostenabrechnung" from this phase?** A Betriebskostenabrechnung is the yearly statement a landlord sends when tenants pay running costs as a *prepayment* (Vorauszahlung) that is settled against the real costs. Your leases charge a *flat rate* (Pauschale) or an all-in rent, which by contract is never settled, so the app would have nothing to generate, and the phase's current done criterion ("one settlement generated from real 2026 data") can never be met. **Recommendation:** take the generator out of phase 4 and keep it as an optional step to build only if you ever sign a prepayment lease (D91). The alternative is to build it now against a made-up lease, which costs a coder's day for a feature nobody uses.
2. **Which incomes do you want to enter, and for whom?** The tax saving the app shows depends on the household's taxable income, which it estimates from each owner's expected gross pay for the year (the standard deductions are estimated for you and can be changed). Both you and your wife would each enter one yearly figure; when the tax assessment (Einkommensteuerbescheid) for a year arrives, you type its "zu versteuerndes Einkommen" and the estimate is replaced for that year. **Recommendation:** enter both gross figures for 2026 now (rough is fine; the screen says "Schätzung"), set church tax yes/no per person and Bayern as the state. If you prefer not to store incomes, a typed percentage per year still works (the override), as today.
3. **Sell on paper when?** The projection needs a sale year to end on; the app assumes the sale on 31 December of that year and warns when it is inside the ten-year period after your purchase contract (the sale is then taxed). **Recommendation:** default to the first year after the ten years are up (2036 for a contract in 2026), which is what the mockup shows; you can slide it.
4. **Assumptions you want as defaults:** value growth, rent increase and cost inflation (all 2 % in the mockup), upkeep €600 a year, selling costs 3.57 % of the price (a typical broker fee incl. VAT; notary is usually the buyer's), and the loan's interest rate after the fixed period (default: unchanged). **Recommendation:** keep these defaults; they are editable per scenario and saved with it.
5. **Kitchen and useful life:** the shorter useful life from your Gutachten (21 years) and the fitted kitchen's own 10-year depreciation (being built as D80) make the yearly AfA much higher than the statutory 2.5 %, which raises the tax saving each year but also raises the taxable gain if you sold within ten years (the AfA claimed is added back). The projection will show both. **No answer needed**, just so the larger "AfA" line is not a surprise.

## 14. Open questions for the orchestrator

- **Q-O1 Splitting rounding.** Whether the half of the joint zvE is floored before the tariff is applied (the fixture floors it; §32a Abs. 1 S. 1 floors the zvE, Abs. 5 is silent about the half). Off by at most one euro of ESt; confirm against the BMF Programmablaufplan or an official calculator in step 4 and amend `tariff.yaml` if different.
- **Q-O2 Kinderfreibeträge and other zvE items.** §51a Abs. 2 computes Soli and church tax on the ESt with child allowances deducted; phase 4 ignores them, so Soli and church tax are overstated slightly for a household with children, and the Progressionsvorbehalt (Elterngeld) is ignored, understating the rate. Decide whether a child count becomes an owner field now or with the household forms (phase 8); the override covers it meanwhile.
- **Q-O3 Pflegeversicherung 3.6 %.** The statute text still prints 3.4 %; the 2025 Verordnung raised it. Verify the Verordnung's citation for `2026.yaml` (effect: 0.1 % of gross on the employee's deductions).
- **Q-O4 D80 line key** for the kitchen AfA and the `AfaLine` shape after D80 lands (§5.6), before step 2 starts.
- **Q-O5 Current year in the IRR.** The current year counts only what is booked; the remaining months are not projected (D86). The alternative (synthesize the rest of the current year) is more accurate for the IRR and less hand-computable; revisit if Luis finds the "bisher" year misleading.
