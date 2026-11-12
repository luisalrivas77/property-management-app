"use client";

// The household's month (phase 6 §13, D162): GET /household/cashflow as the Haushalt page
// (docs/mockups/10-household-cashflow.png) and the Übersicht's Cashflow card (mockup 07) show
// it. Every figure of one month is the server's; the 12-month averages and the spending total
// are sums of those figures in integer cents (BigInt), for display only.
import Link from "next/link";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";

import { formatMoney, formatMonth, formatPercent, todayIso } from "./format";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

export type HouseholdCashflow = components["schemas"]["HouseholdCashflow"];

/** "2026-11-12" → "2026-10": the last full month, which both screens open on. */
export function lastFullMonth(today: string = todayIso()): string {
  return shiftMonth(today.slice(0, 7), -1);
}

/** "2026-01" shifted by -1 → "2025-12". */
export function shiftMonth(month: string, by: number): string {
  const [year = 0, mon = 1] = month.split("-").map(Number);
  const index = year * 12 + (mon - 1) + by;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

/** The `count` months ending at `month`, oldest first. */
export function monthsUpTo(month: string, count: number): string[] {
  return Array.from({ length: count }, (_, i) => shiftMonth(month, i - count + 1));
}

export function loadCashflow(month: string): Promise<HouseholdCashflow> {
  return ok(api.GET("/api/household/cashflow", { params: { query: { month } } }));
}

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** A money string ("1234.5", "-80.00") as cents; the API sends two places. */
export function toCents(value: string): bigint {
  const match = DECIMAL.exec(value);
  if (!match) return 0n;
  const [, sign, int = "0", frac = ""] = match;
  const cents = BigInt(int) * 100n + BigInt((frac + "00").slice(0, 2));
  return sign ? -cents : cents;
}

/** Cents back to the API's money string: -123456n → "-1234.56". */
export function fromCents(cents: bigint): string {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  return `${negative ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

/** The sum of money strings, exact. */
export function sumMoney(values: string[]): string {
  return fromCents(values.reduce((sum, value) => sum + toCents(value), 0n));
}

/** The mean of money strings, rounded half away from zero to the cent. */
export function averageMoney(values: string[]): string {
  if (values.length === 0) return "0.00";
  const n = BigInt(values.length);
  const sum = values.reduce((total, value) => total + toCents(value), 0n);
  const abs = sum < 0n ? -sum : sum;
  const mean = (2n * abs + n) / (2n * n);
  return fromCents(sum < 0n ? -mean : mean);
}

/** surplus ÷ income (the Sparquote) over several months as a fraction string with four places ("0.1563"), as the
 * server's savings_rate; null without income. */
export function averageRate(months: HouseholdCashflow[]): string | null {
  const income = months.reduce((sum, m) => sum + toCents(m.income), 0n);
  const surplus = months.reduce((sum, m) => sum + toCents(m.surplus), 0n);
  if (income <= 0n) return null;
  const size = surplus < 0n ? -surplus : surplus;
  const scaled = (2n * size * 10000n + income) / (2n * income); // half up, on the magnitude
  return `${surplus < 0n && scaled > 0n ? "-" : ""}${scaled / 10000n}.${String(scaled % 10000n).padStart(4, "0")}`;
}

/** What the month's spending groups add up to (income less surplus, as the server computed). */
export function spent(month: HouseholdCashflow): string {
  return sumMoney(Object.values(month.spending_by_group));
}

/** "+1.470,00 €" for a surplus, "-80,00 €" for a deficit. */
export function signedMoney(value: string): string {
  return `${value.startsWith("-") || /^0+(\.0+)?$/.test(value) ? "" : "+"}${formatMoney(value)}`;
}

const loadLastMonth = () => loadCashflow(lastFullMonth());

/** The Übersicht's Cashflow card (mockup 07): last month's surplus, money in and out and the
 * savings rate, linking to the Haushalt page. A month without any private booking says so. */
export function CashflowCard() {
  const month = lastFullMonth();
  const state = useLoad(loadLastMonth);
  return (
    <section className="card stack" aria-labelledby="cashflow-card-title">
      <h2 id="cashflow-card-title">Cashflow {formatMonth(month)}</h2>
      <Loading state={state}>
        {(cashflow) =>
          cashflow.income === "0.00" && Object.keys(cashflow.spending_by_group).length === 0 ? (
            <p className="muted">
              Für diesen Monat ist noch keine private Buchung bestätigt. Die Privatkonten werden unter{" "}
              <Link href="/accounts">Konten</Link> importiert.
              {cashflow.open_lines > 0 ? (
                <>
                  {" "}
                  <Link href="/inbox">{openLinesText(cashflow.open_lines)} in Zu prüfen</Link>
                </>
              ) : null}
            </p>
          ) : (
            <>
              <p className="cashflow-value">{signedMoney(cashflow.surplus)}</p>
              <p className="muted">
                {formatMoney(cashflow.income)} rein · {formatMoney(spent(cashflow))} raus
                {cashflow.savings_rate != null ? ` · Sparquote ${formatPercent(cashflow.savings_rate)}` : ""}
              </p>
              <p>
                <Link href="/household">Zum Haushalt</Link>
                {cashflow.open_lines > 0 ? (
                  <>
                    {" · "}
                    <Link href="/inbox">{openLinesText(cashflow.open_lines)} ohne Kategorie</Link>
                  </>
                ) : null}
              </p>
            </>
          )
        }
      </Loading>
    </section>
  );
}

/** "1 Umsatz" / "2 Umsätze". */
export function openLinesText(count: number): string {
  return count === 1 ? "1 Umsatz" : `${count} Umsätze`;
}
