"use client";

// Haushalt (phase 6 §13, docs/mockups/10-household-cashflow.png): where the household's money
// went in one month, from the confirmed allocations of the private accounts only. Einnahmen,
// Ausgaben and Sparquote with their 12-month average, the lines still without a category (a
// link to Zu prüfen), the surplus per month over twelve months, the spending by taxonomy group,
// the income rows, and „Gespart, nicht ausgegeben“: transfers to own savings, depot or
// exchange accounts are saved, not spent (D162), listed from GET /transfers. No budget and no
// target (household design §10, advice_mode own_use).
//
// Phase 9 (§14, D231, D232, D242): the month is ?monat=YYYY-MM, so Übersicht and Planung link to
// it; the „Ø“ figures are the means over the window's closed months (GET /health), headed with
// their real count, and stand back for a to-do below three; the flat's result R is an income row,
// so the month's rate is visibly the household's; a month whose stored closing differs from the
// month on read carries the chip „seit dem Abschluss geändert“ with both values.
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import {
  averageMoney,
  fromCents,
  type HouseholdCashflow,
  lastFullMonth,
  loadCashflow,
  monthsUpTo,
  openLinesText,
  rateOfSums,
  signedMoney,
  spent,
  sumMoney,
  toCents,
} from "@/components/cashflow";
import { formatMoney, formatMonth, formatPercent, todayIso } from "@/components/format";
import { HealthTodoList } from "@/components/healthFigures";
import { monthRange } from "@/components/healthShared";
import { ALLOCATION_CATEGORY, HOUSEHOLD_GROUP, HOUSEHOLD_SPENDING_GROUPS } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

type HouseholdGroup = components["schemas"]["HouseholdGroup"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type TransferLinkView = components["schemas"]["TransferLinkView"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type Health = components["schemas"]["Health"];
type HouseholdMonthFigures = components["schemas"]["HouseholdMonth"];

const MONTHS_SHOWN = 12;
const MONTHS_OFFERED = 24;
const SHORT_MONTH = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

interface Year {
  /** The twelve months ending at the chosen one, oldest first; the chosen one is last. */
  months: HouseholdCashflow[];
  transfers: TransferLinkView[];
  /** The same twelve months as the household counts them (phase 9 D232, D235): the private
   * accounts plus the flat's result, so the Sparquote is the household's (Q-O139). */
  health: HealthMonthView[];
  /** GET /health: the window of closed months (D231) and its savings rate. */
  overall: Health;
  /** The window's months as the private accounts count them, oldest first; the „Ø“ columns. */
  windowMonths: HouseholdCashflow[];
}

async function loadYear(month: string): Promise<Year> {
  const shown = monthsUpTo(month, MONTHS_SHOWN);
  const [months, transfers, health, overall] = await Promise.all([
    Promise.all(shown.map(loadCashflow)),
    ok(api.GET("/api/transfers", { params: { query: { month } } })),
    ok(api.GET("/api/health/months", { params: { query: { from: shown[0] ?? month, to: month } } })),
    ok(api.GET("/api/health")),
  ]);
  const loaded = new Map(months.map((m) => [m.month, m]));
  const windowMonths = await Promise.all((overall.window.months ?? []).map((m) => loaded.get(m) ?? loadCashflow(m)));
  return { months, transfers, health, overall, windowMonths };
}

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** ?monat=YYYY-MM when it is one of the months offered, else the last full month. */
function chosenMonth(raw: string | null, current: string): string {
  if (raw && MONTH.test(raw) && monthsUpTo(current, MONTHS_OFFERED).includes(raw)) return raw;
  return lastFullMonth();
}

/** „Ø 5 Monate“: a mean headed with its real basis, the window's closed months (phase 9 D231). */
function meanLabel(count: number, short = false): string {
  return `Ø ${count} ${short ? "Mon." : count === 1 ? "Monat" : "Monate"}`;
}

/** The Geldanlage group's fallback („Sonstige Geldanlage“), listed last when the group opens. */
const INVESTMENT_FALLBACK: AllocationCategory = "hh_investment";

function groupLabel(group: string): string {
  return HOUSEHOLD_GROUP[group as HouseholdGroup] ?? group;
}

function categoryLabel(category: string): string {
  return ALLOCATION_CATEGORY[category as AllocationCategory] ?? category;
}

export default function HouseholdPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <Household />
    </Suspense>
  );
}

