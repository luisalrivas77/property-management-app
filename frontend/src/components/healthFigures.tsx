"use client";

// The household's key figures (phase 9 §14, D235–D243, D250; docs/mockups/13-overview-figures.png
// before the limits are confirmed, 13b after), in the sections they are about since phase 10
// §7.6 (Luis, 23 Nov 2026: „They should be moved to their respective sections as Planung will
// be removed“; until then one block on Übersicht):
// - Haushalt › Monat (useHouseholdHealth, HouseholdHealthCard, HealthFigurePanel): the card
//   „Finanzielle Gesundheit“ (`#figures-title`; mockup 19g, phase 10 §7.12 (a), D309) with the
//   verdict's word, its period and whose limits, and the three figures (phase 10 §7.6, Luis 23 Nov
//   2026: Sparquote, Liquiditätsreserve, Schuldendienstquote as „Kreditraten“) as three small
//   cards, each with its dot and its green limit; a tapped card opens the side panel
//   (`#d-kennzahl-<figure>`) with the figure, what it is made of and its green, amber and red
//   ranges. The page places the card between the line on the averages and „Überschuss je Monat“
//   and builds the panel in its own layout, so the panel sits beside the month. The
//   Beleihungsauslauf, Verschuldungsgrad and Schulden zu
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
import {
  assumptionsHref,
  HOUSEHOLD_BUDGET,
  HOUSEHOLD_RESERVE,
  planTodoTarget,
  propertyPlanningHref,
} from "@/lib/planningRoutes";
import { DetailPanel, useDetail } from "./DetailPanel";
import { type Loaded } from "./useLoad";
import { partLabel, STATE_MARK, stateText, type VerdictPart, VerdictDot, wordText, WORD_MARK } from "./verdict";

import "./health.css";

type Account = components["schemas"]["Account"];
type SnapshotResult = components["schemas"]["SnapshotResult"];
type HealthText = Translation<typeof healthFigures.de>;
type VerdictText = Translation<typeof verdictText.de>;

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

export type HouseholdHealthData = HealthData;

/** The detail kind of a figure's side panel: `#d-kennzahl-<figure>`. */
export const FIGURE_DETAIL = "kennzahl";

/** Haushalt's key figures (phase 10 §7.6, §7.12 (a)): the reads behind „Finanzielle Gesundheit“
 * and its panels. A change of `refresh` reads them again in place (phase 10 §7.6 (f): a month-end
 * captured from Haushalt's „Noch keine Durchschnitte“ above moves the figures without a reload). */
export function useHouseholdHealth(refresh = 0): Loaded<HealthData> {
  const state = useLoad(loadHouseholdHealth);
  useScrollToHash(state.data !== undefined);
  const { reload } = state;
  const seen = useRef(refresh);
  useEffect(() => {
    if (seen.current === refresh) return;
    seen.current = refresh;
    reload();
  }, [refresh, reload]);
  return state;
}

/** Anlagen › Übersicht's part (phase 10 §7.6): the card a month-end to-do opens
 * (`?month_end=YYYY-MM-DD`) above the tab's own content (`children`), and „Vermögen im Verlauf“
 * below it. The split of the assets moved into the tab's total card (Luis, 23 Nov 2026), so the
 * card „Aufteilung des Vermögens“ is gone. Inside a Suspense boundary (useSearchParams). */
export function AssetsHealthSection({ children, after }: { children?: ReactNode; after?: ReactNode }) {
  const health = useLoad(loadHealth);
  const requested = useSearchParams().get("month_end");
  return (
    <div className="stack">
      {requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? <MonthEndCard day={requested} onDone={health.reload} /> : null}
      {children}
      <Loading state={health}>
        {(data) => <SeriesCard series={data.series ?? []} onChanged={health.reload} />}
      </Loading>
      {after}
    </div>
  );
}

/** The three figures of Finanzielle Gesundheit (rules/verdict), in the server's order. The sheet
 * edits only these; a band the household stored for any other figure is kept as it is. */
const VERDICT_FIGURES: MarkedFigure[] = ["savings_rate", "reserve", "debt_service_ratio"];

