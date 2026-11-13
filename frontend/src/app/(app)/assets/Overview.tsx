"use client";

// Anlagen › Übersicht (phase 6 step 5b, §13, docs/mockups/09-holdings.png): every depot,
// exchange and wallet account with its positions on today's date, from GET /assets/overview.
// Each position shows its units, the price with its source and day, and the value; a price
// older than seven days is marked, a fund no issuer adapter prices shows „Kurs aus dem letzten
// Bericht“ (D174 (4)), and a position no source prices is a to-do with its button (principle 7).
// A depot fund whose latest report disagrees with the booked trades is not valued (D165 (3),
// valuation.not_valued) and links to its Zu prüfen card, as on the ETF tab.
// The mockup's unrealised gain and „Monatlich angelegt“ need lots and plans (phases 7 and 9) and
// are left out; the Krypto tab is phase 7.
//
// `?kurs=<asset id>` opens the price card for one asset: „Kursquelle wählen“ (a coin's CoinGecko
// id and Kraken pair, PATCH /assets/{id}) and „Kurs eintragen“ (POST /assets/{id}/prices). The
// two price cards on Zu prüfen link here.
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { AssetRow, AssetsOverview, OverviewAccount, OverviewPosition, PriceLabel } from "@/api/schemas";
import { formatDate, formatDecimal, formatMoney, todayIso } from "@/components/format";
import { decimalInput, toDecimalString } from "@/components/input";
import { ACCOUNT_KIND, ASSET_KIND, PRICE_SOURCE } from "@/components/labels";
import { Loading, Notice } from "@/components/ui";
import { useLoad, type Loaded } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

import type { components } from "@/api/types";

type AccountKind = components["schemas"]["AccountKind"];

const ATTRIBUTION = "Kurse: CoinGecko, Kraken, EZB, NAV der Fondsgesellschaften.";

export function OverviewTab({ priceFor }: { priceFor: string | null }) {
  const load = useCallback(() => ok(api.GET("/api/assets/overview")), []);
  const overview = useLoad(load);
  return (
    <div className="stack planning">
      {priceFor ? <PriceCard assetId={priceFor} onSaved={overview.reload} /> : null}
      <Loading state={overview}>{(data) => <Overview data={data} />}</Loading>
    </div>
  );
}

function Overview({ data }: { data: AssetsOverview }) {
  const accounts = data.accounts ?? [];
  if (accounts.length === 0) {
    return (
      <section className="card stack">
        <h2>Positionen</h2>
        <p className="muted">
          Noch keine Anlagen gebucht. Legen Sie unter <Link href="/accounts">Konten</Link> ein Depot an und laden Sie seine
          Abrechnungen und Berichte unter <Link href="/documents">Dokumente</Link> hoch; jede Position erscheint hier mit ihrem
          Tageskurs.
        </p>
      </section>
    );
  }
  const kinds = Object.entries(data.by_kind ?? {}).filter(([, value]) => value != null);
  return (
    <>
      <div className="planning-kpis">
        <div className="planning-kpi planning-kpi-main" role="group" aria-label="Wert gesamt">
          <p className="planning-kpi-label">Wert gesamt</p>
          <p className="planning-kpi-value">{formatMoney(data.total_eur)}</p>
          <p className="planning-kpi-sub">am {formatDate(data.on)}</p>
        </div>
        {kinds.map(([kind, value]) => (
          <div key={kind} className="planning-kpi" role="group" aria-label={assetKindLabel(kind)}>
            <p className="planning-kpi-label">{assetKindLabel(kind)}</p>
            <p className="planning-kpi-value">{formatMoney(value)}</p>
          </div>
        ))}
      </div>
      {(data.unvalued_positions ?? 0) > 0 || (data.stale_positions ?? 0) > 0 ? (
        <Notice>{coverageText(data.unvalued_positions ?? 0, data.stale_positions ?? 0)}</Notice>
      ) : null}
      {accounts.map((account) => (
        <AccountPositions key={account.account_id} account={account} />
      ))}
      <p className="muted hint">{ATTRIBUTION}</p>
    </>
  );
}

function assetKindLabel(kind: string): string {
  return ASSET_KIND[kind as keyof typeof ASSET_KIND] ?? kind;
}

/** Why the total is not the whole picture, as a plain to-do (principle 7). */
function coverageText(unvalued: number, stale: number): string {
  const parts: string[] = [];
  if (unvalued > 0) {
    parts.push(
      unvalued === 1
        ? "1 Position hat noch keinen Kurs und fehlt in der Summe"
        : `${unvalued} Positionen haben noch keinen Kurs und fehlen in der Summe`,
    );
  }
  if (stale > 0) {
    parts.push(stale === 1 ? "1 Position hat einen Kurs, der älter als 7 Tage ist" : `${stale} Positionen haben einen Kurs, der älter als 7 Tage ist`);
  }
  return `${parts.join("; ")}. Bei der Position steht, was zu tun ist.`;
}

