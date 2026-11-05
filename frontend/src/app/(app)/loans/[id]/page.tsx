"use client";

// Darlehen (§9.1): terms, the Tilgungsplan (year totals and every payment, as the API
// computes them), and the linked documents (Darlehensvertrag, Zinsbescheinigungen).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { LinkedDocuments } from "@/components/documents";
import { formatDate, formatMoney, formatPercent } from "@/components/format";
import { EntriesTable, useLoanSchedule, YearTotalsTable } from "@/components/loans";
import { Breadcrumbs, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

export default function LoanPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(async () => {
    const loan = await ok(api.GET("/api/loans/{loan_id}", { params: { path: { loan_id: id } } }));
    const property = await ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: loan.property_id } } }));
    return { loan, property };
  }, [id]);
  const data = useLoad(load);
  const schedule = useLoanSchedule(id);

  return (
    <section>
      <Loading state={data}>
        {({ loan, property }) => (
          <>
            <Breadcrumbs items={[{ href: `/properties/${property.id}?tab=darlehen`, label: property.label }, { label: loan.lender }]} />
            <div className="page-head">
              <h1>
                Darlehen {loan.lender}
                {loan.contract_number ? ` · ${loan.contract_number}` : ""}
              </h1>
              <span className="spacer" />
              <Link href={`/loans/${loan.id}/edit`} className="button-link secondary">
                Bearbeiten
              </Link>
            </div>
            <Facts
              rows={[
                ["Darlehenssumme", formatMoney(loan.terms.principal)],
                ["Nominalzins", formatPercent(loan.terms.nominal_rate)],
                ["Rate", `${formatMoney(loan.terms.monthly_payment)} / Monat`],
                ["Auszahlung", formatDate(loan.terms.disbursed_on)],
                ["Erste Rate", formatDate(loan.terms.first_payment_on)],
                ["Zinsbindung bis", formatDate(loan.terms.fixed_until)],
                ["Anschlusszins", loan.terms.follow_up_rate ? formatPercent(loan.terms.follow_up_rate) : "– (Nominalzins)"],
                ["Konto", loan.account_iban_masked ?? "–"],
              ]}
            />
          </>
        )}
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
