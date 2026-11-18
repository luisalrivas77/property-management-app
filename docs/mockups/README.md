# UI mockups

Nov 5, 2026 (01–06), Nov 6, 2026 (07–12) and Nov 18, 2026 (12 redrawn, 12b, 12c, 13, 13b, for phase 9) · from the design canvas made with the architecture spec, and the phase 9 spec for the later ones

Eighteen screens that show what Hausbuch should look like and how its flows fit together. Each one
comes as a PNG (look at it) and a static HTML file (read the exact structure, labels and
spacing). Links between the HTML files work, so you can click through them in a browser.

| Screen | Files | Phase | What it shows |
| --- | --- | --- | --- |
| Overview | `01-overview.png` / `.html` | 2–4 (dashboard grows with each phase) | Rent, deductible costs, estimated Anlage V result, open items, AI processing notice, rent ledger by month, deadlines, recent activity |
| Property and tenant | `02-property.png` / `.html` | 1 | Property facts, current tenancy, document list with tax tags, History timeline from events |
| AI inbox | `03-inbox.png` / `.html` | 2 | Drop zone, imported statement as rows with suggestion and status, a side panel with the model's reasoning, the proposed split and one question |
| Tax year | `04-tax-year.png` / `.html` | 3 | Anlage V draft: income and Werbungskosten lines, each linking to its sources; paperwork checklist; 15% renovation limit; exports |
| Performance | `05-performance.png` / `.html` | 4 | Actuals for the year, projection levers, §23 sale-date check, sale KPIs, equity by year, profit bridge, year-by-year table |
| Mobile capture | `06-mobile-capture.png` / `.html` (390 px) | 2 | PWA camera capture with the recognized invoice and its suggested property and category |

### Household screens (phases 6–9, added 6 Nov 2026)

Drawn from `docs/design/household-finances.md` §1 (layout and screens) and Luis's answers in its
§10. Unlike 01–06 these are in German, as the app is, and they show the nine-entry sidebar the
design agreed: Übersicht, Posteingang, Konten, Anlagen, Haushalt, Immobilien, Steuern, Planung,
Dokumente. Links to Posteingang, Immobilien and the Anlage V tab open 02–05. The app renamed Posteingang to "Zu prüfen" on 8 Nov 2026 (phase 2 spec D48); the mockups of 6 Nov 2026 keep the old label, and those drawn on 18 Nov 2026 (12, 12b, 12c, 13, 13b) use „Zu prüfen“.

