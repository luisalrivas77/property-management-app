"use client";

// Planung › Budget (phase 10 D272–D277, mockup 17-C2): the household month as Notwendiges,
// Wünsche and Sparen (with one-off and not yet classified spending beside them), the housing
// share, each against a target the household set itself. Every figure is the server's
// (GET /household-budget); nothing is summed or divided here. Nothing is coloured before a
// target is saved; then green when met, amber when missed, never red (D274). The published
// splits are reference points with their sources: one tap fills only the empty fields of the
// goals form, never the housing target, and stores nothing (D275). A tapped line opens in the
// detail panel with its codes, their fixed payments (D300) and the need/want switch; a planned
// amount is typed on Haushalt › Durchspielen since §7.10 (a) (D299), which the panel links to.
import Link from "next/link";
import { type FormEvent, type ReactNode, useId, useState } from "react";

import type { components } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { monthBudget } from "@/i18n/monthBudget";
import { lineLabel, planning, type PlanningText } from "@/i18n/planning";
import { describeError, ErrorNotice } from "@/lib/ErrorNotice";
import { HOUSEHOLD_PLAY } from "@/lib/planningRoutes";

import { DetailPanel, useDetail } from "./DetailPanel";
import { HealthTodoItem } from "./healthFigures";
import { FixedPayments } from "./householdRecurring";
import {
  fillLevers,
  type HouseholdLevers,
  type HouseholdScenario,
  saveDefaultScenario,
} from "./healthShared";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";

