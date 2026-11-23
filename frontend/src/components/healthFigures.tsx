"use client";

// The household's key figures (phase 9 §14, D235–D243, D250; docs/mockups/13-overview-figures.png
// before the limits are confirmed, 13b after), in the sections they are about since phase 10
// §7.6 (Luis, 23 Nov 2026: „They should be moved to their respective sections as Planung will
// be removed“; until then one block on Übersicht):
// - Haushalt (HouseholdHealthSection): the block „Kennzahlen“ (`#figures-title`) with the three
//   figures of Finanzielle Gesundheit (phase 10 §7.6, Luis 23 Nov 2026: Sparquote,
//   Liquiditätsreserve, Schuldendienstquote) as cards, each with its dot against the limits used
//   and what it is made of; the Beleihungsauslauf, Verschuldungsgrad and Schulden zu
//   Jahreseinkommen are no longer shown („Any figure that does not help convey financial health is
//   not needed“), the property page keeps its own LTV. „Grenzen ändern“ (or `#ampel-grenzen`)
//   opens the sheet „Ampel-Grenzen festlegen“: an empty field is the standard limit, a typed one
//   the household's, „Übernehmen“ stores them in a new version of the default scenario.
// - Anlagen › Übersicht (AssetsHealthSection): the card a month-end to-do opens
//   (`/assets?month_end=…`, „Monatsabschluss festhalten“), the split of the assets (never marked)
//   and „Vermögen im Verlauf“.
// Every figure is the server's (GET /health, whose `series` is GET /health/net-worth-series's
// stored runs); a figure's state, limits and sentences are its `verdict` (rules/verdict), on the
// standard limits until the household sets its own (this supersedes phase 9 D250 (3)'s „nothing
// coloured before „Übernehmen““ on these screens). Every figure opens what it is made of, and
// every gap is a to-do with its button (principle 7); a month-end to-do is the button
// „Monatsabschluss festhalten“ itself.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ApiError, ruleCodes } from "@/api/errors";
import { type Translation, useT } from "@/i18n";
import { common } from "@/i18n/common";
import { healthFigures } from "@/i18n/healthFigures";
import { netWorthText } from "@/i18n/netWorth";
import { verdictText } from "@/i18n/verdict";
import { ErrorNotice } from "@/lib/ErrorNotice";

import {
  type BandLimits,
  type BandProposal,
  bandsConfirmedBy,
  currentDefault,
  FIGURE_LABEL,
  formatOneDecimal,
  type Health,
  type HealthTodo,
  HIGHER_IS_BETTER,
  type HouseholdScenario,
  leversOf,
  loadMemberNames,
  loadScenarioHistory,
  type MarkedFigure,
  monthEnd,
  monthEndOf,
  type NetWorthPoint,
  saveDefaultScenario,
  toSignedRate,
} from "./healthShared";
import { Field } from "./form";
import { percentInput } from "./input";
import { snapshotOutcomeText } from "./netWorth";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";
import { VerdictCards, type VerdictPart, VerdictWordLine } from "./verdict";

import "./health.css";

type Account = components["schemas"]["Account"];
type SnapshotResult = components["schemas"]["SnapshotResult"];
type HealthText = Translation<typeof healthFigures.de>;

/** One decimal as phase 9 shows it: German exactly as formatOneDecimal („3,0“), English with a
 * trailing „.0“ dropped. */
function oneDecimal(p: PlanFormat, value: string | null | undefined): string {
  return p.locale === "de" ? formatOneDecimal(value) : p.short(value);
}

interface HealthData {
  health: Health;
  scenarios: HouseholdScenario[];
  names: Map<string, string>;
  accounts: Map<string, Account>;
}

/** Haushalt's reads: GET /health (sent once with the page's own read of it, fetchShared), the
 * scenarios, the members and the accounts. */
async function loadHouseholdHealth(): Promise<HealthData> {
  const [health, scenarios, names, accounts] = await Promise.all([
    ok(api.GET("/api/health")),
    loadScenarioHistory(),
    loadMemberNames(),
    ok(api.GET("/api/accounts")),
  ]);
  return { health, scenarios, names, accounts: new Map(accounts.map((account) => [account.id, account])) };
}

const loadHealth = (): Promise<Health> => ok(api.GET("/api/health"));

