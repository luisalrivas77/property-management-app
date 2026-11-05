"use client";

// The Tilgungsplan (§4.6, §9.1): GET /loans/{id}/schedule, rendered as the API computes it
// (rules.loan_schedule). Year totals and every payment, with interest days (principle 2:
// traceable). Nothing is summed or recomputed here.
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { LoanSchedule } from "@/api/schemas";

import { formatDate, formatMoney, formatPercent } from "./format";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

export function useLoanSchedule(loanId: string) {
  const load = useCallback(() => ok(api.GET("/api/loans/{loan_id}/schedule", { params: { path: { loan_id: loanId } } })), [loanId]);
  return useLoad(load);
}

export function YearTotalsTable({ schedule }: { schedule: LoanSchedule }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Jahr</th>
            <th className="num">Zinsen</th>
            <th className="num">Tilgung</th>
            <th className="num">Raten</th>
            <th className="num">Restschuld Jahresende</th>
          </tr>
        </thead>
        <tbody>
          {schedule.years.map((year) => (
            <tr key={year.year}>
              <td>{year.year}</td>
              <td className="num">{formatMoney(year.interest)}</td>
              <td className="num">{formatMoney(year.principal)}</td>
              <td className="num">{formatMoney(year.payment)}</td>
              <td className="num">{formatMoney(year.closing_balance)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EntriesTable({ schedule }: { schedule: LoanSchedule }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="num">Nr.</th>
            <th>Fällig</th>
            <th className="num">Zinssatz</th>
            <th className="num">Zinstage</th>
            <th className="num">Restschuld vorher</th>
            <th className="num">Zinsen</th>
            <th className="num">Tilgung</th>
            <th className="num">Rate</th>
            <th className="num">Restschuld nachher</th>
          </tr>
        </thead>
        <tbody>
          {schedule.entries.map((entry) => (
            <tr key={entry.number}>
              <td className="num">{entry.number}</td>
              <td>{formatDate(entry.due_on)}</td>
              <td className="num">{formatPercent(entry.rate)}</td>
              <td className="num">
                {entry.interest_days}
                {entry.follow_up_days > 0 ? ` (${entry.follow_up_days} Anschluss)` : ""}
              </td>
              <td className="num">{formatMoney(entry.opening_balance)}</td>
              <td className="num">{formatMoney(entry.interest)}</td>
              <td className="num">{formatMoney(entry.principal)}</td>
              <td className="num">{formatMoney(entry.payment)}</td>
              <td className="num">{formatMoney(entry.closing_balance)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Year totals of one loan, loaded on its own (Darlehen tab of a property). */
export function LoanYearTotals({ loanId }: { loanId: string }) {
  const schedule = useLoanSchedule(loanId);
  return <Loading state={schedule}>{(data) => <YearTotalsTable schedule={data} />}</Loading>;
}
