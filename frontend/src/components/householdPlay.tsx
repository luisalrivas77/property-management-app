"use client";

// Haushalt › Durchspielen (phase 10 §7.10 (a), D299; docs/mockups/19e frames 1–3, Q-O184): the
// what-if as its own tab. „Heute“ is what the saved plan serves (the net income's mean, each
// class's and code's mean); „Angenommen“ writes the existing levers: the income row one income
// change (its difference to the mean from its month), a code's field a spending change, the
// one-offs their own list. „Was das bewirkt“ sets the saved plan beside POST /household-plan/
// preview and POST /household-goal/preview for the draft, asked 600 ms after the last keystroke;
// nothing else on the page moves until „Als Plan behalten“ stores a new version of the default
// scenario (D248, append-only). The draft is held as changes on the saved levers (playDraft.ts),
// in the provider, so it survives a switch to another Haushalt tab. Every figure is the server's;
// this file formats them and builds the levers from what a member typed.
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { lineLabel, planning } from "@/i18n/planning";
import { playText } from "@/i18n/play";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { HOUSEHOLD_BUDGET, HOUSEHOLD_GOAL, HOUSEHOLD_RESERVE, PROJECTION_ANCHOR, REST_ANCHOR } from "@/lib/planningRoutes";

import { todayIso } from "./format";
import { fillLevers, type HouseholdLevers, type HouseholdPlan } from "./healthShared";
import { goalTargetText, type HouseholdGoal, loadGoal } from "./householdGoal";
import { OneOffs, type PlanReady, surplusRow, TooFewMonths, usePlan } from "./householdPlan";
import { decimalInput, toMoneyString } from "./input";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import {
  applyPlay,
  fromLevers,
  fromMonthForAll,
  type IncomeRow,
  moneyDifference,
  monthAfter,
  type PlayChanges,
  playCount,
  withIncome,
} from "./playDraft";
import { type PlanPreview, SpendingPlaySection } from "./spendingPlay";
import { Loading } from "./ui";

import "./householdPlay.css";

type AllocationCategory = components["schemas"]["AllocationCategory"];
type BudgetLine = components["schemas"]["BudgetLine"];

const DEBOUNCE_MS = 600;

/** Haushalt › Durchspielen inside a `PlanProvider`. */
export function HouseholdPlayTab() {
  const { state, ready } = usePlan();
  return (
    <Loading state={state}>
      {(loaded) => (ready ? <Play ready={ready} /> : <TooFewMonths data={loaded} onChanged={state.reload} />)}
    </Loading>
  );
}

// --- the preview -----------------------------------------------------------------------------

interface PlayPreview {
  plan: HouseholdPlan;
  goal: HouseholdGoal | null;
  pending: boolean;
  error: unknown;
}

/** Both previews for the draft's levers, asked together a moment after the last keystroke; an
 * answer that arrives after a newer request was sent is dropped, and the last good answer stays
 * on screen while the next one is computed („rechnet …“). Nothing is stored. */
