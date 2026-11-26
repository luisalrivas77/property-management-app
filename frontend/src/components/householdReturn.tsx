"use client";

// The income tax return's household facts on the Eigentümer page (docs/phases/phase-4b.md §10,
// D97, D109, D112), inside phase 4's Einkommen section (components/income.tsx):
//
// - GrossSourceChip: where an owner's gross pay comes from, as GET /api/tax-rate's
//   owner_estimates says (gross_source): "laut Lohnsteuerbescheinigung <Jahr>", "Hochrechnung
//   aus Gehaltsabrechnung <Monat> (n Monate verbleibend)" (the latest current payslip row's
//   month and months_remaining_paid, as stored) or "Schätzung".
// - HouseholdReturnBlocks: the Kinder block (each child's birth month and the month Kindergeld
//   starts; never a name, D109) and the Haushalt block (§35a household_services, donations as
//   special_expense, childcare_cost rows), each a list of the year's current rows with an add
//   form and "Entfernen"; plus the links to the Anlage N tab and the Jahresfragen.
//
// Rows are append-only (POST /api/return-inputs): "Entfernen" is a withdrawal (withdrawn =
// true, supersedes_id = the row, repeating its value). The server refuses a write against a
// row that is no longer current (return_input.not_current), a childcare cost for a child
// already 14 when the year began (return.childcare_age) and a §35a cost paid in cash
// (return.household_services_cash). Amounts are shown as stored; nothing is computed here.
import Link from "next/link";
import { useCallback, useId, useState, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { components } from "@/api/types";
import { getLocale, LOCALE_TAG, translate, useFormat, useT } from "@/i18n";
import { householdReturn } from "@/i18n/income";

import { DocumentPicker } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { useReturnInputsSync } from "./returnInputsSync";
import { toMoneyString } from "./input";
import { BelegLink } from "./returnInputEvidence";
import { GROSS_SOURCE, HOUSEHOLD_SERVICE_CLASS, HOUSEHOLD_SERVICE_SOURCE, options } from "./labels";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

type Schemas = components["schemas"];
type ReturnInput = Schemas["ReturnInput"];
type ReturnInputCreate = Schemas["ReturnInputCreate"];
type ReturnInputKind = Schemas["ReturnInputKind"];
type OwnerZve = Schemas["OwnerZve"];
type Payslip = Schemas["Payslip"];
type ChildFact = Schemas["ChildFact"];
type ChildcareFact = Schemas["ChildcareFact"];
type HouseholdServicesFact = Schemas["HouseholdServicesFact"];
type SpecialExpenseFact = Schemas["SpecialExpenseFact"];
type ServiceClass = HouseholdServicesFact["service_class"];
type ServiceSource = HouseholdServicesFact["source"];

// --- the API ---------------------------------------------------------------------------------

function loadRows(taxYear: number, kind: ReturnInputKind, ownerId?: string): Promise<ReturnInput[]> {
  return ok(
    api.GET("/api/return-inputs", {
      params: { query: { tax_year: taxYear, kind, ...(ownerId ? { owner_id: ownerId } : {}) } },
    }),
  );
}

function record(body: ReturnInputCreate): Promise<ReturnInput> {
  return ok(api.POST("/api/return-inputs", { body }));
}

function codesOf(error: unknown): string[] {
  return error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
}

/** "2026-03-14" → "März 2026" (the month only: a child's birth date is never shown, D109). */
function monthText(isoDate: string): string {
  const [year, month] = isoDate.split("-").map(Number);
  if (!year || !month) return isoDate;
  return new Intl.DateTimeFormat(LOCALE_TAG[getLocale()], { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, 1)),
  );
}

/** "2026-03-14" → "März" (a payslip's month within its year). */
function monthName(isoDate: string): string {
  const month = Number(isoDate.slice(5, 7));
  if (!month) return isoDate;
  return new Intl.DateTimeFormat(LOCALE_TAG[getLocale()], { month: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(2000, month - 1, 1)),
  );
}

// --- the gross pay's source chip --------------------------------------------------------------

