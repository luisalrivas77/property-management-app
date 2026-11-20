# UI mockups

Nov 5, 2026 (01–06), Nov 6, 2026 (07–12) and Nov 18, 2026 (12 redrawn, 12b, 12c, 12d, 13, 13b and 10b, for phase 9) · from the design canvas made with the architecture spec, and the phase 9 spec for the later ones

Twenty screens that show what Hausbuch should look like and how its flows fit together
(plus the layout overhaul drafts 14–17 of 20 Nov 2026, three design directions that are not
approved; see the section at the end). Each one
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
| Haushalt · geplant | `10b-household-planned.png` / `.html` | 9 | Drawn 18 Nov 2026 and approved by Luis the same day (phase 9 D263, step 3b). Haushalt for a running month (August 2027, „bisher“, Stand 18 August) with the line „3 Umsätze warten in Zu prüfen“ as a link: the column „Geplant“ only for the two categories the saved scenario plans (Supermarkt 682,50 €, Urlaub & Reisen 500 €), the difference in words („34 € mehr als geplant“, „120 € weniger als geplant“), every other row's Geplant cell empty; neutral styling, no colour, mark or alert (D263 (e)); the third wording, „wie geplant“ (less than 1 € apart), is named in a mockup note since no category hits it that month; the averages headed with their basis (5 Monate, März bis Juli 2027) |
| Steuern · Anlage SO | `11-tax-anlage-so.png` / `.html` | 8 | The tax year with tabs V, KAP, SO and an owner switch (Luis / Partnerin); §23 result with the €1,000 Freigrenze meter, the undecided LP positions with both results, sales and swaps with holding time, §22 Nr. 3 rewards against €256, the filing checklist, what BISON reports under the wife's name, exports. Specced in `docs/phases/phase-8.md` (16 Nov 2026, PR #384, not built); its figures are samples, and its LP box shows both positions while the spec proposes the household's position B (D209, Q-O97) |
| Planung · Haushalt | `12-planning-household.png` / `.html` | 9 | Redrawn 18 Nov 2026 from the phase 9 spec §14: the four cards (Anlegbar pro Monat, Schon angelegt, Reserve heute with months, Bis zum Reserveziel fehlen) and the basis line; the levers Reserveziel and Puffer; „Weitere Annahmen“ open (income changes, one-offs with the proposed tax refund and an unchosen month, reserve accounts, savings plans); „Szenario speichern“; the ways to the reserve target, one row per path, none highlighted; „So entsteht die Zahl“ with one row opened; the projection card „Vermögen in einigen Jahren: eure Annahme“ empty, with the to-do „Annahme eintragen“; the KI panel with the template text („Standardtext“) |
| Planung · Haushalt, Projektion | `12b-planning-projection.png` / `.html` | 9 | The projection card filled: the rates a member typed (sample values, marked „Beispielwerte, kein Vorschlag“), the flat's growth from its scenario, the assumption lines above the figures, the net worth by class at each 31 December, the change split into five rows with „Aus euren Annahmen“ on its own, and why the calculation ends that year |
| Planung · Haushalt, wenige Monate | `12c-planning-few-months.png` / `.html` | 9 | The page with fewer than three closed months: no figures, how many months are closed, and a to-do with its button for each thing that keeps the next month open (Kontoauszug hochladen, Zu prüfen öffnen, Gegenbuchung zuordnen) |
| Planung · Haushalt, Ausgaben durchspielen | `12d-planning-spending-play.png` / `.html` | 9 | Drawn 18 Nov 2026 and approved by Luis the same day (phase 9 D263, step 3b). The section „Ausgaben durchspielen“ below „Weitere Annahmen“ (the four cards above it as in 12, at their saved values): one row per spending group with „Ø pro Monat“ and an empty „Geplant pro Monat“ field, „Lebensmittel und Drogerie“ opened into its subcategories; the two filled rows of the spec's example (§7.6 (6)): Supermarkt +5 % from Juni 2027 with no end, and Urlaub & Reisen 500 € from Juli to September 2027, each with its months and the € / % switch; the group's own field greyed while a subcategory is planned, and „Sonstige Lebensmittel“ with no field of its own (§7.6 (1)); „Neue Ausgabe hinzufügen“; the comparison „heute (ohne Einmalausgaben)“ against „mit euren Änderungen“ (Sparquote, Anlegbar pro Monat with the July to September months on their own lines, the month the reserve target is reached on „Alles weiter wie bisher“, and the net worth on 31 December 2032 under 12b's sample rates), in ink only, with no recommending sentence; „nothing is saved until „Szenario speichern““ |
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
- **Drawn 18 Nov 2026 for D263 (step 3b) and approved by Luis the same day („Build as
  drawn“):** 12d (planning the spending) and 10b (the planned figures beside the month's real
  ones). The top cards keep the saved values while a member types; only the comparison moves.
  Where a drawing and the spec disagree, the spec wins. Their figures continue §15.2 and §7.6 (6); the reserve month (Dezember 2028 → März 2029)
  and the net worth on 31 December 2032 (261.470 € → 258.700 €) were computed by a script
  from that example, the latter only under 12b's sample rates. The group is labelled „Urlaub & Reisen“ as the spec writes it; the build uses the app's own group names (Luis, 18 Nov 2026).
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

**Layout on the phone and the computer (18 Nov 2026).** Luis asked for new mockups, for the
phone and the computer alike („Not only for the phone but for the pc as well.“), before any
change: a modern look with little scrolling or clicking and clear, understandable information.
See `docs/ARCHITECTURE.md`, „Input for a later phase: the layout on the phone and the
computer“. This paragraph said until 20 Nov 2026 that no mockup covered a phone width; that
was already not quite true (06 is drawn at 390 px), and the drafts below now cover every
screen they redraw at 390 px.

## Layout overhaul (drafted 20 Nov 2026; menu and detail panels chosen, the rest open)

**Nothing in this section is built, and only the four choices under „Chosen so far“ are agreed.** These are drafts for Luis to choose from:
three design directions, each drawing the same four screens at phone width (390 px) and
computer width (1440 px). The app keeps its current layout until he picks a direction (or a
mix), and the choice is recorded in the Decisions table of `docs/ARCHITECTURE.md`. The colours,
font and tokens are today's (`frontend/src/app/globals.css`); only the arrangement changes.
Every figure is invented sample data, the same in all three directions (net worth 226.900 €,
an ETF depot of 39.000 €, Girokonto A of „Luis“, and so on), so the directions can be compared
screen by screen; none is a household figure or a fixture.

**Start with the gallery.** `layout-gallery.html` is one self-contained page with every draft
embedded: pick a direction and a screen, and the phone and the computer version show side by
side (the computer one scaled down to fit, „Originalgröße“ to see it at full size). Open it
from the file, no server needed.

### Chosen so far (Luis, 20 Nov 2026)

Luis mixes the directions screen by screen („I want to mix. Let's go one by one.“).

1. **The menu is C „Mobil zuerst“ with one Immobilien entry** (`14-C2-immobilien-phone`, `-desktop`). The bottom bar
   holds Übersicht, Zu prüfen, Haushalt, Anlagen and „Mehr“; „Mehr“ lists Konten, Immobilien, Steuern, Planung,
   Dokumente and Einstellungen. Mieter and Eigentümer are no menu entries: Immobilien shows one card per property with
   its tenancies and owners' shares. The owners' income for the tax (pay, church tax, the pay documents) and
   „Kinder und Haushalt“ move to a Steuern tab „Einkommen & Haushalt“ („Let's move the tax stuff from the Immobilien
   to the tax section“; „I like the mock up“).
2. **Details open beside the list, everywhere in the app** („You can make this sliding from the side when clicking a
   design choice for the whole app“). Tapping a row or a card (a bank line, a tenancy, an owner, a document, a
   position, a to-do) never leaves the page: on the computer its details fill the side panel on the right; on the
   phone they slide in from the right as a panel over the page, the page dimmed and still visible at the left edge,
   closed with ✕, „Zurück“ or a swipe to the right, and the page underneath keeps its scroll position. The panel
   shows what decides something and the buttons that act on it; a full page („… öffnen“) stays one tap further for
   what does not fit. Drawn in 14-C2 (a tenancy and an owner); every later mockup follows it.
3. **Übersicht is B's tiles with the cash flow under the net worth** (`15-B2-overview-phone`, `-desktop`; „B but
   with the cashflow under the netto vermögen“; „In general looks good“). A tapped tile slides in its summary from
   the right with one button into its section. A key figure's mark is a dot before its name, never a colour word
   (an empty dot while no limit is set); the kinds of assets carry their colour as a stripe at the tile's edge, so
   a dot always means a mark („You can go with the dots“). The kinds' colours are never green, yellow or red
   (muted slate blue, soft blue, lavender and light grey), so they cannot be read as a mark („Otherwise it's still
   confusing“; „another color combination that look better together and not bright“).
4. **Konten › Umsätze is C's list under B's account card** (`16-C2-table-phone`, `-desktop`; „Mix of mobil zuerst
   for the bank lines and dashboard kompact for the account summary“; „Looks good“). The card shows the balance and
   the month's money in and out, spending without the transfers between own accounts (named under the figures, a
   tile of their own on the computer); the lines are grouped under weekday headings, each with an initial, payee,
   category, amount and its status as „✓ Gebucht“ or a pill, never a dot; a tapped line opens beside the list.

