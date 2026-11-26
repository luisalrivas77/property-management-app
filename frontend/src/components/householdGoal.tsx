"use client";

// Euer Ziel (phase 10 §7.9 (d), D289, D298; mockups 19b, 19c; Luis: the goal „very important in
// the app … highlighted similarly as the financial health part“): the card on Übersicht beside
// Finanzielle Gesundheit, and Haushalt › Ziel with the form, the figures and the spending
// scenarios. Every figure, the word, the scenarios' budgets and whether they close the gap are
// the server's (GET /household-goal, rules/goal); the browser only formats, and it proposes no
// goal, no date and no amount (D289 (1)).
//
// Writes go through the household scenarios' own endpoint, superseding the default scenario
// (POST /household-scenarios): each write reads the scenario history first and starts from the
// default in force, so it never supersedes a row that is no longer current, and it carries every
// other lever as saved. Saving the form writes `levers.goal` (`set_on` today, only for a new or
// changed goal: an unchanged goal is never posted); „Als Budget übernehmen“ writes the scenario's
// `spending_changes` and `goal_choice` {spending, spending_chosen_on today}, the goal untouched;
// „Ziel löschen“ writes no goal and no choice. Adopting is refused while „Annahmen“ holds an
// unsaved draft, so a draft never silently replaces the adopted budget or the other way round.
//
// The panels (`#d-ziel-festlegen`, `#d-ziel-<scenario>`) are built by `useGoalView` and handed
// to the page's one DetailLayout by PlanProvider (components/householdPlan.tsx), like the one-off
// check's panel.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, type CSSProperties, type FormEvent, type ReactNode, useContext, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { type Translation, useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { detailHref } from "@/lib/detailHash";
import { HOUSEHOLD_PLAN_PAGE, HOUSEHOLD_PLAY, HOUSEHOLD_RESERVE, RATE_LEVER } from "@/lib/planningRoutes";

import { DetailPanel, useDetail } from "./DetailPanel";
import { todayIso } from "./format";
import { Field } from "./form";
import { HealthTodoList } from "./healthFigures";
import {
  currentDefault,
  fillLevers,
  type HouseholdLevers,
  type HouseholdPlan,
  type HouseholdScenario,
  loadScenarioHistory,
  saveDefaultScenario,
} from "./healthShared";
import { decimalInput, toDecimalString, toMoneyString } from "./input";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { Loading } from "./ui";
import { type Loaded, useLoad } from "./useLoad";

import "./householdGoal.css";

