"use client";

// "Datum korrigieren" (§9.1): milestone dates (Notartermin, Übergang, Einzug, Auszahlung eines
// Darlehens) are never edited in place. A correction is its own command, which appends a correcting event; History
// then shows the milestone once, marked "korrigiert, ursprünglich …" (principle 4).
import { useState, type FormEvent } from "react";

import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatMoney } from "./format";
import { toMoneyString } from "./input";

export interface CorrectionValues {
  new_date: string;
  purchase_price?: string | null;
}

const KEYS = ["new_date", "purchase_price"] as const;

export function DateCorrectionForm({
  idPrefix,
  label,
  current,
  currentPrice,
  priceLabel = "Kaufpreis",
  priceHint = "Leer lassen, wenn der Kaufpreis stimmt.",
  description = "Das richtige Datum laut Dokument. Die Korrektur wird als eigener Eintrag gespeichert; der Verlauf zeigt das ursprüngliche Datum weiter an.",
  onSubmit,
  onDone,
}: {
  idPrefix: string;
  label: string;
  /** The date in force now (ISO). */
  current: string;
  /** Set for the Notartermin (and a loan's payout): the amount that correction may also fix. */
  currentPrice?: string;
  /** What that amount is called ("Kaufpreis", "Darlehenssumme"). */
  priceLabel?: string;
  priceHint?: string;
  /** What the date is, behind the "i" next to its label. */
  description?: string;
  onSubmit: (values: CorrectionValues) => Promise<unknown>;
  onDone: () => void;
}) {
  const [newDate, setNewDate] = useState(current);
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    if (!newDate) values.messages.new_date = ["Bitte ein Datum eingeben."];
    else if (newDate === current && price.trim() === "") values.messages.new_date = ["Das ist schon das gespeicherte Datum."];
    const purchasePrice = currentPrice !== undefined ? values.optional("purchase_price", price, toMoneyString) : undefined;
    if (!values.ok || (purchasePrice === undefined && currentPrice !== undefined)) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await onSubmit(currentPrice !== undefined && purchasePrice ? { new_date: newDate, purchase_price: purchasePrice } : { new_date: newDate });
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
        Gespeichert: {formatDate(current)}
        {currentPrice !== undefined ? `, ${priceLabel} ${formatMoney(currentPrice)}` : ""}. Die Korrektur wird als eigener Eintrag
        protokolliert; der Verlauf zeigt danach das neue Datum mit dem Hinweis auf das ursprüngliche.
      </p>
      <Field id={`${idPrefix}-date`} label={label} description={description} errors={form.of("new_date")}>
        <input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} required />
      </Field>
      {currentPrice !== undefined ? (
        <Field
          id={`${idPrefix}-price`}
          label={`${priceLabel} korrigieren (€, optional)`}
          hint={priceHint}
          description={`Nur ausfüllen, wenn auch der Betrag (${priceLabel}) falsch erfasst ist: der richtige Betrag laut Vertrag.`}
          errors={form.of("purchase_price")}
        >
          <input type="text" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} autoComplete="off" />
        </Field>
      ) : null}
      <Submit busy={busy}>Korrektur speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
