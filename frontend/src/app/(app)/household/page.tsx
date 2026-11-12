"use client";

// Haushalt (phase 6 §13, docs/mockups/10-household-cashflow.png): where the household's money
// went in one month, from the confirmed allocations of the private accounts only. Einnahmen,
// Ausgaben and Sparquote with their 12-month average, the lines still without a category (a
// link to Zu prüfen), the surplus per month over twelve months, the spending by taxonomy group,
// the income rows, and „Gespart, nicht ausgegeben“: transfers to own savings, depot or
// exchange accounts are saved, not spent (D162), listed from GET /transfers. No budget and no
// target (household design §10, advice_mode own_use).
import Link from "next/link";
import { useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import {
  averageMoney,
  averageRate,
  fromCents,
  type HouseholdCashflow,
  lastFullMonth,
  loadCashflow,
  monthsUpTo,
  openLinesText,
  signedMoney,
  spent,
  toCents,
} from "@/components/cashflow";
import { formatMoney, formatMonth, formatPercent, todayIso } from "@/components/format";
import { ALLOCATION_CATEGORY, HOUSEHOLD_GROUP } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

type HouseholdGroup = components["schemas"]["HouseholdGroup"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type TransferLinkView = components["schemas"]["TransferLinkView"];

const MONTHS_SHOWN = 12;
const MONTHS_OFFERED = 24;
const SHORT_MONTH = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

interface Year {
  /** The twelve months ending at the chosen one, oldest first; the chosen one is last. */
  months: HouseholdCashflow[];
  transfers: TransferLinkView[];
}

async function loadYear(month: string): Promise<Year> {
  const [months, transfers] = await Promise.all([
    Promise.all(monthsUpTo(month, MONTHS_SHOWN).map(loadCashflow)),
    ok(api.GET("/api/transfers", { params: { query: { month } } })),
  ]);
  return { months, transfers };
}

function groupLabel(group: string): string {
  return HOUSEHOLD_GROUP[group as HouseholdGroup] ?? group;
}

function categoryLabel(category: string): string {
  return ALLOCATION_CATEGORY[category as AllocationCategory] ?? category;
}

export default function HouseholdPage() {
  const ids = useId();
  const [month, setMonth] = useState(() => lastFullMonth());
  const load = useCallback(() => loadYear(month), [month]);
  const year = useLoad(load);
  const current = todayIso().slice(0, 7);

  return (
    <section>
      <div className="page-head planning-head">
        <div>
          <p className="eyebrow">Haushalt · {formatMonth(month)}</p>
          <h1>Wohin das Geld geht</h1>
        </div>
        <span className="spacer" />
        <label htmlFor={`${ids}-month`} className="inline">
          Monat
          <select id={`${ids}-month`} value={month} onChange={(event) => setMonth(event.target.value)}>
            {monthsUpTo(current, MONTHS_OFFERED)
              .reverse()
              .map((m) => (
                <option key={m} value={m}>
                  {formatMonth(m)}
                  {m === current ? " (läuft noch)" : ""}
                </option>
              ))}
          </select>
        </label>
      </div>
      <Loading state={year}>{(data) => <HouseholdMonth year={data} />}</Loading>
    </section>
  );
}

function HouseholdMonth({ year }: { year: Year }) {
  const { months, transfers } = year;
  const month = months[months.length - 1];
  if (!month) return null;
  const spentNow = spent(month);
  return (
    <div className="stack planning">
      <div className="planning-kpis">
        <div className="planning-kpi" role="group" aria-label="Einnahmen">
          <p className="planning-kpi-label">Einnahmen</p>
          <p className="planning-kpi-value">{formatMoney(month.income)}</p>
          <p className="planning-kpi-sub">Ø 12 Monate {formatMoney(averageMoney(months.map((m) => m.income)))}</p>
        </div>
        <div className="planning-kpi" role="group" aria-label="Ausgaben">
          <p className="planning-kpi-label">Ausgaben</p>
          <p className="planning-kpi-value">{formatMoney(spentNow)}</p>
          <p className="planning-kpi-sub">Ø 12 Monate {formatMoney(averageMoney(months.map(spent)))}</p>
        </div>
        <div className="planning-kpi" role="group" aria-label="Sparquote">
          <p className="planning-kpi-label">Sparquote</p>
          <p className="planning-kpi-value planning-good">{month.savings_rate != null ? formatPercent(month.savings_rate) : "–"}</p>
          <p className="planning-kpi-sub">
            Ø 12 Monate {formatPercent(averageRate(months))} · Überschuss ÷ Einnahmen
          </p>
        </div>
        <div className={month.open_lines > 0 ? "planning-kpi household-open" : "planning-kpi"} role="group" aria-label="Ohne Kategorie">
          <p className="planning-kpi-label">Ohne Kategorie</p>
          <p className="planning-kpi-value">{openLinesText(month.open_lines)}</p>
          <p className="planning-kpi-sub">
            {month.open_lines > 0 ? <Link href="/inbox">In Zu prüfen beantworten →</Link> : "Alles zugeordnet"}
          </p>
        </div>
      </div>
      <SurplusBars months={months} />
      <div className="planning-split">
        <SpendingTable months={months} />
        <div className="stack household-side">
          <IncomeCard month={month} />
          <SavedBox month={month} transfers={transfers} />
        </div>
      </div>
    </div>
  );
}

/** Überschuss je Monat: income less spending per month, the chosen month in the strong
 * colour. The heights are the returned figures relative to the largest, for display only. */
function SurplusBars({ months }: { months: HouseholdCashflow[] }) {
  const largest = Math.max(0, ...months.map((m) => Math.abs(Number(m.surplus))));
  return (
    <section className="card" aria-labelledby="surplus-title">
      <div className="planning-card-head">
        <h2 id="surplus-title">Überschuss je Monat</h2>
        <span className="spacer" />
        <span className="muted">Einnahmen minus Ausgaben · Ø {formatMoney(averageMoney(months.map((m) => m.surplus)))}</span>
      </div>
      <ul className="planning-bars">
        {months.map((m, index) => {
          const value = Number(m.surplus);
          const height = largest > 0 ? (Math.abs(value) / largest) * 100 : 0;
          const classes = ["planning-bar"];
          if (index === months.length - 1) classes.push("planning-bar-sale");
          if (value < 0) classes.push("household-bar-negative");
          const short = SHORT_MONTH[Number(m.month.slice(5, 7)) - 1] ?? m.month;
          return (
            <li key={m.month} title={`${formatMonth(m.month)}: ${formatMoney(m.surplus)}`}>
              <span className={classes.join(" ")} style={{ height: `${height}%` }} />
              <span className="planning-bar-year" aria-hidden="true">
                {short}
              </span>
              <span className="visually-hidden">
                {formatMonth(m.month)}: {formatMoney(m.surplus)}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Ausgaben nach Kategorie: the chosen month's groups, largest first, with their share of the
 * month's spending and their 12-month average. */
function SpendingTable({ months }: { months: HouseholdCashflow[] }) {
  const month = months[months.length - 1];
  if (!month) return null;
  const total = toCents(spent(month));
  const groups = [...new Set(months.flatMap((m) => Object.keys(m.spending_by_group)))];
  const now = (group: string) => month.spending_by_group[group] ?? "0.00";
  groups.sort((a, b) => {
    const diff = toCents(now(b)) - toCents(now(a));
    return diff > 0n ? 1 : diff < 0n ? -1 : groupLabel(a).localeCompare(groupLabel(b));
  });
  return (
    <section className="card household-spending" aria-labelledby="spending-title">
      <h2 id="spending-title">Ausgaben nach Kategorie</h2>
      {groups.length === 0 ? (
        <p className="muted">In diesem Monat ist keine Ausgabe bestätigt.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Kategorie</th>
                <th scope="col">Anteil</th>
                <th scope="col" className="num">
                  {formatMonth(month.month)}
                </th>
                <th scope="col" className="num">
                  Ø 12 Mon.
                </th>
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => {
                const value = toCents(now(group));
                const share = total > 0n && value > 0n ? Number((value * 1000n) / total) / 10 : 0;
                return (
                  <tr key={group}>
                    <th scope="row">{groupLabel(group)}</th>
                    <td>
                      <span className="household-share" aria-hidden="true">
                        <span style={{ width: `${share}%` }} />
                      </span>
                    </td>
                    <td className="num">{formatMoney(now(group))}</td>
                    <td className="num muted">{formatMoney(averageMoney(months.map((m) => m.spending_by_group[group] ?? "0.00")))}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Summe</th>
                <td />
                <td className="num">
                  <strong>{formatMoney(spent(month))}</strong>
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </section>
  );
}

function IncomeCard({ month }: { month: HouseholdCashflow }) {
  const rows = Object.entries(month.income_by_category ?? {});
  return (
    <section className="card stack" aria-labelledby="income-title">
      <h2 id="income-title">Einnahmen</h2>
      {rows.length === 0 ? (
        <p className="muted">In diesem Monat ist keine Einnahme bestätigt.</p>
      ) : (
        <ul className="household-rows">
          {rows.map(([category, amount]) => (
            <li key={category}>
              <span>{categoryLabel(category)}</span>
              <strong>{formatMoney(amount)}</strong>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** „Gespart, nicht ausgegeben“ (D162): what went to own savings, depot or exchange accounts,
 * from the transfer links, and what of the surplus stayed on the current accounts. */
function SavedBox({ month, transfers }: { month: HouseholdCashflow; transfers: TransferLinkView[] }) {
  const stayed = fromCents(toCents(month.surplus) - toCents(month.saved));
  return (
    <section className="card stack notice-info" aria-labelledby="saved-title">
      <h2 id="saved-title">Gespart, nicht ausgegeben</h2>
      <p>
        Überweisungen an eigene Konten zählen nicht als Ausgaben.
        {toCents(month.saved) > 0n ? ` Auf Spar-, Depot- und Börsenkonten gingen ${formatMoney(month.saved)}.` : ""}
      </p>
      {transfers.length > 0 ? (
        <ul className="household-rows">
          {transfers.map((link) => (
            <li key={link.id}>
              <span>
                {link.out_leg.account_label} → {link.in_leg.account_label}
              </span>
              <span>{formatMoney(link.in_leg.amount)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {toCents(month.saved) > 0n ? (
        <p>
          {toCents(stayed) >= 0n
            ? `Vom Überschuss (${signedMoney(month.surplus)}) sind ${formatMoney(stayed)} auf den Girokonten geblieben.`
            : `Es wurde ${formatMoney(stayed.replace(/^-/, ""))} mehr angelegt, als in diesem Monat übrig blieb.`}
        </p>
      ) : null}
    </section>
  );
}
