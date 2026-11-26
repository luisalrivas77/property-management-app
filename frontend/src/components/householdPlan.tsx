"use client";

// The household plan in its places since phase 10 wave 3 (a) (§7.9 (a), D288; mockups 19b,
// 19c): Planung left the menu, and what its five sections showed now sits where it is used.
// Haushalt › Budget is components/planningBudget.tsx with „Was übrig bleibt“ at its foot
// (what Planung › Anlegen was: free cash, the buffer, „Anlegbar pro Monat“, the savings plans,
// „So entsteht die Zahl“, and the AI explanation behind „Erklärung“); Haushalt › Reserve is the
// reserve, its target, its accounts and the paths to it; the net worth on typed rates (what
// Planung › Vermögen was) is a card on Anlagen › Übersicht; the property's Planung is Anlagen ›
// Immobilie (app/(app)/assets/Property.tsx).
//
// The levers (§7.10 (a), D299; mockup 19e): the panel „Annahmen“ is gone. Spending, income and
// one-offs are played through on Haushalt › Durchspielen (components/householdPlay.tsx), whose
// draft lives in `PlanProvider` beside the Budget class panel's own draft, so no other place moves
// before „Als Plan behalten“. Every other lever sits beside the figure it acts on and saves on its
// own as a new version of the default (D248, append-only): the reserve target and its accounts on
// Reserve (`#reserve-ziel`), the buffer and the savings plans on Budget (`#puffer`), the rates on
// Anlagen (`#rendite`). Every figure is the server's (GET /household-plan, /household-budget);
// while the Budget class panel's draft differs, each place shows its figures from POST
// /household-plan/preview, marked as not saved.
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  createContext,
  type FormEvent,
  type ReactNode,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { monthBudget } from "@/i18n/monthBudget";
import { goalText } from "@/i18n/goal";
import { lineLabel, planning, type PlanningText } from "@/i18n/planning";
import { playText } from "@/i18n/play";
import { ErrorNotice } from "@/lib/ErrorNotice";
import {
  ASSUMPTIONS,
  BUFFER_LEVER,
  HOUSEHOLD_BUDGET,
  HOUSEHOLD_PLAY,
  HOUSEHOLD_RESERVE,
  planTodoTarget,
  PROJECTION_ANCHOR,
  propertyPlanningHref,
  RATE_LEVER,
  RESERVE_LEVER,
  REST_ANCHOR,
  staleAssumptionsTarget,
} from "@/lib/planningRoutes";

import { DetailLayout, DetailPanel, useDetail } from "./DetailPanel";
import { Field } from "./form";
import { todayIso } from "./format";
import { HealthTodoItem } from "./healthFigures";
import {
  currentDefault,
  fillLevers,
  type Health,
  type HealthMonthView,
  type HealthTodo,
  type HouseholdLevers,
  type HouseholdPlan,
  type HouseholdScenario,
  loadMemberNames,
  loadScenarioHistory,
  monthEnd,
  saveDefaultScenario,
  toSignedRate,
} from "./healthShared";
import { percentInput, toSignedMoneyString } from "./input";
import { ALLOCATION_CATEGORY } from "./labels";
import { CardHead, type GoalView, Stat, Stats, useGoalView } from "./householdGoal";
import { PlanExplanationPanel } from "./planExplanation";
import { applyPlay, NO_PLAY, plannedCount, type PlayChanges } from "./playDraft";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { OneOffSheet } from "./oneOffSheet";
import { BudgetClassPanel, BudgetSection, type BudgetView, GoalsPanel, ONE_OFF_SHEET, PlanError } from "./planningBudget";
import { type PlanPreview, stretch, usePlanPreview } from "./spendingPlay";
import { Loading } from "./ui";
import { type Loaded, useLoad } from "./useLoad";

import "./health.css";
import "./planningSections.css";

type BudgetClass = components["schemas"]["BudgetClass"];
type Account = components["schemas"]["Account"];
type SavingsPlan = components["schemas"]["SavingsPlan"];
type SurplusKey = components["schemas"]["SurplusKey"];
type ProjectionPoint = components["schemas"]["ProjectionPoint"];
type AdviceMode = components["schemas"]["AdviceMode"];
type AllocationCategory = components["schemas"]["AllocationCategory"];

export interface PlanData {
  plan: HouseholdPlan;
  health: Health;
  budget: BudgetView;
  scenarios: HouseholdScenario[];
  accounts: Account[];
  names: Map<string, string>;
  months: HealthMonthView[];
  /** workspace.advice_mode (D251), from GET /workspace/settings: GET /household-plan does not
   * carry it. Under `generic` no plan and no plan's account is named on this page (§10). */
  mode: AdviceMode;
}

async function loadPlan(): Promise<PlanData> {
  const [plan, health, budget, scenarios, accounts, names, settings] = await Promise.all([
    ok(api.GET("/api/household-plan")),
    ok(api.GET("/api/health")),
    ok(api.GET("/api/household-budget")),
    loadScenarioHistory(),
    ok(api.GET("/api/accounts")),
    loadMemberNames(),
    ok(api.GET("/api/workspace/settings")),
  ]);
  const closedMonths = health.window.months ?? [];
  const first = closedMonths[0];
  const last = closedMonths[closedMonths.length - 1];
  const months = first && last ? await ok(api.GET("/api/health/months", { params: { query: { from: first, to: last } } })) : [];
  return { plan, health, budget, scenarios, accounts, names, months, mode: settings.advice_mode };
}

/** The draft of the levers. `null` follows the saved scenario; after a save the posted levers
 * stand until the reload brings the row saved (`resetOn`), then the saved ones again, so the
 * server's own additions (a spending change's mean_at_save) never read as a change. */
interface DraftState {
  levers: HouseholdLevers;
  resetOn?: string;
}

function resolveDraft(state: DraftState | null, plan: HouseholdPlan | undefined): HouseholdLevers {
  const saved = fillLevers(plan?.levers);
  if (!state) return saved;
  if (state.resetOn !== undefined && state.resetOn === plan?.scenario_id) return saved;
  return state.levers;
}

type T = PlanningText;

/** The levers „Als Plan behalten“ writes (§7.10 (a) 3) and the goal's choice it may switch. */
const PLAY_KEYS = ["spending_changes", "income_changes", "one_offs", "goal_choice"] as const;

/** The spending changes as the household set them (code, amount or percent, months), in one
 * order and one number format, so a save that leaves them as they are (the server's
 * mean_at_save, another order, "180" against "180.00") never reads as a change of an adopted
 * budget (§7.9 (d)). */
function spendingKey(changes: components["schemas"]["SpendingChange"][] | null | undefined): string {
  const num = (v: string | null | undefined) => (v == null || v === "" ? "" : String(Number(v)));
  return (changes ?? [])
    .map((c) => [c.code, num(c.monthly), num(c.percent), c.from_month, c.to_month ?? ""].join("|"))
    .sort()
    .join(";");
}

/** One line per changed lever area, for the scenario row's count and the panel's list. */
function draftChanges(
  saved: HouseholdLevers,
  draft: HouseholdLevers,
  t: T,
  short: (v: string) => string,
  percent: (v: string) => string,
  defaults: Map<string, BudgetClass>,
): string[] {
  const out: string[] = [];
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  if (saved.reserve_target_months !== draft.reserve_target_months) out.push(t.changeReserveTarget(short(draft.reserve_target_months)));
  if (saved.buffer_percent !== draft.buffer_percent) out.push(t.changeBuffer(percent(draft.buffer_percent)));
  if (saved.horizon_months !== draft.horizon_months) out.push(t.changeHorizon(draft.horizon_months ?? 60));
  if (!same(saved.reserve_accounts, draft.reserve_accounts)) out.push(t.changeReserveAccounts);
  if (!same(saved.plan_overrides, draft.plan_overrides)) out.push(t.changePlanOverrides);
  if (!same(saved.income_changes, draft.income_changes)) out.push(t.changeIncomeChanges);
  if (!same(saved.one_offs, draft.one_offs)) out.push(t.changeOneOffs);
  if (saved.value_change?.depot !== draft.value_change?.depot) out.push(t.changeValueDepot);
  if (saved.value_change?.crypto !== draft.value_change?.crypto) out.push(t.changeValueCrypto);
  if (saved.net_worth_years !== draft.net_worth_years) out.push(t.changeYears(draft.net_worth_years ?? 10));
  const before = new Map((saved.spending_changes ?? []).map((c) => [c.code, c]));
  const after = new Map((draft.spending_changes ?? []).map((c) => [c.code, c]));
  for (const [code, change] of after) {
    const old = before.get(code);
    const strip = (c: typeof change) => ({ ...c, mean_at_save: null });
    if (!old || !same(strip(old), strip(change))) out.push(t.changeSpending(categoryName(code)));
  }
  for (const code of before.keys()) if (!after.has(code)) out.push(t.changeSpendingRemoved(categoryName(code)));
  const moved = new Set([...Object.keys(saved.budget_class_overrides ?? {}), ...Object.keys(draft.budget_class_overrides ?? {})]);
  for (const code of moved) {
    const now = draft.budget_class_overrides?.[code];
    if (saved.budget_class_overrides?.[code] !== now) {
      // A removed override moves the code back to its default class.
      const cls = (now as BudgetClass | undefined) ?? defaults.get(code);
      out.push(t.changeClassMove(categoryName(code), cls ? lineLabel(t, cls) : "–"));
    }
  }
  if (!same(saved.budget_targets, draft.budget_targets)) out.push(t.changeTargets);
  return out;
}

/** Each budget code's class without an override, from GET /household-budget. */
function defaultClasses(budget: BudgetView): Map<string, BudgetClass> {
  const out = new Map<string, BudgetClass>();
  for (const line of budget.lines ?? []) {
    for (const code of line.codes ?? []) if (code.default_class) out.set(code.category, code.default_class);
  }
  return out;
}

function categoryName(code: string): string {
  return ALLOCATION_CATEGORY[code as AllocationCategory] ?? code;
}

export type SetDraft = (update: (previous: HouseholdLevers) => HouseholdLevers) => void;

// --- the plan's state, shared by the places that show it ------------------------------------

