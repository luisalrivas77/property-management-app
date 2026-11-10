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

import { DocumentPicker } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatArea, formatDate, formatFraction, formatMoney, formatShare } from "./format";
import { decimalInput, toDecimalString, toFraction, toMoneyString, type Parsed } from "./input";
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

interface FieldSpec {
  name: string;
  label: string;
  type: FieldType;
  /** The text a new form starts with ("{year}" is the tax year). */
  initial?: string;
  options?: [string, string][];
  hint?: string;
  description?: string;
}

interface KindSpec {
  kind: ReturnInputKind;
  /** One current row per owner and year (the API refuses a second; a save supersedes). */
  single: boolean;
  /** An optional Beleg (receipt, contract, benefit letter) to link. */
  document?: string;
  help: string;
  fields: FieldSpec[];
  /** The stored facts in one line, as typed. */
  summary: (value: Record<string, unknown>) => string;
  /** The tax year of a new row (a work item counts from its purchase year, D108). */
  taxYear?: (value: Record<string, unknown>, year: number) => number;
}

const str = (value: unknown): string => (value == null ? "" : String(value));
const money = (value: unknown): string => formatMoney(value == null ? null : String(value));

const KINDS: KindSpec[] = [
  {
    kind: "commute",
    single: false,
    help: "Ein Weg zur Arbeit für einen Zeitraum. Ein Wechsel der Arbeitsstätte im Jahr ist ein zweiter Eintrag.",
    fields: [
      { name: "kind", label: "Art der Arbeitsstätte", type: "select", options: options(COMMUTE_KIND), initial: "first_workplace" },
      { name: "workplace_label", label: "Arbeitsstätte (Ort, Straße)", type: "text" },
      { name: "period_from", label: "Von", type: "date", initial: "{year}-01-01" },
      { name: "period_to", label: "Bis", type: "date", initial: "{year}-12-31" },
      { name: "days_per_week", label: "Arbeitstage je Woche", type: "int", initial: "5" },
      {
        name: "absent_days",
        label: "Urlaubs-, Krankheits-, Homeoffice- und Dienstreisetage",
        type: "int",
        initial: "0",
      },
      { name: "days", label: "Tage an der Arbeitsstätte", type: "int", description: "An wie vielen Tagen Sie tatsächlich hingefahren sind." },
      { name: "km", label: "Einfache Entfernung (volle km)", type: "int", description: "Die kürzeste Straßenverbindung, eine Strecke." },
      { name: "km_car", label: "davon mit dem Auto (km)", type: "int", initial: "0" },
      { name: "km_collective", label: "davon mit Sammelbeförderung des Arbeitgebers (km)", type: "int", initial: "0" },
      {
        name: "km_other",
        label: "davon mit Bus, Bahn, Rad oder zu Fuß (km)",
        type: "int",
        initial: "0",
        hint: "Die drei Teilstrecken ergeben zusammen die Entfernung.",
      },
      { name: "public_transport_costs", label: "Kosten für Bus und Bahn im Zeitraum (optional)", type: "money_optional" },
    ],
    summary: (v) =>
      `${str(v.workplace_label)} · ${formatDate(str(v.period_from))} bis ${formatDate(str(v.period_to))} · ${str(v.days)} Tage, ${str(v.km)} km`,
  },
  {
    kind: "home_office",
    single: true,
    help: "Die Homeoffice-Tagespauschale: Tage, an denen Sie überwiegend zu Hause gearbeitet haben.",
    fields: [
      {
        name: "days_other_workplace",
        label: "Tage zu Hause, obwohl ein Arbeitsplatz beim Arbeitgeber da war",
        type: "int",
        initial: "0",
      },
      {
        name: "days_no_other_workplace",
        label: "Tage zu Hause ohne anderen Arbeitsplatz (z. B. Lehrkräfte)",
        type: "int",
        initial: "0",
      },
    ],
    summary: (v) => `${str(v.days_other_workplace)} Tage mit Arbeitsplatz beim Arbeitgeber, ${str(v.days_no_other_workplace)} Tage ohne`,
  },
  {
    kind: "work_room",
    single: true,
    document: "Mietvertrag oder Grundriss (optional)",
    help: "Ein häusliches Arbeitszimmer, das Mittelpunkt der gesamten beruflichen Tätigkeit ist. Der Entwurf vergleicht die Jahrespauschale mit den anteiligen tatsächlichen Kosten.",
    fields: [
      { name: "months", label: "Monate genutzt", type: "int", initial: "12" },
      { name: "room_m2", label: "Zimmer (m²)", type: "area" },
      { name: "home_m2", label: "Wohnung (m²)", type: "area" },
      { name: "rent_year", label: "Jahresmiete (oder Kosten des Eigentums)", type: "money" },
      { name: "running_costs_year", label: "Nebenkosten im Jahr", type: "money", initial: "0" },
      { name: "room_only_costs_year", label: "Kosten nur fürs Zimmer (z. B. Renovierung)", type: "money", initial: "0" },
      {
        name: "prefer",
        label: "Wahl",
        type: "select",
        initial: "",
        options: [
          ["", "das Günstigere (empfohlen)"],
          ["flat", "Jahrespauschale"],
          ["actual", "tatsächliche Kosten"],
        ],
      },
    ],
    summary: (v) =>
      `${str(v.months)} Monate · ${formatArea(str(v.room_m2))} von ${formatArea(str(v.home_m2))} · Miete ${money(v.rent_year)}`,
  },
  {
    kind: "work_equipment",
    single: false,
    document: "Rechnung (optional)",
    help: "Ein Arbeitsmittel oder Abo, auch aus Vorjahren: teure Geräte werden über ihre Nutzungsdauer abgeschrieben, der Entwurf rechnet den Anteil dieses Jahres.",
    fields: [
      { name: "label", label: "Bezeichnung", type: "text" },
      { name: "item_class", label: "Art", type: "select", options: options(EQUIPMENT_CLASS), initial: "computer" },
      { name: "gross", label: "Preis brutto", type: "money" },
      { name: "net", label: "Preis netto laut Rechnung (optional)", type: "money_optional" },
      { name: "bought_on", label: "Kaufdatum (auch Vorjahre)", type: "date" },
      {
        name: "professional_share",
        label: "Beruflicher Anteil",
        type: "fraction",
        initial: "100 %",
        hint: "Als Prozent oder Bruch, z. B. 50 % oder 1/2.",
      },
      {
        name: "useful_life_months",
        label: "Nutzungsdauer in Monaten (nur bei Sonstiges nötig)",
        type: "int",
        initial: "",
      },
      { name: "disposed_on", label: "Nicht mehr genutzt seit (optional)", type: "date_optional" },
    ],
    summary: (v) =>
      `${str(v.label)} · ${money(v.gross)} · gekauft ${formatDate(str(v.bought_on))} · beruflich ${formatShare(v.professional_share as never)}${v.disposed_on ? ` · nicht mehr genutzt seit ${formatDate(str(v.disposed_on))}` : ""}`,
    taxYear: (v, year) => {
      const bought = /^(\d{4})-/.exec(str(v.bought_on));
      return bought ? Number(bought[1]) : year;
    },
  },
  {
    kind: "telecom",
    single: true,
    help: "Telefon- und Internetkosten: ohne Einzelnachweis zählt ein Anteil der Rechnungen bis zu einer Obergrenze.",
    fields: [
      { name: "bills_year", label: "Rechnungen im Jahr", type: "money" },
      { name: "months", label: "Monate", type: "int", initial: "12" },
      { name: "employer_reimbursed", label: "Vom Arbeitgeber erstattet", type: "money", initial: "0" },
    ],
    summary: (v) => `${money(v.bills_year)} in ${str(v.months)} Monaten · erstattet ${money(v.employer_reimbursed)}`,
  },
  {
    kind: "professional_association",
    single: false,
    document: "Beitragsbescheinigung (optional)",
    help: "Beiträge zu Gewerkschaften und Berufsverbänden. Gewerkschaftsbeiträge zählen ab 2026 zusätzlich zum Pauschbetrag.",
    fields: [
      { name: "name", label: "Verband", type: "text" },
      { name: "amount", label: "Beiträge im Jahr", type: "money" },
      { name: "is_union", label: "Gewerkschaft", type: "bool", initial: "false" },
    ],
    summary: (v) => `${str(v.name)} · ${money(v.amount)}${v.is_union ? " · Gewerkschaft" : ""}`,
  },
  {
    kind: "training",
    single: false,
    document: "Rechnung (optional)",
    help: "Kurse, Fachbücher, Prüfungsgebühren, soweit der Arbeitgeber sie nicht erstattet.",
    fields: [
      { name: "label", label: "Bezeichnung", type: "text" },
      { name: "amount", label: "Betrag", type: "money" },
    ],
    summary: (v) => `${str(v.label)} · ${money(v.amount)}`,
  },
  {
    kind: "other_expense",
    single: false,
    document: "Beleg (optional)",
    help: "Weitere Werbungskosten, z. B. die Kontoführungspauschale oder Bewerbungskosten.",
    fields: [
      { name: "expense_class", label: "Art", type: "select", options: options(OTHER_EXPENSE_CLASS), initial: "account_fee" },
      { name: "label", label: "Bezeichnung", type: "text", initial: "Kontoführung" },
      {
        name: "amount",
        label: "Betrag (bei der Kontoführungspauschale leer lassen)",
        type: "money_optional",
      },
    ],
    summary: (v) => `${str(v.label)}${v.amount != null ? ` · ${money(v.amount)}` : " · Pauschale"}`,
  },
  {
    kind: "replacement_income",
    single: false,
    document: "Bescheid (optional)",
    help: "Elterngeld, Mutterschaftsgeld der Krankenkasse, Krankengeld oder Arbeitslosengeld: steuerfrei, erhöht aber den Steuersatz (Progressionsvorbehalt). Den Zuschuss des Arbeitgebers zum Mutterschaftsgeld nicht hier eintragen: er steht auf der Lohnsteuerbescheinigung.",
    fields: [
      { name: "benefit", label: "Leistung", type: "select", options: options(REPLACEMENT_BENEFIT), initial: "elterngeld" },
      { name: "amount", label: "Betrag im Jahr", type: "money" },
      { name: "period_from", label: "Von (optional)", type: "date_optional" },
      { name: "period_to", label: "Bis (optional)", type: "date_optional" },
    ],
    summary: (v) =>
      `${REPLACEMENT_BENEFIT[str(v.benefit) as keyof typeof REPLACEMENT_BENEFIT] ?? str(v.benefit)} · ${money(v.amount)}${v.period_from ? ` · ${formatDate(str(v.period_from))} bis ${formatDate(str(v.period_to))}` : ""}`,
  },
  {
    kind: "other_insurance",
    single: true,
    help: "Beiträge zu Haftpflicht-, Unfall- und Berufsunfähigkeitsversicherungen im Jahr.",
    fields: [{ name: "amount", label: "Beiträge im Jahr", type: "money" }],
    summary: (v) => money(v.amount),
  },
  {
    kind: "vorsorge_override",
    single: true,
    help: "Ersetzt die Vorsorgeaufwendungen aus Bescheinigung oder Schätzung ganz, z. B. bei privater Krankenversicherung.",
    fields: [
      { name: "pension", label: "Rentenversicherung (Arbeitnehmeranteil)", type: "money" },
      { name: "health_care_basis", label: "Kranken- und Pflegeversicherung (Basis)", type: "money" },
      { name: "other", label: "Weitere Vorsorge", type: "money", initial: "0" },
      { name: "krankengeld_entitlement", label: "Anspruch auf Krankengeld", type: "bool", initial: "true" },
    ],
    summary: (v) => `Rente ${money(v.pension)} · Kranken/Pflege ${money(v.health_care_basis)} · weitere ${money(v.other)}`,
  },
];

