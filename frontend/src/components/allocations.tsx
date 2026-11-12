"use client";

// Zuordnung (docs/phases/phase-2.md §8.1, §8.3, D25, D38): the side panel for one bank line.
//
// - GET /transactions/{id}/allocations gives the line, its current rows (proposed by a rule, a
//   matcher, a Beleg or the model; or confirmed; or none) and whether the answer may become a
//   rule. The panel loads it itself, so it opens for an inbox item and for an already
//   confirmed line from Konten › Umsätze alike; a correction is just another PUT.
// - The rows are editable: amount, category (the fixed taxonomy), property and, under it, unit,
//   tenancy and loan, service period, recurring, Beleg. Rows can be added and removed.
// - "Rest" = the line's amount minus the rows, in integer cents from the decimal strings (never
//   a float on euros). Bestätigen stays disabled until every amount parses and Rest is exactly
//   0. It is an input aid only: the server's sum check (allocation.sum_mismatch) decides.
// - "Künftig immer so zuordnen" only when can_learn_rule. Until the person ticks or unticks it,
//   it follows the rows (§8.1): on for one row whose category is not among
//   no_default_rule_categories (repairs, renovation, anything capitalized, the catch-alls),
//   off otherwise. Several rows (D51: a split the server learns as as_last_split, "wie
//   zuletzt aufgeteilt") start on when every category is set and not among those, there are
//   two or more different ones and none is loan interest or repayment (the loan schedule
//   learns those) or rent or its prepayment (the tenancy's schedule, or no rule: review F2),
//   since the server learns no copy of those and rule.not_learnable would refuse the whole
//   answer; off otherwise, and the person may still tick it. A line that arrives with rows
//   starts from the server's learn_rule_default and follows the rows once they are edited.
//   Otherwise an optional purpose pattern can still make a rule (D38). Phase 6 D160: for one
//   row whose category is among unlearnable_categories (a private account's transfer, card
//   bill, tax-relevant or depot code, or one without a threshold), no rule is ever learned, so
//   the box (or the pattern field) is greyed and unticked with one line saying why, and
//   learn_rule is sent false; rule.not_learnable would refuse the whole answer. learn_rule is always
//   sent explicitly: a typed pattern means "make a rule", whatever the server default for the
//   category would be.
// - D49: one IBAN can send two kinds of payment (the Hausverwaltung's Hausgeld and its fee).
//   When the box is ticked for one row and the server lists other amounts still waiting from
//   the same IBAN (waiting_other_amounts), the panel asks whether those belong to the same
//   category: "Ja, alle so zuordnen" learns a rule for any amount, "Nein, nur Zahlungen über
//   …" one with same_amount_only. Nothing is preselected and Bestätigen stays disabled until
//   one is chosen; the answer holds for the category it was given for, so changing the
//   category asks again. Without that list there is no question and same_amount_only is false.
// - Phase 3 (§11 "Inputs"): under a confirmed line, its repairs and renovation rows get their
//   tax questions (components/taxInputs.tsx), in their own form beside this one.
import Link from "next/link";
import { useCallback, useId, useMemo, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";

import { DocumentPicker } from "./documents";
import { loadDirectory, partyNames, type Directory } from "./entities";
import { collector, Field, FormBanner, useFormErrors, type FieldMessages } from "./form";
import { formatDate, formatDateTime, formatMoney } from "./format";
import { decimalInput, toSignedMoneyString } from "./input";
import { ALLOCATION_CATEGORY, ALLOCATION_METHOD, categoryGroups, TAX_RELEVANT_CATEGORIES } from "./labels";
import { AllocationTaxInputs } from "./taxInputs";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

// Step 4's schemas have no alias in schemas.ts yet.
type Schemas = components["schemas"];
type Account = Schemas["Account"];
export type Allocation = Schemas["Allocation"];
export type AllocationCategory = Schemas["AllocationCategory"];
export type AllocationDraft = Schemas["AllocationDraft"];
export type TransactionAllocations = Schemas["TransactionAllocations"];
export type ConfirmAllocationsBody = Schemas["ConfirmAllocationsBody"];

// --- money in cents ------------------------------------------------------------------------

const MONEY = /^(-?)(\d+)(?:\.(\d{1,2}))?$/;

/** "-310.00" → -31000n; null for anything that is not a decimal with at most two places. */
export function toCents(value: string): bigint | null {
  const match = MONEY.exec(value);
  if (!match) return null;
  const [, sign, euros = "0", cents = ""] = match;
  const total = BigInt(euros) * 100n + BigInt(cents.padEnd(2, "0"));
  return sign ? -total : total;
}

/** -31000n → "-310.00". */
export function centsToString(cents: bigint): string {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  return `${negative ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

// --- rows ----------------------------------------------------------------------------------

interface Row {
  key: number;
  /** As typed, German ("-180,00"); signed like the line: a debit's parts are negative. */
  amount: string;
  category: AllocationCategory | "";
  propertyId: string;
  unitId: string;
  tenancyId: string;
  loanId: string;
  periodStart: string;
  periodEnd: string;
  recurring: boolean;
  documentId: string | null;
}

let nextKey = 1;

function emptyRow(amount: string, propertyId: string): Row {
  return {
    key: nextKey++,
    amount,
    category: "",
    propertyId,
    unitId: "",
    tenancyId: "",
    loanId: "",
    periodStart: "",
    periodEnd: "",
    recurring: false,
    documentId: null,
  };
}

function rowOf(allocation: Allocation): Row {
  return {
    key: nextKey++,
    amount: decimalInput(allocation.amount),
    category: allocation.category,
    propertyId: allocation.property_id ?? "",
    unitId: allocation.unit_id ?? "",
    tenancyId: allocation.tenancy_id ?? "",
    loanId: allocation.loan_id ?? "",
    periodStart: allocation.service_period_start ?? "",
    periodEnd: allocation.service_period_end ?? "",
    recurring: allocation.recurring,
    documentId: allocation.document_id,
  };
}

/** The rows a line starts with: its current allocations, else one row over the whole amount
 * (on the household's only property, if it has exactly one and the line is the rental
 * account's; a private line starts without one, phase 6 D159). */
function initialRows(view: TransactionAllocations, directory: Directory, account: Account): Row[] {
  if (view.allocations.length > 0) return view.allocations.map(rowOf);
  const only =
    account.purpose === "rental" && directory.properties.length === 1 ? (directory.properties[0]?.id ?? "") : "";
  return [emptyRow(decimalInput(view.transaction.amount), only)];
}

/** A row's amount in cents, or null when it does not parse. */
function rowCents(row: Row): bigint | null {
  const parsed = toSignedMoneyString(row.amount);
  return parsed.ok ? toCents(parsed.value) : null;
}

/** The line's amount minus the rows, or null while any row's amount does not parse. */
const NO_SPLIT_COPY: ReadonlySet<AllocationCategory> = new Set<AllocationCategory>([
  "loan_interest",
  "loan_principal",
  "rent_cold",
  "operating_costs_prepayment",
]);

/** D51: whether a split starts with the rule box ticked, as the server's learn_rule_default
 * would for an as_last_split answer. */
function splitSuggested(rows: Row[], noDefault: ReadonlySet<AllocationCategory>): boolean {
  const categories = rows.map((row) => row.category);
  if (categories.some((category) => category === "" || noDefault.has(category))) return false;
  // Loan interest and repayment move with the schedule; rent is split by its tenancy's schedule
  // or not learned at all (review F2). The server learns no copy of either.
  if (categories.some((category) => category !== "" && NO_SPLIT_COPY.has(category))) return false;
  return new Set(categories).size >= 2;
}

function restOf(lineAmount: string, rows: Row[]): bigint | null {
  let rest = toCents(lineAmount);
  if (rest === null) return null;
  for (const row of rows) {
    const cents = rowCents(row);
    if (cents === null) return null;
    rest -= cents;
  }
  return rest;
}

const ROW_FIELDS = [
  "amount",
  "category",
  "property_id",
  "unit_id",
  "tenancy_id",
  "loan_id",
  "service_period_start",
  "service_period_end",
  "recurring",
  "document_id",
] as const;

const rowKey = (index: number, field: (typeof ROW_FIELDS)[number]) => `allocation_set.allocations.${index}.${field}`;

// --- the panel -------------------------------------------------------------------------------

interface Loaded {
  view: TransactionAllocations;
  directory: Directory;
  /** The line's account: its purpose and kind decide the categories offered (phase 6 D159). */
  account: Account;
}

/**
 * The line and its editable split. `reasoning` / `question` are the inbox item's (an unmatched
 * line has no rows to carry them); a proposed row's own reasoning and question win.
 * `onSaved` gets the PUT's answer (the confirmed rows and the rule it created, if any).
 */
export function AllocationPanel({
  transactionId,
  reasoning,
  question,
  onSaved,
}: {
  transactionId: string;
  reasoning?: string | null;
  question?: string | null;
  onSaved: (result: TransactionAllocations) => void;
}) {
  const load = useCallback(async (): Promise<Loaded> => {
    const [view, directory] = await Promise.all([
      ok(api.GET("/api/transactions/{transaction_id}/allocations", { params: { path: { transaction_id: transactionId } } })),
      loadDirectory(),
    ]);
    const account = await ok(
      api.GET("/api/accounts/{account_id}", { params: { path: { account_id: view.transaction.account_id } } }),
    );
    return { view, directory, account };
  }, [transactionId]);
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {({ view, directory, account }) => (
        <>
          {/* Keyed by the line, so another line starts with its own rows. */}
          <AllocationEditor
            key={view.transaction.id}
            view={view}
            directory={directory}
            account={account}
            reasoning={reasoning}
            question={question}
            onSaved={onSaved}
          />
          <AllocationTaxInputs allocations={view.allocations} />
        </>
      )}
    </Loading>
  );
}

/** "-380.00" → "380.00": the question names amounts without their sign ("Zahlungen über 380,00 €"). */
function unsigned(amount: string): string {
  return amount.startsWith("-") ? amount.slice(1) : amount;
}

function isCredit(amount: string): boolean {
  return !amount.startsWith("-") && !/^0+(\.0+)?$/.test(amount);
}

function AllocationEditor({
  view,
  directory,
  account,
  reasoning,
  question,
  onSaved,
}: {
  view: TransactionAllocations;
  directory: Directory;
  account: Account;
  reasoning?: string | null;
  question?: string | null;
  onSaved: (result: TransactionAllocations) => void;
}) {
  const line = view.transaction;
  const current = view.allocations;
  const proposed = current.length > 0 && current[0]?.status === "proposed";
  const confirmed = current.length > 0 && current[0]?.status === "confirmed";
  const shownReasoning = (proposed ? current[0]?.reasoning : null) ?? reasoning ?? null;
  const shownQuestion = (proposed ? current[0]?.question : null) ?? question ?? null;

  const [rows, setRows] = useState<Row[]>(() => initialRows(view, directory, account));
  // null = not touched: the box follows the rows (see the header).
  const [learnChoice, setLearnChoice] = useState<boolean | null>(null);
  const [rowsEdited, setRowsEdited] = useState(false);
  const [pattern, setPattern] = useState("");
  // D49: the answer to "Gehören die auch zu …?", for the category it was given for.
  const [sameAmount, setSameAmount] = useState<{ category: AllocationCategory; only: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const learnId = useId();
  const patternId = useId();
  const allId = useId();
  const onlyId = useId();

  const keys = useMemo(
    () => [
      ...rows.flatMap((_, index) => ROW_FIELDS.map((field) => rowKey(index, field))),
      "learn_rule",
      "purpose_pattern",
    ],
    [rows],
  );
  const ruleField = view.can_learn_rule ? "learn_rule" : "purpose_pattern";
  const form = useFormErrors(keys, { "rule.invalid_pattern": "purpose_pattern", "rule.not_learnable": ruleField });

  const rest = restOf(line.amount, rows);
  const balanced = rest === 0n;

  const noDefault = new Set(view.no_default_rule_categories);
  // A split on a private account never becomes a rule (D160 learns one row only), so its box
  // starts unticked there.
  const suggested =
    rows.length === 1
      ? rows.every((row) => row.category === "" || !noDefault.has(row.category))
      : account.purpose === "rental" && splitSuggested(rows, noDefault);
  // D160: one row of a category no rule is ever learned for: the box is greyed and off.
  const unlearnable = new Set(view.unlearnable_categories ?? []);
  const onlyCategory = rows.length === 1 ? (rows[0]?.category ?? "") : "";
  const blocked = onlyCategory !== "" && unlearnable.has(onlyCategory) ? onlyCategory : null;
  const learn = blocked !== null ? false : (learnChoice ?? (current.length === 0 || rowsEdited ? suggested : view.learn_rule_default));

  const others = view.waiting_other_amounts ?? [];
  const askedCategory = rows.length === 1 ? (rows[0]?.category ?? "") : "";
  const asked = view.can_learn_rule && learn && others.length > 0 && askedCategory !== "" ? askedCategory : null;
  const sameAmountOnly = asked !== null && sameAmount !== null && sameAmount.category === asked ? sameAmount.only : null;
  const unanswered = asked !== null && sameAmountOnly === null;

  function update(key: number, patch: Partial<Row>) {
    setRowsEdited(true);
    setRows((list) => list.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function addRow() {
    const remaining = rest === null || rest === 0n ? "" : decimalInput(centsToString(rest));
    const property = rows[rows.length - 1]?.propertyId ?? "";
    setRowsEdited(true);
    setRows((list) => [...list, emptyRow(remaining, property)]);
  }

  function removeRow(key: number) {
    setRowsEdited(true);
    setRows((list) => (list.length > 1 ? list.filter((row) => row.key !== key) : list));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (unanswered) return;
    const values = collector();
    const messages: FieldMessages = {};
    const drafts: AllocationDraft[] = [];
    rows.forEach((row, index) => {
      const amount = values.take(rowKey(index, "amount"), toSignedMoneyString(row.amount));
      if (row.category === "") (messages[rowKey(index, "category")] ??= []).push("Bitte eine Kategorie wählen.");
      if (amount === undefined || row.category === "") return;
      drafts.push({
        amount,
        category: row.category,
        property_id: row.propertyId || null,
        unit_id: row.unitId || null,
        tenancy_id: row.tenancyId || null,
        loan_id: row.loanId || null,
        service_period_start: row.periodStart || null,
        service_period_end: row.periodEnd || null,
        recurring: row.recurring,
        document_id: row.documentId,
      });
    });
    if (!values.ok || Object.keys(messages).length > 0) return form.invalid({ ...values.messages, ...messages });
    const typed = pattern.trim();
    const body: ConfirmAllocationsBody = {
      // The server reads only the rows: method, confidence and provenance become the person's.
      allocation_set: { transaction_id: line.id, allocations: drafts, method: "user", confidence: "1" },
      learn_rule: view.can_learn_rule ? learn : typed !== "" && blocked === null,
      purpose_pattern: view.can_learn_rule || typed === "" || blocked !== null ? null : typed,
      same_amount_only: asked !== null && sameAmountOnly === true,
    };
    setBusy(true);
    form.clear();
    try {
      const result = await ok(
        api.PUT("/api/transactions/{transaction_id}/allocations", {
          params: { path: { transaction_id: line.id } },
          body,
        }),
      );
      onSaved(result);
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack allocation-editor" onSubmit={submit} noValidate>
      {proposed ? (
        <p className="allocation-source">
          <span className="ai-badge">{current[0]?.method === "llm" ? "KI" : "Auto"}</span>
          Vorschlag: {ALLOCATION_METHOD[current[0]?.method ?? "user"]}
        </p>
      ) : null}
      <div>
        <h2 className="allocation-title">
          {line.counterparty_name ?? "Ohne Gegenseite"} ·{" "}
          <span className={isCredit(line.amount) ? "amount-in" : undefined}>
            {isCredit(line.amount) ? "+" : ""}
            {formatMoney(line.amount)}
          </span>
        </h2>
        <p className="cell-detail">
          Buchungstag {formatDate(line.booking_date)}
          {line.value_date && line.value_date !== line.booking_date ? ` · Wert ${formatDate(line.value_date)}` : ""}
          {line.counterparty_iban_masked ? ` · ${line.counterparty_iban_masked}` : ""}
        </p>
        <p className="allocation-purpose">{line.purpose || "Kein Verwendungszweck"}</p>
        {line.booking_text ? <p className="cell-detail">{line.booking_text}</p> : null}
      </div>
      {shownReasoning ? <p className="allocation-reasoning">{shownReasoning}</p> : null}
      {shownQuestion ? <p className="allocation-question">{shownQuestion}</p> : null}
      {confirmed ? (
        <div className="notice notice-info">
          <p>
            Bestätigt{current[0]?.confirmed_at ? ` am ${formatDateTime(current[0].confirmed_at)}` : ""} ({ALLOCATION_METHOD[current[0]?.method ?? "user"]}).
            Eine Änderung ersetzt diese Zuordnung; die bisherige bleibt im Verlauf.
          </p>
        </div>
      ) : null}

      <div className="allocation-rows">
        {rows.map((row, index) => (
          <RowFields
            key={row.key}
            row={row}
            index={index}
            directory={directory}
            account={account}
            canRemove={rows.length > 1}
            errors={(field) => form.of(rowKey(index, field))}
            onChange={(patch) => update(row.key, patch)}
            onRemove={() => removeRow(row.key)}
          />
        ))}
      </div>
      <div className="row">
        <button type="button" className="secondary" onClick={addRow}>
          Zeile hinzufügen
        </button>
        <span className="spacer" />
        <p className={balanced ? "allocation-rest" : "allocation-rest open"} role="status" aria-live="polite">
          Rest: {rest === null ? "–" : formatMoney(centsToString(rest))}
        </p>
      </div>
      {rest === null ? <p className="muted">Bitte die Beträge prüfen: jede Zeile braucht einen Betrag, z. B. -180,00.</p> : null}
      {rest !== null && !balanced ? (
        <p className="muted">Die Zeilen müssen zusammen genau den Betrag der Buchung ergeben ({formatMoney(line.amount)}).</p>
      ) : null}

      {view.can_learn_rule ? (
        <div className={form.of("learn_rule").length > 0 ? "field field-invalid" : "field"}>
          <label className="inline" htmlFor={learnId}>
            <input
              id={learnId}
              type="checkbox"
              checked={learn}
              disabled={blocked !== null}
              onChange={(e) => setLearnChoice(e.target.checked)}
            />
            Künftig immer so zuordnen
          </label>
          <p className="muted hint">
            {blocked !== null ? unlearnableReason(blocked) : "Weitere Buchungen dieser Gegenseite werden dann ohne Rückfrage so zugeordnet."}
          </p>
          <FieldErrors messages={form.of("learn_rule")} />
          {asked !== null ? (
            <fieldset className="allocation-same-amount">
              <legend>
                Von dieser Bankverbindung warten auch Zahlungen über{" "}
                {others.map((other) => `${formatMoney(unsigned(other.amount))} (${other.count}×)`).join(", ")}. Gehören die
                auch zu {ALLOCATION_CATEGORY[asked]}?
              </legend>
              <div className="row" role="radiogroup">
                <label className="inline" htmlFor={allId}>
                  <input
                    id={allId}
                    type="radio"
                    name={allId}
                    checked={sameAmountOnly === false}
                    onChange={() => setSameAmount({ category: asked, only: false })}
                  />
                  Ja, alle so zuordnen
                </label>
                <label className="inline" htmlFor={onlyId}>
                  <input
                    id={onlyId}
                    type="radio"
                    name={allId}
                    checked={sameAmountOnly === true}
                    onChange={() => setSameAmount({ category: asked, only: true })}
                  />
                  Nein, nur Zahlungen über {formatMoney(unsigned(line.amount))}
                </label>
              </div>
            </fieldset>
          ) : null}
        </div>
      ) : (
        <Field
          id={patternId}
          label="Muster im Verwendungszweck (optional)"
          hint={
            blocked !== null
              ? unlearnableReason(blocked)
              : "Für diese Gegenseite lernt Hausbuch keine Regel allein aus der IBAN. Mit einem Muster (z. B. Abschluss) werden künftige Buchungen mit passendem Verwendungszweck so zugeordnet."
          }
          description="Ein Text aus dem Verwendungszweck, an dem sich diese Art Buchung erkennen lässt, z. B. „Abschluss“ bei der Kontoabrechnung der Bank."
          errors={form.of("purpose_pattern")}
        >
          <input
            type="text"
            value={blocked !== null ? "" : pattern}
            onChange={(e) => setPattern(e.target.value)}
            disabled={blocked !== null}
            autoComplete="off"
            maxLength={200}
          />
        </Field>
      )}

      <div className="row">
        <button type="submit" disabled={busy || !balanced || unanswered}>
          {busy ? "Bestätigt …" : "Bestätigen"}
        </button>
        {!balanced ? <span className="muted">Erst wenn der Rest 0,00 € ist.</span> : null}
        {balanced && unanswered ? <span className="muted">Erst die Frage zu den weiteren Zahlungen beantworten.</span> : null}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** Why a one-row answer of this category never becomes a rule (D160), by kind. */
function unlearnableReason(category: AllocationCategory): string {
  if (category === "internal_transfer_in" || category === "internal_transfer_out") {
    return "Überträge zwischen eigenen Konten erkennt Hausbuch selbst an der Kontonummer.";
  }
  if (category === "hh_credit_card") {
    return "Kartenzahlungen erkennt Hausbuch selbst, sobald die Kreditkarte als Konto angelegt ist.";
  }
  if (TAX_RELEVANT_CATEGORIES.has(category)) return "Steuerlich relevante Zahlungen bestätigen Sie jedes Mal selbst.";
  return "Für diese Kategorie lernt Hausbuch keine Regel.";
}

function FieldErrors({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <ul className="field-errors">
      {messages.map((message, index) => (
        <li key={index}>{message}</li>
      ))}
    </ul>
  );
}

// --- one row ---------------------------------------------------------------------------------

function RowFields({
  row,
  index,
  directory,
  account,
  canRemove,
  errors,
  onChange,
  onRemove,
}: {
  row: Row;
  index: number;
  directory: Directory;
  account: Account;
  canRemove: boolean;
  errors: (field: (typeof ROW_FIELDS)[number]) => string[];
  onChange: (patch: Partial<Row>) => void;
  onRemove: () => void;
}) {
  const id = `allocation-${row.key}`;
  const units = directory.units.filter((unit) => unit.property_id === row.propertyId);
  const unitIds = new Set(units.map((unit) => unit.id));
  const tenancies = directory.tenancies.filter((tenancy) =>
    row.unitId ? tenancy.unit_id === row.unitId : unitIds.has(tenancy.unit_id),
  );
  const loans = directory.loans.filter((loan) => loan.property_id === row.propertyId);
  const unitLabel = new Map(directory.units.map((unit) => [unit.id, unit.label]));
  // A private line (phase 6 D159) takes the household codes in their groups and names no unit,
  // tenancy or loan; it may name the property (a bill of the flat paid privately).
  const rental = account.purpose === "rental";
  const groups = categoryGroups(account.purpose, account.kind);

  function setProperty(propertyId: string) {
    onChange({ propertyId, unitId: "", tenancyId: "", loanId: "" });
  }

  function setTenancy(tenancyId: string) {
    const tenancy = directory.tenancies.find((t) => t.id === tenancyId);
    onChange(tenancy ? { tenancyId, unitId: tenancy.unit_id } : { tenancyId });
  }

  return (
    <fieldset className="allocation-row">
      <legend>Zeile {index + 1}</legend>
      <div className="allocation-fields">
        <Field
          id={`${id}-amount`}
          label="Betrag (€)"
          description="Der Teil der Buchung, der in diese Kategorie fällt; Ausgaben mit Minus. Alle Zeilen zusammen ergeben genau den Betrag der Buchung."
          errors={errors("amount")}
        >
          <input
            type="text"
            inputMode="decimal"
            value={row.amount}
            onChange={(e) => onChange({ amount: e.target.value })}
            autoComplete="off"
            required
          />
        </Field>
        <Field
          id={`${id}-category`}
          label="Kategorie"
          description={
            rental
              ? "Wofür das Geld war, z. B. Kaltmiete, Darlehenszinsen oder Hausgeld. Nach ihr ordnet der Anlage-V-Entwurf den Betrag ein."
              : "Wofür das Geld war, z. B. Lebensmittel, Gehalt oder Versicherung. Ein Privatkonto zählt nie in die Anlage V; „Kosten der Immobilie, privat bezahlt“ merkt eine Rechnung der Wohnung vor, die von hier bezahlt wurde."
          }
          errors={errors("category")}
        >
          <select value={row.category} onChange={(e) => onChange({ category: e.target.value as AllocationCategory | "" })} required>
            <option value="">– wählen –</option>
            {groups.map((group) =>
              group.label === null ? (
                group.categories.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))
              ) : (
                <optgroup key={group.label} label={group.label}>
                  {group.categories.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </optgroup>
              ),
            )}
          </select>
        </Field>
        <Field
          id={`${id}-property`}
          label="Immobilie"
          description="Zu welcher Immobilie der Betrag gehört."
          errors={errors("property_id")}
        >
          <select value={row.propertyId} onChange={(e) => setProperty(e.target.value)}>
            <option value="">– keine –</option>
            {directory.properties.map((property) => (
              <option key={property.id} value={property.id}>
                {property.label}
              </option>
            ))}
          </select>
        </Field>
        {(rental && units.length > 0) || row.unitId ? (
          <Field
            id={`${id}-unit`}
            label="Einheit"
            description="Zu welcher Einheit der Betrag gehört, z. B. die Wohnung, deren Miete das ist."
            errors={errors("unit_id")}
          >
            <select value={row.unitId} onChange={(e) => onChange({ unitId: e.target.value, tenancyId: "" })}>
              <option value="">– keine –</option>
              {units.map((unit) => (
                <option key={unit.id} value={unit.id}>
                  {unit.label}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {(rental && tenancies.length > 0) || row.tenancyId ? (
          <Field
            id={`${id}-tenancy`}
            label="Mietverhältnis"
            description="Bei Miete, Nebenkosten oder Kaution: das Mietverhältnis, zu dem die Zahlung gehört."
            errors={errors("tenancy_id")}
          >
            <select value={row.tenancyId} onChange={(e) => setTenancy(e.target.value)}>
              <option value="">– keines –</option>
              {tenancies.map((tenancy) => (
                <option key={tenancy.id} value={tenancy.id}>
                  {unitLabel.get(tenancy.unit_id) ?? "?"}: {partyNames(tenancy, directory.tenants)} (ab {formatDate(tenancy.start_date)})
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {(rental && loans.length > 0) || row.loanId ? (
          <Field
            id={`${id}-loan`}
            label="Darlehen"
            description="Bei Zinsen, Tilgung oder Geldbeschaffungskosten: das Darlehen, zu dem die Zahlung gehört."
            errors={errors("loan_id")}
          >
            <select value={row.loanId} onChange={(e) => onChange({ loanId: e.target.value })}>
              <option value="">– keines –</option>
              {loans.map((loan) => (
                <option key={loan.id} value={loan.id}>
                  {loan.lender}
                  {loan.contract_number ? ` ${loan.contract_number}` : ""}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field
          id={`${id}-from`}
          label="Leistungszeitraum von"
          description="Der Zeitraum, für den gezahlt wurde, z. B. 01.01. bis 31.01. für die Januarmiete. Er wird gespeichert, damit Zahlungen um den Jahreswechsel später dem richtigen Steuerjahr zugeordnet werden können."
          errors={errors("service_period_start")}
        >
          <input type="date" value={row.periodStart} onChange={(e) => onChange({ periodStart: e.target.value })} />
        </Field>
        <Field
          id={`${id}-to`}
          label="bis"
          description="Das Ende des Leistungszeitraums."
          errors={errors("service_period_end")}
        >
          <input type="date" value={row.periodEnd} onChange={(e) => onChange({ periodEnd: e.target.value })} />
        </Field>
      </div>
      <label className="inline" htmlFor={`${id}-recurring`}>
        <input
          id={`${id}-recurring`}
          type="checkbox"
          checked={row.recurring}
          onChange={(e) => onChange({ recurring: e.target.checked })}
        />
        Wiederkehrend (z. B. monatliche Miete oder Rate)
      </label>
      <Field
        id={`${id}-document`}
        label="Beleg"
        description="Die Rechnung oder das Schreiben zu dieser Zahlung, falls es eines gibt."
        errors={errors("document_id")}
      >
        <DocumentPicker value={row.documentId} onChange={(documentId) => onChange({ documentId })} />
      </Field>
      {row.documentId ? (
        <p className="cell-detail">
          <Link href={`/documents/${row.documentId}`}>Beleg öffnen</Link>
        </p>
      ) : null}
      <div className="row">
        <button type="button" className="link" onClick={onRemove} disabled={!canRemove}>
          Zeile entfernen
        </button>
      </div>
    </fieldset>
  );
}
