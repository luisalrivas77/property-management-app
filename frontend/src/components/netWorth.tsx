"use client";

// The household's net worth on the Übersicht (phase 6 §13, D167 (4), (5), mockup 07): the card
// „Nettovermögen“ (today on read against the latest stored month-end, with the button
// „Monatsabschluss festhalten“) and the bar „Vermögensaufteilung“ by class, the property net of
// its loans. Every euro figure is the server's (GET /net-worth/overview); the change since the
// month-end and the bar's percentages are display arithmetic in integer cents (BigInt).
// Estimates (the property's value, a loan's balance by its schedule) are labelled as such, and
// every gap the server names is a to-do with its button or link (principle 7).
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { translate, useFormat, useT } from "@/i18n";
import { netWorthText } from "@/i18n/netWorth";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { propertyPlanningHref } from "@/lib/planningRoutes";

import { fromCents, signedMoney, toCents } from "./cashflow";
import { CryptoRefresh } from "./cryptoRefresh";
import { Loading } from "./ui";
import { useLoad, type Loaded as LoadState } from "./useLoad";

type NetWorthLine = components["schemas"]["NetWorthLine"];
type NetWorthTodo = components["schemas"]["NetWorthTodo"];
/** A NetWorth with its defaulted lists filled in (the API always sends them). */
type NetWorth = components["schemas"]["NetWorth"] & { lines: NetWorthLine[]; todos: NetWorthTodo[]; unassigned: string[] };
interface NetWorthOverview {
  today: NetWorth;
  month_end: NetWorth | null;
  month_end_due: string | null;
  /** Today's reading of the stored month-end's day, when it differs from the stored total. */
  month_end_now: string | null;
}
type SnapshotResult = components["schemas"]["SnapshotResult"];
type Owner = components["schemas"]["Owner"];

interface Loaded {
  overview: NetWorthOverview;
  owners: Owner[];
}

function filled(figure: components["schemas"]["NetWorth"]): NetWorth {
  return { ...figure, lines: figure.lines ?? [], todos: figure.todos ?? [], unassigned: figure.unassigned ?? [] };
}

async function loadNetWorth(): Promise<Loaded> {
  const [overview, owners] = await Promise.all([ok(api.GET("/api/net-worth/overview")), ok(api.GET("/api/owners"))]);
  return {
    overview: {
      today: filled(overview.today),
      month_end: overview.month_end ? filled(overview.month_end) : null,
      month_end_due: overview.month_end_due ?? null,
      month_end_now: overview.month_end_now ?? null,
    },
    owners,
  };
}

/** The Übersicht's two net-worth sections; one request for both. With `onCryptoRefreshed`,
 * „Jetzt aktualisieren“ (D291) above them reloads both and tells the caller. */
export function NetWorthSection({ onCryptoRefreshed }: { onCryptoRefreshed?: () => void } = {}) {
  const state = useLoad(loadNetWorth);
  const { reload } = state;
  const refreshed = useCallback(() => {
    reload();
    onCryptoRefreshed?.();
  }, [reload, onCryptoRefreshed]);
  return (
    <>
      {onCryptoRefreshed ? <CryptoRefresh onRefreshed={refreshed} /> : null}
      <NetWorthFigures state={state} />
    </>
  );
}

function NetWorthFigures({ state }: { state: LoadState<Loaded> }) {
  return (
    <Loading state={state}>
      {({ overview, owners }) => (
        <>
          <NetWorthCard overview={overview} owners={owners} onChanged={state.reload} />
          <AllocationBar figure={overview.today} />
        </>
      )}
    </Loading>
  );
}

type NetWorthText = (typeof netWorthText)["de"];

/** The capture's outcome as a sentence, the date already formatted; healthFigures.tsx says the
 * same after its own capture. */
export function snapshotOutcomeText(t: NetWorthText, outcome: SnapshotResult["outcome"], date: string): string {
  switch (outcome) {
    case "written":
      return t.snapshotWritten(date);
    case "recaptured":
      return t.snapshotRecaptured(date);
    case "unchanged":
      return t.snapshotUnchanged(date);
    case "empty":
      return t.snapshotEmpty;
  }
}

/** The capture's sentence on what the stored figure leaves out: a position without a price, or a
 * fund the depot report lists with nothing booked (the to-dos below say what to do). */
