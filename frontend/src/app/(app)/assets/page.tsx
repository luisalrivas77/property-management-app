"use client";

// Anlagen (phase 10 mockup 20, §7.6): four sections as chips, ?tab=… links (etf, krypto,
// immobilie). Übersicht (./Overview.tsx) is the total, large, with the split of the assets inside
// it, and one card per kind; no fund or coin is listed there (Luis, 23 Nov 2026). `?kurs=<asset
// id>` opens a price form in the detail panel. The tab also holds what Übersicht showed of the
// net worth over time (components/healthFigures.tsx, AssetsHealthSection): `?month_end=YYYY-MM-DD`
// opens the card „Monatsabschluss festhalten“ for that month-end, and „Vermögen im Verlauf“
// follows. Under it since phase 10 wave 3 (a) (§7.9 (a), D288): what was Planung › Vermögen, the
// net worth in some years on the rates the household typed (`#annahme`), its rates changed in place
// with „Rendite ändern“ (`#rendite`, §7.10 (a)), components/householdPlan.tsx.
//
// Anlagen › ETF (phase 6 step 4, §13; phase 10 §7.6): „ETF gesamt“, the depots' gain since the
// start (./Performance.tsx, GET /assets/performance), this year's distributions, the savings plans
// into a depot (GET /household-plan's detected plans), one card per depot whose panel lists its
// payments in and out and its funds (GET /assets/etf), and the depots' documents.
// Since phase 10 D280 Anteile, Kurs and Wert are the Übersicht's (the net worth's valuation,
// D167): the newest stored daily price with its source and day; a fund whose report disagrees
// with the booked trades has no value (D165 (3)), and a fund no daily source prices shows „Kurs
// aus dem Bericht vom …“ with the badge „kein Tageskurs“ (D174 (4)). The report's own price and
// value appear only as „Zur Kontrolle“ in the position's panel (D267, `#d-fonds-<account>~<asset>`).
// Einstand is the plain sum of the purchases while a fund was only bought (lots are phase 7).
// The Sparer-Pauschbetrag and Vorabpauschale cards (phase 8, D170, no figure) are gone (§7.6).
//
// Anlagen › Krypto (phase 7 step 6, ./Crypto.tsx): lots and the holding period; its „Kurs
// eintragen“ opens Übersicht's price forms with `?kurs=<asset>&tag=<day>`.
//
// Anlagen › Immobilie (./Property.tsx): the property's Planung, moved here from Planung; an old
// /planning link that names a property lands here (lib/planningRoutes.ts).
import "./assets.css";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { todayIso } from "@/components/format";
import { AssetsHealthSection } from "@/components/healthFigures";
import { HouseholdProjection, PlanProvider, useStaleAssumptionsRedirect } from "@/components/householdPlan";
import { DOCUMENT_SUBTYPE } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { common } from "@/i18n/common";
import { assets } from "@/i18n/assets";

import { CryptoTab } from "./Crypto";
import { OverviewTab } from "./Overview";
import { type AssetsPerformance, GainKpi, InfoNote, loadPerformance, PortfolioCard, PortfolioPayments } from "./Performance";
import { PropertyTab } from "./Property";
import { PortfolioParts } from "./PortfolioParts";
import { gainClass, priceText, readingLabel, signed } from "./words";

type T = Translation<(typeof assets)["de"]>;
type EtfOverview = components["schemas"]["EtfToday"];
type EtfAccount = components["schemas"]["EtfAccountToday"];
type EtfPosition = components["schemas"]["EtfPositionToday"];
type DepotDocumentStatus = components["schemas"]["DepotDocumentStatus"];
type DocumentSubtype = components["schemas"]["DocumentSubtype"];
type HouseholdPlan = components["schemas"]["HouseholdPlan"];

type TabKey = "uebersicht" | "etf" | "krypto" | "immobilie";
const TABS: readonly string[] = ["etf", "krypto", "immobilie"];