/** The kinds the panel offers, in the order of the form. */
export const TYPED_KINDS: ReturnInputKind[] = KINDS.map((k) => k.kind);

export function rowSummary(row: ReturnInput): string {
  const kind = KINDS.find((k) => k.kind === row.kind);
  return kind ? kind.summary(row.value as Record<string, unknown>) : RETURN_INPUT_KIND[row.kind];
}

// --- parsing --------------------------------------------------------------------------------

const NOT_A_WHOLE_NUMBER = "Bitte eine ganze Zahl eingeben, z. B. 220.";
const NOT_A_DATE = "Bitte ein Datum wählen.";
const REQUIRED = "Bitte ausfüllen.";

function parseField(field: FieldSpec, raw: string): Parsed<unknown> {
  const text = raw.trim();
  switch (field.type) {
    case "money":
      return toMoneyString(text);
    case "money_optional":
      return text === "" ? { ok: true, value: null } : toMoneyString(text);
    case "int":
      if (text === "" && field.initial === "") return { ok: true, value: null };
      return /^\d{1,6}$/.test(text) ? { ok: true, value: Number(text) } : { ok: false, message: NOT_A_WHOLE_NUMBER };
    case "text":
      return text === "" ? { ok: false, message: REQUIRED } : { ok: true, value: text };
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { ok: true, value: text } : { ok: false, message: NOT_A_DATE };
    case "date_optional":
      if (text === "") return { ok: true, value: null };
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { ok: true, value: text } : { ok: false, message: NOT_A_DATE };
    case "select":
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
}: {
  ownerId: string;
  year: number;
  rows: ReturnInput[];
  onChanged: () => void;
}) {
  return (
    <section className="card stack tax-inputs" aria-labelledby="tax-inputs-title">
      <h2 id="tax-inputs-title">Eingaben</h2>
      <p className="muted">
        Jede Angabe wird mit Datum gespeichert; eine Änderung ersetzt sie, die bisherige bleibt im Verlauf.
        Lohnsteuerbescheinigung und Gehaltsabrechnungen kommen aus ihren Dokumenten (Dokumente › Auslesen).
      </p>
      {KINDS.map((kind) => (
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
          ? "Die Angabe wurde inzwischen geändert; die Anzeige ist neu geladen."
          : "Das Entfernen hat nicht geklappt. Bitte erneut versuchen.",
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
    <div className="tax-input-block" role="group" aria-labelledby={headingId}>
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
                  {row.tax_year !== year ? <span className="muted"> (aus {row.tax_year})</span> : null}
                </span>
                <span className="row tax-input-actions">
                  <button type="button" className="link" onClick={() => setEditing(row.id)} disabled={busy || editing !== null}>
                    ändern
                  </button>
                  <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy || editing !== null}>
                    Entfernen
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
          {kind.single ? `${title} eintragen` : `${title} hinzufügen`}
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
            ? "Für dieses Jahr ist schon ein Eintrag erfasst. Bitte abbrechen; die Anzeige zeigt ihn nach dem Neuladen."
            : "Die Angabe wurde inzwischen geändert. Bitte abbrechen und erneut ändern.",
        );
      } else {
        form.fail(error);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack tax-input-form" onSubmit={submit} noValidate aria-label={`${RETURN_INPUT_KIND[kind.kind]} ${replaces ? "ändern" : "eintragen"}`}>
      <p className="muted hint">{kind.help}</p>
      <div className="grid">
        {kind.fields.map((field) => (
          <Field
            key={field.name}
            id={`${base}-${field.name}`}
            label={field.label}
            hint={field.hint}
            description={field.description}
            errors={form.of(`value.${field.name}`)}
          >
            {field.type === "select" ? (
              <select value={texts[field.name] ?? ""} onChange={(e) => set(field.name, e.target.value)}>
                {(field.options ?? []).map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </select>
            ) : field.type === "bool" ? (
              <select value={texts[field.name] ?? "false"} onChange={(e) => set(field.name, e.target.value)}>
                <option value="true">ja</option>
                <option value="false">nein</option>
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
          <Field id={`${base}-document`} label={kind.document} errors={form.of("document_id")}>
            <DocumentPicker value={documentId} onChange={setDocumentId} />
          </Field>
        ) : null}
      </div>
      {replaces !== null ? <p className="muted hint">Die bisherige Angabe bleibt im Verlauf.</p> : null}
      <div className="row">
        <Submit busy={busy}>Speichern</Submit>
        <button type="button" className="link" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}