| Screen | Files | Phase | What it shows |
| --- | --- | --- | --- |
| Übersicht (Haushalt) | `07-household-overview.png` / `.html` | 6 (grows with 7–9) | Net worth, last month's cash flow and savings rate, cash reserve in months, open inbox items, the AI sync notice, asset mix (property net of loan, ETF, bank, crypto), the property card, upcoming dates (Verlustbescheinigung, year-end crypto holdings statement, Vorabpauschale, the next lot turning tax-free), recent activity. Phase 9's Kennzahlen block, the reserve against its target and the net worth's Verlauf are drawn in 13 and 13b |
| Konten | `08-accounts.png` / `.html` | 6 | Every account grouped as bank, depot, crypto and liabilities, with source (camt export, quirion PDFs, BISON CSV, Helius, balance-only RPC), owner chip (Luis, Partnerin, Gemeinsam 50/50), balance and reconciliation status; how data gets in; internal transfers never counted. Phase 7 step 1 adds the wallet rows to the Krypto group: each wallet with its chains as pills and the count of its masked addresses, its source, owner chip and last sync (`docs/phases/phase-7.md` §14; planned, not built as of 15 Nov 2026) |
| Anlagen · Übersicht | `09-holdings.png` / `.html` | 6–7 | Tab 1 of 3 (Luis, 7 Nov 2026: ETF and crypto as separate tabs, plus an overview): total value, unrealized gain, monthly savings plans, the ETF/crypto split bar with a summary card per class, largest positions across both, value per owner |
| Anlagen · ETF | `09b-holdings-etf.png` / `.html` | 6 | The quirion depot: value, gain, savings plan, income so far; positions with units, price, value, cost and gain; weighting; Sparer-Pauschbetrag used and the Freistellungsauftrag split; the Vorabpauschale estimate; recent Postbox documents |
| Anlagen · Krypto | `09c-holdings-crypto.png` / `.html` | 7 | Crypto value, gain, what can be sold tax-free today and the next lot to turn tax-free; one card per crypto account with its owner; coins table; selecting a row shows its lots (date, units, cost, source, "steuerfrei ab …"). Built in phase 7 step 6, with the lots and the holding period (`docs/phases/phase-7.md` §14; built and deployed 16 Nov 2026, PR #399; this said „planned, not built“ until then) |
| Haushalt | `10-household-cashflow.png` / `.html` | 6 | Income, spending and savings rate for a month and the 12-month average, monthly surplus chart, spending by category, income rows, and transfers to own accounts counted as saved, not spent. Phase 9 heads the average with its real basis instead of "Ø 12 Monate" (see the phase 9 notes below) |
| Steuern · Anlage SO | `11-tax-anlage-so.png` / `.html` | 8 | The tax year with tabs V, KAP, SO and an owner switch (Luis / Partnerin); §23 result with the €1,000 Freigrenze meter, the undecided LP positions with both results, sales and swaps with holding time, §22 Nr. 3 rewards against €256, the filing checklist, what BISON reports under the wife's name, exports. Specced in `docs/phases/phase-8.md` (16 Nov 2026, PR #384, not built); its figures are samples, and its LP box shows both positions while the spec proposes the household's position B (D209, Q-O97) |
| Planung · Haushalt | `12-planning-household.png` / `.html` | 9 | Redrawn 18 Nov 2026 from the phase 9 spec §14: the four cards (Anlegbar pro Monat, Schon angelegt, Reserve heute with months, Bis zum Reserveziel fehlen) and the basis line; the levers Reserveziel and Puffer; „Weitere Annahmen“ open (income changes, one-offs with the proposed tax refund and an unchosen month, reserve accounts, savings plans); „Szenario speichern“; the ways to the reserve target, one row per path, none highlighted; „So entsteht die Zahl“ with one row opened; the projection card „Vermögen in einigen Jahren: eure Annahme“ empty, with the to-do „Annahme eintragen“; the KI panel with the template text („Standardtext“) |
| Planung · Haushalt, Projektion | `12b-planning-projection.png` / `.html` | 9 | The projection card filled: the rates a member typed (sample values, marked „Beispielwerte, kein Vorschlag“), the flat's growth from its scenario, the assumption lines above the figures, the net worth by class at each 31 December, the change split into five rows with „Aus euren Annahmen“ on its own, and why the calculation ends that year |
| Planung · Haushalt, wenige Monate | `12c-planning-few-months.png` / `.html` | 9 | The page with fewer than three closed months: no figures, how many months are closed, and a to-do with its button for each thing that keeps the next month open (Kontoauszug hochladen, Zu prüfen öffnen, Gegenbuchung zuordnen) |
| Übersicht · Kennzahlen | `13-overview-figures.png` / `.html` | 9 | Übersicht before the household confirms its limits: the Liquiditätsreserve card with a bar against its own target, the card „Ampel-Grenzen festlegen“ with every proposal of `rules/health/bands.yaml`, its origin and reference points in plain words, each value editable, and „Übernehmen“; the Kennzahlen block with a plain sentence beside each term and no colour anywhere; the split of the assets; „Vermögen im Verlauf“ with the saved part per month |
| Übersicht · Kennzahlen, Grenzen übernommen | `13b-overview-figures-confirmed.png` / `.html` | 9 | The same page after „Übernehmen“: each marked figure with its green, amber or red mark, its sentence from `bands.yaml` with the figures filled in, and its own limit; „Grenzen ändern“; Verschuldungsgrad and Schulden zu Jahreseinkommen without a mark and with „Grenze eintragen“; the split never marked |

The Anlagen, Steuern and Planung screens are interactive in the original (row selection, owner
switch, levers); the static HTML shows one state. Owners appear as "Luis", "Partnerin" and
"Gemeinsam 50/50"; every amount, coin and address is sample data.

### Phase 9 notes (16 Nov 2026, drawings 18 Nov 2026)

Phase 9 is specced in `docs/phases/phase-9.md` (merged 16 Nov 2026, PR #388); its key figures (step 1, 17 Nov 2026, PRs #426 and #427) and its plan's calculations (step 2, 18 Nov 2026, PR #429) are deployed without a new screen, and the screens are step 3. (This said „not built“ until 18 Nov 2026, stale since step 1 deployed.)

- **Drawn 18 Nov 2026 and approved by Luis the same day (phase 9 Q-O136; „Looks ok for now“):** mockup 12 redrawn, and the
  new 12b, 12c, 13 and 13b. They cover what these notes listed as not drawn: the paths with one
  row per plan and none highlighted (D246), the reserve as the monthly need (D237), the card
  „Vermögen in einigen Jahren: eure Annahme“ empty and filled (D247), the page below three
  closed months (D231), and the new Übersicht with the Kennzahlen, the Verlauf and the marks
  before and after „Ampel-Grenzen festlegen“ (D250, D240). Until Luis approves them, step 3
  does not start from them, and where a drawing and the spec disagree, the spec wins. The rates in 12b (5,0 % and 0,0 %) are sample values and must not
  be read as a default or built as one; the empty fields of 12 are how the app must look.
- **Mockup 10, not redrawn (text only):** the average is headed „Ø N Monate“ with its real
  basis, written as „Basis: N Monate, <Monat> bis <Monat> <Jahr>“ (with fewer than three
  complete months no average is shown, and a to-do stands in its place, D231); the income rows
  gain „Wohnung (Ergebnis des Mietkontos)“; and a closed month whose figures changed after it
  was stored carries the chip „seit dem Abschluss geändert“ (D242).
- As for every mockup, the numbers are sample data and never fixtures; the worked example of
  the phase 9 spec (§15.2) was computed by a script and takes nothing from these screens.
  12b continues that example with its own sample rates and a flat sold in 2033 (§15.2 has
  2035), so that its projection ends in 2032.

## How to use them

- **Layout, content and flow come from the mockups.** Which facts a screen shows, how they are
  grouped, what links where, and the order of a flow (upload → review → confirm).
- **Visual style comes from the mockups too** (decided 5 Nov 2026, see the Decisions table in
  `docs/ARCHITECTURE.md`): green palette, Manrope, cards, pills, the left sidebar. The tokens
  live in `frontend/src/app/globals.css`; use those variables and classes rather than copying
  hex values or inline styles from the HTML files. Still plain CSS with no component library,
  and dark mode stays.
- **Labels stay German.** The mockups are written in English with German tax terms; the app's
  labels are German throughout (`docs/phases/phase-1-step-3.md` §9).
- **The numbers are sample data**, not test fixtures. The flat, tenant and amounts are made up
  (Musterstraße 12, M. Mustermann). Rule fixtures in `backend/tests/fixtures/` are the
  contract; where a mockup figure disagrees with a fixture or with the spec, the fixture and
  the spec win.
- **Every euro figure on these screens is computed by backend rules**, never in the frontend
  and never by a model (architecture principle 1). Where a mockup shows a total, the real
  screen must link it to its sources (principle 2), as the Tax year screen does.
- **The Performance levers are interactive in the original** (value growth, rent increase,
  tax rate, sale year). The static HTML shows one state: 2% / 2% / 42% / 10 years. The
  projection maths is specified in `docs/ARCHITECTURE.md` → Performance and projection.
- **Copy is English placeholder text** with German tax terms kept as-is. Bracketed text such
  as `[tenant email]` or `[your-inbox]` marks values that do not exist yet.

The phase 1 screens that were already built were restyled to this look on 5 Nov 2026; their
structure and German labels were kept. Do not restructure them to match the 02 mockup's
layout unless a task asks for it.
