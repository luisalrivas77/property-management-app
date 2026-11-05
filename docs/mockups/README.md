# UI mockups

Nov 5, 2026 · from the design canvas made with the architecture spec

Six screens that show what Hausbuch should look like and how its flows fit together. Each one
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

## How to use them

- **Layout, content and flow come from the mockups.** Which facts a screen shows, how they are
  grouped, what links where, and the order of a flow (upload → review → confirm).
- **Visual style stays with the existing frontend** (`frontend/src/app/globals.css`): its
  colours, font, dark mode and the no-component-library rule. The mockups' green palette and
  Manrope font are not adopted unless Luis decides so; if that changes, it is recorded here
  and in `docs/ARCHITECTURE.md`.
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

The 02 screen overlaps with phase 1 screens that are already built. Do not restyle or
restructure those to match the mockup unless a task asks for it.
