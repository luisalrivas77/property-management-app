"use client";

// Anlagen › Krypto (phase 7 step 6, docs/phases/phase-7.md D188 (5), D190, §14; mockup
// docs/mockups/09c-holdings-crypto.png): every wallet and exchange account with its coins, and
// per coin its open lots (FIFO per account and coin) from GET /holdings/crypto. Every euro
// figure is the backend's (principle 1); a lot names its day, units, cost and „steuerfrei ab“,
// or „Kurs fehlt“ with „Kurs eintragen“ for the coin and day whose price is missing (principle
// 7: the typed price of phase 6 D167, on Übersicht's price card, prefilled with the day).
// „Bestandsnachweis 31.12.<Jahr> erstellen“ writes D190's immutable statement per account.
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ownerChip } from "@/components/accounts";
import { signedMoney } from "@/components/cashflow";
import { formatDate, formatDateTime, formatDecimal, formatMoney } from "@/components/format";
import { ACCOUNT_KIND } from "@/components/labels";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

type CryptoHoldings = components["schemas"]["CryptoHoldings"];
type CryptoAccount = components["schemas"]["CryptoAccount"];
type CryptoCoin = components["schemas"]["CryptoCoin"];
type CryptoLot = components["schemas"]["CryptoLot"];
type HoldingsExport = components["schemas"]["HoldingsExport"];
type Owner = components["schemas"]["Owner"];
type AccountKind = components["schemas"]["AccountKind"];
type LotSource = components["schemas"]["LotSource"];

const ATTRIBUTION =
  "Kurse: CoinGecko, Kraken, EZB (Tagesschluss). Haltefrist nach §23 EStG, FIFO je Wallet und Coin: bei einem Verkauf gelten die ältesten Posten zuerst als verkauft. Eine Planungshilfe, keine Steuerberatung.";

const SOURCE: Record<LotSource, string> = {
  acquisition: "Kauf",
  reward: "Belohnung",
  transfer: "Übertrag",
  unknown: "Herkunft offen",
};

export function CryptoTab() {
  const load = useCallback(async () => {
    const [holdings, owners] = await Promise.all([ok(api.GET("/api/holdings/crypto")), ok(api.GET("/api/owners"))]);
    return { holdings, owners };
  }, []);
  const state = useLoad(load);
  return (
    <div className="stack planning">
      <Loading state={state}>{({ holdings, owners }) => <Crypto data={holdings} owners={owners} />}</Loading>
      <StatementCard />
    </div>
  );
}

function coinLabel(coin: { symbol?: string | null; name?: string }): string {
  if (coin.symbol && coin.name && coin.name !== coin.symbol) return `${coin.name} (${coin.symbol})`;
  return coin.symbol ?? coin.name ?? "Coin";
}

