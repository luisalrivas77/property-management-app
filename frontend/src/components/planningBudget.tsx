"use client";

// Planung › Budget (phase 10 D272–D277, mockup 17-C2): the household month as Notwendiges,
// Wünsche and Sparen (with one-off and not yet classified spending beside them), the housing
// share, each against a target the household set itself. Every figure is the server's
// (GET /household-budget); nothing is summed or divided here. Nothing is coloured before a
// target is saved; then green when met, amber when missed, never red (D274). The published
// splits are reference points with their sources: one tap fills only the empty fields of the
// goals form, never the housing target, and stores nothing (D275). A tapped line opens in the
// detail panel with its codes, their „geplant“ fields (D277) and the need/want switch.
import { type FormEvent, type ReactNode, useId, useState } from "react";

import type { components } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { lineLabel, planning, type PlanningText } from "@/i18n/planning";
import { describeError, ErrorNotice } from "@/lib/ErrorNotice";

import { DetailPanel, useDetail } from "./DetailPanel";
import { HealthTodoItem } from "./healthFigures";
import {
  fillLevers,
  type HouseholdLevers,
  type HouseholdPlan,
  type HouseholdScenario,
  saveDefaultScenario,
} from "./healthShared";
import { ALLOCATION_CATEGORY } from "./labels";
import { type PlanFormat, usePlanFormat } from "./planFormat";
import { type PlanPreview, SpendingPlaySection } from "./spendingPlay";

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