/** „Finanzielle Gesundheit“ on Haushalt › Monat, and „Ampel-Grenzen festlegen“ under it while it
 * is open. */
export function HouseholdHealthCard({ data, onChanged }: { data: HealthData; onChanged: () => void }) {
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
        // A plan to-do's href from rules/plan/ still names Planung (D271), which left the menu in
        // phase 10 §7.9 (a): it leads where that part of Planung is now.
        <Link href={planTodoTarget(todo.href, todo.code, HOUSEHOLD_BUDGET)} className="button-link secondary">
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
            <Link href={assumptionsHref(HOUSEHOLD_RESERVE, "reserve")}>{t.change}</Link>
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
              {t.needExplained(range)} <Link href={assumptionsHref(HOUSEHOLD_RESERVE, "reserve")}>{t.changeReserveAccounts}</Link>
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
              <Link href={propertyPlanningHref()}>{t.planningProperty}</Link>
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
                  <Link href={assumptionsHref(HOUSEHOLD_RESERVE, "reserve")}>{t.planning}</Link>
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
            <Link href={assumptionsHref(HOUSEHOLD_BUDGET, "reserve")}>{t.planningHousehold}</Link>
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

// --- Finanzielle Gesundheit ------------------------------------------------------------------

/** Whether the household set its own limit for any of the three figures. */
function ownLimits(current: HouseholdScenario | null): boolean {
  return (current?.levers?.bands ?? []).some((band) => VERDICT_FIGURES.includes(band.figure));
}

/** A figure's short name on its small card (mockup 19g): „Sparquote Ø 5 Mon.“, „Reserve“,
 * „Kreditraten“. */
function shortLabel(t: HealthText, tv: VerdictText, figure: MarkedFigure, months: number | null): string {
  switch (figure) {
    case "savings_rate":
      // Without enough closed months there is no mean to name its months for.
      return months != null ? t.savingsShort(months) : partLabel(tv, figure);
    case "reserve":
      return t.reserveShort;
    case "debt_service_ratio":
      return t.debtShort;
    default:
      return figure;
  }
}

/** The figure as its small card shows it: the reserve in months („7,3 Mon.“), the others as a
 * percent; „–“ without a figure. */
function shortValue(t: HealthText, p: PlanFormat, part: VerdictPart): string {
  if (part.figure === "reserve") return part.months != null ? t.monthsShort(oneDecimal(p, part.months)) : "–";
  if (part.no_loan) return "–";
  return part.value != null ? p.ratio(part.value) : "–";
}

/** The green limit in one line: „ab 20 % grün“, „ab 6 Mon. grün“, „bis 30 % grün“. */
function greenLine(t: HealthText, p: PlanFormat, part: VerdictPart): string {
  if (part.no_loan) return t.noLoan;
  if (part.figure === "reserve") {
    return part.target_months != null ? t.greenFrom(t.monthsShort(p.short(part.target_months))) : t.noLimit;
  }
  if (part.good == null) return t.noLimit;
  return HIGHER_IS_BETTER[part.figure] ? t.greenFrom(p.ratio(part.good)) : t.greenUpTo(p.ratio(part.good));
}

/** „Finanzielle Gesundheit“ (`#figures-title`, mockup 19g): the word, the period and whose
 * limits, the to-dos, and the three figures as small cards that open their panel. */
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
  const tv = useT(verdictText);
  const p = usePlanFormat();
  const detail = useDetail(FIGURE_DETAIL);
  const months = health.window.months ?? [];
  const range = p.monthRange(months);
  const own = ownLimits(current);
  const by = current && own ? bandsConfirmedBy(data.scenarios, current) : null;
  const verdict = health.verdict ?? null;
  const parts = verdict?.parts ?? [];

  return (
    <section className="card stack health-card" aria-labelledby="figures-title">
      <div className="planning-card-head">
        <h2 id="figures-title">{t.healthTitle}</h2>
        <span className="spacer" />
        <button type="button" className="link" onClick={onEditBands}>
          {t.changeLimits}
        </button>
      </div>
      {verdict ? (
        <p className="verdict-word-text health-word">
          <VerdictDot mark={WORD_MARK[verdict.word]} />
          {wordText(tv, verdict.word)}
        </p>
      ) : null}
      <p className="muted">
        {t.periodLimits(range, own)}
        {by ? t.appliedBy(by.created_by ? (data.names.get(by.created_by) ?? t.aMember) : null, p.longDate(by.created_at)) : ""}
      </p>
      <HealthTodoList todos={todos} onChanged={onChanged} />
      {parts.length > 0 ? (
        <ul className="health-minis">
          {parts.map((part) => {
            const label = shortLabel(t, tv, part.figure, health.window.enough ? months.length : null);
            return (
              <li key={part.figure}>
                <a {...detail.linkProps(part.figure)} className="health-mini" data-state={part.state}>
                  <span className="health-mini-label">{label}</span>
                  <span className="health-mini-value">
                    <VerdictDot mark={STATE_MARK[part.state]} label={stateText(tv, part.state)} />
                    {shortValue(t, p, part)}
                  </span>
                  <span className="health-mini-limit">
                    {greenLine(t, p, part)} <span aria-hidden="true">›</span>
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">–</p>
      )}
    </section>
  );
}

interface RangeRow {
  mark: "green" | "amber" | "red";
  text: string;
}

/** The green, amber and red ranges a figure is measured against (mockup 19g frame 3), from the
 * verdict's limits; a side without a limit leaves its rows out. */
function rangeRows(t: HealthText, p: PlanFormat, part: VerdictPart): RangeRow[] {
  if (part.no_loan) return [];
  if (part.figure === "reserve") {
    const good = part.target_months != null ? t.monthsShort(p.short(part.target_months)) : null;
    const strained = part.strained != null ? t.ofTarget(p.ratio(part.strained)) : null;
    const rows: RangeRow[] = [];
    if (good) rows.push({ mark: "green", text: t.fromLimit(good) });
    if (good && strained) rows.push({ mark: "amber", text: t.betweenLimits(strained, good) });
    if (strained) rows.push({ mark: "red", text: t.belowLimit(strained) });
    return rows;
  }
  const good = part.good != null ? p.ratio(part.good) : null;
  const strained = part.strained != null ? p.ratio(part.strained) : null;
  const higher = HIGHER_IS_BETTER[part.figure];
  const rows: RangeRow[] = [];
  if (good) rows.push({ mark: "green", text: higher ? t.fromLimit(good) : t.upToLimit(good) });
  if (good && strained) rows.push({ mark: "amber", text: higher ? t.betweenLimits(strained, good) : t.betweenLimits(good, strained) });
  if (strained) rows.push({ mark: "red", text: higher ? t.belowLimit(strained) : t.aboveLimit(strained) });
  return rows;
}

/** The chosen month's own savings rate, named in the savings rate's panel (it has no colour). */
export interface MonthRate {
  month: string;
  rate: string | null;
}

/** One figure in the side panel (mockup 19g frame 3): the figure with its dot and the server's
 * headline, the period, „Woraus sie besteht“, „Grenzen“ with its green, amber and red ranges and
 * whose limits they are, and since when one-offs stay out of the average. */
export function HealthFigurePanel({
  data,
  figure,
  monthRate,
  backLabel,
  onClose,
  onChanged,
}: {
  data: HealthData;
  figure: string;
  monthRate: MonthRate | null;
  backLabel: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { health } = data;
  const t = useT(healthFigures);
  const tv = useT(verdictText);
  const p = usePlanFormat();
  const part = (health.verdict?.parts ?? []).find((row) => row.figure === figure);
  if (!part) return null;
  const months = health.window.months ?? [];
  const range = p.monthRange(months);
  const title =
    part.figure === "savings_rate" && health.window.enough ? t.savingsLong(months.length) : partLabel(tv, part.figure);
  const rows = rangeRows(t, p, part);
  const own = ownLimits(currentDefault(data.scenarios));
  const since = health.one_offs_left_out_since ?? null;
  const rate = health.savings_rate;
  return (
    <DetailPanel title={title} backLabel={backLabel} onClose={onClose}>
      <div className="stack health-panel" data-figure={part.figure}>
        <p className="health-panel-value">
          <VerdictDot mark={STATE_MARK[part.state]} label={stateText(tv, part.state)} />
          {shortValue(t, p, part)}
        </p>
        <p>
          {part.headline}
          {range ? ` ${t.overRange(range)}` : ""}
          {part.figure === "savings_rate" && monthRate && monthRate.rate != null
            ? ` ${t.monthRateExplained(p.month(monthRate.month), p.ratio(monthRate.rate))}`
            : ""}
        </p>
        {part.figure === "savings_rate" && rate ? (
          <section className="stack" aria-label={t.madeOfTitle}>
            <h3>{t.madeOfTitle}</h3>
            <ul className="household-rows">
              <li>
                <span>
                  {t.incomeRow}
                  <span className="muted health-row-sub">{t.monthsCount(months.length)}</span>
                </span>
                <span>{p.euro(rate.income)}</span>
              </li>
              <li>
                <span>
                  {t.savedRow}
                  <span className="muted health-row-sub">
                    {t.ofWhichPrincipal} {p.euro(rate.principal)}
                  </span>
                </span>
                <span>{p.euro(rate.saved)}</span>
              </li>
              <li>
                <span>
                  {t.rateRow}
                  <span className="muted health-row-sub">{t.rateHow}</span>
                </span>
                <strong>{p.ratio(rate.rate)}</strong>
              </li>
            </ul>
            <FigureDetails part={part} data={data} onChanged={onChanged} />
          </section>
        ) : (
          <section className="stack" aria-label={t.madeOfTitle}>
            <h3>{t.madeOfTitle}</h3>
            <FigureDetails part={part} data={data} onChanged={onChanged} />
          </section>
        )}
        {rows.length > 0 ? (
          <section className="stack" aria-label={t.limitsTitle}>
            <h3>{t.limitsTitle}</h3>
            <ul className="household-rows health-ranges">
              {rows.map((row) => (
                <li key={row.mark}>
                  <span>
                    <VerdictDot mark={row.mark} /> {row.mark === "green" ? t.green : row.mark === "amber" ? t.amber : t.red}
                  </span>
                  <span>{row.text}</span>
                </li>
              ))}
            </ul>
            <p className="muted">
              {own ? t.limitsOwnFoot : t.limitsStandardFoot}{" "}
              {/* The sheet opens from its hash (as Übersicht's link does), which also closes this panel. */}
              <a href="#ampel-grenzen">{t.changeLimits}</a>
            </p>
          </section>
        ) : null}
        {part.why ? <p className="muted">{part.why}</p> : null}
        <details className="verdict-reference">
          <summary>{tv.reference}</summary>
          <p className="muted">{part.reference}</p>
        </details>
        {since && (part.figure === "savings_rate" || part.figure === "reserve") ? (
          <div className="health-one-offs" role="note">
            <p>
              <strong>{t.oneOffsLeftOut(p.month(since))}</strong>
            </p>
            <p className="muted">{t.oneOffsLeftOutText}</p>
          </div>
        ) : null}
      </div>
    </DetailPanel>
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
    <section className="card stack health-series" aria-labelledby="series-title">
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
      <ul className="health-series-list" aria-label={t.seriesTitle}>
        {series.map((point) => (
          <li key={point.month_end}>
            <span className="health-series-line">
              <Link href={`/household?monat=${point.month_end.slice(0, 7)}`}>{p.month(point.month_end.slice(0, 7))}</Link>
              <strong>{point.total != null ? p.euro(point.total) : "–"}</strong>
            </span>
            <span className="health-series-line muted">
              <span>
                {t.changeColumn} {point.change != null ? p.signedEuro(point.change) : "–"}
              </span>
              <span>
                {t.ofWhichSaved} {point.saved != null ? p.euro(point.saved) : "–"}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <div className="table-wrap health-series-table">
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