/** A link from another page to a card here (`/household#ampel-grenzen`, `#figures-title`)
 * arrives before the card has loaded, so the browser finds nothing to scroll to; once it is
 * there, scroll to it. A hash of the detail panel (`#d-…`) is not one of ours. */
function useScrollToHash(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (!id || id.startsWith("d-")) return;
    document.getElementById(id)?.scrollIntoView();
  }, [ready]);
}

/** Haushalt's key figures (phase 10 §7.6): the reserve, the limits and the Kennzahlen. A change
 * of `refresh` reads them again in place (phase 10 §7.6 (f): a month-end captured from Haushalt's
 * „Noch keine Durchschnitte“ above moves the reserve and the Kennzahlen without a reload). */
export function HouseholdHealthSection({ onChanged, refresh = 0 }: { onChanged?: () => void; refresh?: number } = {}) {
  const state = useLoad(loadHouseholdHealth);
  useScrollToHash(state.data !== undefined);
  const { reload } = state;
  const seen = useRef(refresh);
  useEffect(() => {
    if (seen.current === refresh) return;
    seen.current = refresh;
    reload();
  }, [refresh, reload]);
  const changed = () => {
    state.reload();
    onChanged?.();
  };
  return <Loading state={state}>{(data) => <HealthFigures data={data} onChanged={changed} />}</Loading>;
}

/** Anlagen › Übersicht's part (phase 10 §7.6): the card a month-end to-do opens
 * (`?month_end=YYYY-MM-DD`) above the tab's own content (`children`), and the split of the assets
 * and „Vermögen im Verlauf“ below it. Inside a Suspense boundary (useSearchParams). */
export function AssetsHealthSection({ children }: { children?: ReactNode }) {
  const health = useLoad(loadHealth);
  const requested = useSearchParams().get("month_end");
  return (
    <div className="stack">
      {requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? <MonthEndCard day={requested} onDone={health.reload} /> : null}
      {children}
      <Loading state={health}>
        {(data) => (
          <>
            <SplitCard health={data} />
            <SeriesCard series={data.series ?? []} onChanged={health.reload} />
          </>
        )}
      </Loading>
    </div>
  );
}

/** The three figures of Finanzielle Gesundheit (rules/verdict), in the server's order. The sheet
 * edits only these; a band the household stored for any other figure is kept as it is. */
const VERDICT_FIGURES: MarkedFigure[] = ["savings_rate", "reserve", "debt_service_ratio"];

