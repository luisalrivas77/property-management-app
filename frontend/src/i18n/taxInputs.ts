// The tax inputs a person types (components/taxInputs.tsx): an allocation's §82b spread and
// renovation questions, the ortsübliche Miete, a Gutachten's useful life, the Einbauküche, the
// Grundsteuer schedule and the tenancy form's due day and Nebenkosten terms. German first,
// English beside it (glossary.md); tax terms and statutes stay German, glossed on first use.
import type { components } from "@/api/types";

import { defineMessages } from "./core";

type Schemas = components["schemas"];

export const OPERATING_COSTS_TERMS_DE: Record<Schemas["OperatingCostsTerms"], string> = {
  prepayment: "Vorauszahlung mit jährlicher Abrechnung",
  flat_rate: "Pauschale (keine Abrechnung)",
  included: "In der Miete enthalten (Warmmiete, nicht gesondert vereinbart)",
};
export const OPERATING_COSTS_TERMS_EN: Record<Schemas["OperatingCostsTerms"], string> = {
  prepayment: "Prepayment with a yearly statement",
  flat_rate: "Flat rate (no statement)",
  included: "Included in the rent (rent including service charges, not agreed separately)",
};

export const RENOVATION_TREATMENT_DE: Record<Schemas["RenovationTreatment"]["treatment"], string> = {
  erhaltung: "Erhaltungsaufwand",
  herstellung: "Herstellungskosten",
};
export const RENOVATION_TREATMENT_EN: Record<Schemas["RenovationTreatment"]["treatment"], string> = {
  erhaltung: "Erhaltungsaufwand (maintenance, deductible at once)",
  herstellung: "Herstellungskosten (improvement, through the AfA)",
};

export const EXCLUSION_REASON_DE: Record<Schemas["ExcludeFrom15Pct"]["reason"], string> = {
  annual_maintenance: "Jährlich übliche Erhaltungsarbeit",
  extension: "Erweiterung (z. B. Anbau, Aufstockung)",
  damage_after_acquisition: "Schaden, der erst nach dem Kauf entstand",
};
export const EXCLUSION_REASON_EN: Record<Schemas["ExcludeFrom15Pct"]["reason"], string> = {
  annual_maintenance: "Usual yearly maintenance work",
  extension: "Erweiterung (extension, e.g. an annex or an added storey)",
  damage_after_acquisition: "Damage that arose only after the purchase",
};

export const MARKET_RENT_SOURCE_DE: Record<Schemas["MarketRent"]["source"], string> = {
  mietspiegel: "Mietspiegel",
  gutachten: "Gutachten",
  portal: "Mietpreisportal",
  other: "Andere Quelle",
};
export const MARKET_RENT_SOURCE_EN: Record<Schemas["MarketRent"]["source"], string> = {
  mietspiegel: "Mietspiegel (local rent index)",
  gutachten: "Gutachten (expert appraisal)",
  portal: "Rent price portal",
  other: "Other source",
};

