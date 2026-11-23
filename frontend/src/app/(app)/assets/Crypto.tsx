"use client";

// Anlagen › Krypto (phase 7 step 6, docs/phases/phase-7.md D188 (5), D190, §14; phase 10 mockup
// 20): every wallet and exchange account with its coins, and per coin its open lots (FIFO per
// account and coin) from GET /holdings/crypto. Every euro figure is the backend's (principle 1).
// A tapped coin opens the detail panel (D267, `#d-coin-<account>~<asset>`) with its lots: each
// names its day, units, cost and „steuerfrei ab“, or „Kurs fehlt“ with „Kurs eintragen“ for the
// coin and day whose price is missing (principle 7: the typed price of phase 6 D167, in the
// Übersicht's panel, prefilled with the day). „Bestandsnachweis 31.12.<Jahr> erstellen“ writes
// D190's immutable statement per account. A wallet whose read movements wait for a person is
// counted nowhere above: its line names the coins not yet counted and links „Transaktionen
// prüfen“ on the wallet's page (16 Nov 2026).
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { CryptoRefresh } from "@/components/cryptoRefresh";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { assets } from "@/i18n/assets";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { WaitingNotices } from "./Overview";
import { accountKindLabel, gainClass, ownerLabel, signed } from "./words";

type T = Translation<(typeof assets)["de"]>;
type CryptoHoldings = components["schemas"]["CryptoHoldings"];
type CryptoAccount = components["schemas"]["CryptoAccount"];
type CryptoCoin = components["schemas"]["CryptoCoin"];
type CryptoLot = components["schemas"]["CryptoLot"];
type HoldingsExport = components["schemas"]["HoldingsExport"];
type Owner = components["schemas"]["Owner"];
type WalletWaiting = components["schemas"]["WalletWaiting"];

export function CryptoTab() {
  const load = useCallback(async () => {
    const [holdings, owners] = await Promise.all([ok(api.GET("/api/holdings/crypto")), ok(api.GET("/api/owners"))]);
    return { holdings, owners };
  }, []);
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {({ holdings, owners }) => <Crypto data={holdings} owners={owners} onRefreshed={state.reload} />}
    </Loading>
  );
}

function coinLabel(coin: { symbol?: string | null; name?: string }): string {
  if (coin.symbol && coin.name && coin.name !== coin.symbol) return `${coin.name} (${coin.symbol})`;
  return coin.symbol ?? coin.name ?? "Coin";
}

const coinKey = (coin: CryptoCoin) => `${coin.account_id}~${coin.asset_id}`;