export default function AssetsPage() {
  return (
    <Suspense fallback={<Fallback />}>
      <Assets />
    </Suspense>
  );
}

function Fallback() {
  const c = useT(common);
  return <p className="muted">{c.loading}</p>;
}

function Assets() {
  const t = useT(assets);
  useStaleAssumptionsRedirect("/assets");
  const f = useFormat();
  const search = useSearchParams();
  const priceFor = search.get("kurs");
  const priceDay = search.get("tag");
  const asked = search.get("tab");
  const monthEnd = search.get("month_end");
  const tab: TabKey = priceFor || monthEnd ? "uebersicht" : asked && TABS.includes(asked) ? (asked as TabKey) : "uebersicht";
  const tabs: { key: TabKey; label: string; href: string }[] = [
    { key: "uebersicht", label: t.tabOverview, href: "/assets" },
    { key: "etf", label: t.tabEtf, href: "/assets?tab=etf" },
    { key: "krypto", label: t.tabCrypto, href: "/assets?tab=krypto" },
    { key: "immobilie", label: t.tabProperty, href: "/assets?tab=immobilie" },
  ];
  return (
    <section>
      <div className="page-head">
        <div>
          <h1>{t.title}</h1>
          <p className="muted">{t.asOf(f.date(todayIso()))}</p>
        </div>
      </div>
      <nav className="asset-chips" aria-label={t.sections}>
        {tabs.map((item) => (
          <Link key={item.key} href={item.href} aria-current={item.key === tab ? "page" : undefined}>
            {item.label}
          </Link>
        ))}
      </nav>
      {tab === "etf" ? (
        <EtfTab />
      ) : tab === "krypto" ? (
        <CryptoTab />
      ) : tab === "immobilie" ? (
        <PropertyTab />
      ) : (
        <PlanProvider page="/assets" backLabel={t.tabOverview}>
          <AssetsHealthSection after={<HouseholdProjection />}>
            <OverviewTab priceFor={priceFor} priceDay={priceDay} />
          </AssetsHealthSection>
        </PlanProvider>
      )}
    </section>
  );
}

function EtfTab() {
  const load = useCallback(async () => {
    const [etf, performance, plan] = await Promise.all([
      ok(api.GET("/api/assets/etf")),
      loadPerformance(),
      // „Sparpläne“ only; the tab shows without it (a plan needs three closed months).
      ok(api.GET("/api/household-plan")).catch(() => null),
    ]);
    return { etf, performance, plan };
  }, []);
  const state = useLoad(load);
  return <Loading state={state}>{(data) => <Etf {...data} />}</Loading>;
}

const fundKey = (accountId: string, assetId: string) => `${accountId}~${assetId}`;

