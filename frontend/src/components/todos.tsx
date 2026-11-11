"use client";

// What to do about every gap the app knows (phase-3.md D136, 10 Nov 2026): a plain German
// sentence a person without tax knowledge can follow, and an action, either a button that runs
// one existing API command or a link to the exact page, tab or section where the gap is closed.
// One structure keyed by code: WARNING_GUIDE (the draft warnings), REQUIREMENT_GUIDE (the
// document checklist) and REMINDER_GUIDE (the deadline reminders). The rules send codes and
// params only (WARNING_TEXT says what a rule found); these maps say what to do about it. Kept
// apart from WARNING_TEXT so a new warning code is one line there; backend/tests/test_todo_guides.py
// fails until the new code has an entry here too (an action, or `todo: false` for a note).
//
// `todo` is decided per code, not by severity: an info can be a real to-do (the ortsübliche
// Miete, the Hausgeldabrechnung), a warning can be a check with nothing to change (the 15 %
// limit crossed). The "Was noch fehlt" lists (Anlage V tab, Übersicht) show the to-dos only.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type {
  AllocationFact,
  AnlageVDraft,
  ChecklistDepositCandidate,
  ChecklistDepositOffer,
  ChecklistStatus,
  DocumentChecklistItem,
  DraftWarning,
  RequirementCode,
  WarningCode,
} from "@/api/schemas";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { centsToString, toCents } from "./allocations";
import { ReadUpload } from "./documents";
import { formatDate, formatMoney, formatPercent } from "./format";
import { toDecimalString } from "./input";
import { ACQUISITION_COST_KIND, ALLOCATION_CATEGORY, PRICE_SPLIT_SOURCE, proofBill } from "./labels";
import { ActionDialog } from "./ui";
import { useLoad } from "./useLoad";
import { questionnaireHref, taxFormHref } from "./taxYear";

import "./tax.css";

type Params = Record<string, string>;

/** Where a warning is shown, and what the screen already holds to build an exact target. */
export interface GuideContext {
  year: number;
  /** The Anlage V tab's property. */
  propertyId?: string;
  /** The Anlage N tab's owner. */
  ownerId?: string;
  /** The Anlage V draft (unit rows and the reduced-rent checks name the units). */
  draft?: AnlageVDraft;
  /** The draft's cited payments by allocation id (their bank line opens the allocation panel). */
  payments?: Map<string, AllocationFact>;
}

export type GuideAction =
  | { kind: "link"; href: string; label: string }
  /** Upload and read a document (ReadUpload): the reading proposes the missing figures.
   * proofFor (payment_proof.missing, phase 3 D143): the upload is this bill's payment proof.
   * proofOffer (D149): a payment proof already stored, linked to no bill, whose amount fits this
   * bill; „Vorhandenen Nachweis zuordnen“ links it after a confirm. */
  | { kind: "upload"; label: string; proofFor?: string; proofOffer?: { id: string; amount: string | undefined } }
  /** afa.no_split: select a recorded split, or upload the Kaufvertrag. */
  | { kind: "split" }
  /** income.other_review (D140): open the payment, or re-book it as Kaltmiete after a confirm.
   * countedAsRent: a line before the handover the draft already counts as rent. */
  | { kind: "rebook_rent"; href: string; fact: AllocationFact; countedAsRent: boolean }
  /** income.pre_handover: record the D63 input "counts as rent" and book the cited other-income
   * payments as Kaltmiete (D140). */
  | { kind: "pre_handover"; facts: AllocationFact[] }
  /** income.living_area_missing: type the flat's Wohnfläche (PATCH /properties/{id}). */
  | { kind: "living_area" };

/** Was tun, in plain words: what to upload, open or confirm, not the law. The warning is
 * passed too, for a text that depends on what it cites (a payment or a tax input). */
type GuideText = (params: Params, warning?: DraftWarning) => string;
type GuideActionOf = (warning: DraftWarning, ctx: GuideContext) => GuideAction | null;

/** A to-do (listed under "Was noch fehlt") always has a short title and an action; a note or a
 * check may link somewhere but needs nothing. tsc enforces it, test_todo_guides.py that every
 * code has an entry. */
type Guide =
  | { todo: true; title: (params: Params) => string; text: GuideText; action: GuideActionOf }
  | { todo: false; title?: undefined; text: GuideText; action?: GuideActionOf };

// --- targets --------------------------------------------------------------------------------

const link = (href: string, label: string): GuideAction => ({ kind: "link", href, label });

const propertyTab =
  (tab: string, label: string) =>
  (_: DraftWarning, ctx: GuideContext): GuideAction | null =>
    ctx.propertyId ? link(`/properties/${ctx.propertyId}?tab=${tab}`, label) : null;

/** A section of the Anlage V page itself. */
const onPage = (anchor: string, label: string) => () => link(`#${anchor}`, label);

/** The bank line of the warning's first cited payment: its allocation panel holds the inputs
 * (renovation treatment, service period, category). Null until the payments are loaded; the
 * sources button still lists them. */
const payment =
  (label: string) =>
  (warning: DraftWarning, ctx: GuideContext): GuideAction | null => {
    for (const ref of warning.refs ?? []) {
      if (ref.kind !== "allocation" || !ref.id) continue;
      const fact = ctx.payments?.get(ref.id);
      if (fact?.transaction_id) return link(`/inbox?umsatz=${fact.transaction_id}`, label);
    }
    return null;
  };

const inbox = () => link("/inbox", "Zu prüfen öffnen");

/** income.other_review (D140): the payment's bank line plus "Als Miete buchen" for the cited
 * allocation. Null until the payments are loaded (Übersicht then links to Steuern). */
const otherIncome = (warning: DraftWarning, ctx: GuideContext): GuideAction | null => {
  for (const ref of warning.refs ?? []) {
    if (ref.kind !== "allocation" || !ref.id) continue;
    const fact = ctx.payments?.get(ref.id);
    if (fact?.transaction_id) {
      const countedAsRent = warning.params?.counted_as_rent === "1";
      return { kind: "rebook_rent", href: `/inbox?umsatz=${fact.transaction_id}`, fact, countedAsRent };
    }
  }
  return null;
};

/** income.pre_handover (D140): the payments the warning cites, so the dialog names each one. Null
 * until the payments are loaded (Übersicht then links to Steuern). */
const preHandover = (warning: DraftWarning, ctx: GuideContext): GuideAction | null => {
  if (!ctx.propertyId || !ctx.payments) return null;
  const facts: AllocationFact[] = [];
  for (const ref of warning.refs ?? []) {
    const fact = ref.kind === "allocation" && ref.id ? ctx.payments.get(ref.id) : undefined;
    if (fact) facts.push(fact);
  }
  return { kind: "pre_handover", facts };
};

/** The unit page whose "Ortsübliche Miete" section takes the Mietspiegel value: the checked
 * unit of an MFH, else any room of the flat (the flat's value is one for all its rooms). */
function marketRent(label: string) {
  return (_: DraftWarning, ctx: GuideContext): GuideAction | null => {
    const checks = ctx.draft?.reduced_rent?.checks ?? [];
    const unit = checks.find((c) => c.wohnung === "unit")?.id;
    const room = (ctx.draft?.unit_rows ?? []).flatMap((row) => row.rooms ?? [])[0]?.unit_id;
    const target = unit ?? room;
    if (target) return link(`/units/${target}#ortsuebliche-miete`, label);
    return propertyTab("einheiten", label)(_, ctx);
  };
}

/** A section of the Anlage N tab (the screen's owner when it has one). */
function anlageN(anchor: string, label: string) {
  return (_: DraftWarning, ctx: GuideContext): GuideAction =>
    link(`${taxFormHref("n", { year: ctx.year, propertyId: null, ownerId: ctx.ownerId ?? null })}#${anchor}`, label);
}

const owners = (anchor: string, label: string) => () => link(`/owners#${anchor}`, label);
const questions = (_: DraftWarning, ctx: GuideContext) => link(questionnaireHref(ctx.year), "Jahresfragen beantworten");

// --- the map --------------------------------------------------------------------------------

