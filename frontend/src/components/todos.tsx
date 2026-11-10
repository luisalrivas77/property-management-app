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
import { type ReactNode, useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type {
  AllocationFact,
  AnlageVDraft,
  ChecklistStatus,
  DocumentChecklistItem,
  DraftWarning,
  RequirementCode,
  WarningCode,
} from "@/api/schemas";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { ReadUpload } from "./documents";
import { formatDate, formatMoney, formatPercent } from "./format";
import { toDecimalString } from "./input";
import { ACQUISITION_COST_KIND, ALLOCATION_CATEGORY, PRICE_SPLIT_SOURCE } from "./labels";
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
  /** Upload and read a document (ReadUpload): the reading proposes the missing figures. */
  | { kind: "upload"; label: string }
  /** afa.no_split: select a recorded split, or upload the Kaufvertrag. */
  | { kind: "split" }
  /** income.pre_handover: record the D63 input "counts as rent". */
  | { kind: "pre_handover" }
  /** income.living_area_missing: type the flat's Wohnfläche (PATCH /properties/{id}). */
  | { kind: "living_area" };

/** Was tun, in plain words: what to upload, open or confirm, not the law. */
type GuideText = (params: Params) => string;
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
    text: () =>
      "Für eine genaue Rechnung die Rechnung zur Zahlung hochladen und verknüpfen; ohne sie rechnet die App mit 19 % Umsatzsteuer.",
    action: payment("Zahlung öffnen"),
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
    title: () => "Einnahme ohne Zuordnung",
    text: () =>
      "Die Zahlung öffnen und sagen, was sie war, zum Beispiel Miete, Kaution oder eine Erstattung. Dann zählt sie richtig.",
    action: payment("Zahlung öffnen"),
  },
  "income.pre_handover": {
    todo: true,
    title: () => "Einnahmen vor der Übergabe",
    text: () =>
      "Diese Einnahmen kamen, bevor die Wohnung Ihnen übergeben wurde. Standen Sie in diesen Monaten schon als Vermieter im Mietvertrag, sind sie Ihre Miete: dann „Als Miete zählen“ wählen.",
    action: () => ({ kind: "pre_handover" }),
  },
  "income.living_area_missing": {
    todo: true,
    title: () => "Wohnfläche der Wohnung fehlt",
    text: () =>
      "Die Wohnfläche der ganzen Wohnung eintragen; sie steht im Kaufvertrag oder in der Wohnflächenberechnung. Die Flächen der Zimmer reichen nicht, weil Küche, Bad und Flur fehlen.",
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
      <strong>Was tun:</strong> {guide.text(warning.params ?? {})}
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
      return <ReadUpload label={action.label} onUploaded={onChanged} />;
    case "split":
      return ctx.propertyId ? <SplitAction propertyId={ctx.propertyId} onChanged={onChanged} /> : null;
    case "pre_handover":
      return ctx.propertyId ? <PreHandoverAction propertyId={ctx.propertyId} onChanged={onChanged} /> : null;
    case "living_area":
      return ctx.propertyId ? <LivingAreaAction propertyId={ctx.propertyId} onChanged={onChanged} /> : null;
  }
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

/** income.pre_handover: the D63 input "these lines count as rent" (the one reading decided),
 * recorded only after the user confirms the sentence in the dialog. */
function PreHandoverAction({ propertyId, onChanged }: { propertyId: string; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
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
      close();
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
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
          <ErrorNotice error={error} />
          <div className="row">
            <button type="button" disabled={busy} onClick={() => void confirm(close)}>
              {busy ? "Speichert …" : "Ja, als Miete zählen"}
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

/** income.living_area_missing: the flat's Wohnfläche through PATCH /properties/{id} (D71). */
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
  );
}

// --- the document checklist (rules/document_checklist.py, phase-1.md amendment 3, phase-3.md §8)

export interface ChecklistContext {
  propertyId: string;
  /** The draft's cited payments by allocation id (a cost receipt opens its bank line). */
  payments?: Map<string, AllocationFact>;
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
    hint: "Kautionskonto oder Bürgschaft",
    missing: "Einen Nachweis der Kaution (Kautionskonto oder Bürgschaft) hochladen und beim Mietverhältnis verknüpfen.",
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
    text: guide.text(warning.params ?? {}),
    action: <WarningActionView warning={warning} ctx={ctx} onChanged={onChanged} />,
    context,
  };
}

/** A checklist row as a to-do entry (missing and incomplete only). */
export function checklistEntry(
  item: DocumentChecklistItem,
  year: number,
  ctx: ChecklistContext,
  context?: string,
): TodoEntry | null {
  if (!isChecklistTodo(item)) return null;
  const fix = requirementFix(item, ctx);
  return {
    key: `checklist-${ctx.propertyId}-${item.code}-${item.scope.id}`,
    title: `${REQUIREMENT_GUIDE[item.code].title(item, year)}: ${item.status === "incomplete" ? "Steuerjahr fehlt" : "fehlt"}`,
    text: requirementTodo(item),
    action: fix ? <Link href={fix.href}>{fix.label}</Link> : null,
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
 * checklist's price split is left out while afa.no_split stands for it (one gap, one entry).
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
  const hasNoSplit = warnings.some((w) => w.code === "afa.no_split");
  const entries: TodoEntry[] = [];
  warnings.forEach((warning, index) => {
    const entry = warningEntry(warning, ctx, `w-${warning.code}-${index}`, onChanged);
    if (entry) entries.push(entry);
  });
  for (const item of checklist.data?.items ?? []) {
    if (hasNoSplit && item.code === "price_split") continue;
    const entry = checklistEntry(item, year, { propertyId, payments });
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