function Household() {
  const ids = useId();
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const current = todayIso().slice(0, 7);
  const month = chosenMonth(search.get("monat"), current);
  const setMonth = (next: string) => router.replace(`${pathname}?monat=${next}`, { scroll: false });

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
      <HouseholdYear month={month} />
    </section>
  );
}

function HouseholdYear({ month }: { month: string }) {
  const load = useCallback(() => loadYear(month), [month]);
  const year = useLoad(load);
  return <Loading state={year}>{(data) => <HouseholdMonth year={data} onChanged={year.reload} />}</Loading>;
}

function HouseholdMonth({ year, onChanged }: { year: Year; onChanged: () => void }) {
  const { months, transfers, health, overall, windowMonths } = year;
  const month = months[months.length - 1];
  if (!month) return null;
  const view = health.find((h) => h.month === month.month);
  const householdNow = view?.household;
  const spentNow = spent(month);
  const closed = overall.window.months ?? [];
  const enough = overall.window.enough;
  const means = enough ? windowMonths : [];
  const mean = (values: string[]) => (enough ? formatMoney(averageMoney(values)) : "–");
  // The window's rate as the household counts it (D235), the same Σ S ÷ Σ E as Übersicht.
  const windowViews = health.filter((h) => closed.includes(h.month)).map((h) => h.household);
  const windowRate =
    overall.savings_rate?.rate ?? (windowViews.length === closed.length ? rateOfSums(windowViews) : null);
  const last = closed[closed.length - 1];
  // What holds the month after the window open (its own closing to-dos), when it is loaded.
  const after = health.find((h) => (last === undefined || h.month > last) && !h.closed.closed);
  const tooFew = (overall.todos ?? []).filter((todo) => todo.code === "too_few_months");
  return (
    <div className="stack planning">
      {view?.stored ? <ChangedSinceClosing month={month.month} now={view.household} stored={view.stored} /> : null}
      <div className="planning-kpis">
        <div className="planning-kpi" role="group" aria-label="Einnahmen">
          <p className="planning-kpi-label">Einnahmen</p>
          <p className="planning-kpi-value">{formatMoney(month.income)}</p>
          <p className="planning-kpi-sub">
            {meanLabel(closed.length)} {mean(windowMonths.map((m) => m.income))}
          </p>
        </div>
        <div className="planning-kpi" role="group" aria-label="Ausgaben">
          <p className="planning-kpi-label">Ausgaben</p>
          <p className="planning-kpi-value">{formatMoney(spentNow)}</p>
          <p className="planning-kpi-sub">
            {meanLabel(closed.length)} {mean(windowMonths.map(spent))}
          </p>
        </div>
        <div className="planning-kpi" role="group" aria-label="Sparquote">
          <p className="planning-kpi-label">Sparquote</p>
          <p className="planning-kpi-value">
            {householdNow?.savings_rate != null ? formatPercent(householdNow.savings_rate) : "–"}
          </p>
          <p className="planning-kpi-sub">
            {meanLabel(closed.length)} {enough && windowRate != null ? formatPercent(windowRate) : "–"} · Gespart ÷ Einnahmen, mit
            dem Ergebnis der Wohnung
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
      {enough ? (
        <p className="muted">
          Basis der Durchschnitte: {closed.length} abgeschlossene Monate, {monthRange(closed)}.
        </p>
      ) : (
        <section className="card stack" aria-labelledby="window-title">
          <h2 id="window-title">Noch keine Durchschnitte</h2>
          <p className="muted">
            Die Durchschnitte erscheinen ab 3 abgeschlossenen Monaten in Folge; bisher {closed.length === 1 ? "ist es 1" : `sind es ${closed.length}`}.
          </p>
          <HealthTodoList todos={[...tooFew, ...(after?.closed.todos ?? [])]} onChanged={onChanged} />
        </section>
      )}
      <SurplusBars months={months} windowMonths={means} />
      <div className="planning-split">
        <SpendingTable months={months} windowMonths={means} />
        <div className="stack household-side">
          <IncomeCard month={month} household={householdNow ?? null} />
          <SavedBox month={month} transfers={transfers} />
        </div>
      </div>
    </div>
  );
}