export const taxInputsText = defineMessages({
  de: {
    extensionHint:
      "Eine Erweiterung (Anbau, Aufstockung) ist immer Herstellungskosten: die Zahlung als Renovierung buchen und „Herstellungskosten“ wählen.",
    extensionLocksTreatment: "Erst die Ausnahme „Erweiterung“ zurücknehmen.",
    heading: "Steuerliche Angaben",
    rowOf: (amount: string) => `Zeile über ${amount}`,
    spreadLabel: "Auf 2–5 Jahre verteilen (§ 82b EStDV)",
    spreadDescription:
      "Größere Erhaltungsaufwendungen dürfen gleichmäßig auf zwei bis fünf Jahre verteilt werden, statt sie im Jahr der Zahlung ganz abzuziehen. Eine Wahl, keine Pflicht.",
    noSpread: "Nicht verteilen (im Jahr der Zahlung)",
    spreadYears: (years: number) => `Auf ${years} Jahre verteilen`,
    treatmentLabel: "Renovierung ist",
    treatmentDescription:
      "Erhaltungsaufwand erhält oder erneuert, was schon da ist, und ist sofort abziehbar. Herstellungskosten schaffen etwas Neues oder heben den Standard deutlich; sie gehen in die AfA ein.",
    notClassified: "Noch nicht eingeordnet",
    exclusionLabel: "Zählt nicht zur 15 %-Grenze (Grund)",
    exclusionDescription:
      "In den ersten drei Jahren nach dem Kauf zählen Renovierungen zur 15 %-Grenze der anschaffungsnahen Herstellungskosten. Ausgenommen sind jährlich übliche Erhaltungsarbeiten, Erweiterungen (die ohnehin Herstellungskosten sind) und Schäden, die erst nach dem Kauf entstanden.",
    countsTowardLimit: "Zählt zur Grenze",
    saveAnswers: "Angaben speichern",
    savedNote: "Gespeichert. Die bisherige Angabe bleibt im Verlauf.",
    withdrawFailed: "Das Zurückziehen hat nicht geklappt. Bitte erneut versuchen.",
    correct: "Korrigieren",
    withdraw: "Zurückziehen",
    enter: "Eintragen",
    saveCorrection: "Korrektur speichern",
    receiptOptional: "Beleg (optional)",
    noteOptional: "Notiz (optional)",
    fromTaxYear: "Ab Steuerjahr",
    taxYearRequired: (example: number) => `Bitte ein Steuerjahr eingeben, z. B. ${example}.`,
    correctionFromYear: (year: number | null | undefined) =>
      `Eine Korrektur ersetzt die Angabe ab ${year ?? ""}; die bisherige bleibt im Verlauf.`,
    // ortsübliche Miete
    wholeFlat: "Gilt für die ganze Wohnung, auch wenn sie zimmerweise vermietet ist.",
    noMarketRent:
      "Noch keine ortsübliche Miete erfasst. Sie wird für den Vergleich mit der vereinbarten Miete gebraucht (§ 21 Abs. 2 EStG).",
    validFrom: "Gültig ab",
    coldRentPerM2: "Kaltmiete je m²",
    source: "Quelle",
    enterMarketRent: "Ortsübliche Miete eintragen",
    validFromRequired: "Bitte das Datum eingeben, ab dem der Wert gilt.",
    marketDateTaken: "Für dieses Datum ist schon ein Wert erfasst. Bitte diesen korrigieren.",
    marketRentLabel: "Ortsübliche Miete (kalt, €/m², netto laut Mietspiegel)",
    marketRentDescription:
      "Die ortsübliche Kaltmiete je Quadratmeter ohne Nebenkosten, z. B. der Mittelwert der passenden Spalte im Mietspiegel. Liegt die vereinbarte Miete unter 50 % bzw. 66 % davon, ist der Werbungskostenabzug gefährdet.",
    validFromDescription: "Ab wann der Wert gilt, z. B. das Datum, ab dem der Mietspiegel gilt.",
    sourceDescription: "Woher der Wert stammt.",
    marketReceiptDescription:
      "Der Mietspiegel, das Gutachten oder ein Ausdruck des Portals, als Nachweis für das Finanzamt.",
    marketNoteDescription: "Zum Beispiel die Spalte oder das Feld im Mietspiegel.",
    marketCorrectionHint: (date: string) => `Eine Korrektur ersetzt den Wert ab ${date}; der bisherige bleibt im Verlauf.`,
    // a shorter useful life
    lifeBeforePurchase: "Die Nutzungsdauer kann frühestens ab dem Steuerjahr des Kaufs gelten.",
    statutoryAfa:
      "Die AfA läuft mit dem gesetzlichen Satz. Belegt ein Gutachten eine kürzere tatsächliche Nutzungsdauer des Gebäudes, wird der Restwert stattdessen über die restlichen Jahre verteilt.",
    lifeRow: (years: number, year: number | null | undefined) => `${years} Jahre ab ${year ?? ""} (Gutachten)`,
    enterLife: "Nutzungsdauer laut Gutachten eintragen",
    lifeYearsRequired: "Bitte die Nutzungsdauer in ganzen Jahren eingeben.",
    lifeDocumentRequired:
      "Bitte das Gutachten auswählen; ohne Nachweis erkennt das Finanzamt die kürzere Nutzungsdauer nicht an.",
    lifeYearTaken: "Für dieses Steuerjahr ist schon eine Nutzungsdauer erfasst. Bitte diese korrigieren.",
    lifeLabel: "Nutzungsdauer laut Gutachten (Jahre)",
    lifeDescription:
      "Die Restnutzungsdauer des Gebäudes, die das Gutachten ab dem Kauf oder ab dem genannten Jahr feststellt. Sie muss kürzer sein als die gesetzliche (40 Jahre bei Baujahr vor 1925, sonst 50 Jahre, ab 2023 fertiggestellt 33⅓ Jahre).",
    lifeFromDescription:
      "Das erste Steuerjahr, für das die kürzere Nutzungsdauer gilt; meist das Jahr des Kaufs. Davor bleibt die gesetzliche AfA.",
    appraisal: "Gutachten",
    appraisalDescription: "Das Gutachten über die Restnutzungsdauer; das Finanzamt fragt danach.",
    lifeNoteDescription: "Zum Beispiel wer das Gutachten erstellt hat.",
    // the Einbauküche
    movableEstimateHint:
      "Nennt der Kaufvertrag keinen Preis für die Küche, einen angemessenen Wert zum Zeitpunkt des Kaufs schätzen und die Grundlage der Schätzung (z. B. einen Ausdruck des Küchenplaners) als Beleg aufbewahren.",
    movableIntro:
      "Eine Einbauküche ist kein Teil des Gebäudes: sie wird über ihre eigene Nutzungsdauer (meist 10 Jahre) abgeschrieben, in einer eigenen Zeile der Anlage V. War sie im Kaufpreis enthalten, kommt ihr Wert aus der AfA des Gebäudes heraus.",
    movableRow: (amount: string, years: number, year: number | null | undefined, inPrice: boolean, hasDocument: boolean) =>
      `Einbauküche ${amount}, ${years} Jahre ab ${year ?? ""}` +
      `${inPrice ? " (im Kaufpreis enthalten)" : " (gesondert gekauft)"}${hasDocument ? "" : ", ohne Beleg"}`,
    enterKitchen: "Einbauküche eintragen",
    kitchenLifeRequired: "Bitte die Nutzungsdauer in ganzen Jahren eingeben (1 bis 25).",
    max25Years: "Höchstens 25 Jahre.",
    amountAboveZero: "Bitte einen Betrag über 0 € eingeben.",
    kitchenTaken: "Eine Einbauküche ist schon erfasst. Bitte diese korrigieren.",
    kitchenKeepsYear: "Eine Korrektur behält das erste Jahr. Um es zu ändern, die Küche zurückziehen und neu eintragen.",
    kitchenValue: "Wert der Küche (€)",
    kitchenValueDescription: "Ihr Anteil am Kaufpreis (brutto).",
    lifeYears: "Nutzungsdauer (Jahre)",
    kitchenLifeDescription: "Für eine Einbauküche meist 10 Jahre.",
    inPurchasePrice: "Im Kaufpreis enthalten",
    inPurchasePriceHint:
      "Mit der Wohnung gekauft: ihr Wert und ihr Anteil an den Nebenkosten kommen aus der AfA des Gebäudes heraus; die AfA beginnt mit dem Übergang. Eine gesondert gekaufte Küche wird noch nicht unterstützt; sie braucht ihr Anschaffungsdatum.",
    kitchenFromDescription: "Das Jahr des Kaufs der Wohnung.",
    kitchenReceiptDescription: "Kaufvertrag, Rechnung oder die Grundlage der Schätzung, z. B. ein Ausdruck des Küchenplaners.",
    kitchenNoteDescription: "Zum Beispiel wie der Wert geschätzt wurde.",
    // the Grundsteuer schedule
    noGrundsteuer:
      "Noch kein Grundsteuerbescheid erfasst. Ohne ihn erinnert die App an die gesetzlichen Termine (15. Februar, Mai, August und November), sobald ein Bescheid verknüpft ist.",
    grundsteuerRow: (year: number | null | undefined, amount: string, rhythm: string | null) =>
      `Ab ${year ?? ""}: ${amount} im Jahr${rhythm ? `, ${rhythm}` : ""}`,
    paymentModeIs: (mode: string) => `Zahlungsweise: ${mode}`,
    openBescheid: "Bescheid öffnen",
    enterGrundsteuer: "Grundsteuerbescheid eintragen",
    instalmentIncomplete: "Jede Rate braucht ein Datum und einen Betrag.",
    instalmentRequired: "Bitte mindestens eine Rate mit Datum und Betrag eintragen.",
    bescheidRequired: "Bitte den Grundsteuerbescheid auswählen.",
    grundsteuerYearTaken: "Für dieses Jahr ist schon ein Bescheid erfasst. Bitte diesen korrigieren.",
    instalmentsInvalid:
      "Die Raten passen nicht: alle im ersten Jahr, nach Datum geordnet, und zusammen der Jahresbetrag (auf 4 Cent genau).",
    grundsteuerFromDescription: "Das erste Jahr, für das der Bescheid gilt; die Raten liegen in diesem Jahr.",
    annualAmount: "Jahresbetrag (€)",
    asInBescheid: "Wie im Bescheid.",
    dueDates: "Fälligkeiten laut Bescheid",
    instalmentOn: (n: number) => `Rate ${n} am`,
    amountEuro: "Betrag (€)",
    rhythmOptional: "Rhythmus (optional)",
    rhythmDescription: "Die Zahlweise, wie der Bescheid sie nennt.",
    noAnswer: "keine Angabe",
    paymentMode: "Zahlungsweise",
    paymentModeDescription:
      "„Erinnern“: eine Woche vorher. „Lastschrift prüfen“: drei Tage danach, falls die Abbuchung fehlt. „automatisch“: Lastschrift, sobald eine Rate per Lastschrift abgebucht wurde, sonst Erinnern.",
    grundsteuerBescheid: "Grundsteuerbescheid",
    bescheidCorrectionHint: (year: number | null | undefined) =>
      `Eine Korrektur ersetzt den Bescheid ab ${year ?? ""}; der bisherige bleibt im Verlauf.`,
    // the tenancy form
    dueDayInvalid: "Bitte einen Tag von 1 bis 28 eingeben.",
    dueDay: "Miete fällig am Tag",
    dueDayHint: "Meist der 3. (spätestens am dritten Werktag, § 556b BGB).",
    dueDayDescription:
      "Der Tag im Monat, an dem die Miete laut Mietvertrag fällig ist. Er entscheidet, ob eine Miete, die um den Jahreswechsel gezahlt wird, steuerlich ins alte oder ins neue Jahr gehört (Zehn-Tage-Regel, § 11 EStG).",
    terms: "Nebenkosten laut Mietvertrag",
    termsDescription:
      "Wie der Mietvertrag die Nebenkosten regelt: als Vorauszahlung mit jährlicher Abrechnung, als Pauschale ohne Abrechnung, oder gar nicht gesondert (eine Warmmiete, in der alles enthalten ist). Davon hängt ab, in welcher Zeile der Anlage V die Beträge stehen.",
  },
  en: {
    extensionHint:
      "An Erweiterung (extension, such as an annex or an added storey) is always Herstellungskosten (improvement costs): book the payment as a renovation and choose “Herstellungskosten”.",
    extensionLocksTreatment: "First withdraw the exception “Erweiterung”.",
    heading: "Tax details",
    rowOf: (amount: string) => `Row of ${amount}`,
    spreadLabel: "Spread over 2–5 years (§ 82b EStDV)",
    spreadDescription:
      "Larger maintenance costs may be spread evenly over two to five years instead of being deducted in full in the year paid. A choice, not a duty.",
    noSpread: "Do not spread (in the year paid)",
    spreadYears: (years: number) => `Spread over ${years} years`,
    treatmentLabel: "The renovation is",
    treatmentDescription:
      "Erhaltungsaufwand (maintenance) keeps or renews what is already there and is deductible at once. Herstellungskosten (improvement costs) create something new or raise the standard markedly; they go into the AfA (depreciation).",
    notClassified: "Not classified yet",
    exclusionLabel: "Does not count towards the 15% limit (reason)",
    exclusionDescription:
      "In the first three years after the purchase, renovations count towards the 15% limit for improvement costs soon after acquisition (anschaffungsnahe Herstellungskosten). Exempt are usual yearly maintenance work, extensions (which are improvement costs anyway) and damage that arose only after the purchase.",
    countsTowardLimit: "Counts towards the limit",
    saveAnswers: "Save details",
    savedNote: "Saved. The previous detail stays in the history.",
    withdrawFailed: "Withdrawing did not work. Please try again.",
    correct: "Correct",
    withdraw: "Withdraw",
    enter: "Enter",
    saveCorrection: "Save correction",
    receiptOptional: "Receipt (optional)",
    noteOptional: "Note (optional)",
    fromTaxYear: "From tax year",
    taxYearRequired: (example: number) => `Please enter a tax year, e.g. ${example}.`,
    correctionFromYear: (year: number | null | undefined) =>
      `A correction replaces the detail from ${year ?? ""}; the previous one stays in the history.`,
    wholeFlat: "Applies to the whole flat, even when it is let room by room.",
    noMarketRent:
      "No local comparable rent entered yet. It is needed to compare it with the agreed rent (§ 21 Abs. 2 EStG).",
    validFrom: "Valid from",
    coldRentPerM2: "Base rent per m²",
    source: "Source",
    enterMarketRent: "Enter local comparable rent",
    validFromRequired: "Please enter the date from which the value applies.",
    marketDateTaken: "A value is already entered for this date. Please correct that one.",
    marketRentLabel: "Local comparable rent (base, €/m², net as in the Mietspiegel)",
    marketRentDescription:
      "The local comparable base rent per square metre without service charges, e.g. the mean of the matching column in the Mietspiegel (local rent index). If the agreed rent is below 50% or 66% of it, the deduction of Werbungskosten (deductible expenses) is at risk.",
    validFromDescription: "From when the value applies, e.g. the date from which the Mietspiegel applies.",
    sourceDescription: "Where the value comes from.",
    marketReceiptDescription: "The Mietspiegel, the appraisal or a printout of the portal, as proof for the Finanzamt (tax office).",
    marketNoteDescription: "For example the column or field in the Mietspiegel.",
    marketCorrectionHint: (date: string) => `A correction replaces the value from ${date}; the previous one stays in the history.`,
    lifeBeforePurchase: "The useful life can apply from the tax year of the purchase at the earliest.",
    statutoryAfa:
      "The AfA (depreciation) runs at the statutory rate. If a Gutachten (expert appraisal) proves a shorter actual useful life of the building, the remaining value is spread over the remaining years instead.",
    lifeRow: (years: number, year: number | null | undefined) => `${years} years from ${year ?? ""} (Gutachten)`,
    enterLife: "Enter useful life from a Gutachten",
    lifeYearsRequired: "Please enter the useful life in whole years.",
    lifeDocumentRequired:
      "Please choose the Gutachten; without proof the Finanzamt (tax office) does not accept the shorter useful life.",
    lifeYearTaken: "A useful life is already entered for this tax year. Please correct that one.",
    lifeLabel: "Useful life from the Gutachten (years)",
    lifeDescription:
      "The building's remaining useful life that the Gutachten (expert appraisal) finds from the purchase or from the year it names. It must be shorter than the statutory one (40 years for buildings from before 1925, otherwise 50 years, 33⅓ years for those completed from 2023).",
    lifeFromDescription:
      "The first tax year for which the shorter useful life applies; usually the year of the purchase. Before it the statutory AfA (depreciation) stays.",
    appraisal: "Gutachten",
    appraisalDescription: "The Gutachten (expert appraisal) on the remaining useful life; the Finanzamt (tax office) asks for it.",
    lifeNoteDescription: "For example who wrote the Gutachten.",
    movableEstimateHint:
      "If the purchase contract names no price for the kitchen, estimate a reasonable value at the time of the purchase and keep the basis of the estimate (e.g. a printout of the kitchen planner) as a receipt.",
    movableIntro:
      "A fitted kitchen is not part of the building: it is depreciated over its own useful life (usually 10 years), on its own line of the Anlage V (rental income form). If it was included in the purchase price, its value leaves the building's AfA (depreciation).",
    movableRow: (amount: string, years: number, year: number | null | undefined, inPrice: boolean, hasDocument: boolean) =>
      `Fitted kitchen ${amount}, ${years} years from ${year ?? ""}` +
      `${inPrice ? " (included in the purchase price)" : " (bought separately)"}${hasDocument ? "" : ", without a receipt"}`,
    enterKitchen: "Enter fitted kitchen",
    kitchenLifeRequired: "Please enter the useful life in whole years (1 to 25).",
    max25Years: "At most 25 years.",
    amountAboveZero: "Please enter an amount above €0.",
    kitchenTaken: "A fitted kitchen is already entered. Please correct that one.",
    kitchenKeepsYear: "A correction keeps the first year. To change it, withdraw the kitchen and enter it again.",
    kitchenValue: "Value of the kitchen (€)",
    kitchenValueDescription: "Its share of the purchase price (gross).",
    lifeYears: "Useful life (years)",
    kitchenLifeDescription: "Usually 10 years for a fitted kitchen.",
    inPurchasePrice: "Included in the purchase price",
    inPurchasePriceHint:
      "Bought with the flat: its value and its share of the purchase costs leave the building's AfA (depreciation); the AfA starts at the handover. A kitchen bought separately is not supported yet; it needs its own acquisition date.",
    kitchenFromDescription: "The year the flat was bought.",
    kitchenReceiptDescription: "Purchase contract, invoice or the basis of the estimate, e.g. a printout of the kitchen planner.",
    kitchenNoteDescription: "For example how the value was estimated.",
    noGrundsteuer:
      "No Grundsteuerbescheid (property tax assessment) entered yet. Without one, the app reminds you of the statutory dates (15 February, May, August and November) once a Bescheid is linked.",
    grundsteuerRow: (year: number | null | undefined, amount: string, rhythm: string | null) =>
      `From ${year ?? ""}: ${amount} a year${rhythm ? `, ${rhythm}` : ""}`,
    paymentModeIs: (mode: string) => `Payment: ${mode}`,
    openBescheid: "Open Bescheid",
    enterGrundsteuer: "Enter Grundsteuerbescheid",
    instalmentIncomplete: "Each instalment needs a date and an amount.",
    instalmentRequired: "Please enter at least one instalment with a date and an amount.",
    bescheidRequired: "Please choose the Grundsteuerbescheid.",
    grundsteuerYearTaken: "A Bescheid is already entered for this year. Please correct that one.",
    instalmentsInvalid:
      "The instalments do not fit: all in the first year, in date order, and together the annual amount (to within 4 cents).",
    grundsteuerFromDescription: "The first year the Bescheid applies to; the instalments fall in that year.",
    annualAmount: "Annual amount (€)",
    asInBescheid: "As in the Bescheid.",
    dueDates: "Due dates in the Bescheid",
    instalmentOn: (n: number) => `Instalment ${n} on`,
    amountEuro: "Amount (€)",
    rhythmOptional: "Rhythm (optional)",
    rhythmDescription: "How often it is paid, as the Bescheid names it.",
    noAnswer: "not given",
    paymentMode: "Payment",
    paymentModeDescription:
      "“Remind”: one week before. “Check direct debit”: three days after, in case the debit is missing. “automatic”: direct debit once an instalment was taken by direct debit, otherwise remind.",
    grundsteuerBescheid: "Grundsteuerbescheid",
    bescheidCorrectionHint: (year: number | null | undefined) =>
      `A correction replaces the Bescheid from ${year ?? ""}; the previous one stays in the history.`,
    dueDayInvalid: "Please enter a day from 1 to 28.",
    dueDay: "Rent due on day",
    dueDayHint: "Usually the 3rd (at the latest on the third working day, § 556b BGB).",
    dueDayDescription:
      "The day of the month on which the rent is due under the lease. It decides whether a rent paid around the turn of the year belongs to the old or the new tax year (ten-day rule, § 11 EStG).",
    terms: "Service charges under the lease",
    termsDescription:
      "How the lease settles the service charges: as a prepayment with a yearly statement, as a flat rate without a statement, or not separately at all (a rent including everything). This decides on which line of the Anlage V (rental income form) the amounts stand.",
  },
});