/**
 * The source of an owner's gross pay for the year (D98): from a confirmed certificate, the
 * projection of the latest payslip, or the typed estimate. With a certificate or a payslip it
 * also shows the gross the estimate uses, as the API computed it.
 */
export function GrossSourceChip({ estimate, year }: { estimate: OwnerZve | undefined; year: number }) {
  const t = useT(householdReturn);
  const f = useFormat();
  if (!estimate) return null;
  const source = estimate.gross_source;
  if (source === "expected") return <span className="badge">{GROSS_SOURCE.expected}</span>;
  // Phase 4b D195: the owner said „Kein Arbeitslohn“ for the year, so the estimate counts 0;
  // the cell's NoPayMarker (components/noPay.tsx) says so, with „Aufheben“.
  if (source === "declared_none") return null;
  return (
    <span className="muted">
      {t.used} <strong>{f.money(estimate.gross)}</strong>{" "}
      {source === "certificate" ? (
        <span className="badge">
          {GROSS_SOURCE.certificate} {year}
        </span>
      ) : (
        <PayslipChip ownerId={estimate.owner_id} year={year} />
      )}
    </span>
  );
}

function PayslipChip({ ownerId, year }: { ownerId: string; year: number }) {
  const load = useCallback(() => loadRows(year, "payslip", ownerId), [year, ownerId]);
  const rows = useLoad(load);
  const t = useT(householdReturn);
  const latest = (rows.data ?? [])
    .map((row) => row.value as Payslip)
    .sort((a, b) => a.period_month.localeCompare(b.period_month))
    .at(-1);
  if (!latest) return <span className="badge">{GROSS_SOURCE.payslip}</span>;
  const remaining = latest.months_remaining_paid;
  return (
    <span className="badge">
      {t.payslipChip(GROSS_SOURCE.payslip, monthName(latest.period_month), remaining)}
    </span>
  );
}

// --- the Kinder and Haushalt blocks -----------------------------------------------------------

/** The household's return facts for the year, under the Einkommen section. */
export function HouseholdReturnBlocks({ year }: { year: number }) {
  const loadChildren = useCallback(() => loadRows(year, "child"), [year]);
  const children = useLoad(loadChildren);
  const childrenChanged = useReturnInputsSync(children.reload);
  const t = useT(householdReturn);
  const f = useFormat();
  return (
    <div className="stack">
      <p className="row">
        <Link href={`/tax/${year}?tab=anlage-n`}>{t.viewAnlageN(year)}</Link>
        <Link href={`/tax/${year}/fragen`}>{t.answerQuestions(year)}</Link>
      </p>
      <h3 id="kinder">{t.children}</h3>
      <p className="muted">{t.childrenIntro}</p>
      <Loading state={children}>
        {(list) => (
          <>
            <RowList
              rows={list}
              empty={t.noChildren(year)}
              describe={(row, index) => childText(row, index)}
              onChanged={childrenChanged}
            />
            <ChildForm year={year} onDone={childrenChanged} />
          </>
        )}
      </Loading>
      <h3 id="haushalt">{t.household}</h3>
      <ItemBlock
        year={year}
        kind="household_services"
        title={t.servicesTitle}
        intro={t.servicesIntro}
        empty={t.noServices(year)}
        describe={(row) => {
          const v = row.value as HouseholdServicesFact;
          // The payee a card from the bookings named (phase 10 D293), when the row has one.
          const payee = v.payee_label ? `${v.payee_label} · ` : "";
          return (
            payee +
            t.servicesRow(
              HOUSEHOLD_SERVICE_CLASS[v.service_class],
              f.money(v.labour_cost),
              HOUSEHOLD_SERVICE_SOURCE[v.source],
            )
          );
        }}
        form={(done) => <ServicesForm year={year} onDone={done} />}
      />
      <ItemBlock
        year={year}
        kind="special_expense"
        title={t.donations}
        intro={t.donationsIntro}
        empty={t.noDonations(year)}
        describe={(row) => {
          const v = row.value as SpecialExpenseFact;
          return `${v.recipient_label} · ${f.money(v.amount)}`;
        }}
        form={(done) => <DonationForm year={year} onDone={done} />}
      />
      <Loading state={children}>
        {(list) => (
          <ItemBlock
            year={year}
            kind="childcare_cost"
            title={t.childcare}
            intro={t.childcareIntro}
            empty={t.noChildcare(year)}
            describe={(row) => {
              const v = row.value as ChildcareFact;
              const index = list.findIndex((child) => child.id === v.child_id);
              const child = list[index];
              const payee = v.payee_label ? ` · ${v.payee_label}` : "";
              return `${child ? childName(child, index) : t.child}${payee} · ${f.money(v.amount)}`;
            }}
            form={(done) =>
              list.length === 0 ? (
                <p className="muted">{t.childFirst}</p>
              ) : (
                <ChildcareForm year={year} childRows={list} onDone={done} />
              )
            }
          />
        )}
      </Loading>
    </div>
  );
}

