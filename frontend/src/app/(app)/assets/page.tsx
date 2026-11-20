"use client";

// Anlagen: tabs as ?tab=… links. Übersicht (phase 6 step 5b, ./Overview.tsx, mockup 09) values
// every position by its daily price; `?kurs=<asset id>` opens its price card there.
//
// Anlagen › ETF (phase 6 step 4, §13, docs/mockups/09b-holdings-etf.png): every depot account
// with its funds, from GET /assets/etf. Since phase 10 D280 Anteile, Kurs and Wert are the
// Übersicht's (the net worth's valuation, D167): the newest stored daily price with its source
// and day; a fund whose report disagrees with the booked trades has no value (D165 (3)), and a
// fund no daily source prices shows „Kurs aus dem Bericht vom …“ (D174 (4)). The report's own
// price and value are a check only. Einstand is the plain sum of the purchases while a fund was
// only bought (lots are phase 7). The Sparer-Pauschbetrag and Vorabpauschale cards are
// phase 8 (D170) and carry no figure.
//
// Anlagen › Krypto (phase 7 step 6, ./Crypto.tsx, mockup 09c): lots and the holding period;
// its „Kurs eintragen“ opens Übersicht's price card with `?kurs=<asset>&tag=<day>`.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { signedMoney } from "@/components/cashflow";
import { formatDate, formatDecimal, formatMoney } from "@/components/format";
import { DOCUMENT_SUBTYPE, PRICE_SOURCE, PROPOSAL_STATUS } from "@/components/labels";
import { Loading, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { CryptoTab } from "./Crypto";
import { OverviewTab } from "./Overview";

type EtfOverview = components["schemas"]["EtfToday"];
type DepotAccountPositions = components["schemas"]["EtfAccountToday"];
type DepotPosition = components["schemas"]["EtfPositionToday"];
type PriceLabel = components["schemas"]["PriceLabel"];
type DepotDocumentStatus = components["schemas"]["DepotDocumentStatus"];
type DocumentSubtype = components["schemas"]["DocumentSubtype"];
type ProposalStatus = components["schemas"]["ProposalStatus"];

const PHASE_8 = "Kommt mit Anlage KAP (Phase 8)";

type TabKey = "uebersicht" | "etf" | "krypto";

const TABS: Tab<TabKey>[] = [
  { key: "uebersicht", label: "Übersicht" },
  { key: "etf", label: "ETF" },
  { key: "krypto", label: "Krypto" },
];

export default function AssetsPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <Assets />
    </Suspense>
  );
}

function Assets() {
  const search = useSearchParams();
  const priceFor = search.get("kurs");
  const priceDay = search.get("tag");
  const asked = search.get("tab");
  const tab: TabKey = priceFor ? "uebersicht" : asked === "etf" ? "etf" : asked === "krypto" ? "krypto" : "uebersicht";
  const heading = tab === "etf" ? "ETF" : tab === "krypto" ? "Krypto" : "Übersicht";
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Anlagen · {heading}</p>
          <h1>Anlagen</h1>
        </div>
      </div>
      <TabBar tabs={TABS} current={tab} href={(key) => (key === "uebersicht" ? "/assets" : `/assets?tab=${key}`)} />
      {tab === "etf" ? <EtfTab /> : tab === "krypto" ? <CryptoTab /> : <OverviewTab priceFor={priceFor} priceDay={priceDay} />}
    </section>
  );
}

function EtfTab() {
  const load = useCallback(() => ok(api.GET("/api/assets/etf")), []);
  const overview = useLoad(load);
  return <Loading state={overview}>{(data) => <Etf data={data} />}</Loading>;
}

function Etf({ data }: { data: EtfOverview }) {
  return (
    <div className="stack planning">
      <div className="planning-split">
        <div className="stack" style={{ flex: "2 1 520px" }}>
          {(data.accounts ?? []).length === 0 ? (
            <section className="card stack">
              <h2>Positionen</h2>
              <p className="muted">
                Noch keine Wertpapiere gebucht. Legen Sie unter <Link href="/accounts">Konten</Link> ein Konto der Art „Depot“
                an und laden Sie die Wertpapierabrechnungen und Depotberichte unter <Link href="/documents">Dokumente</Link>{" "}
                hoch; jede Abrechnung wird nach „Übernehmen“ hier gezählt.
              </p>
            </section>
          ) : (
            (data.accounts ?? []).map((account) => <Positions key={account.account_id} account={account} />)
          )}
          <Postbox documents={data.documents ?? []} />
        </div>
        <div className="stack" style={{ flex: "1 1 280px" }}>
          <section className="card stack" aria-labelledby="pauschbetrag-title">
            <h2 id="pauschbetrag-title">Sparer-Pauschbetrag</h2>
            <p className="muted">{PHASE_8}</p>
          </section>
          <section className="card stack" aria-labelledby="vorabpauschale-title">
            <h2 id="vorabpauschale-title">Vorabpauschale</h2>
            <p className="muted">{PHASE_8}</p>
          </section>
        </div>
      </div>
    </div>
  );
}

