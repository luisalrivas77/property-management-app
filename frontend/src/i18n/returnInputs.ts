// Steuern › Eure Angaben: the owner's typed facts (components/returnInputs.tsx), a row's evidence
// (components/returnInputEvidence.tsx) and the Werbungskosten block rows of the Anlage N
// (components/anlageNBlocks.tsx). German first, English beside it (glossary.md).
import { defineMessages } from "./core";
import { localized } from "./locale";

export const evidence = defineMessages({
  de: {
    receipt: "Beleg",
    sources: "Fundstellen",
    scrubbed: "Die Fundstellen wurden 90 Tage nach der Übernahme gelöscht (Datenschutz); der Beleg bleibt.",
    byHand: "Von Hand eingetragen; keine Fundstellen aus einem Vorschlag.",
    noValues: "Keine Werte im Vorschlag.",
    edited: " geändert",
    cleared: " geleert",
    openOriginal: (page: number) => `Original auf S. ${page} öffnen`,
    page: (page: number) => `S. ${page}`,
    noSource: " · ohne Fundstelle",
  },
  en: {
    receipt: "Receipt",
    sources: "Where it was read",
    scrubbed: "The passages were deleted 90 days after the values were applied (data protection); the receipt stays.",
    byHand: "Typed by hand; no passages from a suggestion.",
    noValues: "No values in the suggestion.",
    edited: " changed",
    cleared: " cleared",
    openOriginal: (page: number) => `Open the original on p. ${page}`,
    page: (page: number) => `p. ${page}`,
    noSource: " · no passage",
  },
});

const months = (n: number) => (n === 1 ? "Monat" : "Monate");
const monthsEn = (n: number) => (n === 1 ? "month" : "months");