export function BudgetSection({ budget, onChanged }: { budget: BudgetView; onChanged: () => void }) {
  const t = useT(planning);
  const f = usePlanFormat();
  const goals = useDetail("goals");
  const line = useDetail("budget");
  const lines = budget.lines ?? [];
  const income = budget.income;
  const months = budget.window.months ?? [];
  const todos = budget.todos ?? [];
  const targets = budget.targets ?? {};
  const anyTarget = Object.values(targets).some((value) => value != null);
  const housing = budget.housing;
  const marks: { label: string; mark: BudgetMark }[] = [
    ...lines.flatMap((l) => (l.mark ? [{ label: lineLabel(t, l.key), mark: l.mark }] : [])),
    ...(housing?.mark ? [{ label: t.housing, mark: housing.mark }] : []),
  ];
  const barOk = lines.length > 0 && lines.every((l) => l.share != null && !negative(l.share));

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
          <p className="eyebrow" id="budget-month-title">
            {t.yourMonth}
          </p>
          <div className="budget-income">
            <p className="budget-big">{f.euro(income.monthly)}</p>
            <p className="muted">
              {t.incomePerMonth} · {t.meanOver(f.monthRange(months))}
              {income.rental_monthly && !/^-?0(\.0+)?$/.test(income.rental_monthly) ? ` · ${t.rentalIncluded(f.euro(income.rental_monthly))}` : ""}
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
              <li key={l.key}>
                <a className="budget-line" data-key={l.key} {...line.linkProps(l.key)} aria-label={t.openClass(lineLabel(t, l.key))}>
                  <span className="budget-edge" style={{ background: BAR_TOKEN[l.key] }} aria-hidden="true" />
                  {l.key === "need" || l.key === "want" || l.key === "saving" ? <Dot mark={l.mark} t={t} /> : <span className="budget-dot-space" />}
                  <span className="budget-line-name">
                    <strong>{lineLabel(t, l.key)}</strong>
                    <span className="muted">{lineSub(l, t)}</span>
                  </span>
                  <span className="budget-line-figures">
                    <strong>{f.euro(l.monthly)}</strong>
                    <span>{l.share != null ? f.wholePercent(l.share) : "–"}</span>
                    {l.key === "need" || l.key === "want" || l.key === "saving" ? (
                      <span className="muted">{targetText(l, l.key !== "saving", t, f)}</span>
                    ) : null}
                  </span>
                  <span className="budget-chevron" aria-hidden="true">
                    ›
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : todos.length === 0 ? (
        <p className="muted">{t.budgetTooFew}</p>
      ) : null}
      {housing ? (
        <section className="card stack" aria-labelledby="budget-housing-title">
          <div className="planning-card-head">
            <Dot mark={housing.mark} t={t} />
            <h2 id="budget-housing-title">{t.housing}</h2>
          </div>
          <p className="budget-big">{housing.share != null ? t.housingShare(f.wholePercent(housing.share)) : "–"}</p>
          <p>
            {t.perMonth(f.euro(housing.monthly))} · <span className="muted">{targetText(housing, true, t, f)}</span>
          </p>
          <p className="muted">
            {(housing.codes ?? []).map((code) => categoryName(code.category)).join(", ")}
            {(housing.codes ?? []).length > 0 ? " · " : ""}
            {t.housingNote}
          </p>
        </section>
      ) : null}
      {income ? (
        <section className="card stack budget-sentences" aria-label={anyTarget ? t.changeGoals : t.noTargetsTitle}>
          {anyTarget ? (
            marks.map(({ label, mark }) => (
              <p key={label}>
                <Dot mark={mark} t={t} /> {markSentence(mark, label, t, f)}
              </p>
            ))
          ) : (
            <>
              <h2>{t.noTargetsTitle}</h2>
              <p className="muted">{t.noTargetsText}</p>
            </>
          )}
          <div className="row">
            <a className="button-link secondary" {...goals.linkProps("eigene")}>
              {anyTarget ? t.changeGoals : t.setGoals}
            </a>
          </div>
        </section>
      ) : null}
    </div>
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
  plan,
  windowMonths,
  preview,
  draft,
  setDraft,
  onValidity,
  onClose,
  footer,
}: {
  lineKey: string;
  budget: BudgetView;
  plan: HouseholdPlan;
  windowMonths: readonly string[];
  preview: PlanPreview;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  onValidity: (invalid: boolean) => void;
  onClose: () => void;
  footer: ReactNode;
}) {
  const t = useT(planning);
  const f = usePlanFormat();
  const line = (budget.lines ?? []).find((l) => l.key === lineKey);
  if (!line) return null;
  const codes = line.codes ?? [];
  const playable = new Set<string>(codes.map((code) => code.category));
  const inPlay = new Set<string>(
    (plan.spending_play?.groups ?? []).flatMap((group) => group.rows.filter((row) => playable.has(row.code)).map((row) => row.code)),
  );
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

  const segments = preview.withChanges.spending_play?.rates ?? [];
  const planned = (draft.spending_changes ?? []).some((change) => playable.has(change.code));
  const last = (plan.months ?? []).map((m) => m.month).at(-1);

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
        {line.key === "saving" ? <p className="muted">{t.savingPanel}</p> : null}
        {line.key === "one_off" ? <p className="muted">{t.oneOffPanel}</p> : null}
        {line.key !== "saving" ? (
          codes.length === 0 ? (
            <p className="muted">{t.noCodes}</p>
          ) : (
            <>
              {line.key !== "one_off" ? <p className="muted">{t.classIntro(f.monthRange(months))}</p> : null}
              {line.key !== "one_off" ? (
                <SpendingPlaySection
                  saved={plan}
                  windowMonths={windowMonths}
                  preview={preview}
                  draft={draft}
                  setDraft={setDraft}
                  onValidity={onValidity}
                  only={inPlay}
                  extra={(category) => classSwitch(category)}
                />
              ) : null}
              {codes.some((code) => !inPlay.has(code.category) || line.key === "one_off") ? (
                <ul className="budget-codes">
                  {codes
                    .filter((code) => !inPlay.has(code.category) || line.key === "one_off")
                    .map((code) => (
                      <li key={code.category}>
                        <span>
                          {categoryName(code.category)}
                          {classSwitch(code.category)}
                        </span>
                        <span className="num">{f.euro(code.monthly)}</span>
                      </li>
                    ))}
                </ul>
              ) : null}
            </>
          )
        ) : null}
        {switchable ? (
          <section className="stack budget-with-plan" aria-label={t.withPlan}>
            <h3>{t.withPlan}</h3>
            {planned && segments.length > 0 ? (
              <>
                <ul className="planning-change-list">
                  {segments.map((segment) => (
                    <li key={segment.from_month}>
                      {t.withPlanRate(
                        f.ratio(segment.rate),
                        segment.to_month === last ? t.from(f.month(segment.from_month)) : f.monthRange([segment.from_month, segment.to_month]),
                      )}
                    </li>
                  ))}
                </ul>
                <p className="muted">{t.withPlanGap}</p>
              </>
            ) : (
              <p className="muted">{t.withPlanNone}</p>
            )}
          </section>
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