function childName(row: ReturnInput, index: number): string {
  return translate(householdReturn).childName(index + 1, monthText((row.value as ChildFact).born_on));
}

function childText(row: ReturnInput, index: number): string {
  const v = row.value as ChildFact;
  const from = v.kindergeld_from ?? v.born_on;
  return translate(householdReturn).childText(childName(row, index), monthText(from));
}

/** A sub-block of the Haushalt block: the year's current rows of one kind and its add form. */
function ItemBlock({
  year,
  kind,
  title,
  intro,
  empty,
  describe,
  form,
}: {
  year: number;
  kind: ReturnInputKind;
  title: string;
  intro: string;
  empty: string;
  describe: (row: ReturnInput, index: number) => string;
  form: (done: () => void) => ReactNode;
}) {
  const load = useCallback(() => loadRows(year, kind), [year, kind]);
  const rows = useLoad(load);
  // A row saved here ends a proposal of the guide above it, and a proposal taken over there adds
  // a row here (components/returnInputsSync.ts).
  const changed = useReturnInputsSync(rows.reload);
  return (
    <div className="stack">
      <h4>{title}</h4>
      <p className="muted">{intro}</p>
      <Loading state={rows}>
        {(list) => (
          <>
            <RowList rows={list} empty={empty} describe={describe} onChanged={changed} />
            {form(changed)}
          </>
        )}
      </Loading>
    </div>
  );
}