### The three directions

**A · Ruhig & klassisch.** Today's app, tidied: the sidebar stays on the computer but slimmer,
the cards get denser, and on the phone the menu moves into a bar at the bottom. It optimises
for little to relearn; the trade-off is that it saves the least space of the three on the
computer.

**B · Dashboard kompakt.** Figures first: smaller type, everything in tiles on a grid, a dark
icon rail on the computer that opens to show its names, and on the phone a top bar with
„Menü“ that opens a full-screen menu of tiles with a search box. It shows the most at a glance
(Übersicht and Planung fit on one computer screen without scrolling); the trade-off is density:
smaller text and smaller tap targets, and on the phone every place outside the current page is
one tap further away than with a bottom bar.

**C · Mobil zuerst.** Built for the thumb: 16 px type, touch targets of 48 to 56 px, a bottom
bar on the phone, the kinds of investment as cards swiped sideways, and sections that fold. On
the computer it keeps one centred column (720 px) with a side panel (360 px) for the details of
what is selected, and tabs in a top header instead of a sidebar. It is the most comfortable on
the phone and the same on both devices; the trade-off is length (the phone pages are the
longest of the three) and unused width on a large computer screen.

### Files

| File | What it shows, and what it changes against today |
| --- | --- |
| `layout-gallery.html` | Every draft below in one page with a switcher for direction and screen, phone and computer side by side; the gallery's own frame follows the light or dark setting, the drafts inside it are drawn light |
| `14-A-nav.png` / `.html` | The phone menu, closed and open: a bottom bar with Übersicht, Zu prüfen (with its count), Haushalt, Anlagen and „Mehr“, which opens the rest in groups (Geld, Immobilie, Steuern & Planung, Ablage). Today the sidebar is one flat list of eleven entries, which on the phone wraps over several lines at the top of every page |
| `15-A-overview-phone.png` / `.html` | Übersicht on the phone: net worth with its sparkline, the four kinds of investment as a 2×2 grid, Zu prüfen with the coming dates, the three key figures in one row. Today the cards stack one per row below the wrapped menu |
| `15-A-overview-desktop.png` / `.html` | Übersicht on the computer: a slimmer sidebar, net worth, Zu prüfen and the month's cash flow in the first row, a card per kind of investment with a link into its area, the key figures in one strip |
| `16-A-table-phone.png` / `.html` | Konten › Umsätze on the phone as two-line rows (payee and amount, date with category and status), with the balance and filters above; no sideways scrolling. Today the table (Buchungstag, Gegenseite, Verwendungszweck, Betrag, Zuordnung) sits in a box that scrolls sideways on a narrow screen |
| `16-A-table-desktop.png` / `.html` | The same table on the computer, denser, with the account's balance above it |
| `17-A-planning-phone.png` / `.html` | Planung › Haushalt on the phone: the four cards as a 2×2 grid, the box „Was eure Änderungen bewirken“ as stacked rows, every other section folded to its title and a one-line summary |
| `17-A-planning-desktop.png` / `.html` | Planung › Haushalt on the computer in two columns, the sections folded except the levers and the ways to the reserve target |
| `14-B-nav.png` / `.html` | The phone menu: a top bar with the inbox count and „Menü“, which opens a full-screen menu of tiles in three columns with a search box; no bottom bar |
| `15-B-overview-phone.png` / `.html` | Übersicht on the phone as small tiles: net worth with a split bar by kind, the four kinds, the key figures with their marks, Zu prüfen, the coming dates and the cash flow, all of it within the first screen |
| `15-B-overview-desktop.png` / `.html` | Übersicht on the computer on one screen: a six-column grid with net worth, the four kinds, the key figures, cash flow, Zu prüfen with the coming dates, twelve month-ends as bars and the latest lines; the icon rail opened |
| `16-B-table-phone.png` / `.html` | Konten › Umsätze on the phone as dense one-line-plus rows with a coloured status dot; balance, in and out in one card above |
| `16-B-table-desktop.png` / `.html` | The table on the computer with four figure tiles above it and a running balance column, the rail closed to icons |
| `17-B-planning-phone.png` / `.html` | Planung › Haushalt on the phone: the four cards as tiles, the effects as a compact table, the sections as a list of folded rows |
| `17-B-planning-desktop.png` / `.html` | Planung › Haushalt on the computer on one screen, three columns with every section open |
| `14-C-nav.png` / `.html` | The phone menu: a large bottom bar (Übersicht, Zu prüfen, Haushalt, Anlagen, „Mehr“); „Mehr“ opens quick actions (Hochladen, Foto aufnehmen, Suchen) and the rest as large rows with a one-line description |
| `14-C2-immobilien-phone.png` / `.html` | Luis's pick for the menu (20 Nov 2026, not yet approved as drawn): C's bottom bar and „Mehr“, but „Mehr“ lists one Immobilien row instead of Immobilien, Mieter and Eigentümer; Immobilien is one card per property with its tenancies, its owners and the rows Darlehen, Kauf & Nebenkosten, Unterlagen, Dokumente, Verlauf; a tapped tenancy or owner slides in from the right as a panel over the card (the phone's version of the computer's side panel), an owner showing only the share; the owners' income for the tax (pay, church tax, the pay documents) and „Kinder und Haushalt“ move to Steuern › „Einkommen & Haushalt“ (Luis, 20 Nov 2026: „Let's move the tax stuff from the Immobilien to the tax section“) |
| `14-C2-immobilien-desktop.png` / `.html` | The same on the computer: „Mehr“ as a drop-down with one Immobilien row, and the Immobilien page as one card (tenants and owners on the left, the rows about the flat on the right) with the selected tenancy in the side panel |
| `15-B2-overview-phone.png` / `.html` | Übersicht drawn for Luis (20 Nov 2026, „B but with the cashflow under the netto vermögen“), chosen the same day: direction B's tiles inside the chosen menu C, the month's cash flow directly under the net worth, then the four kinds of investment, the three key figures, Zu prüfen and the coming dates; a tapped tile slides in a summary from the right with a button into its section („Zu Anlagen › ETF-Depot“, „Zu Haushalt › Oktober“, „Zu Planung › Kennzahlen“), drawn for the ETF depot, the cash flow and the savings rate; revised the same day at Luis's word: no colour words, a key figure's mark is a dot before its name (an empty dot when no limit is set), and the kinds of assets carry their colour as a stripe at the tile's edge, so a dot always means a mark, and in colours that are never green, yellow or red (muted slate blue, soft blue, lavender and light grey; Luis: „Otherwise it's still confusing“, then „not bright“) |
| `15-B2-overview-desktop.png` / `.html` | The same on the computer: a six-column grid with the cash flow under the net worth, the month-ends as bars and the coming dates; a clicked tile's summary slides in from the right over the page with the same button into its section |
| `16-C2-table-phone.png` / `.html` | Konten › Umsätze drawn for Luis (20 Nov 2026, „Mix of mobil zuerst for the bank lines and dashboard kompact for the account summary“), chosen the same day („Looks good“): direction B's account card on top (Kontostand, Eingänge and Ausgänge of the month, „Konto wechseln“; Ausgänge without the transfers between own accounts, named under the figures), then direction C's list grouped under weekday headings, each line with an initial, payee, category, amount and its status as „✓ Gebucht“ or an „Offen“ or „Vorschlag prüfen“ pill, never a coloured dot (a dot is a mark); a tapped line slides in from the right: a proposal with „Bestätigen“, „Andere Kategorie“ and „Künftig immer so zuordnen“, an open line with category choices and „Beleg hochladen“, and „Konto wechseln“ as a list of accounts |
| `16-C2-table-desktop.png` / `.html` | The same on the computer: four figure tiles from B (with the month's transfers as their own tile), C's day-grouped list as rows with the purpose under the payee, and the clicked line in the side panel on the right |
| `17-C2-planning-phone.png` / `.html` | Planung drawn for Luis (20 Nov 2026, „I would go with mobil zuerst. But there's too many tiles on this section. We should try to split them up into their own sections“), awaiting his approval: direction C split into five sections chosen by chips (Budget, Reserve, Anlegen, Vermögen, Immobilie), all five chips visible at phone width (two rows), the scenario a dashed row on top („gespeichert“, or „1 Änderung, nicht gespeichert“ once a category is planned, „Annahmen“). **Budget** is new (needs and wants): the month's income with one bar split into Notwendiges, Wünsche and Sparen (group colours as edge stripes, never green, yellow or red), the housing share as its own line, an empty dot on each until „Eigene Ziele festlegen“; that sheet takes a percent per group and for housing, empty until typed, with the published splits under „Zum Vergleich“ (50/30/20 and its variants as named by Sparkasse.de, the IWU study for the Mieterbund and Destatis on housing) and one-tap buttons to enter one; after saving, each line with a target shows it and a dot, and a target left empty (housing here) keeps its empty dot („kein Ziel festgelegt“). A tapped group slides in its categories with „geplant“ fields (D263's spending in play, the one place to type amounts) and a category's „gehört zu Notwendiges / Wünsche“ switch. Reserve, Anlegen and Vermögen carry today's cards (the reserve's monthly need named as needs and wants plus what the flat does not carry itself), Immobilie the flat's projection to its sale year; the scenario sheet lists the unsaved changes and draws „Was eure Änderungen bewirken“ with the months before a change starts as „wie bisher“ in grey and the change in bold. Sparen equals the Sparquote (the loan's repayment counted as saving); the numbers reconcile (needs + wants + saving = income). Open for the spec: whether „Ziele speichern“ saves at once or as part of the scenario, whether the one-tap buttons that enter a published split stay (the app proposes no target), whether a category's needs/wants switch is per household, where tax payments and the flat's shortfall fall, and the targets as a lever of the household scenario |
| `17-C2-planning-desktop.png` / `.html` | The same on the computer: the five sections as chips under the title, the scenario row on the right, Budget with its targets, and the clicked group (Wünsche) with its categories in the side panel |
| `15-C-overview-phone.png` / `.html` | Übersicht on the phone: a green net-worth card, the kinds of investment as cards swiped sideways with dots, Zu prüfen with a full-width button, the coming dates, the key figures and the cash flow folded |
| `15-C-overview-desktop.png` / `.html` | Übersicht on the computer: tabs in the header, one centred column, and a side panel with the selected kind (the ETF depot) and the key figures |
| `16-C-table-phone.png` / `.html` | Konten › Umsätze on the phone as a list grouped under weekday headings, each line with an initial, payee, category, amount and status, large enough to tap |
| `16-C-table-desktop.png` / `.html` | The same list on the computer with the selected open line in the side panel: its purpose, account, category choices and „Beleg hochladen“ |
| `17-C-planning-phone.png` / `.html` | Planung › Haushalt on the phone: Anlegbar pro Monat as the green card, the other three as rows, the effects box, the sections folded |
| `17-C-planning-desktop.png` / `.html` | Planung › Haushalt on the computer: one column with „Wege zum Reserveziel“ opened, the effects box and „Szenario speichern“ in the side panel |

### Design choices common to the drafts

**A bottom bar or a top bar on the phone.** Today the sidebar becomes a header that wraps all
eleven entries over several lines, so every page starts with a scroll past the menu. A and C
put the menu in a bar at the bottom that stays on screen: the four places used most are one
tap away with the thumb, and the page starts with its content. B uses a top bar with „Menü“
instead: it leaves more height for figures, at the cost of one more tap to reach another page.
Both were the options shown to Luis on 18 Nov 2026.

**The four entries of the bar are Übersicht, Zu prüfen, Haushalt and Anlagen.** Konten sits
under „Mehr“, because its daily task (deciding what a line is) happens in Zu prüfen, while
Anlagen holds the investments by kind that Luis wants to see more of (the parked „overview by
kind of investment“ in `docs/ARCHITECTURE.md`). Swapping Anlagen for Konten is a one-word
change if he prefers it. The drafts' menus also name Immobilien, as the household design of 6 Nov 2026
listed it; today's sidebar has no such entry (the flat is reached through links from Übersicht, Steuern, Planung and Mieter),
so adding it would be part of the build.

**Tables become rows on the phone.** Konten › Umsätze was drawn because it is the table looked at
most and one of the wide ones; the drafts draw it with seven columns (Kategorie, Saldo and Status
added to today's five), so the row pattern is tested on its widest form. On the phone each line becomes a row showing what decides something (payee,
amount, category, status) and the rest (purpose, running balance) opens with a tap, so nothing
scrolls sideways. The same pattern would apply to the other wide tables.

**Long pages fold into sections.** Planung › Haushalt is the longest page; each section folds
to its title and a one-line summary that already carries its main figure, so the page fits on
far fewer screens and the reader opens only what they need. B on the computer keeps every
section open, since it fits on one screen there.

**The comparison box bolds the stretch the change affects.** In „Was eure Änderungen
bewirken“ the app today prints the first stretch of months in bold even when a change starts
later (a change from January 2027 showed its effect only on a grey line under the unchanged
figure). In every draft the stretch the change affects is the bold one, and the months before
it read „wie heute“. The drafts do not address the second point noted on 18 Nov 2026, a first
stretch that starts in a month already over.

**The dashed line marks the end of the first screen** (844 px, an iPhone's height, less the
bottom bar where there is one), so how much each draft shows before a scroll can be compared.
It is not part of the design. The phone drawings are full-page captures, so the bottom bar,
which would stay fixed on the screen, is drawn once at the end of the page.

**Sample data and marks.** The key figures are drawn with their marks in the confirmed state
(green, amber, none), as in 13b; Verschuldungsgrad carries the sample value 38 % and no limit.
The generator that wrote these files is not in the repository; edit the HTML directly or
redraw.
