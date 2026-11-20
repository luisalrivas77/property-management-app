# Phase 10 · Layout und Budget

Spec drafted 20 Nov 2026 from the four mockups Luis chose that day (`docs/mockups/` 14-C2, 15-B2, 16-C2,
17-C2) and his decision on budgets („the household's own targets“, 20 Nov 2026). Not built. Q-O176 (the default sorting
of the spending codes into needs and wants, §9) answered by Luis the same day. On the same day Luis asked for
mockups of every screen not yet drawn, so the build covers the whole app at once (D278 amended); the screens' steps
wait for them, steps 0 and 1 (the Budget's rules and reads, no screen) were built meanwhile and deployed the same day (PR #490). Luis chose the
remaining screens the same day with five changes and two additions (D279–D284: account kinds as tabs, Anlagen at
the newest price, one Steuern tab „Eure Angaben“ for every input, Dokumente by section with a search, a light and
dark choice, and English), so every screen is drawn and the build covers the whole app.

| Step | What | State |
| --- | --- | --- |
| Spec | This file, the edits of §8 | drafted 20 Nov 2026 |
| 0 | Taxonomy field `budget_class`, `rules/budget/references.yaml`, schemas, fixtures; no migration | merged and deployed 20 Nov 2026 (PR #490, images 4b5883d233e9, no migration; review: ship after fixes, all applied; no rollback once a household scenario is saved by this image) (Q-O176 answered the same day; this said „being built“ until then) |
| 1 | `rules/budget/`, `services/budget.py`, `GET /household-budget`, `GET /accounts/{id}/month` | merged and deployed 20 Nov 2026 (PR #490, images 4b5883d233e9, no migration; review: ship after fixes, all applied; no rollback once a household scenario is saved by this image); the review's fixes: the Budget sums each code from the month's private lines (a group netting to 0 had lost its codes), a mark is decided at the precision its sentence prints, an override carried from the default or the previewed row is not refused (`HouseholdPlanPreview.scenario_id`, optional, not yet sent by the screen) |
| 2 | The shell: the menu, the detail panel, the route map, the message catalogue (D284), „Darstellung“ (D283) | merged and deployed 20 Nov 2026 (PR #493, images f175dade8d26, no migration): bottom bar and tabs with „Mehr“, `DetailPanel` (a column beside the list from 1100 px, the sliding sheet below), `frontend/src/i18n/` with `glossary.md`, „Darstellung & Sprache“ per device, `Accept-Language` sent (read by the server from step 4c); `/properties` an interim list until step 4b; no review (frontend only, CI proves it) |
| 3 | Übersicht (15-B2) and Konten › Umsätze (16-C2) | not started |
| 4 | Planung in five sections with the Budget (17-C2) | not started |
| 4b | The remaining screens as drawn in `docs/mockups/` 18–25 (D278 amended) with D279–D282 | chosen 20 Nov 2026 with Luis's changes (20–24 redrawn the same day) |
| 4c | English (D284) | not started |
| 5 | Closing (§7.1) | not started |

## 1. Scope

**In.** The layout Luis chose screen by screen on 20 Nov 2026, built once for phone and computer, and the
needs-and-wants Budget on Planung, against targets the household sets itself.

- The menu (14-C2): a bottom bar on the phone, tabs with „Mehr“ on the computer; Mieter and Eigentümer leave
  the menu; the owners' income for the tax moves to Steuern.
- The detail panel, app-wide in rule, built in this phase for the lists §5.2 names.
- Übersicht (15-B2), Konten › Umsätze (16-C2), Planung (17-C2) as drawn, and every other screen as drawn in
  `docs/mockups/` 18–25 once Luis chooses it (D278 amended), chosen 20 Nov 2026 with D279–D282: content re-arranged into the shell and the panel,
  nothing added that no endpoint serves except what D280 and D282 name.
- A choice of light or dark (D283) and the app in English (D284), both per device.
- The Budget: the month's income split into Notwendiges, Wünsche and Sparen, the housing share beside them,
  the household's own targets, the published splits as reference points.
- The two noted defects of „Was eure Änderungen bewirken“ (ARCHITECTURE.md, „Input for a later phase: the
  layout“).

**Out.** No screen gets content its mockup does not draw (§5.3). No budget reminder, email, to-do or AI sentence. No new ledger
data, no migration. The finer household-purchase reader, the net-worth goal and the other parked ideas stay
parked.

## 2. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D266 | **The shell is 14-C2.** Below 800 px a bottom bar with Übersicht, Zu prüfen (with its count), Haushalt, Anlagen and „Mehr“; from 800 px the same five as tabs in a top bar, „Mehr“ a drop-down. „Mehr“ lists Konten, Immobilien, Steuern, Planung, Dokumente, Einstellungen. Every route stays (§5.1); `/owners` and `/tenants` lose their menu entries, are reached from Immobilien's card and still open by URL. The owners' income for the tax (expected pay, church tax, the pay documents, „Kein Arbeitslohn“) and „Kinder und Haushalt“ move from Eigentümer to a Steuern tab „Einkommen & Haushalt“, the same components, no new API. The tokens and the font stay `globals.css`'s. | Luis, 20 Nov 2026: „I like the mock up“, „Let's move the tax stuff from the Immobilien to the tax section“. Eleven entries wrapping over the top of every phone page was the first complaint (18 Nov 2026). |
| D267 | **One detail panel.** A component `DetailPanel` opens a row's or card's details without leaving the page: on the computer (from 1100 px) a panel on the right beside the list; below that a panel sliding in from the right over the dimmed page, closed by ✕, „Zurück“, Escape or a swipe to the right, the page keeping its scroll position. The panel's state is in the URL hash (`#d-<kind>-<id>`), so the browser's back button closes it and a link can open it. It shows what decides something and the buttons that act on it; a full page („… öffnen“) stays one tap further. | Luis, 20 Nov 2026: „You can make this sliding from the side when clicking a design choice for the whole app“. The hash follows the artifact rule already used by the app's links and keeps back-navigation honest on the phone. |
| D268 | **Übersicht is 15-B2.** Net worth with its month-end bars, the month's cash flow under it, one tile per kind of asset (bank, depot, crypto, property) with its colour as an edge stripe in the four muted tones of 15-B2 (#46587a, #7f9cc1, #9a86b5, #aab0ba; never green, yellow or red), the three key figures with their mark as a dot before the name (an empty dot while no limit is confirmed), Zu prüfen and the coming dates. A tapped tile opens its summary in the panel with one button into its section. Every figure comes from an endpoint that exists (`/net-worth/overview`, `/health`, `/household/cashflow`, `/reminders`, the inbox count); no figure is computed in the browser. The parked row „overview by kind of investment“ is discharged as far as 15-B2 draws it (a tile per kind with its value); a return per investment stays parked. | Luis: „B but with the cashflow under the netto vermögen“, „You can go with the dots“, „Otherwise it's still confusing“. Principle 1: a figure on a tile is the server's. |
| D269 | **Konten › Umsätze is 16-C2,** backed by one new read, `GET /accounts/{id}/month?month=YYYY-MM` (`AccountMonth`): the closing balance of the month (or today's for the running month), Eingänge and Ausgänge without the transfers between own accounts, the transfers in and out as their own figures, and the count of lines not yet booked. A line counts as a transfer when its confirmed allocations are all transfer codes (`internal_transfer_in`/`_out`, the depot's purchase and sale, the Geldanlage codes); a line not yet confirmed counts by its sign in Eingänge or Ausgänge and the card says how many are open. The lines below come from `/accounts/{id}/transactions` grouped by booking day in the browser (grouping, not a sum); each shows „✓ Gebucht“, „Offen“ or „Vorschlag prüfen“ from the line's own status, never a dot. A tapped line opens in the panel with the actions Zu prüfen already offers for it. | Luis: „Mix of mobil zuerst for the bank lines and dashboard kompact for the account summary“, „Looks good“. The card's figures have no endpoint today; summing in the browser would break principle 1. |
| D270 | **Planung is five sections chosen by chips,** in this order: Budget, Reserve, Anlegen, Vermögen, Immobilie; the section is in the URL (`/planning?section=budget`), Budget the default. Reserve, Anlegen and Vermögen regroup today's Planung › Haushalt cards (the reserve and its paths; „Anlegbar pro Monat“, the savings plans and the explanation; the net-worth projection on typed rates); Immobilie is today's property Planung (projection to sale, the Pauschale card). The scenario is one row on top (saved, or „N Änderungen, nicht gespeichert“) that opens the scenario sheet. The old `?tab=haushalt` link maps to `?section=budget`. | Luis, 20 Nov 2026: „too many tiles on this section … split them up into their own sections“, „Yeah this looks good“. |
| D271 | **„Was eure Änderungen bewirken“ is fixed in the screen only.** The months before the first month in which a change is in force are one stretch in grey, „wie bisher“, with its rate and „Anlegbar“; the stretch in which a change starts is the bold one; a month already begun or over is named as such („läuft“, „vorbei“) and never shown as the plan's start. The server's stretches (`rules/plan/spending.py`) are unchanged. | The two defects noted on 18 Nov 2026. Changing `rules/plan/` would change `health.rule_version()` and make every month stored after it differ in version from March to May; the fix is presentation only. |
| D272 | **Every household spending code has a budget class, need or want, in the taxonomy.** `rules/household/taxonomy.py` gains `budget_class` (`need`, `want`) on every code whose relevance is spending or tax, set like `one_off` (D236); `rules/budget/` gives the two private codes outside the taxonomy theirs (`bank_fees` need, `uncategorized` in its own line, (4)); a completeness check at import refuses a spending or tax code without a class. The default classes are Luis's answer to Q-O176. The classes are not read by `rules/health/` or `rules/plan/`, so no key figure, plan figure or stored snapshot changes. (1) A household may move a code to the other class in its default scenario (D274); the class shown is the override, else the taxonomy's. (2) A one-off code (`hh_car_purchase`) has a class but is shown on its own line „Einmalige Ausgaben“ in a window that holds one, and gets no target. (3) `hh_refund` lowers the class it is set to (a negative amount). (4) `uncategorized` (lines a person booked without a category) is its own line „Ohne Einordnung“ with no class and no target, and a to-do „N Zahlungen einordnen“ links to them. | The partition must hold to the cent (D273), so every spending euro needs exactly one place. A taxonomy field keeps the sorting in one table with the codes' other facts; a separate file could drift from it. |
| D273 | **The Budget is a partition of phase 9's household month, never a new sum.** Over the window of closed months phase 9's key figures use (D231), with E the window's income (private income plus the flat's result, D232) and A its spending: Notwendiges + Wünsche + Einmalige Ausgaben + Ohne Einordnung = A, and Sparen = E − A, the savings rate's numerator, so Sparen ÷ E is the Sparquote to four places. Amounts on screen are the window's monthly means, apportioned by largest remainder so the shown lines add up to the shown income to the cent (`code_means`' method, D263). The flat's result is inside E: a shortfall lowers income, it is not spending; the screen names it under the income („inkl. Wohnung −120 €“) when it is not zero. Shares are of E, four decimals half-up, shown as whole percent; with E ≤ 0 there are no shares and the card says why. | Luis must never see two savings rates. Phase 9's month is closed and checked against its stored snapshots (D259); the Budget reads it and adds nothing to it. |
| D274 | **Targets are the household's, set in its default scenario, never proposed.** `HouseholdLevers` gains `budget_targets` (`needs_max`, `wants_max`, `saving_min`, `housing_max`, each a percent with one decimal, 0–100, default `None`) and `budget_class_overrides` (code → class, default empty); stored rows still validate. „Ziele speichern“ stores a new default-scenario row at once with the saved levers and the new targets, as „Übernehmen“ stores the marks' limits (D250 (3)); unsaved spending changes on screen stay unsaved. A target left empty gets no dot. The overrides live in the scenario too (no migration); a new scenario copies the default's. A line with a target gets a green dot when it meets it and an amber dot when it misses it, with a sentence that says by how many points and how many euros a month; no red, since a third colour would need a tolerance the app would have to choose. The marks are computed on read and never stored. | Luis's decision of 20 Nov 2026 („your own targets“), and D250's pattern: no colour from a value the household has not set. |
| D275 | **The published splits are reference points, and one tap only fills the form.** `rules/budget/references.yaml` (outside `rules/health/` and `rules/plan/`, so `rule_version()` stays) lists each reference with its source, the date it was read, what it measures and what it does not (§3). The goals sheet shows them under „Zum Vergleich“; the buttons 50/30/20, 60/20/20 and 70/20/10 write those numbers into the empty fields and store nothing; nothing is stored or coloured until „Ziele speichern“. No reference is a default, and none fills the housing target. | The app proposes no target (D249 as amended here); a one-tap fill is a typing aid the household confirms, the same as typing the numbers. Luis kept the buttons as drawn. |
| D276 | **The housing share** is the window's spending in the Wohnen group (`hh_housing`, `hh_rent`, `hh_energy`, `hh_broadcast_fee`, `hh_flat_search`, and any code moved there later) ÷ E. It is the household's own home only: the rental flat's costs are inside its result, and a code the household overrides to „want“ still counts in housing. The screen says that the IWU figure is a tenant's gross cold rent of the household's net income and Destatis's all housing costs of disposable income, so neither is the same figure as the app's. | Two published thresholds with two different denominators (§3); the screen must not imply either is the app's figure. |
| D277 | **Spending in play is reached from the Budget.** A tapped class opens its codes in the panel with their window means and the „geplant“ field of D263; the panel shows the class's amount and share with the plan, from `POST /household-plan/preview`. A code's class switch sits on its row there and becomes an override when saved. The explanation of the plan (D252, D253) gets no target, class or mark among its facts. | D263 is the one place amounts are typed; the explanation's validator was tested without budget facts and stays so. |
| D278 | **What undrawn screens get in this build:** the new shell, the panel where §5.2 names it, and nothing else; their content, tables and forms are unchanged. A screen gets its own mockup before its content changes. | Luis drew four screens; „one build“ needs an edge. **Amended 20 Nov 2026 (Luis: „let's do mock ups for the rest of the cards so it's all some [done] at once“):** every screen not yet drawn gets its own mockup before the build (`docs/mockups/` 18 onwards), and the build carries out each one Luis chooses; until a screen's mockup is chosen this decision stands for it. |
| D279 | **Konten shows one kind of account at a time.** Chips at the top, one per kind the household holds, each with its count: Bankkonten, Kreditkarten, Depots, Börsen, Wallets, Darlehen; Bankkonten first and the default; the kind is in the URL (`/accounts?art=…`); the page lists only that kind with its total. An account's panel is unchanged (mockup 21). | Luis, 20 Nov 2026: „each type of konten should have a tab on the top to keep the page leaner and scroll less“. |
| D280 | **Anlagen shows the newest value everywhere.** Übersicht, ETF and Krypto value every position by the same function as the net worth (`/assets/overview`'s valuation, D167): the newest stored price with its source and day (an issuer's daily NAV, Lang & Schwarz's close, a coin's daily close), refreshed by the daily price runs, so "up to date" means the last daily price, never a live quote. The ETF tab's rows show units and that value; the report's price and value move into the position's panel as a check, with whether the report agrees with the booked trades. Unchanged: a fund whose latest report disagrees with the booked trades is not valued and says „siehe Zu prüfen“ (D165 (3)), and a fund no daily source prices shows its report price marked „Kurs aus dem Bericht vom …“ (D174 (4)). No new sum: the ETF tab's figures equal Übersicht's and the net worth's for the same day. Reviewed (money figures). | Luis, 20 Nov 2026: „The anlagen should show the up to date values at all time“; the ETF tab showed the last quarterly report's value while Übersicht showed the daily one. |
| D281 | **Steuern has one tab for every input: „Eure Angaben“.** The form chips are „Eure Angaben“, Anlage V, Anlage N (and KAP, SO when phase 8 is built). „Eure Angaben“ holds every fact the household types or uploads that more than one form reads: per owner the pay (the expected gross pay, payslips, the Lohnsteuerbescheinigung, „Kein Arbeitslohn in <Jahr>“), church tax and Bundesland, the itemised Werbungskosten, the wage-replacement benefits; „Kinder und Haushalt“; and the Jahresfragen as a card into the one-question flow (`/tax/<year>/fragen` kept). Anlage N becomes the form only, like Anlage V: its lines per owner with Zeile and „So gerechnet“, each line's panel linking to its input under „Eure Angaben“. Pay is typed in one place; the two stores behind it stay (`income_input`, the estimate; `return_input`, the certificate and payslip figures, which replace the estimate once confirmed), so no migration. The property's own tax inputs (ortsübliche Miete, Nutzungsdauer, Einbauküche, §82b) stay on Anlage V, which alone reads them. Amends D266 and §5.1, where the tab was named „Einkommen & Haushalt“. | Luis, 20 Nov 2026: „there are two places where the income is asked in the anlage N and the Einkommen und Haushalt. We should combine and put them in one place … You can suggest another name“. |
| D282 | **Dokumente by section, with a search.** Every document gets one section, computed by code on read, never stored: Immobilie (the catalogue's property and purchase, WEG, loan, tenancy and running-cost groups, and every Nebenkosten bill and payment proof), Steuern (the income-tax group, the Einkommensteuerbescheid), Konten (bank and card statements), Anlagen (depot documents, exchange exports), Haushalt & Sonstiges (the rest). A document two sections could claim takes the first in that order, so a Grunderwerbsteuerbescheid is Immobilie; the search finds it from either. `GET /documents` gains `section` and `q` (case- and umlaut-insensitive over title, type label, file name, issuer, date and amount), filtered in the server, and `Document` gains `section`; the page shows the section chips with their counts and the search box (mockup 23). No migration. | Luis, 20 Nov 2026: „it would be nice if they could be categorized by to which section the document applies. And maybe a search bar“. |
| D283 | **Light or dark, chosen per device.** Einstellungen › „Darstellung & Sprache“ offers „Wie das Gerät“ (the default, today's behaviour), „Hell“ and „Dunkel“, stored on the device (`localStorage`), applied before the first paint by setting `data-theme` on the root element; `globals.css` keeps its dark palette under the media query guarded by `:root:not([data-theme="light"])` and repeats it under `[data-theme="dark"]`. Every colour phase 10 adds (the asset stripes, the panel, the dots, the Budget bar) is a token with a dark value. | Luis, 20 Nov 2026: „add to the plan and light mode“; today the app follows the device only. |
| D284 | **The app in English, chosen per device.** „Sprache“ beside „Darstellung“: Deutsch (the default) or English, stored on the device. From step 2 every label of the rebuilt screens goes through one message catalogue (`frontend/src/i18n/`), German its source, and each screen's English is written by the coder who builds it, beside the German, from one glossary written in step 2 (Luis, 20 Nov 2026: English in the same pass), so there is no second rewrite. Texts the server writes (to-do titles and sentences, card texts, error messages, the depot explanations of D177) get an English template keyed by the same code in step 4c, in parallel with the screens, chosen by the request's language; none of them is placed under `rules/health/` or `rules/plan/`, whose files feed `rule_version()` (a change there would make `health-check` differ on the closed months), so the key figures' and plan's sentences are translated from their codes outside those folders. **Stays German:** the names of official forms and lines (Anlage V, Werbungskosten, Zeile 33), shown with an English gloss, because they are what ELSTER shows; every paper for a third party (the letter to a tenant, the ELSTER field sheet, the yearly archive, the payment proofs); the reminder email (a member's language would need a migration); text read from a document; and the AI explanation of the plan, whose validator and evals are German: in English the screen shows the app's own template text in English, marked as such, until an English validator and its evals exist (Q-O178). Amends `CLAUDE.md`'s „labels stay German“ and the Decisions row „UI style“: German stays the source and the default. | Luis, 20 Nov 2026: „and the english Translation“. |

## 3. Sources (read 20 Nov 2026)

- **50/30/20.** Elizabeth Warren, Amelia Warren Tyagi, *All Your Worth* (2005): needs, wants and savings of net
  income. Sparkasse.de names it a „Faustregel“ and offers 60/20/20 and 70/20/10 where fixed costs are high. No
  German authority sets a household split.
- **Housing, tenants.** IWU study for the Deutscher Mieterbund (Stand 30 Mar 2026): a tenant household's rent
  burden (Bruttokaltmiete ÷ net household income) under 30 % „vertretbar“, 30–40 % „hoch“, over 40 %
  „Überlastung“.
- **Housing, all households.** Destatis, EU-SILC: housing costs net of allowances above 40 % of disposable
  household income count as „Wohnkostenüberbelastung“; housing costs include rent, owners' interest and
  property tax, energy and upkeep.
- **Rule of thumb.** Sparkasse: Kaltmiete at most a third of net income.

The wording on screen and in `references.yaml` keeps each source's own denominator (D276).

## 4. Budget rules (`rules/budget/budget.py`, `rules/budget/references.yaml`), fixtures `tests/fixtures/phase10/`

`budget(months, cashflows, overrides, targets) → BudgetView`, pure. `months`: phase 9's `HouseholdMonth` per
closed month of the window; `cashflows`: the same months' private lines by code (built from the lines, not from
`HouseholdCashflow.spending_by_category`, which drops a group whose month nets to 0; review of step 1, 20 Nov 2026). It checks
that each month's codes add up to that month's A (ValueError otherwise), sums per class and line, means them
(§D273), sets the shares, the housing share, the marks and the sentences.

Fixtures, computed by script for invented owners A and B, never from the mockups' numbers:

- `budget_partition.yaml`: one window holding every awkward case at once: a one-off purchase, a refund, a
  tax-relevant payment, a cash withdrawal, a card settlement, money to family, a bank fee, an uncategorised
  line, a depot fee, the flat's shortfall and its principal. Asserts the identity of D273 per month and over
  the window, and Sparen ÷ E = the Sparquote of `rules/health/figures`.
- `budget_overrides.yaml`: an override moves a code's mean between classes; housing unchanged.
- `budget_marks.yaml`: targets met, missed, empty; E ≤ 0; a window shorter than 3 months.
- `budget_references.yaml`: the one-tap fill writes the reference's numbers and leaves housing empty.

## 5. Screens (labels German, English from step 4c, D284)

### 5.1 Routes

| Today | After |
| --- | --- |
| menu entries Übersicht … Einstellungen (eleven) | bottom bar / tabs + „Mehr“ (D266) |
| `/owners`, `/tenants` in the menu | reached from Immobilien's card; routes kept |
| Eigentümer: income, church tax, pay documents, Kinder und Haushalt; Anlage N's inputs; Jahresfragen | Steuern › „Eure Angaben“ (D281; „Einkommen & Haushalt“ until then) |
| `/accounts` | `/accounts?art=…`, one kind at a time (D279) |
| `/documents` | `/documents?bereich=…&q=…` (D282) |
| `/planning`, `/planning?tab=haushalt` | `/planning?section=…` (D270); the old link maps to `budget` |
| Immobilien not in the menu | „Mehr“ › Immobilien, one card per property (14-C2) |

### 5.2 The panel in this build

Bank lines (Konten › Umsätze), Übersicht's tiles, the Budget's classes, a tenancy and an owner on
Immobilien's card; and, as mockups 18–25 draw them (chosen 20 Nov 2026), Zu prüfen cards, documents, positions, accounts,
settings, the tax lines and to-do lists, as each mockup draws them.

### 5.3 Undrawn screens

Zu prüfen (18), Haushalt (19), Anlagen (20), Konten and an account's settings (21), Steuern (22), Dokumente
(23), Einstellungen (24) and the property, unit, tenancy, tenant and loan pages with their forms (25) were drawn
on 20 Nov 2026 at Luis's word (D278 amended) and chosen the same day with D279–D282 (20–24 redrawn for them). Each re-arranges today's content: every card,
button and figure of the screen kept, on the page or in the panel; a wide table's row keeps its label and
amount on the phone, its other columns in the panel, never a sideways scroll.

## 6. API and schemas

- `GET /household-budget?scenario_id=` → `BudgetView`: the window's months; E with the flat's part; per line
  (need, want, one_off, uncategorized, saving) its monthly amount, share, codes with their means and class, the
  target, the mark and its sentence; the housing line; to-dos (`HealthTodo` shape).
- `GET /accounts/{id}/month?month=` → `AccountMonth` (D269).
- `HouseholdLevers.budget_targets`, `HouseholdLevers.budget_class_overrides` (D274), with empty defaults;
  `PATCH` nothing new: a scenario is saved through `/household-scenarios` as today.
- `HouseholdCode.budget_class` (D272).
- `GET /documents?section=&q=`, `Document.section` (D282).
- The ETF tab's values from the net worth's valuation (D280); the endpoint it reads is the coder's choice, its figures equal `/assets/overview`'s.
- In step 4c, the request's language (`Accept-Language`) chooses the server's English templates (D284).

## 7. Steps

0. Taxonomy field and completeness check, `references.yaml`, schemas, the four fixtures; no migration. Review:
   none (schemas and data; CI proves them), unless Q-O176's answer changes D272.
1. `rules/budget/`, `services/budget.py`, both endpoints. **Reviewed** (money figures, a new partition of the
   ledger's month): FOCUS the identity of D273 and the levers' validation of stored rows.
2. The shell, the panel, the route map, the message catalogue (every new label through it, one file per screen), the
   English glossary of the app's own terms, „Darstellung“ and „Sprache“ (D283, D284). Steps 3, 4 and 4b then run
   in parallel, one coder per screen group in its own worktree, each writing its screens' German and English.
3. Übersicht and Konten › Umsätze.
4. Planung's sections, the Budget, the goals sheet, the scenario sheet with D271's fix. Reviewed with step 1
   if built together, else on its own for D274's saving path.
4b. The remaining screens (18–25) with D279–D282; D280 reviewed (money figures), D281 with its links back from Anlage N.
4c. The server's English templates (D284), in parallel with 3–4b: to-dos, cards, errors, depot explanations,
   chosen by `Accept-Language`, none under `rules/health/` or `rules/plan/`.
5. Closing.

### 7.1 Done when

Luis uses the new menu and every screen on his phone and computer, in German and in English, light and dark, and finds no page that starts with a
scroll past the menu; on the live data the Budget's Sparen ÷ E equals the Sparquote `hausbuch health-check`
recomputes for the same window, to four places; Luis finds the split into needs and wants plausible; and
nothing is coloured before he saves a target.

## 8. Edits to other docs (made in the spec PR)

- `docs/ARCHITECTURE.md`: a delivery row for phase 10 and „Ten phases“; a Decisions row „Phase 10 spec“;
  the row „Advice and budgets“ and the Parked rows (layout, needs and wants, overview by kind) point here;
  the Domain model's HouseholdScenario levers.
- `docs/phases/phase-9.md` D249: amended by D274 and D275.
- `CLAUDE.md`: the phase specs list.
- `docs/mockups/README.md`: the chosen screens point to this spec.

## 9. Question for Luis

**Q-O176 (answered 20 Nov 2026): the default sorting.** As proposed below, except Friseur & Körperpflege
(`hh_personal_care`), which Luis put under Notwendiges („I think Friseur is a need. Other than that it looks good.“).
The proposal, each switchable per category by the household (D272 (1));
a group's „Sonstige …“ code follows its group:

- **Notwendiges:** Wohnen (Miete, Energie, Rundfunkbeitrag, Wohnungssuche), Lebensmittel (Supermarkt,
  Drogerie, Bäckerei), Versicherungen, Gesundheit, Mobilität (Tanken, ÖPNV, Parken, Werkstatt, Kfz-Steuer),
  Telefon und Internet, Familie und Kind (Kinderausstattung, Schwangerschaft und Geburt), Kinderbetreuung,
  haushaltsnahe Dienste, Steuerzahlungen, Behörden und Gebühren, Bankgebühren, Kartenzinsen, Arbeit und Bildung, Porto, Körperpflege und Friseur (Luis), privat bezahlte Kosten der
  Wohnung, Depotgebühr, Kapitalertragsteuer.
- **Wünsche:** Essen gehen und bestellen, Freizeit, Reisen, Sport, Kleidung,
  Elektronik, Online-Shopping, Haushalt und Einrichtung, Abos und Streaming, Geschenke, Spenden, Spielzeug, Taxi, Sonstiges.
- **Unsure, accepted as proposed:** Geld an Familie under Notwendiges; Bargeld and Kreditkartenabrechnung,
  whose contents the app cannot see, under Wünsche; Erstattungen lower Wünsche.

## 10. Open for the orchestrator

- **Q-O178 (open, not blocking): the plan's AI explanation in English.** Until an English validator, lexicon and
  evals exist (D255, D258), English shows the app's template text in English (D284). Building them is a later step
  once Luis uses the app in English.
- **Same gap in phase 9, open:** `rules/plan/spending.month_spending` also reads `spending_by_category`, so a group netting to exactly 0 in a month drops its codes from the per-code means of „Ausgaben durchspielen“ (the totals stay right). Not fixed in phase 10, since `rules/plan/` feeds `rule_version()`; to be decided with a phase 9 rule-version change.
- **Rollback limit (from step 1's review):** once a household scenario is saved by the step 1 image, its row carries
  `budget_targets` and `budget_class_overrides`, which an older image refuses (`extra="forbid"`), so that image
  cannot be rolled back after the first save. The frontend's `fillLevers()` must carry both fields from step 4 on,
  or a save from Planung drops the household's targets.

- Q-O177 (settled 20 Nov 2026): `/household-budget` serves the window only. The Haushalt mockup (19) draws
  no Budget, so no screen asks for one month; a month view can be added later without a schema change.