export const blocks = defineMessages({
  de: {
    routeOne: (days: number, km: number, rate: string) => `${days} Tage × ${km} km × ${rate}`,
    tier: (fromKm: number, rate: string) => `ab km ${fromKm} ${rate}`,
    routeTiers: (days: number, km: number, tiers: string) => `${days} Tage × ${km} km (je km ${tiers})`,
    car: (part: string, amount: string) => `Auto: ${part} = ${amount}`,
    rest: (part: string, amount: string) => `Bus, Bahn, Rad, Sammelfahrt: ${part} = ${amount}`,
    restCapped: (cap: string) => `Der Teil ohne Auto ist auf ${cap} im Jahr begrenzt.`,
    publicTransport: (costs: string) =>
      `Die Kosten für Bus und Bahn (${costs}) sind höher als die Pauschale und zählen stattdessen.`,
    employerOffset: (total: string, offset: string) =>
      `${total} − ${offset} Arbeitgeberzuschuss (steuerfrei oder pauschal versteuert)`,
    commute: "Arbeitsweg (Entfernungspauschale)",
    union: "Gewerkschaft: zählt zusätzlich zum Arbeitnehmer-Pauschbetrag",
    accountFlat: "Kontoführung ohne Beleg: die übliche Pauschale",
    homeOffice: "Homeoffice-Tagespauschale",
    homeOfficeDays: (days: number, rate: string, full: string) => `${days} Tage × ${rate} = ${full}`,
    homeOfficeCap: (cap: string) => `höchstens ${cap} im Jahr`,
    workRoom: "Häusliches Arbeitszimmer",
    workRoomFlat: (n: number, flat: string, actual: string) =>
      `Jahrespauschale für ${n} ${months(n)}: ${flat} (anteilige Kosten wären ${actual})`,
    workRoomActual: (n: number, actual: string, flat: string) =>
      `Anteilige tatsächliche Kosten für ${n} ${months(n)}: ${actual} (Pauschale wäre ${flat})`,
    workRoomPrefer: "Nach Ihrer Wahl.",
    workRoomHigher: "Der höhere Betrag zählt.",
    equipment: (label: string) => `Arbeitsmittel: ${label}`,
    equipmentBase: (itemClass: string, year: number, gross: string, share: string, base: string) =>
      `${itemClass}, gekauft ${year}: ${gross} × ${share} beruflich = ${base}`,
    equipmentFullThisYear: "Im Kaufjahr voll abgesetzt.",
    equipmentFullEarlier: (bought: number, year: number) => `Im Kaufjahr ${bought} voll abgesetzt; ${year} zählt nichts mehr.`,
    equipmentSpread: (life: number | string, inYear: number, year: number, disposed: boolean) =>
      `Abgeschrieben über ${life} Monate, davon ${inYear} in ${year}${disposed ? " (nicht mehr genutzt)" : ""}.`,
    telecom: "Telekommunikation",
    telecomLine: (share: string, bills: string, shareAmount: string, capMonthly: string, n: number, cap: string) =>
      `${share} von ${bills} = ${shareAmount}, höchstens ${capMonthly} × ${n} Monate = ${cap}`,
    telecomReimbursed: (amount: string) => `− ${amount} vom Arbeitgeber erstattet`,
    dues: "Beiträge",
    training: "Fortbildung",
    other: "Sonstiges",
  },
  en: {
    routeOne: (days: number, km: number, rate: string) => `${days} days × ${km} km × ${rate}`,
    tier: (fromKm: number, rate: string) => `from km ${fromKm} ${rate}`,
    routeTiers: (days: number, km: number, tiers: string) => `${days} days × ${km} km (per km ${tiers})`,
    car: (part: string, amount: string) => `Car: ${part} = ${amount}`,
    rest: (part: string, amount: string) => `Bus, train, bike, employer shuttle: ${part} = ${amount}`,
    restCapped: (cap: string) => `The part without a car is capped at ${cap} a year.`,
    publicTransport: (costs: string) =>
      `The bus and train costs (${costs}) are higher than the flat rate and count instead.`,
    employerOffset: (total: string, offset: string) =>
      `${total} − ${offset} employer allowance (tax-free or taxed at a flat rate)`,
    commute: "Commute (Entfernungspauschale, commuting allowance)",
    union: "Trade union: counts on top of the Arbeitnehmer-Pauschbetrag (flat allowance)",
    accountFlat: "Account fees without a receipt: the usual flat rate",
    homeOffice: "Home office daily flat rate",
    homeOfficeDays: (days: number, rate: string, full: string) => `${days} days × ${rate} = ${full}`,
    homeOfficeCap: (cap: string) => `at most ${cap} a year`,
    workRoom: "Home study (Arbeitszimmer)",
    workRoomFlat: (n: number, flat: string, actual: string) =>
      `Annual flat rate for ${n} ${monthsEn(n)}: ${flat} (the share of actual costs would be ${actual})`,
    workRoomActual: (n: number, actual: string, flat: string) =>
      `Share of actual costs for ${n} ${monthsEn(n)}: ${actual} (the flat rate would be ${flat})`,
    workRoomPrefer: "As you chose.",
    workRoomHigher: "The higher amount counts.",
    equipment: (label: string) => `Work equipment: ${label}`,
    equipmentBase: (itemClass: string, year: number, gross: string, share: string, base: string) =>
      `${itemClass}, bought ${year}: ${gross} × ${share} for work = ${base}`,
    equipmentFullThisYear: "Deducted in full in the year of purchase.",
    equipmentFullEarlier: (bought: number, year: number) =>
      `Deducted in full in ${bought}, the year of purchase; nothing counts in ${year}.`,
    equipmentSpread: (life: number | string, inYear: number, year: number, disposed: boolean) =>
      `Depreciated over ${life} months, ${inYear} of them in ${year}${disposed ? " (no longer used)" : ""}.`,
    telecom: "Telecommunications",
    telecomLine: (share: string, bills: string, shareAmount: string, capMonthly: string, n: number, cap: string) =>
      `${share} of ${bills} = ${shareAmount}, at most ${capMonthly} × ${n} months = ${cap}`,
    telecomReimbursed: (amount: string) => `− ${amount} reimbursed by the employer`,
    dues: "Fees",
    training: "Training",
    other: "Other",
  },
});

/** One kind's texts on the Eingaben form: its help line, the Beleg field's label, and each
 * field's label, hint and description by field name (components/returnInputs.tsx). */
export interface KindText {
  help: string;
  document?: string;
  labels: Record<string, string>;
  hints?: Record<string, string>;
  descriptions?: Record<string, string>;
}

