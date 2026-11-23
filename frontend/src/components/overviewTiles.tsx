"use client";

// Übersicht's tiles (mockup 15-B2, phase 10 D268): the net worth with the month's cash flow under
// it, one tile per kind of asset (property, ETF depot, crypto, bank) with its colour as an edge
// stripe, the three key figures with their mark as a dot before the name (an empty dot while no
// limit is confirmed; never a colour word), and Zu prüfen. A tapped tile opens its summary in the
// detail panel with one button into its section. Every euro figure and every share is the
// server's (/net-worth/overview, /health, /household/cashflow, /inbox, /properties/equity,
// /properties/payback; /health/months only when /health's last ended month is not the tile's);
// the browser only counts rows and formats.
import Link from "next/link";
import type { ReactNode } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { CashflowCard, lastFullMonth, loadCashflow, type HouseholdCashflow } from "@/components/cashflow";
import { DetailPanel, type Detail } from "@/components/DetailPanel";
import type { Health, Light } from "@/components/healthShared";
import { NetWorthSection } from "@/components/netWorth";
import { payInputsHref } from "@/components/taxYear";
import { Loading } from "@/components/ui";
import type { Loaded } from "@/components/useLoad";
import { useFormat, useT, type Formatter } from "@/i18n";
import { overview as catalogue } from "@/i18n/overview";
import { Icon } from "@/lib/icons";
import { recentInbox } from "@/lib/inboxCount";

import "./overviewTiles.css";

type NetWorth = components["schemas"]["NetWorth"];
type NetWorthLine = components["schemas"]["NetWorthLine"];
type InboxItem = components["schemas"]["InboxItem"];
type PropertiesPaidIn = components["schemas"]["PropertiesPaidIn"];
type PropertyPaidIn = components["schemas"]["PropertyPaidIn"];
type PropertiesPayback = components["schemas"]["PropertiesPayback"];
type PropertyPayback = components["schemas"]["PropertyPayback"];
type T = (typeof catalogue)["de"];

export type TileId = "nettovermoegen" | "cashflow" | "immobilie" | "depot" | "krypto" | "bank" | "sparquote" | "reserve" | "verschuldung" | "pruefen";

export interface MonthCashflow {
  month: string;
  cashflow: HouseholdCashflow;
  householdRate: string | null;
}

/** Last full month's private cash flow and the household's savings rate of that month, as the
 * Cashflow card reads them (phase 9 D235). */
export async function loadMonthCashflow(): Promise<MonthCashflow> {
  const month = lastFullMonth();
  const [cashflow, householdRate] = await Promise.all([loadCashflow(month), householdRateOf(month)]);
  return { month, cashflow, householdRate };
}

/** The household's savings rate of `month` from GET /health's `last_ended` (the key figures'
 * tile's own read, one request for both, fetchShared; phase 10 §7.3) when that is the month;
 * else, or when GET /health failed, from GET /health/months for the month, as before. Both are
 * the server's same household month. */
async function householdRateOf(month: string): Promise<string | null> {
  const health = await loadHealthOnly().catch(() => null);
  const last = health?.last_ended;
  if (last != null && last.month === month) return last.savings_rate ?? null;
  const months = await ok(api.GET("/api/health/months", { params: { query: { from: month, to: month } } }));
  return months[0]?.household.savings_rate ?? null;
}

export const loadNetWorthToday = async (): Promise<NetWorth> => (await ok(api.GET("/api/net-worth/overview"))).today;
export const loadHealthOnly = (): Promise<Health> => ok(api.GET("/api/health"));
/** The list the menu's count last read (lib/inboxCount.ts), so one Übersicht load computes GET
 * /inbox once; a kept list at most TILE_REUSE_MS old (lib/inboxCount.ts). */
export const loadInbox = (): Promise<InboxItem[]> => recentInbox();
/** The own money put into each property and its total, summed by the server (Luis, 21 Nov 2026). */
export const loadPaidIn = (): Promise<PropertiesPaidIn> => ok(api.GET("/api/properties/equity"));
/** The cash back against the own money put in, the break-even year and a sale today, per property
 * and in total, all computed by the server (Luis, 21 Nov 2026: „Both“). */
export const loadPayback = (): Promise<PropertiesPayback> => ok(api.GET("/api/properties/payback"));