/** Haushalt › Durchspielen's draft (§7.10 (a), D299): changes on top of the saved levers, kept
 * apart from the draft above, so no other place moves before „Als Plan behalten“. It lives in the
 * provider, so it survives a switch to Budget, Reserve or Ziel and back. */
export interface PlayState {
  changes: PlayChanges;
  set: (update: (previous: PlayChanges) => PlayChanges) => void;
  /** Stores the saved levers (read again) with the changes as the new default. */
  keep: () => Promise<void>;
  reset: () => void;
  busy: boolean;
  error: unknown;
  /** Moves when the draft is replaced as a whole (reset, kept, „Für alle übernehmen“), so the
   * fields read it again. */
  round: number;
  bump: () => void;
}

/** Stores the saved levers (read again) with `keys` taken from `levers`, as the new default: a
 * lever saved from its place (§7.10 (a) 7), never with either draft. */
export type SaveLevers = (keys: readonly (keyof HouseholdLevers)[], levers: HouseholdLevers) => Promise<void>;

/** The plan once at least three months are closed (12c otherwise). */
export interface PlanReady {
  /** The saved plan and what was read with it. */
  data: PlanData;
  /** The same with the draft's plan from the preview: what a place shows. */
  shown: PlanData;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  changes: string[];
  dirty: boolean;
  preview: PlanPreview;
  saveBar: ReactNode;
  /** Moves on a save or a discard, so every field reads the stored value back. */
  epoch: number;
  current: HouseholdScenario | null;
  onChanged: () => void;
  play: PlayState;
  saveLevers: SaveLevers;
}

interface PlanState {
  state: Loaded<PlanData>;
  ready: PlanReady | null;
  /** The page this provider sits on (`/household?tab=budget`, `/assets`): a to-do whose lever
   * sits on this page opens it here instead of leaving it. */
  page: string;
  /** Haushalt › Ziel's goal (§7.9 (d)), or null on a page without it. */
  goal: GoalView | null;
}

const PlanContext = createContext<PlanState | null>(null);

export function usePlan(): PlanState {
  const value = useContext(PlanContext);
  if (!value) throw new Error("PlanProvider missing");
  return value;
}

/** Stands in for the saved plan until it is read; the preview stays idle then (no draft, no
 * spending change), so it is never sent or shown. */
const NO_PLAN = {} as HouseholdPlan;
/** The levers with nothing changed, for the idle preview before the read. */
const EMPTY_LEVERS = fillLevers(undefined);

/** What each lever beside its figure saves (§7.10 (a) 7). */
// The reserve target, its accounts and the reserve's yearly rate (D304, typed in the same form;
// the form changes only `value_change.reserve`, the depot and crypto rates are kept as read).
const RESERVE_KEYS = ["reserve_target_months", "reserve_accounts", "value_change"] as const;
const BUFFER_KEYS = ["buffer_percent", "plan_overrides"] as const;
const RATE_KEYS = ["value_change", "net_worth_years"] as const;

/** Reads the plan once for a page and holds Durchspielen's draft, the Budget class panel's draft
 * and its save, and Budget's own panels; `backLabel` is the page's name on the panel's „‹“ on the phone. The tree
 * around `children` is the same before and after the read, so nothing under it mounts twice. */
/** A link from before §7.10 (a) (`#d-annahmen-<group>`, a bookmark) leads to the group's new
 * place, on every tab of the page that calls it (Haushalt, Anlagen), not only where the plan is
 * read; on the same page only the hash changes, so the browser's hashchange opens the lever. */
export function useStaleAssumptionsRedirect(page: string) {
  const annahmen = useDetail(ASSUMPTIONS);
  const router = useRouter();
  useEffect(() => {
    if (annahmen.id === null) return;
    const target = staleAssumptionsTarget(`#d-${ASSUMPTIONS}-${annahmen.id}`, page);
    if (target === null) return;
    const [path] = target.split("#", 1);
    if (path === window.location.pathname + window.location.search) window.location.replace(target);
    else router.replace(target);
  }, [annahmen.id, page, router]);
}

export function PlanProvider({
  page,
  backLabel,
  goal = false,
  children,
}: {
  page: string;
  backLabel: string;
  /** Read the household goal for Haushalt › Ziel (§7.9 (d)). */
  goal?: boolean;
  children: ReactNode;
}) {
  const t = useT(planning);
  const f = usePlanFormat();
  const state = useLoad(loadPlan);
  const [draftState, setDraftState] = useState<DraftState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // A discard or a save remounts the fields, so none keeps a text the draft no longer holds.
  const [epoch, setEpoch] = useState(0);
  const [play, setPlay] = useState<PlayChanges>(NO_PLAY);
  const [playBusy, setPlayBusy] = useState(false);
  const [playError, setPlayError] = useState<unknown>(null);
  const [playRound, setPlayRound] = useState(0);
  const goals = useDetail("goals");
  const budgetClass = useDetail("budget");
  const reserveCard = useDetail(RESERVE_CARD);

  const data = state.data;
  const enough = data !== undefined && data.health.window.enough && data.plan.surplus != null;
  const draft = resolveDraft(draftState, data?.plan);
  const changes =
    data && enough ? draftChanges(fillLevers(data.plan.levers), draft, t, f.short, f.percentValue, defaultClasses(data.budget)) : [];
  const dirty = changes.length > 0;
  // D263: every draft goes through the preview while a member types; each place shows its
  // figures from it (D288).
  const preview = usePlanPreview(enough ? data.plan : NO_PLAN, enough ? draft : EMPTY_LEVERS, dirty);
  const setDraft: SetDraft = (update) => setDraftState((previous) => ({ levers: update(resolveDraft(previous, data?.plan)) }));
  const current = data ? currentDefault(data.scenarios) : null;
  // The goal writes the default scenario itself (its own read of the history): the plan is read
  // again, and a draft with no change follows the new row instead of reading as changed.
  const goalView = useGoalView({
    enabled: goal,
    backLabel,
    enough,
    dirty,
    onWritten: () => {
      state.reload();
      if (!dirty) {
        setDraftState(null);
        setEpoch((value) => value + 1);
      }
    },
    info: {
      plan: data?.plan ?? null,
      mode: data?.mode,
      // „In Durchspielen ansehen“ (mockup 19f): the balanced route's cuts as Durchspielen's draft.
      playSpending: enough
        ? (spending) => {
            setPlay((previous) => ({
              ...previous,
              spending: { ...previous.spending, ...Object.fromEntries(spending.map((change) => [change.code, change])) },
            }));
            setPlayRound((n) => n + 1);
          }
        : null,
    },
  });

  let ready: PlanReady | null = null;
  let panel: ReactNode = null;
  if (data && enough) {
    const { plan } = data;

    const save = async () => {
      setBusy(true);
      setError(null);
      try {
        // The limits of the key figures stay as the default holds them (they are set on Haushalt
        // since phase 10 §7.6), and so do the goal and its choice (set on Haushalt › Ziel, §7.9
        // (d)). Spending changes saved over an adopted scenario's are the household's own choice.
        const savedLevers = current?.levers;
        const choice = savedLevers?.goal_choice ?? null;
        const spendingMoved = spendingKey(draft.spending_changes) !== spendingKey(savedLevers?.spending_changes);
        const posted = {
          ...draft,
          bands: savedLevers?.bands ?? draft.bands ?? [],
          goal: savedLevers?.goal ?? null,
          goal_choice:
            choice?.spending && choice.spending !== "own" && spendingMoved
              ? { ...choice, spending: "own" as const, spending_chosen_on: todayIso() }
              : choice,
        };
        const row = await saveDefaultScenario(posted, current);
        setDraftState({ levers: posted, resetOn: row.id });
        state.reload();
        setEpoch((value) => value + 1);
      } catch (caught) {
        setError(caught);
      } finally {
        setBusy(false);
      }
    };

    const saveBar = (
      <div className="stack">
        {error != null ? <PlanError error={error} /> : null}
        <div className="row">
          <button type="button" disabled={busy || !dirty} onClick={() => void save()}>
            {t.saveScenario}
          </button>
          {dirty ? (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => {
                setDraftState(null);
                setEpoch((value) => value + 1);
              }}
            >
              {t.discard}
            </button>
          ) : null}
          <span className="muted">{t.saveHint}</span>
        </div>
      </div>
    );

    /** The levers just stored also become the draft's, so a later save of the draft (the
     * Budget's class panel) never writes the old value back; with no other change the draft
     * follows the new row once it is read. */
    const adopt = (levers: HouseholdLevers, keys: readonly (keyof HouseholdLevers)[], row: HouseholdScenario) => {
      setDraftState((previous) => {
        if (previous === null) return null;
        const merged: HouseholdLevers = { ...resolveDraft(previous, data.plan) };
        for (const key of keys) Object.assign(merged, { [key]: levers[key] });
        return { levers: merged, ...(dirty ? {} : { resetOn: row.id }) };
      });
    };

    /** The default scenario as stored now, read again, so a lever saved in another tab since the
     * page was read is kept; with none stored yet, the levers the served plan used (what „Heute“
     * shows), so a first save never drops them. */
    const storedDefault = async () => {
      const row = currentDefault(await loadScenarioHistory());
      return { row, levers: fillLevers(row ? row.levers : data.plan.levers) };
    };

    const saveLevers: SaveLevers = async (keys, levers) => {
      const stored = await storedDefault();
      const posted: HouseholdLevers = { ...stored.levers };
      for (const key of keys) Object.assign(posted, { [key]: levers[key] });
      const row = await saveDefaultScenario(posted, stored.row);
      adopt(levers, keys, row);
      state.reload();
    };

    const keep = async () => {
      setPlayBusy(true);
      setPlayError(null);
      try {
        const stored = await storedDefault();
        const posted = applyPlay(stored.levers, play, surplusRow(data.plan, "mean_income")?.amount ?? null);
        const choice = stored.levers.goal_choice ?? null;
        const spendingMoved = spendingKey(posted.spending_changes) !== spendingKey(stored.levers.spending_changes);
        // As the save above: spending saved over an adopted scenario's is the household's own.
        const levers: HouseholdLevers = {
          ...posted,
          goal_choice:
            choice?.spending && choice.spending !== "own" && spendingMoved
              ? { ...choice, spending: "own" as const, spending_chosen_on: todayIso() }
              : choice,
        };
        const row = await saveDefaultScenario(levers, stored.row);
        setPlay(NO_PLAY);
        setPlayRound((n) => n + 1);
        adopt(levers, PLAY_KEYS, row);
        state.reload();
      } catch (caught) {
        setPlayError(caught);
      } finally {
        setPlayBusy(false);
      }
    };

    ready = {
      data,
      shown: dirty ? { ...data, plan: preview.withChanges } : data,
      draft,
      setDraft,
      changes,
      dirty,
      preview,
      saveBar,
      epoch,
      current,
      onChanged: state.reload,
      play: {
        changes: play,
        set: setPlay,
        keep,
        reset: () => {
          setPlay(NO_PLAY);
          setPlayRound((n) => n + 1);
          setPlayError(null);
        },
        busy: playBusy,
        round: playRound,
        bump: () => setPlayRound((n) => n + 1),
        error: playError,
      },
      saveLevers,
    };

    if (goals.id === ONE_OFF_SHEET) {
      // „Was als einmalig zählt“ (§7.12 (a), 19h frame 3): each switch stores a row; an unsaved
      // draft takes the new switches, so its own save never puts the old ones back.
      panel = (
        <OneOffSheet
          saved={fillLevers(plan.levers)}
          current={current}
          onClose={goals.close}
          onSaved={(row) => {
            setDraftState((previous) => ({
              levers: { ...resolveDraft(previous, data.plan), one_off_overrides: row.levers?.one_off_overrides ?? {} },
              ...(dirty ? {} : { resetOn: row.id }),
            }));
            state.reload();
          }}
        />
      );
    } else if (goals.id !== null) {
      panel = (
        <GoalsPanel
          budget={data.budget}
          saved={fillLevers(plan.levers)}
          current={current}
          onClose={goals.close}
          onSaved={(targets, row) => {
            // Other unsaved changes stay a draft; with none the page follows the new row.
            const onlyTargets = changes.length === 0 || (changes.length === 1 && changes[0] === t.changeTargets);
            setDraftState((previous) => ({
              levers: { ...resolveDraft(previous, data.plan), budget_targets: targets },
              ...(onlyTargets ? { resetOn: row.id } : {}),
            }));
            state.reload();
            goals.close();
          }}
        />
      );
    } else if (budgetClass.id !== null) {
      panel = (
        <BudgetClassPanel
          key={`${budgetClass.id}-${epoch}`}
          lineKey={budgetClass.id}
          budget={data.budget}
          draft={draft}
          setDraft={setDraft}
          onClose={budgetClass.close}
          onChanged={state.reload}
          footer={dirty ? saveBar : null}
        />
      );
    } else if (isReserveCard(reserveCard.id)) {
      panel = <ReserveCardPanel id={reserveCard.id} data={ready.shown} backLabel={backLabel} onClose={reserveCard.close} />;
    }
  }
  if (panel === null && goalView.panel) panel = goalView.panel;

  return (
    <PlanContext.Provider value={{ state, ready, page, goal: goal ? goalView : null }}>
      <DetailLayout panel={panel}>{children}</DetailLayout>
    </PlanContext.Provider>
  );
}