export type HouseholdGoal = components["schemas"]["HouseholdGoal"];
type Goal = components["schemas"]["Goal"];
type GoalKind = components["schemas"]["GoalKind"];
type GoalWord = components["schemas"]["GoalWord"];
type GoalScenario = components["schemas"]["GoalScenario"];
type GoalSpending = components["schemas"]["GoalSpending"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type AdviceMode = components["schemas"]["AdviceMode"];
type SpendingChange = components["schemas"]["SpendingChange"];
type GT = Translation<typeof goalText.de>;

/** The detail kind of the goal's panels: the form and one panel per spending scenario. */
export const GOAL_DETAIL = "ziel";
export const GOAL_FORM = "festlegen";
/** The side panels of mockup 19f: the balanced route, routes 2, 3 and 4, the four words and
 * „Womit gerechnet wird“. Route 4's own two scenarios keep their kinds as ids (19c). */
const GOAL_BALANCED = "ausgewogen";
const GOAL_MORE_SAVING = "anlegen";
const GOAL_MORE_RETURN = "rendite";
const GOAL_LESS_SPENDING = "ausgeben";
const GOAL_WORDS = "woerter";
const GOAL_BASIS = "basis";
/** Haushalt › Ziel, and the form opened there (Übersicht's „Ziel festlegen“). */
export const GOAL_PAGE = "/household?tab=ziel";
export const GOAL_FORM_HREF = `${GOAL_PAGE}${detailHref(GOAL_DETAIL, GOAL_FORM)}`;

/** The goal as Übersicht reads it (one request). */
export const loadGoal = (): Promise<HouseholdGoal> => ok(api.GET("/api/household-goal"));

interface GoalData {
  goal: HouseholdGoal;
  /** The default scenario in force, for „Eigene Wahl“ (its saved spending changes). */
  current: HouseholdScenario | null;
}

async function loadGoalData(): Promise<GoalData> {
  const [goal, rows] = await Promise.all([loadGoal(), loadScenarioHistory()]);
  return { goal, current: currentDefault(rows) };
}

async function loadNoGoal(): Promise<GoalData | null> {
  return null;
}

const SCENARIO_ORDER: GoalSpending[] = ["wants_only", "all_equal"];

function isScenarioKind(id: string | null): id is "wants_only" | "all_equal" {
  return id === "wants_only" || id === "all_equal";
}

export function goalWordText(t: GT, goal: HouseholdGoal): string {
  switch (goal.word) {
    case "on_track":
      return t.onTrack;
    case "feasible":
      return t.feasible;
    case "ambitious":
      return t.ambitious;
    case "hardly":
      return t.hardly;
    case "none":
      return t.noGoal;
    default:
      return goal.issue ? t.issueWord(goal.issue) : "–";
  }
}

export function goalTargetText(t: GT, p: PlanFormat, goal: Goal): string {
  const month = p.month(goal.due_month);
  switch (goal.kind) {
    case "net_worth":
      return t.targetNetWorth(p.euro(goal.target), month);
    case "saved_sum":
      return t.targetSaved(p.euro(goal.target), month);
    case "reserve_months":
      return t.targetReserve(p.short(goal.target), month);
  }
}

function scenarioName(t: GT, kind: GoalSpending): string {
  return kind === "wants_only" ? t.wantsOnly : kind === "all_equal" ? t.allEqual : t.own;
}

/** The bar's width: the server's percent, at most full. A display width, never a figure. */
function barWidth(percent: string): string {
  const value = Number(percent);
  return `${Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
}

function categoryName(code: AllocationCategory): string {
  return ALLOCATION_CATEGORY[code] ?? code;
}

// --- Übersicht's tile ------------------------------------------------------------------------

/** The tile's body under „Euer Ziel“ (mockup 19f panel 1): the target and its month, the word as
 * its pill, the bar, and two figures (today, the month the balanced route reaches it). Every
 * figure is the server's. */
export function GoalTileBody({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  if (!goal.goal) return null;
  const reached = goal.balanced?.reached ?? null;
  return (
    <>
      <GoalHeadline goal={goal} goalSet={goal.goal} />
      {goal.word && goal.word !== "none" ? <WordPill word={goal.word} text={goalWordText(t, goal)} /> : <span className="tile-sub">{goalWordText(t, goal)}</span>}
      {goal.percent != null ? (
        <span className="goal-bar goal-bar-tile" role="img" aria-label={t.percentReached(p.percentValue(goal.percent))}>
          <span style={{ width: barWidth(goal.percent) }} />
        </span>
      ) : null}
      {goal.word && goal.word !== "none" ? (
        <Stats count={2}>
          <Stat value={p.euro(goal.value_today)} label={t.reserveToday} />
          {reached ? (
            <Stat value={p.month(reached)} label={t.atGoalBalanced} />
          ) : (
            <Stat value={goal.reached ? p.month(goal.reached) : "–"} label={t.atGoal} />
          )}
        </Stats>
      ) : null}
    </>
  );
}

// --- the summary (Übersicht's panel and the Ziel tab) ------------------------------------------

function FormLink({ href, children, className, ariaLabel }: { href: string; children: ReactNode; className?: string; ariaLabel?: string }) {
  // A hash of this page opens the panel in place; a path is another page.
  if (href.startsWith("#"))
    return (
      <a href={href} className={className} aria-label={ariaLabel}>
        {children}
      </a>
    );
  return (
    <Link href={href} className={className} aria-label={ariaLabel}>
      {children}
    </Link>
  );
}

/** Übersicht's panel for „Euer Ziel“: the same card, and while the goal has no word, why and
 * what to do (principle 7: every gap with its button). The rest is on Haushalt › Ziel. */
export function GoalOverviewPanel({ goal, formHref, onChanged }: { goal: HouseholdGoal; formHref: string; onChanged: () => void }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  if (!goal.goal) return null;
  return (
    <div className="stack">
      <div className="goal-c">
        <div className="goal-ch">
          <h3>{t.title}</h3>
          <FormLink href={formHref} className="goal-lnk" ariaLabel={t.changeGoal}>
            {t.change}
          </FormLink>
        </div>
        <GoalTileBody goal={goal} />
        {goal.percent != null ? <p className="goal-one">{t.percentReached(p.percentValue(goal.percent))}</p> : null}
      </div>
      <GoalIssue goal={goal} formHref={formHref} onChanged={onChanged} />
    </div>
  );
}

function GoalIssue({ goal, formHref, onChanged }: { goal: HouseholdGoal; formHref: string; onChanged: () => void }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  switch (goal.issue) {
    case "no_projection":
      return (
        <div className="stack goal-issue">
          <p>{t.issueNoProjection}</p>
          {(goal.todos ?? []).length > 0 ? (
            <>
              <p className="tile-panel-kicker goal-kicker">{t.waiting}</p>
              <HealthTodoList todos={goal.todos ?? []} onChanged={onChanged} />
            </>
          ) : null}
        </div>
      );
    case "due_passed":
      return (
        <div className="stack goal-issue">
          <p>{t.issueDuePassed}</p>
          <p>
            <FormLink href={formHref} className="button-link secondary">
              {t.changeGoal}
            </FormLink>
          </p>
        </div>
      );
    case "beyond_projection":
      return (
        <div className="stack goal-issue">
          <p>{t.issueBeyond}</p>
          <p className="row">
            <FormLink href={formHref} className="button-link secondary">
              {t.changeGoal}
            </FormLink>
          </p>
        </div>
      );
    case "used_up":
      return (
        <div className="stack goal-issue">
          <p>{t.issueUsedUp(p.month(goal.used_up))}</p>
          <p>
            <Link href={HOUSEHOLD_RESERVE} className="button-link secondary">
              {t.issueUsedUpAction}
            </Link>
          </p>
        </div>
      );
    case "no_need":
      return (
        <div className="stack goal-issue">
          <p>{t.issueNoNeed}</p>
          <p>
            <FormLink href={formHref} className="button-link secondary">
              {t.changeGoal}
            </FormLink>
          </p>
        </div>
      );
    default:
      return null;
  }
}

// --- Haushalt › Ziel ---------------------------------------------------------------------------

type Write = (patch: (levers: HouseholdLevers) => HouseholdLevers) => Promise<void>;

/** One write at a time, its error kept for the place that started it. */
function useRun() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (action: () => Promise<void>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setError(caught);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

/** What the goal's cards read from the household plan. PlanProvider hands it in, since
 * components/householdPlan.tsx imports this file and the import must not run back. */
export interface GoalPlanInfo {
  plan: HouseholdPlan | null;
  mode: AdviceMode | undefined;
  /** Puts spending changes into Durchspielen's draft (§7.10 (a)); null before the plan is read. */
  playSpending: ((changes: SpendingChange[]) => void) | null;
}

const GoalPlanContext = createContext<GoalPlanInfo>({ plan: null, mode: undefined, playSpending: null });

function useGoalPlan(): GoalPlanInfo {
  return useContext(GoalPlanContext);
}

/** „Anlegbar pro Monat“, what the walk invests each month (D301). */
function investableMonthly(plan: HouseholdPlan | null): string | null {
  return plan?.surplus?.rows.find((row) => row.key === "investable")?.amount ?? null;
}

export interface GoalView {
  /** The open goal panel, or null. */
  panel: ReactNode;
  /** Haushalt › Ziel's content. */
  tab: ReactNode;
}

/** The goal's read, its panels and the Ziel tab, for PlanProvider. `enough`: the plan has its
 * three closed months, so „Annahmen“ (Eigene Wahl) can open; `dirty`: „Annahmen“ holds an
 * unsaved draft; `onWritten` reads the plan again after a write. */
export function useGoalView({
  enabled,
  backLabel,
  enough,
  dirty,
  onWritten,
  info,
}: {
  enabled: boolean;
  backLabel: string;
  enough: boolean;
  dirty: boolean;
  onWritten: () => void;
  info: GoalPlanInfo;
}): GoalView {
  const t = useT(goalText);
  const state = useLoad(enabled ? loadGoalData : loadNoGoal);
  const detail = useDetail(GOAL_DETAIL);
  const data = state.data ?? null;

  const write: Write = async (patch) => {
    // From the default in force now, so a write never supersedes an old row (not_current).
    const current = currentDefault(await loadScenarioHistory());
    await saveDefaultScenario(patch(fillLevers(current?.levers)), current);
    state.reload();
    onWritten();
  };

  let panel: ReactNode = null;
  if (enabled && data && detail.id !== null) {
    const goal = data.goal;
    if (detail.id === GOAL_FORM) {
      panel = (
        <DetailPanel title={goal.goal ? t.changeGoal : t.setGoal} backLabel={backLabel} onClose={detail.close}>
          <GoalForm key={goal.goal ? JSON.stringify(goal.goal) : "new"} goal={goal.goal ?? null} write={write} onDone={detail.close} />
        </DetailPanel>
      );
    } else if (detail.id === GOAL_WORDS && goal.goal) {
      panel = (
        <DetailPanel title={t.wordsTitle} backLabel={backLabel} onClose={detail.close}>
          <WordsPanel goal={goal} />
        </DetailPanel>
      );
    } else if (detail.id === GOAL_BASIS && goal.goal) {
      panel = (
        <DetailPanel title={t.basisTitle} backLabel={backLabel} onClose={detail.close}>
          <BasisPanel goal={goal} />
        </DetailPanel>
      );
    } else if (detail.id === GOAL_BALANCED && goal.balanced) {
      panel = (
        <DetailPanel title={t.balancedTitle} backLabel={backLabel} onClose={detail.close}>
          <BalancedPanel goal={goal} />
        </DetailPanel>
      );
    } else if (detail.id === GOAL_MORE_SAVING && goal.more_saving) {
      panel = (
        <DetailPanel title={t.onlyMoreSaving} backLabel={backLabel} onClose={detail.close}>
          <MoreSavingPanel goal={goal} />
        </DetailPanel>
      );
    } else if (detail.id === GOAL_MORE_RETURN && goal.more_return) {
      panel = (
        <DetailPanel title={t.onlyMoreReturn} backLabel={backLabel} onClose={detail.close}>
          <MoreReturnPanel goal={goal} />
        </DetailPanel>
      );
    } else if (detail.id === GOAL_LESS_SPENDING && (goal.scenarios ?? []).length > 0) {
      panel = (
        <DetailPanel title={t.onlyLessSpending} backLabel={backLabel} onClose={detail.close}>
          <LessSpendingPanel data={data} enough={enough} dirty={dirty} write={write} />
        </DetailPanel>
      );
    } else if (isScenarioKind(detail.id)) {
      const scenario = (goal.scenarios ?? []).find((s) => s.kind === detail.id);
      if (scenario && goal.goal) {
        panel = (
          <DetailPanel title={scenarioName(t, scenario.kind)} backLabel={backLabel} onClose={detail.close}>
            <ScenarioPanel
              scenario={scenario}
              goal={goal}
              replaces={(data.current?.levers?.spending_changes ?? []).length > 0}
              dirty={dirty}
              write={write}
              onDone={detail.close}
            />
          </DetailPanel>
        );
      }
    }
  }

  const tab = enabled ? <GoalTab state={state} /> : null;
  return {
    panel: panel ? <GoalPlanContext.Provider value={info}>{panel}</GoalPlanContext.Provider> : null,
    tab: tab ? <GoalPlanContext.Provider value={info}>{tab}</GoalPlanContext.Provider> : null,
  };
}

/** The goal's cards on Haushalt › Ziel (mockup 19f panels 2, 3 and 7; „Cards, not text“, 25 Nov
 * 2026): each card a title, one number or a few small figures and at most one short line; every
 * explanation sits in the side panel its card opens. */
function GoalTab({ state }: { state: Loaded<GoalData | null> }) {
  const t = useT(goalText);
  const detail = useDetail(GOAL_DETAIL);
  const ids = useId();
  return (
    <Loading state={state}>
      {(data) =>
        data ? (
          data.goal.word === "none" || !data.goal.goal ? (
            <section className="card goal-card" aria-labelledby={`${ids}-title`}>
              <h2 id={`${ids}-title`}>{t.title}</h2>
              <p className="muted">{t.noGoalText}</p>
              <p>
                <a {...detail.linkProps(GOAL_FORM)} className="button-link">
                  {t.setGoal}
                </a>
              </p>
            </section>
          ) : (
            <GoalCards goal={data.goal} goalSet={data.goal.goal} onChanged={state.reload} />
          )
        ) : null
      }
    </Loading>
  );
}

/** Whether the walk without change misses: then the routes show (19f panel 3); on course, or
 * while the goal has no word yet, none does. */
function hasGap(goal: HouseholdGoal): boolean {
  return goal.issue == null && (goal.word === "feasible" || goal.word === "ambitious" || goal.word === "hardly");
}

function GoalCards({ goal, goalSet, onChanged }: { goal: HouseholdGoal; goalSet: Goal; onChanged: () => void }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const detail = useDetail(GOAL_DETAIL);
  const ids = useId();
  const chosen = goal.choice?.spending ?? null;
  return (
    <section className="goal-cards" aria-labelledby={`${ids}-title`}>
      <div className="goal-c">
        <div className="goal-ch">
          <h3 id={`${ids}-title`}>{t.title}</h3>
          <span className="goal-ch-end">
            {/* „Euer Plan“ (§7.11 (h), mockup 19i): the plan as seven cards, under this tab. */}
            <Link href={HOUSEHOLD_PLAN_PAGE} className="goal-lnk">
              {t.toPlan} ›
            </Link>
            <a {...detail.linkProps(GOAL_FORM)} className="goal-lnk" aria-label={t.changeGoal}>
              {t.change}
            </a>
          </span>
        </div>
        <GoalHeadline goal={goal} goalSet={goalSet} />
        {goal.word != null ? (
          <a {...detail.linkProps(GOAL_WORDS)} className="goal-word-link">
            <WordPill word={goal.word} text={goalWordText(t, goal)} />
          </a>
        ) : (
          <WordPill word="none" text={goalWordText(t, goal)} />
        )}
        {chosen && goal.issue == null ? (
          <p className="goal-one">
            {goal.reached_with_budget
              ? t.reachedWithBudget(scenarioName(t, chosen), p.month(goal.reached_with_budget))
              : t.notReachedWithBudget(scenarioName(t, chosen))}
          </p>
        ) : null}
      </div>
      {goal.issue != null ? (
        <div className="goal-c">
          <GoalIssue goal={goal} formHref={detail.href(GOAL_FORM)} onChanged={onChanged} />
        </div>
      ) : (
        <>
          {hasGap(goal) ? null : <OnTrackCard goal={goal} />}
          <BasisCard goal={goal} />
          <AsIsCard goal={goal} goalSet={goalSet} />
          {hasGap(goal) ? (
            <>
              {goal.balanced ? <BalancedCard goal={goal} /> : null}
              <OtherRoutesCard goal={goal} />
            </>
          ) : null}
          {(goal.assumptions ?? []).map((a) => (
            <AssumptionCard key={a.href} href={a.href} action={a.action} />
          ))}
        </>
      )}
    </section>
  );
}

/** „500.000 € · Dez. 2031“: the target in euros (a reserve goal in months) and the due month. */
function GoalHeadline({ goal, goalSet }: { goal: HouseholdGoal; goalSet: Goal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const target = goalSet.kind === "reserve_months" ? t.headMonths(p.short(goalSet.target)) : p.euro(goal.target ?? goalSet.target);
  return (
    <span className="goal-n">
      {target} · {p.month(goalSet.due_month)}
    </span>
  );
}

/** The word as a pill, its own colour per word (19f's four pills). */
export function WordPill({ word, text }: { word: GoalWord; text: string }) {
  return (
    <span className="goal-word" data-word={word}>
      <i aria-hidden="true" />
      {text}
    </span>
  );
}

/** A small figure with its label (19f's `.st`). */
export function Stat({ value, label, warn }: { value: ReactNode; label: ReactNode; warn?: boolean }) {
  return (
    <div>
      <span className={warn ? "goal-v goal-v-warn" : "goal-v"}>{value}</span>
      <span className="goal-l">{label}</span>
    </div>
  );
}

export function Stats({ children, count }: { children: ReactNode; count: number }) {
  return (
    <div className="goal-st" style={{ "--k": String(Math.max(1, Math.min(3, count))) } as CSSProperties}>
      {children}
    </div>
  );
}

/** A card's head that opens its side panel (`kind`: the panel's detail kind, Haushalt › Ziel's by default). */
export function CardHead({ title, id, tag, kind = GOAL_DETAIL }: { title: string; id?: string; tag?: ReactNode; kind?: string }) {
  const detail = useDetail(kind);
  if (!id)
    return (
      <div className="goal-ch">
        <h3>{title}</h3>
        {tag}
      </div>
    );
  return (
    <a {...detail.linkProps(id)} className="goal-ch goal-ch-link">
      <h3>{title}</h3>
      <span className="goal-ch-end">
        {tag}
        <span className="goal-go" aria-hidden="true">
          ›
        </span>
      </span>
    </a>
  );
}

function isZero(amount: string | null | undefined): boolean {
  return amount == null || /^-?0+(\.0+)?$/.test(amount);
}

function OnTrackCard({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  return (
    <div className="goal-c goal-c-main">
      <CardHead title={t.onTrackTitle} id={GOAL_WORDS} />
      <Stats count={2}>
        <Stat value={p.euro(goal.value_today)} label={t.reserveToday} />
        <Stat value={goal.reached ? p.month(goal.reached) : "–"} label={t.atGoal} />
      </Stats>
    </div>
  );
}

/** „Womit gerechnet wird“: the split the walk invests the money above the reserve in, what is
 * added each month and each sale (D301). The split and the monthly amount are the plan's. */
function BasisCard({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const { plan } = useGoalPlan();
  const shares = plan?.investment_split?.shares ?? null;
  const monthly = investableMonthly(plan);
  const sales = goal.sales ?? [];
  const count = (shares ? 1 : 0) + (monthly != null ? 1 : 0) + sales.length;
  if (count === 0) return null;
  return (
    <div className="goal-c">
      <CardHead title={t.basisTitle} id={GOAL_BASIS} />
      <Stats count={count}>
        {shares ? <Stat value={t.splitValue(p.short(shares.depot), p.short(shares.crypto))} label={t.splitLabel} /> : null}
        {monthly != null ? <Stat value={monthly.startsWith("-") ? p.signedEuro(monthly) : p.euro(monthly)} label={t.perMonth} /> : null}
        {sales.map((sale) => (
          <Stat key={sale.year} value={p.euro(sale.net_proceeds)} label={t.saleLabel(sale.year)} />
        ))}
      </Stats>
    </div>
  );
}

/** „So wie jetzt“: the value at the due month without change, what is missing, and the chart of
 * the server's points (`path`), only scaled to the drawing here. */
function AsIsCard({ goal, goalSet }: { goal: HouseholdGoal; goalSet: Goal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const due = p.month(goalSet.due_month);
  const short = goal.short_at_due;
  return (
    <div className="goal-c">
      <CardHead title={t.asIsTitle} />
      <Stats count={short != null ? 2 : 1}>
        <Stat value={p.euro(goal.projected)} label={t.atMonth(due)} />
        {short != null ? <Stat value={`−${p.euro(short)}`} label={t.missing} warn /> : null}
      </Stats>
      <GoalChart goal={goal} label={t.chartLabel(due)} />
    </div>
  );
}

const CHART = { left: 10, right: 330, top: 20, bottom: 92 };

/** The chart of mockup 19f: the target as a dashed line, the walk without change, the balanced
 * route dotted, each sale's December as a line, the first and last year below. Coordinates only:
 * every value drawn is the server's. */
function GoalChart({ goal, label }: { goal: HouseholdGoal; label: string }) {
  const t = useT(goalText);
  const path = goal.path ?? [];
  if (path.length < 2 || goal.target == null) return null;
  const target = Number(goal.target);
  const values = path.flatMap((point) => [Number(point.as_is), ...(point.balanced != null ? [Number(point.balanced)] : [])]);
  const low = Math.min(...values);
  const high = Math.max(target, ...values);
  const span = high - low || 1;
  const x = (i: number) => CHART.left + (i * (CHART.right - CHART.left)) / (path.length - 1);
  const y = (value: number) => CHART.bottom - ((value - low) / span) * (CHART.bottom - CHART.top);
  const fmt = (n: number) => n.toFixed(1);
  const asIs = path.map((point, i) => `${fmt(x(i))},${fmt(y(Number(point.as_is)))}`).join(" ");
  const balanced = path
    .map((point, i) => (point.balanced != null ? `${fmt(x(i))},${fmt(y(Number(point.balanced)))}` : null))
    .filter((point): point is string => point !== null);
  const sales = (goal.sales ?? [])
    .map((sale) => path.findIndex((point) => point.month === `${sale.year}-12`))
    .filter((i) => i >= 0);
  const last = path.length - 1;
  const lastPoint = path[last];
  return (
    <svg className="goal-chart" viewBox="0 0 340 110" role="img" aria-label={label}>
      <line x1={CHART.left} y1={y(target)} x2={CHART.right} y2={y(target)} stroke="var(--accent)" strokeDasharray="5 4" strokeWidth="1.5" />
      <text x={12} y={Math.max(11, y(target) - 6)} fontSize="11" fontWeight="700" fill="var(--accent-strong)">
        {t.chartGoal}
      </text>
      {sales.map((i) => (
        <g key={i}>
          <line x1={x(i)} y1={12} x2={x(i)} y2={100} stroke="var(--border-strong)" strokeWidth="1" />
          <text x={Math.min(x(i) + 4, 300)} y={100} fontSize="10.5" fill="var(--muted)">
            {t.chartSale}
          </text>
        </g>
      ))}
      <polyline points={asIs} fill="none" stroke="var(--text)" strokeWidth="2.5" />
      {balanced.length >= 2 ? (
        <polyline points={balanced.join(" ")} fill="none" stroke="var(--accent)" strokeWidth="2" strokeDasharray="2 3" />
      ) : null}
      {lastPoint ? <circle cx={x(last)} cy={y(Number(lastPoint.as_is))} r="4" fill="var(--text)" /> : null}
      <text x={12} y={108} fontSize="10.5" fill="var(--muted)">
        {path[0]?.month.slice(0, 4)}
      </text>
      <text x={328} y={108} fontSize="10.5" fill="var(--muted)" textAnchor="end">
        {lastPoint?.month.slice(0, 4)}
      </text>
    </svg>
  );
}

/** „Ausgewogener Weg“, the main route (D302): what is invested more, what the wants give, each
 * want from its mean to its budget, and the month the goal is reached that way. */
function BalancedCard({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const { playSpending } = useGoalPlan();
  const router = useRouter();
  const balanced = goal.balanced;
  if (!balanced) return null;
  const toPlay = () => {
    if (!playSpending) return;
    // The cuts as Durchspielen's draft (§7.10 (a)): only the spending changes travel; the extra
    // investing has no field there.
    playSpending(balanced.spending_changes ?? []);
    router.push(HOUSEHOLD_PLAY);
  };
  return (
    <div className="goal-c goal-c-main">
      <CardHead title={t.balancedTitle} id={GOAL_BALANCED} tag={<span className="goal-tag goal-tag-yes">{t.mainRoute}</span>} />
      <div className="goal-rows">
        <div className="goal-rw">
          <span>{t.moreSavingRow}</span>
          <b>{balanced.saving_extra != null ? t.perMonthAmount(`+${p.euro(balanced.saving_extra)}`) : t.aboveIncome}</b>
        </div>
        {!isZero(balanced.cut) ? (
          <div className="goal-rw">
            <span>{t.fewerWants}</span>
            <b>{t.perMonthAmount(`−${p.euro(balanced.cut)}`)}</b>
          </div>
        ) : null}
        {(balanced.budgets ?? []).map((budget) => (
          <div key={budget.code} className="goal-rw goal-rw-sub">
            <span>{categoryName(budget.code)}</span>
            <b>{t.fromTo(p.euro(budget.mean), p.euro(budget.budget))}</b>
          </div>
        ))}
        <div className="goal-rw">
          <span>{t.atGoal}</span>
          <b>{balanced.reached ? p.month(balanced.reached) : t.notReached}</b>
        </div>
      </div>
      {(balanced.spending_changes ?? []).length > 0 && playSpending ? (
        <button type="button" className="goal-lnk goal-lnk-button" onClick={toPlay}>
          {t.toPlay} ›
        </button>
      ) : (
        <Link href={HOUSEHOLD_PLAY} className="goal-lnk">
          {t.toPlay} ›
        </Link>
      )}
    </div>
  );
}

const VERDICT_TAG: Record<string, string> = { within_history: "goal-tag-yes", good_times: "goal-tag-maybe", never: "goal-tag-no" };

/** The spending scenario „Nur weniger ausgeben“ stands for: the first that closes, else the
 * wants-only one. */
function lessSpending(goal: HouseholdGoal): GoalScenario | null {
  const scenarios = SCENARIO_ORDER.map((kind) => (goal.scenarios ?? []).find((s) => s.kind === kind)).filter((s): s is GoalScenario => s != null);
  return scenarios.find((s) => s.closes) ?? scenarios[0] ?? null;
}

/** „Andere Wege“: routes 2, 3 and 4, each a row that opens its panel (D302, D303). */
function OtherRoutesCard({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const detail = useDetail(GOAL_DETAIL);
  const ret = goal.more_return ?? null;
  const less = lessSpending(goal);
  const returnTag = ret ? (ret.available && ret.verdict ? t.verdictTag(ret.verdict) : ret.reason ? t.reasonTag(ret.reason) : "") : "";
  const returnClass = ret?.available && ret.verdict ? (VERDICT_TAG[ret.verdict] ?? "goal-tag-no") : "goal-tag-no";
  return (
    <div className="goal-c">
      <CardHead title={t.otherRoutes} />
      <div className="goal-rows">
        {goal.more_saving ? (
          <a {...detail.linkProps(GOAL_MORE_SAVING)} className="goal-rw goal-rw-link">
            <span>{t.onlyMoreSaving}</span>
            <b>{t.perMonthAmount(`+${p.euro(goal.more_saving.extra)}`)}</b>
          </a>
        ) : null}
        {ret ? (
          <a {...detail.linkProps(GOAL_MORE_RETURN)} className="goal-rw goal-rw-link">
            <span>
              {t.onlyMoreReturn}
              {returnTag ? <span className={`goal-tag ${returnClass}`}>{returnTag}</span> : null}
            </span>
            <b>{ret.needed_return != null ? p.ratio(ret.needed_return) : "–"}</b>
          </a>
        ) : null}
        {less ? (
          <a {...detail.linkProps(GOAL_LESS_SPENDING)} className="goal-rw goal-rw-link">
            <span>{t.onlyLessSpending}</span>
            <b>{t.perMonthAmount(`−${p.euro(less.cut)}`)}</b>
          </a>
        ) : null}
      </div>
    </div>
  );
}

/** 19f panel 7: a property the walk keeps at today's value because it has no scenario. */
function AssumptionCard({ href, action }: { href: string; action: string }) {
  const t = useT(goalText);
  return (
    <div className="goal-c">
      <CardHead title={t.flatTitle} tag={<span className="goal-tag goal-tag-no">{t.noScenario}</span>} />
      <Stats count={2}>
        <Stat value={t.kept} label={t.assumption} />
        <Stat value={t.asToday} label={t.valueLabel} />
      </Stats>
      <Link href={href} className="button-link secondary goal-btn">
        {action} ›
      </Link>
    </div>
  );
}

// --- the side panels (19f panels 4 and 5) ------------------------------------------------------

function Footnote() {
  const t = useT(goalText);
  return <p className="goal-note">{t.footnote}</p>;
}

function BalancedPanel({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const balanced = goal.balanced;
  if (!balanced || !goal.goal) return null;
  return (
    <div className="stack goal-panel">
      {goal.short_at_due != null ? <p>{t.gapLine(p.month(goal.goal.due_month), p.euro(goal.short_at_due))}</p> : null}
      <p>{balanced.return_rate != null ? t.balancedRate(p.ratio(balanced.return_rate)) : t.balancedTyped}</p>
      <p>{balanced.saving_extra != null ? t.balancedHalf : t.balancedTooMuch}</p>
      {!isZero(balanced.moved_to_saving) ? <p>{t.balancedMoved(p.euro(balanced.moved_to_saving))}</p> : null}
      {(balanced.budgets ?? []).length > 0 ? <p>{t.balancedLimit}</p> : null}
      {balanced.saving != null ? <p>{t.balancedSaving(p.euro(balanced.saving))}</p> : null}
      <p>{balanced.closes && balanced.reached ? t.balancedReached(p.month(balanced.reached)) : t.balancedNotCloses}</p>
      <Footnote />
    </div>
  );
}

function MoreSavingPanel({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const more = goal.more_saving;
  if (!more) return null;
  return (
    <div className="stack goal-panel">
      <p>{t.moreSavingText(p.euro(more.extra))}</p>
      <p>{more.noetig != null ? t.moreSavingTotal(p.euro(more.noetig)) : t.moreSavingTooMuch}</p>
      <p className="goal-note">{t.notForWord}</p>
      <Footnote />
    </div>
  );
}

function MoreReturnPanel({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const ret = goal.more_return;
  const [queued, setQueued] = useState<boolean | null>(null);
  const { busy, error, run } = useRun();
  if (!ret) return null;
  const refresh = () =>
    run(async () => {
      const answer = await ok(api.POST("/api/household-goal/reference-returns/refresh"));
      setQueued(answer.queued);
    });
  const counted = ret.windows != null && ret.windows > 0 && ret.reached_windows != null && ret.window_years != null;
  return (
    <div className="stack goal-panel">
      {ret.needed_return != null ? <p>{t.returnText(p.ratio(ret.needed_return))}</p> : null}
      {ret.available && ret.verdict ? <p>{t.returnVerdict(ret.verdict)}</p> : null}
      {!ret.available && ret.reason ? <p>{t.reasonText(ret.reason)}</p> : null}
      {counted ? (
        <div className="goal-c">
          <Stats count={2}>
            {ret.needed_return != null ? <Stat value={p.ratio(ret.needed_return)} label={t.neededLabel} /> : null}
            <Stat value={t.windowsValue(ret.reached_windows ?? 0, ret.windows ?? 0)} label={t.windowsLabel(ret.window_years ?? 0)} />
          </Stats>
          <Stats count={2}>
            <Stat value={p.ratio(ret.median)} label={t.medianLabel} />
            <Stat value={p.ratio(ret.best)} label={t.bestLabel} />
          </Stats>
        </div>
      ) : null}
      {ret.thin ? <p className="goal-note">{t.thinNote}</p> : null}
      {ret.reason === "series_missing" ? (
        <div className="stack">
          {queued === null ? (
            <p>
              <button type="button" className="secondary" disabled={busy} onClick={refresh}>
                {t.refresh}
              </button>
            </p>
          ) : (
            <p role="status">{queued ? t.refreshQueued : t.refreshRunning}</p>
          )}
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {(ret.left_out ?? []).length > 0 ? (
        <div className="goal-c">
          <CardHead title={t.leftOutTitle} />
          <div className="goal-rows">
            {(ret.left_out ?? []).map((item) => (
              <div key={`${item.kind}-${item.symbol ?? ""}`} className="goal-rw">
                <span>
                  {item.kind === "funds" ? t.leftOutFunds : (item.symbol ?? t.leftOutCrypto)}
                  <span className="goal-tag goal-tag-no">{t.leftOutReason(item.reason)}</span>
                </span>
                <b>{p.percentValue(item.share)}</b>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {(ret.sources ?? []).length > 0 ? (
        <div className="goal-c">
          <CardHead title={t.sourcesTitle} />
          <ul className="goal-sources">
            {(ret.sources ?? []).map((source) => (
              <li key={source.key}>
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.source}
                </a>{" "}
                <span className="muted">
                  {source.first_year}–{source.last_year}
                  {source.read_on ? ` · ${t.sourceRead(p.date(source.read_on))}` : ""}
                </span>
                <span className="goal-note">
                  {source.licence} · {source.attribution} · {source.own_calculation}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <Footnote />
    </div>
  );
}

/** „Was die Wörter bedeuten“ (19f panel 5): the four words, today's marked with what decided it;
 * no „Ehrgeizig“ in `generic` mode (D303). */
function WordsPanel({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const { mode } = useGoalPlan();
  const generic = mode === "generic";
  const less = lessSpending(goal);
  const ret = goal.more_return ?? null;
  const due = goal.goal ? p.month(goal.goal.due_month) : "";
  const today = (word: GoalWord): string | null => {
    switch (word) {
      case "on_track":
        return goal.reached ? t.todayOnTrack(p.month(goal.reached)) : t.todayOnTrackPlain;
      case "feasible":
        if (less?.closes) return t.todaySpending(p.euro(less.cut), scenarioName(t, less.kind));
        if (ret?.available && ret.needed_return != null) return t.todayReturn(p.ratio(ret.needed_return));
        return null;
      case "ambitious":
        return ret?.needed_return != null ? t.todayGoodTimes(p.ratio(ret.needed_return)) : null;
      case "hardly":
        return goal.unreachable ? t.todayHardly(due, p.euro(goal.unreachable.reaches_by_due)) : null;
      default:
        return null;
    }
  };
  const words: { word: GoalWord; name: string; text: string }[] = [
    { word: "on_track", name: t.onTrack, text: t.wordOnTrack },
    { word: "feasible", name: t.feasible, text: t.wordFeasible },
    ...(generic ? [] : [{ word: "ambitious" as GoalWord, name: t.ambitious, text: t.wordAmbitious }]),
    { word: "hardly", name: t.hardly, text: t.wordHardly },
  ];
  return (
    <div className="stack goal-panel">
      <div className="goal-words">
        {words.map((row) => {
          const current = row.word === goal.word;
          const line = current ? today(row.word) : null;
          return (
            <div key={row.word} className={current ? "goal-wrow goal-wrow-cur" : "goal-wrow"}>
              <WordPill word={row.word} text={row.name} />
              <p>{row.text}</p>
              {line ? <span className="goal-today">{t.todayLine(line)}</span> : null}
            </div>
          );
        })}
      </div>
      <p className="goal-note">{t.notForWord}</p>
    </div>
  );
}

/** „Womit gerechnet wird“'s panel: the split, the monthly amount, each sale and the typed rates. */
function BasisPanel({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const { plan } = useGoalPlan();
  const shares = plan?.investment_split?.shares ?? null;
  const monthly = investableMonthly(plan);
  const rates = plan?.levers.value_change ?? null;
  return (
    <div className="stack goal-panel">
      <p>{shares ? t.basisSplit(p.percentValue(shares.depot), p.percentValue(shares.crypto)) : t.basisNoSplit}</p>
      {monthly != null ? <p>{t.basisMonthly(monthly.startsWith("-") ? p.signedEuro(monthly) : p.euro(monthly))}</p> : null}
      {(goal.sales ?? []).map((sale) => (
        <p key={sale.year}>{t.basisSale(sale.year, p.euro(sale.net_proceeds))}</p>
      ))}
      {rates ? <p>{t.basisRates(rates.depot != null ? p.ratio(rates.depot) : t.notTyped, rates.crypto != null ? p.ratio(rates.crypto) : t.notTyped)}</p> : null}
      <p>
        <Link href={`/assets#${RATE_LEVER}`} className="button-link secondary">
          {t.changeRates} ›
        </Link>
      </p>
      <Footnote />
    </div>
  );
}