/** The current rows, each with "Entfernen" (a withdrawal repeating its value). */
function RowList({
  rows,
  empty,
  describe,
  onChanged,
}: {
  rows: ReturnInput[];
  empty: string;
  describe: (row: ReturnInput, index: number) => string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const t = useT(householdReturn);

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

  if (rows.length === 0) return <p className="muted">{empty}</p>;
  return (
    <>
      <ul className="stack">
        {rows.map((row, index) => (
          <li key={row.id} className="row">
            <span>{describe(row, index)}</span>
            <BelegLink documentId={row.document_id} />
            <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
              {t.remove}
            </button>
          </li>
        ))}
      </ul>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
    </>
  );
}

// --- the add forms ----------------------------------------------------------------------------

/** Submit one new household row; maps the rule codes the server may answer to their field. */
function useRowSubmit(keys: readonly string[], codeMessages: Record<string, [string, string]>) {
  const form = useFormErrors(keys);
  const [busy, setBusy] = useState(false);
  async function submit(body: ReturnInputCreate, onDone: () => void): Promise<void> {
    setBusy(true);
    form.clear();
    try {
      await record(body);
      onDone();
    } catch (error) {
      const code = codesOf(error).find((c) => c in codeMessages);
      const mapped = code ? codeMessages[code] : undefined;
      if (mapped) form.invalid({ [mapped[0]]: [mapped[1]] });
      else form.fail(error);
    } finally {
      setBusy(false);
    }
  }
  return { form, busy, submit };
}

const CHILD_KEYS = ["value.born_on", "value.kindergeld_from", "tax_year"] as const;

function ChildForm({ year, onDone }: { year: number; onDone: () => void }) {
  const base = useId();
  const [bornOn, setBornOn] = useState("");
  const [kindergeldFrom, setKindergeldFrom] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const t = useT(householdReturn);
  const { form, busy, submit } = useRowSubmit(CHILD_KEYS, {});

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved(null);
    const messages: Record<string, string[]> = {};
    if (!/^\d{4}-\d{2}-\d{2}$/.test(bornOn)) messages["value.born_on"] = [t.bornRequired];
    if (kindergeldFrom !== "" && !/^\d{4}-\d{2}$/.test(kindergeldFrom)) {
      messages["value.kindergeld_from"] = [t.monthOrEmpty];
    }
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    // A child counts from its birth year in every later year until it turns 18 (D97, D109).
    const firstYear = Number(bornOn.slice(0, 4));
    await submit(
      {
        kind: "child",
        tax_year: firstYear,
        owner_id: null,
        value: { born_on: bornOn, kindergeld_from: kindergeldFrom === "" ? null : `${kindergeldFrom}-01` },
        withdrawn: false,
      },
      () => {
        setBornOn("");
        setKindergeldFrom("");
        setSaved(firstYear > year ? t.savedFrom(firstYear) : null);
        onDone();
      },
    );
  }

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-label={t.addChild}>
      <div className="grid">
        <Field
          id={`${base}-born`}
          label={t.bornOn}
          hint={t.bornHint}
          description={t.bornDescription}
          errors={form.of("value.born_on")}
        >
          <input type="date" value={bornOn} onChange={(e) => setBornOn(e.target.value)} />
        </Field>
        <Field
          id={`${base}-kindergeld`}
          label={t.kindergeldFrom}
          hint={t.kindergeldHint}
          description={t.kindergeldDescription}
          errors={form.of("value.kindergeld_from")}
        >
          <input type="month" value={kindergeldFrom} onChange={(e) => setKindergeldFrom(e.target.value)} />
        </Field>
      </div>
      <div className="row">
        <Submit busy={busy}>{t.addChild}</Submit>
      </div>
      {saved ? <p className="muted">{saved}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

const SERVICES_KEYS = ["value.labour_cost", "value.paid_to_account", "value.service_class", "value.source"] as const;

function ServicesForm({ year, onDone }: { year: number; onDone: () => void }) {
  const base = useId();
  const [serviceClass, setServiceClass] = useState<ServiceClass>("handwerker");
  const [labourCost, setLabourCost] = useState("");
  const [source, setSource] = useState<ServiceSource>("own_invoice");
  const [paidToAccount, setPaidToAccount] = useState(true);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const t = useT(householdReturn);
  const { form, busy, submit } = useRowSubmit(SERVICES_KEYS, {
    "return.household_services_cash": ["value.paid_to_account", t.cashNotCounted],
  });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const amount = values.take("value.labour_cost", toMoneyString(labourCost));
    if (!values.ok || amount === undefined) return form.invalid(values.messages);
    await submit(
      {
        kind: "household_services",
        tax_year: year,
        owner_id: null,
        document_id: documentId,
        value: { service_class: serviceClass, labour_cost: amount, paid_to_account: paidToAccount, source },
        withdrawn: false,
      },
      () => {
        setLabourCost("");
        setDocumentId(null);
        onDone();
      },
    );
  }

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-label={t.addService}>
      <div className="grid">
        <Field id={`${base}-class`} label={t.serviceKind} errors={form.of("value.service_class")}>
          <select value={serviceClass} onChange={(e) => setServiceClass(e.target.value as ServiceClass)}>
            {options(HOUSEHOLD_SERVICE_CLASS).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id={`${base}-labour`}
          label={t.labour}
          description={t.labourDescription}
          errors={form.of("value.labour_cost")}
        >
          <input type="text" inputMode="decimal" value={labourCost} onChange={(e) => setLabourCost(e.target.value)} autoComplete="off" />
        </Field>
        <Field id={`${base}-source`} label={t.source} errors={form.of("value.source")}>
          <select value={source} onChange={(e) => setSource(e.target.value as ServiceSource)}>
            {options(HOUSEHOLD_SERVICE_SOURCE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${base}-document`} label={t.receiptOptional}>
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
      </div>
      <label className="inline">
        <input type="checkbox" checked={paidToAccount} onChange={(e) => setPaidToAccount(e.target.checked)} />
        {t.paidByTransfer}
      </label>
      {form.of("value.paid_to_account").map((message) => (
        <p key={message} className="notice notice-error">
          {message}
        </p>
      ))}
      <div className="row">
        <Submit busy={busy}>{t.add}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

const DONATION_KEYS = ["value.recipient_label", "value.amount"] as const;

function DonationForm({ year, onDone }: { year: number; onDone: () => void }) {
  const base = useId();
  const [recipient, setRecipient] = useState("");
  const [amount, setAmount] = useState("");
  const [documentId, setDocumentId] = useState<string | null>(null);
  const t = useT(householdReturn);
  const { form, busy, submit } = useRowSubmit(DONATION_KEYS, {});

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const money = values.take("value.amount", toMoneyString(amount));
    if (recipient.trim() === "") values.messages["value.recipient_label"] = [t.recipientRequired];
    if (!values.ok || money === undefined) return form.invalid(values.messages);
    const value: SpecialExpenseFact = { expense_class: "donation", amount: money, recipient_label: recipient.trim() };
    await submit(
      { kind: "special_expense", tax_year: year, owner_id: null, document_id: documentId, value, withdrawn: false },
      () => {
        setRecipient("");
        setAmount("");
        setDocumentId(null);
        onDone();
      },
    );
  }

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-label={t.addDonation}>
      <div className="grid">
        <Field id={`${base}-recipient`} label={t.recipient} errors={form.of("value.recipient_label")}>
          <input type="text" value={recipient} onChange={(e) => setRecipient(e.target.value)} maxLength={120} autoComplete="off" />
        </Field>
        <Field id={`${base}-amount`} label={t.amount} errors={form.of("value.amount")}>
          <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${base}-document`}
          label={t.receiptOptional}
          description={t.donationReceipt}
        >
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
      </div>
      <div className="row">
        <Submit busy={busy}>{t.add}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

const CHILDCARE_KEYS = ["value.child_id", "value.amount", "value.paid_to_account"] as const;

function ChildcareForm({ year, childRows, onDone }: { year: number; childRows: ReturnInput[]; onDone: () => void }) {
  const base = useId();
  const [childId, setChildId] = useState(childRows[0]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [paidToAccount, setPaidToAccount] = useState(true);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const t = useT(householdReturn);
  const { form, busy, submit } = useRowSubmit(CHILDCARE_KEYS, {
    "return.childcare_age": ["value.child_id", t.childAge],
  });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const money = values.take("value.amount", toMoneyString(amount));
    if (childId === "") values.messages["value.child_id"] = [t.chooseChild];
    if (!values.ok || money === undefined) return form.invalid(values.messages);
    const value: ChildcareFact = { child_id: childId, amount: money, paid_to_account: paidToAccount };
    await submit(
      { kind: "childcare_cost", tax_year: year, owner_id: null, document_id: documentId, value, withdrawn: false },
      () => {
        setAmount("");
        setDocumentId(null);
        onDone();
      },
    );
  }

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-label={t.addChildcare}>
      <div className="grid">
        <Field id={`${base}-child`} label={t.child} errors={form.of("value.child_id")}>
          <select value={childId} onChange={(e) => setChildId(e.target.value)}>
            {childRows.map((row, index) => (
              <option key={row.id} value={row.id}>
                {childName(row, index)}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${base}-amount`} label={t.costYear} errors={form.of("value.amount")}>
          <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
        </Field>
        <Field id={`${base}-document`} label={t.receiptOptional}>
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
      </div>
      <label className="inline">
        <input type="checkbox" checked={paidToAccount} onChange={(e) => setPaidToAccount(e.target.checked)} />
        {t.paidByTransfer}
      </label>
      <div className="row">
        <Submit busy={busy}>{t.add}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
