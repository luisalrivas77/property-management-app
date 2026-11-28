"use client";

// „Euer Plan“ (phase 10 §7.11 (h), D306; mockup 19i): the household's plan as seven cards under
// Haushalt › Ziel, each a title, a figure or two and at most one short line; a tapped card opens
// its explanation in the side panel (D267, „Cards, not text“). Every figure is the server's
// (GET /household-goal/plan, from the goal's one walk); the browser formats and sums nothing.
//
// Two views, chosen at the top and kept in the address (?ansicht=teilen): „Für euch“ with
// everything, „Zum Teilen“ for showing someone on the screen. The shared view is built by the
// server: it rounds the amounts and leaves out what the five switches of „Was geteilt wird“ keep
// back (the key is absent from its answer, never hidden here), and it never carries a name, the
// property's address or label, a tenant, an IBAN or a wallet address. The switches are kept on
// this device only (lib/deviceSetting, guarded against a missing or refusing localStorage); no
// setting and no migration. No PDF, no link: nothing leaves the app.
//
// The one write: „Übernehmen“ on „Diesen Monat“'s wants step adopts the balanced route's spending
// changes as the budget, after a confirmation in the page, „Für euch“ only. It goes through the
// household scenarios' own endpoint like Haushalt › Ziel's „Als Budget übernehmen“: from the
// default scenario in force, every other lever as saved, the choice „own“ with today's date (the
// enum has no balanced value; the step counts as done once the default's changes are the route's).
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Fragment, type ReactNode, useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { goalPathText } from "@/i18n/goalPath";
import { planPageText } from "@/i18n/planPage";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { deviceSetting, useDeviceSetting } from "@/lib/deviceSetting";
import { assumptionsHref, HOUSEHOLD_RESERVE } from "@/lib/planningRoutes";

import { DetailLayout, DetailPanel, useDetail } from "./DetailPanel";
import { todayIso } from "./format";
import { type GoalTracking, loadPathTracking, PathCards, PathPanelBody, usePathPanelTitle } from "./goalPathCards";
import { pathView, usePathWord } from "./goalPathWord";
import { HealthTodoList } from "./healthFigures";
import { currentDefault, fillLevers, loadScenarioHistory, saveDefaultScenario } from "./healthShared";
import { CardHead, GOAL_FORM_HREF, GOAL_PAGE, type HouseholdGoal, loadGoal, Stat, Stats, WordPill } from "./householdGoal";
import { SplitOnPlanPage } from "./investmentSplit";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./householdGoal.css";
import "./householdPlanPage.css";

type PlanPage = components["schemas"]["PlanPage"];
type PlanSwitches = components["schemas"]["PlanSwitches"];
type PlanStep = components["schemas"]["PlanStep"];
type GoalWord = components["schemas"]["GoalWord"];
type PT = ReturnType<typeof useT<typeof planPageText.de>>;

/** The goal's path (§7.11 (i), D311) and its tracking, read beside the plan for „Für euch“ only:
 * the shared view never asks for them, so nothing exact reaches „Zum Teilen“. */
interface PathData {
  goal: HouseholdGoal | null;
  tracking: GoalTracking | null;
}

const loadPathGoal = (): Promise<HouseholdGoal | null> => loadGoal().catch(() => null);

async function loadNothing(): Promise<null> {
  return null;
}

/** The side panels' detail kind and the cards' ids. */
const KIND = "plan";
const P = {
  goal: "ziel",
  heute: "heute",
  route: "weg",
  assumptions: "annahmen",
  risks: "risiken",
  other: "wege",
  month: "monat",
  share: "teilen",
} as const;

/** The view in the address: Für euch by default, ?ansicht=teilen for Zum Teilen. */
const VIEW_PARAM = "ansicht";
const VIEW_SHARE = "teilen";

// --- „Was geteilt wird“: five switches, one character each, kept on this device ---------------

const SWITCH_NAMES = ["round_amounts", "accounts", "wants", "risks", "assumptions"] as const;
type SwitchName = (typeof SWITCH_NAMES)[number];
/** D306's defaults: round on, accounts off, wants off, risks on, assumptions on. */
const SWITCH_DEFAULT = "10011";
const SWITCH_CODES = Array.from({ length: 32 }, (_, n) => n.toString(2).padStart(5, "0"));
export const PLAN_SHARE_SETTING = deviceSetting("hausbuch.planShare", SWITCH_CODES, SWITCH_DEFAULT);

function decode(code: string): PlanSwitches {
  const on = (i: number) => code[i] === "1";
  return { round_amounts: on(0), accounts: on(1), wants: on(2), risks: on(3), assumptions: on(4) };
}

function encode(switches: PlanSwitches): string {
  return SWITCH_NAMES.map((name) => (switches[name] ? "1" : "0")).join("");
}