export const WARNING_GUIDE: Partial<Record<WarningCode, Guide>> = {
  // The Anlage V draft (phase-3.md §5)
  year_end_straddle: { todo: false, text: () => "Nichts zu tun: die App zählt die Zahlung im richtigen Jahr." },
  "afa.no_split": {
    todo: true,
    title: () => "Gebäudeanteil am Kaufpreis fehlt",
    text: () =>
      "Abschreiben lässt sich nur das Gebäude, nicht das Grundstück. Dafür braucht die App den Anteil des Gebäudes am Kaufpreis; er steht meist im Kaufvertrag. Ist der Kaufvertrag schon ausgelesen, die Aufteilung von dort übernehmen, sonst den Kaufvertrag hochladen.",
    action: () => ({ kind: "split" }),
  },
  "afa.cost_row_unpaid": {
    todo: false,
    text: () =>
      "Nichts zu tun, wenn Sie diese Kosten von einem anderen Konto bezahlt haben: sie zählen trotzdem. Kam die Zahlung vom Mietkonto, sie in Zu prüfen als Nebenkosten des Kaufs zuordnen.",
    action: propertyTab("nebenkosten", "Nebenkosten ansehen"),
  },
  // Phase 3 D143: a bill the draft counts without a bank line that paid it.
  "payment_proof.missing": {
    todo: true,
    title: (p) => {
      const bill = proofBill(p);
      return `Zahlungsnachweis fehlt: ${bill.charAt(0).toUpperCase()}${bill.slice(1)}`;
    },
    text: (p) =>
      p.proof_offer
        ? `Ein schon hochgeladener Zahlungsnachweis${p.proof_offer_amount ? ` über ${formatMoney(p.proof_offer_amount)}` : ""} ist noch keiner Rechnung zugeordnet und passt zu dieser. Mit „Vorhandenen Nachweis zuordnen“ wird er ihr zugeordnet (vorher wird nachgefragt); wurde die Rechnung laut Nachweis in einem anderen Jahr bezahlt, schlägt die App danach das richtige Jahr vor. Ist es nicht der richtige Nachweis, den passenden Beleg hochladen.`
        : p.proof_rest
        ? `Ein Teil ist nachgewiesen; es fehlt noch der Nachweis über ${formatMoney(p.proof_rest)}. Den Überweisungsbeleg oder die Umsatzdetails dieser Zahlung als PDF hier hochladen; die Rechnung zählt bis dahin trotzdem mit.`
        : p.proof_amount
        ? `Der hochgeladene Nachweis lautet über ${formatMoney(p.proof_amount)}, die Rechnung über ${formatMoney(p.amount)}. Wurde in Teilen bezahlt, auch den Beleg der übrigen Zahlung hochladen; die Rechnung zählt bis dahin trotzdem mit.`
        : "Die Rechnung zählt schon mit; nur der Beleg fehlt, dass sie bezahlt wurde. Im Online-Banking den Überweisungsbeleg oder die Umsatzdetails dieser einen Zahlung als PDF speichern und hier hochladen, nicht den ganzen Kontoauszug. Die App liest Datum und Betrag; wurde die Rechnung in einem anderen Jahr bezahlt, schlägt sie das richtige Jahr vor.",
    action: (warning) =>
      warning.params?.document
        ? {
            kind: "upload",
            label: "Zahlungsnachweis hochladen",
            proofFor: warning.params.document,
            proofOffer: warning.params.proof_offer
              ? { id: warning.params.proof_offer, amount: warning.params.proof_offer_amount }
              : undefined,
          }
        : null,
  },
  "afa.acquisition_cost_without_row": {
    todo: true,
    title: () => "Nebenkosten des Kaufs nicht eingetragen",
    text: () =>
      "Unter Nebenkosten eintragen, wofür diese Zahlung war (zum Beispiel Notar, Grundbuch, Makler oder Grunderwerbsteuer), mit dem Betrag der Rechnung. Dann zählt sie mit.",
    action: propertyTab("nebenkosten", "Unter Nebenkosten eintragen"),
  },
  "afa.purchase_price_mismatch": {
    todo: false,
    text: () =>
      "Den Kaufpreis in den Stammdaten mit dem Kaufvertrag vergleichen. Stimmt er, ist nichts zu tun (oft zahlt die Bank einen Teil direkt an den Verkäufer).",
    action: propertyTab("stammdaten", "Kaufpreis ansehen"),
  },
  "afa.useful_life_report": {
    todo: false,
    text: () =>
      "In der App ist nichts zu tun. Beim Ausfüllen in ELSTER zeigt die Ausfüllhilfe, wo das Kreuz für das Gutachten hingehört; das Gutachten bereithalten, falls das Finanzamt danach fragt.",
  },
  "afa.movable_asset_no_evidence": {
    todo: false,
    text: () =>
      "Eine Schätzung reicht. Haben Sie eine Grundlage für den Wert (Angebot, Rechnung, Ausdruck des Küchenplaners), sie hochladen und unten bei der Einbauküche als Beleg wählen.",
    action: onPage("tax-movable-asset", "Zur Einbauküche"),
  },
  "afa.movable_asset_exceeds_building": {
    todo: true,
    title: () => "Einbauküche größer als das Gebäude",
    text: () =>
      "Der Wert der Einbauküche ist höher als der Gebäudeanteil, das kann nicht stimmen. Unten bei der Einbauküche den Wert prüfen oder die Kaufpreisaufteilung.",
    action: onPage("tax-movable-asset", "Einbauküche prüfen"),
  },
  "limit.exceeded": {
    todo: false,
    text: () => "Nichts zu tun: die App schreibt diese Renovierungskosten schon über die Jahre ab.",
  },
  "limit.prior_year_amendment": {
    todo: true,
    title: (p) => `Erklärung ${p.exceeded_in_year ? `vor ${p.exceeded_in_year}` : "eines früheren Jahres"} ändert sich`,
    text: () =>
      "Ist die Steuererklärung für dieses Jahr schon abgegeben, dem Finanzamt die neuen Zahlen schicken; die Ausfüllhilfe unten zeigt sie. Ist sie noch nicht abgegeben, nichts weiter zu tun.",
    action: onPage("tax-handover-title", "Zur Ausfüllhilfe"),
  },
  "limit.estimated_net": {
    todo: false,
    text: (_p, warning) =>
      (warning?.refs ?? []).some((r) => r.kind === "tax_input")
        ? "Diese Kosten wurden von einem anderen Konto bezahlt. Für eine genaue Rechnung in den Jahresfragen bei diesem Eintrag den Nettobetrag von der Rechnung eintragen; ohne ihn rechnet die App mit 19 % Umsatzsteuer."
        : "Für eine genaue Rechnung die Rechnung zur Zahlung hochladen und verknüpfen; ohne sie rechnet die App mit 19 % Umsatzsteuer.",
    // A cost paid from another account (D137) has no bank line: its net is typed in the Jahresfragen.
    action: (warning, ctx) =>
      payment("Zahlung öffnen")(warning, ctx) ?? ((warning.refs ?? []).some((r) => r.kind === "tax_input") ? questions(warning, ctx) : null),
  },
  "limit.private_cost_undated": {
    todo: true,
    title: (p) => `Wann wurde ${p.label ? `„${p.label}“` : "die Reparatur"} gemacht?`,
    text: () =>
      "Diese Reparatur wurde von einem anderen Konto bezahlt. In den ersten drei Jahren nach dem Kauf zählen Reparaturen nur bis zu einer Grenze sofort als Kosten, darüber werden sie über die Jahre abgeschrieben. Ohne das Datum der Arbeit zählt die App sie vorsichtshalber mit. In den Jahresfragen bei diesem Eintrag das Datum der Arbeit von der Rechnung eintragen, und den Nettobetrag, wenn er dort steht.",
    action: questions,
  },
  "limit.private_cost_after_window": {
    todo: true,
    title: (p) => `Wann wurde ${p.label ? `„${p.label}“` : "die Reparatur"} gemacht?`,
    text: () =>
      "Diese Reparatur wurde von einem anderen Konto bezahlt und ist kurz nach dem Ende der ersten drei Jahre nach dem Kauf datiert, oft mit dem Rechnungsdatum. Wurde die Arbeit noch in diesen drei Jahren gemacht, zählt sie zur 15-%-Grenze. In den Jahresfragen bei diesem Eintrag das Leistungsdatum von der Rechnung eintragen.",
    action: questions,
  },
  "anschaffungsnah.performance_date_check": {
    todo: true,
    title: () => "Wann wurde die Arbeit gemacht?",
    text: () =>
      "Auf der Rechnung nachsehen, wann die Arbeit ausgeführt wurde, und diesen Zeitraum bei der Zahlung eintragen.",
    action: payment("Zahlung öffnen"),
  },
  "renovation.undecided": {
    todo: true,
    title: () => "Renovierung einordnen",
    text: () =>
      "Bei der Zahlung angeben, was gemacht wurde: Wurde Vorhandenes repariert oder erneuert (dann sofort abziehbar), oder kam etwas Neues hinzu, das es vorher nicht gab (dann über die Jahre abgeschrieben)?",
    action: payment("Zahlung öffnen und einordnen"),
  },
  "interest.certificate_mismatch": {
    todo: false,
    text: () =>
      "Meist nichts zu tun: eine Rate um den Jahreswechsel zählt in dem Jahr, für das sie gezahlt wurde. Ist der Unterschied größer als eine Rate, beim Darlehen die Zinsbescheinigung mit den gebuchten Zinsen vergleichen.",
    action: (warning, ctx) => {
      const loan = warning.refs?.find((ref) => ref.kind === "loan" && ref.id);
      return loan ? link(`/loans/${loan.id}`, "Darlehen öffnen") : propertyTab("darlehen", "Darlehen ansehen")(warning, ctx);
    },
  },
  "hausgeld.abrechnung_pending": {
    todo: true,
    title: () => "Hausgeldabrechnung fehlt",
    text: () =>
      "Die Hausgeldabrechnung des Jahres hochladen, sobald die Hausverwaltung sie schickt (meist im Frühjahr danach). Die App liest daraus, was aus der Rücklage ausgegeben wurde, und rechnet es ein.",
    action: () => ({ kind: "upload", label: "Hausgeldabrechnung hochladen" }),
  },
  "income.other_review": {
    todo: true,
    title: (p) =>
      p.counted_as_rent === "1" ? "Als Miete gezählt, aber als Sonstige Einnahme gebucht" : "Einnahme ohne Zuordnung",
    text: (p) =>
      p.counted_as_rent === "1"
        ? "Diese Zahlung zählt in der Steuer schon als Miete. Mit einem Klick auf „Als Miete buchen“ wird sie auch als Kaltmiete gebucht."
        : "War es Miete, zum Beispiel vom Verkäufer weitergeleitete Miete, „Als Miete buchen“ wählen. Sonst die Zahlung öffnen und sagen, was sie war, zum Beispiel Kaution oder eine Erstattung. Dann zählt sie richtig.",
    action: otherIncome,
  },
  "income.pre_handover": {
    todo: true,
    title: () => "Einnahmen vor der Übergabe",
    text: () =>
      "Diese Einnahmen kamen, bevor die Wohnung Ihnen übergeben wurde. Standen Sie in diesen Monaten schon als Vermieter im Mietvertrag, sind sie Ihre Miete: dann „Als Miete zählen“ wählen.",
    action: preHandover,
  },
  "income.living_area_missing": {
    todo: true,
    title: () => "Wohnfläche der Wohnung fehlt",
    text: () =>
      "Die Wohnfläche der ganzen Wohnung fehlt. Entweder die Wohnflächenberechnung hochladen: Hausbuch liest sie und schlägt die Fläche Ihrer Wohnung zum Übernehmen vor. Oder die Fläche hier eintragen; sie steht im Kaufvertrag oder in der Wohnflächenberechnung. Die Flächen der Zimmer reichen nicht, weil Küche, Bad und Flur fehlen.",
    action: () => ({ kind: "living_area" }),
  },
  "umlagen.mixed_terms": {
    todo: false,
    text: () =>
      "Bei jedem Mietverhältnis prüfen, wie die Nebenkosten vereinbart sind (Vorauszahlung, Pauschale oder in der Miete enthalten). Stimmt das, ist nichts zu tun.",
    action: (warning) => {
      const first = warning.params?.tenancies?.split(",")[0]?.trim();
      return first ? link(`/tenancies/${first}`, "Mietverhältnis öffnen") : null;
    },
  },
  "reduced_rent.no_market_rent": {
    todo: true,
    title: () => "Ortsübliche Miete fehlt",
    text: () =>
      "Im Mietspiegel der Stadt nachsehen, wie viel Kaltmiete pro m² für eine solche Wohnung üblich ist, und den Wert bei der Einheit eintragen. Dann prüft die App, ob Ihre Miete hoch genug ist, damit alle Kosten zählen.",
    action: marketRent("Mietspiegel-Wert eintragen"),
  },
  "reduced_rent.prognosis_needed": {
    todo: false,
    text: () =>
      "In der App ist nichts zu tun. Erst den Mietspiegel-Wert bei der Einheit prüfen. Stimmt er und fragt das Finanzamt nach einer Prognose, zeigt die Planung die Zahlen der nächsten Jahre.",
    action: marketRent("Mietspiegel-Wert prüfen"),
  },
  "reduced_rent.partial": {
    todo: true,
    title: () => "Miete unter der Hälfte der üblichen",
    text: () =>
      "Erst den Mietspiegel-Wert bei der Einheit prüfen. Stimmt er, kürzt das Finanzamt die Kosten; die Ausfüllhilfe nennt den Prozentsatz, der in ELSTER einzutragen ist.",
    action: marketRent("Mietspiegel-Wert prüfen"),
  },
  "draft.open_lines_excluded": {
    todo: true,
    title: (p) => `${p.count ?? "Einige"} ${p.count === "1" ? "Zahlung" : "Zahlungen"} noch nicht bestätigt`,
    text: () => "In „Zu prüfen“ die offenen Zahlungen bestätigen; danach stehen sie im Entwurf.",
    action: inbox,
  },
  "draft.form_provisional": {
    todo: false,
    text: () => "Nichts zu tun: sobald das Formular erscheint, passt die App die Zeilen an.",
  },
  "draft.allocation_without_property": {
    todo: true,
    title: () => "Zahlung ohne Immobilie",
    text: () => "In „Zu prüfen“ die Zahlung öffnen und der richtigen Immobilie zuordnen.",
    action: inbox,
  },
  "archive.finanzamt_copy_needed": {
    todo: false,
    text: () =>
      "Nur nötig, wenn das Finanzamt Belege anfordert: das Dokument in Adobe Reader öffnen, als PDF drucken und diese Kopie schicken.",
  },
  "tax.travel_frequency": {
    todo: false,
    text: () =>
      "Die Zahl der Fahrten prüfen. Waren Sie fast täglich dort, gilt nur die halbe Pauschale; sonst ist nichts zu tun.",
    action: questions,
  },
  "tax.private_cost_paid_from_account": {
    todo: true,
    title: () => "Rechnung doppelt eingetragen",
    text: () =>
      "Die Rechnung wurde vom Mietkonto bezahlt und zählt schon mit dieser Buchung. In den Jahresfragen den Eintrag bei den von einem anderen Konto bezahlten Kosten entfernen.",
    action: questions,
  },
  // Anlage N (phase-4b.md §9)
  "anlage_n.certificate_missing": {
    todo: true,
    title: () => "Lohnsteuerbescheinigung fehlt",
    text: () =>
      "Die Lohnsteuerbescheinigung hochladen, sobald der Arbeitgeber sie schickt (meist bis Ende Februar). Die App liest sie aus; ihre Zahlen ersetzen dann die Schätzung.",
    action: () => ({ kind: "upload", label: "Lohnsteuerbescheinigung hochladen" }),
  },
  "anlage_n.church_mismatch": {
    todo: true,
    title: () => "Kirchensteuer passt nicht",
    text: () =>
      "Auf der Seite Eigentümer prüfen, ob die Kirchensteuer richtig angegeben ist. Ob Kirchensteuer abgezogen wurde, steht auf der Lohnsteuerbescheinigung.",
    action: () => link("/owners", "Eigentümer öffnen"),
  },
  "anlage_n.tax_class_6": {
    todo: false,
    text: () => "Nichts zu tun: die App trägt den zweiten Job in seine eigene Spalte ein.",
  },
  "anlage_n.periods_overlap": {
    todo: true,
    title: () => "Bescheinigung doppelt?",
    text: () =>
      "Prüfen, ob dieselbe Bescheinigung zweimal erfasst ist oder eine korrigierte die alte ersetzt. Die überzählige unter Unterlagen öffnen und zurückziehen.",
    action: anlageN("tax-n-documents", "Unterlagen ansehen"),
  },
  "anlage_n.days_implausible": {
    todo: true,
    title: () => "Arbeitstage passen nicht",
    text: () =>
      "Unter Eingaben die Homeoffice-Tage und die Tage im Büro prüfen: zusammen höchstens so viele, wie Sie im Jahr gearbeitet haben.",
    action: anlageN("eingabe-home_office", "Homeoffice-Tage prüfen"),
  },
  "anlage_n.home_office_capped": {
    todo: false,
    text: () => "Nichts zu tun: mehr erkennt das Finanzamt im Jahr nicht an.",
  },
  "anlage_n.too_many_routes": {
    todo: false,
    text: () => "In ELSTER die übrigen Wege von Hand ergänzen; die Ausfüllhilfe listet alle.",
  },
  "anlage_n.receipt_missing": {
    todo: false,
    text: () =>
      "Nur nötig, wenn das Finanzamt fragt: die Rechnung aufbewahren oder bei der Ausgabe unter Eingaben als Beleg hinterlegen.",
    action: anlageN("tax-inputs-title", "Eingaben ansehen"),
  },
  "anlage_n.form_provisional": {
    todo: false,
    text: () => "Nichts zu tun: sobald das Formular erscheint, passt die App die Zeilen an.",
  },
  "anlage_n.payslip_gap": {
    todo: true,
    title: () => "Gehaltsabrechnung fehlt",
    text: () => "Die fehlende Gehaltsabrechnung hochladen; die App liest sie aus.",
    action: () => ({ kind: "upload", label: "Gehaltsabrechnung hochladen" }),
  },
  "anlage_n.payslip_no_ytd": {
    todo: false,
    text: () => "Nichts zu tun; genauer wird es mit der nächsten Abrechnung oder der Lohnsteuerbescheinigung.",
  },
  "anlage_n.work_room_records": {
    todo: false,
    text: () => "Grundriss und Rechnungen aufbewahren; hochladen müssen Sie nichts.",
  },
  "anlage_n.work_room_centre": {
    todo: false,
    text: () =>
      "Prüfen: arbeiten Sie fast nur in diesem Zimmer? Wenn nicht, das Arbeitszimmer unter Eingaben entfernen; dann zählen stattdessen die Homeoffice-Tage.",
    action: anlageN("eingabe-work_room", "Arbeitszimmer prüfen"),
  },
  "anlage_n.work_room_partial": {
    todo: false,
    text: () => "Bei den Homeoffice-Tagen nur Tage aus den Monaten ohne Arbeitszimmer eintragen.",
    action: anlageN("eingabe-home_office", "Homeoffice-Tage prüfen"),
  },
  "anlage_n.first_workplace_check": {
    todo: false,
    text: () =>
      "Den Arbeitgeber fragen, welchen Arbeitsort er als ersten festgelegt hat, und den Weg dorthin unter Eingaben eintragen.",
    action: anlageN("eingabe-commute", "Arbeitsweg eintragen"),
  },
  // The whole return (phase-4b.md §9)
  "return.progression_provisional": {
    todo: true,
    title: () => "Bescheid über Lohnersatzleistung fehlt",
    text: () =>
      "Sobald der Bescheid über das Elterngeld, Krankengeld oder Arbeitslosengeld da ist, den Betrag unter Eingaben bei den Lohnersatzleistungen eintragen.",
    action: anlageN("eingabe-replacement_income", "Lohnersatzleistung eintragen"),
  },
  "return.benefit_possible_double": {
    todo: true,
    title: () => "Lohnersatzleistung doppelt",
    text: () =>
      "Die Leistung steht schon auf der Lohnsteuerbescheinigung. Die eigene Angabe unter Eingaben bei den Lohnersatzleistungen entfernen.",
    action: anlageN("eingabe-replacement_income", "Lohnersatzleistungen ansehen"),
  },
  "return.children_mismatch": {
    todo: false,
    text: () => "Auf der Seite Eigentümer unter Kinder prüfen, ob alle Kinder erfasst sind. Stimmt es, ist nichts zu tun.",
    action: owners("kinder", "Kinder ansehen"),
  },
  "return.kindergeld_apply": {
    todo: true,
    title: () => "Kindergeld beantragen",
    text: () =>
      "Kindergeld bei der Familienkasse beantragen und danach beim Kind eintragen, ab wann es gezahlt wird.",
    action: owners("kinder", "Kinder ansehen"),
  },
  "return.child_over_18": {
    todo: false,
    text: () =>
      "Ist das Kind in Ausbildung oder Studium, das in ELSTER in der Anlage Kind angeben; die App rechnet das nicht mit. Sonst ist nichts zu tun.",
  },
  "return.childcare_until_14": {
    todo: false,
    text: () => "Bei den Kinderbetreuungskosten nur die Kosten bis zum 14. Geburtstag eintragen.",
    action: owners("haushalt", "Kinderbetreuung ansehen"),
  },
  "return.donation_over_cap": {
    todo: false,
    text: () => "Nichts zu tun: die App rechnet nur den erlaubten Teil.",
  },
  "return.filing_required": {
    todo: false,
    text: () => "Die Steuererklärung bis zur Frist abgeben; die Frist steht in der Übersicht oben.",
  },
  "return.questionnaire_open": {
    todo: true,
    title: () => "Jahresfragen offen",
    text: () => "Die offenen Jahresfragen beantworten; sie dauern meist nur ein paar Minuten.",
    action: questions,
  },
  "return.not_modelled": {
    todo: false,
    text: () => "Diese Teile in ELSTER von Hand ergänzen; die Schätzung kennt sie nicht.",
  },
  questionnaire_provisional: {
    todo: false,
    text: () => "Nichts zu tun: sobald die Fragen des Jahres feststehen, übernimmt die App sie.",
  },
  "tax_export.questions_open": {
    todo: true,
    title: () => "Jahresfragen offen",
    text: () => "Die offenen Jahresfragen beantworten und den Export danach neu erstellen.",
    action: questions,
  },
};

