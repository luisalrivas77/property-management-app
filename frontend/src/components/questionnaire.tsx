"use client";

// The yearly questions, "Jahresfragen" (docs/phases/phase-4b.md §10, D110), at
// /tax/<year>/fragen: one answer slot per screen, mobile-first.
//
// Everything a screen says comes from GET /api/tax/questionnaire: the year's questions (text,
// "Warum", section, answer type, choices, targets, carry-forward) and its slots (one per owner,
// once for the household, one per property), each with last year's answer, the rows proposed
// from last year and the spanning rows to reconfirm, plus the progress. Slots are shown per
// owner ("Fragen für A"), then the household's, then per property.
//
// An answer is a code (Ja / Nein / Trifft nicht zu, or a choice's code; never free text). A
// "Ja" on a question with input targets opens the target kind's form inline: last year's rows
// pre-filled with "Vorjahr übernommen — prüfen und bestätigen" and each carried value marked,
// else one empty item. POST /api/tax/questionnaire/answers writes the answer and its rows in
// one transaction. A reconfirm question lists the standing items ("weiter genutzt" / "nicht
// mehr genutzt seit …"); an item no longer used is a superseding row with disposed_on. A
// reminder question shows the sentence it prints. "Später beantworten" leaves the slot open.
//
// An answered slot shows its answer (the view's current_answer) and "Antwort ändern": the new
// answer supersedes it (supersedes_id). On a facts question the slot's rows of this year are
// loaded from /return-inputs or the property's /tax-inputs and pre-filled; an edited row is
// posted with its own supersedes_id, an untouched one not at all, and a removed one (or every
// one, when the answer is no longer "Ja") as a withdrawal repeating its value.
//
// No euro figure is computed here: the last screen shows the estimate as the API returns it.
import Link from "next/link";
import { useCallback, useMemo, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { Owner, Property } from "@/api/schemas";
import type { components } from "@/api/types";

import { Field, FormBanner, Submit, useFormErrors, type FieldMessages } from "./form";
import { formatDate, formatMoney } from "./format";
import { decimalInput, toDecimalString, toFraction, toMoneyString, type Parsed } from "./input";
import {
  COMMUTE_KIND,
  HOUSEHOLD_SERVICE_CLASS,
  HOUSEHOLD_SERVICE_SOURCE,
  ITEM_CLASS,
  OTHER_EXPENSE_CLASS,
  PRIVATE_COST_CLASS,
  QUESTION_SECTION,
  QUESTIONNAIRE_ANSWER,
  RETURN_INPUT_KIND,
  WORK_ROOM_PREFER,
} from "./labels";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./questionnaire.css";

type Schemas = components["schemas"];
type View = Schemas["QuestionnaireView"];
type Question = Schemas["Question"];
type Target = Schemas["QuestionTarget"];
type Slot = Schemas["QuestionnaireSlot"];
type AnswerValue = Schemas["QuestionnaireAnswerValue"];
type AnswerBody = Schemas["QuestionnaireAnswerBody"];
type ReturnInputCreate = Schemas["ReturnInputCreate"];
type TaxInputCreate = Schemas["TaxInputCreate"];
type ReturnInputFact = Schemas["ReturnInputFact"];
type ReturnEstimate = Schemas["ReturnEstimate"];
type Value = Record<string, unknown>;

// --- loading --------------------------------------------------------------------------------

interface FlowData {
  view: View;
  owners: Owner[];
  properties: Property[];
  children: ReturnInputFact[];
}

async function loadFlow(year: number): Promise<FlowData> {
  const [view, owners, properties, children] = await Promise.all([
    ok(api.GET("/api/tax/questionnaire", { params: { query: { tax_year: year } } })),
    ok(api.GET("/api/owners")),
    ok(api.GET("/api/properties")),
    ok(api.GET("/api/return-inputs", { params: { query: { tax_year: year, kind: "child" } } })),
  ]);
  return { view, owners, properties, children };
}

function codesOf(error: unknown): string[] {
  return error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
}

// --- the slots in screen order ----------------------------------------------------------------

interface Step {
  slot: Slot;
  question: Question;
  group: string;
}

function sameSlot(a: Pick<Slot, "key" | "owner_id" | "property_id">, b: Pick<Slot, "key" | "owner_id" | "property_id">) {
  return a.key === b.key && (a.owner_id ?? null) === (b.owner_id ?? null) && (a.property_id ?? null) === (b.property_id ?? null);
}

/** The owners' slots per owner, then the household's, then each property's; file order inside. */
function steps(data: FlowData): Step[] {
  const questions = new Map(data.view.questions.map((q) => [q.key, q]));
  const ownerName = new Map(data.owners.map((o) => [o.id, o.full_name]));
  const propertyName = new Map(data.properties.map((p) => [p.id, p.label]));
  const groups = new Map<string, Step[]>();
  const order: string[] = [];
  const add = (groupKey: string, step: Step) => {
    if (!groups.has(groupKey)) {
      groups.set(groupKey, []);
      order.push(groupKey);
    }
    groups.get(groupKey)?.push(step);
  };
  const owners: Step[] = [];
  const household: Step[] = [];
  const properties: Step[] = [];
  for (const slot of data.view.slots) {
    const question = questions.get(slot.key);
    if (!question) continue;
    if (slot.owner_id) {
      owners.push({ slot, question, group: `Fragen für ${ownerName.get(slot.owner_id) ?? "Eigentümer"}` });
    } else if (slot.property_id) {
      properties.push({ slot, question, group: `Fragen zur Immobilie ${propertyName.get(slot.property_id) ?? ""}`.trim() });
    } else {
      household.push({ slot, question, group: "Fragen für den Haushalt" });
    }
  }
  for (const step of owners) add(`o:${step.slot.owner_id}`, step);
  for (const step of household) add("h", step);
  for (const step of properties) add(`p:${step.slot.property_id}`, step);
  return order.flatMap((key) => groups.get(key) ?? []);
}

function isOpen(view: View, slot: Slot): boolean {
  return (view.progress.open ?? []).some((o) => sameSlot(o, slot));
}

// --- the input forms per target kind ----------------------------------------------------------

type FieldType = "text" | "money" | "int" | "decimal" | "date" | "month" | "bool" | "select" | "fraction" | "child";

interface FieldSpec {
  name: string;
  label: string;
  type: FieldType;
  optional?: boolean;
  options?: Record<string, string>;
  hint?: string;
  /** The empty form's value, for the year asked. */
  initial?: (year: number) => string;
}

/** The return_input kinds the flow cannot type: their document is read instead (D96, D105). */
const DOCUMENT_KINDS: ReadonlySet<string> = new Set(["wage_certificate", "payslip"]);

/** Kinds whose standing items end with a disposal date (D108 (a)). */
const DISPOSABLE: ReadonlySet<string> = new Set(["work_equipment"]);

const FORMS: Record<string, FieldSpec[]> = {
  commute: [
    { name: "kind", label: "Art", type: "select", options: COMMUTE_KIND, initial: () => "first_workplace" },
    { name: "workplace_label", label: "Arbeitsstätte", type: "text", hint: "Zum Beispiel „Büro Arbeitgeber“." },
    { name: "period_from", label: "Von", type: "date", initial: (y) => `${y}-01-01` },
    { name: "period_to", label: "Bis", type: "date", initial: (y) => `${y}-12-31` },
    { name: "days_per_week", label: "Arbeitstage pro Woche", type: "int", initial: () => "5" },
    { name: "days", label: "Tage an der Arbeitsstätte", type: "int" },
    { name: "km", label: "Entfernung (km, einfache Strecke)", type: "int" },
    {
      name: "km_collective",
      label: "davon mit Bus und Bahn (km)",
      type: "int",
      optional: true,
      hint: "Leer lassen, wenn die ganze Strecke mit dem Auto gefahren wird.",
    },
    { name: "km_other", label: "davon anders, etwa zu Fuß oder Rad (km)", type: "int", optional: true },
    { name: "absent_days", label: "Urlaubs-, Krankheits- und Heimarbeitstage", type: "int", optional: true },
    { name: "public_transport_costs", label: "Kosten für Bus und Bahn (€)", type: "money", optional: true },
  ],
  home_office: [
    {
      name: "days_other_workplace",
      label: "Homeoffice-Tage, an denen ein Arbeitsplatz im Betrieb bereitstand",
      type: "int",
      initial: () => "0",
    },
    {
      name: "days_no_other_workplace",
      label: "Homeoffice-Tage ohne Arbeitsplatz im Betrieb",
      type: "int",
      initial: () => "0",
    },
  ],
  work_room: [
    { name: "months", label: "Monate genutzt", type: "int", initial: () => "12" },
    { name: "room_m2", label: "Zimmer (m²)", type: "decimal" },
    { name: "home_m2", label: "Wohnung (m²)", type: "decimal" },
    { name: "rent_year", label: "Jahresmiete (€)", type: "money" },
    { name: "running_costs_year", label: "Nebenkosten im Jahr (€)", type: "money", optional: true },
    { name: "room_only_costs_year", label: "Kosten nur für das Zimmer (€)", type: "money", optional: true },
    { name: "prefer", label: "Wahl", type: "select", options: WORK_ROOM_PREFER, optional: true },
  ],
  work_equipment: [
    { name: "label", label: "Bezeichnung", type: "text" },
    { name: "item_class", label: "Art", type: "select", options: ITEM_CLASS, initial: () => "computer" },
    { name: "gross", label: "Preis brutto (€)", type: "money" },
    { name: "bought_on", label: "Kaufdatum (auch Vorjahre)", type: "date" },
    { name: "professional_share", label: "Beruflicher Anteil", type: "fraction", initial: () => "1/1", hint: "Zum Beispiel 1/2 oder 50 %." },
    { name: "useful_life_months", label: "Nutzungsdauer (Monate)", type: "int", optional: true },
    { name: "disposed_on", label: "Nicht mehr genutzt seit", type: "date", optional: true },
  ],
  telecom: [
    { name: "bills_year", label: "Rechnungen im Jahr (€)", type: "money" },
    { name: "months", label: "Monate", type: "int", initial: () => "12" },
    { name: "employer_reimbursed", label: "Vom Arbeitgeber erstattet (€)", type: "money", optional: true },
  ],
  professional_association: [
    { name: "name", label: "Verband", type: "text" },
    { name: "amount", label: "Beiträge im Jahr (€)", type: "money" },
    { name: "is_union", label: "Gewerkschaft", type: "bool", initial: () => "false" },
  ],
  training: [
    { name: "label", label: "Bezeichnung", type: "text" },
    { name: "amount", label: "Kosten (€)", type: "money" },
  ],
  other_expense: [
    { name: "expense_class", label: "Art", type: "select", options: OTHER_EXPENSE_CLASS, initial: () => "other" },
    { name: "label", label: "Bezeichnung", type: "text" },
    { name: "amount", label: "Betrag (€)", type: "money", optional: true },
  ],
  other_insurance: [{ name: "amount", label: "Beiträge im Jahr (€)", type: "money" }],
  child: [
    { name: "born_on", label: "Geburtsdatum", type: "date", hint: "Angezeigt wird nur der Monat." },
    { name: "kindergeld_from", label: "Kindergeld ab (optional)", type: "month", optional: true },
  ],
  childcare_cost: [
    { name: "child_id", label: "Kind", type: "child" },
    { name: "amount", label: "Kosten im Jahr (€)", type: "money" },
    { name: "paid_to_account", label: "Per Überweisung bezahlt", type: "bool", initial: () => "true" },
  ],
  household_services: [
    { name: "service_class", label: "Art", type: "select", options: HOUSEHOLD_SERVICE_CLASS, initial: () => "handwerker" },
    { name: "labour_cost", label: "Arbeitskosten (€)", type: "money" },
    { name: "source", label: "Quelle", type: "select", options: HOUSEHOLD_SERVICE_SOURCE, initial: () => "own_invoice" },
    { name: "paid_to_account", label: "Per Überweisung bezahlt", type: "bool", initial: () => "true" },
  ],
  special_expense: [
    { name: "recipient_label", label: "Empfänger", type: "text" },
    { name: "amount", label: "Betrag (€)", type: "money" },
  ],
  private_cost: [
    { name: "cost_class", label: "Art", type: "select", options: PRIVATE_COST_CLASS, initial: () => "postage" },
    { name: "label", label: "Bezeichnung", type: "text" },
    { name: "amount", label: "Betrag (€)", type: "money", optional: true },
    { name: "trips", label: "Fahrten", type: "int", optional: true },
    { name: "km_round_trip", label: "Kilometer je Fahrt (hin und zurück)", type: "int", optional: true },
    { name: "share", label: "Anteil für die Vermietung", type: "fraction", initial: () => "1/1" },
  ],
};

/** Values a kind always has and the form does not ask for. */
const FIXED: Record<string, Value> = {
  special_expense: { expense_class: "donation" },
};

function hasForm(target: Target): boolean {
  return target.kind !== "reminder" && !!target.input_kind && !DOCUMENT_KINDS.has(target.input_kind) && !!FORMS[target.input_kind];
}

/** A stored value as the input shows it. */
function toRaw(type: FieldType, value: unknown): string {
  if (value === null || value === undefined) return "";
  switch (type) {
    case "money":
    case "decimal":
      return decimalInput(String(value));
    case "month":
      return String(value).slice(0, 7);
    case "bool":
      return value ? "true" : "false";
    case "fraction": {
      const f = value as { numerator: number; denominator: number };
      return `${f.numerator}/${f.denominator}`;
    }
    default:
      return String(value);
  }
}

const REQUIRED = "Bitte ausfüllen.";

function parseField(spec: FieldSpec, raw: string): Parsed<unknown> {
  const text = raw.trim();
  switch (spec.type) {
    case "money":
      return toMoneyString(text);
    case "decimal":
      return toDecimalString(text, 2);
    case "int":
      return /^\d+$/.test(text) ? { ok: true, value: Number(text) } : { ok: false, message: "Bitte eine ganze Zahl eingeben." };
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { ok: true, value: text } : { ok: false, message: "Bitte ein Datum eingeben." };
    case "month":
      return /^\d{4}-\d{2}$/.test(text) ? { ok: true, value: `${text}-01` } : { ok: false, message: "Bitte einen Monat wählen." };
    case "bool":
      return { ok: true, value: text === "true" };
    case "fraction":
      return toFraction(text);
    default:
      return text === "" ? { ok: false, message: REQUIRED } : { ok: true, value: text };
  }
}

/** One item being entered: its target, the inputs, and what it was pre-filled from. */
interface Item {
  key: number;
  target: Target;
  kind: string;
  raw: Record<string, string>;
  /** The carried values as first shown (null: not from last year). */
  carried: Record<string, string> | null;
  base: Value;
  carriedFromId: string | null;
  /** The stored row this item edits (a correction supersedes it), with its inputs as loaded. */
  existing: Existing | null;
  originalRaw: Record<string, string> | null;
}

/** A current row of this year written for the slot, as "Antwort ändern" loads it. */
interface Existing {
  table: "return_inputs" | "tax_inputs";
  id: string;
  kind: string;
  taxYear: number;
  ownerId: string | null;
  propertyId: string | null;
  documentId: string | null;
  value: Value;
}

let itemCounter = 0;

function blankItem(target: Target, year: number): Item {
  const kind = target.input_kind ?? "";
  const raw: Record<string, string> = {};
  for (const spec of FORMS[kind] ?? []) raw[spec.name] = spec.initial ? spec.initial(year) : spec.type === "bool" ? "false" : "";
  for (const [name, value] of Object.entries(target.value_match ?? {})) raw[name] = value;
  return { key: ++itemCounter, target, kind, raw, carried: null, base: {}, carriedFromId: null, existing: null, originalRaw: null };
}

function rawOf(kind: string, value: Value): Record<string, string> {
  const raw: Record<string, string> = {};
  for (const spec of FORMS[kind] ?? []) raw[spec.name] = toRaw(spec.type, value[spec.name]);
  return raw;
}

function matches(value: Value, match: Record<string, string>): boolean {
  return Object.entries(match).every(([name, code]) => value[name] === code);
}

/**
 * The question's target a row of `kind` belongs to: one whose value_match it meets, or one
 * without value_match when no question's value_match claims it (the backend's prefill reads
 * rows the same way).
 */
function targetFor(question: Question, questions: Question[], kind: string, value: Value): Target | null {
  const claimed = questions.some((q) =>
    (q.targets ?? []).some((t) => t.input_kind === kind && t.value_match && matches(value, t.value_match)),
  );
  return (
    (question.targets ?? []).find(
      (t) => t.input_kind === kind && hasForm(t) && (t.value_match ? matches(value, t.value_match) : !claimed),
    ) ?? null
  );
}

/** True when the item edits a stored row and none of its inputs changed: nothing to post. */
function unchanged(item: Item): boolean {
  if (item.existing === null || item.originalRaw === null) return false;
  const original = item.originalRaw;
  return Object.entries(item.raw).every(([name, raw]) => raw === (original[name] ?? ""));
}

/**
 * The slot's current rows of this year, as editable items. A reconfirm question whose kind a
 * yearly question also asks for (the work items: equipment_new) leaves this year's rows to
 * that question, so one row is never edited from two screens.
 */
async function loadSlotRows(year: number, slot: Slot, question: Question, questions: Question[]): Promise<Item[]> {
  const items: Item[] = [];
  const kinds = new Map<string, Target>();
  for (const t of question.targets ?? []) if (hasForm(t) && t.input_kind) kinds.set(t.input_kind, t);
  for (const [kind, target] of kinds) {
    if (
      question.carry_forward === "reconfirm" &&
      questions.some((q) => q.carry_forward !== "reconfirm" && (q.targets ?? []).some((t) => t.input_kind === kind))
    ) {
      continue;
    }
    let rows: Existing[];
    if (target.kind === "tax_input") {
      if (!slot.property_id) continue;
      const list = await ok(
        api.GET("/api/properties/{property_id}/tax-inputs", {
          params: { path: { property_id: slot.property_id }, query: { tax_year: year, kind: kind as TaxInputCreate["kind"] } },
        }),
      );
      rows = list.map((r) => ({
        table: "tax_inputs",
        id: r.id,
        kind: r.kind,
        taxYear: r.tax_year ?? year,
        ownerId: null,
        propertyId: r.property_id,
        documentId: r.document_id ?? null,
        value: r.value as unknown as Value,
      }));
    } else {
      const query: { tax_year: number; kind: ReturnInputCreate["kind"]; owner_id?: string } = {
        tax_year: year,
        kind: kind as ReturnInputCreate["kind"],
      };
      if (slot.owner_id) query.owner_id = slot.owner_id;
      const list = await ok(api.GET("/api/return-inputs", { params: { query } }));
      rows = list.map((r) => ({
        table: "return_inputs",
        id: r.id,
        kind: r.kind,
        taxYear: r.tax_year,
        ownerId: r.owner_id ?? null,
        propertyId: null,
        documentId: r.document_id ?? null,
        value: r.value as unknown as Value,
      }));
    }
    for (const row of rows) {
      if (row.taxYear !== year || (row.table === "return_inputs" && row.ownerId !== (slot.owner_id ?? null))) continue;
      const own = targetFor(question, questions, kind, row.value);
      if (own === null) continue;
      const raw = rawOf(kind, row.value);
      items.push({
        key: ++itemCounter,
        target: own,
        kind,
        raw,
        carried: null,
        base: row.value,
        carriedFromId: null,
        existing: row,
        originalRaw: { ...raw },
      });
    }
  }
  return items;
}

function proposedItem(row: ReturnInputCreate | TaxInputCreate, question: Question): Item | null {
  const value = row.value as unknown as Value;
  const target = question.targets?.find(
    (t) =>
      t.input_kind === row.kind &&
      Object.entries(t.value_match ?? {}).every(([name, code]) => value[name] === code),
  );
  if (!target || !hasForm(target)) return null;
  const raw = rawOf(row.kind, value);
  const carriedFromId = row.carried_from_id ?? null;
  return {
    key: ++itemCounter,
    target,
    kind: row.kind,
    raw,
    carried: carriedFromId ? { ...raw } : null,
    base: value,
    carriedFromId,
    existing: null,
    originalRaw: null,
  };
}

/**
 * The codes a select may not offer on this target: what other questions' value_match claim for
 * the same kind (e.g. a subscription belongs to its own question, D110), unless this target
 * itself is the one that claims them.
 */
function excludedCodes(questions: Question[], target: Target, field: string): Set<string> {
  const out = new Set<string>();
  if (target.value_match?.[field] !== undefined) return out;
  for (const q of questions) {
    for (const t of q.targets ?? []) {
      const code = t.value_match?.[field];
      if (t.input_kind === target.input_kind && code !== undefined) out.add(code);
    }
  }
  return out;
}

/** The item's value, or the messages of its inputs under `prefix`. */
function buildValue(item: Item, prefix: string, messages: FieldMessages): Value | null {
  const value: Value = { ...item.base, ...(FIXED[item.kind] ?? {}) };
  let fine = true;
  for (const spec of FORMS[item.kind] ?? []) {
    const raw = item.raw[spec.name] ?? "";
    if (raw.trim() === "" && spec.type !== "bool") {
      delete value[spec.name];
      if (!spec.optional) {
        messages[`${prefix}.${spec.name}`] = [REQUIRED];
        fine = false;
      }
      continue;
    }
    const parsed = parseField(spec, raw);
    if (parsed.ok) value[spec.name] = parsed.value;
    else {
      messages[`${prefix}.${spec.name}`] = [parsed.message];
      fine = false;
    }
  }
  if (item.kind === "commute" && fine) {
    // The car's share of the route is what the other two leave (km_car + km_collective +
    // km_other = km, CommuteFact): whole kilometres, not money. The form does not ask for it,
    // so it is set afresh each time (an edited distance must not keep last value's share).
    const rest = Number(value.km) - Number(value.km_collective ?? 0) - Number(value.km_other ?? 0);
    value.km_car = Math.max(0, rest);
  }
  return fine ? value : null;
}

// --- describing standing rows ----------------------------------------------------------------

function monthText(isoDate: string): string {
  const [year, month] = isoDate.split("-").map(Number);
  if (!year || !month) return isoDate;
  return new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, 1)),
  );
}

