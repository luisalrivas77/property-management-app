"use client";

// The household's key figures (phase 9 §14, D235–D243, D250; docs/mockups/13-overview-figures.png
// before the limits are confirmed, 13b after), in the sections they are about since phase 10
// §7.6 (Luis, 23 Nov 2026: „They should be moved to their respective sections as Planung will
// be removed“; until then one block on Übersicht):
// - Haushalt (HouseholdHealthSection): the card „Liquiditätsreserve“, the card „Ampel-Grenzen
//   festlegen“ (`#ampel-grenzen`; each proposed limit with its origin, editable; „Übernehmen“
//   stores them in a new version of the default household scenario) and the block „Kennzahlen“
//   (`#figures-title`) with each figure's mark beside its sentences.
// - Anlagen › Übersicht (AssetsHealthSection): the card a month-end to-do opens
//   (`/assets?month_end=…`, „Monatsabschluss festhalten“), the split of the assets (never marked)
//   and „Vermögen im Verlauf“.
// Every figure is the server's (GET /health, whose `series` is GET /health/net-worth-series's
// stored runs); a mark and its sentences come only from confirmed limits, so nothing is coloured
// before „Übernehmen“. Every figure opens what it is made of, and every gap is a to-do with its
// button (principle 7); a month-end to-do is the button „Monatsabschluss festhalten“ itself.
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
import { ErrorNotice } from "@/lib/ErrorNotice";

import {
  type BandLimits,
  type BandProposal,
  bandsConfirmedBy,
  COLOUR_CLASS,
  COLOUR_LABEL,
  currentDefault,
  FIGURE_LABEL,
  FIGURES,
  formatOneDecimal,
  type Health,
  type HealthTodo,
  HIGHER_IS_BETTER,
  type HouseholdScenario,
  type Light,
  leversOf,
  loadMemberNames,
  loadScenarioHistory,
  type MarkedFigure,
  monthEnd,
  monthEndOf,
  type NetWorthPoint,
  REFERENCE_POINTS,
  saveDefaultScenario,
  toMultiple,
  toSignedRate,
} from "./healthShared";
import { Field } from "./form";
import { percentInput } from "./input";
import { snapshotOutcomeText } from "./netWorth";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./health.css";

type Account = components["schemas"]["Account"];
type SnapshotResult = components["schemas"]["SnapshotResult"];
type EstimateSource = components["schemas"]["EstimateSource"];
type HealthText = Translation<typeof healthFigures.de>;

/** One decimal as phase 9 shows it: German exactly as formatOneDecimal („3,0“), English with a
 * trailing „.0“ dropped. */
function oneDecimal(p: PlanFormat, value: string | null | undefined): string {
  return p.locale === "de" ? formatOneDecimal(value) : p.short(value);
}

/** A limit as shown: a ratio as a percent, the reserve's red as a percent of the target,
 * Schulden zu Jahreseinkommen as a multiple (German exactly as healthShared's limitText). */