export function guideFor(code: WarningCode): Guide | undefined {
  return WARNING_GUIDE[code];
}

export function isTodo(warning: DraftWarning): boolean {
  return guideFor(warning.code)?.todo ?? false;
}

export function todoTitle(warning: DraftWarning): string | null {
  return guideFor(warning.code)?.title?.(warning.params ?? {}) ?? null;
}

export function resolveAction(warning: DraftWarning, ctx: GuideContext): GuideAction | null {
  return guideFor(warning.code)?.action?.(warning, ctx) ?? null;
}

// --- rendering ------------------------------------------------------------------------------

/** "Was tun: …" under a warning's text. Nothing for a code the map does not know. */
export function WhatToDo({ warning }: { warning: DraftWarning }) {
  const guide = guideFor(warning.code);
  if (!guide) return null;
  return (
    <p className="todo-text">
      <strong>Was tun:</strong> {guide.text(warning.params ?? {}, warning)}
    </p>
  );
}

/** The action of a warning: a link, an upload, or one of the direct buttons. `onChanged`
 * reloads what the button changed (the draft and the checklist). */
export function WarningActionView({
  warning,
  ctx,
  onChanged,
}: {
  warning: DraftWarning;
  ctx: GuideContext;
  onChanged?: () => void;
}) {
  const action = resolveAction(warning, ctx);
  if (!action) return null;
  switch (action.kind) {
    case "link":
      return <Link href={action.href}>{action.label}</Link>;
    case "upload":
      return (
        <>
          {action.proofOffer && action.proofFor ? (
            <AttachProofAction proof={action.proofOffer} billId={action.proofFor} onChanged={onChanged} />
          ) : null}
          <ReadUpload label={action.label} proofFor={action.proofFor} onUploaded={onChanged} />
        </>
      );
    case "split":
      return ctx.propertyId ? <SplitAction propertyId={ctx.propertyId} onChanged={onChanged} /> : null;
    case "rebook_rent":
      return (
        <>
          <Link href={action.href}>Zahlung öffnen</Link>
          <RebookAsRentAction fact={action.fact} countedAsRent={action.countedAsRent} onChanged={onChanged} />
        </>
      );
    case "pre_handover":
      return ctx.propertyId ? (
        <PreHandoverAction propertyId={ctx.propertyId} facts={action.facts} onChanged={onChanged} />
      ) : null;
    case "living_area":
      return ctx.propertyId ? <LivingAreaAction propertyId={ctx.propertyId} onChanged={onChanged} /> : null;
  }
}