/** The day before an ISO day, as an ISO day. */
function dayBefore(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

/** Where a coin's price stands (D291): „Stand 14:32“ for a price read on demand today; once one
 * was read today, a coin still at a daily price says so: „Kurs von gestern“ for the last close
 * (yesterday's, or a feed's row of today, which is its midnight snapshot), else its day. */
function priceMoment(t: T, f: Formatter, coin: CryptoCoin, data: CryptoHoldings): string | null {
  if (coin.price_source === "spot" && coin.price_read_at) return t.spotAt(f.time(coin.price_read_at));
  if (!data.spot_read_at || !coin.price_on) return null;
  if (coin.price_source !== "coingecko_daily" && coin.price_source !== "kraken_daily") return null;
  return coin.price_on >= dayBefore(data.on) ? t.yesterdaysPrice : t.asOf(f.date(coin.price_on));
}

function Crypto({ data, owners, onRefreshed }: { data: CryptoHoldings; owners: Owner[]; onRefreshed: () => void }) {
  const t = useT(assets);
  const f = useFormat();
  const detail = useDetail("coin");
  const coins = data.coins ?? [];
  const accounts = data.accounts ?? [];
  const accountOf = (id: string) => accounts.find((a) => a.account_id === id);
  const owner = (account: CryptoAccount | undefined) =>
    account && (account.ownership ?? []).length > 0 ? ownerLabel(t, f, account.ownership ?? [], owners) : null;
  const selected = coins.find((coin) => coinKey(coin) === detail.id) ?? null;
  const panel = selected ? (
    <DetailPanel title={coinLabel(selected)} backLabel={t.tabCrypto} onClose={detail.close}>
      <LotsPanel coin={selected} account={accountOf(selected.account_id)} owner={owner(accountOf(selected.account_id))} />
    </DetailPanel>
  ) : null;
  if (accounts.length === 0) {
    return (
      <div className="stack">
        <section className="card stack">
          <h2>{t.coinsTitle}</h2>
          <p className="muted">{t.noCryptoAccounts}</p>
          <p>
            <Link href="/accounts">{t.openAccounts}</Link>
          </p>
        </section>
        <StatementCard />
      </div>
    );
  }
  return (
    <DetailLayout panel={panel}>
      <div className="stack">
        <CryptoRefresh onRefreshed={onRefreshed} />
        <Figures data={data} accounts={accounts} />
        {data.unfilled_movements ? (
          <div className="notice notice-warn" role="status">
            <p>
              {t.unfilled(data.unfilled_movements)}{" "}
              {(data.unfilled_account_ids ?? []).map((id, i) => (
                <span key={id}>
                  {i > 0 ? " · " : ""}
                  <Link href={`/accounts/${id}`}>{t.openMovements(accountOf(id)?.label ?? t.ofAccount)}</Link>
                </span>
              ))}
            </p>
          </div>
        ) : null}
        <WaitingNotices lines={data.waiting ?? []} />
        <div className="asset-pair">
          {accounts.map((account) => (
            <AccountCard
              key={account.account_id}
              account={account}
              owner={owner(account)}
              coins={coins}
              waiting={(data.waiting ?? []).find((line) => line.account_id === account.account_id) ?? null}
            />
          ))}
        </div>
        <section className="card stack" aria-labelledby="coins-title">
          <div className="asset-section-head">
            <h2 id="coins-title">{t.coinsTitle}</h2>
            <span className="spacer" />
            <span className="muted hint">{t.pickRow}</span>
          </div>
          {coins.length === 0 ? (
            <p className="muted">{t.noCoins(f.date(data.on))}</p>
          ) : (
            <ul className="asset-rows" data-cols="crypto">
              <li className="asset-rows-head asset-col" aria-hidden="true">
                <span>{t.colCoin}</span>
                <span>{t.colAccountOwner}</span>
                <span className="num">{t.colValue}</span>
                <span className="num">{t.colUnrealised}</span>
                <span>{t.colHolding}</span>
              </li>
              {coins.map((coin) => {
                const account = accountOf(coin.account_id);
                const who = [account?.label ?? "–", owner(account)].filter(Boolean).join(" · ");
                const moment = priceMoment(t, f, coin, data);
                return (
                  <li key={coinKey(coin)}>
                    <a {...detail.linkProps(coinKey(coin))} className="asset-row">
                      <span className="asset-row-name">
                        <strong>{coinLabel(coin)}</strong>
                        <span className="asset-row-sub">
                          {f.decimal(coin.units)} {coin.symbol ?? ""}
                          {moment ? ` · ${moment}` : ""}
                        </span>
                        <span className="asset-row-sub asset-phone">
                          {who} · {holdingText(t, f, coin)}
                        </span>
                      </span>
                      <span className="asset-col">{who}</span>
                      <span className="num asset-value">{coin.value_eur != null ? f.money(coin.value_eur) : t.noValue}</span>
                      <span className={`num asset-col ${gainClass(coin.unrealized_eur)}`}>
                        {coin.unrealized_eur != null ? signed(f, coin.unrealized_eur) : "–"}
                      </span>
                      <span className="asset-col">
                        <span className={holdingBadge(coin)}>{holdingText(t, f, coin)}</span>
                      </span>
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        <StatementCard />
        <p className="muted hint">{t.cryptoAttribution}</p>
      </div>
    </DetailLayout>
  );
}

/** „steuerfrei“, „ab <Datum>“ or „k von n steuerfrei“ over the coin's open lots. */
function holdingText(t: T, f: Formatter, coin: CryptoCoin): string {
  const lots = coin.lots ?? [];
  const free = lots.filter((lot) => lot.tax_free).length;
  if (lots.length > 0 && free === lots.length) return t.taxFree;
  if (free === 0) return coin.next_tax_free_from ? t.taxFreeFrom(f.date(coin.next_tax_free_from)) : "–";
  return t.taxFreeOf(free, lots.length);
}

function holdingBadge(coin: CryptoCoin): string {
  const lots = coin.lots ?? [];
  return lots.length > 0 && lots.every((lot) => lot.tax_free) ? "badge" : "badge badge-warn";
}

function Figures({ data, accounts }: { data: CryptoHoldings; accounts: CryptoAccount[] }) {
  const t = useT(assets);
  const f = useFormat();
  const coins = data.coins ?? [];
  const next = data.next_tranche;
  const nextParts = (next?.parts ?? [])
    .map((part) => {
      const account = accounts.find((a) => a.account_id === part.account_id);
      return `${f.decimal(part.units)} ${part.symbol ?? ""} · ${account?.label ?? ""}`;
    })
    .join("; ");
  return (
    <div className="planning-kpis">
      <div className="planning-kpi planning-kpi-main" role="group" aria-label={t.cryptoTotal}>
        <p className="planning-kpi-label">{t.cryptoTotal}</p>
        <p className="planning-kpi-value">{f.money(data.total_eur)}</p>
        <p className="planning-kpi-sub">
          {t.coins(coins.length)} {t.inAccounts(accounts.length)}
          {data.unvalued_coins ? ` · ${t.unvaluedCoins(data.unvalued_coins)}` : ""}
        </p>
      </div>
      <div className="planning-kpi" role="group" aria-label={t.unrealisedGain}>
        <p className="planning-kpi-label">{t.unrealisedGain}</p>
        <p className={`planning-kpi-value ${data.unrealized_eur.startsWith("-") ? "planning-bad" : "planning-good"}`}>
          {signed(f, data.unrealized_eur)}
        </p>
        <p className="planning-kpi-sub">{data.unrealized_excluded ? t.unrealisedExcluded(data.unrealized_excluded) : t.valueMinusCost}</p>
      </div>
      <div className="planning-kpi" role="group" aria-label={t.taxFreeToday}>
        <p className="planning-kpi-label">{t.taxFreeToday}</p>
        <p className="planning-kpi-value">{f.money(data.tax_free_today_eur)}</p>
        <p className="planning-kpi-sub">
          {t.holdingYear}
          {data.tax_free_today_excluded ? ` · ${t.lotsWithoutPrice(data.tax_free_today_excluded)}` : ""}
        </p>
      </div>
      <div className="planning-kpi" role="group" aria-label={t.nextTranche}>
        <p className="planning-kpi-label">{t.nextTranche}</p>
        <p className="planning-kpi-value">{next ? f.date(next.on) : "–"}</p>
        <p className="planning-kpi-sub">
          {next ? `${nextParts}${next.value_eur != null ? ` · ${f.money(next.value_eur)}` : ""}` : t.allTaxFree}
        </p>
      </div>
    </div>
  );
}

function AccountCard({
  account,
  owner,
  coins,
  waiting,
}: {
  account: CryptoAccount;
  owner: string | null;
  coins: CryptoCoin[];
  waiting: WalletWaiting | null;
}) {
  const t = useT(assets);
  const f = useFormat();
  const symbols = coins
    .filter((coin) => coin.account_id === account.account_id)
    .map((coin) => coin.symbol ?? coin.name)
    .join(", ");
  return (
    <section className="card stack" aria-label={account.label}>
      <div className="asset-section-head">
        <h3 style={{ margin: 0 }}>
          <Link href={`/accounts/${account.account_id}`}>{account.label}</Link>
        </h3>
        <span className="spacer" />
        <strong>{f.money(account.value_eur)}</strong>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        {[account.provider ?? accountKindLabel(t, account.kind), symbols].filter(Boolean).join(" · ")}{" "}
        {owner ? <span className="badge">{owner}</span> : <Link href={`/accounts/${account.account_id}`}>{t.setOwners}</Link>}
        {account.unvalued_coins ? ` · ${t.withoutPrice(account.unvalued_coins)}` : ""}
      </p>
      {waiting ? (
        <p className="muted" style={{ margin: 0 }}>
          {t.waitingTx(waiting.groups)}: <Link href={waiting.href}>{waiting.action}</Link>
        </p>
      ) : null}
    </section>
  );
}

function LotsPanel({ coin, account, owner }: { coin: CryptoCoin; account: CryptoAccount | undefined; owner: string | null }) {
  const t = useT(assets);
  const f = useFormat();
  const lots = coin.lots ?? [];
  return (
    <div className="stack">
      <p className="muted">{[account?.label, owner].filter(Boolean).join(" · ")}</p>
      <dl className="facts">
        <div>
          <dt>{t.holding}</dt>
          <dd>
            {f.decimal(coin.units)} {coin.symbol ?? ""}
          </dd>
        </div>
        <div>
          <dt>{t.value}</dt>
          <dd>{coin.value_eur != null ? f.money(coin.value_eur) : t.noValue}</dd>
        </div>
        <div>
          <dt>{t.unrealised}</dt>
          <dd className={gainClass(coin.unrealized_eur)}>{coin.unrealized_eur != null ? signed(f, coin.unrealized_eur) : "–"}</dd>
        </div>
      </dl>
      {!coin.reconciled ? (
        <div className="notice notice-warn" role="status">
          <p>
            {t.notReconciled} <Link href={`/accounts/${coin.account_id}`}>{t.openWallet}</Link>
          </p>
        </div>
      ) : null}
      <h3>{t.openLots}</h3>
      <ul className="asset-lots">
        {lots.map((lot) => (
          <LotRow key={lot.id} lot={lot} coin={coin} />
        ))}
      </ul>
      <p className={coin.tax_free_value_eur != null || coin.tax_free_units === "0" ? "planning-good" : undefined}>
        <strong>
          {t.taxFreeSellable(
            `${f.decimal(coin.tax_free_units)} ${coin.symbol ?? ""}`.trim(),
            coin.tax_free_value_eur != null ? f.money(coin.tax_free_value_eur) : null,
          )}
        </strong>
      </p>
      <p className="muted hint">{t.fifoHint}</p>
    </div>
  );
}

function lotSource(t: T, source: string): string {
  const words: Record<string, string> = {
    acquisition: t.lotAcquisition,
    reward: t.lotReward,
    transfer: t.lotTransfer,
    unknown: t.lotUnknown,
  };
  return words[source] ?? source;
}

function LotRow({ lot, coin }: { lot: CryptoLot; coin: CryptoCoin }) {
  const t = useT(assets);
  const f = useFormat();
  const moved = (lot.moved_through ?? []).length > 0;
  const origin = `${lotSource(t, lot.source)}${moved ? ` · ${t.lotMoved}` : ""}`;
  return (
    <li>
      <div className="asset-lot-line">
        <strong>
          {f.date(lot.acquired_on)} · {f.decimal(lot.units_open)} {coin.symbol ?? ""}
        </strong>
        <strong>{lot.value_eur != null ? f.money(lot.value_eur) : "–"}</strong>
      </div>
      <div className="asset-lot-line">
        <span className="muted">
          {origin} · {t.cost(lot.cost_eur != null ? f.money(lot.cost_eur) : costMissing(t, lot))}
        </span>
        {/* A lot of open origin is dated the day it arrived, not a known purchase day: no
            „steuerfrei ab“ until a person assigns where it came from. */}
        {lot.source === "unknown" ? (
          <span className="badge badge-warn">{t.lotUnknown}</span>
        ) : (
          <span className={lot.tax_free ? "badge" : "badge badge-muted"}>
            {lot.tax_free ? t.taxFree : t.taxFreeFromLong(f.date(lot.tax_free_from))}
          </span>
        )}
      </div>
      {lot.source === "unknown" ? (
        <p className="muted hint" style={{ margin: 0 }}>
          {t.unknownOrigin} <Link href="/inbox">{t.assignInInbox}</Link>
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
              {t.typePriceFor(need.asset_id !== coin.asset_id && need.symbol ? need.symbol : "", f.date(need.on))}
            </Link>
          ))}
        </div>
      ) : null}
    </li>
  );
}

/** Why a lot has no cost, as a plain to-do (principle 7). */
function costMissing(t: T, lot: CryptoLot): string {
  if (lot.source === "unknown") return t.costOpenOrigin;
  if ((lot.price_needs ?? []).length > 0) return t.costOpenPrice;
  return t.costOpen;
}

// --- „Bestandsnachweis 31.12.<Jahr> erstellen“ (D190) ---------------------------------------

function StatementCard() {
  const t = useT(assets);
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
      <h2 id="statement-title">{t.statementTitle}</h2>
      <p className="muted">{t.statementText}</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <div className="field">
          <label htmlFor="statement-year">{t.year}</label>
          <select id="statement-year" value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {[0, 1, 2].map((back) => (
              <option key={back} value={lastYear - back}>
                {lastYear - back}
              </option>
            ))}
          </select>
        </div>
        <button type="button" onClick={() => void create()} disabled={busy}>
          {busy ? t.creating : t.createStatement(year)}
        </button>
      </div>
      <ErrorNotice error={error} />
      <Loading state={exports}>{(rows) => <ExportList rows={rows} onRefresh={exports.reload} />}</Loading>
    </section>
  );
}

function ExportList({ rows, onRefresh }: { rows: HoldingsExport[]; onRefresh: () => void }) {
  const t = useT(assets);
  const f = useFormat();
  if (rows.length === 0) return <p className="muted">{t.noStatement}</p>;
  return (
    <>
      <ul className="asset-lots">
        {rows.map((row) => (
          <li key={row.id}>
            <strong>
              {f.date(row.as_of)} · {row.account_label}
            </strong>
            <span className="muted">
              {t.colCreated} {f.dateTime(row.created_at)}
            </span>
            <span>
              {row.pdf_url ? <a href={row.pdf_url}>PDF</a> : null}
              {" · "}
              {row.csv_url ? <a href={row.csv_url}>CSV</a> : null}
            </span>
          </li>
        ))}
      </ul>
      <p className="muted hint">
        {t.linksExpire}{" "}
        <button type="button" className="link" onClick={onRefresh}>
          {t.renewLinks}
        </button>
      </p>
    </>
  );
}