// --- the notices and to-dos every place shares ---------------------------------------------

/** While a draft differs: what the figures of this place count, whether the preview is still
 * calculating or was refused (the figures then stay at its last good answer), and the save. */
function DirtyNotice({ text }: { text: string }) {
  const t = useT(planning);
  const { ready } = usePlan();
  if (!ready?.dirty) return null;
  return (
    <div className="stack" aria-live="polite">
      <p className="notice notice-warn" role="status">
        {text}
        {ready.preview.pending ? t.previewPending : ""}
      </p>
      {ready.preview.error != null ? <ErrorNotice error={ready.preview.error} /> : null}
      {ready.saveBar}
    </div>
  );
}

/** A to-do of the plan: one whose server href (rules/plan/, D271) leads to a lever on this page
 * opens it here (a plain anchor, so the browser's hashchange opens the lever); every other one is
 * HealthTodoItem's link, which maps an old Planung href. */
function PlanTodos({ todos, onChanged }: { todos: HealthTodo[]; onChanged: () => void }) {
  const { page } = usePlan();
  if (todos.length === 0) return null;
  const prefix = `${page}#`;
  return (
    <ul className="health-todos">
      {todos.map((todo, index) => {
        const key = `${todo.code}-${index}`;
        const target = planTodoTarget(todo.href, todo.code, page);
        if (!target.startsWith(prefix)) return <HealthTodoItem key={key} todo={todo} onChanged={onChanged} />;
        return (
          <li key={key} className="health-todo">
            <span>{todo.text}</span>
            <a href={target.slice(page.length)} className="button-link secondary">
              {todo.action}
            </a>
          </li>
        );
      })}
    </ul>
  );
}

/** §7.10 (a) 6: once the saved plan holds planned changes (income, spending, one-offs), the
 * places that count them say so, with the way to Durchspielen, where they are seen and changed. */
export function PlannedChanges({ levers }: { levers: HouseholdLevers | null | undefined }) {
  const p = useT(playText);
  const count = plannedCount(levers);
  if (count === 0) return null;
  return (
    <p className="muted planning-planned" data-testid="planned-changes">
      {p.planned(count)} · <Link href={HOUSEHOLD_PLAY}>{p.plannedOpen} ›</Link>
    </p>
  );
}

// --- the levers beside their figures (§7.10 (a) 7) ------------------------------------------

function subscribeHash(listener: () => void): () => void {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
  };
}

/** The URL's hash, read again on every render too, so a router navigation that only pushed a
 * state (a link from another tab) is seen as well as a plain anchor's hashchange. */
function useLocationHash(): string {
  return useSyncExternalStore(subscribeHash, () => window.location.hash, () => "");
}

/** The link that opens a lever: a plain anchor to its hash on this page. */
function LeverLink({ anchor, children }: { anchor: string; children: ReactNode }) {
  return <a href={`#${anchor}`}>{children}</a>;
}

type LeverFields = (levers: HouseholdLevers, setLevers: SetDraft, setInvalid: (invalid: boolean) => void) => ReactNode;

/** A lever beside the figure it acts on (§7.10 (a) 7, D299): open while the URL's hash names it
 * (`#reserve-ziel`, `#puffer`, `#rendite`), so a link from any page or tab opens it; its form
 * holds a copy of the saved levers, and „Speichern“ stores only `keys` as a new version of the
 * default scenario (D248), over the default as stored now (`saveLevers`). Durchspielen's draft and
 * the Budget class panel's draft stay as they are. */
function InlineLever({
  anchor,
  title,
  keys,
  ready,
  children,
}: {
  anchor: string;
  title: string;
  keys: readonly (keyof HouseholdLevers)[];
  ready: PlanReady;
  children: LeverFields;
}) {
  const p = useT(playText);
  const hash = useLocationHash();
  const open = hash === `#${anchor}`;
  const [saved, setSaved] = useState(false);
  const close = () => {
    if (window.location.hash !== `#${anchor}`) return;
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  };
  return (
    <div id={anchor} className="planning-inline-lever">
      {open ? (
        <LeverForm
          title={title}
          keys={keys}
          ready={ready}
          onCancel={close}
          onSaved={() => {
            setSaved(true);
            close();
          }}
        >
          {children}
        </LeverForm>
      ) : saved ? (
        <p className="muted" role="status">
          {p.leverSavedNote}
        </p>
      ) : null}
    </div>
  );
}

function LeverForm({
  title,
  keys,
  ready,
  onCancel,
  onSaved,
  children,
}: {
  title: string;
  keys: readonly (keyof HouseholdLevers)[];
  ready: PlanReady;
  onCancel: () => void;
  onSaved: () => void;
  children: LeverFields;
}) {
  const p = useT(playText);
  const ids = useId();
  const ref = useRef<HTMLFormElement>(null);
  const [initial] = useState(() => fillLevers(ready.data.plan.levers));
  const [levers, setLevers] = useState(initial);
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const changed = keys.some((key) => JSON.stringify(levers[key]) !== JSON.stringify(initial[key]));

  // Opened by a link from another page, the form appears only once the plan is read, after the
  // browser looked for the anchor: it is scrolled to here.
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: "nearest" });
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await ready.saveLevers(keys, levers);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={ref} className="planning-lever-form stack" aria-labelledby={`${ids}-title`} onSubmit={(event) => void submit(event)}>
      <h3 id={`${ids}-title`}>{title}</h3>
      {children(levers, setLevers, setInvalid)}
      {error != null ? <PlanError error={error} /> : null}
      <div className="row">
        <button type="submit" disabled={busy || invalid || !changed}>
          {busy ? p.saving : p.save}
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
          {p.cancel}
        </button>
      </div>
    </form>
  );
}

// --- the Haushalt tabs Budget and Reserve ----------------------------------------------------

/** Haushalt › Budget or Haushalt › Reserve (D288), inside a `PlanProvider`. */
export function HouseholdPlanTab({ tab }: { tab: "budget" | "reserve" }) {
  const { state, ready } = usePlan();
  return (
    <Loading state={state}>
      {(loaded) =>
        ready ? (
          <div className="stack planning" key={ready.epoch}>
            {tab === "budget" ? <BudgetTab ready={ready} /> : <ReserveTab ready={ready} />}
          </div>
        ) : (
          <TooFewMonths data={loaded} onChanged={state.reload} />
        )
      }
    </Loading>
  );
}

/** Haushalt › Ziel (§7.9 (d)): the goal reads its own answer, so it shows before three closed
 * months too (it then says what the projection still needs); an unsaved draft of the Budget
 * class panel gets the save bar, since the goal counts the saved levers only. */
export function HouseholdGoalTab() {
  const g = useT(goalText);
  const { goal, state } = usePlan();
  return (
    <div className="stack planning">
      <PlannedChanges levers={state.data?.plan.levers} />
      <DirtyNotice text={g.dirtyGoal} />
      {goal?.tab}
    </div>
  );
}

