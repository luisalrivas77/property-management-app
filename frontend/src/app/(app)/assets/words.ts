// The Anlagen screen's words for codes the server sends (a price source, an asset or account kind,
// a reading's state), in the chosen language from src/i18n/assets.ts. components/labels.ts holds
// the same codes in German for the screens not yet moved.
import type { components } from "@/api/types";
import type { Formatter, Translation } from "@/i18n";
import type { assets } from "@/i18n/assets";

type T = Translation<(typeof assets)["de"]>;
type PriceLabel = components["schemas"]["PriceLabel"];
type OwnershipShare = components["schemas"]["OwnershipShare"];
type Owner = components["schemas"]["Owner"];

export function sourceLabel(t: T, source: string): string {
  const words: Record<string, string> = {
    trade: t.sourceTrade,
    statement: t.sourceStatement,
    issuer_nav: t.sourceIssuerNav,
    exchange_close: t.sourceExchangeClose,
    coingecko_daily: t.sourceCoingecko,
    kraken_daily: t.sourceKraken,
    ecb_fx: t.sourceEcb,
    user: t.sourceUser,
    spot: t.sourceSpot,
  };
  return words[source] ?? source;
}

/** „NAV der Fondsgesellschaft (Issuer) vom 26.06.2026“, or „Kurs aus dem Bericht vom …“ (D174 (4)). */
export function priceText(t: T, f: Formatter, price: PriceLabel, fromReport: boolean): string {
  const day = f.date(price.on);
  if (fromReport) return t.priceFromReport(day);
  const source = sourceLabel(t, price.source);
  return t.priceOf(price.issuer ? `${source} (${price.issuer})` : source, day);
}

export function assetKindLabel(t: T, kind: string): string {
  const words: Record<string, string> = { etf: t.kindEtf, stock: t.kindStock, crypto: t.kindCrypto, cash: t.kindCash };
  return words[kind] ?? kind;
}

export function accountKindLabel(t: T, kind: string): string {
  const words: Record<string, string> = { depot: t.accountDepot, exchange: t.accountExchange, wallet: t.accountWallet };
  return words[kind] ?? t.accountOther;
}

export function readingLabel(t: T, reading: string | null | undefined): string {
  if (reading == null) return t.filed;
  const words: Record<string, string> = {
    queued: t.readingQueued,
    running: t.readingRunning,
    ready: t.readingReady,
    applied: t.readingApplied,
    rejected: t.readingRejected,
    superseded: t.readingSuperseded,
    failed: t.readingFailed,
    blocked_tenant_data: t.readingBlocked,
  };
  return words[reading] ?? reading;
}

/** A signed amount: „+1.800,00 €“, „-200,00 €“, „0,00 €“. */
export function signed(f: Formatter, value: string): string {
  return `${value.startsWith("-") || /^-?0+(\.0+)?$/.test(value) ? "" : "+"}${f.money(value)}`;
}

/** A gain's colour class: none for a missing figure. */
export function gainClass(value: string | null | undefined): string {
  if (value == null) return "";
  return value.startsWith("-") ? "asset-bad" : "asset-good";
}

/** The account's owners as a chip: „gemeinsam 50/50“, one name, or names with their shares. */
export function ownerLabel(t: T, f: Formatter, shares: OwnershipShare[], owners: Owner[]): string {
  const name = (ownerId: string) => owners.find((owner) => owner.id === ownerId)?.full_name ?? "?";
  const half = (share: OwnershipShare) => share.share_numerator * 2 === share.share_denominator;
  if (shares.length === 2 && shares.every(half)) return t.jointHalf;
  const [only] = shares;
  if (shares.length === 1 && only && only.share_numerator === only.share_denominator) return name(only.owner_id);
  return shares
    .map((share) => {
      const percent = ((share.share_numerator * 100) / share.share_denominator).toFixed(2).replace(/\.?0+$/, "");
      return `${name(share.owner_id)} ${f.percent(String(Number(percent) / 100))}`;
    })
    .join(", ");
}
