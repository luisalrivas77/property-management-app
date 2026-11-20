"use client";

// Anlagen (phase 10 mockup 20): three sections as chips, ?tab=… links. Übersicht (phase 6 step
// 5b, ./Overview.tsx) values every position by its daily price; `?kurs=<asset id>` opens its
// price forms in the detail panel.
//
// Anlagen › ETF (phase 6 step 4, §13): every depot account with its funds, from GET /assets/etf.
// Since phase 10 D280 Anteile, Kurs and Wert are the Übersicht's (the net worth's valuation,
// D167): the newest stored daily price with its source and day; a fund whose report disagrees
// with the booked trades has no value (D165 (3)), and a fund no daily source prices shows „Kurs
// aus dem Bericht vom …“ with the badge „kein Tageskurs“ (D174 (4)). The report's own price and
// value appear only as „Zur Kontrolle“ in the position's panel (D267, `#d-fonds-<account>~<asset>`).
// Einstand is the plain sum of the purchases while a fund was only bought (lots are phase 7).
// The Sparer-Pauschbetrag and Vorabpauschale cards are phase 8 (D170) and carry no figure.
//
// Anlagen › Krypto (phase 7 step 6, ./Crypto.tsx): lots and the holding period; its „Kurs
// eintragen“ opens Übersicht's price forms with `?kurs=<asset>&tag=<day>`.
import "./assets.css";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { todayIso } from "@/components/format";
import { DOCUMENT_SUBTYPE } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { common } from "@/i18n/common";
import { assets } from "@/i18n/assets";

import { CryptoTab } from "./Crypto";
import { OverviewTab } from "./Overview";
import { gainClass, priceText, readingLabel, signed } from "./words";

type T = Translation<(typeof assets)["de"]>;
type EtfOverview = components["schemas"]["EtfToday"];
type EtfAccount = components["schemas"]["EtfAccountToday"];
type EtfPosition = components["schemas"]["EtfPositionToday"];
type DepotDocumentStatus = components["schemas"]["DepotDocumentStatus"];
type DocumentSubtype = components["schemas"]["DocumentSubtype"];

type TabKey = "uebersicht" | "etf" | "krypto";

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
  const f = useFormat();
  const search = useSearchParams();
  const priceFor = search.get("kurs");
  const priceDay = search.get("tag");
  const asked = search.get("tab");
  const tab: TabKey = priceFor ? "uebersicht" : asked === "etf" ? "etf" : asked === "krypto" ? "krypto" : "uebersicht";
  const tabs: { key: TabKey; label: string; href: string }[] = [
    { key: "uebersicht", label: t.tabOverview, href: "/assets" },
    { key: "etf", label: t.tabEtf, href: "/assets?tab=etf" },
    { key: "krypto", label: t.tabCrypto, href: "/assets?tab=krypto" },
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
      {tab === "etf" ? <EtfTab /> : tab === "krypto" ? <CryptoTab /> : <OverviewTab priceFor={priceFor} priceDay={priceDay} />}
    </section>
  );
}

function EtfTab() {
  const load = useCallback(() => ok(api.GET("/api/assets/etf")), []);
  const overview = useLoad(load);
  return <Loading state={overview}>{(data) => <Etf data={data} />}</Loading>;
}

const fundKey = (accountId: string, assetId: string) => `${accountId}~${assetId}`;

function Etf({ data }: { data: EtfOverview }) {
  const t = useT(assets);
  const detail = useDetail("fonds");
  const accounts = data.accounts ?? [];
  const selected = accounts
    .flatMap((account) => (account.positions ?? []).map((position) => ({ account, position })))
    .find(({ account, position }) => fundKey(account.account_id, position.asset_id) === detail.id);
  const panel = selected ? (
    <DetailPanel title={selected.position.name} backLabel={t.tabEtf} onClose={detail.close}>
      <FundPanel account={selected.account} position={selected.position} />
    </DetailPanel>
  ) : null;
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
          accounts.map((account) => (
            <Positions
              key={account.account_id}
              account={account}
              linkProps={(assetId) => detail.linkProps(fundKey(account.account_id, assetId))}
            />
          ))
        )}
        <Postbox documents={data.documents ?? []} />
        <div className="asset-pair">
          <section className="card stack" aria-labelledby="pauschbetrag-title">
            <h2 id="pauschbetrag-title">{t.allowance}</h2>
            <p className="muted">{t.phase8}</p>
          </section>
          <section className="card stack" aria-labelledby="vorabpauschale-title">
            <h2 id="vorabpauschale-title">{t.advanceLump}</h2>
            <p className="muted">{t.phase8}</p>
          </section>
        </div>
      </div>
    </DetailLayout>
  );
}

type LinkProps = ReturnType<ReturnType<typeof useDetail>["linkProps"]>;

function Positions({ account, linkProps }: { account: EtfAccount; linkProps: (assetId: string) => LinkProps }) {
  const t = useT(assets);
  const f = useFormat();
  return (
    <section className="card stack" aria-label={t.positionsOf(account.label)}>
      <div className="asset-section-head">
        <h2>
          {t.positions} · {f.money(account.value_eur)}
        </h2>
        <span className="spacer" />
        <Link href={`/accounts/${account.account_id}`}>{account.label} ›</Link>
      </div>
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
      <p className="muted hint">{t.todayHint}</p>
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
      <span className="num asset-value">{position.value_today_eur != null ? f.money(position.value_today_eur) : t.notValued}</span>
      <span className="num asset-col" title={position.cost_eur == null ? t.costAfterSale : undefined}>
        {position.cost_eur != null ? f.money(position.cost_eur) : "–"}
      </span>
      <span className={`num asset-col ${gainClass(gain)}`}>{gain != null ? signed(f, gain) : "–"}</span>
    </a>
  );
}

/** Why a fund shows no value, as a plain to-do (principle 7); "" when it has one. */
function valueNote(t: T, f: Formatter, position: EtfPosition): string {
  if (position.value_today_eur != null) return "";
  if (position.not_valued_reason === "valuation.not_valued") {
    return position.report_on ? t.noteReportMismatch(f.date(position.report_on)) : t.noteReportMismatchUndated;
  }
  if (position.price == null) return t.noteNoPrice;
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
            <strong>{position.value_today_eur != null ? f.money(position.value_today_eur) : t.notValued}</strong>
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
      <p className="muted hint">{t.dailyHint}</p>
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