/** Überschuss je Monat: income less spending per month, the chosen month in the strong
 * colour. The heights are the returned figures relative to the largest, for display only. */
function SurplusBars({ months, windowMonths }: { months: HouseholdCashflow[]; windowMonths: HouseholdCashflow[] }) {
  const largest = Math.max(0, ...months.map((m) => Math.abs(Number(m.surplus))));
  return (
    <section className="card" aria-labelledby="surplus-title">
      <div className="planning-card-head">
        <h2 id="surplus-title">Überschuss je Monat</h2>
        <span className="spacer" />
        <span className="muted">
          Einnahmen minus Ausgaben
          {windowMonths.length > 0
            ? ` · ${meanLabel(windowMonths.length)} ${formatMoney(averageMoney(windowMonths.map((m) => m.surplus)))}`
            : ""}
        </span>
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
 * month's spending and their 12-month average. A group booked under more than one code, or
 * under a subcategory (D159 amended 14 Nov 2026, migration 0032), opens to its codes, indented
 * under it, largest first and the group's fallback („Sonstige …“) last. */
function SpendingTable({ months, windowMonths }: { months: HouseholdCashflow[]; windowMonths: HouseholdCashflow[] }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const month = months[months.length - 1];
  if (!month) return null;
  const total = toCents(spent(month));
  const both = [...months, ...windowMonths];
  const groups = [...new Set(both.flatMap((m) => Object.keys(m.spending_by_group)))];
  const meanOf = (value: (m: HouseholdCashflow) => string) =>
    windowMonths.length > 0 ? formatMoney(averageMoney(windowMonths.map(value))) : "–";
  const now = (group: string) => month.spending_by_group[group] ?? "0.00";
  groups.sort((a, b) => {
    const diff = toCents(now(b)) - toCents(now(a));
    return diff > 0n ? 1 : diff < 0n ? -1 : groupLabel(a).localeCompare(groupLabel(b));
  });
  const share = (amount: string) => {
    const value = toCents(amount);
    return total > 0n && value > 0n ? Number((value * 1000n) / total) / 10 : 0;
  };
  const toggle = (group: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
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
                  {windowMonths.length > 0 ? meanLabel(windowMonths.length, true) : "Ø"}
                </th>
              </tr>
            </thead>
            <tbody>
              {groups.flatMap((group) => {
                const codes = groupCodes(both, group);
                const expanded = open.has(group);
                const rowId = `spending-${group}`;
                const rows = [
                  <tr key={group}>
                    <th scope="row">
                      {codes.length > 0 ? (
                        <button
                          type="button"
                          className="household-toggle"
                          aria-expanded={expanded}
                          aria-controls={codes.map((code) => `${rowId}-${code}`).join(" ")}
                          onClick={() => toggle(group)}
                        >
                          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span> {groupLabel(group)}
                        </button>
                      ) : (
                        groupLabel(group)
                      )}
                    </th>
                    <td>
                      <span className="household-share" aria-hidden="true">
                        <span style={{ width: `${share(now(group))}%` }} />
                      </span>
                    </td>
                    <td className="num">{formatMoney(now(group))}</td>
                    <td className="num muted">{meanOf((m) => m.spending_by_group[group] ?? "0.00")}</td>
                  </tr>,
                ];
                if (!expanded) return rows;
                const codeNow = (code: string) => month.spending_by_category?.[group]?.[code] ?? "0.00";
                const fallback = fallbackOf(group);
                const ordered = [...codes].sort((a, b) => {
                  if (a === fallback || b === fallback) return a === fallback ? 1 : -1;
                  const diff = toCents(codeNow(b)) - toCents(codeNow(a));
                  return diff > 0n ? 1 : diff < 0n ? -1 : categoryLabel(a).localeCompare(categoryLabel(b));
                });
                for (const code of ordered) {
                  rows.push(
                    <tr key={`${group}-${code}`} id={`${rowId}-${code}`} className="household-subrow">
                      <th scope="row">{categoryLabel(code)}</th>
                      <td>
                        <span className="household-share" aria-hidden="true">
                          <span style={{ width: `${share(codeNow(code))}%` }} />
                        </span>
                      </td>
                      <td className="num">{formatMoney(codeNow(code))}</td>
                      <td className="num muted">{meanOf((m) => m.spending_by_category?.[group]?.[code] ?? "0.00")}</td>
                    </tr>,
                  );
                }
                return rows;
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

/** The group's fallback code (the last of its codes in HOUSEHOLD_SPENDING_GROUPS), or null for
 * a group without subcategories. */
function fallbackOf(group: string): string | null {
  const categories = HOUSEHOLD_SPENDING_GROUPS.find((entry) => entry.group === group)?.categories;
  return categories ? (categories[categories.length - 1] ?? null) : null;
}

/** The codes a group was booked under in the twelve months, when they say more than the group
 * row does: two or more codes, or one that is not the group's fallback inside a group with
 * subcategories. Otherwise none, and the row does not open. */
function groupCodes(months: HouseholdCashflow[], group: string): string[] {
  const codes = [...new Set(months.flatMap((m) => Object.keys(m.spending_by_category?.[group] ?? {})))];
  if (codes.length > 1) return codes;
  const fallback = fallbackOf(group);
  return codes.length === 1 && fallback !== null && codes[0] !== fallback ? codes : [];
}

function IncomeCard({ month, household }: { month: HouseholdCashflow; household: HouseholdMonthFigures | null }) {
  const rows = Object.entries(month.income_by_category ?? {});
  // The flat's cash result before repayment (R, D233): part of the household's income, signed.
  const flat = household && toCents(household.rental_result) !== 0n ? household.rental_result : null;
  return (
    <section className="card stack" aria-labelledby="income-title">
      <h2 id="income-title">Einnahmen</h2>
      {rows.length === 0 && flat === null ? (
        <p className="muted">In diesem Monat ist keine Einnahme bestätigt.</p>
      ) : (
        <ul className="household-rows">
          {rows.map(([category, amount]) => (
            <li key={category}>
              <span>{categoryLabel(category)}</span>
              <strong>{formatMoney(amount)}</strong>
            </li>
          ))}
          {flat !== null ? (
            <li>
              <span>Wohnung (Ergebnis des Mietkontos)</span>
              <strong>{signedMoney(flat)}</strong>
            </li>
          ) : null}
        </ul>
      )}
      {flat !== null ? (
        <p className="muted">Das Ergebnis der Wohnung vor Tilgung zählt zu den Einnahmen des Haushalts; die Sparquote rechnet damit.</p>
      ) : null}
    </section>
  );
}

const CHANGED_ROWS: [keyof HouseholdMonthFigures, string][] = [
  ["income", "Einnahmen mit der Wohnung"],
  ["spending", "Ausgaben"],
  ["saved", "Gespart"],
];

/** „seit dem Abschluss geändert“ (D242): the month's stored closing beside the month on read. */
function ChangedSinceClosing({ month, now, stored }: { month: string; now: HouseholdMonthFigures; stored: HouseholdMonthFigures }) {
  return (
    <details className="health-sources">
      <summary>
        <span className="badge badge-warn">seit dem Abschluss geändert</span>
      </summary>
      <p className="muted">
        Seit dem Abschluss von {formatMonth(month)} hat sich eine Buchung des Monats geändert. Die festgehaltenen Kennzahlen bleiben, wie
        sie waren; diese Seite zeigt den Monat, wie er heute gebucht ist.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Zahl</th>
              <th scope="col" className="num">
                Beim Abschluss
              </th>
              <th scope="col" className="num">
                Heute
              </th>
            </tr>
          </thead>
          <tbody>
            {CHANGED_ROWS.map(([key, label]) => (
              <tr key={key}>
                <th scope="row">{label}</th>
                <td className="num">{formatMoney(stored[key] as string)}</td>
                <td className="num">{formatMoney(now[key] as string)}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">Sparquote</th>
              <td className="num">{stored.savings_rate != null ? formatPercent(stored.savings_rate) : "–"}</td>
              <td className="num">{now.savings_rate != null ? formatPercent(now.savings_rate) : "–"}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  );
}

/** „Gespart, nicht ausgegeben“ (D162): what went to own savings, depot or exchange accounts,
 * from the transfer links, plus payments booked „Geldanlage“ to an investment the app holds no
 * account of (D159 amended 13 Nov 2026, in `saved` from the API), and what of the surplus
 * stayed on the current accounts. Since D159 amended (6) (migration 0036) the Geldanlage part
 * is its own row that opens to its subcategories (Immobilien, Wertpapiere & ETF, Krypto,
 * Sparen & Tagesgeld, „Sonstige Geldanlage“ last), as a spending group opens in the table;
 * only codes with an amount this month are listed. */
function SavedBox({ month, transfers }: { month: HouseholdCashflow; transfers: TransferLinkView[] }) {
  const [open, setOpen] = useState(false);
  const stayed = fromCents(toCents(month.surplus) - toCents(month.saved));
  const invested = Object.entries(month.invested_by_category ?? {}).filter(([, amount]) => toCents(amount) !== 0n);
  invested.sort(([a, x], [b, y]) => {
    if (a === INVESTMENT_FALLBACK || b === INVESTMENT_FALLBACK) return a === INVESTMENT_FALLBACK ? 1 : -1;
    const diff = toCents(y) - toCents(x);
    return diff > 0n ? 1 : diff < 0n ? -1 : categoryLabel(a).localeCompare(categoryLabel(b));
  });
  const investedTotal = sumMoney(invested.map(([, amount]) => amount));
  // As groupCodes: the row opens when its codes say more than the row does.
  const opens = invested.length > 1 || (invested.length === 1 && invested[0]?.[0] !== INVESTMENT_FALLBACK);
  const rowId = "saved-investment";
  return (
    <section className="card stack notice-info" aria-labelledby="saved-title">
      <h2 id="saved-title">Gespart, nicht ausgegeben</h2>
      <p>
        Überweisungen an eigene Konten zählen nicht als Ausgaben.
        {toCents(month.saved) > 0n ? ` Auf Spar-, Depot- und Börsenkonten und in Geldanlagen gingen ${formatMoney(month.saved)}.` : ""}
      </p>
      {invested.length > 0 || transfers.length > 0 ? (
        <ul className="household-rows">
          {invested.length > 0 ? (
            <li>
              {opens ? (
                <button
                  type="button"
                  className="household-toggle"
                  aria-expanded={open}
                  aria-controls={invested.map(([code]) => `${rowId}-${code}`).join(" ")}
                  onClick={() => setOpen((current) => !current)}
                >
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span> {HOUSEHOLD_GROUP.investment}
                </button>
              ) : (
                <span>{HOUSEHOLD_GROUP.investment}</span>
              )}
              <span>{formatMoney(investedTotal)}</span>
            </li>
          ) : null}
          {opens && open
            ? invested.map(([code, amount]) => (
                <li key={code} id={`${rowId}-${code}`} className="household-subitem">
                  <span>{categoryLabel(code)}</span>
                  <span>{formatMoney(amount)}</span>
                </li>
              ))
            : null}
          {transfers.map((link) => (
            <li key={link.id}>
              <span>
                {link.out_leg.account_label} → {link.in_leg.account_label}
              </span>
              <span>{link.in_leg.amount != null ? formatMoney(link.in_leg.amount) : null}</span>
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