function childLabel(row: ReturnInputFact, index: number): string {
  const v = row.value as { born_on: string };
  return `Kind ${index + 1} (geboren ${monthText(v.born_on)})`;
}

function describeRow(row: ReturnInputFact, index: number): string {
  if (row.kind === "child") return childLabel(row, index);
  if (row.kind === "work_equipment") {
    const v = row.value as { label: string; bought_on: string; item_class: keyof typeof ITEM_CLASS };
    return `${v.label} (${ITEM_CLASS[v.item_class]}, gekauft ${formatDate(v.bought_on)})`;
  }
  return RETURN_INPUT_KIND[row.kind];
}

// --- the page -------------------------------------------------------------------------------

export function QuestionnaireFlow({ year }: { year: number }) {
  const load = useCallback(() => loadFlow(year), [year]);
  const state = useLoad(load);
  const [index, setIndex] = useState<number | null>(null);

  return (
    <section className="fragen">
      <p className="fragen-back">
        <Link href={`/tax/${year}`}>← Steuerjahr {year}</Link>
      </p>
      <h1>Jahresfragen {year}</h1>
      <Loading state={state}>
        {(data) => {
          const list = steps(data);
          const firstOpen = list.findIndex((s) => isOpen(data.view, s.slot));
          const current = index ?? (firstOpen === -1 ? list.length : firstOpen);
          const reload = () => state.reload();
          return (
            <>
              {data.view.reused_from != null ? (
                <p className="notice notice-info">
                  Die Fragen für {year} sind noch nicht festgelegt; es gelten die von {data.view.reused_from}.
                </p>
              ) : null}
              {current >= list.length ? (
                <Summary year={year} data={data} steps={list} onJump={setIndex} />
              ) : (
                <StepScreen
                  key={`${current}:${list[current]?.slot.key}:${list[current]?.slot.current_answer?.id ?? "open"}`}
                  year={year}
                  data={data}
                  step={list[current]!}
                  position={current}
                  total={list.length}
                  onBack={() => setIndex(Math.max(0, current - 1))}
                  onNext={() => setIndex(current + 1)}
                  onAnswered={(changed) => {
                    // A first answer moves on to the next open slot after this one, else to the
                    // last screen (which lists any slot left open before it); a changed answer
                    // moves on by one.
                    const next = changed
                      ? current + 1
                      : list.findIndex((s, at) => at > current && isOpen(data.view, s.slot));
                    setIndex(next === -1 ? list.length : next);
                    reload();
                  }}
                />
              )}
            </>
          );
        }}
      </Loading>
    </section>
  );
}

