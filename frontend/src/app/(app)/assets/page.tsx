"use client";

// Anlagen › ETF (phase 6 step 4, §13, docs/mockups/09b-holdings-etf.png): every depot account
// with its funds, from GET /assets/etf. Anteile are the confirmed trades to today; Kurs and Wert
// are the latest depot report's, shown only while that report agrees with the booked trades and
// nothing was traded after it (D165 (3)); Einstand is the plain sum of the purchases while a
// fund was only bought (lots are phase 7). The Sparer-Pauschbetrag and Vorabpauschale cards are
// phase 8 (D170) and carry no figure. The Übersicht tab and the issuers' daily prices come with
// step 5, the Krypto tab with phase 7.
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { signedMoney } from "@/components/cashflow";
import { formatDate, formatDecimal, formatMoney } from "@/components/format";
import { DOCUMENT_SUBTYPE, PROPOSAL_STATUS } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

type EtfOverview = components["schemas"]["EtfOverview"];
type DepotAccountPositions = components["schemas"]["DepotAccountPositions"];
type DepotPosition = components["schemas"]["DepotPosition"];
type DepotDocumentStatus = components["schemas"]["DepotDocumentStatus"];
type DocumentSubtype = components["schemas"]["DocumentSubtype"];
type ProposalStatus = components["schemas"]["ProposalStatus"];

const PHASE_8 = "Kommt mit Anlage KAP (Phase 8)";

export default function AssetsPage() {
  const load = useCallback(() => ok(api.GET("/api/assets/etf")), []);
  const overview = useLoad(load);
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Anlagen · ETF</p>
          <h1>Anlagen</h1>
        </div>
      </div>
      <Loading state={overview}>{(data) => <Etf data={data} />}</Loading>
    </section>
  );
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
      <p className="muted hint">Kurs und Wert laut dem letzten Depotbericht.</p>
    </section>
  );
}

function PositionRow({ position }: { position: DepotPosition }) {
  const gain = position.gain_eur;
  return (
    <tr>
      <td>
        <strong>{position.name}</strong>
        <span className="inbox-detail">{[position.isin, valueNote(position)].filter(Boolean).join(" · ")}</span>
      </td>
      <td className="num">{formatDecimal(position.units)}</td>
      <td className="num">
        {position.report_price_eur != null ? formatMoney(position.report_price_eur) : "–"}
        {position.report_on ? <span className="inbox-detail">Bericht vom {formatDate(position.report_on)}</span> : null}
      </td>
      <td className="num">
        <strong>{position.value_eur != null ? formatMoney(position.value_eur) : "–"}</strong>
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

/** Why a fund shows no value, as a plain to-do (principle 7); "" when it has one. */
function valueNote(position: DepotPosition): string {
  if (position.value_eur != null) return "";
  if (position.report_on == null) return "Noch in keinem Depotbericht: der Wert kommt mit dem nächsten Bericht";
  if (position.reconciled === false) {
    return `Der Bericht vom ${formatDate(position.report_on)} passt nicht zu den gebuchten Abrechnungen: siehe Zu prüfen`;
  }
  if (position.moved_since_report) {
    return `Nach dem Bericht vom ${formatDate(position.report_on)} gehandelt: der Wert kommt mit dem nächsten Bericht`;
  }
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