const KIND_TEXT_DE: Record<string, KindText> = {
  commute: {
    help: "Ein Weg zur Arbeit für einen Zeitraum. Ein Wechsel der Arbeitsstätte im Jahr ist ein zweiter Eintrag.",
    labels: {
      kind: "Art der Arbeitsstätte",
      workplace_label: "Arbeitsstätte (Ort, Straße)",
      period_from: "Von",
      period_to: "Bis",
      days_per_week: "Arbeitstage je Woche",
      absent_days: "Urlaubs-, Krankheits-, Homeoffice- und Dienstreisetage",
      days: "Tage an der Arbeitsstätte",
      km: "Einfache Entfernung (volle km)",
      km_car: "davon mit dem Auto (km)",
      km_collective: "davon mit Sammelbeförderung des Arbeitgebers (km)",
      km_other: "davon mit Bus, Bahn, Rad oder zu Fuß (km)",
      public_transport_costs: "Kosten für Bus und Bahn im Zeitraum (optional)",
    },
    hints: { km_other: "Die drei Teilstrecken ergeben zusammen die Entfernung." },
    descriptions: {
      days: "An wie vielen Tagen Sie tatsächlich hingefahren sind.",
      km: "Die kürzeste Straßenverbindung, eine Strecke.",
    },
  },
  home_office: {
    help: "Die Homeoffice-Tagespauschale: Tage, an denen Sie überwiegend zu Hause gearbeitet haben.",
    labels: {
      days_other_workplace: "Tage zu Hause, obwohl ein Arbeitsplatz beim Arbeitgeber da war",
      days_no_other_workplace: "Tage zu Hause ohne anderen Arbeitsplatz (z. B. Lehrkräfte)",
    },
  },
  work_room: {
    help: "Ein häusliches Arbeitszimmer, das Mittelpunkt der gesamten beruflichen Tätigkeit ist. Der Entwurf vergleicht die Jahrespauschale mit den anteiligen tatsächlichen Kosten.",
    document: "Mietvertrag oder Grundriss (optional)",
    labels: {
      months: "Monate genutzt",
      room_m2: "Zimmer (m²)",
      home_m2: "Wohnung (m²)",
      rent_year: "Jahresmiete (oder Kosten des Eigentums)",
      running_costs_year: "Nebenkosten im Jahr",
      room_only_costs_year: "Kosten nur fürs Zimmer (z. B. Renovierung)",
      prefer: "Wahl",
    },
  },
  work_equipment: {
    help: "Ein Arbeitsmittel oder Abo, auch aus Vorjahren: teure Geräte werden über ihre Nutzungsdauer abgeschrieben, der Entwurf rechnet den Anteil dieses Jahres.",
    document: "Rechnung (optional)",
    labels: {
      label: "Bezeichnung",
      item_class: "Art",
      gross: "Preis brutto",
      net: "Preis netto laut Rechnung (optional)",
      bought_on: "Kaufdatum (auch Vorjahre)",
      professional_share: "Beruflicher Anteil",
      useful_life_months: "Nutzungsdauer in Monaten (nur bei Sonstiges nötig)",
      disposed_on: "Nicht mehr genutzt seit (optional)",
    },
    hints: {
      bought_on: "Ein anderes Kaufjahr: den Eintrag entfernen und neu anlegen.",
      professional_share: "Als Prozent oder Bruch, z. B. 50 % oder 1/2.",
    },
  },
  telecom: {
    help: "Telefon- und Internetkosten: ohne Einzelnachweis zählt ein Anteil der Rechnungen bis zu einer Obergrenze.",
    labels: { bills_year: "Rechnungen im Jahr", months: "Monate", employer_reimbursed: "Vom Arbeitgeber erstattet" },
  },
  professional_association: {
    help: "Beiträge zu Gewerkschaften und Berufsverbänden. Gewerkschaftsbeiträge zählen ab 2026 zusätzlich zum Pauschbetrag.",
    document: "Beitragsbescheinigung (optional)",
    labels: { name: "Verband", amount: "Beiträge im Jahr", is_union: "Gewerkschaft" },
  },
  training: {
    help: "Kurse, Fachbücher, Prüfungsgebühren, soweit der Arbeitgeber sie nicht erstattet.",
    document: "Rechnung (optional)",
    labels: { label: "Bezeichnung", amount: "Betrag" },
  },
  other_expense: {
    help: "Weitere Werbungskosten, z. B. die Kontoführungspauschale oder Bewerbungskosten.",
    document: "Beleg (optional)",
    labels: {
      expense_class: "Art",
      label: "Bezeichnung",
      amount: "Betrag (bei der Kontoführungspauschale leer lassen)",
    },
  },
  replacement_income: {
    help: "Elterngeld, Mutterschaftsgeld der Krankenkasse, Krankengeld oder Arbeitslosengeld: steuerfrei, erhöht aber den Steuersatz (Progressionsvorbehalt). Den Zuschuss des Arbeitgebers zum Mutterschaftsgeld nicht hier eintragen: er steht auf der Lohnsteuerbescheinigung. Maßgeblich ist der Betrag im Leistungsnachweis oder Bescheid der zahlenden Stelle: weicht er von den gebuchten Zahlungen ab, gilt der Bescheid. Ein vorgeschlagener Betrag ist das, was auf dem Konto eingegangen ist. Beim Krankengeld zeigt die Bescheinigung der Krankenkasse einen höheren Betrag (vor Abzug der Beiträge zur Sozialversicherung): diesen Betrag eintragen.",
    document: "Bescheid (optional)",
    labels: { benefit: "Leistung", amount: "Betrag im Jahr", period_from: "Von (optional)", period_to: "Bis (optional)" },
  },
  other_insurance: {
    help: "Beiträge zu Haftpflicht-, Unfall- und Berufsunfähigkeitsversicherungen im Jahr.",
    labels: { amount: "Beiträge im Jahr" },
  },
  vorsorge_override: {
    help: "Ersetzt die Vorsorgeaufwendungen aus Bescheinigung oder Schätzung ganz, z. B. bei privater Krankenversicherung.",
    labels: {
      pension: "Rentenversicherung (Arbeitnehmeranteil)",
      health_care_basis: "Kranken- und Pflegeversicherung (Basis)",
      other: "Weitere Vorsorge",
      krankengeld_entitlement: "Anspruch auf Krankengeld",
    },
  },
};