/** payment_proof.missing with an offer (phase 3 D149): a payment proof already stored and
 * linked to no bill fits this bill's amount. „Vorhandenen Nachweis zuordnen“ asks first, then
 * links it through POST /documents/{id}/payment-proof, as the to-do's upload links a new one
 * (to the bill and its property). When the proof's reading shows a privately paid bill was
 * paid in another year, the server then proposes moving it to that year (review F1), as a
 * suggestion on the proof's page, which opens; nothing is moved before the user confirms it. */
function AttachProofAction({
  proof,
  billId,
  onChanged,
}: {
  proof: { id: string; amount: string | undefined };
  billId: string;
  onChanged?: () => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function confirm(close: () => void) {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/documents/{document_id}/payment-proof", {
          params: { path: { document_id: proof.id } },
          body: { bill_id: billId },
        }),
      );
      // A suggestion to move the bill to the year paid waits on the proof's page: open it.
      const list = await ok(api.GET("/api/documents/{document_id}/proposals", { params: { path: { document_id: proof.id } } }));
      close();
      if (list[0]?.status === "ready") {
        router.push(`/documents/${proof.id}`);
        return;
      }
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ActionDialog label="Vorhandenen Nachweis zuordnen" title="Vorhandenen Zahlungsnachweis zuordnen" buttonClass="">
      {(close) => (
        <>
          <p>
            Der schon hochgeladene Zahlungsnachweis{proof.amount ? ` über ${formatMoney(proof.amount)}` : ""} wird dieser Rechnung zugeordnet und
            gilt dann als Beleg, dass sie bezahlt wurde. Wurde sie laut Nachweis in einem anderen Jahr bezahlt, öffnet sich danach der
            Nachweis mit dem Vorschlag, sie in diesem Jahr zu zählen. <Link href={`/documents/${proof.id}`}>Nachweis ansehen</Link>
          </p>
          <ErrorNotice error={error} />
          <div className="row">
            <button type="button" disabled={busy} onClick={() => void confirm(close)}>
              {busy ? "Speichert …" : "Ja, zuordnen"}
            </button>
            <button type="button" className="secondary" onClick={close}>
              Abbrechen
            </button>
          </div>
        </>
      )}
    </ActionDialog>
  );
}

/** afa.no_split. One Kaufvertrag split recorded (or one split of any source): a button that
 * selects it through the existing select command. Several candidates: the Kaufpreisaufteilung
 * tab, never a guess. None: upload the Kaufvertrag, whose reading records the split and selects
 * it (phase 2 D133), or type it on the tab. Since D133 a split is selected when recorded, so a
 * recorded but unselected split exists only from before 10 Nov 2026. */