function Etf({ etf, performance, plan }: { etf: EtfOverview; performance: AssetsPerformance; plan: HouseholdPlan | null }) {
  const t = useT(assets);
  const f = useFormat();
  const depot = useDetail("depot");
  const fund = useDetail("fonds");
  const accounts = etf.accounts ?? [];
  const portfolios = (performance.portfolios ?? []).filter((portfolio) => portfolio.kind === "depot");
  const selectedFund = accounts
    .flatMap((account) => (account.positions ?? []).map((position) => ({ account, position })))
    .find(({ account, position }) => fundKey(account.account_id, position.asset_id) === fund.id);
  const selectedDepot = portfolios.find((portfolio) => portfolio.account_id === depot.id);
  const depotAccount = selectedDepot ? accounts.find((account) => account.account_id === selectedDepot.account_id) : undefined;
  const panel = selectedFund ? (
    <DetailPanel
      title={selectedFund.position.name}
      backLabel={selectedFund.account.label}
      onClose={() => depot.open(selectedFund.account.account_id)}
    >
      <FundPanel account={selectedFund.account} position={selectedFund.position} />
    </DetailPanel>
  ) : selectedDepot ? (
    <DetailPanel title={selectedDepot.label} backLabel={t.tabEtf} onClose={depot.close}>
      <div className="stack">
        <PortfolioPayments portfolio={selectedDepot} />
        {depotAccount ? (
          <Positions
            account={depotAccount}
            linkProps={(assetId) => fund.linkProps(fundKey(depotAccount.account_id, assetId))}
          />
        ) : null}
        <Link href={`/accounts/${selectedDepot.account_id}`} className="button-link secondary">
          {t.openAccount(selectedDepot.label)}
        </Link>
      </div>
    </DetailPanel>
  ) : null;
  const plans = (plan?.plans ?? []).filter((row) => row.asset_class === "depot");
  return (
    <DetailLayout panel={panel}>
      <div className="stack">
        {accounts.length === 0 ? (
          <section className="card stack">
            <h2>{t.positions}</h2>
            <p className="muted">{t.noFunds}</p>
            <p className="row">
              <Link href="/accounts">{t.openAccounts}</Link> <Link href="/documents">{t.openDocuments}</Link>
            </p>
          </section>
        ) : (
          <>
            <section className="card stack asset-total" aria-label={t.etfTotal}>
              <p className="planning-kpi-label">{t.etfTotal}</p>
              <p className="asset-total-value">{f.money(performance.etf.value_eur)}</p>
              <p className="muted">
                {t.depotsCount(portfolios.length)} · {t.onDay(f.date(performance.as_of))}
              </p>
            </section>
            <div className="planning-kpis">
              <GainKpi title={t.gainSince} figures={performance.etf} portfolios={portfolios} main />
              <div className="planning-kpi" role="group" aria-label={t.distributionsTitle(performance.distributions_year)}>
                <p className="planning-kpi-label">{t.distributionsTitle(performance.distributions_year)}</p>
                <p className="planning-kpi-value">{f.money(performance.distributions_eur)}</p>
                <p className="planning-kpi-sub">{t.distributionsSub(f.money(performance.distributions_tax_eur))}</p>
              </div>
              <div className="planning-kpi" role="group" aria-label={t.savingsPlansTitle}>
                <p className="planning-kpi-label">{t.savingsPlansTitle}</p>
                {plans.length === 0 ? (
                  <p className="planning-kpi-sub">{t.noSavingsPlans}</p>
                ) : (
                  plans.map((row) => (
                    <p key={row.key} className="planning-kpi-sub">
                      <strong>{f.money(row.monthly)}</strong>{" "}
                      {row.destination_payee ??
                        accounts.find((account) => account.account_id === row.destination_account_id)?.label ??
                        ""}
                      {row.months.length > 0 ? ` · ${t.savingsPlansSub(f.month(row.months[row.months.length - 1]))}` : ""}
                    </p>
                  ))
                )}
              </div>
            </div>
            <div className="asset-pair">
              {portfolios.map((portfolio) => (
                <PortfolioCard key={portfolio.account_id} portfolio={portfolio} link={depot.linkProps(portfolio.account_id)} />
              ))}
            </div>
          </>
        )}
        <Postbox documents={etf.documents ?? []} />
      </div>
    </DetailLayout>
  );
}

type LinkProps = ReturnType<ReturnType<typeof useDetail>["linkProps"]>;

