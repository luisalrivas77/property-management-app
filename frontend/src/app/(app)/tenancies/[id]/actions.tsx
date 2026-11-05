"use client";

// The tenancy's actions (§4.5): Kaution erhalten, Kündigung, Auszug. Each is one command.
// Refusals (a notice that ends before it is given, a tenancy already ended) come from the API
// as 422 `invalid` or 409 `conflict` and show its message.
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Tenancy } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { decimalInput, toMoneyString } from "@/components/input";
import { NOTICE_GIVEN_BY, options } from "@/components/labels";

const path = (tenancy: Tenancy) => ({ params: { path: { tenancy_id: tenancy.id } } });

export function DepositForm({ tenancy, onDone }: { tenancy: Tenancy; onDone: () => void }) {
  const [amount, setAmount] = useState(tenancy.deposit_amount === "0.00" ? "" : decimalInput(tenancy.deposit_amount));
  const [receivedOn, setReceivedOn] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(["amount", "received_on"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const money = values.take("amount", toMoneyString(amount));
    if (!receivedOn) values.messages.received_on = ["Bitte das Datum eingeben."];
    if (!values.ok || money === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(api.POST("/api/tenancies/{tenancy_id}/deposit-received", { ...path(tenancy), body: { amount: money, received_on: receivedOn } }));
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted">Auch für eine Rate der Kaution; jede Zahlung ist ein eigener Eintrag.</p>
      <Field id="deposit-received-amount" label="Betrag (€)" errors={form.of("amount")}>
        <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required autoComplete="off" />
      </Field>
      <Field id="deposit-received-on" label="Erhalten am" errors={form.of("received_on")}>
        <input type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} required />
      </Field>
      <Submit busy={busy}>Speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

export function NoticeForm({ tenancy, onDone }: { tenancy: Tenancy; onDone: () => void }) {
  const [givenBy, setGivenBy] = useState<"tenant" | "landlord">("tenant");
  const [givenOn, setGivenOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(["given_by", "given_on", "ends_on"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const messages: Record<string, string[]> = {};
    if (!givenOn) messages.given_on = ["Bitte das Datum der Kündigung eingeben."];
    if (!endsOn) messages.ends_on = ["Bitte das Ende des Mietverhältnisses eingeben."];
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    setBusy(true);
    form.clear();
    try {
      await ok(api.POST("/api/tenancies/{tenancy_id}/notice", { ...path(tenancy), body: { given_by: givenBy, given_on: givenOn, ends_on: endsOn } }));
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Field id="notice-by" label="Gekündigt durch" errors={form.of("given_by")}>
        <select value={givenBy} onChange={(e) => setGivenBy(e.target.value as "tenant" | "landlord")}>
          {options(NOTICE_GIVEN_BY).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field id="notice-on" label="Kündigung vom" errors={form.of("given_on")}>
        <input type="date" value={givenOn} onChange={(e) => setGivenOn(e.target.value)} required />
      </Field>
      <Field id="notice-ends" label="Zum (Ende des Mietverhältnisses)" errors={form.of("ends_on")}>
        <input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} required />
      </Field>
      <p className="muted">Der Auszug selbst wird danach mit „Auszug“ erfasst.</p>
      <Submit busy={busy}>Kündigung speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

export function EndForm({ tenancy, onDone }: { tenancy: Tenancy; onDone: () => void }) {
  const [endDate, setEndDate] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(["end_date"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!endDate) return form.invalid({ end_date: ["Bitte das Auszugsdatum eingeben."] });
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
      <p className="muted">Das Auszugsdatum ist ein Meilenstein; eine Korrektur ist bisher nicht vorgesehen. Bitte genau prüfen.</p>
      <Field id="end-date" label="Auszug am" errors={form.of("end_date")}>
        <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required />
      </Field>
      <Submit busy={busy}>Auszug speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
