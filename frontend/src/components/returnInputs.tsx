"use client";

// Eingaben of the Anlage N tab (phase-4b.md §10, D97): one block per kind of fact an owner
// types — Arbeitsweg, Homeoffice-Tage, Arbeitszimmer, Arbeitsmittel, Telekommunikation,
// Beiträge, Fortbildung, Sonstiges, Lohnersatzleistungen, weitere Versicherungen, Vorsorge
// manuell. Each save is one append-only return_input row (POST /api/return-inputs): a change
// is a new row naming the current one (supersedes_id), "Entfernen" is a withdrawal repeating
// its value (withdrawn = true). The certificate and the payslips are not typed here: they
// come from their documents (Dokumente › Auslesen) and are listed under Unterlagen.
//
// Nothing here computes a euro figure (principle 1): the forms send what was typed, and the
// list shows the stored facts as typed. Every rule (caps, rates, useful lives) is the API's.
import { useId, useMemo, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { components } from "@/api/types";
import { formatter, translate, useT } from "@/i18n";
import { kindText, returnInputs, workRoomChoice } from "@/i18n/returnInputs";

import { DocumentPicker } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatFraction, formatShare } from "./format";
import { decimalInput, toDecimalString, toFraction, toMoneyString, type Parsed } from "./input";
import { BelegLink } from "./returnInputEvidence";
import { type FormField, type HelpForm } from "./returnInputFields";
import {
  COMMUTE_KIND,
  EQUIPMENT_CLASS,
  options,
  OTHER_EXPENSE_CLASS,
  REPLACEMENT_BENEFIT,
  RETURN_INPUT_KIND,
} from "./labels";

export type ReturnInput = components["schemas"]["ReturnInput"];
type ReturnInputCreate = components["schemas"]["ReturnInputCreate"];
type ReturnInputKind = components["schemas"]["ReturnInputKind"];
type Value = ReturnInputCreate["value"];

// --- the kinds and their fields -------------------------------------------------------------

type FieldType = "money" | "money_optional" | "int" | "text" | "date" | "date_optional" | "select" | "bool" | "fraction" | "area";

/** A field's label, hint and description are in the catalogue (kindText), by its name. */
interface FieldSpec {
  name: string;
  type: FieldType;
  /** The text a new form starts with ("{year}" is the tax year). */
  initial?: string;
  /** The choices of a select, read in the chosen language when the form is drawn. */
  options?: () => [string, string][];
}

interface KindSpec {
  kind: ReturnInputKind;
  /** One current row per owner and year (the API refuses a second; a save supersedes). */
  single: boolean;
  /** An optional Beleg (receipt, contract, benefit letter) to link; its label is kindText's. */
  document?: boolean;
  fields: FieldSpec[];
  /** The stored facts in one line, as typed. */
  summary: (value: Record<string, unknown>) => string;
  /** The tax year of a new row (a work item counts from its purchase year, D108). */
  taxYear?: (value: Record<string, unknown>, year: number) => number;
}

/** A kind's fields, each named as `form`'s fields are named in returnInputFields.ts (D294's field
 * help), so a field without a help entry fails `tsc` here and e2e/field-help.spec.ts there. */
function fieldsOf<F extends HelpForm>(_form: F, list: (FieldSpec & { name: FormField<F> })[]): FieldSpec[] {
  return list;
}

const str = (value: unknown): string => (value == null ? "" : String(value));
const money = (value: unknown): string => formatter().money(value == null ? null : String(value));
const date = (value: unknown): string => formatter().date(str(value));
const area = (value: unknown): string => formatter().area(str(value));
const msg = () => translate(returnInputs);