/** „Nur weniger ausgeben“ (route 4): the spending scenarios and „Eigene Wahl“, each adopted as the
 * budget from its own panel (`#d-ziel-wants_only`, `#d-ziel-all_equal`, kept from 19c). */
function LessSpendingPanel({ data, enough, dirty, write }: { data: GoalData; enough: boolean; dirty: boolean; write: Write }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const detail = useDetail(GOAL_DETAIL);
  const goal = data.goal;
  const chosen = goal.choice?.spending ?? null;
  const scenarios = SCENARIO_ORDER.map((kind) => (goal.scenarios ?? []).find((s) => s.kind === kind)).filter((s): s is GoalScenario => s != null);
  const saved = data.current?.levers?.spending_changes ?? [];
  const budgetName = chosen ? scenarioName(t, chosen) : null;
  return (
    <div className="stack goal-panel">
      <p>{t.lessSpendingIntro}</p>
      {budgetName ? (
        <p>{goal.reached_with_budget ? t.reachedWithBudget(budgetName, p.month(goal.reached_with_budget)) : t.notReachedWithBudget(budgetName)}</p>
      ) : null}
      <div className="goal-rows">
        {scenarios.map((s) => (
          <a key={s.kind} {...detail.linkProps(s.kind)} className="goal-rw goal-rw-link">
            <span>
              {scenarioName(t, s.kind)}
              {chosen === s.kind ? <span className="goal-tag goal-tag-yes">{t.yourBudget}</span> : null}
              <span className={s.closes ? "goal-tag goal-tag-yes" : "goal-tag goal-tag-maybe"}>{s.closes ? t.closes : t.notCloses}</span>
            </span>
            <b>{t.perMonthAmount(`−${p.euro(s.cut)}`)}</b>
          </a>
        ))}
      </div>
      <div className="goal-c">
        <CardHead title={t.own} tag={chosen === "own" ? <span className="goal-tag goal-tag-yes">{t.yourBudget}</span> : undefined} />
        {enough ? (
          <>
            <p className="goal-one">{saved.length > 0 ? t.ownMissing : t.ownNoChanges}</p>
            <Link href={HOUSEHOLD_PLAY} className="goal-lnk">
              {t.own} ›
            </Link>
            {saved.length > 0 ? (
              <Adopt
                label={t.ownAdopt}
                name={t.own}
                replaces={false}
                dirty={dirty}
                onConfirm={() =>
                  write((levers) => ({
                    ...levers,
                    goal_choice: { ...(levers.goal_choice ?? {}), spending: "own", spending_chosen_on: todayIso() },
                  }))
                }
              />
            ) : null}
          </>
        ) : (
          <p className="goal-one">{t.ownNeedsMonths}</p>
        )}
      </div>
      <Footnote />
    </div>
  );
}