function Positions({ account, linkProps }: { account: EtfAccount; linkProps: (assetId: string) => LinkProps }) {
  const t = useT(assets);
  const f = useFormat();
  const cash = (account.cash ?? []).filter((line) => line.value_eur != null);
  return (
    <section className="card stack" aria-label={t.positionsOf(account.label)}>
      <div className="asset-section-head">
        <h2>
          {cash.length > 0 ? t.portfolioTotal : t.positions} · {f.money(account.total_eur)}
        </h2>
        <span className="spacer" />
        <Link href={`/accounts/${account.account_id}`}>{account.label} ›</Link>
      </div>
      {cash.length > 0 ? <PortfolioParts label={account.label} funds={account.value_eur} cash={cash} /> : null}
      <ul className="asset-rows" data-cols="etf">
        <li className="asset-rows-head asset-col" aria-hidden="true">
          <span>{t.colEtf}</span>
          <span className="num">{t.colUnits}</span>
          <span className="num">{t.colPrice}</span>
          <span className="num">{t.colValue}</span>
          <span className="num">{t.colCost}</span>
          <span className="num">{t.colGain}</span>
        </li>
        {(account.positions ?? []).map((position) => (
          <li key={position.asset_id}>
            <FundRow position={position} link={linkProps(position.asset_id)} />
          </li>
        ))}
      </ul>
      <p className="muted hint">
        {t.todayValues} <InfoNote label={t.todayValues}>{t.todayHint}</InfoNote>
      </p>
    </section>
  );
}

function FundRow({ position, link }: { position: EtfPosition; link: LinkProps }) {
  const t = useT(assets);
  const f = useFormat();
  const gain = position.gain_today_eur;
  const price = position.price ? priceText(t, f, position.price, position.from_report ?? false) : null;
  const note = valueNote(t, f, position);
  return (
    <a {...link} className="asset-row">
      <span className="asset-row-name">
        <strong>{position.name}</strong>
        <span className="asset-row-sub">{position.isin}</span>
        <span className="asset-row-sub asset-phone">{[t.unitsOf(f.decimal(position.units)), price].filter(Boolean).join(" · ")}</span>
        {position.from_report ? <span className="badge badge-warn">{t.noDailyPrice}</span> : null}
        {position.stale && !position.from_report ? <span className="badge badge-warn">{t.stale}</span> : null}
        {note ? <span className="asset-row-sub todo-text">{note}</span> : null}
      </span>
      <span className="num asset-col">{f.decimal(position.units)}</span>
      <span className="num asset-col">
        {position.price ? (
          <>
            {f.money(position.price.price_eur)}
            <span className="asset-row-sub" style={{ display: "block" }}>
              {price}
            </span>
          </>
        ) : (
          "–"
        )}
      </span>
      <span className="num asset-value">{valueText(t, f, position)}</span>
      <span className="num asset-col" title={position.cost_eur == null ? t.costAfterSale : undefined}>
        {position.cost_eur != null ? f.money(position.cost_eur) : "–"}
      </span>
      <span className={`num asset-col ${gainClass(gain)}`}>{gain != null ? signed(f, gain) : "–"}</span>
    </a>
  );
}

/**
 * A fund's value today; „nicht bewertet“ only when the server gives a reason, so a fund sold in
 * full (no units today, no reason) shows „–“.
 */
function valueText(t: T, f: Formatter, position: EtfPosition): string {
  if (position.value_today_eur != null) return f.money(position.value_today_eur);
  return position.not_valued_reason ? t.notValued : "–";
}

/**
 * Why a fund shows no value, as a plain to-do (principle 7), by the server's reason only; ""
 * when it has a value or no reason (a fund sold in full).
 */
function valueNote(t: T, f: Formatter, position: EtfPosition): string {
  if (position.value_today_eur != null) return "";
  if (position.not_valued_reason === "valuation.not_valued") {
    return position.report_on ? t.noteReportMismatch(f.date(position.report_on)) : t.noteReportMismatchUndated;
  }
  if (position.not_valued_reason === "price.no_source") return t.noteNoPrice;
  return "";
}