function SplitAction({ propertyId, onChanged }: { propertyId: string; onChanged?: () => void }) {
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/price-splits", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const splits = useLoad(load);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const tab = `/properties/${propertyId}?tab=aufteilung`;
  if (splits.data === undefined) return splits.error != null ? <Link href={tab}>Kaufpreisaufteilung öffnen</Link> : null;

  const list = splits.data;
  const contract = list.filter((split) => split.source === "purchase_contract");
  const only = contract.length === 1 ? contract[0] : list.length === 1 ? list[0] : undefined;

  async function select(splitId: string) {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/properties/{property_id}/price-splits/{split_id}/select", {
          params: { path: { property_id: propertyId, split_id: splitId } },
        }),
      );
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  if (list.length === 0) {
    return (
      <>
        <ReadUpload label="Kaufvertrag hochladen" onUploaded={onChanged} />
        <Link href={tab}>Aufteilung selbst eintragen</Link>
      </>
    );
  }
  if (!only) return <Link href={tab}>Eine der {list.length} Aufteilungen wählen</Link>;
  return (
    <>
      <button type="button" disabled={busy} onClick={() => void select(only.id)}>
        {busy
          ? "Wird übernommen …"
          : `Gebäudeanteil ${formatPercent(only.building_share)} aus ${only.source === "purchase_contract" ? "dem Kaufvertrag" : `der Quelle „${PRICE_SPLIT_SOURCE[only.source]}“`} übernehmen`}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

type AllocationDraft = components["schemas"]["AllocationDraft"];

/** What a re-booking did: booked; nothing to change (the cited rows are booked otherwise
 * already); or gone (a cited row is no longer current, so the line changed since it loaded). */
type RebookResult = "booked" | "unchanged" | "gone";
type AllocationRow = components["schemas"]["Allocation"];

/** A current row as the answer command takes it back, every field as it is. */
function draftOf(row: AllocationRow): AllocationDraft {
  return {
    amount: row.amount,
    category: row.category,
    property_id: row.property_id,
    unit_id: row.unit_id,
    tenancy_id: row.tenancy_id,
    loan_id: row.loan_id,
    service_period_start: row.service_period_start,
    service_period_end: row.service_period_end,
    recurring: row.recurring,
    document_id: row.document_id,
  };
}

/** Re-book cited allocations of one bank line through the existing answer command (PUT
 * /transactions/{id}/allocations, the inbox's path): read the line's current rows, let
 * `rewrite` replace each cited row (null = keep it, "gone" = it is no longer what the to-do
 * cited), send every other row back with its fields as they are (property, unit, tenancy,
 * period, recurring flag, Beleg; the other parts of a split), so the sum still holds. The
 * server supersedes the old rows and records transaction.reallocated. No rule is learned: a
 * later payment from the same payer is asked again. A cited row that is no longer current is
 * never guessed by amount. Shared by D140 (as rent) and D142 (a deposit to its tenancies). */
async function rewriteLine(
  transactionId: string,
  allocationIds: readonly string[],
  rewrite: (row: AllocationRow) => AllocationDraft[] | null | "gone",
): Promise<RebookResult> {
  const view = await ok(
    api.GET("/api/transactions/{transaction_id}/allocations", {
      params: { path: { transaction_id: transactionId } },
    }),
  );
  const rows = view.allocations;
  const current = new Set(rows.map((row) => row.id));
  if (allocationIds.some((id) => !current.has(id))) return "gone";
  const cited = new Set(allocationIds);
  const drafts: AllocationDraft[] = [];
  let changed = false;
  for (const row of rows) {
    const next = cited.has(row.id) ? rewrite(row) : null;
    if (next === "gone") return "gone";
    if (next === null) {
      drafts.push(draftOf(row));
    } else {
      changed = true;
      drafts.push(...next);
    }
  }
  if (!changed) return "unchanged";
  await ok(
    api.PUT("/api/transactions/{transaction_id}/allocations", {
      params: { path: { transaction_id: transactionId } },
      // The server reads only the rows: method and confidence become the person's.
      body: {
        allocation_set: { transaction_id: transactionId, allocations: drafts, method: "user", confidence: "1" },
        learn_rule: false,
        same_amount_only: false,
      },
    }),
  );
  return "booked";
}

/** Book the cited other-income allocations of one bank line as Kaltmiete (D140): only the
 * category of a cited other_income row changes. */
function rebookAsRent(transactionId: string, allocationIds: readonly string[]): Promise<RebookResult> {
  return rewriteLine(transactionId, allocationIds, (row) =>
    row.category === "other_income" ? [{ ...draftOf(row), category: "rent_cold" }] : null,
  );
}

/** A deposit line split to its tenancies (D142): the parts to link and what stays unlinked. */
interface DepositSplit {
  offer: ChecklistDepositOffer;
  parts: { candidate: ChecklistDepositCandidate; cents: bigint }[];
  rest: bigint;
}

/** The split "Kaution aus Bankumsatz zuordnen" proposes for a missing deposit item (D142), or
 * null (no button; the tenancy link stays). For each offered line, oldest first, where this
 * tenancy is a candidate: when every candidate's deposit is known and they sum exactly to the
 * line, all of them in one click; else this tenancy alone, its deposit taken out of the line
 * and the rest left unlinked, if the line covers it. Never anything else. */
export function depositSplit(item: DocumentChecklistItem): DepositSplit | null {
  const deposit = (candidate: ChecklistDepositCandidate) =>
    candidate.deposit_amount ? toCents(candidate.deposit_amount) : null;
  for (const offer of item.deposit_offers ?? []) {
    const line = toCents(offer.amount);
    const candidates = offer.candidates ?? [];
    const own = candidates.find((candidate) => candidate.tenancy_id === item.scope.id);
    if (line === null || line <= 0n || !offer.transaction_id || !own) continue;
    const amounts = candidates.map(deposit);
    if (amounts.every((cents) => cents !== null && cents > 0n)) {
      const known = amounts as bigint[];
      if (known.reduce((sum, cents) => sum + cents, 0n) === line) {
        return { offer, parts: candidates.map((candidate, i) => ({ candidate, cents: known[i] ?? 0n })), rest: 0n };
      }
    }
    const mine = deposit(own);
    if (mine !== null && mine > 0n && mine <= line) {
      return { offer, parts: [{ candidate: own, cents: mine }], rest: line - mine };
    }
  }
  return null;
}

/** Link the offered deposit line to its tenancies (D142): the cited row, still an unlinked
 * deposit_in of the offered amount, becomes one row per tenancy (its deposit, its tenancy and
 * unit) plus the unlinked rest, each keeping the property, period, recurring flag and Beleg of
 * the original; every other row of the line (a rent part, say) is sent back unchanged. */
function assignDeposit(split: DepositSplit): Promise<RebookResult> {
  const { offer, parts, rest } = split;
  return rewriteLine(offer.transaction_id as string, [offer.allocation_id], (row) => {
    if (row.category !== "deposit_in" || row.tenancy_id || toCents(row.amount) !== toCents(offer.amount)) {
      return "gone";
    }
    const base = draftOf(row);
    const rows: AllocationDraft[] = parts.map(({ candidate, cents }) => ({
      ...base,
      amount: centsToString(cents),
      unit_id: candidate.unit_id,
      tenancy_id: candidate.tenancy_id,
    }));
    if (rest > 0n) rows.push({ ...base, amount: centsToString(rest) });
    return rows;
  });
}

/** "WE 1, ab 01.01.2023": a tenancy as the to-do titles name it (scopeSuffix), from its own
 * deposit item in the same checklist; never a tenant's name (D5). */
function tenancyLabel(tenancyId: string, items: readonly DocumentChecklistItem[] | undefined): string {
  const item = items?.find((i) => i.code === "deposit_record" && i.scope.id === tenancyId);
  if (!item) return "Mietverhältnis";
  return `${item.scope.unit_label ?? "Einheit"}, ab ${formatDate(item.scope.tenancy_start)}`;
}

/** deposit_record (D142): „Kaution aus Bankumsatz zuordnen“, a dialog naming the bank line and
 * the tenancies it is split to, and one „Übernehmen“. */
function DepositSplitAction({
  split,
  items,
  onChanged,
}: {
  split: DepositSplit;
  items?: readonly DocumentChecklistItem[];
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [gone, setGone] = useState(false);
  const { offer, parts, rest } = split;
  const payment = `Zahlung vom ${formatDate(offer.booking_date)} (${ALLOCATION_CATEGORY.deposit_in}, ${formatMoney(offer.amount)})`;
  const first = parts[0]?.cents ?? 0n;
  const each = parts.every((part) => part.cents === first) ? centsToString(first) : null;
  const question =
    parts.length > 1
      ? `Die ${payment} auf ${parts.length} Mietverhältnisse aufteilen${each ? `: je ${formatMoney(each)}` : ""}?`
      : rest > 0n
        ? `Von der ${payment} ${formatMoney(centsToString(first))} diesem Mietverhältnis zuordnen? Der Rest von ${formatMoney(centsToString(rest))} bleibt als Kaution ohne Mietverhältnis stehen.`
        : `Die ${payment} diesem Mietverhältnis zuordnen?`;
  async function confirm(close: () => void) {
    setBusy(true);
    setError(null);
    setGone(false);
    try {
      if ((await assignDeposit(split)) === "gone") {
        setGone(true);
        return;
      }
      close();
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ActionDialog label="Kaution aus Bankumsatz zuordnen" title="Kaution zuordnen" buttonClass="">
      {(close) => (
        <>
          <p>{question}</p>
          <ul>
            {parts.map(({ candidate, cents }) => (
              <li key={candidate.tenancy_id}>
                {tenancyLabel(candidate.tenancy_id, items)} · {formatMoney(centsToString(cents))}
              </li>
            ))}
          </ul>
          <p>
            Die Zahlung gilt dann als Nachweis der Kaution. Die Änderung wird mit Datum gespeichert und lässt sich bei der
            Zahlung wieder ändern.
          </p>
          {gone ? (
            <p className="field-errors" role="alert">
              Diese Zahlung wurde inzwischen anders zugeordnet. Bitte die Zahlung öffnen und dort prüfen.
            </p>
          ) : null}
          <ErrorNotice error={error} />
          <div className="row">
            <button type="button" disabled={busy || gone} onClick={() => void confirm(close)}>
              {busy ? "Speichert …" : "Übernehmen"}
            </button>
            <button type="button" className="secondary" onClick={close}>
              Abbrechen
            </button>
          </div>
        </>
      )}
    </ActionDialog>
  );
}

/** The action of a checklist row (the "Was noch fehlt" lists and Unterlagen), or null: for a
 * missing deposit with an offered bank line, „Kaution aus Bankumsatz zuordnen“ first (D142);
 * the row's fix link beside it. */
export function checklistAction(item: DocumentChecklistItem, ctx: ChecklistContext): ReactNode {
  const fix = requirementFix(item, ctx);
  const split = item.status === "missing" && item.code === "deposit_record" ? depositSplit(item) : null;
  const link = fix ? <Link href={fix.href}>{fix.label}</Link> : null;
  if (!split) return link;
  return (
    <>
      <DepositSplitAction split={split} items={ctx.items} onChanged={ctx.onChanged} />
      {link}
    </>
  );
}

/** income.other_review (D140): book the cited allocation as Kaltmiete (rebookAsRent), after the
 * user confirms the sentence naming its date and amount. */
function RebookAsRentAction({
  fact,
  countedAsRent,
  onChanged,
}: {
  fact: AllocationFact;
  countedAsRent: boolean;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [gone, setGone] = useState(false);
  const transactionId = fact.transaction_id;
  if (!transactionId) return null;
  async function confirm(close: () => void) {
    if (!transactionId) return;
    setBusy(true);
    setError(null);
    setGone(false);
    try {
      if ((await rebookAsRent(transactionId, [fact.allocation_id])) === "gone") {
        setGone(true);
        return;
      }
      close();
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ActionDialog label="Als Miete buchen" title="Einnahme als Miete buchen" buttonClass="">
      {(close) => (
        <>
          <p>
            {countedAsRent
              ? `Die Zahlung vom ${formatDate(fact.booking_date)} über ${formatMoney(fact.amount)} zählt in der Steuer schon als Miete und wird jetzt auch als Kaltmiete gebucht. Die Steuerzahlen ändern sich dadurch nicht.`
              : `Die Zahlung vom ${formatDate(fact.booking_date)} über ${formatMoney(fact.amount)} wird als Kaltmiete gebucht und zählt dann in der Zeile der Miete statt bei den sonstigen Einnahmen.`}{" "}
            Die Änderung wird mit Datum gespeichert und lässt sich bei der Zahlung wieder ändern.
          </p>
          {gone ? (
            <p className="field-errors" role="alert">
              Diese Zahlung wurde inzwischen anders zugeordnet. Bitte die Zahlung öffnen und dort prüfen.
            </p>
          ) : null}
          <ErrorNotice error={error} />
          <div className="row">
            <button type="button" disabled={busy || gone} onClick={() => void confirm(close)}>
              {busy ? "Speichert …" : "Ja, als Miete buchen"}
            </button>
            <button type="button" className="secondary" onClick={close}>
              Abbrechen
            </button>
          </div>
        </>
      )}
    </ActionDialog>
  );
}

/** income.pre_handover: the D63 input "these lines count as rent" (the one reading decided),
 * recorded only after the user confirms the dialog, which lists every payment it covers (date,
 * amount, masked IBAN). Then each cited other-income payment is booked as Kaltmiete too
 * (rebookAsRent, one request per bank line; D140). The decision stays recorded when a
 * re-booking fails: the dialog says so, and each such payment keeps its own to-do with
 * "Als Miete buchen". */
function PreHandoverAction({
  propertyId,
  facts,
  onChanged,
}: {
  propertyId: string;
  facts: AllocationFact[];
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notBooked, setNotBooked] = useState<number | null>(null);
  const other = facts.filter((fact) => fact.category === "other_income" && fact.transaction_id);
  async function confirm(close: () => void) {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/properties/{property_id}/tax-inputs", {
          params: { path: { property_id: propertyId } },
          // The warning shows only while no current row answers it, so this is a first answer
          // (a withdrawn row is not current; the server refuses a second one, tax_input.not_current).
          body: {
            property_id: propertyId,
            kind: "pre_handover_income",
            value: { treatment: "rent" },
            supersedes_id: null,
            withdrawn: false,
          },
        }),
      );
    } catch (caught) {
      setError(caught);
      setBusy(false);
      return;
    }
    const byLine = new Map<string, string[]>();
    for (const fact of other) {
      const line = fact.transaction_id as string;
      byLine.set(line, [...(byLine.get(line) ?? []), fact.allocation_id]);
    }
    let failed = 0;
    for (const [line, ids] of byLine) {
      try {
        if ((await rebookAsRent(line, ids)) === "gone") failed += ids.length;
      } catch (caught) {
        failed += ids.length;
        setError(caught);
      }
    }
    setBusy(false);
    if (failed > 0) {
      setNotBooked(failed);
      return;
    }
    close();
    onChanged?.();
  }
  return (
    <ActionDialog label="Als Miete zählen" title="Einnahmen vor der Übergabe" buttonClass="">
      {(close) => (
        <>
          <p>
            Diese Einnahmen kamen, bevor die Wohnung Ihnen übergeben wurde. Wenn Sie in diesen Monaten schon als Vermieter
            im Mietvertrag standen, sind sie Ihre Mieteinnahmen und kommen in die Zeile der Miete. Die Entscheidung wird mit
            Datum gespeichert und lässt sich später ändern.
          </p>
          {facts.length > 0 ? (
            <>
              <p>Das betrifft diese Zahlungen:</p>
              <ul>
                {facts.map((fact) => (
                  <li key={fact.allocation_id}>
                    {formatDate(fact.booking_date)} · {formatMoney(fact.amount)}
                    {fact.counterparty_iban_masked ? ` · von ${fact.counterparty_iban_masked}` : ""}
                    {fact.category === "other_income"
                      ? " · wird als Kaltmiete gebucht"
                      : ` · gebucht als ${ALLOCATION_CATEGORY[fact.category]}`}
                  </li>
                ))}
              </ul>
              <p>Ist eine davon keine Miete, zum Beispiel eine Gutschrift der Bank, erst bei der Zahlung richtig zuordnen.</p>
            </>
          ) : null}
          {notBooked !== null ? (
            <p className="field-errors" role="alert">
              Die Entscheidung ist gespeichert. {notBooked === 1 ? "Eine Zahlung konnte" : `${notBooked} Zahlungen konnten`}{" "}
              nicht als Kaltmiete gebucht werden; sie stehen unter „Was noch fehlt“ mit „Als Miete buchen“.
            </p>
          ) : null}
          <ErrorNotice error={error} />
          <div className="row">
            {notBooked !== null ? (
              <button
                type="button"
                onClick={() => {
                  close();
                  onChanged?.();
                }}
              >
                Schließen
              </button>
            ) : (
              <>
                <button type="button" disabled={busy} onClick={() => void confirm(close)}>
                  {busy ? "Speichert …" : "Ja, als Miete zählen"}
                </button>
                <button type="button" className="secondary" onClick={close}>
                  Abbrechen
                </button>
              </>
            )}
          </div>
        </>
      )}
    </ActionDialog>
  );
}

/** income.living_area_missing: the flat's Wohnfläche through PATCH /properties/{id} (D71), or
 * the Wohnflächenberechnung uploaded and read, whose suggestion proposes it (D139). */
function LivingAreaAction({ propertyId, onChanged }: { propertyId: string; onChanged?: () => void }) {
  const id = useId();
  const [raw, setRaw] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    const parsed = toDecimalString(raw, 2);
    if (!parsed.ok) {
      setMessage(parsed.message);
      return;
    }
    setMessage(null);
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.PATCH("/api/properties/{property_id}", {
          params: { path: { property_id: propertyId } },
          body: { living_area_m2: parsed.value },
        }),
      );
      setRaw("");
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <ReadUpload label="Wohnflächenberechnung hochladen" onUploaded={onChanged} />
      <form
        className="row todo-inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label htmlFor={id} className="inline">
          Wohnfläche (m²)
          <input
            id={id}
            inputMode="decimal"
            value={raw}
            onChange={(event) => setRaw(event.target.value)}
            aria-invalid={message ? true : undefined}
          />
        </label>
        <button type="submit" disabled={busy || raw.trim() === ""}>
          {busy ? "Speichert …" : "Wohnfläche speichern"}
        </button>
        {message ? (
          <span className="field-errors" role="alert">
            {message}
          </span>
        ) : null}
        <ErrorNotice error={error} />
      </form>
    </>
  );
}