function Crypto({ data, owners }: { data: CryptoHoldings; owners: Owner[] }) {
  const coins = data.coins ?? [];
  const accounts = data.accounts ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  if (accounts.length === 0) {
    return (
      <section className="card stack">
        <h2>Coins</h2>
        <p className="muted">
          Noch keine Krypto-Konten. Legen Sie unter <Link href="/accounts">Konten</Link> eine Wallet oder eine Börse an; jeder
          Coin erscheint hier mit seinen Posten und ihrer Haltefrist.
        </p>
      </section>
    );
  }
  const accountOf = (id: string) => accounts.find((a) => a.account_id === id);
  const key = (coin: CryptoCoin) => `${coin.account_id}:${coin.asset_id}`;
  const current = coins.find((coin) => key(coin) === selected) ?? coins[0] ?? null;
  return (
    <>
      <Figures data={data} accounts={accounts} />
      {data.unfilled_movements ? (
        <Notice>
          {data.unfilled_movements === 1 ? "1 Abgang übersteigt" : `${data.unfilled_movements} Abgänge übersteigen`} den gebuchten
          Bestand davor; es fehlt eine Einzahlung oder ein Übertrag. Die Bewegungen des Kontos prüfen und die fehlende Bewegung
          einlesen; bis dahin bleibt der Einstand dieses Abgangs offen.{" "}
          {(data.unfilled_account_ids ?? []).map((id, i) => (
            <span key={id}>
              {i > 0 ? " · " : ""}
              <Link href={`/accounts/${id}`}>Bewegungen {accountOf(id)?.label ?? "des Kontos"} öffnen</Link>
            </span>
          ))}
        </Notice>
      ) : null}
      <div className="planning-kpis">
        {accounts.map((account) => (
          <AccountCard key={account.account_id} account={account} owners={owners} coins={coins} />
        ))}
      </div>
      <div className="planning-split">
        <section className="card stack" style={{ flex: "2 1 520px" }} aria-labelledby="coins-title">
          <div className="planning-card-head">
            <h2 id="coins-title">Coins</h2>
            <span className="spacer" />
            <span className="muted hint">Zeile wählen für Posten und Haltefrist</span>
          </div>
          {coins.length === 0 ? (
            <p className="muted">Am {formatDate(data.on)} hält keines der Konten einen Coin.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Coin</th>
                    <th scope="col">Konto · Eigentümer</th>
                    <th scope="col" className="num">
                      Wert
                    </th>
                    <th scope="col" className="num">
                      Unrealisiert
                    </th>
                    <th scope="col">Haltefrist</th>
                  </tr>
                </thead>
                <tbody>
                  {coins.map((coin) => {
                    const account = accountOf(coin.account_id);
                    const isCurrent = current != null && key(coin) === key(current);
                    return (
                      <tr
                        key={key(coin)}
                        aria-selected={isCurrent}
                        style={isCurrent ? { background: "var(--accent-tint)" } : undefined}
                      >
                        <td>
                          <button type="button" className="link" onClick={() => setSelected(key(coin))}>
                            <strong>{coinLabel(coin)}</strong>
                          </button>
                          <span className="inbox-detail">
                            {formatDecimal(coin.units)} {coin.symbol ?? ""}
                          </span>
                        </td>
                        <td>
                          {account?.label ?? "–"}
                          {account && (account.ownership ?? []).length > 0 ? ` · ${ownerChip(account.ownership ?? [], owners)}` : ""}
                        </td>
                        <td className="num">
                          <strong>{coin.value_eur != null ? formatMoney(coin.value_eur) : "kein Kurs"}</strong>
                        </td>
                        <td className={gainClass(coin.unrealized_eur)}>
                          {coin.unrealized_eur != null ? signedMoney(coin.unrealized_eur) : "–"}
                        </td>
                        <td>
                          <span className={holdingBadge(coin)}>{holdingText(coin)}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
        {current ? <LotsPanel coin={current} account={accountOf(current.account_id)} owners={owners} /> : null}
      </div>
      <p className="muted hint">{ATTRIBUTION}</p>
    </>
  );
}

function gainClass(value: string | null | undefined): string {
  if (value == null) return "num";
  return value.startsWith("-") ? "num planning-bad" : "num planning-good";
}

/** „steuerfrei“, „ab <Datum>“ or „k von n steuerfrei“ over the coin's open lots. */
function holdingText(coin: CryptoCoin): string {
  const lots = coin.lots ?? [];
  const free = lots.filter((lot) => lot.tax_free).length;
  if (lots.length > 0 && free === lots.length) return "steuerfrei";
  if (free === 0) return coin.next_tax_free_from ? `ab ${formatDate(coin.next_tax_free_from)}` : "–";
  return `${free} von ${lots.length} steuerfrei`;
}

function holdingBadge(coin: CryptoCoin): string {
  const lots = coin.lots ?? [];
  return lots.length > 0 && lots.every((lot) => lot.tax_free) ? "badge" : "badge badge-warn";
}

function Figures({ data, accounts }: { data: CryptoHoldings; accounts: CryptoAccount[] }) {
  const coins = data.coins ?? [];
  const next = data.next_tranche;
  const nextParts = (next?.parts ?? [])
    .map((part) => {
      const account = accounts.find((a) => a.account_id === part.account_id);
      return `${formatDecimal(part.units)} ${part.symbol ?? ""} · ${account?.label ?? ""}`;
    })
    .join("; ");
  return (
    <div className="planning-kpis">
      <div className="planning-kpi planning-kpi-main" role="group" aria-label="Krypto gesamt">
        <p className="planning-kpi-label">Krypto gesamt</p>
        <p className="planning-kpi-value">{formatMoney(data.total_eur)}</p>
        <p className="planning-kpi-sub">
          {coins.length === 1 ? "1 Coin" : `${coins.length} Coins`} in{" "}
          {accounts.length === 1 ? "1 Konto" : `${accounts.length} Konten`}
          {data.unvalued_coins ? ` · ${data.unvalued_coins} ohne Kurs, nicht mitgezählt` : ""}
        </p>
      </div>
      <div className="planning-kpi" role="group" aria-label="Unrealisierter Gewinn">
        <p className="planning-kpi-label">Unrealisierter Gewinn</p>
        <p className={`planning-kpi-value ${data.unrealized_eur.startsWith("-") ? "planning-bad" : "planning-good"}`}>
          {signedMoney(data.unrealized_eur)}
        </p>
        <p className="planning-kpi-sub">
          {data.unrealized_excluded
            ? `ohne ${data.unrealized_excluded === 1 ? "1 Coin" : `${data.unrealized_excluded} Coins`}, deren Kurs oder Einstand fehlt`
            : "Wert minus Einstand"}
        </p>
      </div>
      <div className="planning-kpi" role="group" aria-label="Heute steuerfrei verkaufbar">
        <p className="planning-kpi-label">Heute steuerfrei verkaufbar</p>
        <p className="planning-kpi-value">{formatMoney(data.tax_free_today_eur)}</p>
        <p className="planning-kpi-sub">
          Haltefrist über ein Jahr (§ 23 EStG)
          {data.tax_free_today_excluded ? ` · ${data.tax_free_today_excluded} Posten ohne Kurs nicht mitgezählt` : ""}
        </p>
      </div>
      <div className="planning-kpi" role="group" aria-label="Nächste Tranche steuerfrei">
        <p className="planning-kpi-label">Nächste Tranche steuerfrei</p>
        <p className="planning-kpi-value">{next ? formatDate(next.on) : "–"}</p>
        <p className="planning-kpi-sub">
          {next ? `${nextParts}${next.value_eur != null ? ` · ${formatMoney(next.value_eur)}` : ""}` : "Alle Posten sind steuerfrei"}
        </p>
      </div>
    </div>
  );
}

function AccountCard({ account, owners, coins }: { account: CryptoAccount; owners: Owner[]; coins: CryptoCoin[] }) {
  const symbols = coins
    .filter((coin) => coin.account_id === account.account_id)
    .map((coin) => coin.symbol ?? coin.name)
    .join(", ");
  const kind = ACCOUNT_KIND[account.kind as AccountKind] ?? account.kind;
  return (
    <div className="planning-kpi" role="group" aria-label={account.label}>
      <p className="planning-kpi-label">
        <Link href={`/accounts/${account.account_id}`}>
          <strong>{account.label}</strong>
        </Link>
      </p>
      <p className="planning-kpi-sub">{[account.provider ?? kind, symbols].filter(Boolean).join(" · ")}</p>
      <p className="planning-kpi-value">{formatMoney(account.value_eur)}</p>
      <p className="planning-kpi-sub">
        {(account.ownership ?? []).length > 0 ? (
          <span className="badge">{ownerChip(account.ownership ?? [], owners)}</span>
        ) : (
          <Link href={`/accounts/${account.account_id}`}>Eigentümer festlegen</Link>
        )}
        {account.unvalued_coins ? ` · ${account.unvalued_coins} ohne Kurs` : ""}
      </p>
    </div>
  );
}

function LotsPanel({ coin, account, owners }: { coin: CryptoCoin; account: CryptoAccount | undefined; owners: Owner[] }) {
  const lots = coin.lots ?? [];
  const owner = account && (account.ownership ?? []).length > 0 ? ownerChip(account.ownership ?? [], owners) : null;
  return (
    <section className="card stack" style={{ flex: "1 1 300px" }} aria-labelledby="lots-title">
      <p className="eyebrow">{[account?.label, owner].filter(Boolean).join(" · ")}</p>
      <h2 id="lots-title">{coinLabel(coin)}</h2>
      {!coin.reconciled ? (
        <Notice>
          Der letzte Abgleich einer Adresse dieses Kontos ergab einen anderen Bestand als die Kette. Die Wallet öffnen und ihre
          Adressen prüfen; bis dahin können Posten fehlen. <Link href={`/accounts/${coin.account_id}`}>Wallet öffnen</Link>
        </Notice>
      ) : null}
      <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {lots.map((lot) => (
          <LotRow key={lot.id} lot={lot} coin={coin} />
        ))}
      </ul>
      <p className={coin.tax_free_value_eur != null || coin.tax_free_units === "0" ? "planning-good" : undefined}>
        <strong>
          Heute steuerfrei verkaufbar: {formatDecimal(coin.tax_free_units)} {coin.symbol ?? ""}
          {coin.tax_free_value_eur != null ? ` (${formatMoney(coin.tax_free_value_eur)})` : ""}.
        </strong>
      </p>
      <p className="muted hint">
        Bei einem Verkauf gelten die ältesten Posten zuerst als verkauft (FIFO je Wallet und Coin). Ein Tausch ist ein Verkauf und
        ein neuer Kauf, deshalb beginnt die Haltefrist dann neu.
      </p>
    </section>
  );
}

function LotRow({ lot, coin }: { lot: CryptoLot; coin: CryptoCoin }) {
  const moved = (lot.moved_through ?? []).length > 0;
  const origin = `${SOURCE[lot.source] ?? lot.source}${moved ? " · übertragen, Erwerbstag behalten" : ""}`;
  return (
    <li className="stack" style={{ gap: 2, borderBottom: "1px solid var(--border)", paddingBottom: 6 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
        <strong>
          {formatDate(lot.acquired_on)} · {formatDecimal(lot.units_open)} {coin.symbol ?? ""}
        </strong>
        <strong>{lot.value_eur != null ? formatMoney(lot.value_eur) : "–"}</strong>
      </div>
      <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
        <span className="muted">
          {origin} · Einstand {lot.cost_eur != null ? formatMoney(lot.cost_eur) : costMissing(lot)}
        </span>
        {/* A lot of open origin is dated the day it arrived, not a known purchase day: no
            „steuerfrei ab“ until a person assigns where it came from. */}
        {lot.source === "unknown" ? (
          <span className="planning-bad">Herkunft offen</span>
        ) : (
          <span className={lot.tax_free ? "planning-good" : "planning-bad"}>
            {lot.tax_free ? "steuerfrei" : `steuerfrei ab ${formatDate(lot.tax_free_from)}`}
          </span>
        )}
      </div>
      {lot.source === "unknown" ? (
        <p className="muted hint" style={{ margin: 0 }}>
          Woher diese Coins kamen, ist nicht geklärt; erst dann stehen Erwerbstag, Einstand und Haltefrist fest.{" "}
          <Link href="/inbox">In Zu prüfen den Zugang zuordnen</Link>
        </p>
      ) : null}
      {lot.cost_eur == null && lot.source !== "unknown" && (lot.price_needs ?? []).length > 0 ? (
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {(lot.price_needs ?? []).map((need) => (
            <Link
              key={`${need.asset_id}:${need.on}`}
              className="button-link"
              href={`/assets?kurs=${encodeURIComponent(need.asset_id)}&tag=${encodeURIComponent(need.on)}`}
            >
              Kurs eintragen{need.asset_id !== coin.asset_id && need.symbol ? ` (${need.symbol})` : ""} für den {formatDate(need.on)}
            </Link>
          ))}
        </div>
      ) : null}
    </li>
  );
}

/** Why a lot has no cost, as a plain to-do (principle 7). */
function costMissing(lot: CryptoLot): string {
  if (lot.source === "unknown") return "offen: Herkunft offen";
  if ((lot.price_needs ?? []).length > 0) return "offen: Kurs fehlt";
  return "offen";
}

// --- „Bestandsnachweis 31.12.<Jahr> erstellen“ (D190) ---------------------------------------

function StatementCard() {
  const lastYear = new Date().getFullYear() - 1;
  const [year, setYear] = useState(lastYear);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => ok(api.GET("/api/exports/holdings")), []);
  const exports = useLoad(load);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/exports/holdings", { body: { year } }));
      exports.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="statement-title">
      <h2 id="statement-title">Bestandsnachweis zum Jahresende</h2>
      <p className="muted">
        Hält für jede Wallet und jede Börse den Bestand am 31.12. mit den offenen Posten, ihrem Einstand und ihrer Haltefrist als
        PDF und CSV fest; Adressen gekürzt. Ein späterer Nachweis wird neu angelegt, der frühere bleibt unverändert.
      </p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <div className="field">
          <label htmlFor="statement-year">Jahr</label>
          <select id="statement-year" value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {[0, 1, 2].map((back) => (
              <option key={back} value={lastYear - back}>
                {lastYear - back}
              </option>
            ))}
          </select>
        </div>
        <button type="button" onClick={() => void create()} disabled={busy}>
          {busy ? "Erstellt …" : `Bestandsnachweis 31.12.${year} erstellen`}
        </button>
      </div>
      <ErrorNotice error={error} />
      <Loading state={exports}>
        {(rows) => <ExportList rows={rows} onRefresh={exports.reload} />}
      </Loading>
    </section>
  );
}

function ExportList({ rows, onRefresh }: { rows: HoldingsExport[]; onRefresh: () => void }) {
  if (rows.length === 0) return <p className="muted">Noch kein Bestandsnachweis erstellt.</p>;
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Stichtag</th>
              <th scope="col">Konto</th>
              <th scope="col">Erstellt</th>
              <th scope="col">Dateien</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{formatDate(row.as_of)}</td>
                <td>{row.account_label}</td>
                <td>{formatDateTime(row.created_at)}</td>
                <td>
                  {row.pdf_url ? <a href={row.pdf_url}>PDF</a> : null}
                  {" · "}
                  {row.csv_url ? <a href={row.csv_url}>CSV</a> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted hint">
        Die Links gelten einige Minuten.{" "}
        <button type="button" className="link" onClick={onRefresh}>
          Links erneuern
        </button>
      </p>
    </>
  );
}