function loadPlan(share: boolean, code: string): Promise<PlanPage> {
  const switches = decode(code);
  const query = share
    ? {
        view: "share" as const,
        round_amounts: switches.round_amounts,
        accounts: switches.accounts,
        wants: switches.wants,
        risks: switches.risks,
        assumptions: switches.assumptions,
      }
    : { view: "own" as const };
  return ok(api.GET("/api/household-goal/plan", { params: { query } }));
}

// --- the page --------------------------------------------------------------------------------

export function PlanPageView() {
  const t = useT(planPageText);
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const share = search.get(VIEW_PARAM) === VIEW_SHARE;
  const code = useDeviceSetting(PLAN_SHARE_SETTING);
  const load = useCallback(() => loadPlan(share, code), [share, code]);
  const state = useLoad(load);
  // The goal and the tracking apart, so an adopt reads again only the tracking (§7.13 (d) 7).
  const goalState = useLoad(share ? loadNothing : loadPathGoal);
  const trackingState = useLoad(share ? loadNothing : loadPathTracking);
  // The goal an adopt answered with, shown until the goal is read again.
  const [adopted, setAdopted] = useState<{ goal: HouseholdGoal; of: HouseholdGoal | null | undefined } | null>(null);
  const detail = useDetail(KIND);

  const setView = (next: boolean) => {
    const params = new URLSearchParams(search.toString());
    if (next) params.set(VIEW_PARAM, VIEW_SHARE);
    else params.delete(VIEW_PARAM);
    const query = params.toString();
    router.replace(`${pathname}${query ? `?${query}` : ""}${window.location.hash}`, { scroll: false });
  };

  // Only an answer of the view asked for is shown: never „Für euch“'s figures under „Zum Teilen“.
  const data = state.data?.view === (share ? "share" : "own") ? state.data : undefined;
  // The path only under „Für euch“, gated on the view asked for, never on the answer loaded.
  const goal = adopted && adopted.of === goalState.data ? adopted.goal : (goalState.data ?? null);
  const path: PathData | null = !share && data?.view === "own" ? { goal, tracking: trackingState.data ?? null } : null;
  const reloadAll = () => {
    state.reload();
    goalState.reload();
    trackingState.reload();
  };
  // After an adopt: the goal from its answer, then the plan and the tracking read again.
  const onAdopted = (next: HouseholdGoal) => {
    setAdopted({ goal: next, of: goalState.data });
    state.reload();
    trackingState.reload();
  };
  const panel =
    data && detail.id ? (
      <Panel data={data} path={path} id={detail.id} share={share} code={code} onClose={detail.close} onWritten={reloadAll} />
    ) : null;

  return (
    <section className="planp">
      <div className="planp-head">
        <Link href={GOAL_PAGE} className="planp-back">
          ‹ {t.back}
        </Link>
        <h1>{t.title}</h1>
      </div>
      <div className="planp-views" role="group" aria-label={t.views}>
        <button type="button" aria-pressed={!share} onClick={() => setView(false)}>
          {t.viewOwn}
        </button>
        <button type="button" aria-pressed={share} onClick={() => setView(true)}>
          {t.viewShare}
        </button>
      </div>
      {share ? (
        <a {...detail.linkProps(P.share)} className="planp-sharebar">
          <span>{t.shareSummary(decode(code).round_amounts, decode(code).accounts)}</span>
          <span className="planp-sharebar-go">{t.shared} ›</span>
        </a>
      ) : null}
      <DetailLayout panel={panel}>
        <Loading state={{ ...state, data }}>{(plan) => <Cards plan={plan} path={path} onAdopted={onAdopted} />}</Loading>
      </DetailLayout>
    </section>
  );
}

function rounded(plan: PlanPage): boolean {
  return plan.view === "share" && (plan.switches?.round_amounts ?? true);
}

/** A rounded amount reads „rund …“ in the shared view (19i frame 4). */
function amount(t: PT, p: PlanFormat, plan: PlanPage, value: string | null | undefined): string {
  const text = p.euro(value);
  return rounded(plan) && value != null ? t.rounded(text) : text;
}

/** A small figure's amount: „rund“ may wrap onto its own line on a phone, the amount never. */
function StatAmount({ plan, value }: { plan: PlanPage; value: string | null | undefined }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const text = <span className="planp-nowrap">{p.euro(value)}</span>;
  if (!rounded(plan) || value == null) return text;
  const [before, after] = t.rounded("|").split("|");
  return (
    <>
      {before}
      {text}
      {after}
    </>
  );
}

