"use client";

// The tenancy's actions (§4.5): Bearbeiten (the non-milestone fields), Kaution erhalten,
// Kündigung, Auszug, and the corrections of a Kaution receipt and of the notice. Each is one
// command; a correction appends a correcting event, so History shows it marked korrigiert.
// Refusals (a notice that ends before it is given, a tenancy already ended) come from the API
// as 422 `invalid` or 409 `conflict` and show its message.
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { DepositForm as DepositKind, DepositReceipt, RentModel, Tenancy, TenancyPatch } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { blankToNull, decimalInput, toMoneyString } from "@/components/input";
import { DEPOSIT_FORM, NOTICE_GIVEN_BY, options, RENT_MODEL } from "@/components/labels";
import { RentTermsFields, toDueDay, type OperatingCostsTerms } from "@/components/taxInputs";
import { useT } from "@/i18n";
import { tenancies } from "@/i18n/tenancies";

const path = (tenancy: Tenancy) => ({ params: { path: { tenancy_id: tenancy.id } } });

const EDIT_KEYS = [
  "fixed_term_until",
  "rent_model",
  "rent_due_day",
  "operating_costs_terms",
  "deposit_amount",
  "deposit_form",
  "deposit_bank",
  "deposit_iban",
] as const;

/** "Bearbeiten": Befristung, Mietmodell, Fälligkeit und Nebenkosten-Regelung (phase 3 D67,
 * D72), Kaution. Sends only what changed; the IBAN is
 * write-only (empty keeps the stored one). Einzug, Auszug, Kündigung and Vertragsdatum change
 * through their corrections, the Mieter through "Mieter ändern". */
