"use client";

// Planung in five sections (phase 10 D270, mockup 17-C2; before it phase 9 §14, mockups 12, 12b,
// 12c): chips choose Budget, Reserve, Anlegen, Vermögen or Immobilie, the section in the URL
// (`/planning?section=…`). Budget is components/planningBudget.tsx; Reserve, Anlegen and
// Vermögen regroup phase 9's cards (the reserve and its paths; „Anlegbar pro Monat“, the savings
// plans and the explanation; the net worth on typed rates); Immobilie is the property's Planung
// (components/planning.tsx). One scenario row on top says whether the levers are saved and
// opens the scenario sheet in the detail panel: the unsaved changes, „Was eure Änderungen
// bewirken“ (D271), „Ausgaben durchspielen“ and „Weitere Annahmen“. Every figure is the
// server's (GET /household-plan, /household-budget) with the saved default scenario; a lever
// changed here is a draft until „Szenario speichern“ stores it as a new version of the default
// (D248, append-only). The draft lives in this screen, so it survives a change of section.
import Link from "next/link";
import { type FormEvent, type ReactNode, useEffect, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { lineLabel, planning, type PlanningText, sectionLabel } from "@/i18n/planning";

import { DetailLayout, DetailPanel, useDetail } from "./DetailPanel";
import { Field } from "./form";
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
import { PlanExplanationPanel } from "./planExplanation";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { BudgetClassPanel, BudgetSection, type BudgetView, GoalsPanel, PlanError } from "./planningBudget";
import { Effects, type PlanPreview, SpendingPlaySection, usePlanPreview } from "./spendingPlay";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./health.css";
import "./planningSections.css";

type Account = components["schemas"]["Account"];
type SavingsPlan = components["schemas"]["SavingsPlan"];
type SurplusKey = components["schemas"]["SurplusKey"];
type IncomeChangeNote = components["schemas"]["IncomeChangeNote"];
type ProjectionPoint = components["schemas"]["ProjectionPoint"];
type AdviceMode = components["schemas"]["AdviceMode"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type BudgetTargets = components["schemas"]["BudgetTargets"];

export const PLAN_SECTIONS = ["budget", "reserve", "anlegen", "vermoegen", "immobilie"] as const;
export type PlanSection = (typeof PLAN_SECTIONS)[number];

/** The scenario sheet, the goals sheet and a Budget class in the detail panel (D267). */
const SHEET = "entwurf";

interface PlanData {
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

/** One line per changed lever area, for the scenario row's count and the sheet's list. */
function draftChanges(saved: HouseholdLevers, draft: HouseholdLevers, t: T, short: (v: string) => string, percent: (v: string) => string): string[] {
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
      out.push(t.changeClassMove(categoryName(code), now ? lineLabel(t, now) : "–"));
    }
  }
  if (!same(saved.budget_targets, draft.budget_targets)) out.push(t.changeTargets);
  return out;
}

function categoryName(code: string): string {
  return ALLOCATION_CATEGORY[code as AllocationCategory] ?? code;
}

/** Planung: the chips, the scenario row and the chosen section; `property` is the Immobilie
 * section (the property's Planung), loaded by the page. */
export function HouseholdPlanScreen({ section, property }: { section: PlanSection; property: ReactNode }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const state = useLoad(loadPlan);
  const [draftState, setDraftState] = useState<DraftState | null>(null);
  const data = state.data;
  const draft = resolveDraft(draftState, data?.plan);
  const savedLevers = fillLevers(data?.plan.levers);
  const changes = data ? draftChanges(savedLevers, draft, t, f.short, f.percentValue) : [];
  const sheet = useDetail("scenario");
  const current = data ? currentDefault(data.scenarios) : null;

  const setDraft: SetDraft = (update) =>
    setDraftState((previous) => ({ levers: update(resolveDraft(previous, data?.plan)) }));

  const household = section !== "immobilie";
  return (
    <>
      <div className="page-head planning-head">
        <div>
          <p className="eyebrow">{data && household ? t.asOf(f.date(data.plan.as_of)) : " "}</p>
          <h1>{t.title}</h1>
        </div>
        <span className="spacer" />
        {household && data ? (
          <a className="planning-scenario-row" {...sheet.linkProps(SHEET)} data-dirty={changes.length > 0 ? "" : undefined}>
            <span>
              <strong>{current ? t.scenario(current.name) : t.defaults}</strong>
              {" · "}
              <span className="planning-scenario-state">{changes.length > 0 ? t.unsaved(changes.length) : t.saved}</span>
            </span>
            <span className="planning-scenario-open">
              {t.assumptions} <span aria-hidden="true">›</span>
            </span>
          </a>
        ) : null}
      </div>
      <nav aria-label={t.sections} className="planning-chips">
        {PLAN_SECTIONS.map((key) => (
          <Link key={key} href={`/planning?section=${key}`} scroll={false} aria-current={key === section ? "page" : undefined}>
            {sectionLabel(t, key)}
          </Link>
        ))}
      </nav>
      {household ? (
        <Loading state={state}>
          {(loaded) =>
            loaded.health.window.enough && loaded.plan.surplus ? (
              <PlanBody
                data={loaded}
                section={section}
                draft={draft}
                setDraft={setDraft}
                changes={changes}
                onDiscard={() => setDraftState(null)}
                onSaved={(posted, row) => {
                  setDraftState({ levers: posted, resetOn: row.id });
                  state.reload();
                }}
                onGoalsSaved={(targets, row) => {
                  // Other unsaved changes stay a draft; with none the page follows the new row.
                  setDraftState((previous) =>
                    changes.length === 0 || (changes.length === 1 && changes[0] === t.changeTargets)
                      ? { levers: { ...resolveDraft(previous, loaded.plan), budget_targets: targets }, resetOn: row.id }
                      : { levers: { ...resolveDraft(previous, loaded.plan), budget_targets: targets } },
                  );
                  state.reload();
                }}
                onChanged={state.reload}
              />
            ) : (
              <TooFewMonths data={loaded} onChanged={state.reload} />
            )
          }
        </Loading>
      ) : (
        property
      )}
    </>
  );
}

/** The page's own to-dos point at a section or the scenario sheet of this page; every other
 * one is a link. The server's links name phase 9's `/planning?tab=haushalt` (rules/plan/, not
 * changed: D271). */
function PlanTodos({ todos, onChanged }: { todos: HealthTodo[]; onChanged: () => void }) {
  const sheet = useDetail("scenario");
  if (todos.length === 0) return null;
  return (
    <ul className="health-todos">
      {todos.map((todo, index) => {
        const key = `${todo.code}-${index}`;
        if (!todo.href.startsWith("/planning?tab=haushalt")) return <HealthTodoItem key={key} todo={todo} onChanged={onChanged} />;
        return (
          <li key={key} className="health-todo">
            <span>{todo.text}</span>
            {todo.code === "rate_missing" ? (
              <Link href="/planning?section=vermoegen#annahme" className="button-link secondary">
                {todo.action}
              </Link>
            ) : (
              <a {...sheet.linkProps(SHEET)} className="button-link secondary">
                {todo.action}
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// --- 12c: fewer than three closed months ---------------------------------------------------

function TooFewMonths({ data, onChanged }: { data: PlanData; onChanged: () => void }) {
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

// --- the sections with their figures ------------------------------------------------------------

function surplusRow(plan: HouseholdPlan, key: SurplusKey) {
  return plan.surplus?.rows.find((row) => row.key === key) ?? null;
}

function planName(plan: SavingsPlan | undefined, accounts: Account[], t: PlanningText): string {
  if (!plan) return t.planFallback;
  const account = plan.destination_account_id ? accounts.find((a) => a.id === plan.destination_account_id) : undefined;
  return account?.label ?? plan.destination_payee ?? (plan.asset_class === "crypto" ? t.classCrypto : t.classDepot);
}

function PlanBody({
  data,
  section,
  draft,
  setDraft,
  changes,
  onDiscard,
  onSaved,
  onGoalsSaved,
  onChanged,
}: {
  data: PlanData;
  section: Exclude<PlanSection, "immobilie">;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  changes: string[];
  onDiscard: () => void;
  onSaved: (posted: HouseholdLevers, row: HouseholdScenario) => void;
  onGoalsSaved: (targets: BudgetTargets, row: HouseholdScenario) => void;
  onChanged: () => void;
}) {
  const t = useT(planning);
  const f = usePlanFormat();
  const { plan } = data;
  const closedMonths = data.health.window.months ?? [];
  const newest = plan.newest_closed ?? closedMonths[closedMonths.length - 1] ?? "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // A field holding an entry it cannot take keeps the draft's old value, so a save would store
  // a value the field does not show (PR #434 review C): no save until it is fixed.
  const [projectionInvalid, setInvalid] = useState(false);
  const [spendingInvalid, setSpendingInvalid] = useState(false);
  // A discard remounts the fields, so none keeps a text the draft no longer holds.
  const [epoch, setEpoch] = useState(0);
  const invalid = projectionInvalid || spendingInvalid;
  const dirty = changes.length > 0;
  const current = currentDefault(data.scenarios);
  const todos = (plan.todos ?? []).filter((todo) => todo.code !== "rate_missing");
  // D263: every draft goes through the preview while a member types; the cards keep the saved
  // values, only „Was eure Änderungen bewirken“ and the Budget's „Mit eurem Plan“ move.
  const preview = usePlanPreview(plan, draft, dirty);
  const sheet = useDetail("scenario");
  const goals = useDetail("goals");
  const budgetClass = useDetail("budget");

  // Phase 9's links into the old page land here: „Ausgaben durchspielen“ and „Weitere Annahmen“
  // are in the scenario sheet now.
  useEffect(() => {
    if (window.location.hash === "#ausgaben-durchspielen" || window.location.hash === "#weitere-annahmen") sheet.open(SHEET);
    // once, on arrival
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // The limits of the key figures stay as the default holds them (they are set on Übersicht).
      const posted = { ...draft, bands: current?.levers?.bands ?? draft.bands ?? [] };
      const row = await saveDefaultScenario(posted, current);
      onSaved(posted, row);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const saveBar = (
    <div className="stack">
      {error != null ? <PlanError error={error} /> : null}
      <div className="row">
        <button type="button" disabled={busy || !dirty || invalid} onClick={() => void save()}>
          {t.saveScenario}
        </button>
        {dirty ? (
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => {
              onDiscard();
              setInvalid(false);
              setSpendingInvalid(false);
              setEpoch((value) => value + 1);
            }}
          >
            {t.discard}
          </button>
        ) : null}
        <span className="muted">{projectionInvalid ? t.fixProjection : spendingInvalid ? t.fixSpending : t.saveHint}</span>
      </div>
    </div>
  );

  const dirtyNotice = dirty ? (
    <p className="notice notice-warn" role="status">
      {t.dirtyNotice}
    </p>
  ) : null;

  let panel: ReactNode = null;
  if (sheet.id !== null) {
    panel = (
      <DetailPanel title={t.sheetTitle} backLabel={t.back} onClose={sheet.close}>
        <ScenarioSheet
          key={epoch}
          data={data}
          draft={draft}
          setDraft={setDraft}
          changes={changes}
          preview={preview}
          saveBar={saveBar}
          onSpendingValidity={setSpendingInvalid}
        />
      </DetailPanel>
    );
  } else if (goals.id !== null) {
    panel = (
      <GoalsPanel
        budget={data.budget}
        saved={fillLevers(plan.levers)}
        current={current}
        onClose={goals.close}
        onSaved={(targets, row) => {
          onGoalsSaved(targets, row);
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
        plan={plan}
        windowMonths={closedMonths}
        preview={preview}
        draft={draft}
        setDraft={setDraft}
        onValidity={setSpendingInvalid}
        onClose={budgetClass.close}
        footer={dirty ? saveBar : null}
      />
    );
  }

  return (
    <DetailLayout panel={panel}>
      <div className="stack planning" key={epoch}>
        {section === "budget" ? (
          <BudgetSection budget={data.budget} onChanged={onChanged} />
        ) : (
          <>
            {section !== "vermoegen" ? (
              <p className="muted">
                {t.baseLine(closedMonths.length, f.monthRange(closedMonths))}
                {section === "reserve" ? t.basePaths(f.longDate(newest ? monthEnd(newest) : null)) : ""}{" "}
                <Link href={`/household?monat=${newest}`}>{t.baseMonths}</Link>
              </p>
            ) : null}
            <PlanTodos todos={todos} onChanged={onChanged} />
            {dirtyNotice}
          </>
        )}
        {section === "reserve" ? (
          <>
            <ReserveKpis data={data} />
            <Levers data={data} draft={draft} setDraft={setDraft} only="reserve" />
            {dirty ? saveBar : null}
            <Paths data={data} />
          </>
        ) : null}
        {section === "anlegen" ? (
          <div className="planning-columns">
            <div className="planning-main stack">
              <InvestKpis data={data} />
              <Levers data={data} draft={draft} setDraft={setDraft} only="buffer" />
              {dirty ? saveBar : null}
              <HowItIsMade data={data} />
            </div>
            <PlanExplanationPanel scenarioId={plan.scenario_id ?? null} asOf={plan.as_of} />
          </div>
        ) : null}
        {section === "vermoegen" ? (
          <ProjectionCard
            data={data}
            draft={draft}
            setDraft={setDraft}
            dirty={dirty}
            saveBar={saveBar}
            onChanged={onChanged}
            onValidity={setInvalid}
          />
        ) : null}
      </div>
    </DetailLayout>
  );
}

function InvestKpis({ data }: { data: PlanData }) {
  const { plan } = data;
  const plans = plan.plans ?? [];
  const investable = surplusRow(plan, "investable");
  const free = surplusRow(plan, "free_cash");
  const t = useT(planning);
  const f = usePlanFormat();
  return (
    <div className="planning-kpis">
      <div className="planning-kpi planning-kpi-main">
        <p className="planning-kpi-label">{t.kpiInvestable}</p>
        <p className="planning-kpi-value">{f.euro(investable?.amount)}</p>
        <p className="planning-kpi-sub">{t.kpiInvestableSub(f.euro(free?.amount), f.percentValue(plan.levers.buffer_percent))}</p>
      </div>
      <div className="planning-kpi">
        <p className="planning-kpi-label">{t.kpiInvested}</p>
        <p className="planning-kpi-value">{f.euro(plan.already_invested ?? "0")}</p>
        <p className="planning-kpi-sub">
          {plans.length === 0
            ? t.kpiNoPlan
            : data.mode === "generic"
              ? t.kpiPlansTogether(plans.length)
              : plans.map((p) => `${planName(p, data.accounts, t)} ${f.euro(p.monthly)}`).join(" · ")}
        </p>
        {plan.investable_left != null ? <p className="planning-kpi-sub">{t.kpiLeft(f.euro(plan.investable_left))}</p> : null}
      </div>
    </div>
  );
}

function ReserveKpis({ data }: { data: PlanData }) {
  const { plan } = data;
  const reserve = plan.reserve;
  const t = useT(planning);
  const f = usePlanFormat();
  return (
    <div className="planning-kpis">
      <div className="planning-kpi planning-kpi-main">
        <p className="planning-kpi-label">{t.kpiReserve}</p>
        <p className="planning-kpi-value">{f.euro(reserve?.amount)}</p>
        <p className="planning-kpi-sub">{reserve?.months != null ? t.kpiReserveMonths(f.short(reserve.months)) : t.kpiReserveNoNeed}</p>
      </div>
      <div className="planning-kpi">
        <p className="planning-kpi-label">{t.kpiGap}</p>
        <p className="planning-kpi-value">{f.euro(reserve?.gap)}</p>
        <p className="planning-kpi-sub">{t.kpiTarget(f.euro(reserve?.target), f.short(plan.levers.reserve_target_months))}</p>
      </div>
    </div>
  );
}

/** The scenario sheet (D270): the unsaved changes, what they do (D271), the save, then the
 * places to change spending and the other assumptions. */
function ScenarioSheet({
  data,
  draft,
  setDraft,
  changes,
  preview,
  saveBar,
  onSpendingValidity,
}: {
  data: PlanData;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  changes: string[];
  preview: PlanPreview;
  saveBar: ReactNode;
  onSpendingValidity: (invalid: boolean) => void;
}) {
  const t = useT(planning);
  return (
    <div className="stack">
      <p className="muted">{t.sheetIntro}</p>
      <section className="stack" aria-labelledby="sheet-changes">
        <h3 id="sheet-changes">{t.changesTitle}</h3>
        {changes.length === 0 ? (
          <p className="muted">{t.noChanges}</p>
        ) : (
          <ul className="planning-change-list">
            {changes.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        )}
      </section>
      <Effects saved={data.plan} preview={preview} />
      {saveBar}
      <SpendingPlaySection
        saved={data.plan}
        windowMonths={data.health.window.months ?? []}
        preview={preview}
        draft={draft}
        setDraft={setDraft}
        onValidity={onSpendingValidity}
      />
      <MoreAssumptions data={data} draft={draft} setDraft={setDraft} />
    </div>
  );
}

export type SetDraft = (update: (previous: HouseholdLevers) => HouseholdLevers) => void;

// --- Stellschrauben -------------------------------------------------------------------------

/** "6" → 12 half-months and back, for the ± of the reserve target (0 to 24 in 0.5 steps). */
function halves(value: string): number {
  return Math.round(Number(value) * 2);
}

function Levers({
  data,
  draft,
  setDraft,
  only,
}: {
  data: PlanData;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  only: "reserve" | "buffer";
}) {
  const reserve = data.plan.reserve;
  const regular = surplusRow(data.plan, "mean_regular_spending");
  const rental = surplusRow(data.plan, "mean_rental_cash");
  const target = halves(draft.reserve_target_months);
  const buffer = Number(draft.buffer_percent);
  const setTarget = (next: number) =>
    setDraft((previous) => ({ ...previous, reserve_target_months: String(Math.max(0, Math.min(48, next)) / 2) }));
  const setBuffer = (next: number) =>
    setDraft((previous) => ({ ...previous, buffer_percent: String(Math.max(0, Math.min(50, next))) }));
  const flatShortfall = rental && rental.amount.startsWith("-") ? rental.amount.slice(1) : null;
  const t = useT(planning);
  const f = usePlanFormat();
  return (
    <section className="card stack" aria-labelledby="levers-title">
      <h2 id="levers-title">{t.leversTitle}</h2>
      <div className="planning-levers">
        {only === "reserve" ? (
          <div className="planning-lever">
            <p className="planning-kpi-label">{t.leverReserve}</p>
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
        ) : (
          <div className="planning-lever">
            <p className="planning-kpi-label">{t.leverBuffer}</p>
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
        )}
      </div>
    </section>
  );
}

// --- Weitere Annahmen -----------------------------------------------------------------------

const INCOME_NOTES: IncomeChangeNote[] = ["parental_leave", "job_change", "part_time", "other"];

function MoreAssumptions({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const t = useT(planning);
  const [open, setOpen] = useState(true);
  return (
    <section className="card stack" id="weitere-annahmen" aria-labelledby="more-title">
      <div className="planning-card-head">
        <h2 id="more-title">{t.moreTitle}</h2>
        <span className="spacer" />
        <button type="button" className="link" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? t.collapse : t.expand}
        </button>
      </div>
      {open ? (
        <>
          <IncomeChanges draft={draft} setDraft={setDraft} />
          <OneOffs data={data} draft={draft} setDraft={setDraft} />
          <ReserveAccountsBlock data={data} draft={draft} setDraft={setDraft} />
          <SavingsPlans data={data} draft={draft} setDraft={setDraft} />
        </>
      ) : null}
    </section>
  );
}

function IncomeChanges({ draft, setDraft }: { draft: HouseholdLevers; setDraft: SetDraft }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const [adding, setAdding] = useState(false);
  const [amount, setAmount] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [note, setNote] = useState<IncomeChangeNote>("other");
  const [errors, setErrors] = useState<Partial<Record<"amount" | "from" | "to", string>>>({});
  const changes = draft.income_changes ?? [];

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = toSignedMoneyString(amount);
    const found: typeof errors = {};
    if (!parsed.ok) found.amount = parsed.message;
    if (!/^\d{4}-\d{2}$/.test(from)) found.from = t.pickMonth;
    if (to !== "" && (!/^\d{4}-\d{2}$/.test(to) || to < from)) found.to = t.endAfterStart;
    setErrors(found);
    if (!parsed.ok || Object.keys(found).length > 0) return;
    setDraft((previous) => ({
      ...previous,
      income_changes: [...(previous.income_changes ?? []), { monthly_net: parsed.value, from_month: from, to_month: to || null, note_code: note }],
    }));
    setAdding(false);
    setAmount("");
    setFrom("");
    setTo("");
  }

  return (
    <div className="stack">
      <h3>{t.incomeTitle}</h3>
      {changes.length === 0 ? (
        <p className="muted">{t.incomeNone}</p>
      ) : (
        <ul className="household-rows">
          {changes.map((change, index) => (
            <li key={`${change.from_month}-${index}`}>
              <span>
                {t.incomeNote(change.note_code)}: {t.incomeSpan(f.month(change.from_month), change.to_month ? f.month(change.to_month) : null)}
              </span>
              <span>
                {t.perMonth(f.signedEuro(change.monthly_net))}{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() =>
                    setDraft((previous) => ({ ...previous, income_changes: (previous.income_changes ?? []).filter((_, i) => i !== index) }))
                  }
                >
                  {t.remove}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form className="stack" onSubmit={add} aria-label={t.incomeFormLabel}>
          <div className="health-limit-fields">
            <Field id={`${ids}-amount`} label={t.incomeAmount} hint={t.incomeAmountHint} errors={errors.amount ? [errors.amount] : []}>
              <input type="text" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
            </Field>
            <Field id={`${ids}-from`} label={t.fromMonth} errors={errors.from ? [errors.from] : []}>
              <input type="month" value={from} onChange={(event) => setFrom(event.target.value)} />
            </Field>
            <Field id={`${ids}-to`} label={t.toMonth} errors={errors.to ? [errors.to] : []}>
              <input type="month" value={to} onChange={(event) => setTo(event.target.value)} />
            </Field>
            <Field id={`${ids}-note`} label={t.reason}>
              <select value={note} onChange={(event) => setNote(event.target.value as IncomeChangeNote)}>
                {INCOME_NOTES.map((code) => (
                  <option key={code} value={code}>
                    {t.incomeNote(code)}
                  </option>
                ))}
              </select>
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
            {t.addIncome}
          </button>
        </p>
      )}
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

function OneOffs({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
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

// --- Vermögen in einigen Jahren -------------------------------------------------------------

function ProjectionCard({
  data,
  draft,
  setDraft,
  dirty,
  saveBar,
  onChanged,
  onValidity,
}: {
  data: PlanData;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  dirty: boolean;
  saveBar: ReactNode;
  onChanged: () => void;
  /** true while a field holds an entry the draft cannot take */
  onValidity: (invalid: boolean) => void;
}) {
  const t = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const projection = data.plan.projection;
  const points = projection?.points ?? [];
  const assumptions = projection?.assumptions ?? [];
  const flat = assumptions.find((a) => a.kind === "flat_growth");
  const rateTodos = (data.plan.todos ?? []).filter((todo) => todo.code === "rate_missing");
  const projectionTodos = projection?.todos ?? [];
  const [texts, setTexts] = useState({
    depot: percentText(draft.value_change?.depot),
    crypto: percentText(draft.value_change?.crypto),
    years: String(draft.net_worth_years),
  });
  const [errors, setErrors] = useState<Partial<Record<"depot" | "crypto" | "years", string>>>({});

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

  const last = points[points.length - 1];
  const rateLine = (kind: "depot_rate" | "crypto_rate", label: string) => {
    const assumption = assumptions.find((a) => a.kind === kind);
    if (!assumption) return null;
    const by = assumption.created_by ? data.names.get(assumption.created_by) : undefined;
    return (
      <li key={kind}>
        {t.rateLine(label, f.ratio(assumption.value), by ?? null, f.longDate(assumption.created_at))}
      </li>
    );
  };

  return (
    <section className="card stack" id="annahme" aria-labelledby="projection-title">
      <h2 id="projection-title">{t.projTitle}</h2>
      <p className="muted">
        {t.projIntro}
      </p>
      <fieldset className="health-limit">
        <legend>{t.projLegend}</legend>
        <div className="health-limit-fields">
          {(["depot", "crypto"] as const).map((key) => (
            <Field key={key} id={`${ids}-${key}`} label={t.rateField(key === "depot" ? t.classDepot : t.classCrypto)} errors={errors[key] ? [errors[key] as string] : []}>
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
          <Link href={flat?.property_id ? `/planning?immobilie=${flat.property_id}` : "/planning?section=immobilie"}>{t.projFlatChange}</Link>
        </p>
      </fieldset>
      {dirty ? saveBar : null}
      {points.length === 0 ? (
        <>
          {rateTodos.length > 0 ? <p className="health-reference-title">{t.rateMissing}</p> : null}
          <PlanTodos todos={[...rateTodos, ...projectionTodos.filter((t) => t.code !== "rate_missing")]} onChanged={onChanged} />
        </>
      ) : (
        <>
          <ul className="muted">
            {rateLine("depot_rate", t.classDepot)}
            {rateLine("crypto_rate", t.classCrypto)}
            <li>
              {t.projFlatLine(flat ? f.ratio(flat.value) : null)}
            </li>
            <li>
              {t.projSameMean(f.monthRange(data.health.window.months ?? []))}
            </li>
            <li>{t.projNominal}</li>
          </ul>
          {last ? (
            <p className="cashflow-value">
              {t.projAt(f.longDate(last.on), f.euro(last.total))}
            </p>
          ) : null}
          {projection?.start ? (
            <p className="muted">
              {t.projStart(f.longDate(projection.start.on), f.euro(projection.start.total))}
            </p>
          ) : null}
          <ProjectionTable start={projection?.start ?? null} points={points} />
          {projection?.ends_before_sale_year != null ? (
            <p className="muted">
              {t.projEnds(projection.ends_before_sale_year - 1)}{" "}
              <Link href="/planning?section=immobilie">{t.projEndsLink}</Link>
            </p>
          ) : null}
          {projection?.used_up ? (
            <p className="muted">{t.projUsedUp(f.month(projection.used_up))}</p>
          ) : null}
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