export interface TileData {
  netWorth: Loaded<NetWorth>;
  health: Loaded<Health>;
  cashflow: Loaded<MonthCashflow>;
  inbox: Loaded<InboxItem[]>;
  paidIn: Loaded<PropertiesPaidIn>;
  payback: Loaded<PropertiesPayback>;
}

/** The server's percent points with one decimal ("-0.3") as de „-0,3 %“, en „-0.3%“; unlike
 * f.percent, which takes a fraction. */
function points(f: Formatter, value: string): string {
  return f.locale === "de" ? `${f.decimal(value)} %` : `${f.decimal(value)}%`;
}

/** The break-even line: the year, or the sale year it is not reached by; null when neither, and
 * null when the server sends no percent (paid_in ≤ 0: nothing put in, nothing to earn back, so
 * „nicht zurück“ would be false). */
function breakEvenText(
  t: T,
  back: { break_even_year?: number | null; sale_year?: number | null; percent?: string | null },
): string | null {
  if (back.percent == null) return null;
  if (back.break_even_year != null) return t.breakEven(back.break_even_year);
  if (back.sale_year != null) return t.breakEvenNot(back.sale_year);
  return null;
}

/** How many properties the server left out of the totals (each with its reason in the panel). */
function leftOut(back: PropertiesPayback): number {
  return back.properties.filter((p) => p.reason != null).length;
}

/** The profit today misses a tax: a counted property's sale is taxable with a gain but no income
 * is entered (the server's tax_not_computed). */
function profitIncomplete(back: PropertiesPayback): boolean {
  return back.properties.some((p) => p.reason == null && p.sale_today?.tax_not_computed);
}

/** "1200.00" → "-1200.00", "-5" → "5": a sign flip on the string, for the loan shown as a debt. */
function negated(value: string): string {
  return value.startsWith("-") ? value.slice(1) : /^0+(\.0+)?$/.test(value) ? value : `-${value}`;
}

function signed(f: Formatter, value: string): string {
  return `${value.startsWith("-") || /^0+(\.0+)?$/.test(value) ? "" : "+"}${f.money(value)}`;
}

/** One decimal for the reserve's months, as the server sends it ("4.3"). */
function months(f: Formatter, value: string): string {
  return f.decimal(value);
}

const ASSET_LINE_KINDS = new Set<NetWorthLine["account_kind"]>(["bank_current", "bank_savings"]);

function bankAccounts(lines: NetWorthLine[]): number {
  return new Set(lines.filter((l) => l.account_kind && ASSET_LINE_KINDS.has(l.account_kind)).map((l) => l.account_id)).size;
}

function cardLines(lines: NetWorthLine[]): NetWorthLine[] {
  return lines.filter((l) => l.account_kind === "credit_card" && !l.in_transit);
}

function positions(lines: NetWorthLine[], cls: NetWorthLine["net_worth_class"]): number {
  return lines.filter((l) => l.net_worth_class === cls && l.kind === "position").length;
}

/** The mark of one key figure: a dot before the name, filled in the confirmed colour, empty
 * while the household has set no limit (D250, D268). The dot is decoration; its meaning is read
 * out from the figure's own sentence. */
function Dot({ light, t }: { light: Light | undefined; t: T }) {
  const colour = light?.colour ?? null;
  const sentence = colour ? (light?.texts?.[0] ?? light?.sentences?.[0] ?? "") : t.noLimit;
  return (
    <>
      <span className="tile-dot" data-mark={colour ?? "none"} aria-hidden="true" />
      {sentence ? <span className="visually-hidden">{sentence} </span> : null}
    </>
  );
}

function Tile({
  id,
  detail,
  stripe,
  className,
  name,
  children,
}: {
  id: TileId;
  detail: Detail;
  stripe?: "property" | "funds" | "crypto" | "cash";
  className?: string;
  name: ReactNode;
  children: ReactNode;
}) {
  return (
    <a {...detail.linkProps(id)} className={`tile ${className ?? ""}`} data-stripe={stripe}>
      <span className="tile-name">{name}</span>
      {children}
    </a>
  );
}

function Wait<D>({ state, children }: { state: Loaded<D>; children: (data: D) => ReactNode }) {
  if (state.data !== undefined) return <>{children(state.data)}</>;
  return <span className="tile-sub">{state.error ? "–" : "…"}</span>;
}

