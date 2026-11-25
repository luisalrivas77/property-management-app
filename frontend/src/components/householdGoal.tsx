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
import { type FormEvent, type ReactNode, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { type Translation, useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { detailHref } from "@/lib/detailHash";
import { HOUSEHOLD_PLAY, HOUSEHOLD_RESERVE } from "@/lib/planningRoutes";

import { DetailPanel, useDetail } from "./DetailPanel";
import { todayIso } from "./format";
import { Field } from "./form";
import { HealthTodoList } from "./healthFigures";
import { currentDefault, fillLevers, type HouseholdLevers, type HouseholdScenario, loadScenarioHistory, saveDefaultScenario } from "./healthShared";
import { decimalInput, toDecimalString, toMoneyString } from "./input";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { Loading } from "./ui";
import { type Loaded, useLoad } from "./useLoad";
import { type Mark, VerdictDot } from "./verdict";

import "./householdGoal.css";

export type HouseholdGoal = components["schemas"]["HouseholdGoal"];
type Goal = components["schemas"]["Goal"];
type GoalKind = components["schemas"]["GoalKind"];
type GoalWord = components["schemas"]["GoalWord"];
type GoalScenario = components["schemas"]["GoalScenario"];
type GoalSpending = components["schemas"]["GoalSpending"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type GT = Translation<typeof goalText.de>;

/** The detail kind of the goal's panels: the form and one panel per spending scenario. */
export const GOAL_DETAIL = "ziel";
export const GOAL_FORM = "festlegen";
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

// D303: „Ehrgeizig“ comes only through route 3, which is not built yet.
const WORD_MARK: Record<GoalWord, Mark> = { none: "none", on_track: "green", feasible: "amber", ambitious: "amber", hardly: "red" };

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

function leftText(t: GT, months: number): string {
  return t.stillLeft(t.left(Math.floor(months / 12), months % 12));
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

/** The tile's body under „Euer Ziel“ (a set goal): the word with its dot, the target, the bar
 * and „nötig … heute …“ in one line. Spans only: it sits inside the tile's link. */
export function GoalTileBody({ goal }: { goal: HouseholdGoal }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const mark = goal.word ? WORD_MARK[goal.word] : "none";
  const percent = goal.percent != null ? p.percentValue(goal.percent) : null;
  return (
    <>
      <span className="tile-value tile-value-big tile-verdict">
        <span className="tile-dot" data-mark={mark} aria-hidden="true" />
        {goalWordText(t, goal)}
      </span>
      {goal.goal ? <span className="tile-sub">{goalTargetText(t, p, goal.goal)}</span> : null}
      {goal.percent != null ? (
        <span className="goal-bar goal-bar-tile" aria-hidden="true">
          <span style={{ width: barWidth(goal.percent) }} />
        </span>
      ) : null}
      {percent != null ? (
        <span className="tile-sub">
          {goal.noetig != null && goal.heute != null ? t.tileLine(percent, p.euro(goal.noetig), p.euro(goal.heute)) : t.percentReached(percent)}
        </span>
      ) : null}
    </>
  );
}

// --- the summary (Übersicht's panel and the Ziel tab) ------------------------------------------

function FormLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  // A hash of this page opens the panel in place; a path is another page.
  if (href.startsWith("#"))
    return (
      <a href={href} className={className}>
        {children}
      </a>
    );
  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

/** The goal's word, target, progress and figures, its sentences and, while it has no word, why
 * and what to do (principle 7: every gap with its button). */
export function GoalSummary({ goal, formHref, onChanged }: { goal: HouseholdGoal; formHref: string; onChanged: () => void }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  if (!goal.goal) return null;
  const mark = goal.word ? WORD_MARK[goal.word] : "none";
  const budgetName = goal.choice?.spending ? scenarioName(t, goal.choice.spending) : null;
  const due = p.month(goal.goal.due_month);
  return (
    <div className="stack goal-summary">
      <div className="verdict-word verdict-word-big">
        <p className="verdict-word-text">
          <VerdictDot mark={mark} />
          {goalWordText(t, goal)}
        </p>
        <p className="muted">{goalTargetText(t, p, goal.goal)}</p>
      </div>
      {goal.percent != null ? (
        <div className="goal-progress">
          <p className="goal-progress-line">
            <strong>{t.percentReached(p.percentValue(goal.percent))}</strong>
            <span className="muted">
              {t.today(p.euro(goal.value_today))}
              {goal.target != null ? ` · ${t.targetIs(p.euro(goal.target))}` : ""}
              {goal.months_left != null ? ` · ${leftText(t, goal.months_left)}` : ""}
            </span>
          </p>
          <div className="goal-bar" role="presentation">
            <span style={{ width: barWidth(goal.percent) }} />
          </div>
        </div>
      ) : null}
      {goal.noetig != null || goal.heute != null ? (
        <div className="goal-figures">
          {goal.noetig != null ? (
            <div className="goal-figure">
              <span className="goal-figure-label">{t.needed}</span>
              <span className="goal-figure-value">{p.euro(goal.noetig)}</span>
              <span className="muted goal-figure-sub">{t.neededSub}</span>
            </div>
          ) : null}
          {goal.heute != null ? (
            <div className="goal-figure">
              <span className="goal-figure-label">{t.saving}</span>
              <span className="goal-figure-value">{p.euro(goal.heute)}</span>
              <span className="muted goal-figure-sub">{t.savingSub}</span>
            </div>
          ) : null}
        </div>
      ) : null}
      {goal.word != null && goal.word !== "none" ? (
        <p>{goal.reached ? t.reachedAsIs(p.month(goal.reached)) : t.notReachedAsIs}</p>
      ) : null}
      {budgetName && goal.word != null && goal.word !== "none" ? (
        <p>{goal.reached_with_budget ? t.reachedWithBudget(budgetName, p.month(goal.reached_with_budget)) : t.notReachedWithBudget(budgetName)}</p>
      ) : null}
      {goal.word === "hardly" && goal.unreachable ? (
        <div className="notice notice-warn goal-unreachable">
          <p>
            <strong>{t.unreachableTitle}</strong>
          </p>
          <p>{t.unreachableByDue(due, p.euro(goal.unreachable.reaches_by_due))}</p>
          <p>{goal.unreachable.later ? t.unreachableLater(p.month(goal.unreachable.later)) : t.unreachableNever}</p>
          <p>{t.unreachableWhat}</p>
        </div>
      ) : null}
      {(goal.assumptions ?? []).map((a) => (
        <p key={a.text} className="muted goal-assumption">
          {a.text}{" "}
          <Link href={a.href}>{a.action}</Link>
        </p>
      ))}
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
              <p className="tile-panel-kicker">{t.waiting}</p>
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
}: {
  enabled: boolean;
  backLabel: string;
  enough: boolean;
  dirty: boolean;
  onWritten: () => void;
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

  const tab = enabled ? <GoalTab state={state} enough={enough} dirty={dirty} write={write} /> : null;
  return { panel, tab };
}

function GoalTab({
  state,
  enough,
  dirty,
  write,
}: {
  state: Loaded<GoalData | null>;
  enough: boolean;
  dirty: boolean;
  write: Write;
}) {
  const t = useT(goalText);
  const detail = useDetail(GOAL_DETAIL);
  const ids = useId();
  return (
    <Loading state={state}>
      {(data) =>
        data ? (
          <div className="stack goal-tab">
            <section className="card goal-card" aria-labelledby={`${ids}-title`}>
              <div className="goal-card-head">
                <h2 id={`${ids}-title`}>{t.title}</h2>
                {data.goal.goal ? (
                  <a {...detail.linkProps(GOAL_FORM)} className="button-link secondary">
                    {t.changeGoal}
                  </a>
                ) : null}
              </div>
              {data.goal.word === "none" || !data.goal.goal ? (
                <div className="stack">
                  <p className="muted">{t.noGoalText}</p>
                  <p>
                    <a {...detail.linkProps(GOAL_FORM)} className="button-link">
                      {t.setGoal}
                    </a>
                  </p>
                </div>
              ) : (
                <GoalSummary goal={data.goal} formHref={detail.href(GOAL_FORM)} onChanged={state.reload} />
              )}
            </section>
            {(data.goal.scenarios ?? []).length > 0 ? (
              <Scenarios data={data} enough={enough} dirty={dirty} write={write} />
            ) : null}
          </div>
        ) : null
      }
    </Loading>
  );
}

function Scenarios({ data, enough, dirty, write }: { data: GoalData; enough: boolean; dirty: boolean; write: Write }) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const ids = useId();
  const detail = useDetail(GOAL_DETAIL);
  const goal = data.goal;
  const chosen = goal.choice?.spending ?? null;
  const scenarios = SCENARIO_ORDER.map((kind) => (goal.scenarios ?? []).find((s) => s.kind === kind)).filter((s): s is GoalScenario => s != null);
  const saved = data.current?.levers?.spending_changes ?? [];
  const due = goal.goal ? p.month(goal.goal.due_month) : "";
  return (
    <section className="card goal-scenarios" aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>{t.scenariosTitle}</h2>
      <p className="muted">{t.scenariosIntro}</p>
      <ul className="goal-scenario-rows">
        {scenarios.map((s) => (
          <li key={s.kind}>
            <a {...detail.linkProps(s.kind)} className="goal-scenario-row">
              <span className="goal-scenario-text">
                <span className="goal-scenario-title">
                  {scenarioName(t, s.kind)}
                  {chosen === s.kind ? <span className="badge">{t.yourBudget}</span> : null}
                </span>
                <span className="muted">{s.kind === "wants_only" ? t.wantsOnlySub : t.allEqualSub}</span>
                <span className="muted">
                  {t.cutLine(p.money(s.cut))} · {s.closes ? t.closesText(due) : t.notClosesText(due)}
                </span>
              </span>
              <span className={s.closes ? "badge" : "badge badge-warn"}>{s.closes ? t.closes : t.notCloses}</span>
            </a>
          </li>
        ))}
        <li className="goal-scenario-own">
          <div className="goal-scenario-text">
            <span className="goal-scenario-title">
              {t.own}
              {chosen === "own" ? <span className="badge">{t.yourBudget}</span> : null}
            </span>
            <span className="muted">{t.ownSub}</span>
            {enough ? (
              <>
                <p className="muted">{saved.length > 0 ? t.ownMissing : t.ownNoChanges}</p>
                <p className="row">
                  <Link href={HOUSEHOLD_PLAY} className="button-link secondary">
                    {t.own} ›
                  </Link>
                </p>
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
              <p className="muted">{t.ownNeedsMonths}</p>
            )}
          </div>
        </li>
      </ul>
    </section>
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