/** The panel of a tapped fund: today's figures, then the depot report as a check only (D280). */
function FundPanel({ account, position }: { account: EtfAccount; position: EtfPosition }) {
  const t = useT(assets);
  const f = useFormat();
  const note = valueNote(t, f, position);
  const gain = position.gain_today_eur;
  return (
    <div className="stack">
      <p className="muted">{[account.label, position.isin].filter(Boolean).join(" · ")}</p>
      <dl className="facts">
        <div>
          <dt>{t.units}</dt>
          <dd>{f.decimal(position.units)}</dd>
        </div>
        <div>
          <dt>{t.priceToday}</dt>
          <dd>
            {position.price ? f.money(position.price.price_eur) : "–"}
            {position.price ? (
              <span className="asset-row-sub" style={{ display: "block" }}>
                {priceText(t, f, position.price, position.from_report ?? false)}
              </span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>{t.valueToday}</dt>
          <dd>
            <strong>{valueText(t, f, position)}</strong>
          </dd>
        </div>
        <div>
          <dt>{t.colCost}</dt>
          <dd>{position.cost_eur != null ? f.money(position.cost_eur) : t.costAfterSale}</dd>
        </div>
        <div>
          <dt>{t.colGain}</dt>
          <dd className={gainClass(gain)}>{gain != null ? signed(f, gain) : "–"}</dd>
        </div>
      </dl>
      {position.from_report ? <span className="badge badge-warn">{t.noDailyPrice}</span> : null}
      {note ? (
        <div className="notice notice-warn" role="status">
          <p>
            {note} {position.not_valued_reason === "valuation.not_valued" ? <Link href="/inbox">{t.actionInbox}</Link> : null}
          </p>
        </div>
      ) : null}
      <div className="asset-check" role="group" aria-label={t.check}>
        <h3>{t.check}</h3>
        <p>{reportLine(t, f, position)}</p>
        {position.report_on ? <p>{reportVerdict(t, position)}</p> : null}
      </div>
      <p className="muted hint">
        {t.priceSources} <InfoNote label={t.priceSources}>{t.dailyHint}</InfoNote>
      </p>
      <Link href={`/accounts/${account.account_id}`} className="button-link secondary">
        {t.openAccount(account.label)}
      </Link>
    </div>
  );
}

function reportLine(t: T, f: Formatter, position: EtfPosition): string {
  if (!position.report_on) return t.checkNoReport;
  const day = f.date(position.report_on);
  if (position.report_price_eur && position.value_eur) {
    return t.checkReport(day, f.money(position.report_price_eur), f.money(position.value_eur));
  }
  return t.checkReportUnits(day, f.decimal(position.report_units ?? "0"));
}

function reportVerdict(t: T, position: EtfPosition): string {
  if (!position.reconciled) return t.checkDisagrees;
  if (position.moved_since_report) return t.checkMovedSince;
  return t.checkAgrees;
}

function readingBadge(document: DepotDocumentStatus): string {
  if (document.reading === "applied") return "badge";
  if (document.reading === "ready") return "badge badge-info";
  if (document.reading === "failed" || document.reading === "blocked_tenant_data") return "badge badge-warn";
  return "badge badge-muted";
}

function subtypeText(t: T, document: DepotDocumentStatus): string {
  // Document kinds stay in German until the Dokumente screen's catalogue carries them.
  return document.subtype ? (DOCUMENT_SUBTYPE[document.subtype as DocumentSubtype] ?? document.subtype) : t.document;
}

function Postbox({ documents }: { documents: DepotDocumentStatus[] }) {
  const t = useT(assets);
  const f = useFormat();
  return (
    <section className="card stack" aria-labelledby="postbox-title">
      <h2 id="postbox-title">{t.documents}</h2>
      {documents.length === 0 ? (
        <p className="muted">
          {t.noDocuments} <Link href="/documents">{t.openDocuments}</Link>
        </p>
      ) : (
        <ul className="detail-rows">
          {documents.map((document) => (
            <li key={document.document_id}>
              <Link href={`/documents/${document.document_id}`} className="detail-row">
                <span className="detail-row-text">
                  <span className="detail-row-title">{document.title ?? subtypeText(t, document)}</span>
                  <span className="detail-row-sub">
                    {[f.date(document.document_date ?? document.created_at.slice(0, 10)), document.title ? subtypeText(t, document) : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                <span className={readingBadge(document)}>{readingLabel(t, document.reading)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