const KINDS: KindSpec[] = [
  {
    kind: "commute",
    single: false,
    fields: fieldsOf("commute", [
      { name: "kind", type: "select", options: () => options(COMMUTE_KIND), initial: "first_workplace" },
      { name: "workplace_label", type: "text" },
      { name: "period_from", type: "date", initial: "{year}-01-01" },
      { name: "period_to", type: "date", initial: "{year}-12-31" },
      { name: "days_per_week", type: "int", initial: "5" },
      { name: "absent_days", type: "int", initial: "0" },
      { name: "days", type: "int" },
      { name: "km", type: "int" },
      { name: "km_car", type: "int", initial: "0" },
      { name: "km_collective", type: "int", initial: "0" },
      { name: "km_other", type: "int", initial: "0" },
      { name: "public_transport_costs", type: "money_optional" },
    ]),
    summary: (v) =>
      msg().commuteSummary(str(v.workplace_label), date(v.period_from), date(v.period_to), str(v.days), str(v.km)),
  },
  {
    kind: "home_office",
    single: true,
    fields: fieldsOf("home_office", [
      { name: "days_other_workplace", type: "int", initial: "0" },
      { name: "days_no_other_workplace", type: "int", initial: "0" },
    ]),
    summary: (v) => msg().homeOfficeSummary(str(v.days_other_workplace), str(v.days_no_other_workplace)),
  },
  {
    kind: "work_room",
    single: true,
    document: true,
    fields: fieldsOf("work_room", [
      { name: "months", type: "int", initial: "12" },
      { name: "room_m2", type: "area" },
      { name: "home_m2", type: "area" },
      { name: "rent_year", type: "money" },
      { name: "running_costs_year", type: "money", initial: "0" },
      { name: "room_only_costs_year", type: "money", initial: "0" },
      { name: "prefer", type: "select", initial: "", options: () => options(workRoomChoice) },
    ]),
    summary: (v) =>
      msg().workRoomSummary(str(v.months), area(v.room_m2), area(v.home_m2), money(v.rent_year)),
  },
  {
    kind: "work_equipment",
    single: false,
    document: true,
    fields: fieldsOf("work_equipment", [
      { name: "label", type: "text" },
      { name: "item_class", type: "select", options: () => options(EQUIPMENT_CLASS), initial: "computer" },
      { name: "gross", type: "money" },
      { name: "net", type: "money_optional" },
      {
        name: "bought_on",
        type: "date",
        // a change keeps the row's year (the year it counts from), so a new purchase year is a new row
      },
      // "100 %" is parsed by toFraction, which reads the German form
      { name: "professional_share", type: "fraction", initial: "100 %" },
      { name: "useful_life_months", type: "int", initial: "" },
      { name: "disposed_on", type: "date_optional" },
    ]),
    summary: (v) =>
      msg().equipmentSummary(str(v.label), money(v.gross), date(v.bought_on), formatShare(v.professional_share as never)) +
      (v.disposed_on ? msg().disposedSince(date(v.disposed_on)) : ""),
    taxYear: (v, year) => {
      const bought = /^(\d{4})-/.exec(str(v.bought_on));
      return bought ? Number(bought[1]) : year;
    },
  },
  {
    kind: "telecom",
    single: true,
    fields: fieldsOf("telecom", [
      { name: "bills_year", type: "money" },
      { name: "months", type: "int", initial: "12" },
      { name: "employer_reimbursed", type: "money", initial: "0" },
    ]),
    summary: (v) => msg().telecomSummary(money(v.bills_year), str(v.months), money(v.employer_reimbursed)),
  },
  {
    kind: "professional_association",
    single: false,
    document: true,
    fields: fieldsOf("professional_association", [
      { name: "name", type: "text" },
      { name: "amount", type: "money" },
      { name: "is_union", type: "bool", initial: "false" },
    ]),
    summary: (v) => `${str(v.name)} · ${money(v.amount)}${v.is_union ? msg().unionSuffix : ""}`,
  },
  {
    kind: "training",
    single: false,
    document: true,
    fields: fieldsOf("training", [
      { name: "label", type: "text" },
      { name: "amount", type: "money" },
    ]),
    summary: (v) => `${str(v.label)} · ${money(v.amount)}`,
  },
  {
    kind: "other_expense",
    single: false,
    document: true,
    fields: fieldsOf("other_expense", [
      { name: "expense_class", type: "select", options: () => options(OTHER_EXPENSE_CLASS), initial: "account_fee" },
      // stays German in English too: it is the value saved as typed, like any label a person types
      { name: "label", type: "text", initial: "Kontoführung" },
      { name: "amount", type: "money_optional" },
    ]),
    summary: (v) => `${str(v.label)}${v.amount != null ? ` · ${money(v.amount)}` : msg().flatSuffix}`,
  },
  {
    kind: "replacement_income",
    single: false,
    document: true,
    fields: fieldsOf("replacement_income", [
      { name: "benefit", type: "select", options: () => options(REPLACEMENT_BENEFIT), initial: "elterngeld" },
      { name: "amount", type: "money" },
      { name: "period_from", type: "date_optional" },
      { name: "period_to", type: "date_optional" },
    ]),
    summary: (v) =>
      `${REPLACEMENT_BENEFIT[str(v.benefit) as keyof typeof REPLACEMENT_BENEFIT] ?? str(v.benefit)} · ${money(v.amount)}${v.period_from ? msg().periodSuffix(date(v.period_from), date(v.period_to)) : ""}`,
  },
  {
    kind: "other_insurance",
    single: true,
    fields: fieldsOf("other_insurance", [{ name: "amount", type: "money" }]),
    summary: (v) => money(v.amount),
  },
  {
    kind: "vorsorge_override",
    single: true,
    fields: fieldsOf("vorsorge_override", [
      { name: "pension", type: "money" },
      { name: "health_care_basis", type: "money" },
      { name: "other", type: "money", initial: "0" },
      { name: "krankengeld_entitlement", type: "bool", initial: "true" },
    ]),
    summary: (v) => msg().vorsorgeSummary(money(v.pension), money(v.health_care_basis), money(v.other)),
  },
];