function Positions({ account }: { account: DepotAccountPositions }) {
  return (
    <section className="card stack" aria-label={`Positionen ${account.label}`}>
      <div className="planning-card-head">
        <h2>Positionen</h2>
        <span className="spacer" />
        <Link href={`/accounts/${account.account_id}`}>{account.label}</Link>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">ETF</th>
              <th scope="col" className="num">
                Anteile
              </th>
              <th scope="col" className="num">
                Kurs
              </th>
              <th scope="col" className="num">
                Wert
              </th>
              <th scope="col" className="num">
                Einstand
              </th>
              <th scope="col" className="num">
                Gewinn
              </th>
            </tr>
          </thead>
          <tbody>
            {(account.positions ?? []).map((position) => (
              <PositionRow key={position.asset_id} position={position} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted hint">Werte von heute, wie im Nettovermögen auf der Übersicht.</p>
    </section>
  );
}

function PositionRow({ position }: { position: DepotPosition }) {
  const gain = position.gain_today_eur;
  return (
    <tr>
      <td>
        <strong>{position.name}</strong>
        <span className="inbox-detail">{[position.isin, valueNote(position)].filter(Boolean).join(" · ")}</span>
      </td>
      <td className="num">{formatDecimal(position.units)}</td>
      <td className="num">
        {position.price ? (
          <>
            {formatMoney(position.price.price_eur)}
            <span className="inbox-detail">{priceSource(position.price, position.from_report ?? false)}</span>
          </>
        ) : (
          "–"
        )}
      </td>
      <td className="num">
        <strong>{position.value_today_eur != null ? formatMoney(position.value_today_eur) : "–"}</strong>
      </td>
      <td className="num" title={position.cost_eur == null ? "Nach einem Verkauf oder einer Umbuchung: kommt mit Phase 7" : undefined}>
        {position.cost_eur != null ? formatMoney(position.cost_eur) : "–"}
      </td>
      <td className={gain == null ? "num" : gain.startsWith("-") ? "num planning-bad" : "num planning-good"}>
        {gain != null ? signedMoney(gain) : "–"}
      </td>
    </tr>
  );
}

/** "NAV der Fondsgesellschaft vom 26.06.2026", or „Kurs aus dem Bericht vom …“ (D174 (4)). */
function priceSource(price: PriceLabel, fromReport: boolean): string {
  const day = `vom ${formatDate(price.on)}`;
  if (fromReport) return `Kurs aus dem Bericht ${day}`;
  return `${PRICE_SOURCE[price.source] ?? price.source} ${day}`;
}

/** Why a fund shows no value, as a plain to-do (principle 7); "" when it has one. */
function valueNote(position: DepotPosition): string {
  if (position.value_today_eur != null) return "";
  if (position.not_valued_reason === "valuation.not_valued") {
    return position.report_on
      ? `Der Bericht vom ${formatDate(position.report_on)} passt nicht zu den gebuchten Abrechnungen: siehe Zu prüfen`
      : "Der Depotbericht passt nicht zu den gebuchten Abrechnungen: siehe Zu prüfen";
  }
  if (position.price == null) return "Noch kein Kurs: unter Übersicht einen Kurs eintragen";
  return "";
}

function readingText(document: DepotDocumentStatus): string {
  if (document.reading == null) return "abgelegt";
  return PROPOSAL_STATUS[document.reading as ProposalStatus] ?? document.reading;
}

function readingBadge(document: DepotDocumentStatus): string {
  if (document.reading === "applied") return "badge";
  if (document.reading === "ready") return "badge badge-info";
  if (document.reading === "failed" || document.reading === "blocked_tenant_data") return "badge badge-warn";
  return "badge badge-muted";
}

function Postbox({ documents }: { documents: DepotDocumentStatus[] }) {
  return (
    <section className="card stack" aria-labelledby="postbox-title">
      <h2 id="postbox-title">Dokumente des Depots</h2>
      {documents.length === 0 ? (
        <p className="muted">
          Noch keine Dokumente aus der Postbox. Laden Sie sie unter <Link href="/documents">Dokumente</Link> hoch.
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <tbody>
              {documents.map((document) => (
                <tr key={document.document_id}>
                  <td>{formatDate(document.document_date ?? document.created_at.slice(0, 10))}</td>
                  <td>
                    <Link href={`/documents/${document.document_id}`}>
                      {document.title ?? (document.subtype ? (DOCUMENT_SUBTYPE[document.subtype as DocumentSubtype] ?? document.subtype) : "Dokument")}
                    </Link>
                    {document.title && document.subtype ? (
                      <span className="inbox-detail">{DOCUMENT_SUBTYPE[document.subtype as DocumentSubtype] ?? document.subtype}</span>
                    ) : null}
                  </td>
                  <td className="num">
                    <span className={readingBadge(document)}>{readingText(document)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