function unvaluedText(t: NetWorthText, count: number | undefined): string {
  if (!count) return "";
  return t.unvalued(count);
}

function NetWorthCard({ overview, owners, onChanged }: { overview: NetWorthOverview; owners: Owner[]; onChanged: () => void }) {
  const { today, month_end: monthEnd, month_end_due: due, month_end_now: drifted } = overview;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<SnapshotResult | null>(null);
  const t = useT(netWorthText);
  const f = useFormat();

  async function capture() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await ok(api.POST("/api/net-worth/snapshots")));
      onChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const estimates = today.lines.filter((line) => line.estimate && line.value_eur != null);
  const paid = today.lines.filter(isPaidBeforeHandover);
  const moving = today.lines.filter((line) => line.in_transit && line.value_eur != null);
  const older = today.lines.filter((line) => line.kind === "account" && line.as_of != null && line.as_of < today.on);
  const names = new Map(owners.map((owner) => [owner.id, owner.full_name]));
  const byOwner = Object.entries(today.by_owner);
  return (
    <section className="card stack" aria-labelledby="net-worth-card-title">
      <h2 id="net-worth-card-title">{t.title}</h2>
      {today.lines.length === 0 ? (
        <p className="muted">
          {t.emptyBefore}
          <Link href="/accounts">{t.accounts}</Link>
          {t.emptyAfter}
        </p>
      ) : (
        <>
          <p className="cashflow-value">{f.money(today.total)}</p>
          <p className="muted">
            {t.today(f.date(today.on))}
            {monthEnd ? (
              <>
                {" · "}
                <span className={toCents(today.total) >= toCents(monthEnd.total) ? "net-worth-change" : "net-worth-change-down"}>
                  {t.changeSince(signedMoney(fromCents(toCents(today.total) - toCents(monthEnd.total)), f.money), f.date(monthEnd.on))}
                </span>{" "}
                ({f.money(monthEnd.total)})
              </>
            ) : (
              t.noMonthEnd
            )}
          </p>
          {byOwner.length > 0 ? (
            <p className="muted">
              {byOwner.map(([id, value]) => `${names.get(id) ?? t.owner} ${f.money(value)}`).join(" · ")}
              {today.unassigned.length > 0 ? t.unassigned : ""}
            </p>
          ) : null}
          {older.length > 0 ? <BalanceDates lines={older} /> : null}
          {paid.length > 0 ? <PaidNote lines={paid} /> : null}
          {moving.length > 0 ? <TransitNote lines={moving} /> : null}
          {estimates.length > 0 ? <EstimateNote lines={estimates} /> : null}
        </>
      )}
      {due ? (
        <p>{t.due(f.date(due))}</p>
      ) : null}
      {monthEnd && drifted ? (
        <div className="notice notice-warn">
          <p>{t.drifted(f.money(monthEnd.total), f.money(drifted), f.date(monthEnd.on))}</p>
        </div>
      ) : null}
      <div className="row">
        <button type="button" className={due || drifted ? undefined : "secondary"} onClick={() => void capture()} disabled={busy}>
          {busy ? t.capturing : drifted ? t.recapture : t.captureMonthEnd}
        </button>
      </div>
      {result ? (
        <p className="muted">
          {snapshotOutcomeText(t, result.outcome, f.date(result.as_of))}
          {unvaluedText(t, result.unvalued_positions)}
        </p>
      ) : null}
      <ErrorNotice error={error} />
      {today.todos.length > 0 ? <NetWorthTodos todos={today.todos} /> : null}
    </section>
  );
}

/** The balances that count at an earlier day than the figure's: each with its „Stand vom …“ (the
 * last day a reconciled statement covers, D167 (4)). */
function BalanceDates({ lines }: { lines: NetWorthLine[] }) {
  const t = useT(netWorthText);
  const f = useFormat();
  return (
    <p className="muted">
      {lines
        .map((line) => t.balanceAsOf(line.label, f.money(line.value_eur), f.date(line.as_of)))
        .join("; ")}
    </p>
  );
}

/** Phase 6 D265: before a property's handover its line („Anzahlung Kaufpreis – …“) and its
 * loans' lines are booked figures, not estimates: the price paid so far and what each loan paid
 * out. They are the only property and loan lines the server sends with `estimate` false. */
