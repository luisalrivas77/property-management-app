# Phase 10 · Layout und Budget

Spec drafted 20 Nov 2026 from the four mockups Luis chose that day (`docs/mockups/` 14-C2, 15-B2, 16-C2,
17-C2) and his decision on budgets („the household's own targets“, 20 Nov 2026). Steps 0 to 4c built and deployed by 21 Nov 2026, and the property tile's money back (D286, PR #513) and faster reads for Übersicht (PRs #514, #516, #517, §7.2) the same day; step 5, the closing with Luis on phone and computer, in German and English, light and dark, not started (this said „Being built“ until then, and „Not built“ until steps 0 to 3 had deployed). Q-O176 (the default sorting
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
| 3 | Übersicht (15-B2) and Konten › Umsätze (16-C2) | merged and deployed 21 Nov 2026 (PR #497, images 443b68ae59a7, no migration; no review: no figure computed in the browser, CI's e2e green): Übersicht as tiles with the key figures' marks as dots and each tile's summary in the panel (D268), Konten one kind at a time (D279), Konten › Umsätze with the month card from `GET /accounts/{id}/month` and the lines by day (D269); the cards embedded from earlier phases (`netWorth.tsx`, `overviewTodos.tsx`) still German in English (this said „not started“ until then, while it was being built) |
| 4 | Planung in five sections with the Budget (17-C2) | merged and deployed 21 Nov 2026 (PR #496, images 10280795bafc, no migration). Review of the saving path: ship after fixes (no save drops a lever), all applied: the need/want switch shows an override set back to its default, an invalid Vermögen rate stops blocking saves once its card is gone, `fillLevers()` checked by the compiler, e2e for the overrides and the preview's `scenario_id`. CI found one more bug the rework had brought in: after „Szenario speichern“ a field kept the typed value instead of the stored one, because the five-section page no longer remounted the plan after a save (phase 9's page did); a save now remounts it. Q-O179 open. (This said „in review“ until then, and „not started“ before that.) |
| 4b | The remaining screens as drawn in `docs/mockups/` 18–25 (D278 amended) with D279–D282 | chosen 20 Nov 2026 with Luis's changes (20–24 redrawn the same day). Built in two parts, both merged and deployed 21 Nov 2026, no migration. Zu prüfen, Haushalt and Anlagen with the ETF tab at the Übersicht's newest price (D280): PR #501 (images a5026d7bc769; it replaced PR #495 after a rebase). Its review checked the values equal the Übersicht's and found one false „Noch kein Kurs“ note, fixed: a fund the latest report lists with nothing booked now says „siehe Zu prüfen“ and a sold fund shows „–“; `?on=` was dropped. Steuern › Eure Angaben (D281), Dokumente by section with a search (D282), Einstellungen and Immobilien: PR #498 (images 47bab0bbd991). Its review found no isolation or tax-input change and made three fixes: the year chips offer two years back to next year (only the rule-set years had been offered, so 2027's pay and a 2025 Bescheid were reachable only by URL), every pay link opens Eure Angaben for its year and owner instead of the Eigentümer page, and a depot statement is filed under Anlagen (D281 and D282 amended to the build). The property, loan, unit, tenancy and tenant pages of mockup 25: PR #503 (images 75fa003e0dc1), merged and deployed 21 Nov 2026. A wide table's row keeps its label and amount on the phone and opens its other columns in the panel (the loan schedule, units, Nebenkosten, price splits, a tenant's tenancies, the Mietstaffel); edit forms slide in from the right with unchanged request bodies. CI found a real bug on the loan page: the panel's empty column was reserved even with no row open, so at 390 px the page had no width and „Alle N Raten“ could not be opened; the panel now opens only for a row the URL names. Not added at first, since no endpoint served them: the Nebenkosten total, Restschuld heute, Anfängliche Tilgung, the AfA basis amounts; added by D285 the same day (PR #509, images 8ae706e747e6, no migration, merged and deployed 21 Nov 2026; this said „Not added“ until then, though the AfA basis had been served by the Anlage V draft all along). Its ten tabs became mockup 25's six sections at Luis's word („Yes please regroup“), each former tab a card `card-<old key>`, an old `?tab=` link opening its card's section and scrolling to it, merged and deployed 21 Nov 2026 (PR #511, images 7349aa155c8a, no migration; this said „The property keeps its ten tabs“ until then) (this said „Not built yet“ until then). (This said „being built“ and „in review“ until then.) |
| 4c | English (D284) | merged and deployed 21 Nov 2026 in three parts (PRs #500, #506, #507), with each screen's own catalogue shipped with its screen; what stays German on purpose is listed below (this said „in part“ until then): each screen's English catalogue shipped with its screen (PRs #497, #496, #501, #498); the server's own texts in English by `Accept-Language` (cards, to-dos, errors, the plan's template; nothing English stored, jobs and papers German) merged and deployed 21 Nov 2026 (PR #500, images b111113a0fe9, no migration; review: ship, nothing English persisted, the German output unchanged over about 12,700 compared cases). Still German in English: the forms and cards shared with earlier phases inside the screens, category labels inside the server's to-do sentences, the key figures' mark sentences and the Budget's reference texts, the wallet group cards, and Zu prüfen's bulk list showing the server's English fallback question as if it were the model's (review of #500, finding 1). (This said „being built“ until then.) **Second part, merged and deployed 21 Nov 2026 (PR #506, images 78bb2e32bd3c, no migration; it replaced PR #505 after a rebase; CI caught two tests left behind by the change, the category-label check reading the German half of the labels and the English tenant step clicking „Add tenant“; this said „built … not merged“ until then):** the review's finding 1 fixed (the bulk list hides the English fallback question too); the key figures' marks, the debt gaps, the plan's comparison words, the net worth's notes, the Budget's marks and reference points and a category named in the plan's scenario to-do in English on read (`services/read_texts.py`, `services/category_texts.py`, nothing under `rules/health/` or `rules/plan/`); the wallet group cards in English on read; every enum label of `components/labels.ts` (German and English records behind `localized()`, the category names equal to the server's); every error kind, rule code and reading error (`i18n/errors.ts`) and the input checks; the shared pieces (`ui.tsx`, `form.tsx`, `history.tsx`, `entities.ts`, `i18n/shared.ts`); Übersicht's to-dos and „Kein Arbeitslohn“; every to-do guide, checklist row and reminder text with their link labels (`components/todosEn.ts`, the German guides in `todos.tsx` unchanged) and the property's Unterlagen tab; a share's percent and fraction. Backend tests compare the German and English code lists (rule codes, reading errors, to-do guides and their link labels). Nothing English is stored: the server translates on read only, the frontend sends enum keys only. It left German the forms and cards that `flatRate.tsx`, `documents.tsx`, the rest of `taxInputs.tsx`, the action dialogs of `todos.tsx`, Zu prüfen's bulk confirm, transfer and exchange cards, `exchange.tsx`, the Einstellungen components, `returnInputs.tsx`, `householdReturn.tsx`, `income.tsx`, `netWorth.tsx`, `healthFigures.tsx`, `allocations.tsx` and `proposals.tsx` draw. **Third part, merged and deployed 21 Nov 2026 (PR #507, images 8d75ebf10802, no migration; this said „built … not merged“ until then):** all of those in English, and with them the Pauschale card and its letter form (not the letter), DocumentPicker and LinkedDocuments, the Nebenkosten terms, Wohnfläche and Erweiterung fields, the to-dos' action dialogs, the Einstellungen components (Zugangsschlüssel, AI switches, passkeys, Hinweis-Modus), the Anlage N tab, Eure Angaben's input blocks, the Kinder and Haushalt blocks, the whole-return estimate, the income inputs, the net worth, Cashflow and key-figure cards, a bank line's categorisation panel, the AI suggestion review, the wallet's addresses and cards, a document's page and the photo capture, the owners' page, sign-in and enrolment, the reminders, Jahresfragen, the plan's explanation, the export buttons, „Ausgaben durchspielen“ and the route pages' own words and loading line (`i18n/pages.ts`, `LoadingLine` in `ui.tsx`); amounts and dates shown on those screens follow the language. German stays byte-identical (each part checked by script against the file before it); the e2e specs keep every German expectation and gain English steps (the „Als Miete buchen“ dialog, the Anlage N tab, Eure Angaben's commute form); a backend test checks that the German and English record pairs keep the same keys. **Still German on purpose:** the papers (tenant letters, the ELSTER sheets, the archive, `zahlungsnachweise.pdf`), jobs and reminder emails, the stored plan explanation (D284), a stored question with no English entry; every value that is sent as typed or as a name (the default passkey name „<Browser> auf <OS>“, a photo's file name „Beleg …pdf“, the scenario name „Standard“, an expense's default label „Kontoführung“, a wallet's shared-ownership reason, the letter's sender line); inputs that parse German numbers (amounts, shares, the key figures' limits, an exchange's units, which `formatUnits` pre-fills); the German side of the to-do guides in `todos.tsx` (their English in `todosEn.ts`); the page titles in the browser tab, the app's description and the home-screen shortcut's name (`metadata` of the route layouts, `app/layout.tsx`, `app/manifest.ts`, made on the server before the device's choice is known); the form names (Anlage V, Anlage N) and German tax terms, which keep their German word with an English gloss. One German change: Zu prüfen's bulk confirm no longer prints the word „null“ for a rule code it does not know. |
| 4d | Übersicht's speed (§7.2), no new behaviour | merged and deployed 21 Nov 2026 (PRs #514, #516, #517, #518, #519, #520, #522; images de96c9e2356e, 0523409427b7, 397837875ca6, 2ae563e4e0ba, 80656fa4c054; no migration) |
| 4e | Fewer repeated reads (§7.3) and reusing answers while nothing changed (§7.4), no new behaviour | merged and deployed 21 Nov 2026: §7.3 PR #528 (images 5f9aa0594917, no migration), §7.4 PR #529 (images 902f60277dc8, migration 0050 applied); review round pending, Mon 23 Nov 2026, on the deployed code (Luis: „Ship both, review Monday“) |
| 4f | Further speed steps (§7.5): every section kept, the inbox kept until its next time window, answers computed after a change instead of at the next load | scoped 21 Nov 2026, not built; waits for Luis's go after the §7.4 review |
| 4g | Less on each screen (§7.6): Übersicht trimmed first (Luis, 22 Nov 2026), then the other sections one at a time | Übersicht, the „Was noch fehlt“ lists, Zu prüfen (with „Foto“ opening the camera at once), Konten's tabs and total, the „Steuer-Checkliste“ name and Haushalt (the categories as rows, what differs this month, the Budget line, recurring payments) and Anlagen (four tabs with Planung's property section moved in as „Immobilie“, ETF cards like Krypto's, one card per portfolio with its gain since the start and its yearly return, the funds and coins in the panel, the tax-free coins as a list) scoped 22 Nov 2026, not built; the other sections not yet scoped; waits for Luis's go |
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
| D270 | **Planung is five sections chosen by chips,** in this order: Budget, Reserve, Anlegen, Vermögen, Immobilie; the section is in the URL (`/planning?section=budget`), Budget the default. Reserve, Anlegen and Vermögen regroup today's Planung › Haushalt cards (the reserve and its paths; „Anlegbar pro Monat“, the savings plans and the explanation; the net-worth projection on typed rates); Immobilie is today's property Planung (projection to sale, the Pauschale card). The scenario is one row on top (saved, or „N Änderungen, nicht gespeichert“) that opens the scenario sheet. The old `?tab=haushalt` link maps to `?section=budget`. **Amended 22 Nov 2026 (§7.6, scoped, not built):** Immobilie moves to Anlagen as its fourth tab and the Pauschale card to the property's page, so Planung keeps four sections (Budget, Reserve, Anlegen, Vermögen); `?section=immobilie` opens Anlagen › Immobilie. | Luis, 20 Nov 2026: „too many tiles on this section … split them up into their own sections“, „Yeah this looks good“. |
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
| D281 | **Steuern has one tab for every input: „Eure Angaben“.** The form chips are „Eure Angaben“, Anlage V, Anlage N (and KAP, SO when phase 8 is built). „Eure Angaben“ holds every fact the household types or uploads that more than one form reads: per owner the pay (the expected gross pay, payslips, the Lohnsteuerbescheinigung, „Kein Arbeitslohn in <Jahr>“), church tax and Bundesland (amended 21 Nov 2026 as built: shown there, edited with „Bearbeiten“ on the owner's page, where its form stays), the itemised Werbungskosten, the wage-replacement benefits; „Kinder und Haushalt“; and the Jahresfragen as a card into the one-question flow (`/tax/<year>/fragen` kept). Anlage N becomes the form only, like Anlage V: its lines per owner with Zeile and „So gerechnet“, each line's panel linking to its input under „Eure Angaben“. Pay is typed in one place; the two stores behind it stay (`income_input`, the estimate; `return_input`, the certificate and payslip figures, which replace the estimate once confirmed), so no migration. The property's own tax inputs (ortsübliche Miete, Nutzungsdauer, Einbauküche, §82b) stay on Anlage V, which alone reads them. Amends D266 and §5.1, where the tab was named „Einkommen & Haushalt“. | Luis, 20 Nov 2026: „there are two places where the income is asked in the anlage N and the Einkommen und Haushalt. We should combine and put them in one place … You can suggest another name“. |
| D282 | **Dokumente by section, with a search.** Every document gets one section, computed by code on read, never stored: Immobilie (the catalogue's property and purchase, WEG, loan, tenancy and running-cost groups, and every Nebenkosten bill and payment proof), Steuern (the income-tax group, the Einkommensteuerbescheid), Konten (bank and card statements), Anlagen (depot documents, exchange exports), Haushalt & Sonstiges (the rest). A document two sections could claim takes the first in that order, so a Grunderwerbsteuerbescheid is Immobilie; the search finds it from either. `GET /documents` gains `section` and `q` (case- and umlaut-insensitive over title, type label, file name, issuer, date and amount), filtered in the server, and each row of the list gains `section` (amended 21 Nov 2026 as built: on `DocumentListItem`, the list's own row, not on `Document`, since a link added later can change a document's section and the list computes it on read; a bare statement a depot or exchange record relies on and no bank import does, or one uploaded with a depot heading, is Anlagen, not Konten, the review of PR #498 having found a depot's cash statement filed under Konten by the tie-break; a stored reading that no longer validates leaves that document findable by its title, type, file name and dates instead of failing every search); the page shows the section chips with their counts and the search box (mockup 23). No migration. | Luis, 20 Nov 2026: „it would be nice if they could be categorized by to which section the document applies. And maybe a search bar“. |
| D283 | **Light or dark, chosen per device.** Einstellungen › „Darstellung & Sprache“ offers „Wie das Gerät“ (the default, today's behaviour), „Hell“ and „Dunkel“, stored on the device (`localStorage`), applied before the first paint by setting `data-theme` on the root element; `globals.css` keeps its dark palette under the media query guarded by `:root:not([data-theme="light"])` and repeats it under `[data-theme="dark"]`. Every colour phase 10 adds (the asset stripes, the panel, the dots, the Budget bar) is a token with a dark value. | Luis, 20 Nov 2026: „add to the plan and light mode“; today the app follows the device only. |
| D284 | **The app in English, chosen per device.** „Sprache“ beside „Darstellung“: Deutsch (the default) or English, stored on the device. From step 2 every label of the rebuilt screens goes through one message catalogue (`frontend/src/i18n/`), German its source, and each screen's English is written by the coder who builds it, beside the German, from one glossary written in step 2 (Luis, 20 Nov 2026: English in the same pass), so there is no second rewrite. Texts the server writes (to-do titles and sentences, card texts, error messages, the depot explanations of D177) get an English template keyed by the same code in step 4c, in parallel with the screens, chosen by the request's language; none of them is placed under `rules/health/` or `rules/plan/`, whose files feed `rule_version()` (a change there would make `health-check` differ on the closed months), so the key figures' and plan's sentences are translated from their codes outside those folders. **Stays German:** the names of official forms and lines (Anlage V, Werbungskosten, Zeile 33), shown with an English gloss, because they are what ELSTER shows; every paper for a third party (the letter to a tenant, the ELSTER field sheet, the yearly archive, the payment proofs); the reminder email (a member's language would need a migration); text read from a document; and the AI explanation of the plan, whose validator and evals are German: in English the screen shows the app's own template text in English, marked as such, until an English validator and its evals exist (Q-O178). Amends `CLAUDE.md`'s „labels stay German“ and the Decisions row „UI style“: German stays the source and the default. | Luis, 20 Nov 2026: „and the english Translation“. |
| D285 | **The property pages' four figures, served by the server** (Luis, 21 Nov 2026: „Add the property page missing Features“). Two new reads, no existing shape changed, no migration: `GET /properties/{id}/purchase-summary` gives the Nebenkosten „Summe“ (the sum of cost rows the AfA rule itself reads, `rules/tax/afa.py` `cost_rows_total`, so the total and the basis cannot drift), their count, and the Anlage V draft's own AfA line for the last owned year with a rule set (basis, rate or a Gutachten's useful life, the kitchen's deduction); `GET /loans/{id}/summary` gives „Restschuld heute“ (the net worth's `loan_balance` over the loan's stored schedule on the Berlin day, null before the payout) and the „Anfängliche Tilgung“ (payment × 12 ÷ principal − rate, display only, never a form field). No split selected shows a to-do, never 0 €. The loan page's balance follows the contract: before the handover it can differ from the net worth, which counts only the part paid out (phase 6 D265), and after the fixed rate from a scenario's own follow-up rate. Merged and deployed 21 Nov 2026 (PR #509, images 8ae706e747e6). The property page's ten tabs became mockup 25's six sections the same day (PR #511, see step 4b; this said they stay until then) |
| D286 | **The property tile: the money back and a sale today** (Luis, 21 Nov 2026: „I want to see how far I am from recovering the investment“, then „Both“; before that „Both figures“ for the equity). Two new reads, no migration, no existing shape changed: `GET /properties/equity` gives the own money put in per property (the price plus the Nebenkosten rows plus the privately paid loan costs, minus the loan principal; a financing bill the rental account paid is left out, since it is in the cash already), and `GET /properties/payback` gives „Zurück bisher“, the sum of phase 4's actual net cash since the handover year (Tilgung included, the tax effect subtracted), with its percent of the money put in, the year the money is back from the actual years followed by Planung's default projection, and „Gewinn bei Verkauf heute (Schätzung)“, phase 4's `sale()` with this year as the sale year and the net worth's value and loan balance (`project()` refactored through `_sale_facts`, its output identical on the 28 runs of the phase 4 fixture). The tile shows „Zurück bisher“ with its percent, the year and the sale today; the panel lists the actual years behind it and links every gap to its fix (a loan without a value, a stale sale year in the plan, missing income); with no income entered the sale's tax reads „nicht berechnet“, never 0,00 €; a failed projection costs only the year. Known limits, not changed: the §23 test and the AfA added back are decided for 31 Dec of the current year (the note says so); the current year counts a full year's AfA against the rent so far, so „Zurück bisher“ moves at each New Year (phase 4 Q-O5); a rental cost paid privately other than a loan cost is in neither figure (none in the household's data). Review: ship after fixes, all applied. Merged and deployed 21 Nov 2026 (PR #513, images 94606b58c648, no migration). The tile's headline departs from mockup 15-B2 at Luis's word |

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
- `GET /documents?section=&q=`, `DocumentListItem.section` (D282; `Document.section` until 21 Nov 2026, built on the list's row instead).
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

### 7.2 Übersicht's speed (21 Nov 2026)

Luis: „The Übersicht Page loads really slow. Same as the zu prüfen.“ Measured with `hausbuch perf-overview` (PR #519), a
read-only operator command that makes every request Übersicht makes, in order, against the live workspace and counts
time, SQL statements and transactions per request, never printing data: one load sent about 1,700 statements in 30
requests, most of the time network round trips between the API container and the database, not computation; the key
figures (`/api/health`) alone sent 254 and were asked twice. Three changes, none changing an answer:

- PR #514 (images de96c9e2356e): the browser's API client shares one in-flight GET when two parts of a page ask the
  same URL at once (Übersicht had asked for the key figures and for Zu prüfen twice each).
- PR #516 (images 0523409427b7): the transfer reads (`services/transfers.py`) read the links' own legs, the own
  accounts' IBANs and the held answers once per call instead of once per line. Three of its reads are copies of rules
  the write path still uses; PR #518 adds a test that pins each copy to its original.
- PR #517 (images 397837875ca6): the month-end figures read for several month-ends in one call
  (`net_worth.month_end_figures`), the price cards' events and facts read once, and a depot's movements read once per
  call.

Result on the live data, measured after both deployed: about 1,130 statements per load; `/api/health` 254 to 100,
`/api/health/net-worth-series` 204 to 62, `/api/inbox` 169 to 131. The gate for each change: `perf-overview --digest`
(a hash of every response body) run from the old and the new code back to back on the live data, every body equal
except the two Anlage N error bodies, which carry a request id; then `hausbuch health-check --from 2026-03` found every
stored key figure of March to September equal („alles gleich“); one review each, no finding. One side effect, fixed: the first e2e run over both changes (on PR #519) found that the „Mietänderung“ a Pauschale
to-do opens pre-filled could open before the rent schedule had loaded, leaving the Kaltmiete empty so „Speichern“ was
refused (nothing wrong was saved); faster reads had made that order common. The dialog now opens once both have loaded
(PR #520, images 2ae563e4e0ba).

Correction, the same evening (Luis: „Still pretty slow“): the statement counts above were measured from the dev box,
where every round trip to the database costs about 33 ms over the internet; the live API sits on the database's private
network. The live API's own request log (`duration_ms` per route, read from Cockpit) showed one Übersicht load taking
about 16 s with about 27 requests at once on a container of 0.25 vCPU, even a tiny request waiting 1–3 s in the burst,
`/api/inbox` alone about 1.2 s: in production the cost is CPU, not round trips. Luis chose code changes over a bigger
container („No cost, code only“; 1 vCPU would cost roughly 22–27 € a month more). PR #522 (images 80656fa4c054), no
answer changed:

- the Zu prüfen tile reads the menu's `GET /inbox` (the API's most expensive read) instead of computing it again: it
  shares the menu's running request, else takes its answer while it is at most 5 s old; the kept list is forgotten at
  every INBOX_CHANGED, a sign-in, a sign-out, a reminder's „Erledigt“ and an answer on Zu prüfen
  (`frontend/src/lib/inboxCount.ts`);
- the tiles' six reads go first; „Was noch fehlt“, Nächste Termine, the phase 9 block and the property cards start once
  they have settled (at the latest after 20 s); `/health` and `/properties` are each read once per load;
- the §11 tax year (`rules/tax/tax_year.py`) is memoised on the plain values it reads (booking and value date,
  recurring, service period, due date, the rule set's short-period days), so the projection behind
  `/api/properties/payback` runs about 45 % less CPU; about 550,000 cheap lookups per projection remain (a tax year
  computed once per draft would remove them; not done).

Gate as before: `perf-overview --digest` old against new on the live data, every body equal except the two Anlage N
error bodies; `health-check --from 2026-03` „alles gleich“; 163 fixture drafts and projections byte-identical. One
review: the memo clean; the tile's kept list could have outlived a sign-out and shown a count from before an
unannounced change for up to 30 s, fixed in the same PR (the 5 s limit and the resets above). Measured live after
PR #522 (request log, 21 Nov 2026 17:47 UTC, Luis: „It is faster. But still more than 10secs“): Übersicht's tiles
answered between 0.9 and 6.9 s and the rest by about 10 s, against about 16 s before (this said „Still open: the
live load time“ until then). Next step in §7.3.

### 7.3 Next speed step (scoped 21 Nov 2026, merged and deployed 21 Nov 2026)

Luis asked for diagnostics and a scope only, no build yet; he chose code over a bigger container („No cost, code
only“). Measured on the live data, read-only, CPU time without database waits (`time.process_time`, dev box, no
profiler): one Übersicht load's reads cost **about 7.7 s of CPU**, and on 0.25 vCPU the page takes about their sum
(about 10 s live). The largest: `/properties/payback` about 1.0 s, `/health` 0.9 s, `/inbox` 0.8–1.0 s,
`/tax/return-estimate` 0.6 s, `/health/net-worth-series` 0.56 s, `/health/months` 0.53 s, `/net-worth/overview`
0.54 s. A cProfile run (`cProfile.Profile(time.process_time)`) found where the work repeats:
`services.health.read_months` (every ended month's household month) in three routes, `services.transfers.
open_transfers` in four, `services.tax.draft_facts` twice inside payback. Its milliseconds are not savings: the
profiler about doubles a route (payback 2.7 s profiled, 1.0 s plain) and inflates work made of many tiny calls far
more (this section first sized the savings from the profiled figures, so they were too large, corrected the same
day).

Zu prüfen (about 4–5 s live): `loadInbox` (`frontend/src/components/inbox.tsx`) fetches the imports and every line of
all nine accounts that hold lines, 18 requests, 1,219 lines, about 1.2 MB and 1.6 s of CPU, to look up the few lines
the cards name; on 21 Nov 2026 the three open cards named none.

The scope, each change proven to leave every answer unchanged:

1. **Vermögen im Verlauf from `/health`** (frontend only, about −0.56 s). `/health`'s `series` is computed exactly as
   `/health/net-worth-series` (`series(_totals(_newest_run_figures(conn)), closed households of ended_months)`), and
   the two were equal on the live data; `healthFigures.tsx` takes the page's `/health.series` on Übersicht and stops
   asking the series route (the Planung and Haushalt pages unchanged).
2. **The Cashflow tile's month from `/health`** (about −0.53 s). `/health` already reads every ended month; it gains a
   field with the last ended month's household month (`last_ended`), which the tile reads instead of
   `/health/months?from=<month>&to=<month>`. A month read inside the whole range equals the same month read alone
   (checked for September on the live data: `/health.newest` equal to `/health/months`' household); the gate
   compares the two for the last ended month.
3. **Payback once per request** (about −0.3 to −0.4 s; `draft_facts` 173 ms and `checklist_facts` 83 ms plain). `draft_facts` and `checklist_facts` read once per request and
   passed down; the §11 tax year computed once per allocation per draft (or per `project()` run) instead of per
   `of_year` lookup, which removes the remaining 193,000 per-allocation lookups (under 0.2 s plain). Rules stay pure,
   no fixture changes, the 163 fixture drafts and projections byte-identical.
4. **`open_transfers` once per call** (about −0.1 s; the whole call is 174 ms plain). `_line_legs`, `_current_links` and `_linked_ids` run
   two or three times inside one call (in `/health`, `/health/months`, `/inbox`); computed once and passed down.
5. **Zu prüfen reads only the lines its cards name** (about −1.6 s CPU and 1.2 MB on the phone). A new read,
   `GET /inbox/lines`, returns the lines and imports the cards can look up, computed by the server from the same
   inbox items: each item's `transaction_id` and `import_id`, both lines of every possible duplicate and their
   imports; each object serialized exactly as `/accounts/{id}/transactions` and `/imports` serialize it, under the
   workspace's row-level security. `loadInbox` uses it instead of the per-account loop. The readers audited on
   21 Nov 2026: `inbox.tsx` (the search „Empfänger suchen“, the line cards, „Alle N bestätigen“, the import and
   e-mail cards, the duplicate card) and `accounts.tsx`' `DuplicateCard` (the two lines' imports). A card whose
   line is not in the answer fetches that account's lines rather than showing an empty payee or amount (the blank
   card of PR #252).

Not proposed: keeping a figure between requests on a guessed lifetime (a stale money figure after a change would be
silent; §7.4 keeps whole answers only under a version every write moves, at Luis's word), and taking a
closed month's household figures from its stored `health_snapshot` instead of recomputing ten months on every read
(it would change D242's compute-on-read). Luis proposed it the same day (compare once, then serve the stored month
until its old data changes); measured plain, `read_months` costs 384 ms over the ten ended months and 242 ms for the
newest alone, since most of its cost is the open-transfer check over the whole history and the month still open, so
it would save about 0.14 s per `/health` (at most the whole 0.38 s), about 0.2 s of a 10 s load once items 1 and 2
stop the other two routes recomputing the months; against a change detector that fails silently when it misses an
input, so not recommended.

Expected (plain CPU): Übersicht about 7.7 → 6.2 s, so **about 8 s live instead of 10** (this said 6–7 s, from the
profiled figures); Zu prüfen 3.4 → 1.8 s, so about 2–2.5 s instead of 4–5 (measured plain, unchanged). Beyond this,
Übersicht's remaining cost is a dozen separate calculations of 0.4–1 s each with no single large repeat left, so a
further large step needs more CPU (Luis declined a bigger container on 21 Nov 2026), showing less at load, or §7.4.

Gate: `perf-overview --digest` old against new (every body equal; `/health` compared without its new
field), a read-only check on the live data that every id a card can look up is in `GET /inbox/lines` and each object
equals the per-account route's, `health-check --from 2026-03` „alles gleich“, and one review (a new read path under
row-level security, and the blank-card risk).

**As built (PR #528, merged and deployed 21 Nov 2026, images 5f9aa0594917, no migration; this section said „being built“ until then).** All five items as scoped: `/health` gains `last_ended` and its `series` feeds Vermögen im Verlauf; payback reads its draft facts once; a tax-year index computes each allocation's §11 year once per draft; `open_transfers` reads its links once; `GET /inbox/lines` returns `{transactions, imports}` for the lines and imports Zu prüfen's cards name, and `loadInbox()` falls back once to the per-account reads when a card's line is missing. Gate on the live data, read-only: `perf-overview --digest` base against branch, every body equal except `/health`'s new `last_ended` and the two Anlage N 422 bodies (they carry a request id); 21 wide routes digested equal; every id a card can look up found in `/inbox/lines` and equal to the per-account route's object; `health-check --from 2026-03` „alles gleich“. On the dev box payback fell from about 4.1 to 2.7 s and the page's statements from 1,131 to 1,066; Zu prüfen now loads the lines its cards name (0 of 1,219 on 21 Nov 2026) instead of 18 per-account reads. Live load time not yet measured. Review: pending, Mon 23 Nov 2026, on the deployed code (FOCUS: `/inbox/lines` under row-level security and its fallback).

### 7.4 Reuse the answers while nothing changed (scoped 21 Nov 2026, merged and deployed 21 Nov 2026)

Luis chose §7.3 and §7.4 over a full processor the same evening (about 2.5–3 s for every load, at about €22–27 more a
month): „No it's not worth the money. Your proposed fix would reduce it the same amount. And you can calculate the
data after a prefetch. I can live with the 8 second penalty after making a change.“ Both are being built in
parallel (this said „not built“ until then).

Luis, 21 Nov 2026 („Yes please add it“), after the assistant answered that §7.3 is not the farthest code can go.
Most loads of Übersicht and Zu prüfen come when nothing in the household's data has changed since the last one, yet
every read recomputes. A read on the list below keeps its finished answer and serves it again while the household's
**data version** is unchanged; any write moves the version, and the next read recomputes. The version knows nothing
about which figure depends on which row (the weakness of the stored-months idea in §7.3), so it cannot miss an input.

1. **The data version, kept by the database** (one migration). A table `workspace_data_version (workspace_id,
   version bigint)`. On every table that holds a `workspace_id` (all of them except `workspace` itself, the
   Procrastinate tables and `alembic_version`; listed on the live database 21 Nov 2026, `price` included) a
   row-level `AFTER INSERT OR UPDATE OR DELETE` **constraint trigger, deferred to commit**, adds 1 to the row's
   workspace version once per transaction (a transaction-local setting marks it done). Taken at commit, the row lock
   is the transaction's last, so writers cannot deadlock on it, and versions follow commit order. An update of
   `workspace` (its switches, such as `advice_mode`) moves its own version the same way. Because the database does
   it, no code path can forget it; a new table gets the trigger in its migration, and a test fails when a table with
   `workspace_id` lacks it.
2. **Writes that change no answer, excluded by name only.** `auth_session` (its idle deadline slides every 5 minutes
   on use) and `auth_passkey` (the sign-in counter) would otherwise end every kept answer while Luis works; no kept
   route reads either, which the gate checks. Every other table counts, `event` and `ai_call` included.
3. **What is kept.** The GET routes Übersicht and Zu prüfen load (the §7.3 list after its changes), and no route that
   reads the session or the member's own data beyond the member id (`/auth/me` stays live). The key is route and
   query, the member, the version, the Berlin date (every kept route reads the clock only as `datetime.now(
   LOCAL_TIME_ZONE).date()`, checked 21 Nov 2026; the build audits each kept route again), and the image's version
   and `rule_version()`. Kept in the API process's memory (one instance, min- and max-scale 1, `deploy/scaleway.md`),
   at most one answer per key, the oldest dropped first above a size limit; a restart or a deploy starts empty.
4. **Never keep a mixed answer.** The route reads the version before and after computing and keeps the answer only
   when both are equal; requests run at READ COMMITTED, so a commit during the computation could otherwise be half
   seen and kept. A failed or non-200 answer is never kept.
5. **Warm after the morning jobs.** The daily fetches (bank 06:30, wallets 06:45, prices, month-end 08:00, key
   figures 08:30 UTC) move the version every morning, so the first load of the day would recompute. One step after
   the last of them computes the kept routes for each member once, in the worker; nothing is warmed after a person's
   own change (the next load after it takes the §7.3 time, about 8 s).
6. **A check that it is right.** `hausbuch perf-overview --digest` gains a mode that reads each kept route twice, once
   kept and once recomputed, and requires equal bodies; the daily step runs the same comparison for one route chosen
   in turn and, on a difference, empties the store and writes a server-log error (no card: the user can do nothing).

Measured 21 Nov 2026, plain CPU: a request that does almost nothing costs 30–50 ms (sign-in check, workspace, JSON),
so a load of Übersicht whose answers are all kept costs about 1 s of CPU for its ~27 requests, **about 1–1.5 s live**
instead of about 8; the first load after a change stays about 8 s, unless the warm step has run since. Zu prüfen the
same, about 0.5 s. It changes compute-on-read (phase 9 D242 and this app's reads in general) for the routes listed:
the answer shown may be one computed earlier, but only under the same data, date and code. Gate: the digest
comparison above on the live data (kept equal to recomputed for every route), a test per excluded table that no kept
route reads it, a test that a write in a second workspace does not move this one's version (row-level security on
the new table, forced like every table), a concurrency test (a commit during a computation is not kept), and one
review (persisted state, a silent-failure path, a migration).

**As built (PR #529, merged and deployed 21 Nov 2026, images 902f60277dc8, migration 0050 applied before the image; this section said „being built“ until then).** Two changes from the scope above. (a) The deferred trigger does not sit on the workspace tables: a deferred trigger there holds pending events on every table a transaction wrote, and Postgres then refuses TRUNCATE and ALTER TABLE on it, which broke the append-only TRUNCATE guards of phases 2, 4 and 4b. So each workspace table gets three plain AFTER row triggers (INSERT, UPDATE, DELETE; `workspace` INSERT and UPDATE) that queue one row per transaction and workspace into `workspace_data_version_pending`, and the one deferred trigger `bump_data_version` on that helper table moves the version at commit. Both new tables are under forced row-level security, the functions SECURITY INVOKER; a write by the owner role without `app.workspace_id` now raises instead of skipping the bump, and the version can only grow. (b) The key also holds the UTC day and the request language. Kept: 26 GET routes (`KEPT_ROUTES` in `api/kept_answers.py`), 200 answers only, the exact bytes, up to 32 MB; not kept: `/api/inbox` and `/api/inbox/lines` (the inbox reads the time of day: a 24-hour and a 30-day window), `/api/auth/me` and `/api/auth/members` (sessions and passkeys are outside the version). A warm step at 09:00 UTC asks the page's routes once per member; a check at 15:00 UTC recomputes one kept route in turn and empties the store with a server-log error on any difference; the request log line has a `kept` field. Gate on the live data with 0050 applied: the live catalogue holds 182 mark triggers on 61 tables and the one bump trigger; `perf-overview --check-kept --repeat 2`: 24 of 24 keepable requests served kept on the second pass, 22 compared with a recomputation, all equal. Not yet checked on the live container: that a person's write moves the version so the next load recomputes (covered by the CI tests in `tests/db/test_data_version.py`), and the live load time (measured the same evening from the API's request log, 22:33–22:42 UTC: the first Übersicht load after a sign-in about 10 s, every route recomputed and kept; the next two loads about 2 s and 3 s, each route served kept in 0.04–0.35 s except the inbox; Zu prüfen about 3–4 s, most of it the inbox computed three times, see §7.5; no request failed; this said „not yet checked“ until then). Two things to know: a sign-in writes an event, so the first load after it recomputes; a write after the 09:00 warm step discards the warmed answers. Review: pending, Mon 23 Nov 2026, on the deployed code (FOCUS: the triggers and row-level security, a stale answer served silently).

### 7.5 Further speed steps (scoped 21 Nov 2026, not built)

Measured live after §7.4 (§7.4 as built): a repeat load of Übersicht about 2–3 s, the first after a sign-in or a
change about 10 s, Zu prüfen about 3–4 s. Three steps, each answer-identical like §7.2–§7.4; none changes a figure.

**(a) Every section kept.** 90 of the 116 GET routes are not in `KEPT_ROUTES`. Add each one whose answer depends only
on the workspace's stored rows, the day and the code, starting with the slow ones the pages ask on every visit:
Planung (`/household-plan`, `/household-plan/comparison` about 4 s live, `/household-budget`, `/household-plan/explanation`),
Konten (`/transfers` about 2 s live, `/transfers/open`, `/accounts/{id}/month`, `/accounts/{id}`), Anlagen
(`/assets/overview`, `/assets/etf`, `/holdings/crypto`, `/wallets/holdings`, `/net-worth`), the property pages
(`/performance/actuals`, `/performance/projection`, `/purchase-summary`, `/anlage-v/sources`, the loan's
`/schedule` and `/summary`), Steuern (`/tax/questionnaire`, `/return-inputs`, `/income-inputs`, `/tax-rate`,
`/tax/anlage-n/years`). Each added route is checked by reading its service for a clock read (`datetime.now`, `ctx.now`
beyond its date, a window in hours or days) and for a read outside the workspace's rows. Never kept: the auth routes
(sessions and passkeys sit outside the version), `/app-secrets` and `/bank-credential-slots`, `/wallets/{id}/addresses/{id}/plain`
(it writes an event and shows a full address), a file or letter download (`/exports/holdings`, `/flat-rate-letter`),
and a sync's progress (`/syncs/{id}`, `/accounts/{id}/syncs`, `/wallets/{id}/syncs`), polled while a job runs and cheap.
Also: a sign-in's own event (`user.signed_in` and the passkey events on entity `user`) stops moving the version, since no
kept route shows it (checked per route; `/events` and the History reads stay unkept or skip it), so the first load
after a sign-in is no longer a full recomputation. Gate: `perf-overview --check-kept` extended to the new routes on
the live data, all equal; the 15:00 check covers them by itself.

**(b) The inbox kept until its next time window.** GET `/inbox` and `/inbox/lines` are computed on every page (the
menu's count) and three times per Zu prüfen load. They read the clock in three sections: email-in (`inbound_mail_items`,
the 24-hour card and the 30-day list), prices (`price_inbox_items`, the streak window), and wallets
(`wallet_inbox_items`, whose `created_at` falls back to `now`, so the answer's bytes change every second: to be made a
stored time first); the day-based sections (questionnaire, transfers, reminders) are covered by the key's Berlin day.
Each clock section returns, beside its items, the moment its answer next changes (the earliest `recorded_at +` window
among the rows it read); the kept entry expires at the earliest of these and is otherwise keyed like §7.4. Every other
section is read once more for a hidden clock read before the inbox joins `KEPT_ROUTES`. Gate: a test per clock section
that the list computed just before its expiry equals the kept one and the one just after differs where a row crosses
its window; the 15:00 check covers the inbox; `--check-kept` on the live data. Risk: a missed clock read keeps a card a
few hours too long or too short, silently; hence the per-section audit and one review. This supersedes the stopped
change below: with the inbox kept, Zu prüfen's three requests are one computation and two kept answers.
Stopped 21 Nov 2026 at Luis's word (to save credits): `/inbox/lines` returning the inbox items too, so Zu prüfen
computes the list once; its partial work sits unpushed on the local branch `worktree-agent-ac4168660c8225ab1` (a WIP
commit whose regenerated `openapi.json` is broken), to be discarded if (b) is built.

**(c) Computed after a change, not at the next load** (Luis: „as soon as a change is made can't this trigger the
computation instead of doing the computation when the page is loading?“). A task in the API process watches the
data versions; once a workspace's version has moved and no write followed for about 30 s, it asks again, as each
member, the requests that member's pages made in the last days (the key paths kept are recorded per member, so the
step needs no copy of each page's parameters, unlike the 09:00 warm step's list), route by route, pausing while a
person's request runs. The morning's bank, wallet and price runs move the version too, so their pages are ready
before the first visit. Limits: one 0.25 vCPU process, so a page opened during those seconds shares the processor;
a change followed by an immediate visit still waits for that visit's own computation. No model call; no new
container (the one API container already runs all day, min-scale 1). Gate: a test that a write followed by the quiet
time leaves the recorded routes kept and equal to a recomputation, and that a person's request during the step is
answered first.

### 7.1 Done when

Luis uses the new menu and every screen on his phone and computer, in German and in English, light and dark, and finds no page that starts with a
scroll past the menu; on the live data the Budget's Sparen ÷ E equals the Sparquote `hausbuch health-check`
recomputes for the same window, to four places; Luis finds the split into needs and wants plausible; and
nothing is coloured before he saves a target.

### 7.6 Less on each screen (scoped 22 Nov 2026 for Übersicht, not built)

Luis, 22 Nov 2026: „the Übersicht has cards that I think are not relevant. Also to reduce text in the app. Some sections have too much text. For example the Immobilien card from the Übersicht has too much text and it expands vertically too much which also changes the other cards.“ Agreed the same day („Those were exactly what I had in mind“). Frontend only: no endpoint, figure or rule changes, and nothing shown today is lost, it moves behind a tap.

**Two rules for the whole app.** (1) A tile or card shows a name, one number and at most one short line; anything more goes into its detail panel (D267). (2) An explanation stays available but is shown on a tap only: in the detail panel or behind a small ⓘ button beside a heading, never as a paragraph on the page. Every other section is trimmed by these rules one at a time, each agreed with Luis before it is scoped.

**Übersicht.**

1. **The tiles stay**, each under rule (1). The tile grid sizes a row by its tallest tile (CSS grid's default `align-items: stretch` on `.tiles-grid`), so one long tile stretched Depot, Krypto and Bank; with rule (1) every tile is short, and a tile's text is cut to its lines rather than growing it.
2. **The Immobilie tile** keeps the money back so far and one line (the own money put in and the percent back). The year it is back (or „Planung prüfen“), the profit at a sale today with its note, value minus loans and „N Immobilien nicht mitgezählt“ move into its detail panel, which already shows them (D286).
3. **To-dos and the next dates stay.**
4. **Removed from Übersicht:** the key-figures block under the to-dos (`HealthSection`: the month-end, the reserve, Kennzahlen, „Ampel-Grenzen festlegen“, the split of the assets, Vermögen im Verlauf), which repeats the tiles and lives on Planung; the card per property (address, units, tenants, loans), which repeats Immobilien; and the buttons „Mieter“ and „Neue Immobilie“ in the page head, which belong on Immobilien. Each must still be reachable where it moves to (checked in the build: Planung has every part of `HealthSection`, Immobilien has the tenants link and „Neue Immobilie“), and the empty state for a household with no property keeps its hint.
5. **Done when** Übersicht fits a computer screen without a scroll past the tiles and to-dos, no tile changes another's height, and every removed item is one tap away where it moved.

**The „Was noch fehlt“ lists** (Luis, 22 Nov 2026: „reduce the text of the still missing cards“), wherever `TodoList` is used (Übersicht, the Anlage V tab, a property's document checklist): each to-do shows its title, its context and its action only; the guide's text (`components/todos.tsx`, WARNING_GUIDE, REQUIREMENT_GUIDE, REMINDER_GUIDE) moves behind „Warum?“, closed by default. Übersicht shows the first five to-dos and „Alle N anzeigen“. A note with nothing to do (`todo: false`) is never in these lists (already the rule there; kept on the tax page's warnings). No guide text is deleted, so `test_todo_guides.py` still holds.

**Zu prüfen** (agreed 22 Nov 2026):

1. **A bank line in two rows**: the date, the payee and the amount; then the suggestion („Vorschlag: <Kategorie>“, the item's proposed category) and the model's question cut to one line when there is one. The model's reasoning is no longer shown on the list (Luis: „No need for the AI reasoning. Just the suggestion.“); the detail panel keeps it. The purpose text and the private account's name move into the detail panel.
2. **The page head** holds „Hochladen“ and „Foto“; the upload box and the sentence about statements under Konten leave the page (any upload box takes any document, so the sentence is no longer needed); the upload notices stay, one line each.
3. **„Foto“ opens the camera at once** (Luis: „The take photo button should directly open the camera. Not that you to another section.“): the button is the label of a hidden file input with `capture="environment"`, as on the capture page. The photos then open the page strip of `components/capture.tsx` (turn, reorder, „Seite hinzufügen“, „Als ein Dokument hochladen“) in the detail panel on Zu prüfen, so a several-page document still becomes one PDF and nobody leaves the page. On a computer the same button opens the file picker (browsers ignore `capture` there). `/capture` stays for the home-screen shortcut; a browser opens the camera only on a tap, never by itself on page load, so that page keeps its button.
4. **The other cards** (transfers, wallets, exchanges, payment proofs, imports): a title and the action; their explanations (`muted` paragraphs in `components/inbox.tsx`) behind „Warum?“. „Alle N bestätigen“'s introduction behind ⓘ; its categories and counts stay.
5. **Done when** ten waiting bank lines fit a phone screen without the reasoning, and „Foto“ opens the camera in one tap on the phone.

**Immobilien** (22 Nov 2026): stays as it is (Luis: „I think it looks good as of now“), except that the property page's section „Unterlagen“ is renamed **„Steuer-Checkliste“** (English „Tax checklist“; Luis: „It's not clear that these are the documents for the tax return and that it's a checklist“); the route key `unterlagen` and old `?tab=` links keep working.

**Konten** (22 Nov 2026, from Luis's phone screenshot; the rest of the page stays):

1. **The kind tabs** wrapped into uneven rows (2, 3, 1) at phone width, their labels of different lengths. Shorter labels (Bank, Karten, Depots, Börsen, Wallets, Darlehen; English Bank, Cards, Depots, Exchanges, Wallets, Loans) in an even grid of three equal columns on the phone, one row on the computer.
2. **The total** under the tabs: the amount on its own line, large, and under it one grey line „<N> Konten · heute“, instead of the count on the left and „… heute in eurem Nettovermögen“ on the right. Each tab shows its own total only: the Börsen and Wallets tabs each showed the whole crypto total (`by_class.crypto`); a total per tab comes from the net worth's lines of that tab's accounts, summed by the server, never in the browser (principle 1), so this needs the overview read to state a total per kind. With several credit cards the Karten tab gets its total the same way.
3. **To check, not layout:** the screenshot of 22 Nov 2026 showed both synced bank accounts' balance „am 12. Nov 2026“ although the daily fetch runs every morning; either nothing was booked since (a run with no new line stores no import, phase 5 D117) or the balance date is stale. To be checked against the bank sync rows before the build.

**Haushalt** (22 Nov 2026, Luis: „I'm not sure the saved not spent section is needed. Also I need to scroll to see the amounts in the spending category“, sideways and down; „I just don't see what to do with the data that's presented“):

1. **„Gespart, nicht ausgegeben“ leaves as a card**: the surplus card gets one line „Davon angelegt: <Betrag> ›“, which opens the card's content (investments by code, transfers to own accounts, what stayed on the current accounts) in the detail panel.
2. **The categories without a sideways scroll**: on the phone each group is one row with the amount beside its name and a thin bar under it, no table; the mean and the plan figures move into the detail panel of a tapped group (its subcategories open there too). The six largest groups first and „Alle <N> anzeigen“. The computer keeps the table.
3. **„Was diesen Monat anders ist“** at the top: up to three groups whose month differs most from their own mean over the closed months („Restaurant: 180 € mehr als üblich“), each opening its payments in the panel. The differences are computed by the server from phase 9's household month and its means (principle 1), only over closed months; a month in progress compares only once it is closed or says „bisher“. A threshold below which nothing is named (an amount and a share of the mean) is set in the build's spec; no colour, no advice, no reminder.
4. **The Budget in one line**: the month against the household's own targets from Planung › Budget (D274), read from `GET /household-budget` („Notwendiges 1.900 von 2.000 €“, „Wünsche 640 von 800 €“, „Sparen …“), with a link to Planung › Budget; shown only once targets are saved, coloured as there (green when met, amber when missed, never red).
5. **Recurring payments and subscriptions** (taken from „Für später geparkt“): every payment that recurs monthly, quarterly or yearly to one payee, with its yearly total, and a price rise flagged with the month it started („2 € mehr seit März“). Detected by code from the confirmed private lines (payee as D160 (4) recognises it, interval, amount), never by the model; a payment the person marks „kein Abo“ leaves the list. A new read, so it needs its own short spec before the build: the detection rule and its fixtures, where a marked answer is stored (a migration if it is a new table), and whether a payment that stopped is shown. Notice deadlines from contract documents stay parked.
6. **Done when** the category amounts are visible on a phone without a sideways scroll, and the three additions show only figures the server computed.

**Anlagen** (22 Nov 2026, Luis: „we should just show the total and the amount of each portfolio. Then when you click it you can show the individual funds and coins in the side panel. We can also add the since bought. And in the overview we should also show the current performance of each portfolio“, „I meant the performance since the beginning“, „an average apr would also be nice“):

1. **Four tabs: Übersicht, ETF, Krypto, Immobilie** (Luis, 22 Nov 2026: one page without tabs was scoped and drawn first, then „I think I want to go back to the tabs. The overview shows the summary of Krypto and etf. And then each tab goes into more detail. I would like the etf to also have similar cards to Krypto when applicable“, and „I think the planning card for the property should be moved to the investment section“; mockup `docs/mockups/20d-assets-tabs-phone`, chosen the same day: „Yeah looks good“). Every tab shows a portfolio (a depot, an exchange, a wallet) as one card; a tapped card opens its funds or coins in the detail panel, each with its value and its gain since purchase (value less the purchase cost of the units still held, from the FIFO lots for coins and the booked trades for funds; „Einstand fehlt“ where a fund's purchase cost is not in the app, never 0 €).
   - **Übersicht**: the total, which counts the property's equity (its estimated value less the loan's balance, the net worth's own figures), with a line naming that share as an estimate; the split bar ETF, Krypto, Immobilie; the gain since the start and the yearly return of ETF and Krypto together (never the property's, whose value is an estimate, not a price); one card per kind: ETF and Krypto with value, gain since the start and yearly return and one line (depots and funds and the monthly savings plans; accounts, coins and what is tax-free today), Immobilie with its equity and „X € von Y € eigenem Geld zurück“ (D286's figure). A card opens its tab.
   - **ETF**: four cards like Krypto's, only from data the app holds: „ETF gesamt“ (value, depots, funds, cash), „Gewinn seit Beginn“ (euros, percent, p. a.), „Ausschüttungen <Jahr>“ (the distributions booked from the year's Ertragsabrechnungen) and „Sparpläne“ (the savings plans phase 9 D245 detects, their monthly sum and next date); then one card per depot. A depot's panel holds its funds, each fund's price check, and its documents („Postfach“). No card for the Sparer-Pauschbetrag or the Vorabpauschale until phase 8.
   - **Krypto**: its four cards as today („Krypto gesamt“, „Unrealisierter Gewinn“, „Heute steuerfrei verkaufbar“, „Nächste steuerfrei“), then one card per exchange and wallet. „Heute steuerfrei verkaufbar“ opens a panel listing the lots that are tax-free today by account (units, value, purchase day) and below them „Als Nächstes steuerfrei“, the next lots by date; „Nächste steuerfrei“ opens the same list. An exchange's or wallet's panel holds its coins with their gain and „steuerfrei ab“, and a tapped coin its lots; an exchange's also says whether it agrees with the typed balance.
   - **Immobilie** (moved from Planung, amends D270): four cards: „Eigenkapital“ (estimated value less the loan's balance), „Geld zurück“ (D286), „Rendite bis Verkauf“ (phase 4's IRR at the scenario's sale year) and „Steuerfrei verkaufbar ab“ (ten years after the notarised purchase, phase 4 D88's date); then the year's actuals, the projection to sale with its levers („Ändern“), the sale with its profit bridge and the §23 check, and the saved scenarios, all as Planung › Immobilie shows them today, by the same endpoints. With more than one property, one card per property opens its own projection. The Pauschalen-Check (phase 4 D155, its to-dos and the tenant letter) moves to the property's page under Immobilien, beside the tenancies, not to this tab.
   - **Links**: `/planning?section=immobilie` and `?immobilie=<id>` open Anlagen › Immobilie (D266: routes are kept); an old `?tab=etf` or `?tab=krypto` link opens its tab as today.
2. **The figures**, computed by the server from stored data (principle 1), per portfolio and for the total: gain since the start = today's value + everything taken out − everything paid in; percent = that gain ÷ everything paid in; the average yearly return is the money-weighted return (internal rate of return, phase 4's `rules/performance/irr.py` `xirr`) over the dated payments in and out and today's value. Money in and out are the linked transfers from and to other own accounts (phase 6 D162, phase 7 D184) and, for a coin moved between own wallets and exchanges, its value on the day. A move between two own portfolios is money out of one and in to the other; the total counts only money from or to outside the portfolios. The time-weighted return funds publish is not shown (Luis chose the money-weighted one, 22 Nov 2026, as what the household's own money earned).
3. **The start is the first day the app knows the portfolio**: a portfolio that existed before its first record counts its value that day as the first money in, and the row says so („seit 1. Jan. 2026“). No yearly rate is shown for less than a year of history (an annualised rate over weeks misleads), only the euros and the percent.
4. **Trimmed**: the ETF tab's two cards „Sparer-Pauschbetrag“ and „Vorabpauschale“, which only say they come with phase 8, leave until phase 8 is built; the price sources, the FIFO explanation and „Werte von heute …“ behind ⓘ; the Krypto tab's „Bestandsnachweis 31.12.“ card becomes one button at the foot of the tab (its reminder kind stays).
5. **To settle in the build's spec**: whether a depot's payments in are complete from its documents (deposits before the first statement), a dividend paid out to a bank account (money taken out), and fees (inside the value, never added back).
6. **Done when** each tab's cards fit a phone screen above its portfolios, each portfolio's gain and yearly rate agree with a hand check from its payments and today's value, and the Immobilie tab shows the same projection, sale and scenarios Planung › Immobilie showed, figure for figure.

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
- **Q-O179 (open, not blocking): a stale plan draft can overwrite another person's save.** Planung keeps one
  unsaved draft of every lever for all sections, and it survives a reload of the page's data. If one person saves
  (for example „Ziele speichern“) while the other has an unsaved change open and that page reloads its data without
  being left (a to-do's „Monatsabschluss festhalten“), the second person's „Szenario speichern“ supersedes the new row
  with the old snapshot, and the first person's targets leave the row in force without a message. Without that
  reload the server refuses the stale save, which is safe. Options: remember the row the draft started from and warn
  when the default is another row, or keep the draft as changes applied onto the saved levers. Found by the review of
  step 4's saving path, 21 Nov 2026; small harm for a two-person household.
- **Same gap in phase 9, open:** `rules/plan/spending.month_spending` also reads `spending_by_category`, so a group netting to exactly 0 in a month drops its codes from the per-code means of „Ausgaben durchspielen“ (the totals stay right). Not fixed in phase 10, since `rules/plan/` feeds `rule_version()`; to be decided with a phase 9 rule-version change.
- **Rollback limit (from step 1's review):** once a household scenario is saved by the step 1 image, its row carries
  `budget_targets` and `budget_class_overrides`, which an older image refuses (`extra="forbid"`), so that image
  cannot be rolled back after the first save. The frontend's `fillLevers()` must carry both fields from step 4 on,
  or a save from Planung drops the household's targets.

- Q-O177 (settled 20 Nov 2026): `/household-budget` serves the window only. The Haushalt mockup (19) draws
  no Budget, so no screen asks for one month; a month view can be added later without a schema change.