function ProgressBar({ position, total, answered }: { position: number; total: number; answered: number }) {
  return (
    <div className="fragen-progress">
      <div className="row fragen-progress-head">
        <strong>
          Frage {position} von {total}
        </strong>
        <span className="muted">
          {answered} von {total} beantwortet
        </span>
      </div>
      <progress max={total} value={position} aria-label={`Frage ${position} von ${total}`} />
    </div>
  );
}

function StepScreen({
  year,
  data,
  step,
  position,
  total,
  onBack,
  onNext,
  onAnswered,
}: {
  year: number;
  data: FlowData;
  step: Step;
  position: number;
  total: number;
  onBack: () => void;
  onNext: () => void;
  onAnswered: (changed: boolean) => void;
}) {
  const { slot, question } = step;
  const open = isOpen(data.view, slot);
  const targets = question.targets ?? [];
  const reminders = targets.filter((t) => t.kind === "reminder");
  const formTargets = targets.filter(hasForm);
  const documentsOnly = targets.length > 0 && targets.every((t) => t.input_kind && DOCUMENT_KINDS.has(t.input_kind));
  const reconfirm = useMemo(
    () => (question.carry_forward === "reconfirm" ? (slot.reconfirm_rows ?? []) : []),
    [question.carry_forward, slot.reconfirm_rows],
  );
  const facts = question.answer_type !== "choice" && formTargets.length > 0;

  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<Item[]>(() => initialItems(slot, question, formTargets, reconfirm, year));
  // A standing item's "nicht mehr genutzt seit" as stored (null: in use), so only a change is posted.
  const storedDisposal = useMemo(
    () =>
      Object.fromEntries(
        reconfirm
          .filter((r) => DISPOSABLE.has(r.kind))
          .map((r) => [r.id, ((r.value as unknown as Value).disposed_on as string | null | undefined) ?? null]),
      ) as Record<string, string | null>,
    [reconfirm],
  );
  const [disposed, setDisposed] = useState<Record<string, string | null>>(storedDisposal);
  const [busy, setBusy] = useState(false);
  // "Antwort ändern": the slot's answer is open again; its rows of this year are loaded for editing.
  const current = slot.current_answer ?? null;
  const [changing, setChanging] = useState(false);
  const [existing, setExisting] = useState<Existing[]>([]);
  const [loadingRows, setLoadingRows] = useState(false);
  const asking = open || changing;
  const keys = useMemo(() => fieldKeys(items), [items]);
  const form = useFormErrors(keys);
  const carriedAny = items.some((i) => i.carried !== null);

  async function send(answer: AnswerValue, choice: string | null, withRows: boolean) {
    const messages: FieldMessages = {};
    const returnInputs: ReturnInputCreate[] = [];
    const taxInputs: TaxInputCreate[] = [];
    const kept = new Set<string>();
    if (withRows) {
      for (const item of items) {
        if (item.existing) kept.add(item.existing.id);
        if (unchanged(item)) continue;
        const table = tableOf(item);
        const at = table === "tax_inputs" ? taxInputs.length : returnInputs.length;
        const value = buildValue(item, `${table}.${at}.value`, messages);
        if (value === null) {
          // keep the indexes aligned with the keys the inputs use
          if (table === "tax_inputs") taxInputs.push(undefined as unknown as TaxInputCreate);
          else returnInputs.push(undefined as unknown as ReturnInputCreate);
          continue;
        }
        if (table === "tax_inputs") {
          taxInputs.push({
            property_id: slot.property_id ?? "",
            kind: item.kind as TaxInputCreate["kind"],
            tax_year: year,
            document_id: item.existing?.documentId ?? null,
            value: value as unknown as TaxInputCreate["value"],
            supersedes_id: item.existing?.id ?? null,
            carried_from_id: item.carriedFromId,
            withdrawn: false,
          });
        } else {
          returnInputs.push({
            owner_id: slot.owner_id ?? null,
            kind: item.kind as ReturnInputCreate["kind"],
            tax_year: year,
            document_id: item.existing?.documentId ?? null,
            value: value as unknown as ReturnInputCreate["value"],
            supersedes_id: item.existing?.id ?? null,
            carried_from_id: item.carriedFromId,
            withdrawn: false,
          });
        }
      }
    }
    // A stored row removed from the list, or every one when the answer is no longer "Ja": a
    // withdrawal, a new row repeating its value with withdrawn = true.
    for (const row of existing) {
      if (kept.has(row.id)) continue;
      if (row.table === "tax_inputs") {
        taxInputs.push({
          property_id: row.propertyId ?? "",
          kind: row.kind as TaxInputCreate["kind"],
          tax_year: row.taxYear,
          document_id: row.documentId,
          value: row.value as unknown as TaxInputCreate["value"],
          supersedes_id: row.id,
          withdrawn: true,
        });
      } else {
        returnInputs.push({
          owner_id: row.ownerId,
          kind: row.kind as ReturnInputCreate["kind"],
          tax_year: row.taxYear,
          document_id: row.documentId,
          value: row.value as unknown as ReturnInputCreate["value"],
          supersedes_id: row.id,
          withdrawn: true,
        });
      }
    }
    // A standing item no longer used: a superseding row of its own year with disposed_on.
    for (const row of reconfirm) {
      if (!DISPOSABLE.has(row.kind)) continue;
      const since = disposed[row.id] ?? null;
      if (since === (storedDisposal[row.id] ?? null)) continue;
      if (since !== null && !/^\d{4}-\d{2}-\d{2}$/.test(since)) {
        messages[`dispose.${row.id}`] = ["Bitte das Datum eingeben, seit dem es nicht mehr genutzt wird."];
        continue;
      }
      returnInputs.push({
        owner_id: row.owner_id ?? null,
        kind: row.kind,
        tax_year: row.tax_year,
        document_id: row.document_id ?? null,
        value: { ...(row.value as unknown as Value), disposed_on: since } as unknown as ReturnInputCreate["value"],
        supersedes_id: row.id,
        withdrawn: false,
      });
    }
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    const prior = slot.prior_answer;
    const body: AnswerBody = {
      answer: {
        question_key: slot.key,
        tax_year: year,
        owner_id: slot.owner_id ?? null,
        property_id: slot.property_id ?? null,
        answer,
        choice,
        supersedes_id: changing ? (current?.id ?? null) : null,
        carried_from_id: prior && prior.answer === answer && (prior.choice ?? null) === choice ? prior.id : null,
        withdrawn: false,
      },
      return_inputs: returnInputs,
      tax_inputs: taxInputs,
    };
    setBusy(true);
    form.clear();
    try {
      await ok(api.POST("/api/tax/questionnaire/answers", { body }));
      onAnswered(changing);
    } catch (error) {
      if (codesOf(error).includes("questionnaire.not_current")) {
        form.invalid({ answer: ["Diese Frage wurde inzwischen anders beantwortet. Die Seite neu laden."] });
      } else {
        form.fail(error);
      }
    } finally {
      setBusy(false);
    }
  }

  function answerNo(answer: AnswerValue) {
    if (answer === "no" && reconfirm.some((r) => DISPOSABLE.has(r.kind))) {
      // "Nein" to items still in use: each one ends; its date is asked for.
      const missing = reconfirm.filter((r) => DISPOSABLE.has(r.kind) && !disposed[r.id]);
      if (missing.length > 0) {
        setDisposed((d) => ({ ...d, ...Object.fromEntries(missing.map((r) => [r.id, d[r.id] ?? ""])) }));
        form.invalid(Object.fromEntries(missing.map((r) => [`dispose.${r.id}`, ["Seit wann nicht mehr genutzt?"]])));
        return;
      }
    }
    void send(answer, null, false);
  }

  async function startChange() {
    form.clear();
    if (!facts) {
      setChanging(true);
      return;
    }
    setLoadingRows(true);
    try {
      const loaded = await loadSlotRows(year, slot, question, data.view.questions);
      setExisting(loaded.flatMap((i) => (i.existing ? [i.existing] : [])));
      setItems(loaded.length > 0 ? loaded : reconfirm.length > 0 ? [] : [blankItem(formTargets[0]!, year)]);
      setExpanded(current?.answer === "yes" && loaded.length > 0);
      setChanging(true);
    } catch (error) {
      form.fail(error);
    } finally {
      setLoadingRows(false);
    }
  }

  function onSubmitFacts(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send("yes", null, true);
  }

  const prior = slot.prior_answer;
  const suggestion = slot.proposed_answer
    ? `${prior ? "Vorjahr" : "Vorschlag"}: ${QUESTIONNAIRE_ANSWER[slot.proposed_answer]}`
    : prior?.choice
      ? `Vorjahr: ${question.choices?.find((c) => c.code === prior.choice)?.label_de ?? prior.choice}`
      : null;

  return (
    <div className="fragen-card">
      <ProgressBar position={position + 1} total={total} answered={data.view.progress.answered} />
      <p className="fragen-group">
        {step.group} · <span className="muted">{QUESTION_SECTION[question.section]}</span>
      </p>
      <h2 className="fragen-question">{question.question_de}</h2>
      <p className="fragen-why">
        <strong>Warum:</strong> {question.why_de}
      </p>
      {reminders.map((t) => (
        <p key={t.reminder_de} className="notice notice-info">
          Bei „Ja“ steht auf der Übersicht: {t.reminder_de}
        </p>
      ))}
      {documentsOnly ? (
        <p className="muted">
          Bescheinigung und Gehaltsabrechnungen unter <Link href="/documents">Dokumente</Link> hochladen; die App liest sie
          aus und Sie bestätigen jeden Wert.
        </p>
      ) : null}

      {!asking ? (
        <>
          <p className="notice notice-ok">
            {current ? (
              <>
                Ihre Antwort: <strong>{answerText(question, current.answer, current.choice ?? null)}</strong>
              </>
            ) : (
              "Diese Frage ist schon beantwortet."
            )}
          </p>
          {current ? (
            <div className="row">
              <button type="button" className="secondary" disabled={loadingRows} onClick={() => void startChange()}>
                {loadingRows ? "Lädt …" : "Antwort ändern"}
              </button>
            </div>
          ) : null}
          <FormBanner errors={form.errors} />
        </>
      ) : (
        <>
          {changing && current ? (
            <p className="muted">Bisher: {answerText(question, current.answer, current.choice ?? null)}</p>
          ) : suggestion ? (
            <p className="muted">{suggestion}</p>
          ) : null}
          {changing && existing.length > 0 && !expanded ? (
            <p className="muted">
              Bei „Nein“ oder „Trifft nicht zu“{" "}
              {existing.length === 1 ? "wird die Angabe" : `werden die ${existing.length} Angaben`} zu dieser Frage
              zurückgezogen.
            </p>
          ) : null}
          {reconfirm.length > 0 ? (
            <Reconfirm
              rows={reconfirm}
              disposed={disposed}
              onChange={setDisposed}
              errors={(id) => form.of(`dispose.${id}`)}
            />
          ) : null}
          {question.answer_type === "choice" ? (
            <div className="fragen-answers" role="group" aria-label="Antwort">
              {(question.choices ?? []).map((c) => (
                <button
                  key={c.code}
                  type="button"
                  className={prior?.choice === c.code ? "" : "secondary"}
                  disabled={busy}
                  onClick={() => void send("yes", c.code, false)}
                >
                  {c.label_de}
                </button>
              ))}
              <button type="button" className="secondary" disabled={busy} onClick={() => void send("not_applicable", null, false)}>
                {QUESTIONNAIRE_ANSWER.not_applicable}
              </button>
            </div>
          ) : expanded && facts ? (
            <form className="stack" onSubmit={onSubmitFacts} noValidate aria-label="Angaben">
              {carriedAny ? (
                <p className="notice notice-info">Vorjahr übernommen — prüfen und bestätigen</p>
              ) : null}
              {items.map((item, at) => (
                <ItemForm
                  key={item.key}
                  item={item}
                  index={at}
                  prefix={prefixOf(items, at)}
                  questions={data.view.questions}
                  childRows={data.children}
                  errors={form.of}
                  onChange={(raw) => setItems((list) => list.map((i) => (i.key === item.key ? { ...i, raw } : i)))}
                  onRemove={() => setItems((list) => list.filter((i) => i.key !== item.key))}
                />
              ))}
              <div className="row">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setItems((list) => [...list, blankItem(formTargets[0]!, year)])}
                >
                  {items.length === 0 ? "Eintrag hinzufügen" : "Weiteren Eintrag hinzufügen"}
                </button>
              </div>
              <div className="fragen-answers">
                <Submit busy={busy}>Speichern und weiter</Submit>
                <button type="button" className="secondary" onClick={() => setExpanded(false)}>
                  Abbrechen
                </button>
              </div>
            </form>
          ) : (
            <div className="fragen-answers" role="group" aria-label="Antwort">
              <button
                type="button"
                className={slot.proposed_answer === "yes" ? "" : "secondary"}
                disabled={busy}
                onClick={() => (facts ? setExpanded(true) : void send("yes", null, false))}
              >
                {QUESTIONNAIRE_ANSWER.yes}
              </button>
              <button type="button" className="secondary" disabled={busy} onClick={() => answerNo("no")}>
                {QUESTIONNAIRE_ANSWER.no}
              </button>
              <button type="button" className="secondary" disabled={busy} onClick={() => answerNo("not_applicable")}>
                {QUESTIONNAIRE_ANSWER.not_applicable}
              </button>
            </div>
          )}
          {form.of("answer").map((m) => (
            <p key={m} className="notice notice-error">
              {m}
            </p>
          ))}
          <FormBanner errors={form.errors} />
          {changing ? (
            <div className="row">
              <button
                type="button"
                className="link"
                onClick={() => {
                  setChanging(false);
                  setExpanded(false);
                  setExisting([]);
                  setDisposed(storedDisposal);
                  form.clear();
                }}
              >
                Nicht ändern
              </button>
            </div>
          ) : null}
        </>
      )}

      <div className="row fragen-nav">
        <button type="button" className="link" onClick={onBack} disabled={position === 0}>
          Zurück
        </button>
        {open ? (
          <button type="button" className="link" onClick={onNext}>
            Später beantworten
          </button>
        ) : (
          <button type="button" className="link" onClick={onNext}>
            Weiter
          </button>
        )}
      </div>
    </div>
  );
}