// --- the document checklist (rules/document_checklist.py, phase-1.md amendment 3, phase-3.md §8)

export interface ChecklistContext {
  propertyId: string;
  /** The draft's cited payments by allocation id (a cost receipt opens its bank line). */
  payments?: Map<string, AllocationFact>;
  /** The checklist's items: a deposit split names its tenancies by their items (D142). */
  items?: readonly DocumentChecklistItem[];
  /** Called after an action changed the ledger, so the checklist reloads (D142). */
  onChanged?: () => void;
}

type Fix = { href: string; label: string };

interface RequirementGuide {
  /** The row's title; `year` is the checklist's tax year. */
  title: (item: DocumentChecklistItem, year: number) => string;
  /** What the document is for, in a few words. */
  hint: string;
  /** Was tun while it is missing. */
  missing: string;
  /** Was tun before its due day; default "Noch nicht fällig." and the missing sentence. */
  expected?: string;
  /** Where it is fixed: a property tab, the loan, the tenancy, the unit or the payment. */
  fix: (item: DocumentChecklistItem, ctx: ChecklistContext) => Fix;
}

function scopeSuffix(item: DocumentChecklistItem): string {
  const scope = item.scope;
  if (scope.type === "loan") return scope.lender ? ` · ${scope.lender}` : "";
  if (scope.type === "tenancy") return ` · ${scope.unit_label ?? "Einheit"}, ab ${formatDate(scope.tenancy_start)}`;
  return "";
}