const KIND_TEXT_EN: Record<string, KindText> = {
  commute: {
    help: "One way to work for one period. A change of workplace during the year is a second entry.",
    labels: {
      kind: "Kind of workplace",
      workplace_label: "Workplace (town, street)",
      period_from: "From",
      period_to: "To",
      days_per_week: "Working days per week",
      absent_days: "Days of holiday, sickness, home office and business trips",
      days: "Days at the workplace",
      km: "One-way distance (full km)",
      km_car: "of which by car (km)",
      km_collective: "of which by the employer's shuttle (km)",
      km_other: "of which by bus, train, bike or on foot (km)",
      public_transport_costs: "Bus and train costs in the period (optional)",
    },
    hints: { km_other: "The three parts add up to the distance." },
    descriptions: {
      days: "On how many days you actually travelled there.",
      km: "The shortest road connection, one way.",
    },
  },
  home_office: {
    help: "The home office daily flat rate: days on which you worked mainly at home.",
    labels: {
      days_other_workplace: "Days at home although a workplace at the employer was available",
      days_no_other_workplace: "Days at home without another workplace (e.g. teachers)",
    },
  },
  work_room: {
    help: "A home study (Arbeitszimmer) that is the centre of all your professional work. The draft compares the annual flat rate with the share of the actual costs.",
    document: "Lease or floor plan (optional)",
    labels: {
      months: "Months used",
      room_m2: "Room (m²)",
      home_m2: "Home (m²)",
      rent_year: "Annual rent (or costs of ownership)",
      running_costs_year: "Running costs in the year",
      room_only_costs_year: "Costs for the room only (e.g. renovation)",
      prefer: "Choice",
    },
  },
  work_equipment: {
    help: "A piece of work equipment or a subscription, also from earlier years: expensive devices are depreciated over their useful life, and the draft works out this year's share.",
    document: "Invoice (optional)",
    labels: {
      label: "Description",
      item_class: "Kind",
      gross: "Gross price",
      net: "Net price per invoice (optional)",
      bought_on: "Purchase date (earlier years too)",
      professional_share: "Share for work",
      useful_life_months: "Useful life in months (needed only for Other)",
      disposed_on: "No longer used since (optional)",
    },
    hints: {
      bought_on: "A different purchase year: remove the entry and create it again.",
      professional_share: "As a percentage or fraction, e.g. 50 % or 1/2.",
    },
  },
  telecom: {
    help: "Phone and internet costs: without itemised proof, a share of the bills counts up to a cap.",
    labels: { bills_year: "Bills in the year", months: "Months", employer_reimbursed: "Reimbursed by the employer" },
  },
  professional_association: {
    help: "Fees to trade unions and professional associations. From 2026 union fees count on top of the Arbeitnehmer-Pauschbetrag (flat allowance).",
    document: "Fee statement (optional)",
    labels: { name: "Association", amount: "Fees in the year", is_union: "Trade union" },
  },
  training: {
    help: "Courses, specialist books, exam fees, as far as the employer does not reimburse them.",
    document: "Invoice (optional)",
    labels: { label: "Description", amount: "Amount" },
  },
  other_expense: {
    help: "Other Werbungskosten (deductible expenses), e.g. the flat rate for account fees or job application costs.",
    document: "Receipt (optional)",
    labels: {
      expense_class: "Kind",
      label: "Description",
      amount: "Amount (leave empty for the flat rate for account fees)",
    },
  },
  replacement_income: {
    help: "Elterngeld (parental allowance), maternity pay from the health insurer, sickness benefit or unemployment benefit: tax-free, but it raises the tax rate (Progressionsvorbehalt, progression clause). Do not enter the employer's top-up to maternity pay here: it is on the Lohnsteuerbescheinigung (annual wage tax certificate). What counts is the amount in the paying body's statement of benefits or notice: if it differs from the booked payments, the notice applies. A suggested amount is what arrived on the account. For sickness benefit the health insurer's statement shows a higher amount (before social insurance contributions): enter that amount.",
    document: "Notice (optional)",
    labels: { benefit: "Benefit", amount: "Amount in the year", period_from: "From (optional)", period_to: "To (optional)" },
  },
  other_insurance: {
    help: "Premiums for liability, accident and occupational disability insurance in the year.",
    labels: { amount: "Premiums in the year" },
  },
  vorsorge_override: {
    help: "Replaces the provident contributions from the certificate or the estimate entirely, e.g. with private health insurance.",
    labels: {
      pension: "Pension insurance (employee's share)",
      health_care_basis: "Health and long-term care insurance (basic cover)",
      other: "Other provident contributions",
      krankengeld_entitlement: "Entitled to sickness benefit",
    },
  },
};