function isPaidBeforeHandover(line: NetWorthLine): boolean {
  return (line.kind === "property" || line.kind === "loan") && !line.estimate && line.value_eur != null;
}

/** The booked figures before the handover, each with the server's note (until when it counts). */
function PaidNote({ lines }: { lines: NetWorthLine[] }) {
  const t = useT(netWorthText);
  const f = useFormat();
  return (
    <div className="notice notice-info">
      <p>{t.beforeHandover}</p>
      <ul>
        {lines.map((line, index) => (
          <li key={`${line.kind}-${line.property_id ?? ""}-${line.loan_id ?? ""}-${index}`}>
            {line.kind === "loan" ? t.loan : ""}
            {quoted(f.locale, line.label)} {f.money(line.value_eur)}
            {line.note ? ` · ${line.note}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Phase 6 D292 (3): money sent to an own account whose statements do not reach the payment
 * yet („Unterwegs zu …“), each with the server's note (when it was sent, until when it counts). */
function TransitNote({ lines }: { lines: NetWorthLine[] }) {
  const t = useT(netWorthText);
  const f = useFormat();
  return (
    <div className="notice notice-info">
      <p>{t.inTransit}</p>
      <ul>
        {lines.map((line, index) => (
          <li key={`transit-${line.account_id ?? ""}-${index}`}>
            {line.label} {f.money(line.value_eur)}
            {line.note ? ` · ${line.note}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The estimates in the figure, each with where it comes from (the server's note) and, for a
 * property, the Planung page where „Wert heute“ is set. */
function EstimateNote({ lines }: { lines: NetWorthLine[] }) {
  const t = useT(netWorthText);
  const f = useFormat();
  return (
    <div className="notice notice-info">
      <p>{t.estimates}</p>
      <ul>
        {lines.map((line, index) => (
          <li key={`${line.kind}-${line.property_id ?? ""}-${line.loan_id ?? ""}-${index}`}>
            {line.kind === "loan" ? t.loan : ""}
            {quoted(f.locale, line.label)} {f.money(line.value_eur)}
            {line.note ? ` · ${line.note}` : ""}
            {line.kind === "property" && line.property_id ? (
              <>
                {" · "}
                <Link href={propertyPlanningHref(new URLSearchParams({ immobilie: line.property_id }))}>{t.adjustValue}</Link>
              </>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function NetWorthTodos({ todos }: { todos: NetWorthTodo[] }) {
  const t = useT(netWorthText);
  return (
    <div className="notice notice-warn">
      <p>
        <strong>{t.todos(todos.length)}</strong>
      </p>
      <ul>
        {todos.map((todo, index) => (
          <li key={`${todo.code}-${index}`}>
            {todo.text} <Link href={todo.href}>{todo.action}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- Vermögensaufteilung ---------------------------------------------------------------------

interface Slice {
  key: string;
  label: string;
  value: string;
  detail: string;
  href: string;
}

/** A class's lines as "Giro, Tagesgeld" (account labels, each once, at most three). */
function accountNames(lines: NetWorthLine[], cls: string): string {
  const labels = [...new Set(lines.filter((l) => l.net_worth_class === cls && l.value_eur != null).map((l) => l.account_label ?? l.label))];
  return labels.length > 3 ? translate(netWorthText).andMore(labels.slice(0, 3).join(", "), labels.length - 3) : labels.join(", ");
}

/** The bar's slices, mockup 07: the property net of its loans, then the depot, the bank and
 * crypto; a credit card's debt (the class liabilities less the loans) as its own slice. */
export function slices(figure: NetWorth): Slice[] {
  const t = translate(netWorthText);
  const cls = (key: string) => figure.by_class[key] ?? "0.00";
  const cardDebt = fromCents(toCents(cls("liabilities")) + toCents(figure.loan_balances));
  const properties = [...new Set(figure.lines.map((line) => line.property_id).filter((id): id is string => id != null))];
  const all: Slice[] = [
    {
      key: "property",
      label: t.sliceProperty,
      value: figure.property_net,
      detail: propertyDetail(figure.lines),
      href: properties.length === 1 ? `/properties/${properties[0]}` : propertyPlanningHref(),
    },
    { key: "depot", label: t.sliceDepot, value: cls("depot"), detail: accountNames(figure.lines, "depot"), href: "/assets" },
    { key: "bank", label: t.sliceBank, value: cls("bank"), detail: accountNames(figure.lines, "bank"), href: "/accounts" },
    { key: "crypto", label: t.sliceCrypto, value: cls("crypto"), detail: accountNames(figure.lines, "crypto"), href: "/assets" },
    { key: "card", label: t.sliceCards, value: cardDebt, detail: t.openBalance, href: "/accounts" },
  ];
  const hasProperty = figure.lines.some((line) => line.kind === "property" || line.kind === "loan");
  return all.filter((slice) => (slice.key === "property" ? hasProperty : toCents(slice.value) !== 0n));
}

/** The property slice's detail, saying only what its valued lines hold: an estimate from the
 * handover on; before it (D265) the price paid so far and the loans' payouts, which are booked
 * figures; „teils geschätzt“ when one property is before its handover and another after it. No
 * „geschätzt“ while no line holds a value, and no payout named while no loan is valued (D265
 * amended, the build's review). */
function propertyDetail(lines: NetWorthLine[]): string {
  const t = translate(netWorthText);
  const own = lines.filter((line) => (line.kind === "property" || line.kind === "loan") && line.value_eur != null);
  const estimated = own.some((line) => line.estimate);
  const advance = own.some((line) => line.kind === "property" && !line.estimate);
  const payout = own.some((line) => line.kind === "loan" && !line.estimate);
  if (advance || payout) {
    if (estimated) return t.valueLessLoansPartly;
    if (advance && payout) return t.advanceLessPayout;
    return advance ? t.advance : t.payout;
  }
  return estimated ? t.valueLessLoansEstimated : t.valueLessLoans;
}

/** A slice's share of the positive slices, in whole percent ("68"); null for a negative one. */
function percent(value: string, positive: bigint): string | null {
  const cents = toCents(value);
  if (cents <= 0n || positive <= 0n) return null;
  return String((cents * 1000n + positive * 5n) / (positive * 10n));
}

function AllocationBar({ figure }: { figure: NetWorth }) {
  const t = useT(netWorthText);
  const f = useFormat();
  const parts = slices(figure);
  if (parts.length === 0) return null;
  const positive = parts.reduce((sum, part) => (toCents(part.value) > 0n ? sum + toCents(part.value) : sum), 0n);
  const priced = figure.lines.map((line) => line.price_on).filter((on): on is string => on != null);
  const latest = priced.length > 0 ? priced.reduce((a, b) => (a > b ? a : b)) : null;
  const shown = parts.filter((part) => percent(part.value, positive) !== null);
  return (
    <section className="card stack" aria-labelledby="allocation-title">
      <div className="net-worth-head">
        <h2 id="allocation-title">{t.allocation}</h2>
        <span className="muted">
          {t.propertyNetOfLoans}
          {latest ? t.pricesOf(f.date(latest)) : ""}
          {figure.stale_positions > 0 ? t.stale(figure.stale_positions) : ""}
        </span>
      </div>
      {positive > 0n ? (
        <div
          className="net-worth-bar"
          role="img"
          aria-label={shown.map((part) => `${part.label} ${t.share(percent(part.value, positive) ?? "")}`).join(", ")}
        >
          {shown.map((part) => (
            <div
              key={part.key}
              className={`net-worth-segment net-worth-${part.key}`}
              style={{ width: `${Number((toCents(part.value) * 10000n) / positive) / 100}%` }}
            />
          ))}
        </div>
      ) : null}
      <div className="net-worth-items">
        {parts.map((part) => {
          const share = percent(part.value, positive);
          return (
            <Link key={part.key} href={part.href} className="net-worth-item">
              <span className={`net-worth-swatch net-worth-${part.key}`} aria-hidden="true" />
              <span>
                <span className="net-worth-item-label">{part.label}</span>
                <span className="net-worth-item-value">{f.money(part.value)}</span>
                <span className="net-worth-item-detail">{[share !== null ? t.share(share) : null, part.detail || null].filter(Boolean).join(" · ")}</span>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

/** An account's or property's own name in the language's quotation marks: „…“ / “…”. */
function quoted(locale: "de" | "en", label: string): string {
  return locale === "de" ? `„${label}“` : `“${label}”`;
}