/** An answer as the screen names it: the choice's label, or Ja / Nein / Trifft nicht zu. */
function answerText(question: Question, answer: AnswerValue, choice: string | null): string {
  if (choice) return question.choices?.find((c) => c.code === choice)?.label_de ?? choice;
  return QUESTIONNAIRE_ANSWER[answer];
}

function initialItems(slot: Slot, question: Question, formTargets: Target[], reconfirm: ReturnInputFact[], year: number): Item[] {
  if (formTargets.length === 0) return [];
  const proposed = (slot.proposed_rows ?? [])
    .map((row) => proposedItem(row, question))
    .filter((i): i is Item => i !== null);
  if (proposed.length > 0) return proposed;
  if (reconfirm.length > 0) return [];
  return [blankItem(formTargets[0]!, year)];
}

/** The body path of an item's value, as the API reports a field error. */
function prefixOf(items: Item[], at: number): string {
  const item = items[at]!;
  if (unchanged(item)) return `kept.${item.key}.value`;
  const table = tableOf(item);
  const index = items.slice(0, at).filter((i) => tableOf(i) === table && !unchanged(i)).length;
  return `${table}.${index}.value`;
}

function tableOf(item: Item): "return_inputs" | "tax_inputs" {
  return item.target.kind === "tax_input" ? "tax_inputs" : "return_inputs";
}