/** The texts of each kind, read in the chosen language on every access. */
export const kindText: Record<string, KindText> = localized(KIND_TEXT_DE, KIND_TEXT_EN);

/** The Arbeitszimmer's choice on the form ("" is the cheaper of the two, D106). */
export const workRoomChoice: Record<string, string> = localized(
  { "": "das Günstigere (empfohlen)", flat: "Jahrespauschale", actual: "tatsächliche Kosten" },
  { "": "whichever is cheaper (recommended)", flat: "Annual flat rate", actual: "Actual costs" },
);

export const returnInputs = defineMessages({
  de: {
    commuteSummary: (label: string, from: string, to: string, days: string, km: string) =>
      `${label} · ${from} bis ${to} · ${days} Tage, ${km} km`,
    homeOfficeSummary: (withPlace: string, without: string) =>
      `${withPlace} Tage mit Arbeitsplatz beim Arbeitgeber, ${without} Tage ohne`,
    workRoomSummary: (months: string, room: string, home: string, rent: string) =>
      `${months} Monate · ${room} von ${home} · Miete ${rent}`,
    equipmentSummary: (label: string, gross: string, bought: string, share: string) =>
      `${label} · ${gross} · gekauft ${bought} · beruflich ${share}`,
    disposedSince: (date: string) => ` · nicht mehr genutzt seit ${date}`,
    telecomSummary: (bills: string, months: string, reimbursed: string) =>
      `${bills} in ${months} Monaten · erstattet ${reimbursed}`,
    unionSuffix: " · Gewerkschaft",
    flatSuffix: " · Pauschale",
    periodSuffix: (from: string, to: string) => ` · ${from} bis ${to}`,
    vorsorgeSummary: (pension: string, health: string, other: string) =>
      `Rente ${pension} · Kranken/Pflege ${health} · weitere ${other}`,
    notWholeNumber: "Bitte eine ganze Zahl eingeben, z. B. 220.",
    notADate: "Bitte ein Datum wählen.",
    required: "Bitte ausfüllen.",
    pleaseSelect: "Bitte auswählen.",
    inputs: "Eingaben",
    inputsIntro:
      "Jede Angabe wird mit Datum gespeichert; eine Änderung ersetzt sie, die bisherige bleibt im Verlauf. Lohnsteuerbescheinigung und Gehaltsabrechnungen kommen aus ihren Dokumenten (Dokumente › Auslesen).",
    notCurrent: "Die Angabe wurde inzwischen geändert; die Anzeige ist neu geladen.",
    removeFailed: "Das Entfernen hat nicht geklappt. Bitte erneut versuchen.",
    fromYear: (year: number) => ` (aus ${year})`,
    change: "ändern",
    remove: "Entfernen",
    addSingle: (title: string) => `${title} eintragen`,
    addMany: (title: string) => `${title} hinzufügen`,
    formChange: (title: string) => `${title} ändern`,
    formEnter: (title: string) => `${title} eintragen`,
    alreadyRecorded:
      "Für dieses Jahr ist schon ein Eintrag erfasst. Bitte abbrechen; die Anzeige zeigt ihn nach dem Neuladen.",
    changedMeanwhile: "Die Angabe wurde inzwischen geändert. Bitte abbrechen und erneut ändern.",
    pleaseChoose: "Bitte wählen",
    yes: "ja",
    no: "nein",
    kept: "Die bisherige Angabe bleibt im Verlauf.",
    save: "Speichern",
    cancel: "Abbrechen",
  },
  en: {
    commuteSummary: (label: string, from: string, to: string, days: string, km: string) =>
      `${label} · ${from} to ${to} · ${days} days, ${km} km`,
    homeOfficeSummary: (withPlace: string, without: string) =>
      `${withPlace} days with a workplace at the employer, ${without} days without`,
    workRoomSummary: (months: string, room: string, home: string, rent: string) =>
      `${months} months · ${room} of ${home} · rent ${rent}`,
    equipmentSummary: (label: string, gross: string, bought: string, share: string) =>
      `${label} · ${gross} · bought ${bought} · ${share} for work`,
    disposedSince: (date: string) => ` · no longer used since ${date}`,
    telecomSummary: (bills: string, months: string, reimbursed: string) =>
      `${bills} in ${months} months · ${reimbursed} reimbursed`,
    unionSuffix: " · trade union",
    flatSuffix: " · flat rate",
    periodSuffix: (from: string, to: string) => ` · ${from} to ${to}`,
    vorsorgeSummary: (pension: string, health: string, other: string) =>
      `Pension ${pension} · health/care ${health} · other ${other}`,
    notWholeNumber: "Please enter a whole number, e.g. 220.",
    notADate: "Please choose a date.",
    required: "Please fill in.",
    pleaseSelect: "Please choose one.",
    inputs: "Entries",
    inputsIntro:
      "Every entry is saved with its date; a change replaces it and the previous one stays in the history. The Lohnsteuerbescheinigung (annual wage tax certificate) and payslips come from their documents (Documents › Read).",
    notCurrent: "The entry has changed in the meantime; the view has been reloaded.",
    removeFailed: "Removing did not work. Please try again.",
    fromYear: (year: number) => ` (from ${year})`,
    change: "change",
    remove: "Remove",
    addSingle: (title: string) => `Enter ${title}`,
    addMany: (title: string) => `Add ${title}`,
    formChange: (title: string) => `Change ${title}`,
    formEnter: (title: string) => `Enter ${title}`,
    alreadyRecorded: "An entry is already recorded for this year. Please cancel; the view shows it after reloading.",
    changedMeanwhile: "The entry has changed in the meantime. Please cancel and change it again.",
    pleaseChoose: "Please choose",
    yes: "yes",
    no: "no",
    kept: "The previous entry stays in the history.",
    save: "Save",
    cancel: "Cancel",
  },
});
