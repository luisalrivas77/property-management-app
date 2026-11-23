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
//
// Since phase 10 §7.6 (mockup 20d): the four cards stay, the total large and with the gain since
// the start of every exchange and wallet (./Performance.tsx); „Heute steuerfrei verkaufbar“ and
// „Nächste steuerfrei“ open the panel of the lots tax-free today by account, then the next ones
// (`#d-steuerfrei-heute`); one card per exchange and wallet opens its coins and its payments in
// and out (`#d-krypto-<account>`), a tapped coin its lots; the global coin table is gone, and
// „Bestandsnachweis 31.12.“ is a button at the foot that opens its card in the panel
// (`#d-nachweis-jahr`); the price sources and the FIFO explanation sit behind ⓘ.
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { CryptoRefresh } from "@/components/cryptoRefresh";
import { type Detail, DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { assets } from "@/i18n/assets";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { WaitingNotices } from "./Overview";
import {
  type AssetsPerformance,
  GainInfo,
  GainTodos,
  gainSub,
  InfoNote,
  loadPerformance,
  PortfolioCard,
  PortfolioPayments,
  type PortfolioPerformance,
} from "./Performance";
import { accountKindLabel, gainClass, ownerLabel, signed } from "./words";

type T = Translation<(typeof assets)["de"]>;
type CryptoHoldings = components["schemas"]["CryptoHoldings"];
type CryptoAccount = components["schemas"]["CryptoAccount"];
type CryptoCoin = components["schemas"]["CryptoCoin"];
type CryptoLot = components["schemas"]["CryptoLot"];
type HoldingsExport = components["schemas"]["HoldingsExport"];
type Owner = components["schemas"]["Owner"];

export function CryptoTab() {
  const load = useCallback(async () => {
    const [holdings, owners, performance] = await Promise.all([
      ok(api.GET("/api/holdings/crypto")),
      ok(api.GET("/api/owners")),
      // The gain since the start; the tab shows its coins without it.
      loadPerformance().catch(() => null),
    ]);
    return { holdings, owners, performance };
  }, []);
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {({ holdings, owners, performance }) => (
        <Crypto data={holdings} owners={owners} performance={performance} onRefreshed={state.reload} />
      )}
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

function Crypto({
  data,
  owners,
  performance,
  onRefreshed,
}: {
  data: CryptoHoldings;
  owners: Owner[];
  performance: AssetsPerformance | null;
  onRefreshed: () => void;
}) {
  const t = useT(assets);
  const f = useFormat();
  const coinDetail = useDetail("coin");
  const accountDetail = useDetail("krypto");
  const taxFreeDetail = useDetail("steuerfrei");
  const statementDetail = useDetail("nachweis");
  const coins = data.coins ?? [];
  const accounts = data.accounts ?? [];
  const portfolios = (performance?.portfolios ?? []).filter((portfolio) => portfolio.kind !== "depot");
  const accountOf = (id: string) => accounts.find((a) => a.account_id === id);
  const owner = (account: CryptoAccount | undefined) =>
    account && (account.ownership ?? []).length > 0 ? ownerLabel(t, f, account.ownership ?? [], owners) : null;
  const selectedCoin = coins.find((coin) => coinKey(coin) === coinDetail.id) ?? null;
  const selectedAccount = accounts.find((account) => account.account_id === accountDetail.id) ?? null;
  const panel = selectedCoin ? (
    <DetailPanel
      title={coinLabel(selectedCoin)}
      backLabel={accountOf(selectedCoin.account_id)?.label ?? t.tabCrypto}
      onClose={() => accountDetail.open(selectedCoin.account_id)}
    >
      <LotsPanel
        coin={selectedCoin}
        account={accountOf(selectedCoin.account_id)}
        owner={owner(accountOf(selectedCoin.account_id))}
      />
    </DetailPanel>
  ) : selectedAccount ? (
    <DetailPanel title={selectedAccount.label} backLabel={t.tabCrypto} onClose={accountDetail.close}>
      <AccountPanel
        account={selectedAccount}
        owner={owner(selectedAccount)}
        coins={coins.filter((coin) => coin.account_id === selectedAccount.account_id)}
        portfolio={portfolios.find((portfolio) => portfolio.account_id === selectedAccount.account_id) ?? null}
        data={data}
        linkProps={(coin) => coinDetail.linkProps(coinKey(coin))}
      />
    </DetailPanel>
  ) : taxFreeDetail.id ? (
    <DetailPanel title={t.taxFreeTitle} backLabel={t.tabCrypto} onClose={taxFreeDetail.close}>
      <TaxFreePanel coins={coins} accounts={accounts} />
    </DetailPanel>
  ) : statementDetail.id ? (
    <DetailPanel title={t.statementTitle} backLabel={t.tabCrypto} onClose={statementDetail.close}>
      <StatementCard />
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
        <Figures
          data={data}
          accounts={accounts}
          performance={performance}
          portfolios={portfolios}
          taxFreeLink={taxFreeDetail.linkProps("heute")}
        />
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
          {accounts.map((account) => {
            const portfolio = portfolios.find((row) => row.account_id === account.account_id);
            const sub = accountSub(t, account, owner(account), coins);
            return portfolio ? (
              <PortfolioCard
                key={account.account_id}
                portfolio={portfolio}
                link={accountDetail.linkProps(account.account_id)}
                sub={sub}
              />
            ) : (
              <a
                key={account.account_id}
                {...accountDetail.linkProps(account.account_id)}
                className="card asset-portfolio-card"
                aria-label={account.label}
              >
                <span className="asset-section-head">
                  <strong>{account.label}</strong>
                  <span className="spacer" />
                  <strong>{f.money(account.value_eur)}</strong>
                </span>
                <span className="muted asset-row-sub">{sub}</span>
              </a>
            );
          })}
        </div>
        <p className="asset-foot">
          <a {...statementDetail.linkProps("jahr")} className="button-link secondary">
            {t.statementButton}
          </a>
        </p>
        <p className="muted hint">
          {t.priceSources} <InfoNote label={t.priceSources}>{t.cryptoAttribution}</InfoNote>
        </p>
      </div>
    </DetailLayout>
  );
}

/** The line under an account's card: provider or kind, its coins, its owners. */
function accountSub(t: T, account: CryptoAccount, owner: string | null, coins: CryptoCoin[]): string {
  const symbols = coins
    .filter((coin) => coin.account_id === account.account_id)
    .map((coin) => coin.symbol ?? coin.name)
    .join(", ");
  return [account.provider ?? accountKindLabel(t, account.kind), symbols, owner].filter(Boolean).join(" · ");
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

function Figures({
  data,
  accounts,
  performance,
  portfolios,
  taxFreeLink,
}: {
  data: CryptoHoldings;
  accounts: CryptoAccount[];
  performance: AssetsPerformance | null;
  portfolios: PortfolioPerformance[];
  taxFreeLink: ReturnType<Detail["linkProps"]>;
}) {
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
  const gain = performance?.crypto.gain_eur;
  return (
    <div className="planning-kpis">
      <div className="planning-kpi planning-kpi-main asset-total" role="group" aria-label={t.cryptoTotal}>
        <p className="planning-kpi-label">{t.cryptoTotal}</p>
        <p className="asset-total-value">{f.money(data.total_eur)}</p>
        <p className="planning-kpi-sub">
          {t.coins(coins.length)} {t.inAccounts(accounts.length)}
          {data.unvalued_coins ? ` · ${t.unvaluedCoins(data.unvalued_coins)}` : ""}
        </p>
        {performance ? (
          <p className="planning-kpi-sub">
            {t.gainSince} <GainInfo />{" "}
            <strong className={gain != null ? gainClass(gain) : undefined}>{gain != null ? signed(f, gain) : t.gainUnknown}</strong>
            {gain != null ? ` · ${gainSub(t, f, performance.crypto)}` : ""}
          </p>
        ) : null}
        {performance ? <GainTodos portfolios={portfolios} /> : null}
      </div>
      <div className="planning-kpi" role="group" aria-label={t.unrealisedGain}>
        <p className="planning-kpi-label">{t.unrealisedGain}</p>
        <p className={`planning-kpi-value ${data.unrealized_eur.startsWith("-") ? "planning-bad" : "planning-good"}`}>
          {signed(f, data.unrealized_eur)}
        </p>
        <p className="planning-kpi-sub">{data.unrealized_excluded ? t.unrealisedExcluded(data.unrealized_excluded) : t.valueMinusCost}</p>
      </div>
      <a {...taxFreeLink} className="planning-kpi asset-kind-card" aria-label={t.taxFreeToday}>
        <span className="planning-kpi-label">{t.taxFreeToday}</span>
        <span className="planning-kpi-value">{f.money(data.tax_free_today_eur)}</span>
        <span className="planning-kpi-sub">
          {t.holdingYear}
          {data.tax_free_today_excluded ? ` · ${t.lotsWithoutPrice(data.tax_free_today_excluded)}` : ""}
        </span>
        <span className="planning-kpi-sub">{t.tapToSee} ›</span>
      </a>
      <a {...taxFreeLink} className="planning-kpi asset-kind-card" aria-label={t.nextTranche}>
        <span className="planning-kpi-label">{t.nextTranche}</span>
        <span className="planning-kpi-value">{next ? f.date(next.on) : "–"}</span>
        <span className="planning-kpi-sub">
          {next ? `${nextParts}${next.value_eur != null ? ` · ${f.money(next.value_eur)}` : ""}` : t.allTaxFree}
        </span>
        <span className="planning-kpi-sub">{t.tapToSee} ›</span>
      </a>
    </div>
  );
}

/** „Heute steuerfrei verkaufbar“ and „Nächste steuerfrei“ open this: the lots tax-free today by
 * account (units, value, purchase day), then the next lots by the day they turn tax-free. Every
 * figure is a lot's own (GET /holdings/crypto); nothing is summed here. */
function TaxFreePanel({ coins, accounts }: { coins: CryptoCoin[]; accounts: CryptoAccount[] }) {
  const t = useT(assets);
  const f = useFormat();
  const lots = coins.flatMap((coin) => (coin.lots ?? []).map((lot) => ({ coin, lot })));
  const free = lots.filter(({ lot }) => lot.tax_free && lot.source !== "unknown");
  const coming = lots
    .filter(({ lot }) => !lot.tax_free && lot.source !== "unknown")
    .sort((a, b) => a.lot.tax_free_from.localeCompare(b.lot.tax_free_from));
  const line = ({ coin, lot }: { coin: CryptoCoin; lot: CryptoLot }) => (
    <li key={lot.id}>
      <div className="asset-lot-line">
        <strong>{t.lotSince(`${f.decimal(lot.units_open)} ${coin.symbol ?? ""}`.trim(), f.date(lot.acquired_on))}</strong>
        <strong>{lot.value_eur != null ? f.money(lot.value_eur) : "–"}</strong>
      </div>
      {lot.tax_free ? null : (
        <div className="asset-lot-line">
          <span className="muted">{accounts.find((a) => a.account_id === lot.account_id)?.label ?? ""}</span>
          <span className="badge badge-muted">{t.taxFreeFromLong(f.date(lot.tax_free_from))}</span>
        </div>
      )}
    </li>
  );
  return (
    <div className="stack">
      <h3>{t.taxFreeNowTitle}</h3>
      {free.length === 0 ? <p className="muted">{t.noneTaxFreeToday}</p> : null}
      {accounts
        .filter((account) => free.some(({ lot }) => lot.account_id === account.account_id))
        .map((account) => (
          <section key={account.account_id} className="stack" aria-label={account.label}>
            <h4 style={{ margin: 0 }}>{account.label}</h4>
            <ul className="asset-lots">{free.filter(({ lot }) => lot.account_id === account.account_id).map(line)}</ul>
          </section>
        ))}
      <h3>{t.nextTaxFreeTitle}</h3>
      {coming.length === 0 ? <p className="muted">{t.noNextTaxFree}</p> : <ul className="asset-lots">{coming.map(line)}</ul>}
      <p className="muted hint">
        {t.holdingYear} <InfoNote label={t.taxFreeTitle}>{t.fifoHint}</InfoNote>
      </p>
    </div>
  );
}

/** An exchange's or wallet's panel: its gain since the start and its payments, then its coins
 * with their gain and „steuerfrei ab“; a tapped coin opens its lots. */
function AccountPanel({
  account,
  owner,
  coins,
  portfolio,
  data,
  linkProps,
}: {
  account: CryptoAccount;
  owner: string | null;
  coins: CryptoCoin[];
  portfolio: PortfolioPerformance | null;
  data: CryptoHoldings;
  linkProps: (coin: CryptoCoin) => ReturnType<Detail["linkProps"]>;
}) {
  const t = useT(assets);
  const f = useFormat();
  const waiting = (data.waiting ?? []).find((line) => line.account_id === account.account_id) ?? null;
  const unreconciled = coins.some((coin) => !coin.reconciled);
  return (
    <div className="stack">
      <p className="muted">
        {[account.provider ?? accountKindLabel(t, account.kind), owner].filter(Boolean).join(" · ")}{" "}
        {owner ? null : <Link href={`/accounts/${account.account_id}`}>{t.setOwners}</Link>}
      </p>
      {waiting ? (
        <p className="muted">
          {t.waitingTx(waiting.groups)}: <Link href={waiting.href}>{waiting.action}</Link>
        </p>
      ) : null}
      {unreconciled ? (
        <div className="notice notice-warn" role="status">
          <p>
            {t.notReconciled} <Link href={`/accounts/${account.account_id}`}>{t.openWallet}</Link>
          </p>
        </div>
      ) : null}
      <h3>{t.coinsOfAccount}</h3>
      {coins.length === 0 ? (
        <p className="muted">{t.noCoins(f.date(data.on))}</p>
      ) : (
        <ul className="asset-rows">
          {coins.map((coin) => {
            const moment = priceMoment(t, f, coin, data);
            return (
              <li key={coinKey(coin)}>
                <a {...linkProps(coin)} className="asset-row">
                  <span className="asset-row-name">
                    <strong>{coinLabel(coin)}</strong>
                    <span className="asset-row-sub">
                      {f.decimal(coin.units)} {coin.symbol ?? ""}
                      {moment ? ` · ${moment}` : ""}
                    </span>
                    <span className="asset-row-sub">
                      <span className={holdingBadge(coin)}>{holdingText(t, f, coin)}</span>
                    </span>
                  </span>
                  <span className="num asset-value">
                    {coin.value_eur != null ? f.money(coin.value_eur) : t.noValue}
                    <span className={`asset-row-sub ${gainClass(coin.unrealized_eur)}`} style={{ display: "block" }}>
                      {coin.unrealized_eur != null ? signed(f, coin.unrealized_eur) : "–"}
                    </span>
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      )}
      {portfolio ? <PortfolioPayments portfolio={portfolio} /> : null}
      <Link href={`/accounts/${account.account_id}`} className="button-link secondary">
        {t.openAccount(account.label)}
      </Link>
    </div>
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
      <p className="muted hint">
        {t.openLots} <InfoNote label={t.openLots}>{t.fifoHint}</InfoNote>
      </p>
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
