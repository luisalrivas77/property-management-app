# English for Hausbuch's own words (phase 10 D284)

German is the app's source language and stays the default. When you write a screen's English
(`en` in its file under `src/i18n/`), use the words below so the same thing has one name
everywhere. British spelling („notarised“, „categorise“), sentence case, no full stop on a
label.

## Words that stay German

Official tax forms, their lines and the offices and documents named in them keep their German
name, because that is what the household sees on the form and in ELSTER. On a screen, give the
short English gloss in brackets the first time the word appears, then the German word alone.

| German | Gloss on first use |
| --- | --- |
| Anlage V | Anlage V (rental income form) |
| Anlage N | Anlage N (employment income form) |
| Anlage KAP | Anlage KAP (capital income form) |
| Anlage SO | Anlage SO (other income form, e.g. crypto) |
| Werbungskosten | Werbungskosten (deductible expenses) |
| AfA | AfA (depreciation) |
| Zeile | Zeile (line on the form) |
| Kennzahl | Kennzahl (field number in ELSTER) |
| Lohnsteuerbescheinigung | Lohnsteuerbescheinigung (annual wage tax certificate) |
| Gehaltsabrechnung | Gehaltsabrechnung (payslip) |
| Einkommensteuerbescheid | Einkommensteuerbescheid (income tax assessment) |
| Grundsteuer | Grundsteuer (property tax) |
| Grunderwerbsteuer | Grunderwerbsteuer (property transfer tax) |
| ELSTER | ELSTER (the tax office's online portal) |
| Finanzamt | Finanzamt (tax office) |
| Hausgeld | Hausgeld (monthly payment to the owners' association) |
| Wirtschaftsplan | Wirtschaftsplan (owners' association budget) |
| Hausgeldabrechnung | Hausgeldabrechnung (owners' association annual statement) |
| Nebenkostenpauschale | Nebenkostenpauschale (flat-rate service charge) |
| Kaution | Kaution (rent deposit) |
| Grundbuch | Grundbuch (land register) |
| Sparer-Pauschbetrag | Sparer-Pauschbetrag (savings allowance) |
| Freistellungsauftrag | Freistellungsauftrag (tax exemption order at a bank) |
| Jahressteuerbescheinigung | Jahressteuerbescheinigung (bank's annual tax certificate) |
| Spekulationsfrist | Spekulationsfrist (holding period for tax-free sale) |

Letters to tenants, the ELSTER field sheet and the yearly archive stay German in either language.

## The app's own words

| German | English |
| --- | --- |
| Übersicht | Overview |
| Zu prüfen | To review |
| Haushalt | Household |
| Anlagen | Investments |
| Mehr | More |
| Konten | Accounts |
| Konto | Account |
| Umsätze | Transactions |
| Umsatz, Bankumsatz | Transaction, bank line |
| Kontoauszug | Statement |
| Bankabruf | Bank sync |
| Immobilien | Properties |
| Immobilie | Property |
| Einheit | Unit |
| Mietverhältnis | Tenancy |
| Mieter | Tenant(s) |
| Eigentümer | Owner(s) |
| Darlehen | Loan |
| Steuern | Taxes |
| Steuerjahr | Tax year |
| Eure Angaben | Your details |
| Einkommen & Haushalt | Income & household |
| Jahresfragen | Yearly questions |
| Planung | Planning |
| Budget | Budget |
| Notwendiges | Needs |
| Wünsche | Wants |
| Sparen | Saving |
| Reserve | Reserve |
| Anlegen | Investing |
| Vermögen, Nettovermögen | Net worth |
| Sparquote | Savings rate |
| Anlegbar pro Monat | Available to invest per month |
| Kennzahlen | Key figures |
| Dokumente | Documents |
| Beleg | Receipt |
| Nachweis, Zahlungsnachweis | Proof, proof of payment |
| Einstellungen | Settings |
| Darstellung | Appearance |
| Sprache | Language |
| Hochladen | Upload |
| Foto aufnehmen | Take a photo |
| Abmelden | Sign out |
| Bestätigen | Confirm |
| Übernehmen | Apply |
| Künftig immer so zuordnen | Always categorise like this |
| Kategorie | Category |
| Vorschlag | Suggestion |
| Was noch fehlt | Still missing |
| Nächste Termine | Upcoming dates |
| Monatsabschluss | Month-end |
| Gegenbuchung | Matching transaction |
| Überweisung zwischen eigenen Konten | Transfer between own accounts |
| Kaltmiete | Base rent |
| Warmmiete | Rent including service charges |
| Übergabe | Handover |
| Beurkundet | Notarised |
| Wohnfläche | Living area |

## Formats

Numbers, money and dates come from `useFormat()` in `src/i18n/format.ts`, never typed by hand:
German „1.400,50 €“, „3,65 %“, „15.03.2026“; English „€1,400.50“, „3.65%“, „15 Mar 2026“.