/** „Als Budget übernehmen“ with its confirmation in the page (no browser dialog). */
function Adopt({
  label,
  name,
  replaces,
  dirty,
  onConfirm,
  onDone,
}: {
  label: string;
  name: string;
  replaces: boolean;
  dirty: boolean;
  onConfirm: () => Promise<void>;
  onDone?: () => void;
}) {
  const t = useT(goalText);
  const [asking, setAsking] = useState(false);
  const { busy, error, run } = useRun();
  const ids = useId();
  return (
    <div className="stack goal-adopt">
      {asking ? (
        <div className="notice notice-info goal-confirm" role="group" aria-labelledby={`${ids}-confirm`}>
          <p>
            <strong id={`${ids}-confirm`}>{t.adoptConfirmTitle}</strong>
          </p>
          <p>{t.adoptConfirmText(name)}</p>
          {replaces ? <p>{t.adoptReplaces}</p> : null}
          <div className="row">
            <button
              type="button"
              disabled={busy || dirty}
              onClick={async () => {
                if (await run(onConfirm)) {
                  setAsking(false);
                  onDone?.();
                }
              }}
            >
              {t.adoptYes}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(false)}>
              {t.cancel}
            </button>
          </div>
        </div>
      ) : (
        <p>
          <button type="button" disabled={dirty} onClick={() => setAsking(true)}>
            {label}
          </button>
        </p>
      )}
      {dirty ? <p className="muted">{t.adoptDirty}</p> : null}
      <ErrorNotice error={error} />
    </div>
  );
}