function Cards({ plan, path, onAdopted }: { plan: PlanPage; path: PathData | null; onAdopted: (goal: HouseholdGoal) => void }) {
  const t = useT(planPageText);
  const goal = path?.goal ?? null;
  const withPath = plan.view === "own" && pathView(goal) != null;
  if (!plan.goal) {
    return (
      <div className="goal-cards">
        <div className="goal-c">
          <div className="goal-ch">
            <h3>{plan.word === "none" ? t.noGoal : t.goalCard}</h3>
          </div>
          {plan.word === "none" ? <p className="goal-one">{t.noGoalText}</p> : null}
          <Link href={GOAL_FORM_HREF} className="goal-lnk">
            {t.setGoal} ›
          </Link>
        </div>
      </div>
    );
  }
  return (
    <div className="goal-cards planp-cards">
      <GoalCard plan={plan} goal={withPath ? goal : null} />
      {plan.issue != null || plan.heute == null ? <IssueCard plan={plan} /> : null}
      {plan.heute ? <HeuteCard plan={plan} /> : null}
      {/* With the goal's path (§7.11 (i)), its cards replace the route and „Diesen Monat“. */}
      {withPath && goal ? <PathCards goal={goal} tracking={path?.tracking ?? null} onAdopted={onAdopted} /> : plan.plan ? <RouteCard plan={plan} /> : null}
      {plan.assumptions ? <AssumptionsCard plan={plan} /> : null}
      {plan.risks ? <RisksCard plan={plan} /> : null}
      {plan.other_routes ? <OtherCard plan={plan} /> : null}
      {!withPath && plan.this_month ? <MonthCard plan={plan} /> : null}
    </div>
  );
}

function wordText(g: ReturnType<typeof useT<typeof goalText.de>>, word: GoalWord | null | undefined, issue: string | null | undefined): string {
  switch (word) {
    case "on_track":
      return g.onTrack;
    case "feasible":
      return g.feasible;
    case "ambitious":
      return g.ambitious;
    case "hardly":
      return g.hardly;
    case "none":
      return g.noGoal;
    default:
      return issue ? g.issueWord(issue) : "–";
  }
}

function targetText(t: PT, p: PlanFormat, plan: PlanPage): string {
  const goal = plan.goal!;
  const month = p.month(goal.due_month);
  if (goal.kind === "reserve_months") return t.targetReserve(p.short(goal.target), month);
  return t.targetMoney(p.euro(goal.target), month);
}

