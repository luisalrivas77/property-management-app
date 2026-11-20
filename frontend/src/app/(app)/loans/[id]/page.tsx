"use client";

// Darlehen (§9.1): terms, the Tilgungsplan (year totals and every payment, as the API
// computes them), and the linked documents (Darlehensvertrag, Zinsbescheinigungen). The payout
// (Auszahlung: date and Darlehenssumme) is a milestone, so it changes through "Auszahlung
// korrigieren", a correcting event, never through Bearbeiten; the Tilgungsplan follows it.
//
// Phase 10 (mockup 25): a year or an instalment is a row with its label and amount, its other
// figures in the detail panel; "Bearbeiten" slides the loan form in from the right (the PATCH
// body is LoanForm's, unchanged; /loans/{id}/edit stays for a direct link).
import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { DateCorrectionForm } from "@/components/corrections";
import { DetailLayout, useDetail } from "@/components/DetailPanel";
import { LinkedDocuments } from "@/components/documents";
import { correctedFrom, findMilestone } from "@/components/history";
import { EntryRows, SchedulePanel, useLoanSchedule, YearRows } from "@/components/loans";
import { SheetDialog } from "@/components/SheetDialog";
import { ActionDialog, Breadcrumbs, CorrectedNote, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { loans } from "@/i18n/loans";

import { LoanForm } from "../LoanForm";

export default function LoanPage() {
  const { id } = useParams<{ id: string }>();
  const t = useT(loans);
  const f = useFormat();
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
  const rates = useDetail("rate");
  const years = useDetail("loanyear");

  function corrected() {
    data.reload();
    schedule.reload();
  }

  const panel =
    schedule.data ? <SchedulePanel schedule={schedule.data} rates={rates} years={years} backLabel={t.schedule} /> : null;

  return (
    <section>
      <DetailLayout panel={panel}>
        <Loading state={data}>
          {({ loan, property, history }) => {
            const payout = findMilestone(history, "loan.disbursed", loan.id);
            const payoutFrom = correctedFrom(payout, loan.terms.disbursed_on);
            return (
              <>
                <Breadcrumbs items={[{ href: `/properties/${property.id}?tab=darlehen`, label: property.label }, { label: loan.lender }]} />
                <div className="page-head">
                  <h1>
                    {t.title(loan.lender)}
                    {loan.contract_number ? ` · ${loan.contract_number}` : ""}
                  </h1>
                  <span className="spacer" />
                  <ActionDialog label={t.correctPayout} title={t.correctPayout}>
                    {(close) => (
                      <DateCorrectionForm
                        idPrefix="disbursement"
                        label={t.correctPayoutLabel}
                        current={loan.terms.disbursed_on}
                        currentPrice={loan.terms.principal}
                        priceLabel={t.correctPayoutPrice}
                        priceHint={t.correctPayoutHint}
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
                  <SheetDialog label={t.edit} title={t.editTitle}>
                    {(close) => (
                      <LoanForm
                        propertyId={loan.property_id}
                        loan={loan}
                        onDone={() => {
                          close();
                          corrected();
                        }}
                        onCancel={close}
                      />
                    )}
                  </SheetDialog>
                </div>
                <Facts
                  rows={[
                    [t.principal, f.money(loan.terms.principal)],
                    [t.nominalRate, f.percent(loan.terms.nominal_rate)],
                    [t.payment, t.perMonth(f.money(loan.terms.monthly_payment))],
                    [
                      t.payout,
                      <>
                        {f.date(loan.terms.disbursed_on)}
                        {payoutFrom ? <CorrectedNote original={f.date(payoutFrom)} /> : null}
                      </>,
                    ],
                    [t.firstPayment, f.date(loan.terms.first_payment_on)],
                    [t.beforeFirst, loan.terms.interest_only_before_first_payment ? t.interestOnly : "–"],
                    [t.fixedUntil, f.date(loan.terms.fixed_until)],
                    [t.followUp, loan.terms.follow_up_rate ? f.percent(loan.terms.follow_up_rate) : t.followUpNone],
                    [t.account, loan.account_iban_masked ?? "–"],
                  ]}
                />
              </>
            );
          }}
        </Loading>
        <h2>{t.schedule}</h2>
        <Loading state={schedule}>
          {(plan) => (
            <div className="stack">
              <YearRows schedule={plan} detail={years} />
              <details>
                <summary>{t.allRates(plan.entries.length)}</summary>
                <p className="muted">{t.scheduleHint}</p>
                <EntryRows schedule={plan} detail={rates} />
              </details>
            </div>
          )}
        </Loading>
        <h2>{t.documents}</h2>
        <LinkedDocuments targetType="loan" targetId={id} />
      </DetailLayout>
    </section>
  );
}
