"use client";

// Anlagen › Übersicht (phase 6 step 5b, §13; phase 10 mockup 20): every depot, exchange and
// wallet account with its positions on today's date, from GET /assets/overview. Each position
// shows its units, the price with its source and day, and the value; a price older than seven
// days is marked, a fund no issuer adapter prices shows „Kurs aus dem Bericht vom …“ (D174 (4)),
// and a position no source prices is a to-do with its button (principle 7). A depot fund whose
// latest report disagrees with the booked trades is not valued (D165 (3), valuation.not_valued)
// and links to its Zu prüfen card, as on the ETF tab.
//
// A tapped position opens the detail panel (D267, `#d-position-<account>~<asset>`) with its
// figures and the price forms: „Kursquelle wählen“ (a coin's CoinGecko id and Kraken pair,
// PATCH /assets/{id}) and „Kurs eintragen“ (POST /assets/{id}/prices). `?kurs=<asset id>`
// (`&tag=<day>`, from a lot's „Kurs fehlt“ on the Krypto tab and the price cards on Zu prüfen)
// opens the same forms in the panel for one asset.
//
// A wallet whose read movements wait for a person (a first sync proposes the whole history) is
// counted nowhere yet: its line names the coins not yet counted and links „Transaktionen prüfen“
// on the wallet's page, inside the wallet's card, or on its own while nothing is confirmed.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { AssetRow, AssetsOverview, OverviewAccount, OverviewPosition, WalletWaiting } from "@/api/schemas";
import { todayIso } from "@/components/format";
import { decimalInput, toDecimalString } from "@/components/input";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { Loading, Notice } from "@/components/ui";
import { useLoad, type Loaded } from "@/components/useLoad";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { assets } from "@/i18n/assets";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { PortfolioParts } from "./PortfolioParts";
import { accountKindLabel, assetKindLabel, priceText, sourceLabel } from "./words";

type T = Translation<(typeof assets)["de"]>;

const KIND_COLOUR: Record<string, string> = {
  etf: "var(--kind-funds)",
  stock: "var(--kind-funds)",
  crypto: "var(--kind-crypto)",
  cash: "var(--kind-cash)",
};

export function OverviewTab({ priceFor, priceDay = null }: { priceFor: string | null; priceDay?: string | null }) {
  const load = useCallback(() => ok(api.GET("/api/assets/overview")), []);
  const overview = useLoad(load);
  return <Loading state={overview}>{(data) => <Overview data={data} priceFor={priceFor} priceDay={priceDay} onSaved={overview.reload} />}</Loading>;
}

const positionKey = (accountId: string, assetId: string) => `${accountId}~${assetId}`;

