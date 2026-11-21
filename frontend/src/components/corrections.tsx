"use client";

// "Datum korrigieren" (§9.1): milestone dates (Notartermin, Übergang, Einzug, Auszahlung eines
// Darlehens) are never edited in place. A correction is its own command, which appends a correcting event; History
// then shows the milestone once, marked "korrigiert, ursprünglich …" (principle 4).
import { useState, type FormEvent } from "react";

import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { useFormat, useT } from "@/i18n";
import { corrections } from "@/i18n/corrections";

import { decimalInput, toMoneyString } from "./input";

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
  priceLabel: priceLabelProp,
  priceHint: priceHintProp,
  description: descriptionProp,
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
  const t = useT(corrections);
  const f = useFormat();
  const priceLabel = priceLabelProp ?? t.priceLabel;
  const priceHint = priceHintProp ?? t.priceHint;
  const description = descriptionProp ?? t.description;
  const [newDate, setNewDate] = useState(current);
  // Pre-filled with the stored amount; only a changed amount is sent (an emptied field keeps it).
  const [price, setPrice] = useState(currentPrice !== undefined ? decimalInput(currentPrice) : "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const purchasePrice = currentPrice !== undefined ? values.optional("purchase_price", price, toMoneyString) : undefined;
    const changedPrice = currentPrice !== undefined && purchasePrice && purchasePrice !== currentPrice ? purchasePrice : null;
    if (!newDate) values.messages.new_date = [t.dateMissing];
    else if (newDate === current && changedPrice === null && values.ok)
      values.messages.new_date = [currentPrice !== undefined ? t.sameBoth : t.sameDate];
    if (!values.ok || (purchasePrice === undefined && currentPrice !== undefined)) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await onSubmit(changedPrice !== null ? { new_date: newDate, purchase_price: changedPrice } : { new_date: newDate });
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
        {t.stored(f.date(current), currentPrice !== undefined ? t.storedPrice(priceLabel, f.money(currentPrice)) : null)}
      </p>
      <Field id={`${idPrefix}-date`} label={label} description={description} errors={form.of("new_date")}>
        <input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} required />
      </Field>
      {currentPrice !== undefined ? (
        <Field
          id={`${idPrefix}-price`}
          label={t.priceField(priceLabel)}
          hint={priceHint}
          description={t.priceDescription(priceLabel)}
          errors={form.of("purchase_price")}
        >
          <input type="text" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} autoComplete="off" />
        </Field>
      ) : null}
      <Submit busy={busy}>{t.submit}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
