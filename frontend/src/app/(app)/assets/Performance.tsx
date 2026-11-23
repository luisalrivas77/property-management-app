"use client";

// Anlagen's gain since the start (phase 10 §7.6, build decision (b)), from GET /assets/performance:
// per portfolio (a depot, an exchange, a wallet) and over the ETF tab, the Krypto tab and both,
// the value today, the money paid in and taken out, the gain = value + taken out − paid in, its
// share of the money paid in and the money-weighted yearly return. Every euro is the server's
// (principle 1); a payment opens what it was read from (principle 2). A figure the server cannot
// give is a plain to-do with its link (principle 7): a transfer without that day's price links
// „Kurs eintragen“ for its coin and day, a wallet's waiting movements „Transaktionen prüfen“, a
// depot's unassigned statement lines its account; never „0 €“ or a bare „–“.
import Link from "next/link";
import { type ReactNode, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import type { Detail } from "@/components/DetailPanel";
import { InfoToggle } from "@/components/form";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { assets } from "@/i18n/assets";

import { gainClass, signed } from "./words";

type T = Translation<(typeof assets)["de"]>;
export type AssetsPerformance = components["schemas"]["AssetsPerformance"];
export type PortfolioPerformance = components["schemas"]["PortfolioPerformance"];
export type PortfolioFigures = components["schemas"]["PortfolioFigures"];
type PortfolioFlow = components["schemas"]["PortfolioFlow"];

export const loadPerformance = (): Promise<AssetsPerformance> => ok(api.GET("/api/assets/performance"));

/** A ratio of six places ("0.300000") as a percent with one decimal: de „30,0 %“. */
export function percentOne(f: Formatter, ratio: string): string {
  return f.percent((Math.round(Number(ratio) * 1000) / 1000).toFixed(3));
}

/** The line under a gain: „+30,0 % · 5,2 % pro Jahr“, or why there is no yearly return. */
export function gainSub(t: T, f: Formatter, figures: PortfolioFigures): string {
  const parts: string[] = [];
  if (figures.gain_ratio != null) parts.push(`${figures.gain_ratio.startsWith("-") ? "" : "+"}${percentOne(f, figures.gain_ratio)}`);
  if (figures.yearly_return != null) parts.push(t.perYear(percentOne(f, figures.yearly_return)));
  else if (figures.no_rate === "short_history") parts.push(t.noRateShort);
  else if (figures.no_rate === "no_flows") parts.push(t.noRateFlows);
  else if (figures.no_rate === "no_rate") parts.push(t.noRateOther);
  if (figures.start) parts.push(t.sinceDay(f.date(figures.start)));
  return parts.join(" · ");
}

/** A planning-kpi card for a gain since the start; the to-dos when it cannot be computed. */
export function GainKpi({
  title,
  figures,
  portfolios,
  main = false,
}: {
  title: string;
  figures: PortfolioFigures;
  /** The portfolios these figures sum, for the to-dos that name what is missing. */
  portfolios: PortfolioPerformance[];
  main?: boolean;
}) {
  const t = useT(assets);
  const f = useFormat();
  const gain = figures.gain_eur;
  return (
    <div className={main ? "planning-kpi planning-kpi-main" : "planning-kpi"} role="group" aria-label={title}>
      <p className="planning-kpi-label">
        {title} <GainInfo />
      </p>
      <p className={`planning-kpi-value ${gain != null ? gainClass(gain) : ""}`}>{gain != null ? signed(f, gain) : t.gainUnknown}</p>
      {gain != null ? <p className="planning-kpi-sub">{gainSub(t, f, figures)}</p> : null}
      {figures.paid_in_eur != null && figures.taken_out_eur != null ? (
        <p className="planning-kpi-sub">{t.paidInOut(f.money(figures.paid_in_eur), f.money(figures.taken_out_eur))}</p>
      ) : null}
      <GainTodos portfolios={portfolios} />
    </div>
  );
}

/** ⓘ and the explanation it shows (§7.6: explanations behind ⓘ). */
export function InfoNote({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <>
      <InfoToggle open={open} onToggle={() => setOpen(!open)} controls={id} label={label} />
      <span className="muted field-description" id={id} hidden={!open} style={{ display: open ? "block" : undefined }}>
        {children}
      </span>
    </>
  );
}

/** ⓘ: what the gain since the start and the yearly return are. */
export function GainInfo() {
  const t = useT(assets);
  return <InfoNote label={t.gainSince}>{t.gainExplained}</InfoNote>;
}

/** What keeps a gain from being complete, per portfolio, each with its link (principle 7). */
export function GainTodos({ portfolios }: { portfolios: PortfolioPerformance[] }) {
  const t = useT(assets);
  const f = useFormat();
  const items: ReactNode[] = [];
  for (const portfolio of portfolios) {
    const unpriced = (portfolio.flows ?? []).filter((flow) => flow.amount_eur == null && flow.asset_id);
    if (unpriced.length > 0) {
      items.push(
        <li key={`${portfolio.account_id}-unpriced`}>
          {portfolio.label}: {t.gainUnpriced(unpriced.length)}{" "}
          {unpriced.map((flow, index) => (
            <span key={`${flow.asset_id}-${flow.on}-${index}`}>
              {index > 0 ? " · " : ""}
              <Link href={priceHref(flow)}>{t.typePriceOn(f.date(flow.on))}</Link>
            </span>
          ))}
        </li>,
      );
    }
    if (portfolio.waiting) {
      items.push(
        <li key={`${portfolio.account_id}-waiting`}>
          {portfolio.label}: {t.gainWaiting} <Link href={`/accounts/${portfolio.account_id}`}>{t.reviewTransactions}</Link>
        </li>,
      );
    }
    if (portfolio.unbooked_lines > 0) {
      items.push(
        <li key={`${portfolio.account_id}-unbooked`}>
          {portfolio.label}: {t.unbookedLines(portfolio.unbooked_lines)}{" "}
          <Link href={`/accounts/${portfolio.account_id}`}>{t.assignLines}</Link>
        </li>,
      );
    }
  }
  if (items.length === 0) return null;
  return <ul className="asset-gain-todos todo-text">{items}</ul>;
}

function priceHref(flow: PortfolioFlow): string {
  return `/assets?kurs=${encodeURIComponent(flow.asset_id ?? "")}&tag=${encodeURIComponent(flow.on)}`;
}

/** One portfolio as a card that opens its panel: its value and its gain since the start. */
export function PortfolioCard({
  portfolio,
  link,
  sub,
}: {
  portfolio: PortfolioPerformance;
  link: ReturnType<Detail["linkProps"]>;
  /** A line under the name: the provider, the coins it holds. */
  sub?: string | null;
}) {
  const t = useT(assets);
  const f = useFormat();
  const figures = portfolio.figures;
  const gain = figures.gain_eur;
  const unpriced = (portfolio.flows ?? []).filter((flow) => flow.amount_eur == null && flow.asset_id).length;
  const todo = portfolio.waiting
    ? t.gainWaiting
    : portfolio.unbooked_lines > 0
      ? t.unbookedLines(portfolio.unbooked_lines)
      : unpriced > 0
        ? t.gainUnpriced(unpriced)
        : null;
  return (
    <a {...link} className="card asset-portfolio-card" aria-label={portfolio.label}>
      <span className="asset-section-head">
        <strong>{portfolio.label}</strong>
        <span className="spacer" />
        <strong>{f.money(figures.value_eur)}</strong>
      </span>
      {sub ? <span className="muted asset-row-sub">{sub}</span> : null}
      <span className="asset-portfolio-gain">
        <span className="muted">{t.gainSince}</span>{" "}
        <strong className={gain != null ? gainClass(gain) : undefined}>{gain != null ? signed(f, gain) : t.gainUnknown}</strong>
      </span>
      {gain != null ? <span className="muted asset-row-sub">{gainSub(t, f, figures)}</span> : null}
      {todo ? <span className="asset-row-sub todo-text">{todo}</span> : null}
    </a>
  );
}

/** The panel's figures of one portfolio and every payment in and out it was computed from. */
export function PortfolioPayments({ portfolio }: { portfolio: PortfolioPerformance }) {
  const t = useT(assets);
  const f = useFormat();
  const flows = portfolio.flows ?? [];
  return (
    <div className="stack">
      <div className="planning-kpis">
        <GainKpi title={t.gainSince} figures={portfolio.figures} portfolios={[portfolio]} main />
      </div>
      <h3>{t.paymentsTitle}</h3>
      {flows.length === 0 ? (
        <p className="muted">{t.noPayments}</p>
      ) : (
        <ul className="asset-lots" aria-label={t.paymentsTitle}>
          {flows.map((flow, index) => (
            <li key={`${flow.kind}-${flow.on}-${index}`}>
              <div className="asset-lot-line">
                <span>
                  {f.date(flow.on)} · {flowLabel(t, flow)}
                </span>
                <strong className={flow.amount_eur != null ? gainClass(flow.amount_eur) : undefined}>
                  {flow.amount_eur != null ? signed(f, flow.amount_eur) : t.flowNoPrice}
                </strong>
              </div>
              {flow.internal ? <span className="muted asset-row-sub">{t.flowInternal}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function flowLabel(t: T, flow: PortfolioFlow): string {
  switch (flow.kind) {
    case "start":
      return t.flowStart;
    case "transfer":
      return t.flowTransfer;
    case "in_transit":
      return t.flowTransit;
    case "units":
      return t.flowUnits;
    case "trade":
      return t.flowTrade;
    case "distribution":
      return t.flowDistribution;
    default:
      return flow.kind;
  }
}