function Overview({
  data,
  priceFor,
  priceDay,
  onSaved,
}: {
  data: AssetsOverview;
  priceFor: string | null;
  priceDay: string | null;
  onSaved: () => void;
}) {
  const t = useT(assets);
  const f = useFormat();
  const router = useRouter();
  const detail = useDetail("position");
  const accounts = data.accounts ?? [];
  const waiting = data.waiting ?? [];
  const withRow = new Set(accounts.map((account) => account.account_id));
  const alone = waiting.filter((line) => !withRow.has(line.account_id));

  let panel = null;
  const open = accounts.flatMap((account) => (account.positions ?? []).map((position) => ({ account, position })));
  const selected = open.find(({ account, position }) => positionKey(account.account_id, position.asset_id) === detail.id);
  if (selected) {
    panel = (
      <DetailPanel title={selected.position.name} backLabel={t.tabOverview} onClose={detail.close}>
        <PositionPanel account={selected.account} position={selected.position} onSaved={onSaved} />
      </DetailPanel>
    );
  } else if (priceFor) {
    const close = () => router.replace("/assets", { scroll: false });
    panel = (
      <DetailPanel title={t.typePrice} backLabel={t.tabOverview} onClose={close}>
        <PriceForms assetId={priceFor} day={priceDay} onSaved={onSaved} titled />
      </DetailPanel>
    );
  }

  if (accounts.length === 0) {
    return (
      <DetailLayout panel={panel}>
        <div className="stack">
          <WaitingNotices lines={alone} />
          <section className="card stack">
            <h2>{t.positions}</h2>
            <p className="muted">{t.noAssets}</p>
            <p className="row">
              <Link href="/accounts">{t.openAccounts}</Link> <Link href="/documents">{t.openDocuments}</Link>
            </p>
          </section>
        </div>
      </DetailLayout>
    );
  }
  const kinds = Object.entries(data.by_kind ?? {}).filter((entry): entry is [string, string] => entry[1] != null);
  const total = Number(data.total_eur);
  return (
    <DetailLayout panel={panel}>
      <div className="stack">
        <section className="card stack" aria-label={t.totalValue}>
          <p className="planning-kpi-label">{t.totalValue}</p>
          <p className="planning-kpi-value">{f.money(data.total_eur)}</p>
          <p className="muted">{t.onDay(f.date(data.on))}</p>
          {kinds.length > 0 && total > 0 ? (
            <>
              <div className="asset-kind-bar" aria-hidden="true">
                {kinds.map(([kind, value]) => (
                  <span key={kind} style={{ width: `${Math.max(0, (Number(value) / total) * 100)}%`, background: KIND_COLOUR[kind] }} />
                ))}
              </div>
              <ul className="asset-kinds">
                {kinds.map(([kind, value]) => (
                  <li key={kind}>
                    <span className="muted">
                      <span className="dot" style={{ background: KIND_COLOUR[kind] }} />
                      {assetKindLabel(t, kind)}
                    </span>
                    <strong>{f.money(value)}</strong>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
        {(data.unvalued_positions ?? 0) > 0 || (data.stale_positions ?? 0) > 0 ? (
          <div className="notice notice-warn" role="status">
            <p>{coverageText(t, data.unvalued_positions ?? 0, data.stale_positions ?? 0)}</p>
          </div>
        ) : null}
        <WaitingNotices lines={alone} />
        {accounts.map((account) => (
          <AccountPositions
            key={account.account_id}
            account={account}
            waiting={waiting.find((line) => line.account_id === account.account_id) ?? null}
            linkProps={(assetId) => detail.linkProps(positionKey(account.account_id, assetId))}
          />
        ))}
        <p className="muted hint">{t.attribution}</p>
      </div>
    </DetailLayout>
  );
}

/** Why the total is not the whole picture, as a plain to-do (principle 7). */
function coverageText(t: T, unvalued: number, stale: number): string {
  const parts: string[] = [];
  if (unvalued > 0) parts.push(t.unvalued(unvalued));
  if (stale > 0) parts.push(t.staleCount(stale));
  return `${parts.join("; ")}. ${t.coverageEnd}`;
}

/** A wallet's coins read but not yet confirmed, with the link that confirms them (principle 7).
 * The text and the link's label come from the server. */
export function WaitingNotices({ lines }: { lines: WalletWaiting[] }) {
  return (
    <>
      {lines.map((line) => (
        <Notice key={line.account_id}>
          {line.text} <Link href={line.href}>{line.action}</Link>
        </Notice>
      ))}
    </>
  );
}

type LinkProps = ReturnType<ReturnType<typeof useDetail>["linkProps"]>;

function AccountPositions({
  account,
  waiting,
  linkProps,
}: {
  account: OverviewAccount;
  waiting: WalletWaiting | null;
  linkProps: (assetId: string) => LinkProps;
}) {
  const t = useT(assets);
  const f = useFormat();
  const cash = (account.cash ?? []).filter((line) => line.value_eur != null);
  return (
    <section className="card stack" aria-label={t.positionsOf(account.label)}>
      <div className="asset-section-head">
        <h2>
          <Link href={`/accounts/${account.account_id}`}>{account.label}</Link>
        </h2>
        <span className="badge badge-muted">{accountKindLabel(t, account.kind)}</span>
        <span className="spacer" />
        <strong>{f.money(account.total_eur)}</strong>
      </div>
      {cash.length > 0 ? <PortfolioParts label={account.label} funds={account.value_eur} cash={cash} /> : null}
      <WaitingNotices lines={waiting ? [waiting] : []} />
      <ul className="asset-rows" data-cols="overview">
        <li className="asset-rows-head asset-col" aria-hidden="true">
          <span>{t.colAsset}</span>
          <span className="num">{t.colUnits}</span>
          <span className="num">{t.colPrice}</span>
          <span className="num">{t.colValue}</span>
        </li>
        {(account.positions ?? []).map((position) => (
          <li key={position.asset_id}>
            <PositionRow position={position} link={linkProps(position.asset_id)} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function PositionRow({ position, link }: { position: OverviewPosition; link: LinkProps }) {
  const t = useT(assets);
  const f = useFormat();
  const todo = positionTodo(t, position);
  const price = position.price ? priceText(t, f, position.price, position.from_report ?? false) : null;
  return (
    <a {...link} className="asset-row">
      <span className="asset-row-name">
        <strong>{position.name}</strong>
        <span className="asset-row-sub">
          {[assetKindLabel(t, position.kind), position.symbol ?? position.isin].filter(Boolean).join(" · ")}
        </span>
        <span className="asset-row-sub asset-phone">
          {[t.unitsOf(f.decimal(position.units)), price].filter(Boolean).join(" · ")}
        </span>
        {position.stale ? <span className="badge badge-warn">{t.stale}</span> : null}
        {todo ? <span className="asset-row-sub todo-text">{todo.text}</span> : null}
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
      <span className="num asset-value">{position.value_eur != null ? f.money(position.value_eur) : t.noValue}</span>
    </a>
  );
}

/** What to do about a position without a current value; null when nothing is to do. */
function positionTodo(t: T, position: OverviewPosition): { text: string; action: string; href: string | null } | null {
  if (position.not_valued_reason === "valuation.not_valued") {
    return { text: t.todoNotValued, action: t.actionInbox, href: "/inbox" };
  }
  if (position.kind === "cash") return null;
  if (position.value_eur == null) {
    if (position.kind === "crypto") return { text: t.todoNoCoinSource, action: t.actionChooseSource, href: null };
    return { text: t.todoNoFundPrice, action: t.actionTypePrice, href: null };
  }
  if (position.stale && !position.from_report) return { text: t.todoStale, action: t.actionTypePrice, href: null };
  return null;
}

/** The panel of a tapped position: its figures, its to-do and the price forms. */
function PositionPanel({ account, position, onSaved }: { account: OverviewAccount; position: OverviewPosition; onSaved: () => void }) {
  const t = useT(assets);
  const f = useFormat();
  const todo = positionTodo(t, position);
  return (
    <div className="stack">
      <p className="muted">{[account.label, accountKindLabel(t, account.kind)].join(" · ")}</p>
      <dl className="facts">
        <div>
          <dt>{t.units}</dt>
          <dd>{f.decimal(position.units)}</dd>
        </div>
        <div>
          <dt>{t.priceToday}</dt>
          <dd>
            {position.price ? f.money(position.price.price_eur) : "–"}
            {position.price ? <span className="asset-row-sub" style={{ display: "block" }}>{priceText(t, f, position.price, position.from_report ?? false)}</span> : null}
          </dd>
        </div>
        <div>
          <dt>{t.valueToday}</dt>
          <dd>
            <strong>{position.value_eur != null ? f.money(position.value_eur) : t.noValue}</strong>
          </dd>
        </div>
      </dl>
      {position.stale ? <span className="badge badge-warn">{t.stale}</span> : null}
      {todo ? (
        <div className="notice notice-warn" role="status">
          <p>
            {todo.text} {todo.href ? <Link href={todo.href}>{todo.action}</Link> : null}
          </p>
        </div>
      ) : null}
      {position.kind === "cash" && position.symbol === "EUR" ? null : <PriceForms assetId={position.asset_id} day={null} onSaved={onSaved} />}
      <Link href={`/accounts/${account.account_id}`} className="button-link secondary">
        {t.openAccount(account.label)}
      </Link>
    </div>
  );
}

// --- „Kursquelle wählen“ and „Kurs eintragen“ ------------------------------------------------

function PriceForms({ assetId, day, onSaved, titled = false }: { assetId: string; day: string | null; onSaved: () => void; titled?: boolean }) {
  const t = useT(assets);
  const f = useFormat();
  const load = useCallback(() => ok(api.GET("/api/assets")), []);
  const rows: Loaded<AssetRow[]> = useLoad(load);
  return (
    <Loading state={rows}>
      {(list) => {
        const asset = list.find((row) => row.id === assetId);
        if (!asset) {
          return (
            <p className="muted">
              {t.assetGone} <Link href="/assets">{t.backToOverview}</Link>
            </p>
          );
        }
        const saved = () => {
          rows.reload();
          onSaved();
        };
        return (
          <div className="stack">
            {titled ? <h3>{t.priceFor(asset.name)}</h3> : null}
            <p className="muted">{howPriced(t, f, asset)}</p>
            {asset.kind === "crypto" ? <SourceForm asset={asset} onSaved={saved} /> : null}
            {asset.kind === "cash" && asset.currency === "EUR" ? null : <TypedPriceForm asset={asset} day={day} onSaved={saved} />}
          </div>
        );
      }}
    </Loading>
  );
}

/** How this asset is priced now, in plain words (D167, D174). */
function howPriced(t: T, f: Formatter, asset: AssetRow): string {
  const latest = asset.latest_price
    ? t.latestPrice(f.money(asset.latest_price.price_eur), sourceLabel(t, asset.latest_price.source), f.date(asset.latest_price.on))
    : t.noPriceYet;
  if (asset.kind === "cash") return asset.currency === "EUR" ? t.cashEuro : `${t.cashFx(asset.currency)}${latest}`;
  if (asset.kind === "crypto") return `${asset.has_source ? t.coinWithSource : t.coinWithoutSource}${latest}`;
  // D264 (8): a fund with no issuer source, priced by the exchange's daily quote.
  if (asset.price_venue) return `${t.fundVenue(asset.price_venue)}${latest}`;
  return `${asset.has_source ? t.fundNav : t.fundReport}${latest}`;
}

function SourceForm({ asset, onSaved }: { asset: AssetRow; onSaved: () => void }) {
  const t = useT(assets);
  const [coingecko, setCoingecko] = useState(asset.coingecko_id ?? "");
  const [kraken, setKraken] = useState(asset.kraken_pair ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: { coingecko_id?: string; kraken_pair?: string } = {};
    if (coingecko.trim() && coingecko.trim() !== asset.coingecko_id) body.coingecko_id = coingecko.trim();
    if (kraken.trim() && kraken.trim() !== asset.kraken_pair) body.kraken_pair = kraken.trim().toUpperCase();
    if (Object.keys(body).length === 0) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await ok(api.PATCH("/api/assets/{asset_id}", { params: { path: { asset_id: asset.id } }, body }));
      setSaved(true);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={(event) => void submit(event)} noValidate>
      <h3>{t.chooseSource}</h3>
      <div className="field">
        <label htmlFor="asset-coingecko">{t.coingeckoId}</label>
        <input id="asset-coingecko" value={coingecko} onChange={(e) => setCoingecko(e.target.value)} placeholder={t.coingeckoPlaceholder} />
      </div>
      <div className="field">
        <label htmlFor="asset-kraken">{t.krakenPair}</label>
        <input id="asset-kraken" value={kraken} onChange={(e) => setKraken(e.target.value)} placeholder={t.krakenPlaceholder} />
      </div>
      <p className="muted hint">{t.sourceHint}</p>
      <div className="row">
        <button type="submit" className="secondary" disabled={busy}>
          {busy ? t.saving : t.saveSource}
        </button>
      </div>
      {saved ? <Notice>{t.sourceSaved}</Notice> : null}
      <ErrorNotice error={error} />
    </form>
  );
}

function TypedPriceForm({ asset, day, onSaved }: { asset: AssetRow; day: string | null; onSaved: () => void }) {
  const t = useT(assets);
  // `?tag=<day>` from a lot's „Kurs fehlt“ on the Krypto tab (phase 7 D188 (4)).
  const [on, setOn] = useState(day && /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : todayIso());
  const [price, setPrice] = useState(decimalInput(null));
  const [invalid, setInvalid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = toDecimalString(price, 8);
    if (!parsed.ok) {
      setInvalid(parsed.message);
      return;
    }
    setInvalid(null);
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await ok(
        api.POST("/api/assets/{asset_id}/prices", {
          params: { path: { asset_id: asset.id } },
          body: { on, price_eur: parsed.value },
        }),
      );
      setSaved(true);
      setPrice("");
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={(event) => void submit(event)} noValidate>
      <h3>{t.typePrice}</h3>
      <div className="filter-row">
        <div className="field">
          <label htmlFor="asset-price-on">{t.day}</label>
          <input id="asset-price-on" type="date" value={on} onChange={(e) => setOn(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="asset-price-eur">{t.pricePerUnit}</label>
          <input
            id="asset-price-eur"
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            aria-invalid={invalid ? true : undefined}
            aria-describedby={invalid ? "asset-price-eur-error" : undefined}
          />
          {invalid ? (
            <ul id="asset-price-eur-error" className="field-errors">
              <li>{invalid}</li>
            </ul>
          ) : null}
        </div>
      </div>
      <p className="muted hint">{t.typedHint}</p>
      <div className="row">
        <button type="submit" disabled={busy}>
          {busy ? t.saving : t.savePrice}
        </button>
      </div>
      {saved ? <Notice>{t.priceSaved}</Notice> : null}
      <ErrorNotice error={error} />
    </form>
  );
}