/** The kinds the panel offers, in the order of the form. */
export const TYPED_KINDS: ReturnInputKind[] = KINDS.map((k) => k.kind);

export function rowSummary(row: ReturnInput): string {
  const kind = KINDS.find((k) => k.kind === row.kind);
  return kind ? kind.summary(row.value as Record<string, unknown>) : RETURN_INPUT_KIND[row.kind];
}

// --- parsing --------------------------------------------------------------------------------

function parseField(field: FieldSpec, raw: string): Parsed<unknown> {
  const text = raw.trim();
  const t = msg();
  switch (field.type) {
    case "money":
      return toMoneyString(text);
    case "money_optional":
      return text === "" ? { ok: true, value: null } : toMoneyString(text);
    case "int":
      if (text === "" && field.initial === "") return { ok: true, value: null };
      return /^\d{1,6}$/.test(text) ? { ok: true, value: Number(text) } : { ok: false, message: t.notWholeNumber };
    case "text":
      return text === "" ? { ok: false, message: t.required } : { ok: true, value: text };
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { ok: true, value: text } : { ok: false, message: t.notADate };
    case "date_optional":
      if (text === "") return { ok: true, value: null };
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { ok: true, value: text } : { ok: false, message: t.notADate };
    case "select":
      // "" is a choice only where the field offers it; otherwise nothing is picked yet (a
      // pre-filled form whose benefit kind the payments did not name).
      if (text === "" && !(field.options?.() ?? []).some(([code]) => code === "")) {
        return { ok: false, message: t.pleaseSelect };
      }
      return { ok: true, value: text === "" ? null : text };
    case "bool":
      return { ok: true, value: text === "true" };
    case "fraction":
      return toFraction(text);
    case "area":
      return toDecimalString(text, 2);
  }
}

/** The text an input starts with: the stored value when changing a row, else the default. */
function initialText(field: FieldSpec, value: Record<string, unknown> | null, year: number): string {
  if (value) {
    const stored = value[field.name];
    if (stored == null) return "";
    switch (field.type) {
      case "money":
      case "money_optional":
      case "area":
        return decimalInput(String(stored));
      case "fraction":
        return formatFraction(stored as never);
      default:
        return String(stored);
    }
  }
  return (field.initial ?? "").replace("{year}", String(year));
}