export type BudgetView = components["schemas"]["BudgetView"];
type BudgetLine = components["schemas"]["BudgetLine"];
type BudgetCode = components["schemas"]["BudgetCode"];
type BudgetLineKey = components["schemas"]["BudgetLineKey"];
type BudgetMark = components["schemas"]["BudgetMark"];
type BudgetTargets = components["schemas"]["BudgetTargets"];
type BudgetReference = components["schemas"]["BudgetReference"];
type BudgetClass = components["schemas"]["BudgetClass"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type SetDraft = (update: (previous: HouseholdLevers) => HouseholdLevers) => void;
type T = PlanningText;
type MonthBudgetText = (typeof monthBudget)["de"];

const BAR_TOKEN: Record<BudgetLineKey, string> = {
  need: "var(--budget-need)",
  want: "var(--budget-want)",
  one_off: "var(--border-strong)",
  uncategorized: "var(--mark-empty)",
  saving: "var(--budget-save)",
};

function categoryName(code: string): string {
  return ALLOCATION_CATEGORY[code as AllocationCategory] ?? code;
}

function negative(value: string | null | undefined): boolean {
  return value != null && value.trim().startsWith("-");
}

function unsigned(value: string): string {
  return value.trim().replace(/^[-+]/, "");
}

function zero(value: string): boolean {
  return /^[-+]?0(\.0+)?$/.test(value.trim());
}

/** The mark's sentence: the server's in German, built from its figures in English. */
function markSentence(mark: BudgetMark, label: string, t: T, f: PlanFormat): string {
  if (f.locale === "de") return mark.text;
  const points = f.short(unsigned(mark.gap_points));
  const amount = f.euro(unsigned(mark.gap_monthly));
  if (/^[-+]?0(\.0+)?$/.test(mark.gap_points.trim())) return t.markMet(label);
  return negative(mark.gap_points) ? t.markBelow(label, points, amount) : t.markAbove(label, points, amount);
}

function targetText(line: { key?: BudgetLineKey; target?: string | null }, max: boolean, t: T, f: PlanFormat): string {
  if (line.target == null) return t.noTarget;
  return max ? t.targetMax(f.percentValue(line.target)) : t.targetMin(f.percentValue(line.target));
}

function Dot({ mark, t }: { mark: BudgetMark | null | undefined; t: T }) {
  const label = mark ? (mark.colour === "green" ? t.markGreen : t.markAmber) : t.markNone;
  return <span className={`budget-dot${mark ? ` budget-dot-${mark.colour}` : ""}`} role="img" aria-label={label} />;
}

/** A server error; in English with Planung's own text for the scenario codes, since
 * src/api/errors.ts is German only. */
export function PlanError({ error }: { error: unknown }) {
  const locale = useLocale();
  const t = useT(planning);
  return (
    <ErrorNotice
      error={error}
      text={(caught) => {
        if (locale === "de") return null;
        const code = describeError(caught).code;
        return code ? (t.errorText(code) || null) : null;
      }}
    />
  );
}

// --- the section ---------------------------------------------------------------------------

/** One card as mockup 19h frame 1 (§7.12 (a), D309): the income, the bar, Notwendiges, Wünsche
 * and Sparen each with „fest · schwankend“ (D300, both the server's) and its target, „Einmalig im
 * Zeitraum“ beside the partition (D308), and „Ziele ändern“ inside. The housing share is one line
 * in Notwendiges' panel; a mark's sentence is in its class's panel. */
export function BudgetSection({
  budget,
  savingPlans = null,
  onChanged,
}: {
  budget: BudgetView;
  /** The savings plans' monthly sum (the plan's `already_invested`), shown under Sparen. */
  savingPlans?: string | null;
  onChanged: () => void;
}) {
  const t = useT(planning);
  const m = useT(monthBudget);
  const f = usePlanFormat();
  const goals = useDetail("goals");
  const line = useDetail("budget");
  const lines = budget.lines ?? [];
  const income = budget.income;
  const months = budget.window.months ?? [];
  const todos = budget.todos ?? [];
  const targets = budget.targets ?? {};
  const anyTarget = Object.values(targets).some((value) => value != null);
  const barOk = lines.length > 0 && lines.every((l) => l.share != null && !negative(l.share));
  const plans = savingPlans != null && !zero(savingPlans) ? savingPlans : null;

  return (
    <div className="stack">
      {todos.length > 0 ? (
        <ul className="health-todos">
          {todos.map((todo, index) => (
            <HealthTodoItem key={`${todo.code}-${index}`} todo={todo} onChanged={onChanged} />
          ))}
        </ul>
      ) : null}
      {income ? (
        <section className="card stack" aria-labelledby="budget-month-title">
          <div className="budget-income">
            <p className="eyebrow" id="budget-month-title">
              {m.cardEyebrow}
            </p>
            <p className="budget-big">{m.income(f.euro(income.monthly))}</p>
            <p className="muted">
              {t.meanOver(f.monthRange(months))}
              {income.rental_monthly && !zero(income.rental_monthly) ? ` · ${t.rentalIncluded(f.euro(income.rental_monthly))}` : ""}
            </p>
          </div>
          {barOk ? (
            <span className="budget-bar" aria-hidden="true">
              {lines.map((l) => (
                <span key={l.key} style={{ flexGrow: Number(l.share), background: BAR_TOKEN[l.key] }} />
              ))}
            </span>
          ) : null}
          {budget.no_shares ? <p className="muted">{f.locale === "de" ? budget.no_shares : t.noShares}</p> : null}
          <ul className="budget-lines">
            {lines.map((l) => (
              <BudgetLineRow key={l.key} line={l} plans={plans} link={line.linkProps(l.key)} />
            ))}
          </ul>
          {budget.one_off ? (
            // „Einmalig im Zeitraum“ (D308): beside the partition, never in it or in the bar.
            <ul className="budget-lines" aria-label={lineLabel(t, "one_off")}>
              <BudgetLineRow line={budget.one_off} plans={null} link={line.linkProps(budget.one_off.key)} />
            </ul>
          ) : null}
          {anyTarget ? null : <p className="muted">{m.noTargets}</p>}
          <div className="budget-foot">
            <span className="muted">{m.footNote(f.monthRange(months))}</span>
            <span className="budget-foot-links">
              <a {...goals.linkProps(ONE_OFF_SHEET)}>{m.oneOffLink} ›</a>
              <a {...goals.linkProps("eigene")}>{anyTarget ? m.changeGoals : m.setGoals} ›</a>
            </span>
          </div>
        </section>
      ) : todos.length === 0 ? (
        <p className="muted">{t.budgetTooFew}</p>
      ) : null}
    </div>
  );
}

/** The goals sheet's id for „Was als einmalig zählt“ (19h frame 3), on the goals panel's prefix. */
export const ONE_OFF_SHEET = "einmalig";

/** „Ziel bis 50 %“, with „· 300 € drüber“ (or „darunter“ for Sparen) when the mark is amber. */
function rowTarget(line: BudgetLine, m: MonthBudgetText, f: PlanFormat): string {
  if (line.target == null) return m.noTarget;
  const target = line.key === "saving" ? m.targetFrom(f.percentValue(line.target)) : m.targetUpTo(f.percentValue(line.target));
  const mark = line.mark;
  if (!mark || mark.colour !== "amber") return target;
  const amount = f.euro(unsigned(mark.gap_monthly));
  return `${target} · ${negative(mark.gap_monthly) ? m.under(amount) : m.over(amount)}`;
}

function BudgetLineRow({
  line,
  plans,
  link,
}: {
  line: BudgetLine;
  plans: string | null;
  link: ReturnType<ReturnType<typeof useDetail>["linkProps"]>;
}) {
  const t = useT(planning);
  const m = useT(monthBudget);
  const f = usePlanFormat();
  const marked = line.key === "need" || line.key === "want" || line.key === "saving";
  const sub =
    line.fixed_monthly != null && line.variable != null
      ? m.fixedVariable(f.euro(line.fixed_monthly), f.euro(line.variable))
      : line.key === "saving"
        ? plans != null
          ? m.savingPlans(f.euro(plans))
          : t.savingSub
        : lineSub(line, t);
  const amount = f.euro(line.monthly);
  return (
    <li>
      <a className="budget-line" data-key={line.key} {...link} aria-label={t.openClass(lineLabel(t, line.key))}>
        <span className="budget-edge" style={{ background: BAR_TOKEN[line.key] }} aria-hidden="true" />
        {marked ? <Dot mark={line.mark} t={t} /> : <span className="budget-dot-space" />}
        <span className="budget-line-name">
          <strong>{lineLabel(t, line.key)}</strong>
          <span className="muted">{sub}</span>
        </span>
        <span className="budget-line-figures">
          <strong>{line.share != null ? m.amountShare(amount, f.wholePercent(line.share)) : amount}</strong>
          {marked ? <span className="muted">{rowTarget(line, m, f)}</span> : null}
        </span>
        <span className="budget-chevron" aria-hidden="true">
          ›
        </span>
      </a>
    </li>
  );
}

function lineSub(line: BudgetLine, t: T): string {
  if (line.key === "saving") return t.savingSub;
  const names = (line.codes ?? []).slice(0, 3).map((code) => categoryName(code.category));
  if (names.length > 0) return `${names.join(", ")}${(line.codes ?? []).length > 3 ? " …" : ""}`;
  if (line.key === "one_off") return t.oneOffSub;
  if (line.key === "uncategorized") return t.uncategorizedSub;
  return "";
}

// --- the class panel (D277) ------------------------------------------------------------------

export function BudgetClassPanel({
  lineKey,
  budget,
  draft,
  setDraft,
  onClose,
  onChanged,
  footer,
}: {
  lineKey: string;
  budget: BudgetView;
  /** The provider's draft: only the class moves (`budget_class_overrides`) are typed here; a
   * planned amount is typed on Haushalt › Durchspielen since §7.10 (a) (D299). */
  draft: HouseholdLevers;
  setDraft: SetDraft;
  onClose: () => void;
  /** Reads the plan, the Budget and the key figures again after an answer on a fixed payment. */
  onChanged: () => void;
  footer: ReactNode;
}) {
  const t = useT(planning);
  const m = useT(monthBudget);
  const f = usePlanFormat();
  // „Einmalig im Zeitraum“ stands beside the partition (D308), so it is not among the lines.
  const line = lineKey === "one_off" ? budget.one_off : (budget.lines ?? []).find((l) => l.key === lineKey);
  if (!line) return null;
  const codes = line.codes ?? [];
  const overrides = draft.budget_class_overrides ?? {};
  const switchable = line.key === "need" || line.key === "want";
  const months = budget.window.months ?? [];

  const setClass = (code: BudgetCode, cls: BudgetClass) =>
    setDraft((previous) => {
      const next = { ...(previous.budget_class_overrides ?? {}) };
      if (cls === code.default_class) delete next[code.category];
      else next[code.category] = cls;
      return { ...previous, budget_class_overrides: next };
    });

  const classSwitch = (category: string) => {
    const code = codes.find((c) => c.category === category);
    if (!code || !switchable || !code.default_class) return null;
    // The draft decides: an override it holds, else the code's default. The served class already
    // carries the saved override, so it is only what „verschoben“ compares against.
    const current = (overrides[category] as BudgetClass | undefined) ?? code.default_class;
    const moved = current !== (code.budget_class ?? code.default_class);
    return (
      <span className="budget-class-switch">
        <span className="muted">{t.belongsTo}</span>
        <span role="group" aria-label={t.classSwitch(categoryName(category))} className="spending-play-unit">
          {(["need", "want"] as const).map((cls) => (
            <button key={cls} type="button" aria-pressed={current === cls} onClick={() => setClass(code, cls)}>
              {lineLabel(t, cls)}
            </button>
          ))}
        </span>
        {moved ? <span className="muted">{t.classMoved}</span> : null}
      </span>
    );
  };

  return (
    <DetailPanel title={lineLabel(t, line.key)} backLabel={t.back} onClose={onClose}>
      <div className="stack">
        <p className="eyebrow">{t.selectedGroup}</p>
        <p className="budget-big">{f.euro(line.monthly)}</p>
        <p>
          {line.share != null ? f.wholePercent(line.share) : "–"}
          {switchable || line.key === "saving" ? ` · ${targetText(line, line.key !== "saving", t, f)}` : ""}
        </p>
        {line.mark ? (
          <p>
            <Dot mark={line.mark} t={t} /> {markSentence(line.mark, lineLabel(t, line.key), t, f)}
          </p>
        ) : null}
        {line.key === "need" && budget.housing ? (
          // The housing share (D276) is one line here since §7.12 (a); its own card went (19h).
          <p data-testid="budget-housing">
            {budget.housing.mark ? <Dot mark={budget.housing.mark} t={t} /> : null}{" "}
            {m.housingLine(budget.housing.share != null ? f.wholePercent(budget.housing.share) : "–", f.euro(budget.housing.monthly))}
            {budget.housing.target != null ? ` · ${m.housingTarget(f.percentValue(budget.housing.target))}` : ""}
          </p>
        ) : null}
        {switchable ? <FixedPayments line={line} onChanged={onChanged} /> : null}
        {line.key === "saving" ? <p className="muted">{t.savingPanel}</p> : null}
        {line.key === "one_off" ? <p className="muted">{t.oneOffPanel}</p> : null}
        {line.key !== "saving" ? (
          codes.length === 0 ? (
            <p className="muted">{t.noCodes}</p>
          ) : (
            <>
              {line.key !== "one_off" ? <p className="muted">{t.classIntro(f.monthRange(months))}</p> : null}
              {/* Every code with its mean and, in Notwendiges and Wünsche, its class switch (19h
                  frame 2); a planned amount is typed on Durchspielen (§7.10 (a), D299), so this
                  panel moves no figure before „Als Plan behalten“. */}
              <ul className="budget-codes">
                {codes.map((code) => (
                  <li key={code.category}>
                    <span>
                      {categoryName(code.category)}
                      {classSwitch(code.category)}
                    </span>
                    <span className="num">{f.euro(code.monthly)}</span>
                  </li>
                ))}
              </ul>
            </>
          )
        ) : null}
        {switchable ? (
          <p>
            <Link href={HOUSEHOLD_PLAY}>{m.playLink} ›</Link>
          </p>
        ) : null}
        {footer}
      </div>
    </DetailPanel>
  );
}

// --- the goals sheet (D274, D275) --------------------------------------------------------------

type GoalKey = keyof BudgetTargets;
const GOAL_KEYS: GoalKey[] = ["needs_max", "wants_max", "saving_min", "housing_max"];

function goalText(value: string | null | undefined, locale: string): string {
  if (value == null) return "";
  const trimmed = value.replace(/\.0$/, "");
  return locale === "de" ? trimmed.replace(".", ",") : trimmed;
}

/** "22,5" or "22.5" → "22.5"; null for empty; undefined when it is no target (0–100, one place). */
function parseGoal(text: string): string | null | undefined {
  const trimmed = text.trim().replace(",", ".");
  if (trimmed === "") return null;
  if (!/^\d{1,3}(\.\d)?$/.test(trimmed)) return undefined;
  if (Number(trimmed) > 100) return undefined;
  return trimmed;
}

export function GoalsPanel({
  budget,
  saved,
  current,
  onClose,
  onSaved,
}: {
  budget: BudgetView;
  /** The saved levers (the default scenario's, else the defaults): a save of the goals changes
   * only the targets, never another unsaved draft lever (D274). */
  saved: HouseholdLevers;
  current: HouseholdScenario | null;
  onClose: () => void;
  onSaved: (targets: BudgetTargets, row: HouseholdScenario) => void;
}) {
  const t = useT(planning);
  const f = usePlanFormat();
  const locale = useLocale();
  const ids = useId();
  const targets = budget.targets ?? {};
  const [texts, setTexts] = useState<Record<GoalKey, string>>(() => ({
    needs_max: goalText(targets.needs_max, locale),
    wants_max: goalText(targets.wants_max, locale),
    saving_min: goalText(targets.saving_min, locale),
    housing_max: goalText(targets.housing_max, locale),
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const parsed = Object.fromEntries(GOAL_KEYS.map((key) => [key, parseGoal(texts[key])])) as Record<GoalKey, string | null | undefined>;
  const invalid = GOAL_KEYS.some((key) => parsed[key] === undefined);
  const labels: Record<GoalKey, string> = {
    needs_max: t.goalNeeds,
    wants_max: t.goalWants,
    saving_min: t.goalSaving,
    housing_max: t.goalHousing,
  };

  function fill(reference: BudgetReference) {
    setTexts((previous) => ({
      ...previous,
      needs_max: previous.needs_max.trim() === "" ? goalText(reference.needs, locale) : previous.needs_max,
      wants_max: previous.wants_max.trim() === "" ? goalText(reference.wants, locale) : previous.wants_max,
      saving_min: previous.saving_min.trim() === "" ? goalText(reference.saving, locale) : previous.saving_min,
    }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (invalid) return;
    setBusy(true);
    setError(null);
    try {
      const next: BudgetTargets = {
        needs_max: parsed.needs_max ?? null,
        wants_max: parsed.wants_max ?? null,
        saving_min: parsed.saving_min ?? null,
        housing_max: parsed.housing_max ?? null,
      };
      const base = fillLevers(saved);
      const levers = { ...base, budget_targets: next, bands: current?.levers?.bands ?? base.bands ?? [] };
      const row = await saveDefaultScenario(levers, current);
      onSaved(next, row);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <DetailPanel title={t.goalsTitle} backLabel={t.back} onClose={onClose}>
      <form className="stack" onSubmit={(event) => void submit(event)} noValidate>
        <p className="muted">{t.goalsIntro}</p>
        {GOAL_KEYS.map((key) => {
          const id = `${ids}-${key}`;
          const bad = parsed[key] === undefined;
          return (
            <div className="field" key={key}>
              <label htmlFor={id}>{labels[key]}</label>
              <span className="spending-play-input">
                <input
                  id={id}
                  type="text"
                  inputMode="decimal"
                  value={texts[key]}
                  aria-invalid={bad ? true : undefined}
                  aria-describedby={bad ? `${id}-error` : undefined}
                  onChange={(event) => setTexts((previous) => ({ ...previous, [key]: event.target.value }))}
                />
                <span aria-hidden="true">%</span>
              </span>
              {bad ? (
                <ul id={`${id}-error`} className="field-errors">
                  <li>{t.goalInvalid}</li>
                </ul>
              ) : null}
            </div>
          );
        })}
        {error != null ? <PlanError error={error} /> : null}
        <div className="row">
          <button type="submit" disabled={busy || invalid}>
            {t.saveGoals}
          </button>
        </div>
        <p className="muted">{t.goalsSaved}</p>
        {(budget.references ?? []).length > 0 ? (
          <section className="stack" aria-labelledby={`${ids}-compare`}>
            <h3 id={`${ids}-compare`}>{t.compare}</h3>
            <ul className="budget-references">
              {(budget.references ?? []).map((reference) => (
                <li key={reference.key} className="stack">
                  <strong>{reference.label}</strong>
                  {reference.kind === "split" ? (
                    <span>
                      {t.splitParts(f.percentValue(reference.needs), f.percentValue(reference.wants), f.percentValue(reference.saving))}
                    </span>
                  ) : (
                    <ul>
                      {(reference.thresholds ?? []).map((band, index) => (
                        <li key={index}>
                          {t.band(
                            band.above != null ? f.percentValue(band.above) : null,
                            band.up_to != null ? f.percentValue(band.up_to) : null,
                            band.label,
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <span className="muted">
                    {t.measures}: {reference.measures}; {t.notOurs}: {reference.does_not_measure}
                  </span>
                  <span className="muted">{t.source(reference.source, f.date(reference.read_on))}</span>
                  {reference.kind === "split" ? (
                    <span>
                      <button type="button" className="secondary" aria-label={t.fillEmptyLabel(reference.label)} onClick={() => fill(reference)}>
                        {t.fillEmpty}
                      </button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </form>
    </DetailPanel>
  );
}