function BudgetTab({ ready }: { ready: PlanReady }) {
  const t = useT(planning);
  return (
    <>
      <PlannedChanges levers={ready.data.plan.levers} />
      <DirtyNotice text={t.dirtyNoticeBudget} />
      <BudgetSection budget={ready.data.budget} savingPlans={ready.data.plan.already_invested ?? null} onChanged={ready.onChanged} />
      <RestCard ready={ready} />
    </>
  );
}

function ReserveTab({ ready }: { ready: PlanReady }) {
  const t = useT(planning);
  const p = useT(playText);
  const f = usePlanFormat();
  const { shown, onChanged } = ready;
  const closedMonths = shown.health.window.months ?? [];
  const newest = shown.plan.newest_closed ?? closedMonths[closedMonths.length - 1] ?? "";
  const todos = (shown.plan.todos ?? []).filter((todo) => todo.code !== "rate_missing");
  return (
    <>
      <PlannedChanges levers={ready.data.plan.levers} />
      <p className="muted">
        {t.baseLine(closedMonths.length, f.monthRange(closedMonths))}
        {t.basePaths(f.longDate(newest ? monthEnd(newest) : null))}{" "}
        <Link href={`/household?monat=${newest}`}>{t.baseMonths}</Link>
      </p>
      <PlanTodos todos={todos} onChanged={onChanged} />
      <DirtyNotice text={t.dirtyNotice} />
      <ReserveCards data={shown} />
      <InlineLever anchor={RESERVE_LEVER} title={p.changeReserve} keys={RESERVE_KEYS} ready={ready}>
        {(levers, setLevers, setInvalid) => (
          <>
            <ReserveLever data={ready.data} draft={levers} setDraft={setLevers} />
            <ReserveAccountsBlock data={ready.data} draft={levers} setDraft={setLevers} />
            <ReserveRateField draft={levers} setDraft={setLevers} onValidity={setInvalid} />
          </>
        )}
      </InlineLever>
      <Paths data={shown} />
    </>
  );
}

/** What was Planung › Anlegen, at the foot of Haushalt › Budget (§7.9 (a) 3): from the money left
 * each month down to what is still free, each figure the server's; the explanation and the
 * figures' sources behind their buttons. */
