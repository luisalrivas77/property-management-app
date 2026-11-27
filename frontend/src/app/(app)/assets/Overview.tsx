"use client";

// Anlagen › Übersicht (phase 6 step 5b, §13; phase 10 mockup 20, §7.6): what the household holds
// in investments, as one headline figure and one card per kind; no fund and no coin is listed
// here (Luis, 23 Nov 2026): they open on the ETF and Krypto tabs and in their portfolio's panel.
// - The total card: GET /assets/performance's `total_eur` (the depots, exchanges and wallets at
//   today's price as on GET /assets/overview, plus the property's equity, its estimate less its
//   loans), large, with the split of the assets inside it: GET /assets/overview's `by_kind`
//   (funds, crypto, cash: a depot's cash, an exchange's euros, money on its way) and the
//   property's equity, each with its amount. It replaces the card „Aufteilung des Vermögens“.
// - „ETF und Krypto: Gewinn seit Beginn“ (./Performance.tsx), then a card per kind (ETF, Krypto,
//   Immobilie), each opening its tab; the Immobilie card shows the equity and „X von Y eigenem
//   Geld zurück“ from GET /properties/payback's totals (`cash_back`, `paid_in`).
// - `?kurs=<asset id>` (`&tag=<day>`, from a lot's „Kurs fehlt“, a portfolio's missing price and
//   the price cards on Zu prüfen) opens „Kursquelle wählen“ (a coin's CoinGecko id and Kraken
//   pair, PATCH /assets/{id}) and „Kurs eintragen“ (POST /assets/{id}/prices) in the panel.
// - „Für euer Ziel“ (./GoalTracking.tsx, §7.9 (e) 3) after the gain card, once the household
//   follows a return on Haushalt › Ziel; GET /household-goal/tracking, its panel in the hash.
// - „Noch nicht angelegt“ (./NotInvested.tsx, §7.11 (a) 6) after it, once a split is saved and
//   the purchases fall short of it; GET /household-plan's `not_invested`, its own read.
//
// A wallet whose read movements wait for a person (a first sync proposes the whole history) is
// counted nowhere yet: its line names the coins not yet counted and links „Transaktionen prüfen“.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { AssetRow, AssetsOverview, WalletWaiting } from "@/api/schemas";
import type { components } from "@/api/types";
import { todayIso } from "@/components/format";
import { decimalInput, toDecimalString } from "@/components/input";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { Loading, Notice } from "@/components/ui";
import { useLoad, type Loaded } from "@/components/useLoad";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { assets } from "@/i18n/assets";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { type GoalTracking, GoalTrackingCard, GoalTrackingPanel, loadTracking, TRACKING_DETAIL, TRACKING_ID } from "./GoalTracking";
import { NOT_INVESTED_ID, NotInvestedCard, NotInvestedPanel, useNotInvested } from "./NotInvested";
import { type AssetsPerformance, GainKpi, gainSub, loadPerformance } from "./Performance";
import { MoneyBack } from "./Property";
import { assetKindLabel, sourceLabel } from "./words";

type T = Translation<(typeof assets)["de"]>;
type PropertiesPayback = components["schemas"]["PropertiesPayback"];

const KIND_COLOUR: Record<string, string> = {
  etf: "var(--kind-funds)",
  stock: "var(--kind-funds)",
  crypto: "var(--kind-crypto)",
  cash: "var(--kind-cash)",
  property: "var(--kind-property)",
};

export function OverviewTab({ priceFor, priceDay = null }: { priceFor: string | null; priceDay?: string | null }) {
  const load = useCallback(async () => {
    const [overview, performance, payback, tracking] = await Promise.all([
      ok(api.GET("/api/assets/overview")),
      loadPerformance(),
      // The Immobilie card's „Geld zurück“ only; Anlagen shows without it.
      ok(api.GET("/api/properties/payback")).catch(() => null),
      loadTracking(),
    ]);
    return { overview, performance, payback, tracking };
  }, []);
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {(data) => <Overview {...data} priceFor={priceFor} priceDay={priceDay} onSaved={state.reload} />}
    </Loading>
  );
}

