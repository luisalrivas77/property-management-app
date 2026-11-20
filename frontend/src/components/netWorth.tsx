"use client";

// The household's net worth on the Übersicht (phase 6 §13, D167 (4), (5), mockup 07): the card
// „Nettovermögen“ (today on read against the latest stored month-end, with the button
// „Monatsabschluss festhalten“) and the bar „Vermögensaufteilung“ by class, the property net of
// its loans. Every euro figure is the server's (GET /net-worth/overview); the change since the
// month-end and the bar's percentages are display arithmetic in integer cents (BigInt).
// Estimates (the property's value, a loan's balance by its schedule) are labelled as such, and
// every gap the server names is a to-do with its button or link (principle 7).
import Link from "next/link";
import { useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { fromCents, signedMoney, toCents } from "./cashflow";
import { formatDate, formatMoney } from "./format";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

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

/** The Übersicht's two net-worth sections; one request for both. */
export function NetWorthSection() {
  const state = useLoad(loadNetWorth);
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

const OUTCOME_TEXT: Record<SnapshotResult["outcome"], (asOf: string) => string> = {
  written: (asOf) => `Monatsabschluss zum ${formatDate(asOf)} festgehalten.`,
  recaptured: (asOf) => `Monatsabschluss zum ${formatDate(asOf)} neu festgehalten: die Zahlen hatten sich seitdem geändert.`,
  unchanged: (asOf) => `Der Monatsabschluss zum ${formatDate(asOf)} ist schon mit genau diesen Zahlen festgehalten.`,
  empty: () => "Nichts festzuhalten: noch kein Konto, keine Anlage und keine Immobilie mit einem Wert.",
};

/** The capture's sentence on what the stored figure leaves out: a position without a price, or a
 * fund the depot report lists with nothing booked (the to-dos below say what to do). */
function unvaluedText(count: number | undefined): string {
  if (!count) return "";
  return count === 1
    ? " 1 Position ohne Wert ist darin nicht enthalten (siehe offene Punkte)."
    : ` ${count} Positionen ohne Wert sind darin nicht enthalten (siehe offene Punkte).`;
}

function NetWorthCard({ overview, owners, onChanged }: { overview: NetWorthOverview; owners: Owner[]; onChanged: () => void }) {
  const { today, month_end: monthEnd, month_end_due: due, month_end_now: drifted } = overview;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<SnapshotResult | null>(null);

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
  const older = today.lines.filter((line) => line.kind === "account" && line.as_of != null && line.as_of < today.on);
  const names = new Map(owners.map((owner) => [owner.id, owner.full_name]));
  const byOwner = Object.entries(today.by_owner);
  return (
    <section className="card stack" aria-labelledby="net-worth-card-title">
      <h2 id="net-worth-card-title">Nettovermögen</h2>
      {today.lines.length === 0 ? (
        <p className="muted">
          Noch kein Konto, keine Anlage und keine Immobilie mit einem Wert. Konten werden unter <Link href="/accounts">Konten</Link>{" "}
          angelegt und importiert.
        </p>
      ) : (
        <>
          <p className="cashflow-value">{formatMoney(today.total)}</p>
          <p className="muted">
            Heute, {formatDate(today.on)}
            {monthEnd ? (
              <>
                {" · "}
                <span className={toCents(today.total) >= toCents(monthEnd.total) ? "net-worth-change" : "net-worth-change-down"}>
                  {signedMoney(fromCents(toCents(today.total) - toCents(monthEnd.total)))} gegenüber dem Monatsabschluss vom{" "}
                  {formatDate(monthEnd.on)}
                </span>{" "}
                ({formatMoney(monthEnd.total)})
              </>
            ) : (
              " · noch kein Monatsabschluss festgehalten"
            )}
          </p>
          {byOwner.length > 0 ? (
            <p className="muted">
              {byOwner.map(([id, value]) => `${names.get(id) ?? "Eigentümer"} ${formatMoney(value)}`).join(" · ")}
              {today.unassigned.length > 0 ? " · ohne Eigentümer: siehe unten" : ""}
            </p>
          ) : null}
          {older.length > 0 ? <BalanceDates lines={older} /> : null}
          {paid.length > 0 ? <PaidNote lines={paid} /> : null}
          {estimates.length > 0 ? <EstimateNote lines={estimates} /> : null}
        </>
      )}
      {due ? (
        <p>
          Der Monatsabschluss zum {formatDate(due)} ist noch nicht festgehalten. Hausbuch speichert ihn am Monatsersten automatisch,
          nach dem morgendlichen Abruf der Kontoumsätze und Kurse.
        </p>
      ) : null}
      {monthEnd && drifted ? (
        <div className="notice notice-warn">
          <p>
            Das gespeicherte Monatsende weicht vom heutigen Stand ab: {formatMoney(monthEnd.total)} statt {formatMoney(drifted)}. Seit dem
            Speichern ist für den {formatDate(monthEnd.on)} noch etwas dazugekommen (ein Kontoauszug, ein Kurs oder ein geänderter
            Schätzwert).
          </p>
        </div>
      ) : null}
      <div className="row">
        <button type="button" className={due || drifted ? undefined : "secondary"} onClick={() => void capture()} disabled={busy}>
          {busy ? "Hält fest …" : drifted ? "Monatsabschluss neu speichern" : "Monatsabschluss festhalten"}
        </button>
      </div>
      {result ? (
        <p className="muted">
          {OUTCOME_TEXT[result.outcome](result.as_of)}
          {unvaluedText(result.unvalued_positions)}
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
  return (
    <p className="muted">
      {lines
        .map((line) => `„${line.label}“ ${formatMoney(line.value_eur)} · Stand vom ${formatDate(line.as_of)}`)
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
  return (
    <div className="notice notice-info">
      <p>Vor der Übergabe der Wohnung:</p>
      <ul>
        {lines.map((line, index) => (
          <li key={`${line.kind}-${line.property_id ?? ""}-${line.loan_id ?? ""}-${index}`}>
            {line.kind === "loan" ? "Darlehen " : ""}„{line.label}“ {formatMoney(line.value_eur)}
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
  return (
    <div className="notice notice-info">
      <p>Enthält Schätzwerte:</p>
      <ul>
        {lines.map((line, index) => (
          <li key={`${line.kind}-${line.property_id ?? ""}-${line.loan_id ?? ""}-${index}`}>
            {line.kind === "loan" ? "Darlehen " : ""}„{line.label}“ {formatMoney(line.value_eur)}
            {line.note ? ` · ${line.note}` : ""}
            {line.kind === "property" && line.property_id ? (
              <>
                {" · "}
                <Link href={`/planning?immobilie=${line.property_id}`}>Wert in der Planung anpassen</Link>
              </>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function NetWorthTodos({ todos }: { todos: NetWorthTodo[] }) {
  return (
    <div className="notice notice-warn">
      <p>
        <strong>{todos.length === 1 ? "1 offener Punkt" : `${todos.length} offene Punkte`} im Nettovermögen</strong>
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
  return labels.length > 3 ? `${labels.slice(0, 3).join(", ")} und ${labels.length - 3} weitere` : labels.join(", ");
}

/** The bar's slices, mockup 07: the property net of its loans, then the depot, the bank and
 * crypto; a credit card's debt (the class liabilities less the loans) as its own slice. */
export function slices(figure: NetWorth): Slice[] {
  const cls = (key: string) => figure.by_class[key] ?? "0.00";
  const cardDebt = fromCents(toCents(cls("liabilities")) + toCents(figure.loan_balances));
  const properties = [...new Set(figure.lines.map((line) => line.property_id).filter((id): id is string => id != null))];
  const all: Slice[] = [
    {
      key: "property",
      label: "Immobilie (netto)",
      value: figure.property_net,
      detail: propertyDetail(figure.lines),
      href: properties.length === 1 ? `/properties/${properties[0]}` : "/planning",
    },
    { key: "depot", label: "Depot", value: cls("depot"), detail: accountNames(figure.lines, "depot"), href: "/assets" },
    { key: "bank", label: "Bank", value: cls("bank"), detail: accountNames(figure.lines, "bank"), href: "/accounts" },
    { key: "crypto", label: "Krypto", value: cls("crypto"), detail: accountNames(figure.lines, "crypto"), href: "/assets" },
    { key: "card", label: "Kreditkarten", value: cardDebt, detail: "offener Saldo", href: "/accounts" },
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
  const own = lines.filter((line) => (line.kind === "property" || line.kind === "loan") && line.value_eur != null);
  const estimated = own.some((line) => line.estimate);
  const advance = own.some((line) => line.kind === "property" && !line.estimate);
  const payout = own.some((line) => line.kind === "loan" && !line.estimate);
  if (advance || payout) {
    if (estimated) return "Wert minus Darlehen, teils geschätzt";
    if (advance && payout) return "Anzahlung minus ausgezahltes Darlehen";
    return advance ? "Anzahlung Kaufpreis" : "ausgezahltes Darlehen";
  }
  return estimated ? "Wert minus Darlehen, geschätzt" : "Wert minus Darlehen";
}

/** A slice's share of the positive slices, in whole percent ("68"); null for a negative one. */
function percent(value: string, positive: bigint): string | null {
  const cents = toCents(value);
  if (cents <= 0n || positive <= 0n) return null;
  return String((cents * 1000n + positive * 5n) / (positive * 10n));
}

function AllocationBar({ figure }: { figure: NetWorth }) {
  const parts = slices(figure);
  if (parts.length === 0) return null;
  const positive = parts.reduce((sum, part) => (toCents(part.value) > 0n ? sum + toCents(part.value) : sum), 0n);
  const priced = figure.lines.map((line) => line.price_on).filter((on): on is string => on != null);
  const latest = priced.length > 0 ? priced.reduce((a, b) => (a > b ? a : b)) : null;
  const shown = parts.filter((part) => percent(part.value, positive) !== null);
  return (
    <section className="card stack" aria-labelledby="allocation-title">
      <div className="net-worth-head">
        <h2 id="allocation-title">Vermögensaufteilung</h2>
        <span className="muted">
          Immobilie abzüglich Darlehen
          {latest ? ` · Kurse vom ${formatDate(latest)}` : ""}
          {figure.stale_positions > 0 ? ` · ${figure.stale_positions} Kurs${figure.stale_positions === 1 ? "" : "e"} veraltet` : ""}
        </span>
      </div>
      {positive > 0n ? (
        <div
          className="net-worth-bar"
          role="img"
          aria-label={shown.map((part) => `${part.label} ${percent(part.value, positive)} %`).join(", ")}
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
                <span className="net-worth-item-value">{formatMoney(part.value)}</span>
                <span className="net-worth-item-detail">{[share !== null ? `${share} %` : null, part.detail || null].filter(Boolean).join(" · ")}</span>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