function RestCard({ ready }: { ready: PlanReady }) {
  const t = useT(planning);
  const m = useT(monthBudget);
  const f = usePlanFormat();
  const p = useT(playText);
  const [explain, setExplain] = useState(false);
  const [made, setMade] = useState(false);
  const { shown, onChanged } = ready;
  const { plan } = shown;
  const plans = plan.plans ?? [];
  const free = surplusRow(plan, "free_cash");
  const buffer = surplusRow(plan, "buffer");
  const investable = surplusRow(plan, "investable");
  const reserve = plan.reserve;
  const gap = reserve?.gap ?? null;
  const reached = gap !== null && Number(gap) <= 0;
  const todos = (plan.todos ?? []).filter((todo) => todo.code !== "rate_missing");
  // A planned spending change counts from its month on, not in the closed months the rows
  // above average, so its effect is the server's stretches of the coming months.
  const segments = (ready.draft.spending_changes ?? []).length > 0 ? (plan.spending_play?.rates ?? []) : [];
  const lastMonth = plan.months?.[plan.months.length - 1]?.month;
  return (
    <section className="card stack planning-rest-card" id={REST_ANCHOR} aria-labelledby="rest-title">
      <div className="planning-rest-head">
        <h2 id="rest-title">{t.restTitle}</h2>
        <button type="button" className="link" aria-expanded={made} onClick={() => setMade((value) => !value)}>
          {m.howCalculated} ›
        </button>
      </div>
      <ul className="household-rows planning-rest">
        <li>
          <span>{t.restFree}</span>
          <span>{f.signedEuro(free?.amount ?? "0")}</span>
        </li>
        <li>
          <span>
            {t.restPlans}
            <span className="muted planning-rest-sub">
              {plans.length === 0
                ? t.kpiNoPlan
                : shown.mode === "generic"
                  ? t.kpiPlansTogether(plans.length)
                  : plans.map((p) => `${planName(p, shown.accounts, t)} ${f.euro(p.monthly)}`).join(" · ")}
            </span>
          </span>
          <span>{f.signedEuro(negated(plan.already_invested ?? "0"))}</span>
        </li>
        <li>
          <span>
            {t.madeBuffer(f.percentValue(plan.levers.buffer_percent))}{" "}
            <LeverLink anchor={BUFFER_LEVER}>{t.change}</LeverLink>
          </span>
          <span>{f.signedEuro(negated(unsigned(buffer?.amount ?? "0")))}</span>
        </li>
        <li className="planning-rest-sum" data-testid="rest-left">
          <span>{t.restLeft}</span>
          {/* The server's: free cash less the buffer less the savings plans. */}
          <span>{f.euro(plan.investable_left ?? investable?.amount)}</span>
        </li>
      </ul>
      <InlineLever anchor={BUFFER_LEVER} title={p.changeBuffer} keys={BUFFER_KEYS} ready={ready}>
        {(levers, setLevers) => (
          <>
            <BufferLever draft={levers} setDraft={setLevers} />
            <SavingsPlans data={ready.data} draft={levers} setDraft={setLevers} />
          </>
        )}
      </InlineLever>
      {segments.length > 0 ? (
        <div className="stack" data-testid="rest-with-changes">
          <h3>{t.restWithChanges}</h3>
          <ul className="household-rows planning-rest planning-rest-segments">
            {segments.map((segment) => (
              <li key={segment.from_month}>
                <span>{stretch(segment, lastMonth, f)}</span>
                <span>{t.restSegment(f.money(segment.investable), f.ratio(segment.rate))}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {reserve ? (
        <p>
          {reached ? t.restReserveReached : t.restReserveGap(f.euro(gap))}{" "}
          <Link href={HOUSEHOLD_RESERVE}>{t.toReserve} ›</Link> <Link href="/assets">{t.toAssets} ›</Link>
        </p>
      ) : null}
      <PlanTodos todos={todos} onChanged={onChanged} />
      <div className="row">
        <button type="button" className="secondary" aria-expanded={explain} onClick={() => setExplain((value) => !value)}>
          {t.explain}
        </button>
      </div>
      {explain ? <PlanExplanationPanel scenarioId={plan.scenario_id ?? null} asOf={plan.as_of} /> : null}
      {made ? <HowItIsMade data={shown} /> : null}
    </section>
  );
}

// --- 12c: fewer than three closed months ---------------------------------------------------

export function TooFewMonths({ data, onChanged }: { data: PlanData; onChanged: () => void }) {
  const closed = data.health.window.months ?? [];
  const count = closed.length;
  const last = closed[count - 1];
  const next = last ? nextMonth(last) : null;
  const todos = [...(data.plan.todos ?? [])].filter((todo) => todo.code !== "too_few_months");
  const t = useT(planning);
  const f = usePlanFormat();
  return (
    <section className="card stack" aria-labelledby="too-few-title">
      <h2 id="too-few-title">{t.tooFewTitle}</h2>
      <p>
        {count === 0 ? t.tooFewNone : t.tooFewSome(count, closed.map((month) => f.month(month)).join(", "))} {t.tooFewWhy}
      </p>
      <p className="muted">{t.tooFewProgress(Math.min(count, 3))}</p>
      <span className="health-progress" aria-hidden="true">
        <span style={{ width: `${(Math.min(count, 3) / 3) * 100}%` }} />
      </span>
      <p className="muted">{t.tooFewClosed}</p>
      {todos.length > 0 ? (
        <>
          <h3>{t.tooFewHolds(next ? f.month(next) : null)}</h3>
          <PlanTodos todos={todos} onChanged={onChanged} />
        </>
      ) : null}
      <p className="muted">{t.tooFewThen}</p>
    </section>
  );
}

function nextMonth(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  return mon === 12 ? `${year + 1}-01` : `${year}-${String(mon + 1).padStart(2, "0")}`;
}

// --- the figures --------------------------------------------------------------------------------

export function surplusRow(plan: HouseholdPlan, key: SurplusKey) {
  return plan.surplus?.rows.find((row) => row.key === key) ?? null;
}

function planName(plan: SavingsPlan | undefined, accounts: Account[], t: PlanningText): string {
  if (!plan) return t.planFallback;
  const account = plan.destination_account_id ? accounts.find((a) => a.id === plan.destination_account_id) : undefined;
  return account?.label ?? plan.destination_payee ?? (plan.asset_class === "crypto" ? t.classCrypto : t.classDepot);
}

function unsigned(value: string): string {
  return value.startsWith("-") ? value.slice(1) : value;
}

// --- Haushalt › Reserve as mockup 19f panel 6 --------------------------------------------------

/** The detail kind of the Reserve tab's cards; each card's head opens its explanation. */
const RESERVE_CARD = "reservekarte";
const RESERVE_CARDS = ["reserve", "anlegbar", "bedarf"] as const;
type ReserveCardId = (typeof RESERVE_CARDS)[number];

function isReserveCard(id: string | null): id is ReserveCardId {
  return id !== null && (RESERVE_CARDS as readonly string[]).includes(id);
}

/** The reserve's need codes, largest first: an order for display, never a figure. */
function needRows(codes: Record<string, string> | undefined): [string, string][] {
  return Object.entries(codes ?? {}).sort((a, b) => Number(b[1]) - Number(a[1]));
}

/** The flat's shortfall in the need (the rental cash's mean while it is below 0), or null. */
function flatShortfall(plan: HouseholdPlan): string | null {
  const rental = surplusRow(plan, "mean_rental_cash");
  return rental && rental.amount.startsWith("-") ? unsigned(rental.amount) : null;
}

/** Three cards (19f panel 6): the reserve with its accounts, what can be invested now and each
 * month, and the monthly need it is measured in. Every figure is the server's (D269); each card's
 * head opens its explanation in the side panel (cards, not text, 25 Nov 2026). */
function ReserveCards({ data }: { data: PlanData }) {
  const t = useT(goalText);
  const p = useT(playText);
  const f = usePlanFormat();
  const { plan } = data;
  const reserve = plan.reserve;
  const rate = plan.levers.value_change?.reserve ?? null;
  const counted = (reserve?.accounts ?? []).filter((account) => account.origin !== "removed");
  const label = (id: string) => data.accounts.find((account) => account.id === id)?.label ?? id;
  const investable = surplusRow(plan, "investable")?.amount ?? null;
  const needs = needRows(reserve?.need_codes);
  const shown = needs.slice(0, 3);
  const more = needs.length - shown.length;
  const needsOnly = reserve?.need_basis === "needs";
  return (
    <div className="goal-cards">
      <div className="goal-c">
        <CardHead kind={RESERVE_CARD} id="reserve" title={t.reserveTitle} />
        <Stats count={3}>
          <Stat value={f.euro(reserve?.amount)} label={t.reserveToday} />
          <Stat
            value={reserve?.months != null ? t.reserveMonths(f.short(reserve.months)) : "–"}
            label={t.reserveTarget(f.short(reserve?.target_months ?? plan.levers.reserve_target_months))}
            warn={reserve?.state === "below"}
          />
          <Stat value={rate != null ? f.ratio(rate) : t.reserveNoRate} label={t.reserveRateLabel} />
        </Stats>
        {counted.length > 0 ? (
          <div className="goal-rows">
            {counted.map((account) => (
              <div key={account.account_id} className="goal-rw">
                <span>
                  {label(account.account_id)}
                  {account.kind === "depot" ? <span className="goal-tag goal-tag-yes">{t.reserveCounts}</span> : null}
                </span>
                <b>{f.signedEuro(account.balance)}</b>
              </div>
            ))}
          </div>
        ) : null}
        <a className="goal-lnk" href={`#${RESERVE_LEVER}`}>
          {p.changeReserve} ›
        </a>
      </div>
      <div className="goal-c goal-c-excess">
        <CardHead kind={RESERVE_CARD} id="anlegbar" title={t.investableTitle} />
        <Stats count={2}>
          <Stat value={plan.above_target != null ? f.euro(plan.above_target) : "–"} label={t.investableNow} />
          <Stat value={investable != null ? f.signedEuro(investable) : "–"} label={t.investableMonthly} />
        </Stats>
      </div>
      <div className="goal-c">
        <CardHead
          kind={RESERVE_CARD}
          id="bedarf"
          title={t.needTitle}
          tag={needsOnly ? <span className="goal-tag goal-tag-no">{t.needOnly}</span> : undefined}
        />
        <span className="goal-n">{f.euro(reserve?.need)}</span>
        {shown.length > 0 ? (
          <div className="goal-rows">
            {shown.map(([code, amount]) => (
              <div key={code} className="goal-rw">
                <span>{categoryName(code)}</span>
                <b>{f.euro(amount)}</b>
              </div>
            ))}
            {more > 0 ? (
              <div className="goal-rw">
                <span className="muted">{t.needMore(more)}</span>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A Reserve card's side panel: what the figures are, every row behind them and where to change
 * them. */
function ReserveCardPanel({ id, data, backLabel, onClose }: { id: ReserveCardId; data: PlanData; backLabel: string; onClose: () => void }) {
  const t = useT(goalText);
  const pl = useT(planning);
  const p = useT(playText);
  const f = usePlanFormat();
  const { plan } = data;
  const reserve = plan.reserve;
  const label = (accountId: string) => data.accounts.find((account) => account.id === accountId)?.label ?? accountId;
  let title: string;
  let body: ReactNode;
  if (id === "reserve") {
    const accounts = reserve?.accounts ?? [];
    const depot = accounts.find((account) => account.kind === "depot" && account.origin !== "removed");
    title = t.reserveTitle;
    body = (
      <>
        <div className="goal-rows">
          <div className="goal-rw">
            <span>{t.reserveToday}</span>
            <b>{f.euro(reserve?.amount)}</b>
          </div>
          <div className="goal-rw">
            <span>{reserve ? t.reserveTargetOf(f.short(reserve.target_months)) : t.reserveTargetAmount}</span>
            <b>{f.euro(reserve?.target)}</b>
          </div>
          <div className="goal-rw">
            <span>{t.reserveGap}</span>
            <b>{f.euro(reserve?.gap)}</b>
          </div>
        </div>
        <p>{t.reserveWhatText}</p>
        {accounts.length > 0 ? (
          <>
            <p className="goal-kicker">{t.reserveAccountsHead}</p>
            <div className="goal-rows">
              {accounts.map((account) => (
                <div key={account.account_id} className="goal-rw">
                  <span>
                    {label(account.account_id)}
                    {account.origin === "removed" ? (
                      <span className="goal-tag goal-tag-no">{t.reserveNotCounted}</span>
                    ) : account.kind === "depot" ? (
                      <span className="goal-tag goal-tag-yes">{t.reserveCounts}</span>
                    ) : null}
                  </span>
                  <b>{f.signedEuro(account.balance)}</b>
                </div>
              ))}
            </div>
            {depot ? <p>{t.reserveDepotText(f.date(depot.as_of))}</p> : null}
          </>
        ) : null}
        <p>{t.reserveRateText}</p>
        <p>
          <a href={`#${RESERVE_LEVER}`} className="button-link secondary">
            {p.changeReserve} ›
          </a>
        </p>
      </>
    );
  } else if (id === "anlegbar") {
    const investable = surplusRow(plan, "investable")?.amount ?? null;
    title = t.investableTitle;
    body = (
      <>
        <div className="goal-rows">
          <div className="goal-rw">
            <span>{t.investableNow}</span>
            <b>{plan.above_target != null ? f.euro(plan.above_target) : "–"}</b>
          </div>
          <div className="goal-rw">
            <span>{t.investableMonthly}</span>
            <b>{investable != null ? f.signedEuro(investable) : "–"}</b>
          </div>
        </div>
        <p>{t.investableNowText}</p>
        <p>{t.investableMonthlyText}</p>
        <p>
          <Link href={HOUSEHOLD_BUDGET} className="button-link secondary">
            {t.toBudget} ›
          </Link>
        </p>
      </>
    );
  } else {
    const shortfall = flatShortfall(plan);
    title = t.needTitle;
    body = (
      <>
        <p className="goal-n">{f.euro(reserve?.need)}</p>
        <p>{reserve?.need_basis === "needs" ? t.needText : t.needTextRegular}</p>
        {reserve?.need_basis === "needs" && reserve.need_basis_from ? <p>{t.needSince(f.month(reserve.need_basis_from))}</p> : null}
        <div className="goal-rows">
          {needRows(reserve?.need_codes).map(([code, amount]) => (
            <div key={code} className="goal-rw">
              <span>{categoryName(code)}</span>
              <b>{f.euro(amount)}</b>
            </div>
          ))}
          {shortfall ? (
            <div className="goal-rw">
              <span>{t.needFlat}</span>
              <b>{f.euro(shortfall)}</b>
            </div>
          ) : null}
        </div>
        <p className="muted">{pl.kpiNeedSub}</p>
        <p>{t.needMoveText}</p>
        <p>
          <Link href={HOUSEHOLD_BUDGET} className="button-link secondary">
            {t.toBudget} ›
          </Link>
        </p>
      </>
    );
  }
  return (
    <DetailPanel title={title} backLabel={backLabel} onClose={onClose}>
      <div className="stack goal-panel">{body}</div>
    </DetailPanel>
  );
}

// --- Stellschrauben -------------------------------------------------------------------------

/** "6" → 12 half-months and back, for the ± of the reserve target (0 to 24 in 0.5 steps). */
function halves(value: string): number {
  return Math.round(Number(value) * 2);
}

function ReserveLever({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const reserve = data.plan.reserve;
  const regular = surplusRow(data.plan, "mean_regular_spending");
  const rental = surplusRow(data.plan, "mean_rental_cash");
  const target = halves(draft.reserve_target_months);
  const setTarget = (next: number) =>
    setDraft((previous) => ({ ...previous, reserve_target_months: String(Math.max(0, Math.min(48, next)) / 2) }));
  const flatShortfall = rental && rental.amount.startsWith("-") ? rental.amount.slice(1) : null;
  const t = useT(planning);
  const f = usePlanFormat();
  return (
    <div className="planning-lever">
      <div className="planning-lever-control">
        <button type="button" className="secondary" aria-label={t.leverReserveDown} disabled={target <= 0} onClick={() => setTarget(target - 1)}>
          −
        </button>
        <span className="planning-lever-value" aria-live="polite">
          {t.leverMonths(f.short(String(target / 2)))}
        </span>
        <button type="button" className="secondary" aria-label={t.leverReserveUp} disabled={target >= 48} onClick={() => setTarget(target + 1)}>
          +
        </button>
      </div>
      <p className="planning-lever-sub">
        {t.leverReserveSub(f.euro(reserve?.need), f.euro(regular?.amount))}
        {flatShortfall ? t.leverFlat(f.euro(flatShortfall)) : ""}
      </p>
    </div>
  );
}

/** The reserve's yearly rate (D304): the household's own assumption, the app proposing none;
 * empty means none (the reserve does not grow in the goal's walk), a typed 0 is a rate. */
function ReserveRateField({ draft, setDraft, onValidity }: { draft: HouseholdLevers; setDraft: SetDraft; onValidity: (invalid: boolean) => void }) {
  const t = useT(planning);
  const tg = useT(goalText);
  const id = useId();
  const [text, setText] = useState(() => percentText(draft.value_change?.reserve));
  const [error, setError] = useState<string | undefined>(undefined);
  // An invalid entry left when the form closes must not keep blocking a save elsewhere.
  useEffect(() => () => onValidity(false), [onValidity]);

  function fail(message: string | undefined) {
    setError(message);
    onValidity(message !== undefined);
  }

  function apply(raw: string) {
    setText(raw);
    if (raw.trim() === "") {
      fail(undefined);
      setDraft((previous) => ({ ...previous, value_change: { ...previous.value_change, reserve: null } }));
      return;
    }
    const parsed = toSignedRate(raw, 3);
    if (!parsed.ok) {
      fail(parsed.message);
      return;
    }
    const value = parsed.value;
    if (Math.abs(Number(value)) > 0.5) {
      fail(t.rateRange);
      return;
    }
    fail(undefined);
    setDraft((previous) => ({ ...previous, value_change: { ...previous.value_change, reserve: value } }));
  }

  return (
    <div className="stack">
      <Field id={id} label={tg.reserveRateField} errors={error ? [error] : []}>
        <input type="text" inputMode="decimal" className="health-limit-input" value={text} onChange={(event) => apply(event.target.value)} />
      </Field>
      <p className="planning-lever-sub">{tg.reserveRateHint}</p>
    </div>
  );
}

function BufferLever({ draft, setDraft }: { draft: HouseholdLevers; setDraft: SetDraft }) {
  const buffer = Number(draft.buffer_percent);
  const setBuffer = (next: number) =>
    setDraft((previous) => ({ ...previous, buffer_percent: String(Math.max(0, Math.min(50, next))) }));
  const t = useT(planning);
  const f = usePlanFormat();
  return (
    <div className="planning-lever">
      <div className="planning-lever-control">
        <button type="button" className="secondary" aria-label={t.leverBufferDown} disabled={buffer <= 0} onClick={() => setBuffer(buffer - 5)}>
          −
        </button>
        <span className="planning-lever-value" aria-live="polite">
          {f.percentValue(draft.buffer_percent)}
        </span>
        <button type="button" className="secondary" aria-label={t.leverBufferUp} disabled={buffer >= 50} onClick={() => setBuffer(buffer + 5)}>
          +
        </button>
      </div>
      <p className="planning-lever-sub">{t.leverBufferSub}</p>
    </div>
  );
}

// An accepted refund names its tax year in its source, "return_estimate:2026" (PR #434 review D);
// a bare "return_estimate" was saved before the year was written and names none.
const RETURN_ESTIMATE = "return_estimate";

function isReturnEstimate(source: string | undefined): boolean {
  return source === RETURN_ESTIMATE || (source ?? "").startsWith(`${RETURN_ESTIMATE}:`);
}

function estimateYear(source: string | undefined): number | null {
  return (source ?? "").startsWith(`${RETURN_ESTIMATE}:`) ? Number(source!.slice(RETURN_ESTIMATE.length + 1)) : null;
}

export function OneOffs({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const months = (data.plan.months ?? []).map((m) => m.month);
  const proposals = data.plan.proposed_one_offs ?? [];
  const oneOffs = draft.one_offs ?? [];
  // a proposal is hidden once the draft holds a one-off of its tax year, or a bare one (no year)
  const bareTaken = oneOffs.some((o) => o.source === RETURN_ESTIMATE);
  const takenYears = new Set(oneOffs.map((o) => estimateYear(o.source)).filter((year) => year !== null));
  const newestClosed = data.plan.newest_closed;
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [adding, setAdding] = useState(false);
  const [month, setMonth] = useState("");
  const [amount, setAmount] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"month" | "amount", string>>>({});

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = toSignedMoneyString(amount);
    const found: typeof errors = {};
    if (!parsed.ok) found.amount = parsed.message;
    if (month === "") found.month = t.pickMonth;
    setErrors(found);
    if (!parsed.ok || month === "") return;
    setDraft((previous) => ({ ...previous, one_offs: [...(previous.one_offs ?? []), { month, amount: parsed.value, source: "typed" }] }));
    setAdding(false);
    setMonth("");
    setAmount("");
  }

  const monthSelect = (id: string, value: string, onChange: (value: string) => void) => (
    <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{t.pickMonthOption}</option>
      {months.map((m) => (
        <option key={m} value={m}>
          {f.month(m)}
        </option>
      ))}
    </select>
  );

  return (
    <div className="stack">
      <h3>{t.oneOffsTitle}</h3>
      {!bareTaken
        ? proposals.map((proposal, index) => {
            if (takenYears.has(proposal.tax_year)) return null;
            const id = `${ids}-proposal-${index}`;
            const picked = chosen[index] ?? "";
            return (
              <div key={proposal.tax_year} className="health-plan-row">
                <p>
                  {t.oneOffTax(proposal.tax_year, proposal.amount.startsWith("-"))}{" "}
                  <span className="badge badge-info">{t.proposal}</span> <strong>{f.signedEuro(proposal.amount)}</strong>{" "}
                  <span className="muted">
                    {t.fromEstimateAt} <Link href={`/tax/${proposal.tax_year}`}>{t.taxTotal}</Link>
                  </span>
                </p>
                <span className="row">
                  <label htmlFor={id}>{t.monthLabel}</label>
                  {monthSelect(id, picked, (value) => setChosen((previous) => ({ ...previous, [index]: value })))}
                  <button
                    type="button"
                    className="secondary"
                    disabled={picked === ""}
                    onClick={() =>
                      setDraft((previous) => ({
                        ...previous,
                        one_offs: [
                          ...(previous.one_offs ?? []),
                          { month: picked, amount: proposal.amount, source: `${RETURN_ESTIMATE}:${proposal.tax_year}` },
                        ],
                      }))
                    }
                  >
                    {t.apply}
                  </button>
                </span>
                <p className="muted">{t.oneOffPickHint}</p>
              </div>
            );
          })
        : null}
      {oneOffs.length > 0 ? (
        <ul className="household-rows">
          {oneOffs.map((oneOff, index) => (
            <li key={`${oneOff.month}-${index}`}>
              <span>
                {f.month(oneOff.month)}
                {isReturnEstimate(oneOff.source)
                  ? ` · ${t.oneOffFromEstimate(estimateYear(oneOff.source))}`
                  : ""}
                {newestClosed && oneOff.month <= newestClosed ? ` · ${t.oneOffClosed}` : ""}
              </span>
              <span>
                {f.signedEuro(oneOff.amount)}{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() => setDraft((previous) => ({ ...previous, one_offs: (previous.one_offs ?? []).filter((_, i) => i !== index) }))}
                >
                  {t.remove}
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : proposals.length === 0 ? (
        <p className="muted">{t.oneOffNone}</p>
      ) : null}
      {adding ? (
        <form className="stack" onSubmit={add} aria-label={t.oneOffFormLabel}>
          <div className="health-limit-fields">
            <Field id={`${ids}-month`} label={t.monthLabel} errors={errors.month ? [errors.month] : []}>
              {monthSelect(`${ids}-month`, month, setMonth)}
            </Field>
            <Field id={`${ids}-amount`} label={t.oneOffAmount} hint={t.oneOffAmountHint} errors={errors.amount ? [errors.amount] : []}>
              <input type="text" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
            </Field>
          </div>
          <div className="row">
            <button type="submit">{t.add}</button>
            <button type="button" className="secondary" onClick={() => setAdding(false)}>
              {t.cancel}
            </button>
          </div>
        </form>
      ) : (
        <p>
          <button type="button" className="secondary" onClick={() => setAdding(true)}>
            {t.addOneOff}
          </button>
        </p>
      )}
    </div>
  );
}

const RESERVE_KINDS = new Set<Account["kind"]>(["bank_current", "bank_savings", "credit_card"]);

function ReserveAccountsBlock({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const rows = data.plan.reserve?.accounts ?? [];
  // The default set: the accounts the server lists as default or removed (D237).
  const byDefault = new Set(rows.filter((row) => row.origin !== "added").map((row) => row.account_id));
  const added = new Set(draft.reserve_accounts?.added ?? []);
  const removed = new Set(draft.reserve_accounts?.removed ?? []);
  const accounts = data.accounts.filter((account) => RESERVE_KINDS.has(account.kind));

  function toggle(accountId: string, on: boolean) {
    setDraft((previous) => {
      const nextAdded = new Set(previous.reserve_accounts?.added ?? []);
      const nextRemoved = new Set(previous.reserve_accounts?.removed ?? []);
      if (byDefault.has(accountId)) {
        if (on) nextRemoved.delete(accountId);
        else nextRemoved.add(accountId);
      } else if (on) nextAdded.add(accountId);
      else nextAdded.delete(accountId);
      return { ...previous, reserve_accounts: { added: [...nextAdded], removed: [...nextRemoved] } };
    });
  }

  return (
    <div className="stack">
      <h3>{t.reserveAccountsTitle}</h3>
      <ul className="household-rows">
        {accounts.map((account) => {
          const counted = byDefault.has(account.id) ? !removed.has(account.id) : added.has(account.id);
          const row = rows.find((r) => r.account_id === account.id);
          const id = `${ids}-${account.id}`;
          return (
            <li key={account.id}>
              <span>
                <input id={id} type="checkbox" checked={counted} onChange={(event) => toggle(account.id, event.target.checked)} />{" "}
                <label htmlFor={id}>{account.label}</label>{" "}
                <span className="muted">
                  {account.purpose === "rental" && !counted
                    ? t.reserveRentalNot
                    : account.kind === "credit_card"
                      ? t.reserveCardDebt
                      : counted
                        ? t.reserveCounts
                        : t.reserveNotCounted}
                </span>
              </span>
              <span>{row ? f.euro(row.balance) : ""}</span>
            </li>
          );
        })}
      </ul>
      <p className="muted">{t.reserveSaved(f.euro(data.plan.reserve?.amount))}</p>
    </div>
  );
}

function SavingsPlans({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const overrides = draft.plan_overrides ?? [];
  const plans = data.plan.plans ?? [];
  const known = new Set(plans.map((p) => p.key));
  // A plan the saved scenario leaves out is not in the plan any more; it is listed from its override.
  const dropped = overrides.filter((o) => o.dropped && !known.has(o.plan_key));

  function setOverride(planKey: string, next: { monthly?: string | null; dropped: boolean } | null) {
    setDraft((previous) => {
      const rest = (previous.plan_overrides ?? []).filter((o) => o.plan_key !== planKey);
      return { ...previous, plan_overrides: next ? [...rest, { plan_key: planKey, ...next }] : rest };
    });
  }

  if (data.mode === "generic") {
    // §10: one total, no plan and no account named; changing a single plan needs its name, so
    // it is done in „Eigene Nutzung“.
    return (
      <div className="stack">
        <h3>{t.plansTitle}</h3>
        {plans.length === 0 ? (
          <p className="muted">{t.noPlanFound}</p>
        ) : (
          <p data-testid="plans-total">
            {t.perMonth(f.euro(data.plan.already_invested ?? "0"))}, {t.kpiPlansTogether(plans.length)}
          </p>
        )}
        {plans.length > 0 || dropped.length > 0 ? (
          <p className="muted">
            {t.plansGeneric} (<Link href="/settings">{t.settingsLink}</Link> › {t.adviceModeName}).
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="stack">
      <h3>{t.plansTitle}</h3>
      {plans.length === 0 && dropped.length === 0 ? (
        <p className="muted">{t.noPlanFound}</p>
      ) : null}
      {plans.map((p) => {
        const override = overrides.find((o) => o.plan_key === p.key);
        const id = `${ids}-${p.key}`;
        return (
          <div key={p.key} className="health-plan-row">
            <p>
              <strong>{planName(p, data.accounts, t)}</strong>{" "}
              <span className="muted">
                {t.planFound(joinMonths(p.months, f, t.and))}
                {p.overridden ? ` · ${t.planChanged}` : ""}
              </span>
            </p>
            <span className="row">
              <span>
                {override?.dropped ? t.notCounted : t.perMonth(f.euro(override?.monthly ?? p.monthly))}
              </span>
              {editing === p.key ? (
                <span className="row">
                  <label htmlFor={id}>{t.amountPerMonth}</label>
                  <input id={id} type="text" inputMode="decimal" value={value} onChange={(event) => setValue(event.target.value)} />
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => {
                      const parsed = toSignedMoneyString(value);
                      if (!parsed.ok || parsed.value.startsWith("-")) {
                        setMessage(parsed.ok ? t.amountFromZero : parsed.message);
                        return;
                      }
                      setOverride(p.key, { monthly: parsed.value, dropped: false });
                      setEditing(null);
                      setMessage(null);
                    }}
                  >
                    {t.apply}
                  </button>
                </span>
              ) : (
                <>
                  <button
                    type="button"
                    className="link"
                    onClick={() => {
                      setEditing(p.key);
                      setValue((override?.monthly ?? p.monthly).replace(".", ","));
                    }}
                  >
                    {t.changeAmount}
                  </button>
                  {override?.dropped ? (
                    <button type="button" className="link" onClick={() => setOverride(p.key, null)}>
                      {t.countAgain}
                    </button>
                  ) : (
                    <button type="button" className="link" onClick={() => setOverride(p.key, { dropped: true })}>
                      {t.dropPlan}
                    </button>
                  )}
                </>
              )}
            </span>
            {editing === p.key && message ? <p className="field-errors">{message}</p> : null}
          </div>
        );
      })}
      {dropped.map((o) => (
        <div key={o.plan_key} className="health-plan-row">
          <p className="muted">{t.planDropped}</p>
          <button type="button" className="link" onClick={() => setOverride(o.plan_key, null)}>
            {t.countAgain}
          </button>
        </div>
      ))}
    </div>
  );
}

function joinMonths(months: readonly string[], f: PlanFormat, and: string): string {
  const sorted = [...months].sort();
  const year = sorted[sorted.length - 1]?.slice(0, 4) ?? "";
  const sameYear = sorted.every((m) => m.startsWith(year));
  const names = sorted.map((m) => (sameYear ? f.monthName(m) : f.month(m)));
  const text = names.length > 1 ? `${names.slice(0, -1).join(", ")} ${and} ${names[names.length - 1]}` : (names[0] ?? "");
  return sameYear && year ? `${text} ${year}` : text;
}

// --- Wege zum Reserveziel --------------------------------------------------------------------

function Paths({ data }: { data: PlanData }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const { plan } = data;
  const paths = plan.paths ?? [];
  const plans = plan.plans ?? [];
  const later = plan.flat_change;
  const target = plan.reserve?.target;
  const others = plan.paths_others ?? [];
  const name = (key: string) => planName(plans.find((p) => p.key === key), data.accounts, t);
  if (paths.length === 0) return null;

  function label(key: string, paused: string[]): { title: string; sub: string } {
    const running = plans.filter((p) => !paused.includes(p.key));
    const runningText =
      running.length === 0
        ? ""
        : t.pathRunning(running.map((p) => `${planName(p, data.accounts, t)} ${f.euro(p.monthly)}`).join(", "), running.length);
    if (key === "reserve_first") {
      return { title: t.pathReserveFirst, sub: t.pathAllPaused(plans.length) };
    }
    if (key === "as_is") return { title: t.pathAsIs, sub: t.pathAsIsSub };
    if (data.mode === "generic") return { title: t.pathOnePaused, sub: "" };
    return { title: t.pathPaused(name(key.slice("pause:".length))), sub: runningText };
  }

  return (
    <section className="card stack" aria-labelledby="paths-title">
      <h2 id="paths-title">{t.pathsTitle}</h2>
      <p className="muted">
        {t.pathsIntro(f.euro(target), f.euro(plan.reserve?.amount), f.longDate(plan.newest_closed ? monthEnd(plan.newest_closed) : null))}
      </p>
      <div className="table-wrap">
        <table className="planning-table">
          <thead>
            <tr>
              <th scope="col">{t.pathCol}</th>
              <th scope="col" className="num">
                {t.toReserveCol}
              </th>
              <th scope="col">{t.reachedCol}</th>
            </tr>
          </thead>
          <tbody>
            {paths.map((path) => {
              const { title, sub } = label(path.key, path.paused ?? []);
              const after = later?.paths.find((p) => p.key === path.key);
              return (
                <tr key={path.key}>
                  <th scope="row">
                    {title}
                    {sub ? <span className="muted"> · {sub}</span> : null}
                  </th>
                  <td className="num">
                    {t.perMonth(f.euro(path.to_reserve_first_month))}
                    {after && later ? (
                      <span className="muted">
                        <br />
                        {t.from(f.month(later.month))}: {t.perMonth(f.euro(after.to_reserve))}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    {path.reached ? (
                      <>
                        {f.month(path.reached)}
                        {path.months_needed != null ? <span className="muted"> · {t.inMonths(path.months_needed)}</span> : null}
                      </>
                    ) : path.used_up ? (
                      <>{t.pathUsedUp(f.month(path.used_up))}</>
                    ) : (
                      <>{t.pathNotIn(Math.round(plan.levers.horizon_months / 12), f.euro(path.reserve_at_horizon))}</>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {others.length > 0 && data.mode !== "generic" ? <p className="muted">{t.pathsOthers(others.map(name).join(", "))}</p> : null}
      {plan.paths_on_rental_account ? (
        <p className="muted">
          {t.pathsRental(f.euro(plan.paths_on_rental_account))}
        </p>
      ) : null}
      {later ? (
        <p className="muted">
          {t.pathsLater(f.month(later.month), f.signedEuro(later.flat), f.signedEuro(later.before), later.before_is_mean)} {t.pathsAssume}
        </p>
      ) : (
        <p className="muted">{t.pathsAssume}</p>
      )}
    </section>
  );
}

// --- So entsteht die Zahl -----------------------------------------------------------------

/** The household month's field each row is the mean (or median, lowest, sum) of. */
const SURPLUS_FIELD: Partial<Record<SurplusKey, keyof components["schemas"]["HouseholdMonth"]>> = {
  mean_income: "private_income",
  mean_regular_spending: "regular_spending",
  mean_rental_cash: "rental_cash",
  free_cash: "free_cash",
  median_free_cash: "free_cash",
  lowest_free_cash: "free_cash",
  one_off_spending_left_out: "one_off_spending",
};

function HowItIsMade({ data }: { data: PlanData }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const { plan } = data;
  const rows = plan.surplus?.rows ?? [];
  const closedMonths = data.health.window.months ?? [];
  const rental = data.accounts.find((account) => account.purpose === "rental");
  const first = closedMonths[0];
  const last = closedMonths[closedMonths.length - 1];
  return (
    <section className="card stack" aria-labelledby="made-title">
      <h2 id="made-title">{t.madeTitle}</h2>
      <p className="muted">
        {t.madeIntro(closedMonths.length, f.monthRange(closedMonths))}
      </p>
      <div className="stack">
        {rows.map((row) => {
          const field = SURPLUS_FIELD[row.key];
          const title =
            row.key === "buffer"
              ? t.madeBuffer(f.percentValue(plan.levers.buffer_percent))
              : row.key === "lowest_free_cash" && row.month
                ? `${t.surplusLabel(row.key)}: ${f.month(row.month)}`
                : t.surplusLabel(row.key);
          const amount = row.key === "buffer" ? f.signedEuro(`-${row.amount.replace(/^-/, "")}`) : f.signedEuro(row.amount);
          if (!field) {
            return (
              <div key={row.key} className="health-plan-row">
                <p>
                  <strong>{title}</strong>
                </p>
                <span>{amount}</span>
              </div>
            );
          }
          return (
            <details key={row.key} className="health-sources">
              <summary>
                <span className="health-plan-row">
                  <span>{title}</span>
                  <span>{amount}</span>
                </span>
              </summary>
              <ul className="household-rows">
                {data.months.map((view) => (
                  <li key={view.month}>
                    <Link href={row.key === "mean_rental_cash" && rental ? rentalHref(rental.id, view.month) : `/household?monat=${view.month}`}>
                      {f.month(view.month)}
                    </Link>
                    <span>{f.signedEuro(view.household[field] as string)}</span>
                  </li>
                ))}
              </ul>
              {row.key === "mean_rental_cash" && rental && first && last ? (
                <p>
                  <Link href={`/accounts/${rental.id}?tab=umsaetze&von=${first}-01&bis=${monthEnd(last)}`}>
                    {t.madeRentalAll}
                  </Link>
                </p>
              ) : (
                <p className="muted">{t.madeMonthsOpen}</p>
              )}
            </details>
          );
        })}
      </div>
    </section>
  );
}

function rentalHref(accountId: string, month: string): string {
  return `/accounts/${accountId}?tab=umsaetze&von=${month}-01&bis=${monthEnd(month)}`;
}

// --- Vermögen in einigen Jahren (Anlagen › Übersicht) ----------------------------------------

/** „Rendite ändern“ on Anlagen's projection: the yearly value change of funds and of crypto the
 * household types (D247, the app proposes none), and the projection's years. */
function RateFields({
  data,
  draft,
  setDraft,
  onValidity,
}: {
  data: PlanData;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  /** true while a field holds an entry the draft cannot take */
  onValidity: (invalid: boolean) => void;
}) {
  const t = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const flat = (data.plan.projection?.assumptions ?? []).find((a) => a.kind === "flat_growth");
  const [texts, setTexts] = useState({
    depot: percentText(draft.value_change?.depot),
    crypto: percentText(draft.value_change?.crypto),
    years: String(draft.net_worth_years),
  });
  const [errors, setErrors] = useState<Partial<Record<"depot" | "crypto" | "years", string>>>({});
  // A field left invalid when the panel closes keeps the draft's old value, which is valid, so it
  // must not keep blocking „Szenario speichern“ where no red field shows.
  useEffect(() => () => onValidity(false), [onValidity]);

  function setError(key: "depot" | "crypto" | "years", message: string | undefined) {
    const next = { ...errors, [key]: message };
    setErrors(next);
    onValidity(Object.values(next).some(Boolean));
  }

  function apply(key: "depot" | "crypto" | "years", raw: string) {
    setTexts((previous) => ({ ...previous, [key]: raw }));
    if (key === "years") {
      const years = Number(raw);
      if (!/^\d{1,2}$/.test(raw.trim()) || years < 1 || years > 30) {
        setError("years", t.yearsRange);
        return;
      }
      setError("years", undefined);
      setDraft((previous) => ({ ...previous, net_worth_years: years }));
      return;
    }
    if (raw.trim() === "") {
      setError(key, undefined);
      setDraft((previous) => ({ ...previous, value_change: { ...previous.value_change, [key]: null } }));
      return;
    }
    const parsed = toSignedRate(raw, 3);
    if (!parsed.ok) {
      setError(key, parsed.message);
      return;
    }
    const value = parsed.value;
    const tooLarge = Math.abs(Number(value)) > 0.5;
    setError(key, tooLarge ? t.rateRange : undefined);
    if (!tooLarge) setDraft((previous) => ({ ...previous, value_change: { ...previous.value_change, [key]: value } }));
  }

  return (
    <fieldset className="health-limit">
      <legend>{t.projLegend}</legend>
      <div className="health-limit-fields">
        {(["depot", "crypto"] as const).map((key) => (
          <Field
            key={key}
            id={`${ids}-${key}`}
            label={t.rateField(key === "depot" ? t.classDepot : t.classCrypto)}
            errors={errors[key] ? [errors[key] as string] : []}
          >
            <input
              type="text"
              inputMode="decimal"
              className="health-limit-input"
              value={texts[key]}
              onChange={(event) => apply(key, event.target.value)}
            />
          </Field>
        ))}
        <Field id={`${ids}-years`} label={t.years} errors={errors.years ? [errors.years] : []}>
          <input
            type="text"
            inputMode="numeric"
            className="health-limit-input"
            value={texts.years}
            onChange={(event) => apply("years", event.target.value)}
          />
        </Field>
      </div>
      <p className="muted">
        {t.projFlat(flat ? f.ratio(flat.value) : null)}{" "}
        <Link href={propertyPlanningHref(flat?.property_id ? new URLSearchParams({ immobilie: flat.property_id }) : undefined)}>
          {t.projFlatChange}
        </Link>
      </p>
    </fieldset>
  );
}

/** What was Planung › Vermögen, on Anlagen › Übersicht under „Vermögen im Verlauf“ (§7.9 (a) 4):
 * the net worth in some years on the rates the household typed, inside a `PlanProvider`. Nothing
 * while the plan cannot be read or has fewer than three closed months: Haushalt says why. */
export function HouseholdProjection() {
  const { ready } = usePlan();
  if (!ready) return null;
  return <ProjectionCard key={ready.epoch} ready={ready} />;
}

function ProjectionCard({ ready }: { ready: PlanReady }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const p = useT(playText);
  const { shown, onChanged } = ready;
  const projection = shown.plan.projection;
  const points = projection?.points ?? [];
  const assumptions = projection?.assumptions ?? [];
  const flat = assumptions.find((a) => a.kind === "flat_growth");
  const rateTodos = (shown.plan.todos ?? []).filter((todo) => todo.code === "rate_missing");
  const projectionTodos = projection?.todos ?? [];
  const last = points[points.length - 1];
  const rateLine = (kind: "depot_rate" | "crypto_rate", label: string) => {
    const assumption = assumptions.find((a) => a.kind === kind);
    if (!assumption) return null;
    const by = assumption.created_by ? shown.names.get(assumption.created_by) : undefined;
    return <li key={kind}>{t.rateLine(label, f.ratio(assumption.value), by ?? null, f.longDate(assumption.created_at))}</li>;
  };
  const propertyLink = propertyPlanningHref(flat?.property_id ? new URLSearchParams({ immobilie: flat.property_id }) : undefined);

  return (
    <section className="card stack" id={PROJECTION_ANCHOR} aria-labelledby="projection-title">
      <div className="planning-card-head">
        <h2 id="projection-title">{t.projTitle}</h2>
        <span className="spacer" />
        <LeverLink anchor={RATE_LEVER}>
          {p.changeRate} <span aria-hidden="true">›</span>
        </LeverLink>
      </div>
      <p className="muted">{t.projIntro}</p>
      <PlannedChanges levers={ready.data.plan.levers} />
      <InlineLever anchor={RATE_LEVER} title={p.changeRate} keys={RATE_KEYS} ready={ready}>
        {(levers, setLevers, setInvalid) => (
          <RateFields data={ready.data} draft={levers} setDraft={setLevers} onValidity={setInvalid} />
        )}
      </InlineLever>
      <DirtyNotice text={t.dirtyNoticeProjection} />
      {points.length === 0 ? (
        <>
          {rateTodos.length > 0 ? <p className="health-reference-title">{t.rateMissing}</p> : null}
          <PlanTodos todos={[...rateTodos, ...projectionTodos.filter((todo) => todo.code !== "rate_missing")]} onChanged={onChanged} />
        </>
      ) : (
        <>
          <ul className="muted">
            {rateLine("depot_rate", t.classDepot)}
            {rateLine("crypto_rate", t.classCrypto)}
            <li>
              {t.projFlatLine(flat ? f.ratio(flat.value) : null)} <Link href={propertyLink}>{t.projFlatChange}</Link>
            </li>
            <li>{t.projSameMean(f.monthRange(shown.health.window.months ?? []))}</li>
            <li>{t.projNominal}</li>
          </ul>
          {last ? <p className="cashflow-value">{t.projAt(f.longDate(last.on), f.euro(last.total))}</p> : null}
          {projection?.start ? (
            <p className="muted">{t.projStart(f.longDate(projection.start.on), f.euro(projection.start.total))}</p>
          ) : null}
          <ProjectionTable start={projection?.start ?? null} points={points} />
          {projection?.ends_before_sale_year != null ? (
            <p className="muted">
              {t.projEnds(projection.ends_before_sale_year - 1)} <Link href={propertyLink}>{t.projEndsLink}</Link>
            </p>
          ) : null}
          {projection?.used_up ? <p className="muted">{t.projUsedUp(f.month(projection.used_up))}</p> : null}
          {last?.change ? <ChangeRows point={last} /> : null}
          <PlanTodos todos={projectionTodos} onChanged={onChanged} />
        </>
      )}
    </section>
  );
}

function percentText(value: string | null | undefined): string {
  if (value == null) return "";
  const negative = value.startsWith("-");
  return `${negative ? "-" : ""}${percentInput(negative ? value.slice(1) : value)}`;
}

function ProjectionTable({ start, points }: { start: ProjectionPoint | null; points: ProjectionPoint[] }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const rows = start ? [start, ...points] : points;
  return (
    <div className="table-wrap">
      <table className="planning-table">
        <thead>
          <tr>
            <th scope="col">{t.colDate}</th>
            <th scope="col" className="num">
              {t.colBank}
            </th>
            <th scope="col" className="num">
              {t.classDepot}
            </th>
            <th scope="col" className="num">
              {t.classCrypto}
            </th>
            <th scope="col" className="num">
              {t.colFlat}
            </th>
            <th scope="col" className="num">
              {t.colLoans}
            </th>
            <th scope="col" className="num">
              {t.colCard}
            </th>
            <th scope="col" className="num">
              {t.colNetWorth}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.on}>
              <th scope="row">{f.date(point.on)}</th>
              <td className="num">{f.euro(point.bank)}</td>
              <td className="num">{f.euro(point.depot)}</td>
              <td className="num">{f.euro(point.crypto)}</td>
              <td className="num">{f.euro(point.property)}</td>
              <td className="num">{f.euro(negated(point.loans))}</td>
              <td className="num">{f.euro(negated(point.other_liabilities))}</td>
              <td className="num">
                <strong>{f.euro(point.total)}</strong>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A debt shown as a negative amount, whichever sign the server keeps it in. */
function negated(value: string): string {
  if (value.startsWith("-")) return value;
  return /^0+(\.0+)?$/.test(value) ? value : `-${value}`;
}

function ChangeRows({ point }: { point: ProjectionPoint }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const change = point.change;
  if (!change) return null;
  return (
    <div className="stack">
      <h3>{t.changeTitle(f.signedEuro(change.total))}</h3>
      <ul className="household-rows">
        <li>
          <span>{t.changeAccounts}</span>
          <span>{f.signedEuro(change.on_accounts)}</span>
        </li>
        <li>
          <span>{t.changePaidIn}</span>
          <span>{f.signedEuro(change.paid_in)}</span>
        </li>
        <li>
          <span>{t.changeAssumptions}</span>
          <span>{f.signedEuro(change.from_assumptions)}</span>
        </li>
        <li>
          <span>{t.changeFlat}</span>
          <span>{f.signedEuro(change.flat_value)}</span>
        </li>
        <li>
          <span>{t.changeLoan}</span>
          <span>{f.signedEuro(change.loan_repaid)}</span>
        </li>
      </ul>
    </div>
  );
}