/** The bar's width: the server's percent, at most full. A display width, never a figure. */
function barWidth(percent: string): string {
  const value = Number(percent);
  return `${Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
}

function GoalCard({ plan, goal: household }: { plan: PlanPage; goal: HouseholdGoal | null }) {
  const t = useT(planPageText);
  const g = useT(goalText);
  const p = usePlanFormat();
  const goal = plan.goal!;
  // With the goal's path, its word and the return it needs as its own labelled figure, as
  // Übersicht and Ziel read it (§7.13 (d) 1, D316).
  const pathWord = usePathWord(household);
  return (
    <div className="goal-c goal-c-excess planp-goal">
      <CardHead kind={KIND} id={P.goal} title={targetText(t, p, plan)} />
      {pathWord ? (
        <>
          <WordPill word={pathWord.word} text={pathWord.text} />
          {pathWord.figure ? <span className="tile-sub">{pathWord.figure}</span> : null}
        </>
      ) : (
        <WordPill word={goal.word ?? "none"} text={wordText(g, goal.word, plan.issue)} />
      )}
      {goal.percent != null ? (
        <>
          <span className="goal-bar" role="img" aria-label={g.percentReached(p.percentValue(goal.percent))}>
            <span style={{ width: barWidth(goal.percent) }} />
          </span>
          <div className="planp-goal-foot">
            <span>{t.today(amount(t, p, plan, goal.value_today))}</span>
            <b>{g.percentReached(p.percentValue(goal.percent))}</b>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** While the goal has no word yet (`issue`), why and what is missing, „Für euch“ with its to-dos. */
function IssueCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const g = useT(goalText);
  const todos = plan.todos ?? [];
  return (
    <div className="goal-c">
      <div className="goal-ch">
        <h3>{plan.issue ? g.issueWord(plan.issue) : t.waiting}</h3>
      </div>
      {todos.length > 0 ? <HealthTodoList todos={todos} onChanged={() => undefined} /> : null}
      <Link href={GOAL_PAGE} className="goal-lnk">
        {t.back} ›
      </Link>
    </div>
  );
}

function HeuteCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const g = useT(goalText);
  const p = usePlanFormat();
  const heute = plan.heute!;
  return (
    <div className="goal-c">
      <CardHead kind={KIND} id={P.heute} title={t.heute} />
      <Stats count={3}>
        <Stat value={<StatAmount plan={plan} value={heute.net_worth} />} label={t.netWorth} />
        {/* The reserve always as „X € von Y €“ with „erreicht“ or „noch Z € bis zum Ziel“
            (§7.13 (d) 2, D316); its months are in the panel. */}
        <Stat
          value={
            heute.reserve_target != null ? (
              <>
                <StatAmount plan={plan} value={heute.reserve} /> {g.reserveOfWord} <StatAmount plan={plan} value={heute.reserve_target} />
              </>
            ) : (
              <StatAmount plan={plan} value={heute.reserve} />
            )
          }
          label={
            heute.reserve_gap == null
              ? t.reserveNone
              : heute.reserve_full === true
                ? g.reserveReached
                : g.reserveStill(amount(t, p, plan, heute.reserve_gap))
          }
        />
        <Stat value={<StatAmount plan={plan} value={heute.saved_mean} />} label={t.savedMean} />
      </Stats>
    </div>
  );
}

function isZero(value: string | null | undefined): boolean {
  return value == null || /^-?0+(\.0+)?$/.test(value);
}

/** A server amount above 0 (its sign read from the text; nothing computed). */
function positive(value: string | null | undefined): value is string {
  return value != null && !value.startsWith("-") && !isZero(value);
}

function RouteCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const route = plan.plan!;
  return (
    <div className="goal-c goal-c-main">
      <CardHead kind={KIND} id={P.route} title={route.on_track ? t.onTrackCard : t.planCard} />
      <div className="goal-rows">
        {!route.on_track && !isZero(route.saving_extra) ? (
          <div className="goal-rw">
            <span>{t.moreInvest}</span>
            <b>{amount(t, p, plan, route.saving_extra)}</b>
          </div>
        ) : null}
        {!route.on_track && !isZero(route.cut) ? (
          <div className="goal-rw">
            <span>{t.lessWants}</span>
            <b>{amount(t, p, plan, route.cut)}</b>
          </div>
        ) : null}
        {route.on_track ? <OnTrackRows plan={plan} /> : null}
        <div className="goal-rw">
          <span>{t.atGoal}</span>
          <b>{route.reached ? p.month(route.reached) : t.notReached}</b>
        </div>
      </div>
    </div>
  );
}

/** On track, what keeps the household there (Luis, 26 Nov 2026: „it doesn't say what the plan
 * is“): the mean it saves, the money above the reserve target to invest now and the rates it
 * typed. Each the server's figure; the rates only while the shared view keeps the assumptions. */
function OnTrackRows({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const heute = plan.heute;
  const rates = plan.assumptions?.rates;
  const rateParts: string[] = [];
  if (rates?.depot != null) rateParts.push(`${t.funds} ${p.ratio(rates.depot)}`);
  if (rates?.crypto != null) rateParts.push(`${t.crypto} ${p.ratio(rates.crypto)}`);
  return (
    <>
      {heute && positive(heute.saved_mean) ? (
        <div className="goal-rw">
          <span>{t.keepSaving}</span>
          <b>{t.perMonth(amount(t, p, plan, heute.saved_mean))}</b>
        </div>
      ) : null}
      {heute && positive(heute.above_target) ? (
        <div className="goal-rw">
          <span>{t.investNow}</span>
          <b>{amount(t, p, plan, heute.above_target)}</b>
        </div>
      ) : null}
      {rateParts.length > 0 ? (
        <div className="goal-rw">
          <span>{t.atYourRates}</span>
          <b>{rateParts.join(" · ")}</b>
        </div>
      ) : null}
    </>
  );
}

function AssumptionsCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const a = plan.assumptions!;
  const chips: { key: string; label: string; value: string; never?: boolean }[] = [];
  if (a.rates.depot != null) chips.push({ key: "depot", label: t.funds, value: p.ratio(a.rates.depot) });
  if (a.rates.crypto != null) chips.push({ key: "crypto", label: t.crypto, value: p.ratio(a.rates.crypto) });
  if (a.rates.reserve != null) chips.push({ key: "reserve", label: t.reserve, value: p.ratio(a.rates.reserve) });
  for (const property of a.properties ?? [])
    if (property.value_change != null)
      chips.push({ key: property.key, label: t.propertyKind(property.kind), value: p.ratio(property.value_change), never: property.house_prices?.never });
  return (
    <div className="goal-c">
      <CardHead kind={KIND} id={P.assumptions} title={t.assumptionsCard} />
      {chips.length > 0 ? (
        <ul className="planp-chips">
          {chips.map((chip) => (
            <li key={chip.key}>
              {chip.label} <b>{chip.value}</b>
              {chip.never ? (
                <span className="planp-mark" data-testid="house-prices-never">
                  {" · "}
                  {t.neverReached}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="goal-one">{t.noRates}</p>
      )}
    </div>
  );
}

function delayText(t: PT, p: PlanFormat, risk: { delay_months?: number | null; not_until?: string | null }): string {
  if (risk.not_until) return t.notUntil(p.month(risk.not_until));
  if (risk.delay_months != null) return t.delay(risk.delay_months);
  return t.unavailable;
}

function RisksCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const risks = plan.risks!;
  const weak = risks.weak_markets;
  const weakRate = weak.rates?.depot ?? weak.rates?.crypto ?? null;
  return (
    <div className="goal-c">
      <CardHead kind={KIND} id={P.risks} title={t.risksCard} />
      <div className="goal-rows">
        {weak.available ? (
          <div className="goal-rw">
            <span>{weakRate != null ? t.weakMarkets(p.ratio(weakRate)) : t.weakMarketsPlain}</span>
            <b>{delayText(t, p, weak)}</b>
          </div>
        ) : null}
        {risks.income_gap.available ? (
          <div className="goal-rw">
            <span>{t.incomeGap(risks.income_gap.months)}</span>
            <b>{delayText(t, p, risks.income_gap)}</b>
          </div>
        ) : null}
        <div className="goal-rw">
          <span>{risks.repair.available ? t.repair(amount(t, p, plan, risks.repair.amount)) : t.repairPlain}</span>
          <b>{risks.repair.available ? delayText(t, p, risks.repair) : t.unavailable}</b>
        </div>
      </div>
    </div>
  );
}

function OtherCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const tp = useT(goalPathText);
  const p = usePlanFormat();
  const o = plan.other_routes!;
  return (
    <div className="goal-c">
      <CardHead kind={KIND} id={P.other} title={t.otherCard} />
      <div className="goal-rows">
        <div className="goal-rw">
          <span>{t.onlyMoreSaving}</span>
          <b>{o.more_saving != null ? t.plusPerMonth(amount(t, p, plan, o.more_saving)) : t.unavailable}</b>
        </div>
        <div className="goal-rw">
          <span>{t.onlyMoreReturn}</span>
          <b>{o.needed_return != null ? tp.perYear(p.ratio(o.needed_return)) : t.unavailable}</b>
        </div>
        <div className="goal-rw">
          <span>{t.onlyLessSpending}</span>
          <b>{o.less_spending != null ? t.minusPerMonth(amount(t, p, plan, o.less_spending)) : t.unavailable}</b>
        </div>
      </div>
    </div>
  );
}

function stepText(t: PT, p: PlanFormat, plan: PlanPage, step: PlanStep): string {
  const value = amount(t, p, plan, step.amount);
  return step.kind === "invest" ? t.investStep(value) : t.wantsStep(value);
}

function MonthCard({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const detail = useDetail(KIND);
  const month = plan.this_month!;
  const steps = month.steps ?? [];
  return (
    <div className="goal-c">
      <CardHead kind={KIND} id={P.month} title={t.monthCard} tag={<span className="goal-tag">{t.doneOf(month.done, month.of)}</span>} />
      {steps.length === 0 ? (
        <p className="goal-one">{t.noSteps}</p>
      ) : (
        <ul className="planp-steps">
          {steps.map((step) => (
            <li key={step.kind}>
              <span className="planp-tick" data-done={step.done ? "" : undefined} role="img" aria-label={step.done ? t.done : t.open} />
              <span className="planp-step-text">{stepText(t, p, plan, step)}</span>
              {step.kind === "invest" ? (
                <a {...detail.linkProps(P.route)} className="goal-lnk">
                  {t.how} ›
                </a>
              ) : !step.done && plan.view === "own" ? (
                <a {...detail.linkProps(P.month)} className="goal-lnk">
                  {t.adopt}
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- the side panels -------------------------------------------------------------------------

function Panel({
  data,
  path,
  id,
  share,
  code,
  onClose,
  onWritten,
}: {
  data: PlanPage;
  path: PathData | null;
  id: string;
  share: boolean;
  code: string;
  onClose: () => void;
  onWritten: () => void;
}) {
  const t = useT(planPageText);
  const titles: Record<string, string> = {
    [P.goal]: t.goalCard,
    [P.heute]: t.heute,
    [P.route]: data.plan?.on_track ? t.onTrackCard : t.planCard,
    [P.assumptions]: t.assumptionsCard,
    [P.risks]: t.risksCard,
    [P.other]: t.otherCard,
    [P.month]: t.monthCard,
    [P.share]: t.shared,
  };
  const goal = data.view === "own" && pathView(path?.goal) != null ? (path?.goal ?? null) : null;
  const pathTitle = usePathPanelTitle(goal, id);
  if (goal && pathTitle) {
    return (
      <DetailPanel title={pathTitle} backLabel={t.title} onClose={onClose}>
        <div className="stack planp-panel">
          <PathPanelBody goal={goal} id={id} extra={id === P.route ? <SplitOnPlanPage onWritten={onWritten} /> : undefined} />
        </div>
      </DetailPanel>
    );
  }
  const title = titles[id];
  if (!title) return null;
  let body: ReactNode = null;
  if (id === P.share) body = share ? <SharePanel code={code} /> : null;
  else if (id === P.goal) body = <GoalPanel plan={data} household={data.view === "own" ? (path?.goal ?? null) : null} />;
  else if (id === P.heute && data.heute) body = <HeutePanel plan={data} />;
  else if (id === P.route && data.plan) body = <RoutePanel plan={data} onWritten={onWritten} />;
  else if (id === P.assumptions && data.assumptions) body = <AssumptionsPanel plan={data} />;
  else if (id === P.risks && data.risks) body = <RisksPanel plan={data} />;
  else if (id === P.other && data.other_routes) body = <OtherPanel plan={data} />;
  else if (id === P.month && data.this_month) body = <MonthPanel plan={data} onWritten={onWritten} onDone={onClose} />;
  if (body == null) return null;
  return (
    <DetailPanel title={title} backLabel={t.title} onClose={onClose}>
      <div className="stack planp-panel">{body}</div>
    </DetailPanel>
  );
}

function SharePanel({ code }: { code: string }) {
  const t = useT(planPageText);
  const ids = useId();
  const switches = decode(code);
  const labels: Record<SwitchName, { label: string; sub?: string }> = {
    round_amounts: { label: t.switchRound, sub: t.switchRoundSub },
    accounts: { label: t.switchAccounts },
    wants: { label: t.switchWants },
    risks: { label: t.switchRisks },
    assumptions: { label: t.switchAssumptions },
  };
  return (
    <>
      <p className="goal-one">{t.sharedIntro}</p>
      <ul className="planp-switches">
        {SWITCH_NAMES.map((name) => (
          <li key={name}>
            <label htmlFor={`${ids}-${name}`}>
              <span>
                {labels[name].label}
                {labels[name].sub ? <span className="planp-switch-sub">{labels[name].sub}</span> : null}
              </span>
              <input
                id={`${ids}-${name}`}
                type="checkbox"
                role="switch"
                checked={switches[name]}
                onChange={(event) => PLAN_SHARE_SETTING.set(encode({ ...switches, [name]: event.target.checked }))}
              />
            </label>
          </li>
        ))}
      </ul>
      <p className="planp-never">{t.neverShared}</p>
      <p className="goal-one">{t.keptHere}</p>
    </>
  );
}

function GoalPanel({ plan, household }: { plan: PlanPage; household: HouseholdGoal | null }) {
  const t = useT(planPageText);
  const tp = useT(goalPathText);
  const p = usePlanFormat();
  const goal = plan.goal!;
  // The sentence the card leaves out, with how often the household's own mix reached it.
  const pathWord = usePathWord(household);
  const view = pathWord?.view ?? null;
  const words: Record<string, string> = { on_track: t.wordOnTrack, feasible: t.wordFeasible, ambitious: t.wordAmbitious, hardly: t.wordHardly };
  return (
    <>
      {goal.value_today != null && goal.percent != null ? (
        <p>{t.goalPanelText(targetText(t, p, plan), amount(t, p, plan, goal.value_today), p.percentValue(goal.percent))}</p>
      ) : null}
      <p className="goal-one">{t.goalPanelWord}</p>
      {pathWord?.sentence ? <p>{pathWord.sentence}</p> : null}
      {pathWord?.sentence && view?.windows != null && view.reached_windows != null && view.path.window_years != null ? (
        <p>{tp.windowsLine(view.path.window_years, view.reached_windows, view.windows)}</p>
      ) : null}
      {goal.word && words[goal.word] ? <p>{words[goal.word]}</p> : null}
      {plan.view === "own" ? (
        <Link href={GOAL_FORM_HREF} className="goal-lnk">
          {t.changeGoal} ›
        </Link>
      ) : null}
    </>
  );
}

function HeutePanel({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const heute = plan.heute!;
  return (
    <>
      <p>{t.heuteReserve(amount(t, p, plan, heute.reserve))}</p>
      {heute.reserve_months != null ? <p>{t.reserveMonths(p.short(heute.reserve_months), heute.reserve_full === true)}</p> : null}
      {heute.reserve_full != null ? <p>{heute.reserve_full ? t.heuteReserveFull : t.heuteReserveShort}</p> : null}
      {heute.reserve_accounts && heute.reserve_accounts.length > 0 ? (
        <>
          <h3 className="goal-kicker">{t.reserveAccounts}</h3>
          <ul className="planp-list">
            {heute.reserve_accounts.map((account) => (
              <li key={account}>{account}</li>
            ))}
          </ul>
        </>
      ) : null}
      {heute.window_from && heute.window_to ? <p className="goal-one">{t.heuteWindow(p.month(heute.window_from), p.month(heute.window_to))}</p> : null}
      <Link href={HOUSEHOLD_RESERVE} className="goal-lnk">
        {t.toReserve} ›
      </Link>
    </>
  );
}

function RoutePanel({ plan, onWritten }: { plan: PlanPage; onWritten: () => void }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const route = plan.plan!;
  const byClass = route.by_class ?? {};
  return (
    <>
      {route.on_track ? (
        <p>
          {route.reached
            ? t.planPanelOnTrack(
                p.month(route.reached),
                plan.heute && positive(plan.heute.saved_mean) ? amount(t, p, plan, plan.heute.saved_mean) : null,
                plan.heute && positive(plan.heute.above_target) ? amount(t, p, plan, plan.heute.above_target) : null,
              )
            : null}
        </p>
      ) : (
        <>
          {route.without_route != null && route.gap != null ? (
            <p>{t.planPanelText(amount(t, p, plan, route.without_route), amount(t, p, plan, route.gap))}</p>
          ) : null}
          {route.wants ? (
            route.wants.length > 0 ? (
              <>
                <h3 className="goal-kicker">{t.wantsDown}</h3>
                <div className="goal-rows">
                  {route.wants.map((want) => (
                    <div key={want.code} className="goal-rw goal-rw-sub">
                      <span>{ALLOCATION_CATEGORY[want.code] ?? want.code}</span>
                      <b>
                        {p.euro(want.from)} → {p.euro(want.to)}
                      </b>
                    </div>
                  ))}
                </div>
              </>
            ) : null
          ) : (
            <p className="goal-one">{t.wantsHidden}</p>
          )}
          {byClass.depot != null || byClass.crypto != null ? (
            <div className="goal-rows">
              {byClass.depot != null ? (
                <div className="goal-rw">
                  <span>{t.inDepot}</span>
                  <b>+{amount(t, p, plan, byClass.depot)}</b>
                </div>
              ) : null}
              {byClass.crypto != null ? (
                <div className="goal-rw">
                  <span>{t.inCrypto}</span>
                  <b>+{amount(t, p, plan, byClass.crypto)}</b>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      )}
      {/* §7.11 (i) item 10: „Aufteilung ändern“, „Für euch“ only; the shared view writes nothing. */}
      {plan.view === "own" ? <SplitOnPlanPage onWritten={onWritten} /> : null}
      <Link href={GOAL_PAGE} className="goal-lnk">
        {t.sources} ›
      </Link>
    </>
  );
}

function AssumptionsPanel({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const a = plan.assumptions!;
  return (
    <>
      <p className="goal-one">{t.assumptionsText}</p>
      <div className="goal-rows">
        {a.rates.depot != null ? (
          <div className="goal-rw">
            <span>{t.funds}</span>
            <b>{p.ratio(a.rates.depot)}</b>
          </div>
        ) : null}
        {a.rates.crypto != null ? (
          <div className="goal-rw">
            <span>{t.crypto}</span>
            <b>{p.ratio(a.rates.crypto)}</b>
          </div>
        ) : null}
        {a.rates.reserve != null ? (
          <div className="goal-rw">
            <span>{t.reserve}</span>
            <b>{p.ratio(a.rates.reserve)}</b>
          </div>
        ) : null}
        {(a.properties ?? []).map((property) => (
          <Fragment key={property.key}>
            <div className="goal-rw">
              <span>{property.label ?? t.propertyKind(property.kind)}</span>
              <b>
                {property.value_change != null ? `${p.ratio(property.value_change)} · ` : ""}
                {property.kept || property.sale_year == null ? t.keptProperty : t.soldIn(property.sale_year)}
              </b>
            </div>
            {property.house_prices ? (
              <div className="goal-rw" data-testid="house-prices-row">
                <span>{property.house_prices.never ? t.neverReached : t.housePrices}</span>
                <b>{t.housePricesReached(property.house_prices.reached, property.house_prices.windows, property.house_prices.window_years)}</b>
              </div>
            ) : null}
          </Fragment>
        ))}
      </div>
      {(a.properties ?? []).some((property) => property.house_prices) ? <p className="goal-one">{t.housePricesText}</p> : null}
      {a.house_price_source ? (
        <ul className="planp-list planp-sources">
          <li>
            <a href={a.house_price_source.url} target="_blank" rel="noreferrer">
              {a.house_price_source.read_on ? t.source(a.house_price_source.name, p.date(a.house_price_source.read_on)) : a.house_price_source.name}
            </a>
          </li>
        </ul>
      ) : null}
      {plan.view === "own" ? (
        <Link href={assumptionsHref("", "rendite")} className="goal-lnk">
          {t.toRates} ›
        </Link>
      ) : null}
    </>
  );
}

function RisksPanel({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const risks = plan.risks!;
  const weak = risks.weak_markets;
  const kinds: Record<string, string> = { depot: t.funds, crypto: t.crypto };
  const leftOut = (weak.left_out ?? []).map((item) => item.symbol ?? (item.kind === "funds" ? t.funds : t.crypto));
  return (
    <>
      <h3 className="goal-kicker">{t.weakMarketsPlain}</h3>
      {weak.available ? (
        <>
          {weak.window_years != null ? <p>{t.weakText(weak.window_years)}</p> : null}
          <ul className="planp-list">
            {Object.entries(weak.worst ?? {}).map(([kind, worst]) => (
              <li key={kind}>{t.weakWorst(kinds[kind] ?? kind, p.ratio(worst), p.ratio(weak.rates?.[kind as "depot" | "crypto"]))}</li>
            ))}
          </ul>
          {(weak.capped ?? []).length > 0 ? <p className="goal-one">{t.weakCapped}</p> : null}
          {leftOut.length > 0 ? <p className="goal-one">{t.weakLeftOut(leftOut.join(", "))}</p> : null}
        </>
      ) : (
        <p>{weak.reason ? t.weakReason(weak.reason) : t.unavailable}</p>
      )}
      <h3 className="goal-kicker">{t.incomeGap(risks.income_gap.months)}</h3>
      <p>{t.incomeText(risks.income_gap.months)}</p>
      {!isZero(risks.income_gap.drawn) ? <p>{t.incomeDrawn(amount(t, p, plan, risks.income_gap.drawn))}</p> : null}
      {risks.income_gap.used_up ? <p>{t.usedUp(p.month(risks.income_gap.used_up))}</p> : null}
      <h3 className="goal-kicker">{t.repairPlain}</h3>
      {risks.repair.available && risks.repair.per_m2 != null && risks.repair.years != null ? (
        <>
          <p>{t.repairText(p.money(risks.repair.per_m2), risks.repair.years)}</p>
          <p className="goal-one">{t.repairYears(risks.repair.years)}</p>
          {risks.repair.used_up ? <p>{t.usedUp(p.month(risks.repair.used_up))}</p> : null}
        </>
      ) : (
        <p>{risks.repair.reason ? t.repairReason(risks.repair.reason) : t.unavailable}</p>
      )}
      {(risks.sources ?? []).length > 0 ? (
        <ul className="planp-list planp-sources">
          {(risks.sources ?? []).map((source) => (
            <li key={source.url}>
              <a href={source.url} target="_blank" rel="noreferrer">
                {t.source(source.name, p.date(source.read_on))}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      {risks.todos && risks.todos.length > 0 ? (
        <>
          <h3 className="goal-kicker">{t.risksToDo}</h3>
          <HealthTodoList todos={risks.todos} onChanged={() => undefined} />
        </>
      ) : null}
    </>
  );
}

function OtherPanel({ plan }: { plan: PlanPage }) {
  const t = useT(planPageText);
  const g = useT(goalText);
  const o = plan.other_routes!;
  return (
    <>
      <p>{t.otherText}</p>
      {o.return_windows != null && o.return_reached_windows != null ? (
        <p>
          {t.returnHistory(o.return_reached_windows, o.return_windows)}
          {o.return_verdict ? <span className="goal-tag planp-verdict">{g.verdictTag(o.return_verdict)}</span> : null}
        </p>
      ) : null}
      <Link href={GOAL_PAGE} className="goal-lnk">
        {t.sources} ›
      </Link>
    </>
  );
}

/** „Übernehmen“: the balanced route's spending changes as the default scenario's budget, read
 * from GET /household-goal at the moment of the write (the plan's answer carries no changes).
 * Without a balanced route with changes at that moment nothing is written, so the household's
 * saved spending changes are never replaced by none. */
async function adoptBalanced(noRoute: string): Promise<void> {
  const goal = await loadGoal();
  const changes = goal.balanced?.spending_changes ?? [];
  if (changes.length === 0) throw new Error(noRoute);
  const current = currentDefault(await loadScenarioHistory());
  const levers = fillLevers(current?.levers);
  await saveDefaultScenario(
    {
      ...levers,
      spending_changes: changes,
      goal_choice: { ...(levers.goal_choice ?? {}), spending: "own", spending_chosen_on: todayIso() },
    },
    current,
  );
}

function MonthPanel({ plan, onWritten, onDone }: { plan: PlanPage; onWritten: () => void; onDone: () => void }) {
  const t = useT(planPageText);
  const p = usePlanFormat();
  const ids = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const steps = plan.this_month?.steps ?? [];
  const wants = steps.find((step) => step.kind === "wants_budget");
  const canAdopt = plan.view === "own" && wants != null && !wants.done;
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await adoptBalanced(t.adoptNoRoute);
      onWritten();
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <p className="goal-one">{t.monthText}</p>
      <div className="goal-rows">
        {steps.map((step) => (
          <div key={step.kind} className="goal-rw">
            <span>{stepText(t, p, plan, step)}</span>
            <b>{step.done ? t.done : t.open}</b>
          </div>
        ))}
      </div>
      {steps.some((step) => step.kind === "invest") ? <p>{t.investText}</p> : null}
      {wants ? <p>{t.wantsText}</p> : null}
      {canAdopt ? (
        <div className="notice notice-info goal-confirm" role="group" aria-labelledby={`${ids}-confirm`}>
          <p>
            <strong id={`${ids}-confirm`}>{t.adoptConfirm}</strong>
          </p>
          <p>{t.adoptConfirmText}</p>
          <div className="row">
            <button type="button" disabled={busy} onClick={confirm}>
              {t.adoptYes}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={onDone}>
              {t.cancel}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorNotice error={error} />
    </>
  );
}