function Overview({
  overview,
  performance,
  payback,
  tracking,
  priceFor,
  priceDay,
  onSaved,
}: {
  overview: AssetsOverview;
  performance: AssetsPerformance;
  payback: PropertiesPayback | null;
  tracking: GoalTracking | null;
  priceFor: string | null;
  priceDay: string | null;
  onSaved: () => void;
}) {
  const t = useT(assets);
  const f = useFormat();
  const router = useRouter();
  const goalDetail = useDetail(TRACKING_DETAIL);
  const notInvested = useNotInvested();
  const panel = priceFor ? (
    <DetailPanel title={t.typePrice} backLabel={t.tabOverview} onClose={() => router.replace("/assets", { scroll: false })}>
      <PriceForms assetId={priceFor} day={priceDay} onSaved={onSaved} titled />
    </DetailPanel>
  ) : tracking?.shown && goalDetail.id === TRACKING_ID ? (
    <GoalTrackingPanel tracking={tracking} backLabel={t.tabOverview} />
  ) : notInvested && goalDetail.id === NOT_INVESTED_ID ? (
    <NotInvestedPanel value={notInvested} backLabel={t.tabOverview} />
  ) : null;
  const hasProperty = (payback?.properties ?? []).length > 0 || Number(performance.property_estimate_eur) !== 0;
  if ((overview.accounts ?? []).length === 0 && !hasProperty) {
    return (
      <DetailLayout panel={panel}>
        <div className="stack">
          <WaitingNotices lines={overview.waiting ?? []} />
          <section className="card stack">
            <h2>{t.positions}</h2>
            <p className="muted">{t.noAssets}</p>
            <p className="row">
              <Link href="/accounts">{t.openAccounts}</Link> <Link href="/documents">{t.openDocuments}</Link>
            </p>
          </section>
          {/* A followed route with nothing invested yet still says how far it is (§7.9 (e) 3). */}
          {tracking?.shown || notInvested ? (
            <div className="planning-kpis">
              {tracking?.shown ? <GoalTrackingCard tracking={tracking} /> : null}
              {notInvested ? <NotInvestedCard value={notInvested} /> : null}
            </div>
          ) : null}
        </div>
      </DetailLayout>
    );
  }
  const portfolios = performance.portfolios ?? [];
  return (
    <DetailLayout panel={panel}>
      <div className="stack">
        <TotalCard overview={overview} performance={performance} hasProperty={hasProperty} />
        {(overview.unvalued_positions ?? 0) > 0 || (overview.stale_positions ?? 0) > 0 ? (
          <div className="notice notice-warn" role="status">
            <p>{coverageText(t, overview.unvalued_positions ?? 0, overview.stale_positions ?? 0)}</p>
          </div>
        ) : null}
        <WaitingNotices lines={overview.waiting ?? []} />
        <div className="planning-kpis">
          <GainKpi title={t.investedGain} figures={performance.invested} portfolios={portfolios} main />
          {tracking?.shown ? <GoalTrackingCard tracking={tracking} /> : null}
          {notInvested ? <NotInvestedCard value={notInvested} /> : null}
          <KindCard
            title={t.kindEtf}
            href="/assets?tab=etf"
            value={performance.etf.value_eur}
            sub={performance.etf.gain_eur != null ? `${t.gainSince}: ${gainSub(t, f, performance.etf)}` : null}
          />
          <KindCard
            title={t.kindCrypto}
            href="/assets?tab=krypto"
            value={performance.crypto.value_eur}
            sub={performance.crypto.gain_eur != null ? `${t.gainSince}: ${gainSub(t, f, performance.crypto)}` : null}
          />
          {hasProperty ? (
            <KindCard title={t.kindProperty} href="/assets?tab=immobilie" value={performance.property_equity_eur}>
              {payback ? <MoneyBack payback={payback} /> : null}
            </KindCard>
          ) : null}
        </div>
        <p className="muted hint">{t.attribution}</p>
      </div>
    </DetailLayout>
  );
}

/** The tab's headline: the total, large, and the split of the assets with each amount inside
 * the same card (Luis, 23 Nov 2026). The split's widths are the returned amounts relative to the
 * total, for display only; a negative equity (a loan above the estimate) shows its amount and no
 * width. */
function TotalCard({ overview, performance, hasProperty }: { overview: AssetsOverview; performance: AssetsPerformance; hasProperty: boolean }) {
  const t = useT(assets);
  const f = useFormat();
  const kinds: [string, string][] = Object.entries(overview.by_kind ?? {}).filter(
    (entry): entry is [string, string] => entry[1] != null,
  );
  if (hasProperty) kinds.push(["property", performance.property_equity_eur]);
  const widths = kinds.map(([, value]) => Math.max(0, Number(value)));
  const sum = widths.reduce((a, b) => a + b, 0);
  return (
    <section className="card stack asset-total" aria-label={t.totalTitle}>
      <p className="planning-kpi-label">{t.totalTitle}</p>
      <p className="asset-total-value">{f.money(performance.total_eur)}</p>
      <p className="muted">
        {t.onDay(f.date(performance.as_of))}
        {hasProperty ? ` · ${t.totalEstimate}` : ""}
      </p>
      {kinds.length > 0 && sum > 0 ? (
        <>
          <div className="asset-kind-bar" aria-hidden="true">
            {kinds.map(([kind], index) => (
              <span key={kind} style={{ width: `${((widths[index] ?? 0) / sum) * 100}%`, background: KIND_COLOUR[kind] }} />
            ))}
          </div>
          <ul className="asset-kinds">
            {kinds.map(([kind, value]) => (
              <li key={kind}>
                <span className="muted">
                  <span className="dot" style={{ background: KIND_COLOUR[kind] }} />
                  {kind === "property" ? t.kindProperty : assetKindLabel(t, kind)}
                </span>
                <strong>{f.money(value)}</strong>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

function KindCard({
  title,
  href,
  value,
  sub = null,
  children,
}: {
  title: string;
  href: string;
  value: string;
  sub?: string | null;
  children?: ReactNode;
}) {
  const t = useT(assets);
  const f = useFormat();
  return (
    <Link href={href} className="planning-kpi asset-kind-card" aria-label={t.openTab(title)}>
      <p className="planning-kpi-label">{title} ›</p>
      <p className="planning-kpi-value">{f.money(value)}</p>
      {sub ? <p className="planning-kpi-sub">{sub}</p> : null}
      {children}
    </Link>
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