function ScenarioPanel({
  scenario,
  goal,
  replaces,
  dirty,
  write,
  onDone,
}: {
  scenario: GoalScenario;
  goal: HouseholdGoal;
  replaces: boolean;
  dirty: boolean;
  write: Write;
  onDone: () => void;
}) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const due = goal.goal ? p.month(goal.goal.due_month) : "";
  const name = scenarioName(t, scenario.kind);
  const budgets = scenario.budgets ?? [];
  const rest = scenario.rest;
  const hasRest = !/^-?0+(\.0+)?$/.test(rest);
  return (
    <div className="stack goal-scenario-panel">
      <p className="muted">{scenario.kind === "wants_only" ? t.wantsOnlySub : t.allEqualSub}</p>
      {goal.choice?.spending === scenario.kind ? <p><span className="badge">{t.yourBudget}</span></p> : null}
      <p>
        <strong>{t.cutLine(p.money(scenario.cut))}</strong>
      </p>
      <p>{scenario.closes ? t.closesText(due) : t.notClosesText(due)}</p>
      {hasRest ? <p>{t.restText(p.money(rest))}</p> : null}
      <p className="muted">{t.savingWith(p.money(scenario.saving))}</p>
      {budgets.length > 0 ? (
        <table className="goal-budget-table">
          <thead>
            <tr>
              <th scope="col">{t.category}</th>
              <th scope="col" className="num">
                {t.before}
              </th>
              <th scope="col" className="num">
                {t.budget}
              </th>
            </tr>
          </thead>
          <tbody>
            {budgets.map((b) => (
              <tr key={b.code}>
                <td>
                  {categoryName(b.code)}
                  <span className="muted goal-class"> · {b.budget_class === "need" ? t.need : t.want}</span>
                </td>
                <td className="num">{p.euro(b.mean)}</td>
                <td className="num">{p.euro(b.budget)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="muted">{t.budgetFrom}</p>
      <Adopt
        label={t.adopt}
        name={name}
        replaces={replaces}
        dirty={dirty}
        onDone={onDone}
        onConfirm={() =>
          write((levers) => ({
            ...levers,
            // The goal stays as saved: adopting never re-dates or changes it.
            spending_changes: scenario.spending_changes ?? [],
            goal_choice: { ...(levers.goal_choice ?? {}), spending: scenario.kind, spending_chosen_on: todayIso() },
          }))
        }
      />
    </div>
  );
}

// --- the form ----------------------------------------------------------------------------------

function currentMonth(): string {
  return todayIso().slice(0, 7);
}

function nextMonth(): string {
  const [year = "0", month = "1"] = currentMonth().split("-");
  const y = Number(year);
  const m = Number(month);
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
}

/** "500000.00" and "500000" are the same amount; "6.50" and "6.5" the same months. */
function sameNumber(a: string, b: string): boolean {
  const strip = (v: string) => (v.includes(".") ? v.replace(/0+$/, "").replace(/\.$/, "") : v);
  return strip(a.trim()) === strip(b.trim());
}

function GoalForm({ goal, write, onDone }: { goal: Goal | null; write: Write; onDone: () => void }) {
  const t = useT(goalText);
  const ids = useId();
  const [kind, setKind] = useState<GoalKind>(goal?.kind ?? "net_worth");
  const [amount, setAmount] = useState(goal ? decimalInput(goal.target) : "");
  const [due, setDue] = useState(goal?.due_month ?? "");
  const [errors, setErrors] = useState<{ amount?: string; due?: string }>({});
  const [unchanged, setUnchanged] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const { busy, error, run } = useRun();

  const kinds: { key: GoalKind; label: string; hint: string }[] = [
    { key: "net_worth", label: t.kindNetWorth, hint: t.kindNetWorthHint },
    { key: "saved_sum", label: t.kindSaved, hint: t.kindSavedHint },
    { key: "reserve_months", label: t.kindReserve, hint: t.kindReserveHint },
  ];
  const months = kind === "reserve_months";

  const parseTarget = (): { ok: true; value: string } | { ok: false; message: string } => {
    if (months) {
      const parsed = toDecimalString(amount, 1);
      if (!parsed.ok) return { ok: false, message: t.monthsInvalid };
      const halves = Number(parsed.value) * 2;
      if (!(halves > 0) || halves > 48 || !Number.isInteger(halves)) return { ok: false, message: t.monthsInvalid };
      return parsed;
    }
    const parsed = toMoneyString(amount);
    if (!parsed.ok) return parsed;
    if (parsed.value.startsWith("-") || /^0+(\.0+)?$/.test(parsed.value)) return { ok: false, message: t.amountInvalidZero };
    return parsed;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setUnchanged(false);
    const target = parseTarget();
    const next: { amount?: string; due?: string } = {};
    if (!target.ok) next.amount = target.message;
    if (!/^\d{4}-\d{2}$/.test(due) || due <= currentMonth()) next.due = t.dueInvalid;
    setErrors(next);
    if (!target.ok || next.due) return;
    if (goal && goal.kind === kind && goal.due_month === due && sameNumber(goal.target, target.value)) {
      setUnchanged(true);
      return;
    }
    const saved: Goal = { kind, target: target.value, due_month: due, set_on: todayIso() };
    if (await run(() => write((levers) => ({ ...levers, goal: saved })))) onDone();
  };

  return (
    <form className="stack goal-form" onSubmit={(event) => void submit(event)} noValidate>
      <p className="muted">{t.formIntro}</p>
      <fieldset className="goal-choice-group">
        <legend>{t.kind}</legend>
        {kinds.map((k) => (
          <label key={k.key} className="goal-choice">
            <input type="radio" name={`${ids}-kind`} checked={kind === k.key} onChange={() => setKind(k.key)} />
            <span>
              {k.label}
              <span className="muted goal-choice-hint">{k.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <Field id={`${ids}-amount`} label={months ? t.months : t.amount} hint={months ? t.monthsHint : t.amountHint} errors={errors.amount ? [errors.amount] : []}>
        <input inputMode="decimal" autoComplete="off" value={amount} onChange={(event) => setAmount(event.target.value)} />
      </Field>
      <Field id={`${ids}-due`} label={t.due} hint={t.dueHint} errors={errors.due ? [errors.due] : []}>
        <input type="month" min={nextMonth()} value={due} onChange={(event) => setDue(event.target.value)} />
      </Field>
      <div className="row">
        <button type="submit" disabled={busy}>
          {t.save}
        </button>
        {unchanged ? <span className="muted">{t.unchanged}</span> : null}
      </div>
      <ErrorNotice error={error} />
      {goal ? (
        <div className="goal-delete">
          {deleting ? (
            <div className="notice notice-warn" role="group" aria-label={t.delete}>
              <p>{t.deleteConfirm}</p>
              <div className="row">
                <button
                  type="button"
                  className="danger"
                  disabled={busy}
                  onClick={async () => {
                    if (await run(() => write((levers) => ({ ...levers, goal: null, goal_choice: null })))) onDone();
                  }}
                >
                  {t.deleteYes}
                </button>
                <button type="button" className="secondary" disabled={busy} onClick={() => setDeleting(false)}>
                  {t.cancel}
                </button>
              </div>
            </div>
          ) : (
            <button type="button" className="secondary" onClick={() => setDeleting(true)}>
              {t.delete}
            </button>
          )}
        </div>
      ) : null}
    </form>
  );
}