function codesOf(error: unknown): string[] {
  return error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
}

function record(body: ReturnInputCreate): Promise<ReturnInput> {
  return ok(api.POST("/api/return-inputs", { body }));
}

// --- the panel ------------------------------------------------------------------------------

/**
 * Eingaben: one block per kind with its current rows (as typed), "ändern" and "Entfernen",
 * and a form to add one. `rows` are the owner's current rows of the year (GET
 * /api/return-inputs?tax_year=&owner_id=); `onChanged` reloads them, the draft and the
 * estimate.
 */
export function InputsPanel({
  ownerId,
  year,
  rows,
  onChanged,
  kinds,
}: {
  ownerId: string;
  year: number;
  rows: ReturnInput[];
  onChanged: () => void;
  /** Only these kinds' blocks (one topic of Eure Angaben, phase 10 D294); all without it. */
  kinds?: readonly string[];
}) {
  const t = useT(returnInputs);
  return (
    <section className="card stack tax-inputs" aria-labelledby="tax-inputs-title">
      <h2 id="tax-inputs-title">{t.inputs}</h2>
      <p className="muted">{t.inputsIntro}</p>
      {KINDS.filter((kind) => !kinds || kinds.includes(kind.kind)).map((kind) => (
        <KindBlock
          key={kind.kind}
          spec={kind}
          ownerId={ownerId}
          year={year}
          rows={rows.filter((row) => row.kind === kind.kind)}
          onChanged={onChanged}
        />
      ))}
    </section>
  );
}