const tabFix = (tab: string, label: string) => (_: DocumentChecklistItem, ctx: ChecklistContext) => ({
  href: `/properties/${ctx.propertyId}?tab=${tab}`,
  label,
});
const documentsFix = tabFix("dokumente", "Unter Dokumente verknüpfen");
const loanFix = (item: DocumentChecklistItem) => ({
  href: `/loans/${item.scope.id}`,
  label: "Beim Darlehen verknüpfen",
});
const tenancyFix = (item: DocumentChecklistItem) => ({
  href: `/tenancies/${item.scope.id}`,
  label: "Beim Mietverhältnis verknüpfen",
});

export const REQUIREMENT_GUIDE: Record<RequirementCode, RequirementGuide> = {
  purchase_contract: {
    title: () => "Kaufvertrag",
    hint: "Grundlage für AfA und Spekulationsfrist",
    missing:
      "Den Kaufvertrag vom Notar hochladen und bei der Immobilie verknüpfen. Die App liest daraus Kaufpreis, Daten und den Anteil des Gebäudes.",
    fix: documentsFix,
  },
  price_split: {
    title: () => "Kaufpreisaufteilung",
    hint: "Gebäudeanteil als AfA-Basis",
    missing:
      "Festlegen, welcher Teil des Kaufpreises auf das Gebäude entfällt. Meist steht er im Kaufvertrag; sonst helfen die Arbeitshilfe des Finanzministeriums oder ein Gutachten.",
    fix: tabFix("aufteilung", "Kaufpreisaufteilung wählen"),
  },
  acquisition_cost_evidence: {
    title: (item) =>
      `Beleg ${item.scope.cost_kind ? ACQUISITION_COST_KIND[item.scope.cost_kind] : "Nebenkosten"} ${formatMoney(item.scope.cost_amount)}`,
    hint: "Nebenkosten gehören in die AfA-Basis",
    missing: "Die Rechnung zu diesen Nebenkosten des Kaufs hochladen und unter Nebenkosten als Beleg wählen.",
    fix: tabFix("nebenkosten", "Beleg unter Nebenkosten zuordnen"),
  },
  grundsteuerbescheid: {
    title: () => "Grundsteuerbescheid",
    hint: "Gilt, bis ein neuer Bescheid kommt",
    missing: "Den Grundsteuerbescheid der Gemeinde hochladen und bei der Immobilie verknüpfen.",
    expected: "Noch nicht fällig: die Gemeinde schickt ihn meist Anfang des Jahres. Dann hochladen und verknüpfen.",
    fix: documentsFix,
  },
  loan_contract: {
    title: (item) => `Darlehensvertrag${scopeSuffix(item)}`,
    hint: "Zinsen sind Werbungskosten, Tilgung nicht",
    missing: "Den Darlehensvertrag hochladen und beim Darlehen verknüpfen.",
    fix: loanFix,
  },
  zinsbescheinigung: {
    title: (item, year) => `Zinsbescheinigung ${year}${scopeSuffix(item)}`,
    hint: "Die Bank schickt sie meist im Januar",
    missing:
      "Die Zinsbescheinigung der Bank hochladen und beim Darlehen verknüpfen; die App vergleicht damit die gebuchten Zinsen.",
    expected: "Noch nicht fällig: die Bank schickt sie meist im Januar. Dann hochladen und beim Darlehen verknüpfen.",
    fix: loanFix,
  },
  lease: {
    title: (item) => `Mietvertrag${scopeSuffix(item)}`,
    hint: "Grundlage der Mieteinnahmen",
    missing: "Den unterschriebenen Mietvertrag hochladen und beim Mietverhältnis verknüpfen.",
    fix: tenancyFix,
  },
  handover_protocol: {
    title: (item) => `Übergabeprotokoll${scopeSuffix(item)}`,
    hint: "Zustand der Wohnung beim Einzug",
    missing: "Das Übergabeprotokoll vom Einzug hochladen und beim Mietverhältnis verknüpfen.",
    fix: tenancyFix,
  },
  deposit_record: {
    title: (item) => `Kautionsnachweis${scopeSuffix(item)}`,
    hint: "Kautionskonto, Bürgschaft oder die Zahlung auf dem Konto",
    missing:
      "Einen Nachweis der Kaution (Kautionskonto oder Bürgschaft) hochladen und beim Mietverhältnis verknüpfen. Ging die Kaution auf dem Konto ein, genügt die Zahlung: sie diesem Mietverhältnis zuordnen.",
    fix: tenancyFix,
  },
  wirtschaftsplan: {
    title: (_, year) => `Wirtschaftsplan ${year} (WEG)`,
    hint: "Für die genaue Aufteilung des Hausgelds",
    missing:
      "Den Wirtschaftsplan der Eigentümergemeinschaft hochladen und bei der Immobilie verknüpfen; die App teilt das Hausgeld danach auf.",
    expected: "Noch nicht fällig: die Hausverwaltung schickt ihn vor oder zu Beginn des Jahres. Dann hochladen und verknüpfen.",
    fix: documentsFix,
  },
  hausgeldabrechnung: {
    title: (_, year) => `Hausgeldabrechnung ${year} (WEG)`,
    hint: "Kommt meist im Frühjahr des Folgejahres",
    missing: "Die Hausgeldabrechnung hochladen und bei der Immobilie verknüpfen.",
    expected:
      "Noch nicht fällig: die Hausverwaltung schickt sie meist im Frühjahr des nächsten Jahres. Dann hochladen und verknüpfen.",
    fix: documentsFix,
  },
  cost_receipt: {
    title: (item) =>
      `Beleg ${item.scope.category ? ALLOCATION_CATEGORY[item.scope.category] : "Zahlung"} ${formatDate(item.scope.booking_date)}`,
    hint: "Rechnung zur Zahlung, falls das Finanzamt fragt",
    missing: "Die Rechnung zu dieser Zahlung hochladen und bei der Zahlung als Beleg verknüpfen.",
    fix: (item, ctx) => {
      const fact = ctx.payments?.get(item.scope.id);
      return fact?.transaction_id
        ? { href: `/inbox?umsatz=${fact.transaction_id}`, label: "Zahlung öffnen" }
        : documentsFix(item, ctx);
    },
  },
  market_rent: {
    title: (item) => `Ortsübliche Miete${item.scope.type === "unit" ? ` · ${item.scope.unit_label ?? "Einheit"}` : ""}`,
    hint: "Mietspiegel-Wert für den Vergleich mit der vereinbarten Miete",
    missing:
      "Im Mietspiegel der Stadt nachsehen, wie viel Kaltmiete pro m² üblich ist, und den Wert bei der Einheit eintragen, mit dem Mietspiegel als Beleg.",
    fix: (item, ctx) =>
      item.scope.type === "unit"
        ? { href: `/units/${item.scope.id}#ortsuebliche-miete`, label: "Mietspiegel-Wert eintragen" }
        : tabFix("einheiten", "Bei der Einheit eintragen")(item, ctx),
  },
};

/** Missing and incomplete rows are to-dos; an expected one is not due yet (its reminder comes). */
export function isChecklistTodo(item: DocumentChecklistItem): boolean {
  return item.status === "missing" || item.status === "incomplete";
}

/** Was tun for a checklist row; null when the document is there. */
export function requirementTodo(item: DocumentChecklistItem): string | null {
  const guide = REQUIREMENT_GUIDE[item.code];
  const status: ChecklistStatus = item.status;
  if (status === "missing") return guide.missing;
  if (status === "expected") return guide.expected ?? `Noch nicht fällig. ${guide.missing}`;
  if (status === "incomplete") return "Das Dokument öffnen und eintragen, für welches Steuerjahr es gilt.";
  return null;
}

/** The fix of a checklist row: the document's Typisierung for a missing tax year, else the guide's. */
export function requirementFix(item: DocumentChecklistItem, ctx: ChecklistContext): Fix | null {
  if (item.status === "incomplete") {
    const [first] = item.documents;
    return first ? { href: `/documents/${first.id}#typisierung`, label: "Steuerjahr eintragen" } : null;
  }
  if (item.status === "missing" || item.status === "expected") return REQUIREMENT_GUIDE[item.code].fix(item, ctx);
  return null;
}

// --- the deadline reminders (phase-5.md §6) ---------------------------------------------------
// The link of each kind stays in components/reminders.tsx (reminderLink), next to its label.

