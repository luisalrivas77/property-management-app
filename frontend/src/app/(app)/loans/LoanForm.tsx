"use client";

// Darlehen form (§9.1): Bank, Vertragsnummer, Darlehenssumme, Nominalzins, Rate, Auszahlung,
// erste Rate, Zinsbindung bis, Anschlusszins, Konto-IBAN, Vertragsdatum, and "Vor der ersten
// Rate nur Zinsen" (interest_only_before_first_payment). Rates are entered
// in percent and sent as fractions. The API builds the Tilgungsplan before it saves, so a rate
// that never repays the loan comes back as 422 loan.payment_does_not_cover_interest (with
// the payment number) and is shown under "Rate". Editing (LoanPatch) cannot change the
// principal or the disbursement: those are the loan's milestones, corrected by "Auszahlung
// korrigieren" on the loan page.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Loan, LoanPatch } from "@/api/schemas";
import { collector, Field, FieldDescription, FormBanner, InfoToggle, Submit, useFormErrors } from "@/components/form";
import { blankToNull, decimalInput, percentInput, toMoneyString, toRate } from "@/components/input";
import { Facts } from "@/components/ui";
import { useFormat, useT } from "@/i18n";
import { loans } from "@/i18n/loans";

const KEYS = [
  "lender",
  "contract_number",
  "principal",
  "nominal_rate",
  "monthly_payment",
  "disbursed_on",
  "first_payment_on",
  "fixed_until",
  "follow_up_rate",
  "interest_only_before_first_payment",
  "account_iban",
  "signed_on",
] as const;

const CODE_FIELDS = {
  "loan.payment_does_not_cover_interest": "monthly_payment",
  "loan.term_too_long": "monthly_payment",
  "iban.": "account_iban",
};