function AccountPositions({ account }: { account: OverviewAccount }) {
  return (
    <section className="card stack" aria-label={`Positionen ${account.label}`}>
      <div className="planning-card-head">
        <h2>
          <Link href={`/accounts/${account.account_id}`}>{account.label}</Link>
        </h2>
        <span className="badge badge-muted">{ACCOUNT_KIND[account.kind as AccountKind] ?? account.kind}</span>
        <span className="spacer" />
        <strong>{formatMoney(account.value_eur)}</strong>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Anlage</th>
              <th scope="col" className="num">
                Anteile
              </th>
              <th scope="col" className="num">
                Kurs
              </th>
              <th scope="col" className="num">
                Wert
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
    </section>
  );
}

function PositionRow({ position }: { position: OverviewPosition }) {
  const todo = positionTodo(position);
  return (
    <tr>
      <td>
        <strong>{position.name}</strong>
        <span className="inbox-detail">{[assetKindLabel(position.kind), position.symbol ?? position.isin].filter(Boolean).join(" · ")}</span>
        {todo ? (
          <span className="inbox-detail todo-text">
            {todo.text} <Link href={todo.href}>{todo.action}</Link>
          </span>
        ) : null}
      </td>
      <td className="num">{formatDecimal(position.units)}</td>
      <td className="num">
        {position.price ? (
          <>
            {formatMoney(position.price.price_eur)}
            <span className="inbox-detail">{priceSource(position.price, position.from_report ?? false)}</span>
            {position.stale ? <span className="badge badge-warn">Kurs älter als 7 Tage</span> : null}
          </>
        ) : (
          "–"
        )}
      </td>
      <td className="num">
        <strong>{position.value_eur != null ? formatMoney(position.value_eur) : "–"}</strong>
      </td>
    </tr>
  );
}

/** "NAV der Fondsgesellschaft (State Street SPDR) vom 26.06.2026", or the report's own label. */
function priceSource(price: PriceLabel, fromReport: boolean): string {
  const day = `vom ${formatDate(price.on)}`;
  if (fromReport) return `Kurs aus dem letzten Bericht ${day}`;
  const source = PRICE_SOURCE[price.source] ?? price.source;
  return price.issuer ? `${source} (${price.issuer}) ${day}` : `${source} ${day}`;
}

/** What to do about a position without a current value; null when nothing is to do. */
function positionTodo(position: OverviewPosition): { text: string; action: string; href: string } | null {
  const priceCard = `/assets?kurs=${position.asset_id}`;
  if (position.not_valued_reason === "valuation.not_valued") {
    return {
      text: "Der letzte Depotbericht nennt eine andere Stückzahl als die gebuchten Abrechnungen; bis das geklärt ist, zählt diese Position nicht mit.",
      action: "Zu prüfen",
      href: "/inbox",
    };
  }
  if (position.kind === "cash") return null;
  if (position.value_eur == null) {
    if (position.kind === "crypto") {
      return { text: "Für diese Münze kennt Hausbuch keine Kursquelle.", action: "Kursquelle wählen", href: priceCard };
    }
    return {
      text: "Für diesen Fonds gibt es keinen täglichen Kurs und noch keinen Depotbericht.",
      action: "Kurs eintragen",
      href: priceCard,
    };
  }
  if (position.stale && !position.from_report) {
    return { text: "Der letzte Kurs ist älter als 7 Tage.", action: "Kurs eintragen", href: priceCard };
  }
  return null;
}

// --- „Kursquelle wählen“ and „Kurs eintragen“ (?kurs=<asset id>) ---------------------------

function PriceCard({ assetId, onSaved }: { assetId: string; onSaved: () => void }) {
  const load = useCallback(() => ok(api.GET("/api/assets")), []);
  const assets: Loaded<AssetRow[]> = useLoad(load);
  return (
    <Loading state={assets}>
      {(rows) => {
        const asset = rows.find((row) => row.id === assetId);
        if (!asset) {
          return (
            <section className="card stack">
              <h2>Kurs</h2>
              <p className="muted">
                Diese Anlage gibt es nicht (mehr). <Link href="/assets">Zur Übersicht</Link>
              </p>
            </section>
          );
        }
        return (
          <section className="card stack" aria-labelledby="price-card-title">
            <div className="planning-card-head">
              <h2 id="price-card-title">Kurs für {asset.name}</h2>
              <span className="spacer" />
              <Link href="/assets">Schließen</Link>
            </div>
            <p className="muted">{howPriced(asset)}</p>
            {asset.kind === "crypto" ? (
              <SourceForm
                asset={asset}
                onSaved={() => {
                  assets.reload();
                  onSaved();
                }}
              />
            ) : null}
            {asset.kind === "cash" && asset.currency === "EUR" ? null : (
              <TypedPriceForm
                asset={asset}
                onSaved={() => {
                  assets.reload();
                  onSaved();
                }}
              />
            )}
          </section>
        );
      }}
    </Loading>
  );
}

