"use client";

// The Tilgungsplan (§4.6, §9.1): GET /loans/{id}/schedule, rendered as the API computes it
// (rules.loan_schedule). Year totals and every payment, with interest days (principle 2:
// traceable). Nothing is summed or recomputed here.
//
// Phase 10 (mockup 25): a year and a payment are rows that keep their label and amount; the
// other columns (interest, repayment, balances, rate and interest days) open in the detail panel,
// so no table scrolls sideways on a phone.
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { LoanSchedule, LoanSummary } from "@/api/schemas";
import { useFormat, useT } from "@/i18n";
import { loans } from "@/i18n/loans";
import { Icon } from "@/lib/icons";

import { DetailPanel, type Detail } from "./DetailPanel";
import { Facts, Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./pageRows.css";

export function useLoanSchedule(loanId: string) {
  const load = useCallback(() => ok(api.GET("/api/loans/{loan_id}/schedule", { params: { path: { loan_id: loanId } } })), [loanId]);
  return useLoad(load);
}

/** Restschuld heute and the anfängliche Tilgung, as the server computes them (phase 10,
 * mockup 25). Loaded on its own, so a stored loan the schedule refuses (409) leaves the
 * rest of the page standing. */
export function useLoanSummary(loanId: string) {
  const load = useCallback(() => ok(api.GET("/api/loans/{loan_id}/summary", { params: { path: { loan_id: loanId } } })), [loanId]);
  return useLoad(load);
}

/** The Restschuld text: the served balance, or „noch nicht ausgezahlt“ before the payout. */
export function useBalanceText() {
  const t = useT(loans);
  const f = useFormat();
  return (summary: LoanSummary) => (summary.balance_today === null ? t.notPaidOut : f.money(summary.balance_today));
}

/** A loan card's headline figure: Restschuld heute (nothing while loading or refused). */
export function LoanBalanceToday({ loanId }: { loanId: string }) {
  const t = useT(loans);
  const summary = useLoanSummary(loanId);
  const text = useBalanceText();
  if (!summary.data) return null;
  return (
    <div>
      <div className="page-big">{text(summary.data)}</div>
      <div className="muted">{t.balanceToday}</div>
    </div>
  );
}

/** The year totals as rows: the year, its interest and repayment, and the instalments paid.
 * `yearHref` makes each row a link (to the panel on this page, or to the loan page's panel). */
export function YearRows({
  schedule,
  yearHref,
  detail,
}: {
  schedule: LoanSchedule;
  yearHref?: (year: number) => string;
  detail?: Detail;
}) {
  const t = useT(loans);
  const f = useFormat();
  return (
    <ul className="detail-rows" aria-label={t.years}>
      {schedule.years.map((year) => {
        const content = (
          <>
            <span className="detail-row-text">
              <span className="detail-row-title">{year.year}</span>
              <span className="detail-row-sub">{t.yearSub(f.money(year.interest), f.money(year.principal))}</span>
            </span>
            <span className="num row-amount">{f.money(year.payment)}</span>
          </>
        );
        return (
          <li key={year.year}>
            {detail ? (
              <a {...detail.linkProps(String(year.year))} className="detail-row">
                {content}
                <Icon name="chevronRight" size={18} />
              </a>
            ) : yearHref ? (
              <a href={yearHref(year.year)} className="detail-row">
                {content}
                <Icon name="chevronRight" size={18} />
              </a>
            ) : (
              <div className="detail-row">{content}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Every instalment as a row: its due date and payment; the rest in the panel. */
export function EntryRows({ schedule, detail }: { schedule: LoanSchedule; detail: Detail }) {
  const t = useT(loans);
  const f = useFormat();
  return (
    <ul className="detail-rows" aria-label={t.schedule}>
      {schedule.entries.map((entry) => (
        <li key={entry.number}>
          <a {...detail.linkProps(String(entry.number))} className="detail-row">
            <span className="detail-row-text">
              <span className="detail-row-title">{t.rateTitle(f.date(entry.due_on))}</span>
              <span className="detail-row-sub">{t.rateSub(entry.number)}</span>
            </span>
            <span className="num row-amount">{f.money(entry.payment)}</span>
            <Icon name="chevronRight" size={18} />
          </a>
        </li>
      ))}
    </ul>
  );
}

/** The panel of the open instalment or year, or null. */
/** Whether the URL names an instalment or a year this schedule has, i.e. whether SchedulePanel
 * renders anything. The page passes a panel to DetailLayout only then: a panel element that
 * renders nothing still opened the layout's second column, which on a phone squeezed the page
 * itself to no width. */
export function scheduleRowOpen(schedule: LoanSchedule, rates: Detail, years: Detail): boolean {
  return (
    (rates.id !== null && schedule.entries.some((e) => String(e.number) === rates.id)) ||
    (years.id !== null && schedule.years.some((y) => String(y.year) === years.id))
  );
}

export function SchedulePanel({
  schedule,
  rates,
  years,
  backLabel,
}: {
  schedule: LoanSchedule;
  rates: Detail;
  years: Detail;
  backLabel: string;
}) {
  const t = useT(loans);
  const f = useFormat();
  const entry = rates.id ? schedule.entries.find((e) => String(e.number) === rates.id) : undefined;
  if (entry) {
    return (
      <DetailPanel title={t.ratePanel(entry.number, schedule.entries.length)} backLabel={backLabel} onClose={rates.close}>
        <Facts
          rows={[
            [t.due, f.date(entry.due_on)],
            [t.rate, f.money(entry.payment)],
            [t.interest, f.money(entry.interest)],
            [t.principalPart, f.money(entry.principal)],
            [t.openingBalance, f.money(entry.opening_balance)],
            [t.closingBalance, f.money(entry.closing_balance)],
            [t.rateAndDays, `${f.percent(entry.rate)} · ${t.days(entry.interest_days, entry.follow_up_days)}`],
          ]}
        />
      </DetailPanel>
    );
  }
  const year = years.id ? schedule.years.find((y) => String(y.year) === years.id) : undefined;
  if (year) {
    return (
      <DetailPanel title={t.yearPanel(year.year)} backLabel={backLabel} onClose={years.close}>
        <Facts
          rows={[
            [t.interest, f.money(year.interest)],
            [t.principalPart, f.money(year.principal)],
            [t.payments, f.money(year.payment)],
            [t.closingYear, f.money(year.closing_balance)],
          ]}
        />
      </DetailPanel>
    );
  }
  return null;
}

/** Year totals of one loan, loaded on its own (Darlehen tab of a property); a year opens the
 * loan page with that year in its panel. */
export function LoanYearTotals({ loanId }: { loanId: string }) {
  const schedule = useLoanSchedule(loanId);
  return (
    <Loading state={schedule}>
      {(data) => <YearRows schedule={data} yearHref={(year) => `/loans/${loanId}#d-loanyear-${year}`} />}
    </Loading>
  );
}