function KindBlock({
  spec: kind,
  ownerId,
  year,
  rows,
  onChanged,
}: {
  spec: KindSpec;
  ownerId: string;
  year: number;
  rows: ReturnInput[];
  onChanged: () => void;
}) {
  // null: no form open; "new": adding; a row id: changing that row.
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const title = RETURN_INPUT_KIND[kind.kind];
  const headingId = useId();
  const t = useT(returnInputs);

  async function withdraw(row: ReturnInput) {
    setBusy(true);
    setBanner(null);
    try {
      await record({
        kind: row.kind,
        tax_year: row.tax_year,
        owner_id: row.owner_id ?? null,
        document_id: row.document_id ?? null,
        value: row.value,
        supersedes_id: row.id,
        withdrawn: true,
      });
    } catch (error) {
      setBanner(
        codesOf(error).includes("return_input.not_current")
          ? t.notCurrent
          : t.removeFailed,
      );
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  const done = () => {
    setEditing(null);
    onChanged();
  };
  const canAdd = !kind.single || rows.length === 0;

  return (
    <div className="tax-input-block" role="group" aria-labelledby={headingId} id={`eingabe-${kind.kind}`}>
      <h3 id={headingId}>{title}</h3>
      {rows.length > 0 ? (
        <ul className="tax-input-rows">
          {rows.map((row) =>
            editing === row.id ? (
              <li key={row.id}>
                <InputForm spec={kind} ownerId={ownerId} year={year} replaces={row} onDone={done} onCancel={() => setEditing(null)} />
              </li>
            ) : (
              <li key={row.id}>
                <span>
                  {rowSummary(row)}
                  {row.tax_year !== year ? <span className="muted">{t.fromYear(row.tax_year)}</span> : null}
                </span>
                <span className="row tax-input-actions">
                  <BelegLink documentId={row.document_id} />
                  <button type="button" className="link" onClick={() => setEditing(row.id)} disabled={busy || editing !== null}>
                    {t.change}
                  </button>
                  <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy || editing !== null}>
                    {t.remove}
                  </button>
                </span>
              </li>
            ),
          )}
        </ul>
      ) : null}
      {editing === "new" ? (
        <InputForm spec={kind} ownerId={ownerId} year={year} replaces={null} onDone={done} onCancel={() => setEditing(null)} />
      ) : canAdd ? (
        <button type="button" className="secondary" onClick={() => setEditing("new")} disabled={busy || editing !== null}>
          {kind.single ? t.addSingle(title) : t.addMany(title)}
        </button>
      ) : null}
      {banner ? <p className="notice notice-error">{banner}</p> : null}
    </div>
  );
}

/** Rule codes the API sends for one field, shown under it. */
const CODE_FIELDS: Record<string, string> = {
  "anlage_n.useful_life_missing": "value.useful_life_months",
  "return_input.work_room_conflict": "value.months",
};

function InputForm({
  spec: kind,
  ownerId,
  year,
  replaces,
  onDone,
  onCancel,
}: {
  spec: KindSpec;
  ownerId: string;
  year: number;
  replaces: ReturnInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const old = (replaces?.value as Record<string, unknown> | undefined) ?? null;
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(kind.fields.map((field) => [field.name, initialText(field, old, year)])),
  );
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const keys = useMemo(() => [...kind.fields.map((field) => `value.${field.name}`), "document_id"], [kind]);
  const form = useFormErrors(keys, CODE_FIELDS);
  const base = useId();
  const t = useT(returnInputs);
  const kindTexts = kindText[kind.kind];
  const set = (name: string, text: string) => setTexts((current) => ({ ...current, [name]: text }));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const value: Record<string, unknown> = {};
    for (const field of kind.fields) {
      const parsed = values.take(`value.${field.name}`, parseField(field, texts[field.name] ?? ""));
      if (parsed !== undefined) value[field.name] = parsed;
    }
    if (!values.ok) return form.invalid(values.messages);
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record({
        kind: kind.kind,
        // a change keeps its row's year (the API checks kind, year and owner match)
        tax_year: replaces?.tax_year ?? kind.taxYear?.(value, year) ?? year,
        owner_id: ownerId,
        document_id: kind.document ? documentId : null,
        value: value as Value,
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      if (codesOf(error).includes("return_input.not_current")) {
        setBanner(
          replaces === null
            ? t.alreadyRecorded
            : t.changedMeanwhile,
        );
      } else {
        form.fail(error);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack tax-input-form" onSubmit={submit} noValidate aria-label={replaces ? t.formChange(RETURN_INPUT_KIND[kind.kind]) : t.formEnter(RETURN_INPUT_KIND[kind.kind])}>
      <p className="muted hint">{kindTexts?.help}</p>
      <div className="grid">
        {kind.fields.map((field) => (
          <Field
            key={field.name}
            id={`${base}-${field.name}`}
            label={kindTexts?.labels[field.name] ?? field.name}
            hint={kindTexts?.hints?.[field.name]}
            description={kindTexts?.descriptions?.[field.name]}
            errors={form.of(`value.${field.name}`)}
          >
            {field.type === "select" ? (
              <select value={texts[field.name] ?? ""} onChange={(e) => set(field.name, e.target.value)}>
                {texts[field.name] === "" && !(field.options?.() ?? []).some(([code]) => code === "") ? (
                  <option value="">{t.pleaseChoose}</option>
                ) : null}
                {(field.options?.() ?? []).map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </select>
            ) : field.type === "bool" ? (
              <select value={texts[field.name] ?? "false"} onChange={(e) => set(field.name, e.target.value)}>
                <option value="true">{t.yes}</option>
                <option value="false">{t.no}</option>
              </select>
            ) : (
              <input
                type={field.type === "date" || field.type === "date_optional" ? "date" : "text"}
                inputMode={field.type === "text" ? undefined : field.type === "int" ? "numeric" : "decimal"}
                value={texts[field.name] ?? ""}
                onChange={(e) => set(field.name, e.target.value)}
                autoComplete="off"
                maxLength={field.type === "text" ? 120 : undefined}
              />
            )}
          </Field>
        ))}
        {kind.document ? (
          <Field id={`${base}-document`} label={kindTexts?.document ?? ""} errors={form.of("document_id")}>
            <DocumentPicker value={documentId} onChange={setDocumentId} />
          </Field>
        ) : null}
      </div>
      {replaces !== null ? <p className="muted hint">{t.kept}</p> : null}
      <div className="row">
        <Submit busy={busy}>{t.save}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}