/** How this asset is priced now, in plain words (D167, D174). */
function howPriced(asset: AssetRow): string {
  const latest = asset.latest_price
    ? ` Letzter Kurs: ${formatMoney(asset.latest_price.price_eur)} (${PRICE_SOURCE[asset.latest_price.source] ?? asset.latest_price.source}, ${formatDate(asset.latest_price.on)}).`
    : " Bisher ist kein Kurs gespeichert.";
  if (asset.kind === "cash") {
    return asset.currency === "EUR"
      ? "Euro-Guthaben zählt immer zum Kurs 1."
      : `Guthaben in ${asset.currency} wird täglich mit dem Referenzkurs der EZB umgerechnet.${latest}`;
  }
  if (asset.kind === "crypto") {
    return asset.has_source
      ? `Hausbuch holt den Kurs jeden Morgen bei CoinGecko oder Kraken.${latest}`
      : `Für diese Münze kennt Hausbuch keine Kursquelle. Tragen Sie die CoinGecko-ID oder das Kraken-Paar ein, dann holt Hausbuch den Kurs jeden Morgen; bis dahin können Sie einen Kurs von Hand eintragen.${latest}`;
  }
  return asset.has_source
    ? `Hausbuch holt den Kurs jeden Morgen von der Fondsgesellschaft (NAV). Fehlt er oder ist er falsch, können Sie ihn hier von Hand eintragen.${latest}`
    : `Für diesen Fonds gibt es keinen täglichen Kurs der Fondsgesellschaft. Hausbuch bewertet ihn mit dem Kurs aus dem letzten Depotbericht; einen neueren Kurs, etwa aus der App Ihres Depots, können Sie hier eintragen.${latest}`;
}

function SourceForm({ asset, onSaved }: { asset: AssetRow; onSaved: () => void }) {
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
      <h3>Kursquelle wählen</h3>
      <div className="filter-row">
        <div className="field">
          <label htmlFor="asset-coingecko">CoinGecko-ID</label>
          <input id="asset-coingecko" value={coingecko} onChange={(e) => setCoingecko(e.target.value)} placeholder="z. B. bitcoin" />
        </div>
        <div className="field">
          <label htmlFor="asset-kraken">Kraken-Paar</label>
          <input id="asset-kraken" value={kraken} onChange={(e) => setKraken(e.target.value)} placeholder="z. B. XBTEUR" />
        </div>
      </div>
      <p className="muted hint">
        Die CoinGecko-ID steht in der Adresse der Münze auf coingecko.com (…/coins/<em>bitcoin</em>). Der nächste Abruf am Morgen holt
        die Kurse ab der ersten Buchung, höchstens 31 Tage je Lauf.
      </p>
      <div className="row">
        <button type="submit" disabled={busy}>
          {busy ? "Speichert …" : "Kursquelle speichern"}
        </button>
      </div>
      {saved ? <Notice>Gespeichert. Der Kurs kommt mit dem nächsten Abruf am Morgen.</Notice> : null}
      <ErrorNotice error={error} />
    </form>
  );
}

function TypedPriceForm({ asset, onSaved }: { asset: AssetRow; onSaved: () => void }) {
  const [on, setOn] = useState(todayIso());
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
      <h3>Kurs eintragen</h3>
      <div className="filter-row">
        <div className="field">
          <label htmlFor="asset-price-on">Tag</label>
          <input id="asset-price-on" type="date" value={on} onChange={(e) => setOn(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="asset-price-eur">Kurs je Anteil in Euro</label>
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
      <p className="muted hint">
        Ein von Hand eingetragener Kurs ersetzt für diesen Tag den abgerufenen Kurs, etwa wenn der abgerufene falsch ist. Ab dem
        nächsten Tag gilt wieder der abgerufene Kurs.
      </p>
      <div className="row">
        <button type="submit" disabled={busy}>
          {busy ? "Speichert …" : "Kurs speichern"}
        </button>
      </div>
      {saved ? <Notice>Kurs gespeichert.</Notice> : null}
      <ErrorNotice error={error} />
    </form>
  );
}