function usePlayPreview(saved: HouseholdPlan, savedGoal: HouseholdGoal | null, levers: HouseholdLevers, idle: boolean): PlayPreview {
  const key = JSON.stringify(levers);
  const withGoal = levers.goal != null;
  const [result, setResult] = useState<{ key: string; plan: HouseholdPlan; goal: HouseholdGoal | null } | null>(null);
  const [error, setError] = useState<{ key: string; error: unknown } | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    if (idle) return;
    const body = { levers: JSON.parse(key) as HouseholdLevers, scenario_id: saved.scenario_id ?? null };
    const id = latest.current + 1;
    latest.current = id;
    const timer = window.setTimeout(() => {
      Promise.all([
        ok(api.POST("/api/household-plan/preview", { body })),
        withGoal ? ok(api.POST("/api/household-goal/preview", { body })) : Promise.resolve(null),
      ]).then(
        ([plan, goal]) => {
          if (id !== latest.current) return;
          setResult({ key, plan, goal });
          setError(null);
        },
        (caught: unknown) => {
          if (id === latest.current) setError({ key, error: caught });
        },
      );
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [key, idle, saved, withGoal]);

  if (idle) return { plan: saved, goal: savedGoal, pending: false, error: null };
  const failed = error?.key === key ? error.error : null;
  return {
    plan: result?.plan ?? saved,
    goal: result ? result.goal : savedGoal,
    pending: result?.key !== key && failed == null,
    error: failed,
  };
}

// --- the tab ---------------------------------------------------------------------------------

function Play({ ready }: { ready: PlanReady }) {
  const t = useT(playText);
  const p = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const { data, play } = ready;
  const plan = data.plan;
  const saved = fillLevers(plan.levers);
  const meanIncome = surplusRow(plan, "mean_income")?.amount ?? null;
  const levers = applyPlay(saved, play.changes, meanIncome);
  const count = playCount(saved, play.changes, meanIncome);
  const futureMonths = (plan.months ?? []).map((m) => m.month);
  const firstMonth = futureMonths[0] ?? (plan.newest_closed ? monthAfter(plan.newest_closed) : todayIso().slice(0, 7));
  const windowMonths = data.health.window.months ?? [];
  const [allFrom, setAllFrom] = useState(firstMonth);

  // The saved goal (GET /household-goal), read again after every save.
  const [savedGoal, setSavedGoal] = useState<HouseholdGoal | null>(null);
  useEffect(() => {
    let cancelled = false;
    loadGoal().then(
      (goal) => {
        if (!cancelled) setSavedGoal(goal);
      },
      () => {
        if (!cancelled) setSavedGoal(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [plan]);

  // The goal counts spending changes only with a spending choice (§7.9 (d)); the draft's own
  // spending is the household's own choice, as „Als Plan behalten“ stores it.
  const spendingMoved = Object.keys(play.changes.spending).length > 0;
  const choice = saved.goal_choice ?? null;
  const previewLevers: HouseholdLevers =
    spendingMoved && choice?.spending !== "own"
      ? { ...levers, goal_choice: { ...(choice ?? {}), spending: "own", spending_chosen_on: todayIso() } }
      : levers;
  const preview = usePlayPreview(plan, savedGoal, previewLevers, count === 0);

  /** The lever components (the spending fields, the one-offs) work on whole levers; their
   * change is kept as the draft's difference to the saved levers. */
  const setLevers = (update: (previous: HouseholdLevers) => HouseholdLevers) =>
    play.set((previous) => {
      const after = update(applyPlay(saved, previous, meanIncome));
      return fromLevers(saved, previous, { ...after, spending_changes: backToToday(after.spending_changes ?? [], plan) });
    });
  const spendingPreview: PlanPreview = { withChanges: preview.plan, error: preview.error, pending: preview.pending };

  const budget = data.budget;
  const lineOf = (key: string) => (budget.lines ?? []).find((line) => line.key === key) ?? null;
  const [invalid, setInvalid] = useState(false);

  return (
    <div className="stack planning play" key={`${ready.epoch}-${play.round}`}>
      <section className="card stack" aria-labelledby={`${ids}-title`}>
        <h2 id={`${ids}-title`}>{t.title}</h2>
        <p className="muted">{t.intro}</p>
        <div className="play-from">
          <label className="inline">
            {t.from}
            <select value={allFrom} onChange={(event) => setAllFrom(event.target.value)}>
              {futureMonths.map((m) => (
                <option key={m} value={m}>
                  {f.month(m)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="secondary"
            disabled={count === 0}
            onClick={() => {
              play.set((previous) => fromMonthForAll(previous, allFrom));
              play.bump();
            }}
          >
            {t.applyAll}
          </button>
          <span className="muted">{t.applyAllHint}</span>
        </div>

        <div className="play-grid" role="table" aria-label={t.title}>
          <div className="play-row play-head" role="row">
            <span role="columnheader">{t.colWhat}</span>
            <span role="columnheader" className="num">
              {t.colToday(f.monthRange(windowMonths))}
            </span>
            <span role="columnheader">{t.colAssumed}</span>
          </div>
          <IncomeRowField
            saved={saved}
            changes={play.changes}
            meanIncome={meanIncome}
            months={futureMonths}
            first={firstMonth}
            onChange={(update) => play.set(update)}
          />
          <p className="play-section" role="row">
            <span role="cell">{t.spending}</span>
          </p>
          {(["need", "want"] as const).map((key) => {
            const line = lineOf(key);
            return line ? (
              <ClassRow
                key={key}
                line={line}
                label={lineLabel(p, key)}
                plan={plan}
                windowMonths={windowMonths}
                preview={spendingPreview}
                levers={levers}
                setLevers={setLevers}
                changed={Object.keys(play.changes.spending).filter((code) => (line.codes ?? []).some((c) => c.category === code)).length}
                onValidity={setInvalid}
              />
            ) : null;
          })}
          <div className="play-row" role="row">
            <span role="rowheader">
              {t.left}
              <span className="muted play-sub">{t.leftSub}</span>
            </span>
            <span role="cell" className="num">
              {f.money(lineOf("saving")?.monthly)}
            </span>
            <span role="cell" className="muted">
              {t.asToday}
            </span>
          </div>
        </div>

        <div className="play-one-offs">
          <OneOffs data={data} draft={levers} setDraft={setLevers} />
        </div>
      </section>

      <Effects saved={plan} savedGoal={savedGoal} preview={preview} levers={levers} changes={play.changes} firstMonth={firstMonth} />

      <div className="card stack play-foot" aria-live="polite">
        <p>
          <strong>{count > 0 ? t.changes(count) : t.noChanges}</strong>
        </p>
        {play.error != null ? <ErrorNotice error={play.error} /> : null}
        <div className="row">
          <button type="button" disabled={count === 0 || invalid || play.busy} onClick={() => void play.keep()}>
            {play.busy ? t.saving : t.keep}
          </button>
          <button type="button" className="secondary" disabled={count === 0 || play.busy} onClick={play.reset}>
            {t.reset}
          </button>
        </div>
        <p className="muted">{t.keepNote}</p>
      </div>
    </div>
  );
}

/** A code typed back to its mean, in euros and with no end, is no change (§7.10 (a) 3). */
function backToToday(changes: components["schemas"]["SpendingChange"][], plan: HouseholdPlan) {
  const means = new Map<string, string>();
  for (const group of plan.spending_play?.groups ?? []) for (const row of group.rows) means.set(row.code, row.mean);
  return changes.filter((change) => {
    const mean = means.get(change.code);
    if (mean === undefined || change.monthly == null || change.to_month) return true;
    return moneyDifference(change.monthly, mean) !== "0.00";
  });
}

// --- the income row --------------------------------------------------------------------------

function IncomeRowField({
  saved,
  changes,
  meanIncome,
  months,
  first,
  onChange,
}: {
  saved: HouseholdLevers;
  changes: PlayChanges;
  meanIncome: string | null;
  months: string[];
  first: string;
  onChange: (update: (previous: PlayChanges) => PlayChanges) => void;
}) {
  const t = useT(playText);
  const p = useT(planning);
  const f = usePlanFormat();
  const ids = useId();
  const row = changes.incomeRow;
  const [text, setText] = useState(row ? decimalInput(row.monthly) : "");
  const [error, setError] = useState<string | null>(null);
  const kept = changes.income ?? saved.income_changes ?? [];

  const set = (patch: Partial<IncomeRow> & { text?: string }) => {
    const nextText = patch.text ?? text;
    setText(nextText);
    if (nextText.trim() === "") {
      setError(null);
      onChange((previous) => {
        const next = { ...previous };
        delete next.incomeRow;
        return next;
      });
      return;
    }
    const parsed = toMoneyString(nextText);
    if (!parsed.ok) {
      setError(parsed.message);
      return;
    }
    setError(null);
    onChange((previous) => {
      const base = previous.incomeRow ?? { monthly: parsed.value, from: first, to: null };
      const from = patch.from ?? base.from;
      const to = patch.to !== undefined ? patch.to : base.to;
      return { ...previous, incomeRow: { monthly: parsed.value, from, to: to && to < from ? null : to } };
    });
  };

  const label = t.netIncome;
  return (
    <>
      <p className="play-section" role="row">
        <span role="cell">{t.income}</span>
      </p>
      <div className="play-row" role="row">
        <span role="rowheader">{label}</span>
        <span role="cell" className="num">
          {f.money(meanIncome)}
        </span>
        <span role="cell" className="play-assumed">
          <span className="spending-play-input">
            <input
              id={`${ids}-income`}
              type="text"
              inputMode="decimal"
              aria-label={t.assumedLabel(label)}
              aria-invalid={error ? true : undefined}
              placeholder={t.asToday}
              value={text}
              onChange={(event) => set({ text: event.target.value })}
            />
            <span aria-hidden="true">€</span>
          </span>
          {row ? (
            <span className="play-months">
              <label className="inline">
                {t.rowFrom}
                <select aria-label={t.fromLabel(label)} value={row.from} onChange={(event) => set({ from: event.target.value })}>
                  {months.map((m) => (
                    <option key={m} value={m}>
                      {f.month(m)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline">
                {t.rowTo}
                <select aria-label={t.toLabel(label)} value={row.to ?? ""} onChange={(event) => set({ to: event.target.value || null })}>
                  <option value="">{t.rowToOpen}</option>
                  {months
                    .filter((m) => m >= row.from)
                    .map((m) => (
                      <option key={m} value={m}>
                        {f.month(m)}
                      </option>
                    ))}
                </select>
              </label>
            </span>
          ) : null}
          {error ? <span className="field-errors">{error}</span> : null}
        </span>
      </div>
      {kept.length > 0 ? (
        <div className="play-row play-saved-income" role="row">
          <span role="rowheader" className="muted">
            {t.savedIncome}
          </span>
          <span role="cell" />
          <ul role="cell" className="play-list">
            {kept.map((change, index) => (
              <li key={`${change.from_month}-${index}`}>
                <span>
                  {f.signedEuro(change.monthly_net)} · {p.incomeSpan(f.month(change.from_month), change.to_month ? f.month(change.to_month) : null)}
                </span>{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() => onChange((previous) => withIncome(saved, previous, kept.filter((_, i) => i !== index)))}
                >
                  {t.remove}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

// --- a spending class ------------------------------------------------------------------------

function ClassRow({
  line,
  label,
  plan,
  windowMonths,
  preview,
  levers,
  setLevers,
  changed,
  onValidity,
}: {
  line: BudgetLine;
  label: string;
  plan: HouseholdPlan;
  windowMonths: readonly string[];
  preview: PlanPreview;
  levers: HouseholdLevers;
  setLevers: (update: (previous: HouseholdLevers) => HouseholdLevers) => void;
  changed: number;
  onValidity: (invalid: boolean) => void;
}) {
  const t = useT(playText);
  const f = usePlanFormat();
  const ids = useId();
  const [open, setOpen] = useState(changed > 0);
  const codes = line.codes ?? [];
  const inClass = new Set<string>(codes.map((code) => code.category));
  const inPlay = new Set<string>(
    (plan.spending_play?.groups ?? []).flatMap((group) => group.rows.filter((row) => inClass.has(row.code)).map((row) => row.code)),
  );
  const readOnly = codes.filter((code) => !inPlay.has(code.category));
  return (
    <>
      <div className="play-row" role="row">
        <span role="rowheader">
          <button type="button" className="household-toggle" aria-expanded={open} aria-controls={`${ids}-codes`} onClick={() => setOpen((v) => !v)}>
            <span aria-hidden="true">{open ? "▾" : "▸"}</span> {label}
          </button>
        </span>
        <span role="cell" className="num">
          {f.money(line.monthly)}
        </span>
        <span role="cell" className={changed > 0 ? "play-changed" : "muted"}>
          {changed > 0 ? t.changedCodes(changed) : t.asToday}
        </span>
      </div>
      {open ? (
        <div id={`${ids}-codes`} className="play-codes" role="row">
          <div role="cell">
            <SpendingPlaySection
              saved={plan}
              windowMonths={windowMonths}
              preview={preview}
              draft={levers}
              setDraft={setLevers}
              onValidity={onValidity}
              only={inPlay}
            />
            {readOnly.length > 0 ? (
              <ul className="play-list">
                {readOnly.map((code) => (
                  <li key={code.category}>
                    <span>{ALLOCATION_CATEGORY[code.category as AllocationCategory] ?? code.category}</span>
                    <span className="num">{f.money(code.monthly)}</span>
                    <span className="muted">{t.notPlayable}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}

// --- „Was das bewirkt“ -----------------------------------------------------------------------

/** The month the effect rows read: the latest start among the draft's rows, else the first month
 * after the newest closed one. */
function effectMonth(changes: PlayChanges, first: string): string {
  const starts = [
    ...Object.values(changes.spending).flatMap((change) => (change ? [change.from_month] : [])),
    ...(changes.incomeRow ? [changes.incomeRow.from] : []),
  ];
  return starts.reduce((latest, month) => (month > latest ? month : latest), first);
}

function segmentAt(plan: HouseholdPlan, month: string) {
  const rates = plan.spending_play?.rates ?? [];
  return rates.find((segment) => segment.from_month <= month && month <= segment.to_month) ?? rates.at(-1) ?? null;
}

function reserveReached(plan: HouseholdPlan) {
  return (plan.paths ?? []).find((path) => path.key === "as_is")?.reached ?? null;
}

function lastTotal(plan: HouseholdPlan) {
  return plan.projection?.points?.at(-1)?.total ?? null;
}

function goalReached(goal: HouseholdGoal | null) {
  if (!goal?.goal) return null;
  return goal.choice?.spending ? (goal.reached_with_budget ?? null) : (goal.reached ?? null);
}

function Effects({
  saved,
  savedGoal,
  preview,
  levers,
  changes,
  firstMonth,
}: {
  saved: HouseholdPlan;
  savedGoal: HouseholdGoal | null;
  preview: PlayPreview;
  levers: HouseholdLevers;
  changes: PlayChanges;
  firstMonth: string;
}) {
  const t = useT(playText);
  const g = useT(goalText);
  const f = usePlanFormat();
  const ids = useId();
  const month = effectMonth(changes, firstMonth);
  const years = levers.net_worth_years ?? 10;
  const monthOr = (value: string | null) => (value ? f.month(value) : t.notReached);
  const rows: { key: string; label: string; href: string; today: string; with: string }[] = [
    {
      key: "investable",
      label: t.effectInvestable(f.month(month)),
      href: `${HOUSEHOLD_BUDGET}#${REST_ANCHOR}`,
      today: f.euro(segmentAt(saved, month)?.investable),
      with: f.euro(segmentAt(preview.plan, month)?.investable),
    },
    {
      key: "rate",
      label: t.effectRate(f.month(month)),
      href: HOUSEHOLD_BUDGET,
      today: f.ratio(segmentAt(saved, month)?.rate),
      with: f.ratio(segmentAt(preview.plan, month)?.rate),
    },
    {
      key: "reserve",
      label: t.effectReserve,
      href: HOUSEHOLD_RESERVE,
      today: monthOr(reserveReached(saved)),
      with: monthOr(reserveReached(preview.plan)),
    },
    {
      key: "net-worth",
      label: t.effectNetWorth(years),
      href: `/assets#${PROJECTION_ANCHOR}`,
      today: f.euro(lastTotal(saved)),
      with: f.euro(lastTotal(preview.plan)),
    },
  ];
  if (savedGoal?.goal) {
    rows.push({
      key: "goal",
      label: t.effectGoal(goalTargetText(g, f, savedGoal.goal)),
      href: HOUSEHOLD_GOAL,
      today: monthOr(goalReached(savedGoal)),
      with: monthOr(goalReached(preview.goal)),
    });
  }
  return (
    <section className="card stack play-effects" aria-labelledby={`${ids}-effects`} aria-busy={preview.pending}>
      <div className="planning-card-head">
        <h2 id={`${ids}-effects`}>{t.effectTitle}</h2>
        <span className="spacer" />
        <span className="muted" role="status">
          {preview.pending ? t.effectPending : t.effectLive}
        </span>
      </div>
      <div className="play-grid play-effect-grid" role="table" aria-label={t.effectTitle}>
        <div className="play-row play-head" role="row">
          <span role="columnheader" />
          <span role="columnheader" className="num">
            {t.effectToday}
          </span>
          <span role="columnheader" className="num">
            {t.effectWith}
          </span>
        </div>
        {rows.map((row) => (
          <EffectRow key={row.key} row={row} pending={preview.pending} f={f} />
        ))}
      </div>
      {preview.error != null ? <ErrorNotice error={preview.error} /> : null}
    </section>
  );
}

function EffectRow({ row, pending }: { row: { key: string; label: string; href: string; today: string; with: string }; pending: boolean; f: PlanFormat }) {
  const differs = row.today !== row.with;
  return (
    <div className="play-row" role="row" data-effect={row.key}>
      <span role="rowheader">
        <Link href={row.href}>
          {row.label} <span aria-hidden="true">›</span>
        </Link>
      </span>
      <span role="cell" className="num">
        {row.today}
      </span>
      <span role="cell" className={`num${differs ? " play-changed" : ""}${pending ? " play-pending" : ""}`}>
        {row.with}
      </span>
    </div>
  );
}