/** The split of the gross assets by kind, from /health's shares (D240): a bar and its legend. */
function SplitBar({ split, t, f }: { split: Record<string, string>; t: T; f: Formatter }) {
  const parts = (
    [
      ["property", t.shareProperty],
      ["depot", t.shareDepot],
      ["crypto", t.shareCrypto],
      ["bank", t.shareBank],
    ] as const
  ).filter(([key]) => split[key] != null && !/^0+(\.0+)?$/.test(split[key] ?? ""));
  if (parts.length === 0) return null;
  return (
    <div className="tile-split">
      <div className="tile-split-bar" role="img" aria-label={`${t.splitLabel}: ${parts.map(([key, label]) => `${label} ${f.percent(split[key])}`).join(", ")}`}>
        {parts.map(([key]) => (
          <span key={key} data-kind={key} style={{ flexGrow: 0, flexBasis: `calc(${split[key]} * 100%)` }} />
        ))}
      </div>
      <ul className="tile-legend" aria-hidden="true">
        {parts.map(([key, label]) => (
          <li key={key}>
            <span className="tile-legend-swatch" data-kind={key} />
            {label} {f.percent(split[key])}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function OverviewTiles({ data, detail }: { data: TileData; detail: Detail }) {
  const t = useT(catalogue);
  const f = useFormat();
  const { netWorth, health, cashflow, inbox, payback } = data;
  const lines = netWorth.data?.lines ?? [];
  const cards = cardLines(lines);
  const marks = health.data?.marks ?? {};
  const rate = health.data?.savings_rate ?? null;
  const reserve = health.data?.reserve ?? null;
  const debt = health.data?.debt ?? null;
  const windowMonths = (rate?.months ?? []).map((m) => m.month);
  const items = inbox.data ?? [];

  return (
    <section className="tiles" aria-label={t.tilesLabel}>
      <div className="tiles-money">
        <Tile id="nettovermoegen" detail={detail} className="tile-wide tile-hero" name={t.netWorth}>
          <Wait state={netWorth}>
            {(today) => (
              <>
                <span className="tile-value tile-value-big">{f.money(today.total)}</span>
                <span className="tile-sub">{t.today}</span>
                {(today.todos ?? []).length > 0 ? (
                  <span className="tile-note">{t.netWorthTodos((today.todos ?? []).length)}</span>
                ) : null}
                {health.data?.split ? <SplitBar split={health.data.split} t={t} f={f} /> : null}
              </>
            )}
          </Wait>
        </Tile>
        <Tile id="cashflow" detail={detail} className="tile-wide" name={t.cashflow(f.month(cashflow.data?.month ?? lastFullMonth()))}>
          <Wait state={cashflow}>
            {({ cashflow: month, householdRate }) =>
              month.income === "0.00" && Object.keys(month.spending_by_group).length === 0 ? (
                <span className="tile-sub">{t.cashflowEmpty}</span>
              ) : (
                <>
                  <span className="tile-value">{signed(f, month.surplus)}</span>
                  <span className="tile-sub">
                    {t.cashflowIn} {f.money(month.income)}
                    {householdRate != null ? ` · ${t.cashflowLeft(f.percent(householdRate))}` : ""}
                  </span>
                </>
              )
            }
          </Wait>
        </Tile>
      </div>

      <div className="tiles-grid">
        <Tile id="immobilie" detail={detail} stripe="property" name={t.property}>
          <Wait state={payback}>
            {(back) =>
              back.properties.some((p) => p.reason == null) ? (
                <>
                  <span className="tile-value">{f.money(back.cash_back)}</span>
                  <span className="tile-sub">{t.backSoFar}</span>
                  <span className="tile-sub">{t.backOf(f.money(back.paid_in), back.percent != null ? points(f, back.percent) : null)}</span>
                  {breakEvenText(t, back) ? (
                    <span className="tile-sub">{breakEvenText(t, back)}</span>
                  ) : back.properties.some((p) => p.reason == null && p.projection_error_code) ? (
                    <span className="tile-note">{t.breakEvenCheckPlan}</span>
                  ) : null}
                  {leftOut(back) > 0 ? <span className="tile-note">{t.leftOutTile(leftOut(back))}</span> : null}
                </>
              ) : (
                <>
                  <span className="tile-value">–</span>
                  <span className="tile-sub">{t.backSoFar}</span>
                  <span className="tile-sub">{t.backNone}</span>
                  {leftOut(back) > 0 ? <span className="tile-note">{t.leftOutTile(leftOut(back))}</span> : null}
                </>
              )
            }
          </Wait>
          <dl className="tile-pairs">
            <div>
              <dt>{t.profitToday}</dt>
              <dd>
                <Wait state={payback}>
                  {(back) =>
                    back.properties.some((p) => p.reason == null) ? (
                      <>
                        {f.money(back.profit_today)}
                        {profitIncomplete(back) ? <span className="tile-note"> ({t.profitNoTax})</span> : null}
                      </>
                    ) : (
                      "–"
                    )
                  }
                </Wait>
              </dd>
            </div>
            <div>
              <dt>{t.propertyNet}</dt>
              <dd>
                <Wait state={netWorth}>{(today) => f.money(today.property_net)}</Wait>
              </dd>
            </div>
          </dl>
        </Tile>
        <Tile id="depot" detail={detail} stripe="funds" name={t.depot}>
          <Wait state={netWorth}>
            {(today) => (
              <>
                <span className="tile-value">{f.money(today.by_class.depot ?? "0.00")}</span>
                <span className="tile-sub">{t.positions(positions(today.lines ?? [], "depot"))}</span>
              </>
            )}
          </Wait>
        </Tile>
        <Tile id="krypto" detail={detail} stripe="crypto" name={t.crypto}>
          <Wait state={netWorth}>
            {(today) => (
              <>
                <span className="tile-value">{f.money(today.by_class.crypto ?? "0.00")}</span>
                <span className="tile-sub">{t.positions(positions(today.lines ?? [], "crypto"))}</span>
              </>
            )}
          </Wait>
        </Tile>
        <Tile id="bank" detail={detail} stripe="cash" name={t.bank}>
          <Wait state={netWorth}>
            {(today) => (
              <>
                <span className="tile-value">{f.money(today.by_class.bank ?? "0.00")}</span>
                <span className="tile-sub">
                  {t.bankAccounts(bankAccounts(lines))}
                  {cards.length > 0 ? `, ${t.cards(cards.length)}` : ""}
                </span>
                {cards.length === 1 && cards[0]?.value_eur != null ? (
                  <dl className="tile-pairs">
                    <div>
                      <dt>{t.creditCard}</dt>
                      <dd>{f.money(cards[0].value_eur)}</dd>
                    </div>
                  </dl>
                ) : null}
              </>
            )}
          </Wait>
        </Tile>
      </div>

      <div className="tiles-grid tiles-figures">
        <Tile
          id="sparquote"
          detail={detail}
          name={
            <>
              <Dot light={marks.savings_rate} t={t} />
              {t.savingsRate}
            </>
          }
        >
          <Wait state={health}>
            {() => (
              <>
                <span className="tile-value">{rate?.rate != null ? f.percent(rate.rate) : "–"}</span>
                <span className="tile-sub">
                  {windowMonths.length > 1
                    ? t.windowAvg(f.month(windowMonths[0]), f.month(windowMonths[windowMonths.length - 1]))
                    : windowMonths.length === 1
                      ? t.windowOne(f.month(windowMonths[0]))
                      : t.noFigure}
                </span>
              </>
            )}
          </Wait>
        </Tile>
        <Tile
          id="reserve"
          detail={detail}
          name={
            <>
              <Dot light={marks.reserve} t={t} />
              {t.reserve}
            </>
          }
        >
          <Wait state={health}>
            {() => (
              <>
                <span className="tile-value">{reserve?.months != null ? t.reserveMonths(months(f, reserve.months)) : "–"}</span>
                <span className="tile-sub">{reserve ? t.reserveTarget(months(f, reserve.target_months)) : t.noFigure}</span>
              </>
            )}
          </Wait>
        </Tile>
        <Tile
          id="verschuldung"
          detail={detail}
          name={
            <>
              <Dot light={marks.debt_ratio} t={t} />
              {t.debtRatio}
            </>
          }
        >
          <Wait state={health}>
            {() => (
              <>
                <span className="tile-value">{debt?.debt_ratio != null ? f.percent(debt.debt_ratio) : "–"}</span>
                <span className="tile-sub">{t.debtRatioSub}</span>
              </>
            )}
          </Wait>
        </Tile>
        <Tile id="pruefen" detail={detail} className="tile-review" name={t.review}>
          <Wait state={inbox}>
            {() => (
              <>
                <span className="tile-value">{f.count(items.length)}</span>
                <span className="tile-sub">{items.length === 0 ? t.reviewNothing : reviewText(items, t)}</span>
              </>
            )}
          </Wait>
        </Tile>
      </div>
      <p className="muted tiles-foot">{t.footnote}</p>
    </section>
  );
}

function reviewText(items: InboxItem[], t: T): string {
  const lines = items.filter((item) => item.transaction_id).length;
  const documents = items.filter((item) => !item.transaction_id && (item.document_id || item.proposal_id)).length;
  const other = items.length - lines - documents;
  return [lines ? t.reviewLines(lines) : null, documents ? t.reviewDocuments(documents) : null, other ? t.reviewOther(other) : null]
    .filter(Boolean)
    .join(" · ");
}

// --- the panels ---------------------------------------------------------------------------

function Rows({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="tile-panel-rows">
      {rows.map(([label, value], index) => (
        <div key={index}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** „Eigenkapital eingesetzt“ traced to its parts, per property as the server computed it; with
 * several properties each gets its own block and the server's total follows. */
function PaidInRows({ state, t, f }: { state: Loaded<PropertiesPaidIn>; t: T; f: Formatter }) {
  if (state.data === undefined) return <p className="muted">{state.error ? "–" : "…"}</p>;
  const { properties, total_paid_in } = state.data;
  if (properties.length === 0) return null;
  const several = properties.length > 1;
  const block = (p: PropertyPaidIn) => {
    const rows: [ReactNode, ReactNode][] = [];
    if (p.purchase_price != null) rows.push([t.paidInPrice, f.money(p.purchase_price)]);
    rows.push([t.paidInCosts, f.money(p.acquisition_costs)], [t.paidInLoanCosts, f.money(p.own_loan_costs)], [t.paidInLoans, f.money(negated(p.loan_principal))]);
    rows.push([t.paidInTotal, p.paid_in != null ? f.money(p.paid_in) : p.reason === "ownership_ended" ? t.paidInEnded : t.paidInNoPrice]);
    return (
      <div key={p.property_id}>
        {several ? <p className="tile-panel-kicker">{p.label}</p> : null}
        <Rows rows={rows} />
      </div>
    );
  };
  return (
    <>
      {properties.map(block)}
      {several ? <Rows rows={[[t.paidInAll, f.money(total_paid_in)]]} /> : null}
    </>
  );
}

/** Planung › Immobilie of one property, where its default scenario is changed. */
const planningHref = (propertyId: string) => `/planning?section=immobilie&immobilie=${propertyId}`;

/** A gap and the link that resolves it (principle 7): an inline line under the figure. */
function Gap({ text, href, action }: { text: ReactNode; href?: string | null; action?: string }) {
  return (
    <p className="muted">
      {text}
      {href && action ? (
        <>
          {" "}
          <Link href={href}>{action} ›</Link>
        </>
      ) : null}
    </p>
  );
}

/** Why a property is left out, with the page that fixes it where one exists. */
function leftOutGap(p: PropertyPayback, t: T): { href: string | null; action?: string } {
  switch (p.reason) {
    case "no_purchase_price":
      return { href: `/properties/${p.property_id}`, action: t.enterPrice };
    case "loan_not_valued":
      return { href: p.loan_id ? `/loans/${p.loan_id}` : `/properties/${p.property_id}`, action: t.openLoan };
    case "not_computable":
      return { href: planningHref(p.property_id), action: t.toPropertyPlanning };
    default:
      return { href: null };
  }
}

/** The years behind „Zurück bisher“ (the actual ones, which sum to it) and behind the break-even
 * year (the plan's), as the server sent them: no sum is made here. */
function YearRows({ p, t, f }: { p: PropertyPayback; t: T; f: Formatter }) {
  const years = p.years ?? [];
  const estimated = new Set(p.tax_estimated_years ?? []);
  const missing = new Set(p.income_missing_years ?? []);
  const actual = years.filter((y) => y.kind !== "projected");
  const planned = years.filter((y) => y.kind === "projected");
  const value = (y: (typeof years)[number]) => {
    const money = f.money(y.net_cash);
    if (missing.has(y.year)) return t.yearNoTax(money);
    if (estimated.has(y.year)) return t.yearEstimated(money);
    return money;
  };
  return (
    <>
      {actual.length > 0 ? (
        <>
          <p className="tile-panel-kicker">{t.yearsKicker}</p>
          <Rows rows={actual.map((y) => [y.kind === "actual_partial" ? t.yearSoFar(y.year) : String(y.year), value(y)] as [ReactNode, ReactNode])} />
        </>
      ) : null}
      {planned.length > 0 ? (
        <details>
          <summary className="muted">{t.plannedYears}</summary>
          <Rows rows={planned.map((y) => [String(y.year), t.plannedYear(f.money(y.net_cash), f.money(y.cumulative))] as [ReactNode, ReactNode])} />
        </details>
      ) : null}
    </>
  );
}

/** „Zurück bisher“ against „Eigenkapital eingesetzt“ and the sale today traced to its parts, per
 * property as the server computed them; with several properties each gets its own block and the
 * server's totals follow. A property the server left out says why, and every gap links to the
 * page that resolves it (principle 7). */
function PaybackRows({ state, t, f }: { state: Loaded<PropertiesPayback>; t: T; f: Formatter }) {
  if (state.data === undefined) return <p className="muted">{state.error ? "–" : "…"}</p>;
  const back = state.data;
  if (back.properties.length === 0) return null;
  const several = back.properties.length > 1;
  const thisYear = new Date().getFullYear();
  const block = (p: PropertyPayback) => {
    const kicker = several ? <p className="tile-panel-kicker">{p.label}</p> : null;
    if (p.reason != null || p.cash_back == null || p.paid_in == null || p.sale_today == null) {
      const gap = leftOutGap(p, t);
      return (
        <div key={p.property_id}>
          {kicker}
          <Gap text={p.reason != null ? t.backLeftOut(p.reason) : t.backNone} href={gap.href} action={gap.action} />
        </div>
      );
    }
    const sold = p.sale_today;
    const line = breakEvenText(t, p);
    const rows: [ReactNode, ReactNode][] = [[t.backSoFar, f.money(p.cash_back)]];
    if (p.percent != null) rows.push([t.backOf(f.money(p.paid_in), null), points(f, p.percent)]);
    if (line) rows.push([line, ""]);
    const tax: [ReactNode, ReactNode] = sold.tax_not_computed
      ? [t.saleTaxNotComputed, "–"]
      : [sold.taxable ? t.saleTax : t.saleTaxNone, f.money(negated(sold.sale_tax))];
    const sale: [ReactNode, ReactNode][] = [
      [t.saleValue, f.money(sold.value)],
      [t.saleCosts(f.percent(sold.selling_costs_rate)), f.money(negated(sold.selling_costs))],
      tax,
      [t.saleLoan, f.money(negated(sold.loan_balance))],
      [t.saleBack, f.money(p.cash_back)],
      [t.salePaidIn, f.money(negated(p.paid_in))],
      [sold.tax_not_computed ? `${t.saleProfit} (${t.profitNoTax})` : t.saleProfit, f.money(sold.profit)],
    ];
    return (
      <div key={p.property_id}>
        {kicker}
        <Rows rows={rows} />
        {p.projection_error_code ? (
          <Gap
            text={t.projectionMissing(p.projection_error_code)}
            href={planningHref(p.property_id)}
            action={p.projection_error_code === "scenario.sale_year_before_today" ? t.fixPlanning : t.toPropertyPlanning}
          />
        ) : null}
        <YearRows p={p} t={t} f={f} />
        <p className="tile-panel-kicker">{t.profitToday}</p>
        <Rows rows={sale} />
        {sold.tax_not_computed ? <Gap text={t.taxNotComputedWhy} href={payInputsHref(thisYear)} action={t.enterIncome} /> : null}
        {sold.taxable && sold.tax_free_from ? <p className="muted">{t.saleTaxFree(f.date(sold.tax_free_from))}</p> : null}
      </div>
    );
  };
  const totalLine = breakEvenText(t, back);
  const totals: [ReactNode, ReactNode][] = [
    [t.backSoFar, f.money(back.cash_back)],
    [t.backOf(f.money(back.paid_in), null), back.percent != null ? points(f, back.percent) : "–"],
  ];
  if (totalLine) totals.push([totalLine, ""]);
  totals.push([profitIncomplete(back) ? `${t.profitToday} (${t.profitNoTax})` : t.profitToday, f.money(back.profit_today)]);
  const years = (list: number[] | undefined) => (list ?? []).join(", ");
  const missing = back.income_missing_years ?? [];
  return (
    <>
      {back.properties.map(block)}
      {several ? (
        <>
          <p className="tile-panel-kicker">{t.backAll}</p>
          <Rows rows={totals} />
        </>
      ) : null}
      {back.tax_estimated_years?.length ? <p className="muted">{t.taxEstimated(years(back.tax_estimated_years))}</p> : null}
      {missing.length ? <Gap text={t.incomeMissing(years(missing))} href={payInputsHref(missing[0] ?? thisYear)} action={t.enterIncome} /> : null}
      <p className="muted">{t.backYears}</p>
      <p className="muted">{t.backTilgung}</p>
      <p className="muted">{t.backBreakEven}</p>
      <p className="muted">{t.saleNote}</p>
    </>
  );
}

function Go({ href, children }: { href: string; children: ReactNode }) {
  // A section of this page: a plain anchor, so the browser's hashchange closes the panel (its
  // state is the hash) and scrolls to the section.
  if (href.startsWith("#"))
    return (
      <a href={href} className="button-link tile-panel-go">
        {children}
        <Icon name="chevronRight" size={18} />
      </a>
    );
  return (
    <Link href={href} className="button-link tile-panel-go">
      {children}
      <Icon name="chevronRight" size={18} />
    </Link>
  );
}

function Sentences({ light }: { light: Light | undefined }) {
  const texts = light?.texts ?? [];
  if (texts.length === 0) return null;
  return (
    <div className="stack">
      {texts.map((text) => (
        <p key={text}>{text}</p>
      ))}
    </div>
  );
}

const TITLES: Record<TileId, (t: T) => string> = {
  nettovermoegen: (t) => t.netWorth,
  cashflow: (t) => t.cashflow(""),
  immobilie: (t) => t.property,
  depot: (t) => t.depot,
  krypto: (t) => t.crypto,
  bank: (t) => t.bank,
  sparquote: (t) => t.savingsRate,
  reserve: (t) => t.reserve,
  verschuldung: (t) => t.debtRatio,
  pruefen: (t) => t.review,
};

export function isTileId(id: string | null): id is TileId {
  return id !== null && id in TITLES;
}

export function TilePanel({
  id,
  data,
  detail,
  properties,
}: {
  id: TileId;
  data: TileData;
  detail: Detail;
  properties: { id: string; label: string }[];
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const { netWorth, health, cashflow, inbox, paidIn, payback } = data;
  const marks = health.data?.marks ?? {};
  const title = id === "cashflow" ? t.cashflow(f.month(cashflow.data?.month ?? lastFullMonth())) : TITLES[id](t);
  const single = properties.length === 1 ? properties[0] : undefined;

  let body: ReactNode;
  switch (id) {
    case "nettovermoegen":
      body = (
        <>
          <NetWorthSection onCryptoRefreshed={netWorth.reload} />
          <Go href="/assets">{t.toAssets}</Go>
        </>
      );
      break;
    case "cashflow": {
      const month = cashflow.data?.month ?? lastFullMonth();
      body = (
        <>
          <CashflowCard />
          <Go href={`/household?monat=${month}`}>{t.toHousehold(f.month(month))}</Go>
        </>
      );
      break;
    }
    case "immobilie":
      body = (
        <Loading state={netWorth}>
          {(today) => (
            <>
              <PaybackRows state={payback} t={t} f={f} />
              <p className="tile-panel-kicker">{t.paidIn}</p>
              <PaidInRows state={paidIn} t={t} f={f} />
              <Rows
                rows={[
                  [t.propertyNet, f.money(today.property_net)],
                  [t.propertyValue, f.money(today.property_estimate)],
                  [t.propertyLoan, f.money(negated(today.loan_balances))],
                ]}
              />
              {single ? <Go href={`/properties/${single.id}`}>{t.toProperty}</Go> : <Go href="/properties">{t.toProperties}</Go>}
              <Go href={single ? `/planning?section=immobilie&immobilie=${single.id}` : "/planning?section=immobilie"}>{t.toPlanning}</Go>
            </>
          )}
        </Loading>
      );
      break;
    case "depot":
    case "krypto":
    case "bank": {
      const cls = id === "depot" ? "depot" : id === "krypto" ? "crypto" : "bank";
      body = (
        <Loading state={netWorth}>
          {(today) => (
            <>
              <Rows
                rows={(today.lines ?? [])
                  .filter((line) => line.net_worth_class === cls || (id === "bank" && line.account_kind === "credit_card"))
                  .map((line) => [line.label, line.value_eur != null ? f.money(line.value_eur) : t.notValued] as [ReactNode, ReactNode])}
              />
              <p className="muted">{t.balanceOn(f.date(today.on))}</p>
              {id === "depot" ? <Go href="/assets?tab=etf">{t.toEtf}</Go> : id === "krypto" ? <Go href="/assets?tab=krypto">{t.toCrypto}</Go> : <Go href="/accounts?art=bank">{t.toBank}</Go>}
            </>
          )}
        </Loading>
      );
      break;
    }
    case "sparquote": {
      const rate = health.data?.savings_rate ?? null;
      body = (
        <Loading state={health}>
          {() => (
            <>
              <Rows rows={[[t.savingsRate, rate?.rate != null ? f.percent(rate.rate) : "–"]]} />
              <Sentences light={marks.savings_rate} />
              {(rate?.months ?? []).length > 0 ? (
                <div>
                  <strong>{t.monthsOfRate}</strong>
                  <ul className="tile-panel-months">
                    {(rate?.months ?? []).map(({ month, rate: monthRate }) => (
                      <li key={month}>
                        <Link href={`/household?monat=${month}`}>{f.month(month)}</Link>
                        {monthRate != null ? ` · ${f.percent(monthRate)}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <Go href="#figures-title">{t.toFigures}</Go>
            </>
          )}
        </Loading>
      );
      break;
    }
    case "reserve": {
      const reserve = health.data?.reserve ?? null;
      body = (
        <Loading state={health}>
          {() => (
            <>
              {reserve ? (
                <Rows
                  rows={[
                    [t.reserveAmount, f.money(reserve.amount)],
                    [t.reserveNeed, f.money(reserve.need)],
                    [t.reserveGoal, `${t.reserveMonths(months(f, reserve.target_months))} · ${f.money(reserve.target)}`],
                    [t.reserveGap, f.money(reserve.gap)],
                  ]}
                />
              ) : (
                <p className="muted">{t.noFigure}</p>
              )}
              <Sentences light={marks.reserve} />
              <Go href="/planning?section=reserve">{t.toReserve}</Go>
            </>
          )}
        </Loading>
      );
      break;
    }
    case "verschuldung": {
      const debt = health.data?.debt ?? null;
      body = (
        <Loading state={health}>
          {() => (
            <>
              {debt ? (
                <Rows
                  rows={[
                    [t.debtRatio, debt.debt_ratio != null ? f.percent(debt.debt_ratio) : "–"],
                    [t.liabilities, f.money(debt.liabilities)],
                    [t.grossAssets, f.money(debt.gross_assets)],
                  ]}
                />
              ) : (
                <p className="muted">{t.noFigure}</p>
              )}
              <Sentences light={marks.debt_ratio} />
              {marks.debt_ratio?.colour ? null : <p className="muted">{t.noLimit}</p>}
              <Go href="#figures-title">{t.toFigures}</Go>
            </>
          )}
        </Loading>
      );
      break;
    }
    case "pruefen":
      body = (
        <Loading state={inbox}>
          {(items) => (
            <>
              <p>{items.length === 0 ? t.reviewNothing : reviewText(items, t)}</p>
              <Go href="/inbox">{t.toReview}</Go>
            </>
          )}
        </Loading>
      );
      break;
  }

  return (
    <DetailPanel title={title} backLabel={t.title} onClose={detail.close}>
      <div className="stack">
        <p className="tile-panel-kicker">{t.summary}</p>
        {body}
      </div>
    </DetailPanel>
  );
}
