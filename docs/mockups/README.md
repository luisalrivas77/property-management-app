# UI mockups

Nov 5, 2026 (01–06) and Nov 6, 2026 (07–12) · from the design canvas made with the architecture spec

Fourteen screens that show what Hausbuch should look like and how its flows fit together. Each one
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
Dokumente. Links to Posteingang, Immobilien and the Anlage V tab open 02–05.

| Screen | Files | Phase | What it shows |
| --- | --- | --- | --- |
| Übersicht (Haushalt) | `07-household-overview.png` / `.html` | 6 (grows with 7–9) | Net worth, last month's cash flow and savings rate, cash reserve in months, open inbox items, the AI sync notice, asset mix (property net of loan, ETF, bank, crypto), the property card, upcoming dates (Verlustbescheinigung, year-end crypto holdings statement, Vorabpauschale, the next lot turning tax-free), recent activity |
| Konten | `08-accounts.png` / `.html` | 6 | Every account grouped as bank, depot, crypto and liabilities, with source (camt export, quirion PDFs, BISON CSV, Helius, balance-only RPC), owner chip (Luis, Partnerin, Gemeinsam 50/50), balance and reconciliation status; how data gets in; internal transfers never counted |
| Anlagen · Übersicht | `09-holdings.png` / `.html` | 6–7 | Tab 1 of 3 (Luis, 7 Nov 2026: ETF and crypto as separate tabs, plus an overview): total value, unrealized gain, monthly savings plans, the ETF/crypto split bar with a summary card per class, largest positions across both, value per owner |
| Anlagen · ETF | `09b-holdings-etf.png` / `.html` | 6 | The quirion depot: value, gain, savings plan, income so far; positions with units, price, value, cost and gain; weighting; Sparer-Pauschbetrag used and the Freistellungsauftrag split; the Vorabpauschale estimate; recent Postbox documents |
| Anlagen · Krypto | `09c-holdings-crypto.png` / `.html` | 7 | Crypto value, gain, what can be sold tax-free today and the next lot to turn tax-free; one card per crypto account with its owner; coins table; selecting a row shows its lots (date, units, cost, source, "steuerfrei ab …") |
| Haushalt | `10-household-cashflow.png` / `.html` | 6 | Income, spending and savings rate for a month and the 12-month average, monthly surplus chart, spending by category, income rows, and transfers to own accounts counted as saved, not spent |
| Steuern · Anlage SO | `11-tax-anlage-so.png` / `.html` | 8 | The tax year with tabs V, KAP, SO and an owner switch (Luis / Partnerin); §23 result with the €1,000 Freigrenze meter, the undecided LP positions with both results, sales and swaps with holding time, §22 Nr. 3 rewards against €256, the filing checklist, what BISON reports under the wife's name, exports |
| Planung · Haushalt | `12-planning-household.png` / `.html` | 9 | Investable amount per month and how it is derived, the existing savings plans, reserve gap, two levers (reserve target, buffer), three ways to reach the reserve target, and the AI explanation panel in own-use mode (names existing accounts only) |

The Anlagen, Steuern and Planung screens are interactive in the original (row selection, owner
switch, levers); the static HTML shows one state. Owners appear as "Luis", "Partnerin" and
"Gemeinsam 50/50"; every amount, coin and address is sample data.

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