export function LoanForm({
  propertyId,
  loan,
  onDone,
  onCancel,
}: {
  propertyId: string;
  loan?: Loan;
  /** In a sheet: called after a saved edit instead of going to the loan page. */
  onDone?: () => void;
  /** In a sheet: "Abbrechen" closes it instead of going back. */
  onCancel?: () => void;
}) {
  const router = useRouter();
  const t = useT(loans);
  const f = useFormat();
  const [lender, setLender] = useState(loan?.lender ?? "");
  const [contractNumber, setContractNumber] = useState(loan?.contract_number ?? "");
  const [principal, setPrincipal] = useState("");
  const [rate, setRate] = useState(percentInput(loan?.terms.nominal_rate));
  const [payment, setPayment] = useState(decimalInput(loan?.terms.monthly_payment));
  const [disbursedOn, setDisbursedOn] = useState("");
  const [firstPaymentOn, setFirstPaymentOn] = useState(loan?.terms.first_payment_on ?? "");
  const [fixedUntil, setFixedUntil] = useState(loan?.terms.fixed_until ?? "");
  const [followUpRate, setFollowUpRate] = useState(percentInput(loan?.terms.follow_up_rate));
  const [interestOnly, setInterestOnly] = useState(loan?.terms.interest_only_before_first_payment ?? false);
  const [interestOnlyInfo, setInterestOnlyInfo] = useState(false);
  const interestOnlyDescription = useId();
  const [iban, setIban] = useState("");
  const [removeIban, setRemoveIban] = useState(false);
  const [signedOn, setSignedOn] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const nominalRate = values.take("nominal_rate", toRate(rate));
    const monthlyPayment = values.take("monthly_payment", toMoneyString(payment));
    const followUp = values.optional("follow_up_rate", followUpRate, (raw) => toRate(raw));
    if (!firstPaymentOn) values.messages.first_payment_on = [t.firstMissing];
    const common = {
      lender: lender.trim(),
      contract_number: blankToNull(contractNumber),
      first_payment_on: firstPaymentOn,
      fixed_until: fixedUntil || null,
      interest_only_before_first_payment: interestOnly,
    };
    setBusy(true);
    form.clear();
    try {
      if (loan) {
        if (!values.ok || nominalRate === undefined || monthlyPayment === undefined || followUp === undefined) {
          return form.invalid(values.messages);
        }
        const body: LoanPatch = { ...common, nominal_rate: nominalRate, monthly_payment: monthlyPayment, follow_up_rate: followUp };
        if (removeIban) body.account_iban = null;
        else if (iban.trim() !== "") body.account_iban = iban.trim();
        await ok(api.PATCH("/api/loans/{loan_id}", { params: { path: { loan_id: loan.id } }, body }));
        if (onDone) onDone();
        else router.push(`/loans/${loan.id}`);
      } else {
        const amount = values.take("principal", toMoneyString(principal));
        if (!disbursedOn) values.messages.disbursed_on = [t.disbursedMissing];
        if (!values.ok || nominalRate === undefined || monthlyPayment === undefined || followUp === undefined || amount === undefined) {
          return form.invalid(values.messages);
        }
        const created = await ok(
          api.POST("/api/loans", {
            body: {
              ...common,
              property_id: propertyId,
              principal: amount,
              nominal_rate: nominalRate,
              monthly_payment: monthlyPayment,
              disbursed_on: disbursedOn,
              follow_up_rate: followUp,
              account_iban: blankToNull(iban),
              signed_on: signedOn || null,
            },
          }),
        );
        router.push(`/loans/${created.id}`);
      }
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="grid">
        <Field
          id="loan-lender"
          label={t.lender}
          description={t.lenderDescription}
          errors={form.of("lender")}
        >
          <input type="text" value={lender} onChange={(e) => setLender(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="loan-contract"
          label={t.contract}
          description={t.contractDescription}
          errors={form.of("contract_number")}
        >
          <input type="text" value={contractNumber} onChange={(e) => setContractNumber(e.target.value)} autoComplete="off" />
        </Field>
        {loan ? null : (
          <Field
            id="loan-principal"
            label={t.principalField}
            description={t.principalDescription}
            errors={form.of("principal")}
          >
            <input type="text" inputMode="decimal" value={principal} onChange={(e) => setPrincipal(e.target.value)} required autoComplete="off" />
          </Field>
        )}
        <Field
          id="loan-rate"
          label={t.rateField}
          hint={t.rateHint}
          description={t.rateDescription}
          errors={form.of("nominal_rate")}
        >
          <input type="text" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="loan-payment"
          label={t.paymentField}
          hint={t.paymentHint}
          description={t.paymentDescription}
          errors={form.of("monthly_payment")}
        >
          <input type="text" inputMode="decimal" value={payment} onChange={(e) => setPayment(e.target.value)} required autoComplete="off" />
        </Field>
        {loan ? null : (
          <Field
            id="loan-disbursed"
            label={t.disbursedField}
            description={t.disbursedDescription}
            errors={form.of("disbursed_on")}
          >
            <input type="date" value={disbursedOn} onChange={(e) => setDisbursedOn(e.target.value)} required />
          </Field>
        )}
        <Field
          id="loan-first"
          label={t.firstField}
          description={t.firstDescription}
          errors={form.of("first_payment_on")}
        >
          <input type="date" value={firstPaymentOn} onChange={(e) => setFirstPaymentOn(e.target.value)} required />
        </Field>
        <Field
          id="loan-fixed"
          label={t.fixedField}
          description={t.fixedDescription}
          errors={form.of("fixed_until")}
        >
          <input type="date" value={fixedUntil} onChange={(e) => setFixedUntil(e.target.value)} />
        </Field>
        <Field
          id="loan-follow-up"
          label={t.followUpField}
          hint={t.followUpHint}
          description={t.followUpDescription}
          errors={form.of("follow_up_rate")}
        >
          <input type="text" inputMode="decimal" value={followUpRate} onChange={(e) => setFollowUpRate(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id="loan-iban"
          label={loan ? t.ibanNew : t.iban}
          hint={loan ? t.ibanStored(loan.account_iban_masked ?? null) : t.ibanHint}
          description={t.ibanDescription}
          errors={form.of("account_iban")}
        >
          <input type="text" value={iban} onChange={(e) => setIban(e.target.value)} autoComplete="off" spellCheck={false} disabled={removeIban} />
        </Field>
        {loan ? null : (
          <Field
            id="loan-signed"
            label={t.signedField}
            description={t.signedDescription}
            errors={form.of("signed_on")}
          >
            <input type="date" value={signedOn} onChange={(e) => setSignedOn(e.target.value)} />
          </Field>
        )}
      </div>
      <div>
        <div className="field-head">
          <label className="inline">
            <input
              type="checkbox"
              checked={interestOnly}
              onChange={(e) => setInterestOnly(e.target.checked)}
              aria-describedby={interestOnlyInfo ? interestOnlyDescription : undefined}
            />
            {t.interestOnlyField}
          </label>
          <InfoToggle
            open={interestOnlyInfo}
            onToggle={() => setInterestOnlyInfo((open) => !open)}
            controls={interestOnlyDescription}
            label={t.interestOnlyField}
          />
        </div>
        <FieldDescription id={interestOnlyDescription} open={interestOnlyInfo}>
          {t.interestOnlyDescription}
        </FieldDescription>
        {form.of("interest_only_before_first_payment").length > 0 ? (
          <ul className="field-errors">
            {form.of("interest_only_before_first_payment").map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {loan?.account_iban_masked ? (
        <label className="inline">
          <input type="checkbox" checked={removeIban} onChange={(e) => setRemoveIban(e.target.checked)} />
          {t.removeIban}
        </label>
      ) : null}
      {loan ? (
        <Facts
          rows={[
            [t.principal, f.money(loan.terms.principal)],
            [t.payout, f.date(loan.terms.disbursed_on)],
          ]}
        />
      ) : null}
      {loan ? <p className="muted">{t.milestonesNote}</p> : null}
      <div className="row">
        <Submit busy={busy}>{loan ? t.save : t.create}</Submit>
        {onCancel ? (
          <button type="button" className="link" onClick={onCancel}>
            {t.cancel}
          </button>
        ) : (
          <Link href={loan ? `/loans/${loan.id}` : `/properties/${propertyId}?tab=darlehen`}>{t.cancel}</Link>
        )}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
