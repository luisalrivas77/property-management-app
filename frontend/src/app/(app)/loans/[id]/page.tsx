"use client";

// Darlehen (§9.1): terms, the Tilgungsplan (year totals and every payment, as the API
// computes them), and the linked documents (Darlehensvertrag, Zinsbescheinigungen). The payout
// (Auszahlung: date and Darlehenssumme) is a milestone, so it changes through "Auszahlung
// korrigieren", a correcting event, never through Bearbeiten; the Tilgungsplan follows it.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { DateCorrectionForm } from "@/components/corrections";
import { LinkedDocuments } from "@/components/documents";
import { formatDate, formatMoney, formatPercent } from "@/components/format";
import { correctedFrom, findMilestone } from "@/components/history";
import { EntriesTable, useLoanSchedule, YearTotalsTable } from "@/components/loans";
import { ActionDialog, Breadcrumbs, CorrectedNote, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

export default function LoanPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(async () => {
    const loan = await ok(api.GET("/api/loans/{loan_id}", { params: { path: { loan_id: id } } }));
    const path = { params: { path: { property_id: loan.property_id } } };
    const [property, history] = await Promise.all([
      ok(api.GET("/api/properties/{property_id}", path)),
      ok(api.GET("/api/properties/{property_id}/history", path)),
    ]);
    return { loan, property, history };
  }, [id]);
  const data = useLoad(load);
  const schedule = useLoanSchedule(id);

  function corrected() {
    data.reload();
    schedule.reload();
  }

  return (
    <section>
      <Loading state={data}>
        {({ loan, property, history }) => {
          const payout = findMilestone(history, "loan.disbursed", loan.id);
          const payoutFrom = correctedFrom(payout, loan.terms.disbursed_on);
          return (
            <>
              <Breadcrumbs items={[{ href: `/properties/${property.id}?tab=darlehen`, label: property.label }, { label: loan.lender }]} />
              <div className="page-head">
                <h1>
                  Darlehen {loan.lender}
                  {loan.contract_number ? ` · ${loan.contract_number}` : ""}
                </h1>
                <span className="spacer" />
                <ActionDialog label="Auszahlung korrigieren" title="Auszahlung korrigieren">
                  {(close) => (
                    <DateCorrectionForm
                      idPrefix="disbursement"
                      label="Richtiges Auszahlungsdatum"
                      current={loan.terms.disbursed_on}
                      currentPrice={loan.terms.principal}
                      priceLabel="Darlehenssumme"
                      priceHint="Leer lassen, wenn die Darlehenssumme stimmt."
                      onSubmit={(values) =>
                        ok(
                          api.POST("/api/loans/{loan_id}/corrections/disbursement", {
                            params: { path: { loan_id: loan.id } },
                            body: { new_date: values.new_date, principal: values.purchase_price ?? null },
                          }),
                        )
                      }
                      onDone={() => {
                        close();
                        corrected();
                      }}
                    />
                  )}
                </ActionDialog>
                <Link href={`/loans/${loan.id}/edit`} className="button-link secondary">
                  Bearbeiten
                </Link>
              </div>
              <Facts
                rows={[
                  ["Darlehenssumme", formatMoney(loan.terms.principal)],
                  ["Nominalzins", formatPercent(loan.terms.nominal_rate)],
                  ["Rate", `${formatMoney(loan.terms.monthly_payment)} / Monat`],
                  [
                    "Auszahlung",
                    <>
                      {formatDate(loan.terms.disbursed_on)}
                      {payoutFrom ? <CorrectedNote original={formatDate(payoutFrom)} /> : null}
                    </>,
                  ],
                  ["Erste Rate", formatDate(loan.terms.first_payment_on)],
                  ["Zinsbindung bis", formatDate(loan.terms.fixed_until)],
                  ["Anschlusszins", loan.terms.follow_up_rate ? formatPercent(loan.terms.follow_up_rate) : "– (Nominalzins)"],
                  ["Konto", loan.account_iban_masked ?? "–"],
                ]}
              />
            </>
          );
        }}
      </Loading>
      <h2>Tilgungsplan</h2>
      <Loading state={schedule}>
        {(plan) => (
          <div className="stack">
            <YearTotalsTable schedule={plan} />
            <details>
              <summary>Alle {plan.entries.length} Raten</summary>
              <EntriesTable schedule={plan} />
            </details>
          </div>
        )}
      </Loading>
      <h2>Dokumente</h2>
      <LinkedDocuments targetType="loan" targetId={id} />
    </section>
  );
}