type Reminder = components["schemas"]["Reminder"];
type ReminderKind = components["schemas"]["ReminderKind"];

interface ReminderGuide {
  /** True: something to do. False: a note (the date passes and nothing has to change). */
  todo: boolean;
  /** Was tun, in plain words. */
  text: (reminder: Reminder) => string;
}

export const REMINDER_GUIDE: Record<ReminderKind, ReminderGuide> = {
  "grundsteuer.instalment": {
    todo: true,
    text: (r) =>
      r.label_code.endsWith("sepa_mandate")
        ? "Auf dem Konto nachsehen, ob die Gemeinde die Grundsteuer abgebucht hat; dann „Erledigt“."
        : "Die Grundsteuer an die Gemeinde überweisen (Betrag und Kassenzeichen stehen unter Grundsteuer); dann „Erledigt“.",
  },
  "checklist.due": {
    todo: true,
    text: () => "Das Dokument hochladen und verknüpfen, sobald es da ist; danach verschwindet der Termin von selbst.",
  },
  "tax.return_deadline": {
    todo: true,
    text: () =>
      "Auf Steuern die offenen Punkte erledigen, die Ausfüllhilfe herunterladen und die Erklärung in ELSTER abgeben; dann „Erledigt“.",
  },
  "tax.questionnaire_open": {
    todo: true,
    text: () => "Die Jahresfragen beantworten; sie dauern meist nur ein paar Minuten.",
  },
  "tax.wage_certificate_missing": {
    todo: true,
    text: () => "Die Lohnsteuerbescheinigung vom Arbeitgeber auf der Anlage N hochladen; die App liest sie aus.",
  },
  "loan.fixed_rate_end": {
    todo: true,
    text: () =>
      "Bei der eigenen und bei anderen Banken ein Angebot für die Zeit danach einholen und den neuen Zins beim Darlehen eintragen.",
  },
  "loan.special_termination": {
    todo: false,
    text: () => "Nur prüfen: sind die Zinsen heute deutlich niedriger, kann eine Umschuldung lohnen. Sonst ist nichts zu tun.",
  },
  "property.speculation_end": {
    todo: false,
    text: () => "Nichts zu tun: ab diesem Tag bleibt ein Gewinn aus einem Verkauf steuerfrei.",
  },
  "tenancy.settlement_deadline": {
    todo: true,
    text: () =>
      "Die Nebenkostenabrechnung des Jahres erstellen und dem Mieter zustellen, bevor die Frist endet; danach ist keine Nachforderung mehr möglich.",
  },
  "tenancy.rent_missing": {
    todo: true,
    text: () =>
      "Auf dem Konto nachsehen, ob die Miete doch kam (dann in Zu prüfen zuordnen); sonst beim Mieter nachfragen.",
  },
  "bank.sync_stale": {
    todo: true,
    text: () => "Das Konto öffnen und den Abruf neu starten; oft muss die Freigabe in der Banking-App bestätigt werden.",
  },
};

/** Was tun for a reminder; null for a kind the map does not know yet. */
export function reminderTodo(reminder: Reminder): string | null {
  return REMINDER_GUIDE[reminder.kind]?.text(reminder) ?? null;
}

// --- the "Was noch fehlt" list ------------------------------------------------------------------

export interface TodoEntry {
  key: string;
  title: string;
  text: string | null;
  action: ReactNode;
  /** Where it belongs (the property's label on Übersicht). */
  context?: string;
}

/** The to-do card: a count, each entry with its Was tun and action; an empty list says so. */
export function TodoList({
  entries,
  empty,
  loading,
  titleId,
}: {
  entries: TodoEntry[];
  empty: string;
  loading?: boolean;
  titleId: string;
}) {
  return (
    <section className="card todo-card" aria-labelledby={titleId}>
      <div className="row todo-head">
        <h2 id={titleId}>Was noch fehlt</h2>
        {entries.length > 0 ? (
          <span className="badge badge-warn" aria-label={`${entries.length} offen`}>
            {entries.length}
          </span>
        ) : null}
      </div>
      {entries.length === 0 ? (
        <p className="muted">{loading ? "Lädt …" : empty}</p>
      ) : (
        <ol className="todo-list">
          {entries.map((entry) => (
            <li key={entry.key} className="todo-item">
              <p className="todo-title">
                {entry.title}
                {entry.context ? <span className="muted"> · {entry.context}</span> : null}
              </p>
              {entry.text ? <p className="todo-text">{entry.text}</p> : null}
              {entry.action ? <div className="row todo-actions">{entry.action}</div> : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** A draft warning as a to-do entry (to-do codes only). */
export function warningEntry(
  warning: DraftWarning,
  ctx: GuideContext,
  key: string,
  onChanged?: () => void,
  context?: string,
): TodoEntry | null {
  const guide = guideFor(warning.code);
  if (!guide?.todo) return null;
  return {
    key,
    title: guide.title?.(warning.params ?? {}) ?? "Offener Punkt",
    text: guide.text(warning.params ?? {}, warning),
    action: <WarningActionView warning={warning} ctx={ctx} onChanged={onChanged} />,
    context,
  };
}

/** A checklist row as a to-do entry (missing and incomplete only). */
/** A checklist requirement whose gap a draft warning already shows as a to-do, with the same
 * action: the price split (afa.no_split) and the Mietspiegel value (reduced_rent.no_market_rent).
 * The warning's entry stands for both, so the gap is listed once. */
const COVERING_WARNING: Partial<Record<DocumentChecklistItem["code"], string>> = {
  price_split: "afa.no_split",
  market_rent: "reduced_rent.no_market_rent",
};

export function coveredByWarning(item: DocumentChecklistItem, warnings: readonly DraftWarning[]): boolean {
  const code = COVERING_WARNING[item.code];
  return code !== undefined && warnings.some((w) => w.code === code);
}

export function checklistEntry(
  item: DocumentChecklistItem,
  year: number,
  ctx: ChecklistContext,
  context?: string,
): TodoEntry | null {
  if (!isChecklistTodo(item)) return null;
  return {
    key: `checklist-${ctx.propertyId}-${item.code}-${item.scope.id}`,
    title: `${REQUIREMENT_GUIDE[item.code].title(item, year)}: ${item.status === "incomplete" ? "Steuerjahr fehlt" : "fehlt"}`,
    text: requirementTodo(item),
    action: checklistAction(item, ctx),
    context,
  };
}

type OpenQuestion = components["schemas"]["OpenQuestion"];

/** An open Jahresfrage as a to-do entry, with its own question text when known. */
export function questionEntry(question: OpenQuestion, year: number, text: string | undefined, context?: string): TodoEntry {
  return {
    key: `frage-${question.key}-${question.owner_id ?? ""}-${question.property_id ?? ""}`,
    title: text ?? "Jahresfrage offen",
    text: "Die Frage beantworten; die App rechnet die Antwort ein.",
    action: (
      <Link
        href={questionnaireHref(year, {
          key: question.key,
          ownerId: question.owner_id,
          propertyId: question.property_id,
        })}
      >
        Beantworten
      </Link>
    ),
    context,
  };
}

/**
 * Was noch fehlt on the Anlage V tab: the draft's to-do warnings, the checklist's missing and
 * incomplete documents and the property's open Jahresfragen, each with its action. The
 * checklist's price split and market rent are left out while a draft warning stands for them
 * (one gap, one entry; see `coveredByWarning`).
 * `refresh` reloads the checklist and the questions after an action changed them.
 */
export function AnlageVTodos({
  propertyId,
  year,
  draft,
  payments,
  refresh,
  onChanged,
}: {
  propertyId: string;
  year: number;
  draft: AnlageVDraft;
  payments?: Map<string, AllocationFact>;
  refresh: number;
  onChanged: () => void;
}) {
  const loadChecklist = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/checklist", {
          params: { path: { property_id: propertyId }, query: { tax_year: year } },
        }),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh forces a reload
    [propertyId, year, refresh],
  );
  const checklist = useLoad(loadChecklist);
  const loadQuestions = useCallback(
    () => ok(api.GET("/api/tax/questionnaire", { params: { query: { tax_year: year } } })),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh forces a reload
    [year, refresh],
  );
  const questionnaire = useLoad(loadQuestions);

  const ctx: GuideContext = { year, propertyId, draft, payments };
  const warnings = draft.warnings ?? [];
  const entries: TodoEntry[] = [];
  warnings.forEach((warning, index) => {
    const entry = warningEntry(warning, ctx, `w-${warning.code}-${index}`, onChanged);
    if (entry) entries.push(entry);
  });
  for (const item of checklist.data?.items ?? []) {
    if (coveredByWarning(item, warnings)) continue;
    const entry = checklistEntry(item, year, { propertyId, payments, items: checklist.data?.items, onChanged });
    if (entry) entries.push(entry);
  }
  const text = new Map((questionnaire.data?.questions ?? []).map((q) => [q.key, q.question_de]));
  for (const question of questionnaire.data?.progress.open ?? []) {
    if (question.property_id !== propertyId) continue;
    entries.push(questionEntry(question, year, text.get(question.key)));
  }
  return (
    <TodoList
      titleId="tax-todos"
      entries={entries}
      loading={checklist.loading || questionnaire.loading}
      empty="Für diesen Entwurf ist nichts offen."
    />
  );
}