export function EditTenancyForm({ tenancy, onDone }: { tenancy: Tenancy; onDone: () => void }) {
  const [fixedTermUntil, setFixedTermUntil] = useState(tenancy.fixed_term_until ?? "");
  const [rentModel, setRentModel] = useState<RentModel>(tenancy.rent_model);
  const [dueDay, setDueDay] = useState(String(tenancy.rent_due_day));
  const [costTerms, setCostTerms] = useState<OperatingCostsTerms>(tenancy.operating_costs_terms);
  const [depositForm, setDepositForm] = useState<DepositKind>(tenancy.deposit_form);
  const [depositAmount, setDepositAmount] = useState(tenancy.deposit_form === "none" ? "" : decimalInput(tenancy.deposit_amount));
  const [depositBank, setDepositBank] = useState(tenancy.deposit_bank ?? "");
  const [iban, setIban] = useState("");
  const [removeIban, setRemoveIban] = useState(false);
  const [busy, setBusy] = useState(false);
  const t = useT(tenancies);
  const form = useFormErrors(EDIT_KEYS, { "iban.": "deposit_iban" });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const amount = depositAmount.trim() === "" ? "0.00" : values.take("deposit_amount", toMoneyString(depositAmount));
    const rentDueDay = values.take("rent_due_day", toDueDay(dueDay));
    if (!values.ok || amount === undefined || rentDueDay === undefined) return form.invalid(values.messages);
    const body: TenancyPatch = {};
    if ((fixedTermUntil || null) !== tenancy.fixed_term_until) body.fixed_term_until = fixedTermUntil || null;
    if (rentModel !== tenancy.rent_model) body.rent_model = rentModel;
    if (rentDueDay !== tenancy.rent_due_day) body.rent_due_day = rentDueDay;
    if (costTerms !== tenancy.operating_costs_terms) body.operating_costs_terms = costTerms;
    if (depositForm !== tenancy.deposit_form) body.deposit_form = depositForm;
    if (amount !== tenancy.deposit_amount) body.deposit_amount = amount;
    if (blankToNull(depositBank) !== tenancy.deposit_bank) body.deposit_bank = blankToNull(depositBank);
    if (removeIban) body.deposit_iban = null;
    else if (iban.trim() !== "") body.deposit_iban = iban.trim();
    setBusy(true);
    form.clear();
    try {
      await ok(api.PATCH("/api/tenancies/{tenancy_id}", { ...path(tenancy), body }));
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Field
        id="edit-fixed"
        label={t.fixedField}
        hint={t.fixedHint}
        description={t.fixedDescription}
        errors={form.of("fixed_term_until")}
      >
        <input type="date" value={fixedTermUntil} onChange={(e) => setFixedTermUntil(e.target.value)} />
      </Field>
      <Field
        id="edit-model"
        label={t.rentModel}
        description={t.rentModelDescription}
        errors={form.of("rent_model")}
      >
        <select value={rentModel} onChange={(e) => setRentModel(e.target.value as RentModel)}>
          {options(RENT_MODEL).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <RentTermsFields
        idPrefix="edit"
        dueDay={dueDay}
        onDueDay={setDueDay}
        terms={costTerms}
        onTerms={setCostTerms}
        errors={(key) => form.of(key)}
      />
      <Field
        id="edit-deposit-form"
        label={t.depositForm}
        description={t.depositFormDescription}
        errors={form.of("deposit_form")}
      >
        <select value={depositForm} onChange={(e) => setDepositForm(e.target.value as DepositKind)}>
          {options(DEPOSIT_FORM).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field
        id="edit-deposit-amount"
        label={t.depositAmount}
        hint={t.depositAmountHint}
        description={t.depositAmountDescription}
        errors={form.of("deposit_amount")}
      >
        <input type="text" inputMode="decimal" value={depositAmount} onChange={(e) => setDepositAmount(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id="edit-deposit-bank"
        label={t.depositBank}
        description={t.depositBankDescription}
        errors={form.of("deposit_bank")}
      >
        <input type="text" value={depositBank} onChange={(e) => setDepositBank(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id="edit-deposit-iban"
        label={t.depositIban}
        hint={t.depositIbanStored(tenancy.deposit_iban_masked ?? null)}
        description={t.depositIbanDescription}
        errors={form.of("deposit_iban")}
      >
        <input type="text" value={iban} onChange={(e) => setIban(e.target.value)} autoComplete="off" spellCheck={false} disabled={removeIban} />
      </Field>
      {tenancy.deposit_iban_masked ? (
        <label className="inline">
          <input type="checkbox" checked={removeIban} onChange={(e) => setRemoveIban(e.target.checked)} />
          {t.depositIbanRemove}
        </label>
      ) : null}
      <Submit busy={busy}>{t.save}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** "Kaution erhalten", or with `receipt` the correction of that receipt (amount and date). */
export function DepositForm({ tenancy, receipt, onDone }: { tenancy: Tenancy; receipt?: DepositReceipt; onDone: () => void }) {
  const [amount, setAmount] = useState(
    receipt ? decimalInput(receipt.amount) : tenancy.deposit_amount === "0.00" ? "" : decimalInput(tenancy.deposit_amount),
  );
  const [receivedOn, setReceivedOn] = useState(receipt?.received_on ?? "");
  const [busy, setBusy] = useState(false);
  const t = useT(tenancies);
  const form = useFormErrors(["amount", "received_on"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const money = values.take("amount", toMoneyString(amount));
    if (!receivedOn) values.messages.received_on = [t.dateMissing];
    if (!values.ok || money === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      const body = { amount: money, received_on: receivedOn };
      if (receipt) {
        await ok(
          api.POST("/api/tenancies/{tenancy_id}/deposits/{event_id}/corrections", {
            params: { path: { tenancy_id: tenancy.id, event_id: receipt.event_id } },
            body,
          }),
        );
      } else {
        await ok(api.POST("/api/tenancies/{tenancy_id}/deposit-received", { ...path(tenancy), body }));
      }
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted">
        {receipt ? t.depositCorrectionIntro : t.depositIntro}
      </p>
      <Field
        id="deposit-received-amount"
        label={t.amount}
        description={t.amountDescription}
        errors={form.of("amount")}
      >
        <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id="deposit-received-on"
        label={t.receivedField}
        description={t.receivedDescription}
        errors={form.of("received_on")}
      >
        <input type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} required />
      </Field>
      <Submit busy={busy}>{receipt ? t.saveCorrection : t.save}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

export interface NoticeValues {
  given_by: "tenant" | "landlord";
  given_on: string;
  ends_on: string;
}

/** "Kündigung", or with `current` the correction of the notice already given. */
export function NoticeForm({ tenancy, current, onDone }: { tenancy: Tenancy; current?: NoticeValues; onDone: () => void }) {
  const [givenBy, setGivenBy] = useState<"tenant" | "landlord">(current?.given_by ?? "tenant");
  const [givenOn, setGivenOn] = useState(current?.given_on ?? "");
  const [endsOn, setEndsOn] = useState(current?.ends_on ?? "");
  const [busy, setBusy] = useState(false);
  const t = useT(tenancies);
  const form = useFormErrors(["given_by", "given_on", "ends_on"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const messages: Record<string, string[]> = {};
    if (!givenOn) messages.given_on = [t.noticeDateMissing];
    if (!endsOn) messages.ends_on = [t.noticeEndMissing];
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    setBusy(true);
    form.clear();
    try {
      const body = { given_by: givenBy, given_on: givenOn, ends_on: endsOn };
      if (current) await ok(api.POST("/api/tenancies/{tenancy_id}/corrections/notice", { ...path(tenancy), body }));
      else await ok(api.POST("/api/tenancies/{tenancy_id}/notice", { ...path(tenancy), body }));
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Field
        id="notice-by"
        label={t.givenBy}
        description={t.givenByDescription}
        errors={form.of("given_by")}
      >
        <select value={givenBy} onChange={(e) => setGivenBy(e.target.value as "tenant" | "landlord")}>
          {options(NOTICE_GIVEN_BY).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field
        id="notice-on"
        label={t.givenOn}
        description={t.givenOnDescription}
        errors={form.of("given_on")}
      >
        <input type="date" value={givenOn} onChange={(e) => setGivenOn(e.target.value)} required />
      </Field>
      <Field
        id="notice-ends"
        label={t.endsOn}
        description={t.endsOnDescription}
        errors={form.of("ends_on")}
      >
        <input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} required />
      </Field>
      <p className="muted">
        {current
          ? t.noticeCorrectionIntro
          : t.noticeIntro}
      </p>
      <Submit busy={busy}>{current ? t.saveCorrection : t.noticeSave}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

export function EndForm({ tenancy, onDone }: { tenancy: Tenancy; onDone: () => void }) {
  const [endDate, setEndDate] = useState("");
  const [busy, setBusy] = useState(false);
  const t = useT(tenancies);
  const form = useFormErrors(["end_date"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!endDate) return form.invalid({ end_date: [t.moveOutMissing] });
    setBusy(true);
    form.clear();
    try {
      await ok(api.POST("/api/tenancies/{tenancy_id}/end", { ...path(tenancy), body: { end_date: endDate } }));
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted">{t.moveOutIntro}</p>
      <Field
        id="end-date"
        label={t.moveOutField}
        description={t.moveOutDescription}
        errors={form.of("end_date")}
      >
        <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required />
      </Field>
      <Submit busy={busy}>{t.moveOutSave}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