function limitShown(p: PlanFormat, t: HealthText, figure: MarkedFigure, value: string | null | undefined): string {
  if (value == null) return "–";
  return figure === "debt_to_income" ? t.times(oneDecimal(p, value)) : p.ratio(value);
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

function HealthFigures({ data, onChanged }: { data: HealthData; onChanged: () => void }) {
  const { health, scenarios } = data;
  const [editing, setEditing] = useState(false);
  const proposals = health.proposals ?? [];
  const todos = health.todos ?? [];
  const current = currentDefault(scenarios);
  const confirmed = (current?.levers?.bands ?? []).length > 0;
  const showCard = !confirmed || editing;
  // The bands' own to-do is the card itself on this page. Below three closed months Haushalt's
  // „Noch keine Durchschnitte“ above shows „too few closed months“ and what holds the next month
  // open (its month-end to-do included) with their buttons, so they are not listed here a second
  // time (phase 10 §7.6 (f)); GET /health has no other to-dos (rules/health/window, bands).
  const otherTodos = health.window.enough ? todos.filter((todo) => todo.code !== "bands_unconfirmed" && todo.code !== "too_few_months") : [];
  const t = useT(healthFigures);
  return (
    <>
      <ReserveCard data={data} onChanged={onChanged} />
      {showCard ? (
        <BandsCard
          health={health}
          proposals={proposals}
          current={current}
          confirmed={confirmed}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
          onCancel={confirmed ? () => setEditing(false) : null}
        />
      ) : (
        <div className="notice notice-info row health-confirmed" role="note">
          <p>{t.bandsConfirmed}</p>
          <span className="spacer" />
          <button type="button" className="link" onClick={() => setEditing(true)}>
            {t.changeLimits}
          </button>
        </div>
      )}
      <FiguresCard
        data={data}
        confirmed={confirmed}
        current={current}
        todos={otherTodos}
        onChanged={onChanged}
        onEditBands={() => setEditing(true)}
      />
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

// --- the mark ---------------------------------------------------------------------------

function MarkPill({ light }: { light: Light | undefined }) {
  if (!light?.colour) return null;
  return <span className={COLOUR_CLASS[light.colour]}>{COLOUR_LABEL[light.colour]}</span>;
}

/** „eigene Grenze: rot unter 0,0 %, keine Grenze für Grün“ beside a marked figure. */
function LimitLine({ figure, light, target }: { figure: MarkedFigure; light: Light | undefined; target?: string }) {
  const t = useT(healthFigures);
  const p = usePlanFormat();
  if (!light?.colour) return null;
  const higher = HIGHER_IS_BETTER[figure];
  if (figure === "reserve") {
    return <p className="muted">{t.reserveLimit(target ? p.euro(target) : null, limitShown(p, t, figure, light.red))}</p>;
  }
  const red =
    light.red != null
      ? higher
        ? t.redBelow(limitShown(p, t, figure, light.red))
        : t.redAbove(limitShown(p, t, figure, light.red))
      : t.noRed;
  const green =
    light.green != null
      ? higher
        ? t.greenFrom(limitShown(p, t, figure, light.green))
        : t.greenUpTo(limitShown(p, t, figure, light.green))
      : t.noGreen;
  return <p className="muted">{t.ownLimitLine(higher ? `${red}, ${green}` : `${green}, ${red}`)}</p>;
}

function Texts({ light }: { light: Light | undefined }) {
  const texts = light?.texts ?? [];
  if (texts.length === 0) return null;
  return (
    <div className="health-texts">
      {texts.map((text) => (
        <p key={text}>{text}</p>
      ))}
    </div>
  );
}

// --- Liquiditätsreserve -------------------------------------------------------------------

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

function ReserveCard({ data, onChanged }: { data: HealthData; onChanged: () => void }) {
  const reserve = data.health.reserve;
  const light = data.health.marks?.reserve;
  const t = useT(healthFigures);
  const p = usePlanFormat();
  return (
    <section className="card stack" aria-labelledby="reserve-title">
      <h2 id="reserve-title">{FIGURE_LABEL.reserve}</h2>
      {reserve ? (
        <>
          <p className="cashflow-value">
            {p.euro(reserve.amount)} <MarkPill light={light} />
          </p>
          <p className="muted">
            {reserve.months != null ? t.reserveMonths(oneDecimal(p, reserve.months), p.euro(reserve.need)) : t.reserveNoNeed}
          </p>
          <p className="muted">
            {t.ownTarget(p.euro(reserve.target), p.short(reserve.target_months))}{" "}
            <Link href="/planning?tab=haushalt">{t.change}</Link>
          </p>
          <LimitLine figure="reserve" light={light} target={reserve.target} />
          <Texts light={light} />
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
              {t.needExplained(p.monthRange(data.health.window.months ?? []))}{" "}
              <Link href="/planning?tab=haushalt">{t.changeReserveAccounts}</Link>
            </p>
          </details>
          <HealthTodoList todos={reserve.todos ?? []} onChanged={onChanged} />
        </>
      ) : (
        <p className="muted">{t.reserveLater}</p>
      )}
    </section>
  );
}

// --- Ampel-Grenzen festlegen ---------------------------------------------------------------

interface LimitDraft {
  green: string;
  red: string;
}

/** A limit from the API as typed text: a ratio in percent ("0.3" → "30,0"), the reserve's red as
 * a percent of the target, Schulden zu Jahreseinkommen as a multiple. */
function limitInput(figure: MarkedFigure, value: string | null | undefined): string {
  if (value == null) return "";
  if (figure === "debt_to_income") return value.replace(".", ",");
  const percent = percentInput(value);
  return percent.includes(",") ? percent : `${percent},0`;
}

function parseLimit(figure: MarkedFigure, raw: string): { ok: true; value: string | null } | { ok: false; message: string } {
  if (raw.trim() === "") return { ok: true, value: null };
  return figure === "debt_to_income" ? toMultiple(raw, 4) : toSignedRate(raw, 4);
}

function draftsFrom(bands: BandLimits[], proposals: BandProposal[]): Record<MarkedFigure, LimitDraft> {
  const out = {} as Record<MarkedFigure, LimitDraft>;
  for (const figure of FIGURES) {
    const band = bands.find((b) => b.figure === figure);
    const proposal = proposals.find((p) => p.figure === figure);
    out[figure] = band
      ? { green: limitInput(figure, band.green), red: limitInput(figure, band.red) }
      : bands.length > 0
        ? { green: "", red: "" }
        : { green: limitInput(figure, proposal?.green?.value), red: limitInput(figure, proposal?.red?.value) };
  }
  return out;
}

function figureIntro(t: HealthText, figure: MarkedFigure): string {
  switch (figure) {
    case "reserve":
      return "";
    case "savings_rate":
      return t.introSavingsRate;
    case "ltv":
      return t.introLtv;
    case "debt_service_ratio":
      return t.introDebtService;
    case "debt_ratio":
    case "debt_to_income":
      return t.introNoProposal;
  }
}

/** A refusal of the default scenario the limits are saved in (`household_scenario.*`, PR #434
 * review D): the fix is on Planung › Haushalt, where the scenario's other levers are. */
function scenarioRefused(error: unknown): boolean {
  return error instanceof ApiError && error.body !== null && ruleCodes(error.body).some((code) => code.startsWith("household_scenario."));
}

function BandsCard({
  health,
  proposals,
  current,
  confirmed,
  onSaved,
  onCancel,
}: {
  health: Health;
  proposals: BandProposal[];
  current: HouseholdScenario | null;
  confirmed: boolean;
  onSaved: () => void;
  onCancel: (() => void) | null;
}) {
  const ids = useId();
  const [drafts, setDrafts] = useState(() => draftsFrom(current?.levers?.bands ?? [], proposals));
  const [messages, setMessages] = useState<Partial<Record<string, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const initial = draftsFrom(current?.levers?.bands ?? [], proposals);
  const target = health.reserve?.target ?? null;
  const targetMonths = current?.levers?.reserve_target_months ?? health.reserve?.target_months ?? "6";
  const t = useT(healthFigures);
  const c = useT(common);
  const p = usePlanFormat();

  function set(figure: MarkedFigure, side: keyof LimitDraft, value: string) {
    setDrafts((previous) => ({ ...previous, [figure]: { ...previous[figure], [side]: value } }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found: Partial<Record<string, string>> = {};
    const bands: BandLimits[] = [];
    for (const figure of FIGURES) {
      const draft = drafts[figure];
      const green = figure === "reserve" ? ({ ok: true, value: null } as const) : parseLimit(figure, draft.green);
      const red = parseLimit(figure, draft.red);
      if (!green.ok) found[`${figure}.green`] = green.message;
      if (!red.ok) found[`${figure}.red`] = red.message;
      if (green.ok && red.ok && (green.value !== null || red.value !== null)) {
        bands.push({ figure, green: green.value, red: red.value });
      }
    }
    setMessages(found);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    setError(null);
    try {
      await saveDefaultScenario({ ...leversOf(current), bands }, current);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  function originText(figure: MarkedFigure): string {
    const draft = drafts[figure];
    if (draft.green.trim() === "" && draft.red.trim() === "") return t.noLimitYet;
    const proposal = proposals.find((p) => p.figure === figure);
    const same = draft.green === initial[figure].green && draft.red === initial[figure].red;
    if (!confirmed && proposal && same) {
      const source = proposal.red?.origin === "source" || proposal.green?.origin === "source";
      return source ? t.sourceSuggested : t.ownSuggested;
    }
    return t.ownLimit;
  }

  function field(figure: MarkedFigure, side: keyof LimitDraft, label: string, unit: string, hint?: string) {
    const message = messages[`${figure}.${side}`];
    return (
      <Field id={`${ids}-${figure}-${side}`} label={t.fieldLabel(label, unit)} hint={hint} errors={message ? [message] : []}>
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

  const reserveRed = parseLimit("reserve", drafts.reserve.red);
  const reserveRedEuro =
    target && reserveRed.ok && reserveRed.value !== null ? p.euro(multiply(target, reserveRed.value)) : null;

  return (
    <section className="card stack" id="ampel-grenzen" aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>{confirmed ? t.bandsChange : t.bandsSet}</h2>
      <p>{t.bandsIntro}</p>
      <form className="stack" onSubmit={(event) => void submit(event)} aria-label={t.bandsForm}>
        {FIGURES.map((figure) => (
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
                field("reserve", "red", t.redBelowLabel, t.unitOfTarget, reserveRedEuro ? `= ${reserveRedEuro}` : undefined)
              ) : HIGHER_IS_BETTER[figure] ? (
                <>
                  {field(figure, "red", t.redBelowLabel, t.unitPercent)}
                  {field(figure, "green", t.greenFromLabel, t.unitPercent, drafts[figure].green.trim() === "" ? t.emptyNoGreen : undefined)}
                </>
              ) : (
                <>
                  {field(figure, "green", t.greenUpToLabel, figure === "debt_to_income" ? t.unitTimes : t.unitPercent)}
                  {field(figure, "red", t.redAboveLabel, figure === "debt_to_income" ? t.unitTimes : t.unitPercent)}
                </>
              )}
            </div>
            <p>
              <span className="badge badge-muted">{originText(figure)}</span>
            </p>
            <div className="health-reference">
              <p className="health-reference-title">{t.reference}</p>
              {REFERENCE_POINTS[figure].map((text) => (
                <p key={text} className="muted">
                  {text}
                </p>
              ))}
            </div>
          </fieldset>
        ))}
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
          {onCancel ? (
            <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
              {c.cancel}
            </button>
          ) : null}
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

function FiguresCard({
  data,
  confirmed,
  current,
  todos,
  onChanged,
  onEditBands,
}: {
  data: HealthData;
  confirmed: boolean;
  current: HouseholdScenario | null;
  todos: HealthTodo[];
  onChanged: () => void;
  onEditBands: () => void;
}) {
  const { health } = data;
  const t = useT(healthFigures);
  const p = usePlanFormat();
  const marks = health.marks ?? {};
  const months = health.window.months ?? [];
  const range = p.monthRange(months);
  const newest = months[months.length - 1];
  const by = current && confirmed ? bandsConfirmedBy(data.scenarios, current) : null;
  const rate = health.savings_rate;
  const debt = health.debt;
  const ltvRow = (debt?.properties ?? []).reduce<components["schemas"]["PropertyLtv"] | null>(
    (best, row) => (row.ltv != null && (best?.ltv == null || Number(row.ltv) > Number(best.ltv)) ? row : best),
    null,
  );
  const gap = (figure: components["schemas"]["DebtFigure"]) => debt?.gaps?.find((g) => g.figure === figure)?.reason ?? null;
  const annualised = debt && debt.months < 12 ? t.annualised(debt.months) : "";

  return (
    <section className="card stack" aria-labelledby="figures-title">
      <div className="planning-card-head">
        <h2 id="figures-title">{t.figuresTitle}</h2>
        <span className="spacer" />
        {confirmed ? (
          <button type="button" className="link" onClick={onEditBands}>
            {t.changeLimits}
          </button>
        ) : (
          <a href="#ampel-grenzen" className="link">
            {t.setLimits}
          </a>
        )}
      </div>
      <p className="muted">
        {range ? `${range} · ` : ""}
        {newest ? t.holdingsOn(p.longDate(monthEnd(newest))) : ""}
        {confirmed
          ? `${t.coloursOwn}${
              by ? t.appliedBy(by.created_by ? (data.names.get(by.created_by) ?? t.aMember) : null, p.longDate(by.created_at)) : ""
            }`
          : t.noColours}
      </p>
      <HealthTodoList todos={todos} onChanged={onChanged} />
      {rate ? (
        <Figure title={FIGURE_LABEL.savings_rate} value={p.ratio(rate.rate)} light={marks.savings_rate}>
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
          <LimitLine figure="savings_rate" light={marks.savings_rate} />
          <Texts light={marks.savings_rate} />
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
        </Figure>
      ) : null}
      {debt ? (
        <>
          <Figure
            title={FIGURE_LABEL.debt_service_ratio}
            value={debt.debt_service_ratio != null ? p.ratio(debt.debt_service_ratio) : "–"}
            light={marks.debt_service_ratio}
          >
            <p className="muted">{t.debtServiceExplained(annualised, p.euro(debt.debt_service_year), p.euro(debt.income_year))}</p>
            {gap("debt_service_ratio") ? <p className="muted">{gap("debt_service_ratio")}</p> : null}
            <LimitLine figure="debt_service_ratio" light={marks.debt_service_ratio} />
            <Texts light={marks.debt_service_ratio} />
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
          </Figure>
          <Figure title={FIGURE_LABEL.ltv} value={ltvRow?.ltv != null ? p.ratio(ltvRow.ltv) : "–"} light={marks.ltv}>
            <p className="muted">
              {ltvRow?.estimate_source === "advance" ? t.ltvAdvance : t.ltvEstimate}
              {ltvRow ? t.ofAmount(p.euro(ltvRow.loan_balance), p.euro(ltvRow.estimate)) : ""}.
            </p>
            {gap("ltv") ? <p className="muted">{gap("ltv")}</p> : null}
            <LimitLine figure="ltv" light={marks.ltv} />
            <Texts light={marks.ltv} />
            <details className="health-sources">
              <summary>{t.madeOf}</summary>
              <ul className="household-rows">
                {(debt.properties ?? []).map((row) => (
                  <li key={row.property_id}>
                    <span>
                      <Link href={`/properties/${row.property_id}`}>{t.property}</Link>{" "}
                      <span className="muted">
                        {t.estimateRow(estimateSource(t, row.estimate_source), p.euro(row.estimate), p.euro(row.loan_balance))}
                      </span>
                    </span>
                    <span>{p.ratio(row.ltv)}</span>
                  </li>
                ))}
              </ul>
            </details>
          </Figure>
          <Figure
            title={FIGURE_LABEL.debt_ratio}
            value={debt.debt_ratio != null ? p.ratio(debt.debt_ratio) : "–"}
            light={marks.debt_ratio}
          >
            <p className="muted">{t.debtRatioExplained(p.euro(debt.liabilities), p.euro(debt.gross_assets))}</p>
            {gap("debt_ratio") ? <p className="muted">{gap("debt_ratio")}</p> : null}
            <LimitLine figure="debt_ratio" light={marks.debt_ratio} />
            <Texts light={marks.debt_ratio} />
            <details className="health-sources">
              <summary>{t.madeOf}</summary>
              <ul className="household-rows">
                <li>
                  <span>{t.allDebts}</span>
                  <span>{p.euro(debt.liabilities)}</span>
                </li>
                <li>
                  <span>{t.allOwned}</span>
                  <span>{p.euro(debt.gross_assets)}</span>
                </li>
              </ul>
              <p className="muted">
                {t.fromMonthEndBefore}
                <Link href="/assets">{t.investments}</Link>
                {t.and}{" "}
                <Link href="/accounts">{t.accounts}</Link>.
              </p>
            </details>
          </Figure>
          <Figure
            title={FIGURE_LABEL.debt_to_income}
            value={debt.debt_to_income != null ? oneDecimal(p, debt.debt_to_income) : "–"}
            light={marks.debt_to_income}
          >
            <p className="muted">{t.debtToIncomeExplained(annualised)}</p>
            {gap("debt_to_income") ? <p className="muted">{gap("debt_to_income")}</p> : null}
            <LimitLine figure="debt_to_income" light={marks.debt_to_income} />
            <Texts light={marks.debt_to_income} />
            <details className="health-sources">
              <summary>{t.madeOf}</summary>
              <ul className="household-rows">
                <li>
                  <span>{t.allDebts}</span>
                  <span>{p.euro(debt.liabilities)}</span>
                </li>
                <li>
                  <span>{t.yearIncome(annualised ? debt.months : null)}</span>
                  <span>{p.euro(debt.income_year)}</span>
                </li>
              </ul>
              <p className="muted">
                {t.monthsUnderBefore}
                <Link href="/loans">{t.loans}</Link>
                {t.and}{" "}
                <Link href="/accounts">{t.accounts}</Link>.
              </p>
            </details>
          </Figure>
          {!confirmed ? null : (["debt_ratio", "debt_to_income"] as const).some((f) => !current?.levers?.bands?.some((b) => b.figure === f)) ? (
            <p className="muted">
              {t.noColourYet}{" "}
              <button type="button" className="link" onClick={onEditBands}>
                {t.enterLimit}
              </button>
            </p>
          ) : null}
        </>
      ) : null}
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

function Figure({
  title,
  value,
  light,
  children,
}: {
  title: string;
  value: string;
  light: Light | undefined;
  children: ReactNode;
}) {
  return (
    <div className="health-figure" role="group" aria-label={title}>
      <p className="health-figure-label">{title}</p>
      <p className="health-figure-value">
        {value} <MarkPill light={light} />
      </p>
      {children}
    </div>
  );
}

/** Where the loan-to-value's property value comes from (phase 9 D239 amended): the scenario's
 * estimate, the purchase price, or before the handover the price paid so far, „Anzahlung,
 * gebucht“ (phase 6 D265), a booked figure and no estimate. */
function estimateSource(t: HealthText, source: EstimateSource): string {
  switch (source) {
    case "scenario":
      return t.sourceScenario;
    case "purchase_price":
      return t.sourcePurchasePrice;
    case "advance":
      return t.sourceAdvance;
  }
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

