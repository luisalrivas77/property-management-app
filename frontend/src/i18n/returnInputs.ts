// Steuern › Eure Angaben: the owner's typed facts (components/returnInputs.tsx), a row's evidence
// (components/returnInputEvidence.tsx) and the Werbungskosten block rows of the Anlage N
// (components/anlageNBlocks.tsx). German first, English beside it (glossary.md).
import { defineMessages } from "./core";

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