function fieldKeys(items: Item[]): string[] {
  return items.flatMap((item, at) => (FORMS[item.kind] ?? []).map((spec) => `${prefixOf(items, at)}.${spec.name}`));
}

function ItemForm({
  item,
  index,
  prefix,
  questions,
  childRows,
  errors,
  onChange,
  onRemove,
}: {
  item: Item;
  index: number;
  prefix: string;
  questions: Question[];
  childRows: ReturnInputFact[];
  errors: (key: string) => string[];
  onChange: (raw: Record<string, string>) => void;
  onRemove: () => void;
}) {
  const fixed = item.target.value_match ?? {};
  const set = (name: string, value: string) => onChange({ ...item.raw, [name]: value });
  const id = (name: string) => `item-${item.key}-${name}`;
  return (
    <fieldset className="fragen-item">
      <legend>
        {item.kind === "private_cost" ? "Privat bezahlte Kosten" : RETURN_INPUT_KIND[item.kind as keyof typeof RETURN_INPUT_KIND]}{" "}
        {index + 1}
      </legend>
      <div className="grid">
        {(FORMS[item.kind] ?? []).map((spec) => {
          const raw = item.raw[spec.name] ?? "";
          const carried = item.carried !== null && raw !== "" && raw === item.carried[spec.name];
          const label = (
            <>
              {spec.label}
              {carried ? <span className="badge fragen-carried">Vorjahr</span> : null}
            </>
          );
          if (fixed[spec.name] !== undefined) {
            return (
              <p key={spec.name} className="field">
                <span className="muted">{spec.label}: </span>
                {spec.options?.[fixed[spec.name]!] ?? fixed[spec.name]}
              </p>
            );
          }
          if (spec.type === "bool") {
            return (
              <label key={spec.name} className="inline">
                <input type="checkbox" checked={raw === "true"} onChange={(e) => set(spec.name, e.target.checked ? "true" : "false")} />
                {label}
              </label>
            );
          }
          return (
            <Field key={spec.name} id={id(spec.name)} label={label} hint={spec.hint} errors={errors(`${prefix}.${spec.name}`)}>
              {spec.type === "select" ? (
                <select value={raw} onChange={(e) => set(spec.name, e.target.value)}>
                  {spec.optional ? <option value="">–</option> : null}
                  {Object.entries(spec.options ?? {})
                    .filter(([code]) => !excludedCodes(questions, item.target, spec.name).has(code))
                    .map(([code, text]) => (
                      <option key={code} value={code}>
                        {text}
                      </option>
                    ))}
                </select>
              ) : spec.type === "child" ? (
                <select value={raw} onChange={(e) => set(spec.name, e.target.value)}>
                  <option value="">–</option>
                  {childRows.map((row, at) => (
                    <option key={row.id} value={row.id}>
                      {childLabel(row, at)}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={spec.type === "date" ? "date" : spec.type === "month" ? "month" : "text"}
                  inputMode={spec.type === "money" || spec.type === "decimal" ? "decimal" : spec.type === "int" ? "numeric" : undefined}
                  value={raw}
                  onChange={(e) => set(spec.name, e.target.value)}
                  autoComplete="off"
                />
              )}
            </Field>
          );
        })}
      </div>
      <button type="button" className="link" onClick={onRemove}>
        Eintrag entfernen
      </button>
    </fieldset>
  );
}

function Reconfirm({
  rows,
  disposed,
  onChange,
  errors,
}: {
  rows: ReturnInputFact[];
  disposed: Record<string, string | null>;
  onChange: (next: Record<string, string | null>) => void;
  errors: (id: string) => string[];
}) {
  let childIndex = 0;
  return (
    <div className="stack">
      <p className="muted">Aus den Vorjahren erfasst:</p>
      <ul className="fragen-standing">
        {rows.map((row) => {
          const text = describeRow(row, row.kind === "child" ? childIndex++ : 0);
          if (!DISPOSABLE.has(row.kind)) return <li key={row.id}>{text}</li>;
          const since = disposed[row.id];
          const ended = since !== undefined && since !== null;
          const name = `reconfirm-${row.id}`;
          return (
            <li key={row.id} className="stack">
              <strong>{text}</strong>
              <div role="radiogroup" aria-label={text} className="stack">
                <label className="inline">
                  <input type="radio" name={name} checked={!ended} onChange={() => onChange({ ...disposed, [row.id]: null })} />
                  weiter genutzt
                </label>
                <label className="inline">
                  <input type="radio" name={name} checked={ended} onChange={() => onChange({ ...disposed, [row.id]: since ?? "" })} />
                  nicht mehr genutzt seit …
                </label>
              </div>
              {ended ? (
                <Field id={`${name}-since`} label="Nicht mehr genutzt seit" errors={errors(row.id)}>
                  <input type="date" value={since} onChange={(e) => onChange({ ...disposed, [row.id]: e.target.value })} />
                </Field>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// --- the last screen ------------------------------------------------------------------------

function loadEstimate(year: number): Promise<ReturnEstimate | null> {
  return ok(api.GET("/api/tax/return-estimate", { params: { query: { tax_year: year } } })).catch(() => null);
}

function Summary({
  year,
  data,
  steps: list,
  onJump,
}: {
  year: number;
  data: FlowData;
  steps: Step[];
  onJump: (index: number) => void;
}) {
  const load = useCallback(() => loadEstimate(year), [year]);
  const estimate = useLoad(load);
  const { progress, reminders } = data.view;
  const open = list.map((s, at) => [s, at] as const).filter(([s]) => isOpen(data.view, s.slot));
  const result = estimate.data?.result;
  return (
    <div className="fragen-card stack">
      <div className="fragen-progress">
        <progress max={progress.total} value={progress.answered} aria-label={`${progress.answered} von ${progress.total} beantwortet`} />
      </div>
      <h2 className="fragen-question">
        {open.length === 0 ? "Alle Fragen beantwortet" : `${open.length} ${open.length === 1 ? "Frage" : "Fragen"} noch offen`}
      </h2>
      <p>
        {progress.answered} von {progress.total} beantwortet.
      </p>
      {result ? (
        <p>
          {/^-?0+(\.0+)?$/.test(result)
            ? "Voraussichtlich weder Erstattung noch Nachzahlung"
            : result.startsWith("-")
              ? "Voraussichtliche Erstattung"
              : "Voraussichtliche Nachzahlung"}{" "}
          <strong>{formatMoney(result.replace(/^-/, ""))}</strong>{" "}
          <span className="badge">{estimate.data?.status === "certified" ? "laut Bescheinigungen" : "Schätzung"}</span>
        </p>
      ) : null}
      {open.length > 0 ? (
        <ul className="fragen-standing">
          {open.map(([s, at]) => (
            <li key={`${s.slot.key}:${s.slot.owner_id ?? ""}:${s.slot.property_id ?? ""}`}>
              <button type="button" className="link" onClick={() => onJump(at)}>
                {s.group}: {s.question.question_de}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {reminders.length > 0 ? (
        <>
          <h3>Von Hand in ELSTER</h3>
          <ul>
            {reminders.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="fragen-answers">
        <Link className="button-link" href={`/tax/${year}`}>
          Zum Steuerjahr {year}
        </Link>
        {list.length > 0 ? (
          <button type="button" className="secondary" onClick={() => onJump(0)}>
            Zur ersten Frage
          </button>
        ) : null}
      </div>
    </div>
  );
}