function HealthFigures({ data, onChanged }: { data: HealthData; onChanged: () => void }) {
  const { health, scenarios } = data;
  // `/household#ampel-grenzen` (Übersicht's „Grenzen ändern“) opens the sheet. This part mounts
  // only after its read, so the sheet is there when the section scrolls to the hash.
  const [editing, setEditing] = useState(() => typeof window !== "undefined" && window.location.hash === "#ampel-grenzen");
  useEffect(() => {
    const open = () => {
      if (window.location.hash === "#ampel-grenzen") setEditing(true);
    };
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);
  // Closing drops the hash too, so a reload or a second link to it opens the sheet again.
  const close = () => {
    setEditing(false);
    if (window.location.hash === "#ampel-grenzen") {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
  };
  const todos = health.todos ?? [];
  const current = currentDefault(scenarios);
  // Below three closed months Haushalt's „Noch keine Durchschnitte“ above shows „too few closed
  // months“ and what holds the next month open (its month-end to-do included) with their buttons,
  // so they are not listed here a second time (phase 10 §7.6 (f)). The limits' own to-do
  // (bands_unconfirmed, still sent by rules/health/bands) is not shown: the standard limits apply
  // until the household sets its own, so nothing waits for it (phase 10 §7.6, Finanzielle
  // Gesundheit).
  const otherTodos = health.window.enough ? todos.filter((todo) => todo.code !== "bands_unconfirmed" && todo.code !== "too_few_months") : [];
  return (
    <>
      <FiguresCard data={data} current={current} todos={otherTodos} onChanged={onChanged} onEditBands={() => setEditing(true)} />
      {editing ? (
        <BandsCard
          health={health}
          current={current}
          onSaved={() => {
            close();
            onChanged();
          }}
          onCancel={close}
        />
      ) : null}
    </>
  );
}

// --- to-dos -----------------------------------------------------------------------------

/** One to-do with its button or link; a month-end to-do captures the month-end at once. */
export function HealthTodoItem({ todo, onChanged }: { todo: HealthTodo; onChanged: () => void }) {
  const day = monthEndOf(todo.href);
  return (
    <li className="health-todo">
      <span>{todo.text}</span>
      {day !== null ? (
        <MonthEndButton day={day} label={todo.action} onDone={onChanged} />
      ) : todo.href === "/" ? (
        // The limits' to-do (rules/health/bands, whose href stays "/" for rule_version()): the
        // card „Ampel-Grenzen festlegen“ is on Haushalt since phase 10 §7.6.
        <Link href="/household#ampel-grenzen" className="button-link secondary">
          {todo.action}
        </Link>
      ) : (
        <Link href={todo.href} className="button-link secondary">
          {todo.action}
        </Link>
      )}
    </li>
  );
}

export function HealthTodoList({ todos, onChanged }: { todos: HealthTodo[]; onChanged: () => void }) {
  if (todos.length === 0) return null;
  return (
    <ul className="health-todos">
      {todos.map((todo, index) => (
        <HealthTodoItem key={`${todo.code}-${index}`} todo={todo} onChanged={onChanged} />
      ))}
    </ul>
  );
}

/** „Monatsabschluss festhalten“ for a past month-end (POST /net-worth/snapshots {as_of}, D241). */
export function MonthEndButton({ day, label, onDone }: { day: string; label: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<SnapshotResult | null>(null);
  const tn = useT(netWorthText);
  const p = usePlanFormat();
  async function capture() {
    setBusy(true);
    setError(null);
    try {
      setResult(await ok(api.POST("/api/net-worth/snapshots", { body: { as_of: day } })));
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="health-month-end">
      <button type="button" className="secondary" disabled={busy} onClick={() => void capture()}>
        {label}
      </button>
      {result ? <span role="status">{snapshotOutcomeText(tn, result.outcome, p.date(result.as_of))}</span> : null}
      {error != null ? <ErrorNotice error={error} /> : null}
    </span>
  );
}

/** The card a month-end to-do opens (`/assets?month_end=YYYY-MM-DD`; Übersicht forwards the old
 * `/?month_end=` there). */
function MonthEndCard({ day, onDone }: { day: string; onDone: () => void }) {
  const t = useT(healthFigures);
  const tn = useT(netWorthText);
  const p = usePlanFormat();
  return (
    <section className="card stack notice-info" aria-labelledby="month-end-title">
      <h2 id="month-end-title">{t.monthEndTitle(p.longDate(day))}</h2>
      <p>{t.monthEndMissing}</p>
      <p>
        <MonthEndButton day={day} label={tn.captureMonthEnd} onDone={onDone} />
      </p>
    </section>
  );
}

// --- Finanzielle Gesundheit's three figures ------------------------------------------------

function reserveOrigin(t: HealthText, origin: components["schemas"]["ReserveOrigin"]): string {
  switch (origin) {
    case "default":
      return t.originDefault;
    case "added":
      return t.originAdded;
    case "removed":
      return t.originRemoved;
  }
}

/** What one verdict card is made of, under the server's sentences: the savings rate's rows and
 * months, the reserve's target and accounts, the loans' interest and repayment. */
function FigureDetails({ part, data, onChanged }: { part: VerdictPart; data: HealthData; onChanged: () => void }) {
  const { health } = data;
  const t = useT(healthFigures);
  const p = usePlanFormat();
  const range = p.monthRange(health.window.months ?? []);
  switch (part.figure) {
    case "savings_rate": {
      const rate = health.savings_rate;
      if (!rate) return null;
      return (
        <>
          <ul className="household-rows">
            <li>
              <span>{t.withoutOneOffs}</span>
              <span>{p.ratio(rate.rate_regular)}</span>
            </li>
            <li>
              <span>{t.ofWhichPrincipal}</span>
              <span>{p.euro(rate.principal)}</span>
            </li>
            <li>
              <span>{t.withoutPrincipal}</span>
              <span>{p.ratio(rate.rate_without_principal)}</span>
            </li>
          </ul>
          <p className="muted">{t.savingsExplained(range)}</p>
          <details className="health-sources">
            <summary>{t.viewMonths}</summary>
            <ul className="household-rows">
              {(rate.months ?? []).map((row) => (
                <li key={row.month}>
                  <Link href={`/household?monat=${row.month}`}>{p.month(row.month)}</Link>
                  <span>{p.ratio(row.rate)}</span>
                </li>
              ))}
            </ul>
            <p className="muted">{t.savedOf(p.euro(rate.saved), p.euro(rate.income), p.euro(rate.one_off_spending))}</p>
          </details>
        </>
      );
    }
    case "reserve": {
      const reserve = health.reserve;
      if (!reserve) return <p className="muted">{t.reserveLater}</p>;
      return (
        <>
          <p className="muted">
            {reserve.months != null ? t.reserveMonths(oneDecimal(p, reserve.months), p.euro(reserve.need)) : t.reserveNoNeed}
          </p>
          <p className="muted">
            {t.ownTarget(p.euro(reserve.target), p.short(reserve.target_months))}{" "}
            <Link href="/planning?tab=haushalt">{t.change}</Link>
          </p>
          <details className="health-sources">
            <summary>{t.reserveSources}</summary>
            <ul className="household-rows">
              {(reserve.accounts ?? []).map((row) => (
                <li key={row.account_id}>
                  <span>
                    <Link href={`/accounts/${row.account_id}`}>{data.accounts.get(row.account_id)?.label ?? t.account}</Link>{" "}
                    <span className="muted">{t.originAsOf(reserveOrigin(t, row.origin), p.date(row.as_of))}</span>
                  </span>
                  <span>{p.euro(row.balance)}</span>
                </li>
              ))}
            </ul>
            <p className="muted">
              {t.needExplained(range)} <Link href="/planning?tab=haushalt">{t.changeReserveAccounts}</Link>
            </p>
          </details>
          <HealthTodoList todos={reserve.todos ?? []} onChanged={onChanged} />
        </>
      );
    }
    case "debt_service_ratio": {
      const debt = health.debt;
      if (!debt || part.no_loan) return null;
      const annualised = debt.months < 12 ? t.annualised(debt.months) : "";
      const gap = debt.gaps?.find((g) => g.figure === "debt_service_ratio")?.reason ?? null;
      return (
        <>
          <p className="muted">{t.debtServiceExplained(annualised, p.euro(debt.debt_service_year), p.euro(debt.income_year))}</p>
          {gap ? <p className="muted">{gap}</p> : null}
          <details className="health-sources">
            <summary>{t.madeOf}</summary>
            <ul className="household-rows">
              <li>
                <span>{t.interestIn(range)}</span>
                <span>{p.euro(debt.interest)}</span>
              </li>
              <li>
                <span>{t.principalIn(range)}</span>
                <span>{p.euro(debt.principal)}</span>
              </li>
            </ul>
            <p className="muted">
              {t.fromRentalBefore}
              <Link href="/planning">{t.planningProperty}</Link>
              {t.fromRentalAfter}
            </p>
          </details>
        </>
      );
    }
    default:
      return null;
  }
}

// --- Ampel-Grenzen festlegen ---------------------------------------------------------------

interface LimitDraft {
  green: string;
  red: string;
}

/** A limit from the API as typed text: a ratio in percent ("0.3" → "30,0"), the reserve's red as
 * a percent of the target. */
function limitInput(value: string | null | undefined): string {
  if (value == null) return "";
  const percent = percentInput(value);
  return percent.includes(",") ? percent : `${percent},0`;
}

function parseLimit(raw: string): { ok: true; value: string | null } | { ok: false; message: string } {
  if (raw.trim() === "") return { ok: true, value: null };
  return toSignedRate(raw, 4);
}

/** The sheet starts from the sides the household stored; an empty side is the standard limit
 * (rules/verdict), so saving the sheet unchanged stores nothing new. */
function draftsFrom(bands: BandLimits[]): Record<MarkedFigure, LimitDraft> {
  const out = {} as Record<MarkedFigure, LimitDraft>;
  for (const figure of VERDICT_FIGURES) {
    const band = bands.find((b) => b.figure === figure);
    out[figure] = { green: limitInput(band?.green), red: limitInput(band?.red) };
  }
  return out;
}

/** The standard limit of one side, shown beside an empty field: the verdict's own limit while
 * that side is the standard one, else the proposal of rules/health/bands. */
function standardLimit(
  figure: MarkedFigure,
  side: keyof LimitDraft,
  part: VerdictPart | undefined,
  proposals: BandProposal[],
): string | null {
  if (part) {
    const value = side === "green" ? part.good : part.strained;
    const origin = side === "green" ? part.good_origin : part.strained_origin;
    if (value != null && origin === "default") return value;
  }
  const proposal = proposals.find((p) => p.figure === figure);
  return (side === "green" ? proposal?.green?.value : proposal?.red?.value) ?? null;
}

function figureIntro(t: HealthText, figure: MarkedFigure): string {
  switch (figure) {
    case "savings_rate":
      return t.introSavingsRate;
    case "debt_service_ratio":
      return t.introDebtService;
    default:
      return "";
  }
}

/** A refusal of the default scenario the limits are saved in (`household_scenario.*`, PR #434
 * review D): the fix is on Planung › Haushalt, where the scenario's other levers are. */
function scenarioRefused(error: unknown): boolean {
  return error instanceof ApiError && error.body !== null && ruleCodes(error.body).some((code) => code.startsWith("household_scenario."));
}

function BandsCard({
  health,
  current,
  onSaved,
  onCancel,
}: {
  health: Health;
  current: HouseholdScenario | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const ids = useId();
  const stored = current?.levers?.bands ?? [];
  const [drafts, setDrafts] = useState(() => draftsFrom(stored));
  const [messages, setMessages] = useState<Partial<Record<string, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const card = useRef<HTMLElement>(null);
  const proposals = health.proposals ?? [];
  const parts = health.verdict?.parts ?? [];
  const target = health.reserve?.target ?? null;
  const targetMonths = current?.levers?.reserve_target_months ?? health.reserve?.target_months ?? "6";
  const t = useT(healthFigures);
  const tv = useT(verdictText);
  const c = useT(common);
  const p = usePlanFormat();

  // Opened by „Grenzen ändern“ below the cards: bring it into view.
  useEffect(() => {
    card.current?.scrollIntoView({ block: "start" });
  }, []);

  function set(figure: MarkedFigure, side: keyof LimitDraft, value: string) {
    setDrafts((previous) => ({ ...previous, [figure]: { ...previous[figure], [side]: value } }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found: Partial<Record<string, string>> = {};
    const bands: BandLimits[] = [];
    for (const figure of VERDICT_FIGURES) {
      const draft = drafts[figure];
      const green = figure === "reserve" ? ({ ok: true, value: null } as const) : parseLimit(draft.green);
      const red = parseLimit(draft.red);
      if (!green.ok) found[`${figure}.green`] = green.message;
      if (!red.ok) found[`${figure}.red`] = red.message;
      if (green.ok && red.ok && (green.value !== null || red.value !== null)) {
        bands.push({ figure, green: green.value, red: red.value });
      }
    }
    setMessages(found);
    if (Object.keys(found).length > 0) return;
    // A figure the screens no longer show (LTV, Verschuldungsgrad, Schulden zu Jahreseinkommen)
    // keeps the band the household stored for it, untouched.
    const kept = stored.filter((band) => !VERDICT_FIGURES.includes(band.figure));
    setBusy(true);
    setError(null);
    try {
      await saveDefaultScenario({ ...leversOf(current), bands: [...bands, ...kept] }, current);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  function hint(figure: MarkedFigure, side: keyof LimitDraft, unit: string): string | undefined {
    if (drafts[figure][side].trim() !== "") return undefined;
    const standard = standardLimit(figure, side, parts.find((part) => part.figure === figure), proposals);
    return t.emptyStandard(standard != null ? `${limitInput(standard)} ${unit}` : null);
  }

  function field(figure: MarkedFigure, side: keyof LimitDraft, label: string, unit: string, extra?: string) {
    const message = messages[`${figure}.${side}`];
    const shown = [hint(figure, side, unit), extra].filter((text): text is string => text !== undefined).join(" · ");
    return (
      <Field id={`${ids}-${figure}-${side}`} label={t.fieldLabel(label, unit)} hint={shown || undefined} errors={message ? [message] : []}>
        <input
          type="text"
          inputMode="decimal"
          className="health-limit-input"
          value={drafts[figure][side]}
          onChange={(event) => set(figure, side, event.target.value)}
        />
      </Field>
    );
  }

  const reserveRed = parseLimit(drafts.reserve.red);
  const reserveRedEuro = target && reserveRed.ok && reserveRed.value !== null ? `= ${p.euro(multiply(target, reserveRed.value))}` : undefined;

  return (
    <section className="card stack" id="ampel-grenzen" aria-labelledby={`${ids}-title`} ref={card}>
      <h2 id={`${ids}-title`}>{t.bandsSet}</h2>
      <p>{t.bandsIntro}</p>
      <form className="stack" onSubmit={(event) => void submit(event)} aria-label={t.bandsForm}>
        {VERDICT_FIGURES.map((figure) => {
          const draft = drafts[figure];
          const own = draft.green.trim() !== "" || draft.red.trim() !== "";
          const part = parts.find((row) => row.figure === figure);
          return (
            <fieldset key={figure} className="health-limit">
              <legend>{FIGURE_LABEL[figure]}</legend>
              {figure === "reserve" ? (
                <p className="muted">
                  {t.reserveGreenBefore(p.short(targetMonths), target ? p.euro(target) : null)}
                  <Link href="/planning?tab=haushalt">{t.planning}</Link>
                  {t.reserveGreenAfter}
                </p>
              ) : (
                <p className="muted">{figureIntro(t, figure)}</p>
              )}
              <div className="health-limit-fields">
                {figure === "reserve" ? (
                  field("reserve", "red", t.redBelowLabel, t.unitOfTarget, reserveRedEuro)
                ) : HIGHER_IS_BETTER[figure] ? (
                  <>
                    {field(figure, "red", t.redBelowLabel, t.unitPercent)}
                    {field(figure, "green", t.greenFromLabel, t.unitPercent)}
                  </>
                ) : (
                  <>
                    {field(figure, "green", t.greenUpToLabel, t.unitPercent)}
                    {field(figure, "red", t.redAboveLabel, t.unitPercent)}
                  </>
                )}
              </div>
              <p>
                <span className="badge badge-muted">{own ? tv.yourLimit : tv.standardLimit}</span>
              </p>
              {part ? (
                <div className="health-reference">
                  <p className="health-reference-title">{t.reference}</p>
                  <p className="muted">{part.reference}</p>
                </div>
              ) : null}
            </fieldset>
          );
        })}
        {error != null ? <ErrorNotice error={error} /> : null}
        {scenarioRefused(error) ? (
          <p className="muted" role="note">
            {t.scenarioRefusedBefore}
            <Link href="/planning?tab=haushalt">{t.planningHousehold}</Link>
            {t.scenarioRefusedAfter}
          </p>
        ) : null}
        <div className="row">
          <button type="submit" disabled={busy}>
            {c.apply}
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
            {c.cancel}
          </button>
          <span className="muted">{t.savedIn}</span>
        </div>
      </form>
    </section>
  );
}

/** a × b for two decimal strings, exact (the reserve's red limit in euros, for display). */
function multiply(a: string, b: string): string {
  const parse = (value: string): [bigint, number] => {
    const negative = value.startsWith("-");
    const [int = "0", frac = ""] = value.replace(/^-/, "").split(".");
    const digits = BigInt(int + frac);
    return [negative ? -digits : digits, frac.length];
  };
  const [x, xs] = parse(a);
  const [y, ys] = parse(b);
  const product = x * y;
  const scale = xs + ys;
  const negative = product < 0n;
  const text = (negative ? -product : product).toString().padStart(scale + 1, "0");
  const whole = scale > 0 ? text.slice(0, -scale) : text;
  const fraction = scale > 0 ? text.slice(-scale) : "";
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

// --- Kennzahlen and the split of the assets -----------------------------------------------

function splitLabel(t: HealthText, key: string): string {
  switch (key) {
    case "property":
      return t.splitProperty;
    case "depot":
      return t.splitDepot;
    case "bank":
      return t.splitBank;
    case "crypto":
      return t.splitCrypto;
    default:
      return key;
  }
}

/** „Kennzahlen“ (`#figures-title`): the verdict's word and its three figures as cards, each with
 * its dot against the limits used, and what it is made of. */
function FiguresCard({
  data,
  current,
  todos,
  onChanged,
  onEditBands,
}: {
  data: HealthData;
  current: HouseholdScenario | null;
  todos: HealthTodo[];
  onChanged: () => void;
  onEditBands: () => void;
}) {
  const { health } = data;
  const t = useT(healthFigures);
  const p = usePlanFormat();
  const months = health.window.months ?? [];
  const range = p.monthRange(months);
  const newest = months[months.length - 1];
  const own = (current?.levers?.bands ?? []).some((band) => VERDICT_FIGURES.includes(band.figure));
  const by = current && own ? bandsConfirmedBy(data.scenarios, current) : null;
  const verdict = health.verdict ?? null;

  return (
    <section className="card stack" aria-labelledby="figures-title">
      <div className="planning-card-head">
        <h2 id="figures-title">{t.figuresTitle}</h2>
        <span className="spacer" />
        <button type="button" className="link" onClick={onEditBands}>
          {t.changeLimits}
        </button>
      </div>
      <p className="muted">
        {range ? `${range} · ` : ""}
        {newest ? t.holdingsOn(p.longDate(monthEnd(newest))) : ""}
        {own
          ? `${t.coloursOwn}${
              by ? t.appliedBy(by.created_by ? (data.names.get(by.created_by) ?? t.aMember) : null, p.longDate(by.created_at)) : ""
            }`
          : t.coloursStandard}
      </p>
      {verdict ? <VerdictWordLine verdict={verdict} /> : null}
      <HealthTodoList todos={todos} onChanged={onChanged} />
      {verdict ? (
        <VerdictCards parts={verdict.parts ?? []}>{(part) => <FigureDetails part={part} data={data} onChanged={onChanged} />}</VerdictCards>
      ) : (
        <p className="muted">–</p>
      )}
    </section>
  );
}

/** The split of the assets on the newest closed month-end: never marked (D240). */
function SplitCard({ health }: { health: Health }) {
  const t = useT(healthFigures);
  const p = usePlanFormat();
  const split = Object.entries(health.split ?? {}).sort(([, a], [, b]) => Number(b) - Number(a));
  if (split.length === 0) return null;
  const months = health.window.months ?? [];
  const newest = months[months.length - 1];
  return (
    <section className="card stack health-split" aria-labelledby="split-title">
      <h2 id="split-title">{t.splitTitle}</h2>
      <p className="muted">
        {t.splitExplained(health.debt ? p.euro(health.debt.gross_assets) : null, newest ? p.longDate(monthEnd(newest)) : null)}
      </p>
      <ul className="health-split-rows">
        {split.map(([key, share]) => (
          <li key={key}>
            <span>{splitLabel(t, key)}</span>
            <span className="household-share" aria-hidden="true">
              <span style={{ width: `${Math.max(0, Math.min(100, Number(share) * 100))}%` }} />
            </span>
            <span>{p.ratio(share)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// --- Vermögen im Verlauf ------------------------------------------------------------------

function SeriesCard({ series, onChanged }: { series: NetWorthPoint[]; onChanged: () => void }) {
  const t = useT(healthFigures);
  const p = usePlanFormat();
  if (series.length === 0) return null;
  const totals = series.map((point) => (point.total != null ? Number(point.total) : 0));
  const largest = Math.max(1, ...totals.map(Math.abs));
  const todos = series.map((point) => point.todo).filter((todo): todo is HealthTodo => todo != null);
  return (
    <section className="card stack" aria-labelledby="series-title">
      <h2 id="series-title">{t.seriesTitle}</h2>
      <p className="muted">{t.seriesIntro}</p>
      <ul className="planning-bars" aria-hidden="true">
        {series.map((point, index) => (
          <li key={point.month_end}>
            <span
              className={index === series.length - 1 ? "planning-bar planning-bar-sale" : "planning-bar"}
              style={{ height: `${(Math.abs(totals[index] ?? 0) / largest) * 100}%` }}
            />
            <span className="planning-bar-year">{p.longDate(point.month_end).replace(/ \d{4}$/, "")}</span>
          </li>
        ))}
      </ul>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{t.month}</th>
              <th scope="col" className="num">
                {t.netWorthAtMonthEnd}
              </th>
              <th scope="col" className="num">
                {t.changeColumn}
              </th>
              <th scope="col" className="num">
                {t.ofWhichSaved}
              </th>
              <th scope="col" className="num">
                {t.otherChanges}
              </th>
            </tr>
          </thead>
          <tbody>
            {series.map((point) => (
              <tr key={point.month_end}>
                <th scope="row">
                  <Link href={`/household?monat=${point.month_end.slice(0, 7)}`}>{p.month(point.month_end.slice(0, 7))}</Link>
                </th>
                <td className="num">{point.total != null ? p.euro(point.total) : "–"}</td>
                <td className="num">{point.change != null ? p.signedEuro(point.change) : "–"}</td>
                <td className="num">{point.saved != null ? p.euro(point.saved) : "–"}</td>
                <td className="num">{point.other != null ? p.signedEuro(point.other) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <HealthTodoList todos={todos} onChanged={onChanged} />
    </section>
  );
}

