"use client";

// The goal's path on the screens (phase 10 §7.11 (i), D311; mockup 19j): on „Euer Plan“ the card
// „Heute angelegt“, the plan card per state (Machbar at the typed rates or at a higher return,
// Ehrgeizig, Kaum erreichbar), „Woher“ for the saving ceiling, „Diesen Plan übernehmen“, „Diesen
// Monat“ and, once adopted and followed, the months since; the three rows of Übersicht's goal
// tile; and the chart of „Für euer Ziel“ on Anlagen. Every figure is the server's
// (GET /household-goal's `goal_path`, GET /household-goal/tracking's `rows` and `points`); the
// browser formats, sums nothing and decides nothing (a gap or a word is the server's).
//
// The one write: „Diesen Plan übernehmen“ posts POST /household-goal/path/adopt (no body); the
// server writes the default scenario's `goal_path` lever and follows it, refusing a path that is
// no longer adoptable (422 `household_scenario.path_invalid`). The page then reads again.
import Link from "next/link";
import { type ReactNode, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { goalPathText } from "@/i18n/goalPath";
import { planPageText } from "@/i18n/planPage";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { HOUSEHOLD_BUDGET, HOUSEHOLD_PLAN_PAGE } from "@/lib/planningRoutes";

import { hasGap, type PathView, usePathWord } from "./goalPathWord";
import { CardHead, GOAL_FORM_HREF, Stat, Stats } from "./householdGoal";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";

import "./goalPath.css";

type HouseholdGoal = components["schemas"]["HouseholdGoal"];
export type GoalTracking = components["schemas"]["GoalTracking"];
type GoalPathRow = components["schemas"]["GoalPathRow"];
type GoalPathPoint = components["schemas"]["GoalPathPoint"];
type T = ReturnType<typeof useT<typeof goalPathText.de>>;

/** The side panels' ids on „Euer Plan“ (detail kind `plan`); the plan card keeps `weg`, so the
 * split's form („Aufteilung ändern“) stays at #d-plan-weg. */
export const PATH_IDS = { today: "angelegt", ceiling: "woher", route: "weg", month: "monat", since: "verlauf" } as const;
const KIND = "plan";

/** The tracking read; the screens show without it. */
export const loadPathTracking = (): Promise<GoalTracking | null> =>
  ok(api.GET("/api/household-goal/tracking")).catch(() => null);

export async function adoptPath(): Promise<void> {
  await ok(api.POST("/api/household-goal/path/adopt"));
}

function signedPercent(p: PlanFormat, value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  return value.startsWith("-") ? p.percentValue(value) : `+${p.percentValue(value)}`;
}

/** A row subtracted from the ceiling: „− 2.600 €“; a negative one (refunds above spending) adds. */
function minus(p: PlanFormat, value: string): string {
  return value.startsWith("-") ? `+ ${p.euro(value.slice(1))}` : `− ${p.euro(value)}`;
}

/** The newest row the tracking has (the latest closed month-end since the day adopted). */
export function latestRow(tracking: GoalTracking | null | undefined): GoalPathRow | null {
  const rows = tracking?.rows ?? [];
  return rows.length > 0 ? rows[rows.length - 1]! : null;
}

function OnPath({ t, behind, text }: { t: T; behind: boolean; text?: string }) {
  return (
    <span className={behind ? "goal-tag goal-tag-maybe" : "goal-tag goal-tag-yes"}>{text ?? (behind ? t.behind : t.onPath)}</span>
  );
}

// --- „Euer Plan“ -------------------------------------------------------------------------------

/** The cards of the path in place of the plan's route and month cards („Für euch“ only). */
export function PathCards({ goal, tracking, onAdopted }: { goal: HouseholdGoal; tracking: GoalTracking | null; onAdopted: () => void }) {
  const word = usePathWord(goal);
  if (!word) return null;
  const view = word.view;
  const rows = view.followed ? (tracking?.rows ?? []) : [];
  return (
    <>
      <TodayCard view={view} />
      {view.state === "on_track" || view.state === "doable_rates" ? <PlanCard view={view} onAdopted={onAdopted} /> : null}
      {view.state === "doable_return" ? (
        <>
          <CeilingCard view={view} />
          <PlanCard view={view} onAdopted={onAdopted} />
        </>
      ) : null}
      {view.state === "ambitious" ? (
        <>
          <CeilingCard view={view} />
          <ReturnCard view={view} />
          <RealisticCard view={view} goal={goal} />
        </>
      ) : null}
      {view.state === "hardly" ? (
        <>
          <WhyCard view={view} />
          {/* „Kaum erreichbar“ has no plan card: „Jetzt anlegen“ goes on this one */}
          <RealisticCard view={view} goal={goal} budgetLink investNow />
        </>
      ) : null}
      {view.adopted ? <MonthCard view={view} row={rows.length > 0 ? rows[rows.length - 1]! : null} /> : null}
      {view.adopted && view.followed ? <SinceCard rows={rows} /> : null}
    </>
  );
}

function TodayCard({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const today = view.path.today;
  if (!today) return null;
  const plans = today.plans ?? [];
  return (
    <div className="goal-c" data-testid="path-today">
      <CardHead kind={KIND} id={PATH_IDS.today} title={t.todayCard} />
      <Stats count={2}>
        <Stat value={p.euro(today.invested)} label={plans.length > 0 ? `${t.investedLabel} · ${t.plansCount(plans.length)}` : t.investedLabel} />
        <Stat value={p.euro(today.saved_not_invested)} label={t.savedNotInvested} />
      </Stats>
    </div>
  );
}

function AdoptedLine({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const adopted = view.adopted;
  if (!adopted) return null;
  const day = p.date(adopted.adopted_on);
  if (view.current) return <p className="goal-one goal-path-adopted">{t.adoptedTag(day)}</p>;
  return <p className="goal-one goal-path-adopted">{view.followed ? t.adoptedMoved(day) : t.adoptedOther(day)}</p>;
}

/** „Diesen Plan übernehmen“: only while the path is adoptable and not already the one followed. */
function AdoptButton({ view, onAdopted }: { view: PathView; onAdopted: () => void }) {
  const t = useT(goalPathText);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!view.path.adoptable || view.current) return null;
  const adopt = async () => {
    setBusy(true);
    setError(null);
    try {
      await adoptPath();
      onAdopted();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="goal-path-adopt" disabled={busy} onClick={adopt}>
        {view.adopted ? t.adoptAgain : t.adopt}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** „Jetzt anlegen“: the money above the reserve target today (the server's `above_target`),
 * which the plan invests in its first month; on the path's plan card in every state, only
 * when above 0. Its split in funds and crypto is in the card's panel. */
function InvestNowRow({ view }: { view: PathView }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const amount = view.path.above_target;
  if (!hasGap(amount)) return null;
  return (
    <div className="goal-rows" data-testid="path-invest-now">
      <div className="goal-rw">
        <span>{t.investNow}</span>
        <b>{p.euro(amount)}</b>
      </div>
    </div>
  );
}

/** „Der Plan“ at step 1 (the typed rates) or step 2 (the return needed, within the room). */
function PlanCard({ view, onAdopted }: { view: PathView; onAdopted: () => void }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const atReturn = view.state === "doable_return";
  return (
    <div className="goal-c goal-c-main" data-testid="path-plan" data-state={view.state}>
      <CardHead kind={KIND} id={PATH_IDS.route} title={t.planTitle} />
      {atReturn ? (
        <>
          <Stats count={2}>
            <Stat value={view.return_needed ? t.perYear(p.ratio(view.return_needed)) : "–"} label={t.returnNeeded} />
            <Stat value={p.euro(view.monthly)} label={t.investMonthly} />
          </Stats>
          {view.windows != null && view.reached_windows != null && view.path.window_years != null ? (
            <p className="goal-one">{t.windowsLine(view.path.window_years, view.reached_windows, view.windows)}</p>
          ) : null}
        </>
      ) : (
        <>
          <Stats count={2}>
            <Stat value={p.euro(view.monthly)} label={t.investMonthly} />
            <Stat value={p.euro(view.wants_budget_total)} label={t.wantsBudget} />
          </Stats>
          <p className="goal-one">
            {hasGap(view.extra) ? t.extraSub(p.euro(view.extra)) : t.sameAsToday}
            {view.rate ? ` · ${t.atYourRates}: ${t.mix(p.ratio(view.rate))}` : ""}
          </p>
        </>
      )}
      <InvestNowRow view={view} />
      <AdoptedLine view={view} />
      <AdoptButton view={view} onAdopted={onAdopted} />
    </div>
  );
}

/** „Mehr ist nicht drin“: the most that can be invested a month; „Woher“ in its panel. */
function CeilingCard({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  return (
    <div className="goal-c" data-testid="path-ceiling">
      <CardHead kind={KIND} id={PATH_IDS.ceiling} title={t.ceilingTitle} />
      <Stats count={1}>
        <Stat value={p.euro(view.path.ceiling.ceiling)} label={t.ceilingLabel} />
      </Stats>
    </div>
  );
}

function historyLine(t: T, view: PathView): string | null {
  const years = view.path.years ?? [];
  if (!view.path.short_history) return null;
  return years.length > 0 ? t.shortHistory(years[0]!, years[years.length - 1]!) : t.shortHistoryPlain;
}

/** Ehrgeizig: the return needed and how often the mix reached it; never adopted. */
function ReturnCard({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const history = historyLine(t, view);
  return (
    <div className="goal-c" data-testid="path-return">
      <CardHead kind={KIND} id={PATH_IDS.route} title={t.returnNeeded} />
      <Stats count={2}>
        <Stat value={view.return_needed ? t.perYear(p.ratio(view.return_needed)) : t.noReturn} label={t.returnNeeded} />
        {view.windows != null && view.reached_windows != null ? (
          <Stat value={t.windowsValue(view.reached_windows, view.windows)} label={t.pastReached} warn />
        ) : null}
      </Stats>
      {history ? <p className="goal-one">{history}</p> : null}
      <InvestNowRow view={view} />
    </div>
  );
}

/** Kaum erreichbar: why, in figures (the ceiling, the return it would need, how often reached). */
function WhyCard({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  // Step 2 was judged when `windows` is set; a return above the search's 100 % a year comes
  // without a return and without a count (rules/goal/goal.py `_step_2`).
  const judged = view.windows != null;
  const counted = judged && view.windows! > 0 && view.reached_windows != null;
  return (
    <div className="goal-c" data-testid="path-why">
      <CardHead kind={KIND} id={PATH_IDS.ceiling} title={t.whyTitle} />
      <Stats count={1 + (judged ? 1 : 0) + (counted ? 1 : 0)}>
        <Stat value={p.euro(view.path.ceiling.ceiling)} label={t.ceilingLabel} />
        {judged ? <Stat value={view.return_needed ? t.perYear(p.ratio(view.return_needed)) : t.noReturn} label={t.returnForIt} /> : null}
        {counted ? <Stat value={t.windowsValue(view.reached_windows!, view.windows!)} label={t.pastReached} warn /> : null}
      </Stats>
    </div>
  );
}

/** What the most that can be invested reaches: the value at the due month and the month reached. */
function RealisticCard({
  view,
  goal,
  budgetLink = false,
  investNow = false,
}: {
  view: PathView;
  goal: HouseholdGoal;
  budgetLink?: boolean;
  investNow?: boolean;
}) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const why = view.path.why;
  if (!why || !goal.goal) return null;
  return (
    <div className="goal-c" data-testid="path-realistic">
      <CardHead kind={KIND} id={PATH_IDS.route} title={t.realisticTitle} />
      <Stats count={2}>
        <Stat value={p.euro(why.due_value)} label={t.byDue(p.month(goal.goal.due_month))} />
        <Stat value={why.reached ? p.month(why.reached) : t.notReached} label={t.targetReached(p.euro(goal.goal.target))} />
      </Stats>
      {investNow ? <InvestNowRow view={view} /> : null}
      <div className="goal-path-links">
        <Link href={GOAL_FORM_HREF} className="goal-lnk">
          {t.changeGoal} ›
        </Link>
        {budgetLink ? (
          <Link href={HOUSEHOLD_BUDGET} className="goal-lnk">
            {t.viewBudget} ›
          </Link>
        ) : null}
      </div>
    </div>
  );
}

/** „Diesen Monat“: the full monthly amount and the wants budget of the adopted path (its stored
 * figures, even once the path read today has moved, so they match the row's `planned`), and the
 * newest closed month compared, when there is one. */
function MonthCard({ view, row }: { view: PathView; row: GoalPathRow | null }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  return (
    <div className="goal-c" data-testid="path-month">
      <CardHead kind={KIND} id={PATH_IDS.month} title={t.monthCard} />
      <div className="goal-rows">
        <div className="goal-rw">
          <span>{t.monthInvest(p.euro(view.adopted?.monthly ?? view.monthly))}</span>
        </div>
        <div className="goal-rw">
          <span>{t.monthWants(p.euro(view.adopted?.wants_budget_total ?? view.wants_budget_total))}</span>
        </div>
      </div>
      {row ? <RowFigures row={row} day={view.adopted?.adopted_on ?? null} /> : null}
    </div>
  );
}

/** One closed month's four comparisons (19j frame 5), each with the server's verdict. */
function RowFigures({ row, day }: { row: GoalPathRow; day: string | null }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  return (
    <div className="goal-path-row" data-testid="path-row">
      <h4 className="goal-kicker">{t.lastCard(p.month(row.month_end))}</h4>
      <PathLine label={t.rowInvested} sub={t.ofPlan(p.euro(row.planned))} value={p.euro(row.invested)}>
        <OnPath t={t} behind={hasGap(row.invested_gap)} text={hasGap(row.invested_gap) ? t.short(p.euro(row.invested_gap)) : undefined} />
      </PathLine>
      {hasGap(row.waiting) ? <p className="goal-note">{t.waiting(p.euro(row.waiting))}</p> : null}
      <PathLine label={t.rowWantsTracked} sub={t.ofBudget(p.euro(row.wants_budget))} value={p.euro(row.wants)}>
        <OnPath t={t} behind={hasGap(row.wants_gap)} text={hasGap(row.wants_gap) ? t.over(p.euro(row.wants_gap)) : undefined} />
      </PathLine>
      <PathLine label={t.rowGrowth(day ? p.date(day) : "–")} sub={t.needed(signedPercent(p, row.needed_percent))} value={signedPercent(p, row.actual_percent)}>
        <OnPath t={t} behind={row.value_behind} />
      </PathLine>
      {row.path_net_worth != null ? (
        <PathLine label={t.rowNetWorth} sub={t.ofPath(p.euro(row.path_net_worth))} value={p.euro(row.net_worth)}>
          <OnPath t={t} behind={hasGap(row.net_worth_gap)} text={hasGap(row.net_worth_gap) ? t.behindBy(p.euro(row.net_worth_gap)) : undefined} />
        </PathLine>
      ) : null}
    </div>
  );
}

function PathLine({ label, sub, value, children }: { label: string; sub: string; value: string; children: ReactNode }) {
  return (
    <div className="goal-path-line">
      <span className="goal-path-label">
        {label}
        <small>{sub}</small>
      </span>
      <b>{value}</b>
      {children}
    </div>
  );
}

/** „Seit ihr dem Plan folgt“: each closed month since the day adopted, the newest first. */
function SinceCard({ rows }: { rows: GoalPathRow[] }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  return (
    <div className="goal-c" data-testid="path-since">
      <CardHead kind={KIND} id={PATH_IDS.since} title={t.sinceTitle} />
      {rows.length === 0 ? (
        <p className="goal-one">{t.noRowsYet}</p>
      ) : (
        <ul className="goal-path-since">
          {[...rows].reverse().map((row) => (
            <li key={row.month_end}>
              <b>{p.month(row.month_end)}</b>
              <span>
                {t.sinceInvested(p.euro(row.invested))}
                <small>{t.sinceGrowth(signedPercent(p, row.actual_percent), signedPercent(p, row.needed_percent))}</small>
              </span>
              <OnPath t={t} behind={row.word === "behind"} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- the side panels on „Euer Plan“ ------------------------------------------------------------

/** The panel's title for one of the path's ids, or null when the id is not the path's. */
export function usePathPanelTitle(goal: HouseholdGoal | null | undefined, id: string): string | null {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const word = usePathWord(goal);
  if (!word) return null;
  const view = word.view;
  switch (id) {
    case PATH_IDS.today:
      return view.path.today ? t.todayCard : null;
    case PATH_IDS.ceiling:
      return t.woherTitle(p.euro(view.path.ceiling.ceiling));
    case PATH_IDS.route:
      if (view.state === "ambitious") return t.returnNeeded;
      if (view.state === "hardly") return t.realisticTitle;
      return t.planTitle;
    case PATH_IDS.month:
      return view.adopted ? t.monthCard : null;
    case PATH_IDS.since:
      return view.adopted && view.followed ? t.sinceTitle : null;
    default:
      return null;
  }
}

/** The panel's body; `extra` is what the page adds under the plan's panel (the split's form). */
export function PathPanelBody({ goal, id, extra }: { goal: HouseholdGoal; id: string; extra?: ReactNode }) {
  const word = usePathWord(goal);
  if (!word) return null;
  const view = word.view;
  if (id === PATH_IDS.today) return <TodayPanel view={view} />;
  if (id === PATH_IDS.ceiling) return <CeilingPanel view={view} />;
  if (id === PATH_IDS.route)
    return (
      <>
        <RoutePanel view={view} due={goal.goal?.due_month ?? null} target={goal.goal?.target ?? null} />
        {extra}
      </>
    );
  if (id === PATH_IDS.month || id === PATH_IDS.since) return <TrackingPanel view={view} />;
  return null;
}

function TodayPanel({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const today = view.path.today;
  if (!today) return null;
  const plans = today.plans ?? [];
  return (
    <>
      <p>{t.todayPanelInvested(p.euro(today.invested))}</p>
      <h3 className="goal-kicker">{t.plansTitle}</h3>
      {plans.length === 0 ? (
        <p className="goal-one">{t.noPlans}</p>
      ) : (
        <div className="goal-rows">
          {plans.map((plan) => (
            <div key={plan.key} className="goal-rw goal-rw-sub">
              <span>
                {plan.destination_payee ?? t.planClass(plan.asset_class)} · {t.planClass(plan.asset_class)}
              </span>
              <b>{t.perMonth(p.euro(plan.monthly))}</b>
            </div>
          ))}
        </div>
      )}
      <p>{t.todayPanelWalk}</p>
      {hasGap(today.saved_not_invested) ? <p>{t.todayPanelNot(p.euro(today.saved_not_invested))}</p> : null}
    </>
  );
}

function CeilingPanel({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const c = view.path.ceiling;
  return (
    <>
      <p className="goal-one">{t.woherIntro}</p>
      <div className="goal-rows goal-path-woher" data-testid="path-woher">
        <div className="goal-rw">
          <span>{t.rowIncome}</span>
          <b>{p.euro(c.income)}</b>
        </div>
        <div className="goal-rw">
          <span>{t.rowRental}</span>
          <b>+ {p.euro(c.rental_cash)}</b>
        </div>
        <div className="goal-rw">
          <span>{t.rowNeed}</span>
          <b>{minus(p, c.need)}</b>
        </div>
        <div className="goal-rw">
          <span>
            {t.rowWants}
            {c.suggested ? <span className="goal-tag goal-tag-maybe goal-path-suggested">{t.suggested}</span> : null}
          </span>
          <b>{minus(p, c.wants_kept)}</b>
        </div>
        <div className="goal-rw">
          <span>{t.rowOther}</span>
          <b>{minus(p, c.other_spending)}</b>
        </div>
        <div className="goal-rw">
          <span>{t.rowBuffer}</span>
          <b>{minus(p, c.buffer)}</b>
        </div>
        <div className="goal-rw goal-path-total">
          <span>{t.rowCeiling}</span>
          <b>{p.euro(c.ceiling)}</b>
        </div>
      </div>
      <p>{c.suggested ? t.woherSuggested : t.woherOwn}</p>
      <p className="goal-note">{t.woherOther}</p>
      <p>{t.woherRoom(p.euro(view.path.anlegbar), p.euro(view.path.room))}</p>
      <Link href={HOUSEHOLD_BUDGET} className="goal-lnk">
        {t.setWants} ›
      </Link>
    </>
  );
}

function RoutePanel({ view, due, target }: { view: PathView; due: string | null; target: string | null }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const history = historyLine(t, view);
  return (
    <>
      <PlanSteps view={view} target={target} />
      {view.state === "ambitious" && view.windows != null && view.reached_windows != null ? <p>{t.ambitiousText(view.reached_windows, view.windows)}</p> : null}
      {view.state === "hardly" ? <p>{view.windows != null ? t.hardlyText : t.hardlyNoHistory}</p> : null}
      {(view.state === "ambitious" || view.state === "hardly") && view.path.why && due ? (
        <p>
          {t.realisticText(p.month(due), p.euro(view.path.why.due_value))}
          {view.path.why.reached && target ? ` ${t.realisticReached(p.euro(target), p.month(view.path.why.reached))}` : ""}
        </p>
      ) : null}
      {view.state === "doable_return" && view.windows != null && view.reached_windows != null && view.path.window_years != null ? (
        <p>{t.windowsLine(view.path.window_years, view.reached_windows, view.windows)}</p>
      ) : null}
      {history ? <p className="goal-note">{history}</p> : null}
      {view.path.mix_today_share != null && view.path.mix_future_share != null ? (
        <>
          <h3 className="goal-kicker">{t.mixTitle}</h3>
          <div className="goal-rows">
            <div className="goal-rw">
              <span>{t.mixToday}</span>
              <b>{p.percentValue(view.path.mix_today_share)}</b>
            </div>
            <div className="goal-rw">
              <span>{t.mixFuture}</span>
              <b>{p.percentValue(view.path.mix_future_share)}</b>
            </div>
          </div>
        </>
      ) : null}
      {view.path.adoptable ? <p className="goal-one">{t.adoptText}</p> : null}
    </>
  );
}

/** „Was ihr tun müsst“, the plan's steps at the top of the route panel in every state: each a
 * sentence with the server's figure (goal_path.actions), and the month the plan then reaches the
 * goal (goal_path.plan_reached). Nothing is summed here; a step the server sends without its
 * figure is left out. */
function PlanSteps({ view, target }: { view: PathView; target: string | null }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const rows: { key: string; text: string; sub?: string }[] = [];
  for (const [i, a] of (view.path.actions ?? []).entries()) {
    const key = `${a.kind}-${a.code ?? i}`;
    const split = a.amount_depot != null && a.amount_crypto != null ? t.stepSplit(p.euro(a.amount_depot), p.euro(a.amount_crypto)) : undefined;
    if (a.kind === "save_monthly" && a.amount != null) rows.push({ key, text: t.stepSave(p.euro(a.amount)), sub: split });
    else if (a.kind === "invest_now" && a.amount != null) rows.push({ key, text: t.stepInvestNow(p.euro(a.amount)), sub: split });
    else if (a.kind === "keep_reserve" && a.amount != null)
      // below its target the walk fills the reserve before it invests anything
      rows.push({ key, text: a.within_target === false ? t.stepReserveFill(p.euro(a.amount)) : t.stepReserve(p.euro(a.amount)) });
    else if (a.kind === "wants_cut" && a.code && a.mean != null && a.amount != null)
      rows.push({ key, text: t.stepWantCut(ALLOCATION_CATEGORY[a.code] ?? a.code, p.euro(a.mean), p.euro(a.amount)) });
    else if (a.kind === "wants_none" && a.amount != null) {
      // on track by the adopted budget the plan counts on it; „the plan works with your X“ only
      // on plain on track; elsewhere a neutral row
      if (a.adopted_budget) rows.push({ key, text: t.stepWantsBudget(p.euro(a.amount)) });
      else if (!a.within_target && view.state === "on_track") rows.push({ key, text: t.stepWantsAbove(p.euro(a.amount)) });
      else if (!a.within_target) rows.push({ key, text: t.stepWantsKeep(p.euro(a.amount)) });
      else if (a.wants_percent != null) rows.push({ key, text: t.stepWantsWithinTarget(p.euro(a.amount), p.percentValue(a.wants_percent)) });
      else rows.push({ key, text: t.stepWantsWithinSuggestion(p.euro(a.amount), p.euro(view.path.ceiling.wants_kept)) });
    } else if (a.kind === "return_needed" && a.rate != null) rows.push({ key, text: t.stepReturn(p.ratio(a.rate)) });
  }
  if (rows.length === 0) return null;
  // without a path and without a return needed the month is the why's, which the panel's
  // „realistic“ sentence below already names
  const reached = !view.path.adoptable && view.path.return_needed == null ? null : view.path.plan_reached;
  return (
    <>
      <h3 className="goal-kicker">{t.stepsTitle}</h3>
      <ol className="goal-steps" data-testid="path-steps">
        {rows.map((row) => (
          <li key={row.key}>
            {row.text}
            {row.sub ? <span className="goal-steps-sub">{row.sub}</span> : null}
          </li>
        ))}
      </ol>
      {reached && target ? <p className="goal-one">{t.stepsReached(p.euro(target), p.month(reached))}</p> : null}
    </>
  );
}

function TrackingPanel({ view }: { view: PathView }) {
  const t = useT(goalPathText);
  return (
    <>
      <p>{t.monthText}</p>
      {view.followed ? <p>{t.trackingText}</p> : null}
      <p className="goal-note">{t.waitingText}</p>
    </>
  );
}

// --- Übersicht's goal tile -------------------------------------------------------------------

/** The adopted plan in three rows under the goal tile (19j frame 7), from the newest closed
 * month the tracking compares; nothing while the plan is not adopted and followed. */
export function PathTileRows({ goal, tracking }: { goal: HouseholdGoal; tracking: GoalTracking | null }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const word = usePathWord(goal);
  const row = latestRow(tracking);
  if (!word || !word.view.adopted || !word.view.followed || !row) return null;
  return (
    <Link href={HOUSEHOLD_PLAN_PAGE} className="tile-goal-path" data-testid="goal-tile-path">
      <span className="tile-goal-path-head">{t.tilePlan(p.month(row.month_end))} ›</span>
      <span className="tile-goal-path-row">
        <span>{t.tileInvest(p.euro(row.planned))}</span>
        <OnPath t={t} behind={hasGap(row.invested_gap)} text={hasGap(row.invested_gap) ? t.short(p.euro(row.invested_gap)) : undefined} />
      </span>
      <span className="tile-goal-path-row">
        <span>{t.tileWants(p.euro(row.wants_budget))}</span>
        <OnPath t={t} behind={hasGap(row.wants_gap)} text={hasGap(row.wants_gap) ? t.over(p.euro(row.wants_gap)) : undefined} />
      </span>
      <span className="tile-goal-path-row">
        <span>{t.tileGrowth}</span>
        <OnPath t={t} behind={row.value_behind} />
      </span>
    </Link>
  );
}

// --- Anlagen's chart -------------------------------------------------------------------------

const CHART = { width: 340, height: 120, left: 34, right: 330, top: 10, bottom: 96 };

/** A display position only (Number for drawing, never for a figure shown). */
function num(value: string | null | undefined): number | null {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The investments' growth against the growth needed, per closed month-end since the day
 * adopted (19j frame 8): a line each, a dot where the investments are behind, the axis from the
 * lowest value (0 at least) to the highest. `compact` draws the lines only, for the card. */
export function PathGrowthChart({ points, rate, compact = false }: { points: GoalPathPoint[]; rate: string | null; compact?: boolean }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  if (points.length === 0) return <p className="goal-one">{t.noPointsYet}</p>;
  const values = points.flatMap((point) => [num(point.actual), num(point.needed)]).filter((v): v is number => v != null);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  const x = (i: number) => (points.length === 1 ? (CHART.left + CHART.right) / 2 : CHART.left + ((CHART.right - CHART.left) * i) / (points.length - 1));
  const y = (v: number) => CHART.bottom - ((v - low) / span) * (CHART.bottom - CHART.top);
  const line = (key: "actual" | "needed") =>
    points
      .map((point, i) => {
        const v = num(point[key]);
        return v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      })
      .filter((s): s is string => s != null);
  const actual = line("actual");
  const needed = line("needed");
  const first = points[0]!;
  const last = points[points.length - 1]!;
  return (
    <figure className={compact ? "goal-path-chart goal-path-chart-compact" : "goal-path-chart"} data-testid="path-chart">
      <svg viewBox={`0 0 ${CHART.width} ${CHART.height}`} role="img" aria-label={t.chartLabel}>
        <line x1={CHART.left} x2={CHART.right} y1={y(0)} y2={y(0)} stroke="var(--border)" strokeWidth={1} />
        {!compact ? (
          <>
            <text x={CHART.left - 4} y={y(high) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">
              {signedPercent(p, String(high))}
            </text>
            <text x={CHART.left - 4} y={y(low) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">
              {signedPercent(p, String(low))}
            </text>
            <text x={x(0)} y={CHART.height - 6} textAnchor={points.length === 1 ? "middle" : "start"} fontSize="10" fill="var(--muted)">
              {p.month(first.month_end)}
            </text>
            {points.length > 1 ? (
              <text x={x(points.length - 1)} y={CHART.height - 6} textAnchor="end" fontSize="10" fill="var(--muted)">
                {p.month(last.month_end)}
              </text>
            ) : null}
          </>
        ) : null}
        {needed.length > 1 ? <polyline points={needed.join(" ")} fill="none" stroke="var(--muted)" strokeWidth={2} strokeDasharray="5 4" /> : null}
        {actual.length > 1 ? <polyline points={actual.join(" ")} fill="none" stroke="var(--accent)" strokeWidth={2.5} /> : null}
        {points.map((point, i) => {
          const v = num(point.actual);
          if (v == null) return null;
          return <circle key={point.month_end} cx={x(i)} cy={y(v)} r={point.behind ? 4 : 2.5} fill={point.behind ? "var(--warn)" : "var(--accent)"} />;
        })}
      </svg>
      {!compact ? (
        <figcaption className="goal-path-legend">
          <span className="goal-path-key goal-path-key-actual">{t.legendActual}</span>
          <span className="goal-path-key goal-path-key-needed">{rate ? t.legendNeeded(p.ratio(rate)) : t.legendNeededPlain}</span>
          {points.some((point) => point.behind) ? <span className="goal-path-key goal-path-key-behind">{t.legendBehind}</span> : null}
        </figcaption>
      ) : null}
    </figure>
  );
}

/** The chart's last point in words: „Bis Juli +6,9 % (nötig +6,2 %)“, by its month-end. */
export function PathChartLast({ points }: { points: GoalPathPoint[] }) {
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const last = points[points.length - 1];
  if (!last) return null;
  return <p className="goal-one">{t.lastLine(p.month(last.month_end), signedPercent(p, last.actual), signedPercent(p, last.needed))}</p>;
}

