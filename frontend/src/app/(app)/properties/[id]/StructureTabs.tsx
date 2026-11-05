"use client";

// Einheiten and Darlehen of a property. Each loan shows its year totals from the Tilgungsplan
// (GET /loans/{id}/schedule); the loan page has every payment.
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { formatArea, formatDate, formatDecimal, formatFraction, formatMoney, formatPercent } from "@/components/format";
import { LoanYearTotals } from "@/components/loans";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

export function UnitsTab({ propertyId }: { propertyId: string }) {
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/units", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const units = useLoad(load);
  return (
    <div className="stack">
      <Loading state={units}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Einheiten. Eine ETW hat meist eine Wohnung, oft dazu einen Stellplatz.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Einheit</th>
                    <th className="num">Fläche</th>
                    <th className="num">Zimmer</th>
                    <th className="num">MEA</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((unit) => (
                    <tr key={unit.id}>
                      <td>
                        <Link href={`/units/${unit.id}`}>{unit.label}</Link>
                      </td>
                      <td className="num">{formatArea(unit.floor_area_m2)}</td>
                      <td className="num">{formatDecimal(unit.rooms)}</td>
                      <td className="num">{formatFraction(unit.mea)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      <div>
        <Link href={`/properties/${propertyId}/units/new`} className="button-link">
          Einheit anlegen
        </Link>
      </div>
    </div>
  );
}

export function LoansTab({ propertyId }: { propertyId: string }) {
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/loans", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const loans = useLoad(load);
  return (
    <div className="stack">
      <Loading state={loans}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Darlehen.</p>
          ) : (
            list.map((loan) => (
              <article key={loan.id} className="card stack">
                <h2>
                  <Link href={`/loans/${loan.id}`}>
                    {loan.lender}
                    {loan.contract_number ? ` · ${loan.contract_number}` : ""}
                  </Link>
                </h2>
                <p className="muted">
                  {formatMoney(loan.terms.principal)} zu {formatPercent(loan.terms.nominal_rate)}, Rate {formatMoney(loan.terms.monthly_payment)},
                  ausgezahlt {formatDate(loan.terms.disbursed_on)}
                  {loan.terms.fixed_until ? `, Zinsbindung bis ${formatDate(loan.terms.fixed_until)}` : ""}
                </p>
                <h3>Tilgungsplan (Jahressummen)</h3>
                <LoanYearTotals loanId={loan.id} />
                <p>
                  <Link href={`/loans/${loan.id}`}>Alle Raten und Details</Link>
                </p>
              </article>
            ))
          )
        }
      </Loading>
      <div>
        <Link href={`/loans/new?property=${propertyId}`} className="button-link">
          Darlehen anlegen
        </Link>
      </div>
    </div>
  );
}
