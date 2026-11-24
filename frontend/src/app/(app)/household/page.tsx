"use client";

// Haushalt (phase 6 §13; phase 10 mockup 19): where the household's money went in one month,
// from the confirmed allocations of the private accounts only. Einnahmen, Ausgaben and
// Sparquote with their average, the lines still without a category (a link to Zu prüfen), the
// surplus per month over twelve months, the spending by taxonomy group, the income rows, and
// „Gespart, nicht ausgegeben“: transfers to own savings, depot or exchange accounts are saved,
// not spent (D162), listed from GET /transfers.
//
// Phase 9 (§14, D231, D232, D242): the month is ?monat=YYYY-MM, so Übersicht and Planung link to
// it; the „Ø“ figures are the means over the window's closed months (GET /health), headed with
// their real count, and stand back for a to-do below three; the flat's result R is an income row,
// so the month's rate is visibly the household's; a month whose stored closing differs from the
// month on read carries the chip „seit dem Abschluss geändert“, which opens both values in the
// detail panel (`#d-abschluss-<month>`).
//
// Phase 9 D263 (§7.7): for each code the default scenario in force on the month's last day names,
// „Geplant“ and „Unterschied“ beside the month's spending, from GET /household-plan/comparison;
// „bisher“ while the month is not closed, with the lines that still wait in Zu prüfen. No colour,
// no mark: the words are the server's.
//
// Phase 10 (D267, mockup 19): a group's name opens the detail panel (`#d-gruppe-<group>`) with its
// month, share and mean and its subcategories, each with its plan; the table still opens a group
// in place on the computer.
//
// Phase 10 §7.6 (Haushalt (1), (2)): „Gespart, nicht ausgegeben“ is no longer a card; the surplus
// card's line „Davon angelegt: <saved> ›“ opens it in the panel (`#d-angelegt-<month>`). On the
// phone the groups are rows (name and amount, a thin bar under it), the six largest first and
// „Alle <N> anzeigen“, with no table; the mean and the plan are in a tapped group's panel.
//
// Phase 10 §7.6 (Luis, 23 Nov 2026: „They should be moved to their respective sections as
// Planung will be removed“): below the month, the household's key figures that were on
// Übersicht: „Kennzahlen“ (`/household#figures-title`), since 23 Nov 2026 the three figures of
// Finanzielle Gesundheit (Sparquote, Liquiditätsreserve, Schuldendienstquote) as cards with their
// dots, and „Ampel-Grenzen festlegen“ (`/household#ampel-grenzen` opens it), from
// components/healthFigures.tsx. They are the window's figures, not the chosen month's, so they
// sit outside the month's own read.
//
// Phase 10 §7.6 Haushalt (3), build decision (a): „Was diesen Monat anders ist“ at the top, from
// GET /health's `differences`: the newest closed month (named, whichever month is chosen) against
// the mean of the closed months before it, at most three groups, each opening its payments in
// the panel (`#d-anders-<group>`). The server picks the groups and every figure; nothing is
// coloured and nothing is shown when it names none or has fewer than three months to compare.
//
// Phase 10 §7.6 Haushalt (4): the Budget in one line under it, from GET /household-budget: each of
// Notwendiges, Wünsche and Sparen that has a target saved, its monthly mean against the target's
// euros (both the server's), with the dot of Haushalt › Budget (green met, amber missed, never red)
// and a link there. No line before a target is saved; a failed read shows no line either.
//
// Phase 10 wave 3 (a) (§7.9 (a), D288; mockups 19b, 19c): Planung folded in. Haushalt has four
// tabs, ?tab=budget|ziel|reserve, and Monat (everything above) by default. Budget and Reserve are
// what Planung's sections were, with „Was mit dem Rest passiert“ (Planung › Anlegen) at Budget's
// foot; every lever is in one detail panel „Annahmen“ (`#d-annahmen-<group>`), and each tab shows
// its own figures with an unsaved change (components/householdPlan.tsx). Ziel is a placeholder
// until §7.9 (d).
import "./household.css";
import "@/components/planningSections.css";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useId, useState, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import {
  averageMoney,
  fromCents,
  type HouseholdCashflow,
  lastFullMonth,
  loadCashflow,
  monthsUpTo,
  rateOfSums,
  spent,
  sumMoney,
  toCents,
} from "@/components/cashflow";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { todayIso } from "@/components/format";
import { HealthTodoList, HouseholdHealthSection } from "@/components/healthFigures";
import { HouseholdPlanTab, PlanProvider } from "@/components/householdPlan";
import { useRecurringPayments } from "@/components/householdRecurring";
import { ALLOCATION_CATEGORY, HOUSEHOLD_GROUP, HOUSEHOLD_SPENDING_GROUPS } from "@/components/labels";
import { usePlanFormat } from "@/components/planFormat";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT, type Formatter } from "@/i18n";
import { common } from "@/i18n/common";
import { household } from "@/i18n/household";
import { lineLabel, planning } from "@/i18n/planning";
import { HOUSEHOLD_BUDGET, HOUSEHOLD_TABS, type HouseholdTab } from "@/lib/planningRoutes";

type HouseholdGroup = components["schemas"]["HouseholdGroup"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type TransferLinkView = components["schemas"]["TransferLinkView"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type Health = components["schemas"]["Health"];
type HealthTodo = components["schemas"]["HealthTodo"];
type HouseholdMonthFigures = components["schemas"]["HouseholdMonth"];
type SpendingComparison = components["schemas"]["SpendingComparison"];
type SpendingComparisonRow = components["schemas"]["SpendingComparisonRow"];
type MonthDifferences = components["schemas"]["MonthDifferences"];
type GroupDifference = components["schemas"]["GroupDifference"];
type BudgetView = components["schemas"]["BudgetView"];
type BudgetLine = components["schemas"]["BudgetLine"];

const MONTHS_SHOWN = 12;
const MONTHS_OFFERED = 24;

interface Year {
  /** The twelve months ending at the chosen one, oldest first; the chosen one is last. */
  months: HouseholdCashflow[];
  transfers: TransferLinkView[];
  /** The same twelve months as the household counts them (phase 9 D232, D235): the private
   * accounts plus the flat's result, so the Sparquote is the household's (Q-O139). */
  health: HealthMonthView[];
  /** GET /health: the window of closed months (D231) and its savings rate. */
  overall: Health;
  /** The window's months as the private accounts count them, oldest first; the „Ø“ columns. */
  windowMonths: HouseholdCashflow[];
  /** The chosen month beside the saved plan (D263 (d)); no rows when no scenario names a code. */
  comparison: SpendingComparison;
  /** GET /household-budget for Haushalt's Budget line (§7.6); null when the read failed. */
  budget: BudgetView | null;
}

async function loadYear(month: string): Promise<Year> {
  const shown = monthsUpTo(month, MONTHS_SHOWN);
  const [months, transfers, health, overall, comparison, budget] = await Promise.all([
    Promise.all(shown.map(loadCashflow)),
    ok(api.GET("/api/transfers", { params: { query: { month } } })),
    ok(api.GET("/api/health/months", { params: { query: { from: shown[0] ?? month, to: month } } })),
    ok(api.GET("/api/health")),
    ok(api.GET("/api/household-plan/comparison", { params: { query: { month } } })),
    // The Budget's line is an extra: a read that fails leaves it out, never the month.
    ok(api.GET("/api/household-budget")).catch(() => null),
  ]);
  const loaded = new Map(months.map((m) => [m.month, m]));
  const windowMonths = await Promise.all((overall.window.months ?? []).map((m) => loaded.get(m) ?? loadCashflow(m)));
  return { months, transfers, health, overall, windowMonths, comparison, budget };
}

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** ?monat=YYYY-MM when it is one of the months offered, else the last full month. */
function chosenMonth(raw: string | null, current: string): string {
  if (raw && MONTH.test(raw) && monthsUpTo(current, MONTHS_OFFERED).includes(raw)) return raw;
  return lastFullMonth();
}

/** Each to-do once, by its code, link and text, in the order first given. */
function uniqueTodos(todos: readonly HealthTodo[]): HealthTodo[] {
  const seen = new Set<string>();
  return todos.filter((todo) => {
    const key = `${todo.code}\n${todo.href}\n${todo.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** The Geldanlage group's fallback („Sonstige Geldanlage“), listed last when the group opens. */
const INVESTMENT_FALLBACK: AllocationCategory = "hh_investment";

function groupLabel(group: string): string {
  return HOUSEHOLD_GROUP[group as HouseholdGroup] ?? group;
}

function categoryLabel(category: string): string {
  return ALLOCATION_CATEGORY[category as AllocationCategory] ?? category;
}

/** A signed amount: „+1.350,00 €“, „-200,00 €“. */
function signed(f: Formatter, value: string): string {
  return `${toCents(value) > 0n ? "+" : ""}${f.money(value)}`;
}

/** „18. November 2026“ / „18 November 2026“: the day a scenario was saved. */
function longDay(f: Formatter, iso: string): string {
  const day = Number(iso.slice(8, 10));
  return f.locale === "de" ? `${day}. ${f.month(iso)}` : `${day} ${f.month(iso)}`;
}

/** „August 2026 – Oktober 2026“: the closed months the means run over. */
function monthSpan(f: Formatter, months: string[]): string {
  const first = months[0];
  const last = months[months.length - 1];
  if (!first || !last) return "";
  return first === last ? f.month(first) : `${f.month(first)} – ${f.month(last)}`;
}

/** An amount's share of the month's spending as a fraction („0.073“), for display only. */
function shareOf(amount: string, total: bigint): string {
  const value = toCents(amount);
  if (total <= 0n || value <= 0n) return "0";
  return (Number((value * 1000n) / total) / 1000).toFixed(3);
}

export default function HouseholdPage() {
  return (
    <Suspense fallback={<Fallback />}>
      <Household />
    </Suspense>
  );
}

function Fallback() {
  const c = useT(common);
  return <p className="muted">{c.loading}</p>;
}

function Household() {
  const t = useT(household);
  const f = useFormat();
  const ids = useId();
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const current = todayIso().slice(0, 7);
  const month = chosenMonth(search.get("monat"), current);
  const tab = householdTab(search.get("tab"));
  const setMonth = (next: string) => router.replace(`${pathname}?monat=${next}`, { scroll: false });
  // A month-end captured in „Noch keine Durchschnitte“ reads the reserve and the Kennzahlen below
  // again (phase 10 §7.6 (f)).
  const [healthRefresh, setHealthRefresh] = useState(0);
  const healthChanged = useCallback(() => setHealthRefresh((n) => n + 1), []);
  const tabs: { key: HouseholdTab; label: string }[] = [
    { key: "monat", label: t.tabMonth },
    { key: "budget", label: t.tabBudget },
    { key: "ziel", label: t.tabGoal },
    { key: "reserve", label: t.tabReserve },
  ];
  // Monat keeps the chosen month; the other tabs run over the closed months, so a link back to
  // Monat lands on the month that was shown.
  const tabHref = (key: HouseholdTab) => (key === "monat" ? `${pathname}?monat=${month}` : `${pathname}?tab=${key}`);
  const planTab = tab === "budget" || tab === "reserve" ? tab : null;

  return (
    <section>
      <div className="page-head planning-head">
        <div>
          <h1>{t.title}</h1>
          <p className="household-sub">{t.subtitle}</p>
        </div>
        <span className="spacer" />
        {tab === "monat" ? (
          <label htmlFor={`${ids}-month`} className="inline">
            {t.month}
            <select id={`${ids}-month`} value={month} onChange={(event) => setMonth(event.target.value)}>
              {monthsUpTo(current, MONTHS_OFFERED)
                .reverse()
                .map((m) => (
                  <option key={m} value={m}>
                    {f.month(m)}
                    {m === current ? t.running : ""}
                  </option>
                ))}
            </select>
          </label>
        ) : null}
      </div>
      <nav className="planning-chips" aria-label={t.tabs}>
        {tabs.map((item) => (
          <Link key={item.key} href={tabHref(item.key)} aria-current={item.key === tab ? "page" : undefined} scroll={false}>
            {item.label}
          </Link>
        ))}
      </nav>
      {tab === "monat" ? (
        <>
          <HouseholdYear month={month} onHealthChanged={healthChanged} />
          <div className="stack household-health">
            <HouseholdHealthSection refresh={healthRefresh} />
          </div>
        </>
      ) : null}
      {tab === "ziel" ? <p className="card muted">{t.goalSoon}</p> : null}
      {/* Budget and Reserve share one plan and one draft (one element in one place), so an unsaved
          change survives a switch between them (§7.9 (a)). */}
      {planTab ? (
        <PlanProvider page={`${pathname}?tab=${planTab}`} backLabel={t.title} oneOff>
          <HouseholdPlanTab tab={planTab} />
        </PlanProvider>
      ) : null}
    </section>
  );
}

/** ?tab=budget|ziel|reserve, else Monat (D288). */
function householdTab(raw: string | null): HouseholdTab {
  return raw !== null && (HOUSEHOLD_TABS as readonly string[]).includes(raw) ? (raw as HouseholdTab) : "monat";
}

function HouseholdYear({ month, onHealthChanged }: { month: string; onHealthChanged: () => void }) {
  const load = useCallback(() => loadYear(month), [month]);
  const year = useLoad(load);
  const { reload } = year;
  const changed = useCallback(() => {
    reload();
    onHealthChanged();
  }, [reload, onHealthChanged]);
  return <Loading state={year}>{(data) => <HouseholdMonth year={data} onChanged={changed} />}</Loading>;
}

function HouseholdMonth({ year, onChanged }: { year: Year; onChanged: () => void }) {
  const t = useT(household);
  const f = useFormat();
  const groupDetail = useDetail("gruppe");
  const closingDetail = useDetail("abschluss");
  const savedDetail = useDetail("angelegt");
  const differenceDetail = useDetail("anders");
  // The recurring payments (§7.9 (b)): their own read, so a failure leaves the card out only.
  const recurring = useRecurringPayments(t.title);
  const { months, transfers, health, overall, windowMonths, comparison, budget } = year;
  const month = months[months.length - 1];
  if (!month) return null;
  const view = health.find((h) => h.month === month.month);
  const householdNow = view?.household;
  const spentNow = spent(month);
  const closed = overall.window.months ?? [];
  const enough = overall.window.enough;
  const means = enough ? windowMonths : [];
  const mean = (values: string[]) => (enough ? f.money(averageMoney(values)) : "–");
  // The window's rate as the household counts it (D235), the same Σ S ÷ Σ E as Übersicht.
  const windowViews = health.filter((h) => closed.includes(h.month)).map((h) => h.household);
  const windowRate = overall.savings_rate?.rate ?? (windowViews.length === closed.length ? rateOfSums(windowViews) : null);
  const last = closed[closed.length - 1];
  // What holds the month after the window open (its own closing to-dos), when it is loaded.
  const after = health.find((h) => (last === undefined || h.month > last) && !h.closed.closed);
  const tooFew = (overall.todos ?? []).filter((todo) => todo.code === "too_few_months");
  // What holds the next month open: the months shown and GET /health's own (the same month's,
  // unless the chosen month is before it or no month is closed yet), each once; the Kennzahlen
  // below no longer repeat them (phase 10 §7.6 (f)).
  const holding = uniqueTodos([
    ...(after?.closed.todos ?? []),
    ...(overall.todos ?? []).filter((todo) => todo.code !== "too_few_months" && todo.code !== "bands_unconfirmed"),
  ]);

  const differences = overall.differences ?? null;
  const shownDifference = (differences?.groups ?? []).find((d) => d.group === differenceDetail.id);

  let panel = null;
  if (differences && shownDifference) {
    panel = (
      <DetailPanel title={groupLabel(shownDifference.group)} backLabel={t.title} onClose={differenceDetail.close}>
        <DifferencePanel differences={differences} difference={shownDifference} />
      </DetailPanel>
    );
  } else if (view?.stored && closingDetail.id === month.month) {
    panel = (
      <DetailPanel title={t.changedTitle} backLabel={t.title} onClose={closingDetail.close}>
        <ChangedSinceClosing month={month.month} now={view.household} stored={view.stored} />
      </DetailPanel>
    );
  } else if (savedDetail.id === month.month) {
    panel = (
      <DetailPanel title={t.savedTitle} backLabel={t.title} onClose={savedDetail.close}>
        <SavedPanel month={month} transfers={transfers} />
      </DetailPanel>
    );
  } else if (groupDetail.id) {
    panel = (
      <DetailPanel title={groupLabel(groupDetail.id)} backLabel={t.title} onClose={groupDetail.close}>
        <GroupPanel group={groupDetail.id} month={month} windowMonths={means} comparison={comparison} />
      </DetailPanel>
    );
  } else if (recurring.panel) {
    panel = recurring.panel;
  }

  return (
    <DetailLayout panel={panel}>
      <div className="stack planning">
        {view?.stored ? (
          <p>
            <a {...closingDetail.linkProps(month.month)} className="household-changed">
              <span className="badge badge-warn">{t.changed} ›</span>
            </a>
          </p>
        ) : null}
        {differences ? <Differences differences={differences} link={differenceDetail.linkProps} /> : null}
        {recurring.card}
        {budget ? <BudgetOneLine budget={budget} /> : null}
        <div className="planning-kpis">
          <div className="planning-kpi" role="group" aria-label={t.income}>
            <p className="planning-kpi-label">{t.income}</p>
            <p className="planning-kpi-value">{f.money(month.income)}</p>
            <p className="planning-kpi-sub">
              {t.mean(closed.length)} {mean(windowMonths.map((m) => m.income))}
            </p>
          </div>
          <div className="planning-kpi" role="group" aria-label={t.spending}>
            <p className="planning-kpi-label">{t.spending}</p>
            <p className="planning-kpi-value">{f.money(spentNow)}</p>
            <p className="planning-kpi-sub">
              {t.mean(closed.length)} {mean(windowMonths.map(spent))}
            </p>
          </div>
          <div className="planning-kpi" role="group" aria-label={t.savingsRate}>
            <p className="planning-kpi-label">{t.savingsRate}</p>
            <p className="planning-kpi-value">{householdNow?.savings_rate != null ? f.percent(householdNow.savings_rate) : "–"}</p>
            <p className="planning-kpi-sub">
              {t.mean(closed.length)} {enough && windowRate != null ? f.percent(windowRate) : "–"} · {t.savingsRateHow}
            </p>
          </div>
          <div className={month.open_lines > 0 ? "planning-kpi household-open" : "planning-kpi"} role="group" aria-label={t.uncategorised}>
            <p className="planning-kpi-label">{t.uncategorised}</p>
            <p className="planning-kpi-value">{t.openLines(month.open_lines)}</p>
            <p className="planning-kpi-sub">{month.open_lines > 0 ? <Link href="/inbox">{t.answerInInbox}</Link> : t.allAssigned}</p>
          </div>
        </div>
        {enough ? (
          <p className="muted">{t.basis(closed.length, monthSpan(f, closed))}</p>
        ) : (
          <section className="card stack" aria-labelledby="window-title">
            <h2 id="window-title">{t.noMeansTitle}</h2>
            <p className="muted">{t.noMeansText(closed.length)}</p>
            <HealthTodoList todos={[...tooFew, ...holding]} onChanged={onChanged} />
          </section>
        )}
        <SurplusBars months={months} windowMonths={means} savedLink={savedDetail.linkProps(month.month)} />
        <SpendingTable
          months={months}
          windowMonths={means}
          comparison={comparison}
          groupLink={groupDetail.linkProps}
          openGroup={groupDetail.id}
        />
        <IncomeCard month={month} household={householdNow ?? null} />
      </div>
    </DetailLayout>
  );
}

/** Überschuss je Monat: income less spending per month, the chosen month in the strong
 * colour. The heights are the returned figures relative to the largest, for display only.
 * Under the bars one line „Davon angelegt: <the month's `saved`> ›“ (§7.6 Haushalt (1)), which
 * opens what was saved, not spent, in the detail panel (`#d-angelegt-<month>`). */
function SurplusBars({
  months,
  windowMonths,
  savedLink,
}: {
  months: HouseholdCashflow[];
  windowMonths: HouseholdCashflow[];
  savedLink: LinkProps;
}) {
  const t = useT(household);
  const f = useFormat();
  const chosen = months[months.length - 1];
  const short = t.shortMonths.split(",");
  const largest = Math.max(0, ...months.map((m) => Math.abs(Number(m.surplus))));
  return (
    <section className="card" aria-labelledby="surplus-title">
      <div className="planning-card-head">
        <h2 id="surplus-title">{t.surplusTitle}</h2>
        <span className="spacer" />
        <span className="muted">
          {t.surplusHow}
          {windowMonths.length > 0 ? ` · ${t.mean(windowMonths.length)} ${f.money(averageMoney(windowMonths.map((m) => m.surplus)))}` : ""}
        </span>
      </div>
      <ul className="planning-bars">
        {months.map((m, index) => {
          const value = Number(m.surplus);
          const height = largest > 0 ? (Math.abs(value) / largest) * 100 : 0;
          const classes = ["planning-bar"];
          if (index === months.length - 1) classes.push("planning-bar-sale");
          if (value < 0) classes.push("household-bar-negative");
          return (
            <li key={m.month} title={`${f.month(m.month)}: ${f.money(m.surplus)}`}>
              <span className={classes.join(" ")} style={{ height: `${height}%` }} />
              <span className="planning-bar-year" aria-hidden="true">
                {short[Number(m.month.slice(5, 7)) - 1] ?? m.month}
              </span>
              <span className="visually-hidden">
                {f.month(m.month)}: {f.money(m.surplus)}
              </span>
            </li>
          );
        })}
      </ul>
      {chosen ? (
        <p className="household-invested">
          <a {...savedLink}>
            {t.investedOf(f.money(chosen.saved))} <span aria-hidden="true">›</span>
          </a>
        </p>
      ) : null}
    </section>
  );
}

type LinkProps = ReturnType<ReturnType<typeof useDetail>["linkProps"]>;

/** „Restaurant: 180,00 € mehr als üblich“ / „… weniger als üblich“, from the server's words. */
function DifferenceText({ difference: d }: { difference: GroupDifference }) {
  const t = useT(household);
  const f = useFormat();
  const amount = f.money(d.difference.replace(/^-/, ""));
  return <>{d.words === "more" ? t.differenceMore(groupLabel(d.group), amount) : t.differenceLess(groupLabel(d.group), amount)}</>;
}

/** The Budget in one line (§7.6 Haushalt (4)): the lines with a mark, i.e. with a saved target. */
function BudgetOneLine({ budget }: { budget: BudgetView }) {
  const t = useT(household);
  const tp = useT(planning);
  const p = usePlanFormat();
  const marked = (budget.lines ?? []).flatMap((l: BudgetLine) =>
    l.mark && (l.key === "need" || l.key === "want" || l.key === "saving") ? [{ key: l.key, monthly: l.monthly, mark: l.mark }] : [],
  );
  if (marked.length === 0) return null;
  return (
    <section className="card household-budget-line" aria-labelledby="budget-line-title">
      <p>
        <strong id="budget-line-title">{t.budgetLineTitle}</strong>{" "}
        <span className="muted">({t.budgetLineBasis((budget.window.months ?? []).length)})</span>
      </p>
      <p>
        {marked.map(({ key, monthly, mark }) => (
          <span key={key} className="household-budget-item">
            <span
              className={`budget-dot budget-dot-${mark.colour}`}
              role="img"
              aria-label={mark.colour === "green" ? tp.markGreen : tp.markAmber}
            />{" "}
            {(key === "saving" ? t.budgetOfMinimum : t.budgetOf)(lineLabel(tp, key), p.euro(monthly), p.euro(mark.at_target))}
          </span>
        ))}
        <Link href={HOUSEHOLD_BUDGET}>{t.planningBudget} ›</Link>
      </p>
    </section>
  );
}

/** „Was diesen Monat anders ist“ (§7.6 Haushalt (3)): up to three lines, each opening its group's
 * payments in the panel; no card when the server names no group. */
function Differences({ differences, link }: { differences: MonthDifferences; link: (group: string) => LinkProps }) {
  const t = useT(household);
  const f = useFormat();
  const groups = differences.groups ?? [];
  if (groups.length === 0) return null;
  return (
    <section className="card stack" aria-labelledby="differences-title">
      <div>
        <h2 id="differences-title">{t.differencesTitle}</h2>
        <p className="muted">{t.differencesBasis(f.month(differences.month), differences.baseline.length)}</p>
      </div>
      <ul className="household-rows">
        {groups.map((d) => (
          <li key={d.group}>
            <a {...link(d.group)}>
              <DifferenceText difference={d} /> <span aria-hidden="true">›</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** A named group's month and mean, and the payments behind the month (the server's list, its
 * amounts signed as spending, so they add up to the month). */
function DifferencePanel({ differences, difference }: { differences: MonthDifferences; difference: GroupDifference }) {
  const t = useT(household);
  const f = useFormat();
  const payments = difference.payments ?? [];
  return (
    <div className="stack">
      <p>
        <DifferenceText difference={difference} />
      </p>
      <ul className="household-rows">
        <li>
          <span>{f.month(differences.month)}</span>
          <span>{f.money(difference.amount)}</span>
        </li>
        <li>
          <span>{t.differenceMean(differences.baseline.length)}</span>
          <span>{f.money(difference.mean)}</span>
        </li>
      </ul>
      <h3>{t.differencePayments}</h3>
      {payments.length === 0 ? (
        <p className="muted">{t.differenceNoPayments}</p>
      ) : (
        <ul className="household-rows">
          {payments.map((p) => (
            <li key={p.allocation_id}>
              <span>
                {f.date(p.booked_on)} · {p.payee || p.purpose || categoryLabel(p.category)}
                <br />
                <span className="muted">
                  {categoryLabel(p.category)} · {p.account_label}
                </span>
              </span>
              <span>{f.money(p.amount)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The codes of a group to list under it, the group's fallback last; planned codes included. */
function orderedCodes(group: string, both: HouseholdCashflow[], planned: SpendingComparisonRow[], month: HouseholdCashflow): string[] {
  const codes = [...new Set([...groupCodes(both, group), ...planned.filter((r) => r.group === group && !onGroupRow(r)).map((r) => r.code)])];
  const codeNow = (code: string) => month.spending_by_category?.[group]?.[code] ?? "0.00";
  const fallback = fallbackOf(group);
  return codes.sort((a, b) => {
    if (a === fallback || b === fallback) return a === fallback ? 1 : -1;
    const diff = toCents(codeNow(b)) - toCents(codeNow(a));
    return diff > 0n ? 1 : diff < 0n ? -1 : categoryLabel(a).localeCompare(categoryLabel(b));
  });
}

function plannedFor(planned: SpendingComparisonRow[], group: string, code: string | null): SpendingComparisonRow | undefined {
  return planned.find((row) => row.group === group && (code === null ? onGroupRow(row) : row.code === code && !onGroupRow(row)));
}

/** Ausgaben nach Kategorie: the chosen month's groups, largest first, with their share of the
 * month's spending and their average. A group booked under more than one code, or under a
 * subcategory (D159 amended 14 Nov 2026, migration 0032), opens to its codes, indented under it,
 * largest first and the group's fallback („Sonstige …“) last; its name opens the panel. */
function SpendingTable({
  months,
  windowMonths,
  comparison,
  groupLink,
  openGroup,
}: {
  months: HouseholdCashflow[];
  windowMonths: HouseholdCashflow[];
  comparison: SpendingComparison;
  groupLink: (group: string) => LinkProps;
  openGroup: string | null;
}) {
  const t = useT(household);
  const f = useFormat();
  const planned = comparison.rows ?? [];
  // A planned subcategory opens its group, as mockup 10b shows it.
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set(planned.filter((row) => !onGroupRow(row)).map((row) => row.group)));
  const month = months[months.length - 1];
  if (!month) return null;
  const total = toCents(spent(month));
  const both = [...months, ...windowMonths];
  const groups = [...new Set([...both.flatMap((m) => Object.keys(m.spending_by_group)), ...planned.map((row) => row.group)])];
  const withPlan = planned.length > 0;
  const planCells = (row: SpendingComparisonRow | undefined) =>
    withPlan ? (
      <>
        <td className="num muted household-wide">{row ? f.money(row.planned) : ""}</td>
        <td className="household-wide">{row ? row.text : ""}</td>
      </>
    ) : null;
  const meanOf = (value: (m: HouseholdCashflow) => string) => (windowMonths.length > 0 ? f.money(averageMoney(windowMonths.map(value))) : "–");
  const now = (group: string) => month.spending_by_group[group] ?? "0.00";
  groups.sort((a, b) => {
    const diff = toCents(now(b)) - toCents(now(a));
    return diff > 0n ? 1 : diff < 0n ? -1 : groupLabel(a).localeCompare(groupLabel(b));
  });
  const bar = (amount: string) => (
    <span className="household-share" aria-hidden="true">
      <span style={{ width: `${Number(shareOf(amount, total)) * 100}%` }} />
    </span>
  );
  const toggle = (group: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  return (
    <section className="card household-spending" aria-labelledby="spending-title">
      <h2 id="spending-title">{t.byCategory}</h2>
      {withPlan ? (
        <p className="muted">
          {t.plannedOnly(comparison.scenario_created_at ? t.savedOn(longDay(f, comparison.scenario_created_at)) : "")}
          {t.under}
          <Link href={HOUSEHOLD_BUDGET}>{t.planningBudget}</Link>.
        </p>
      ) : null}
      {groups.length === 0 ? (
        <p className="muted">{t.noSpending}</p>
      ) : (
        <>
          <PhoneGroups
            groups={groups}
            amountOf={now}
            bar={bar}
            plansOf={(group) => planned.filter((row) => row.group === group).length}
            heading={`${f.month(month.month)}${withPlan && !comparison.final ? t.soFar : ""}`}
            groupLink={groupLink}
          />
          <div className="table-wrap household-table-wrap">
            <table className="household-table">
              <thead>
                <tr>
                  <th scope="col">{t.category}</th>
                  <th scope="col" className="household-wide">
                    {t.share}
                  </th>
                  <th scope="col" className="num">
                    {f.month(month.month)}
                    {withPlan && !comparison.final ? t.soFar : ""}
                  </th>
                  {withPlan ? (
                    <>
                      <th scope="col" className="num household-wide">
                        {t.planned}
                      </th>
                      <th scope="col" className="household-wide">
                        {t.difference}
                      </th>
                    </>
                  ) : null}
                  <th scope="col" className="num household-wide">
                    {windowMonths.length > 0 ? t.meanShort(windowMonths.length) : "Ø"}
                  </th>
                </tr>
              </thead>
              <tbody>
                {groups.flatMap((group) => {
                  const codes = orderedCodes(group, both, planned, month);
                  const expanded = open.has(group);
                  const rowId = `spending-${group}`;
                  const rows = [
                    <tr key={group} aria-current={openGroup === group ? "true" : undefined}>
                      <th scope="row">
                        {codes.length > 0 ? (
                          <button
                            type="button"
                            className="household-toggle"
                            aria-expanded={expanded}
                            aria-controls={codes.map((code) => `${rowId}-${code}`).join(" ")}
                            aria-label={t.openGroup(groupLabel(group))}
                            onClick={() => toggle(group)}
                          >
                            <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
                          </button>
                        ) : null}{" "}
                        <a {...groupLink(group)} className="household-group">
                          {groupLabel(group)}
                        </a>
                      </th>
                      <td className="household-wide">{bar(now(group))}</td>
                      <td className="num">{f.money(now(group))}</td>
                      {planCells(plannedFor(planned, group, null))}
                      <td className="num muted household-wide">{meanOf((m) => m.spending_by_group[group] ?? "0.00")}</td>
                    </tr>,
                  ];
                  if (!expanded) return rows;
                  const codeNow = (code: string) => month.spending_by_category?.[group]?.[code] ?? "0.00";
                  for (const code of codes) {
                    rows.push(
                      <tr key={`${group}-${code}`} id={`${rowId}-${code}`} className="household-subrow">
                        <th scope="row">
                          {categoryLabel(code)}
                        </th>
                        <td className="household-wide">{bar(codeNow(code))}</td>
                        <td className="num">{f.money(codeNow(code))}</td>
                        {planCells(plannedFor(planned, group, code))}
                        <td className="num muted household-wide">{meanOf((m) => m.spending_by_category?.[group]?.[code] ?? "0.00")}</td>
                      </tr>,
                    );
                  }
                  return rows;
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">{withPlan && !comparison.final ? t.sumSoFar : t.sum}</th>
                  <td className="household-wide" />
                  <td className="num">
                    <strong>{f.money(spent(month))}</strong>
                  </td>
                  {withPlan ? (
                    <>
                      <td className="household-wide" />
                      <td className="household-wide" />
                    </>
                  ) : null}
                  <td className="household-wide" />
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}
      {withPlan && !comparison.final ? (
        <p className="muted">
          {t.notFinal}
          {(comparison.open_lines ?? 0) > 0 ? (
            <>
              {" "}
              <Link href="/inbox">{t.waiting(t.openLines(comparison.open_lines ?? 0), comparison.open_lines ?? 0)}</Link>
            </>
          ) : null}
        </p>
      ) : null}
    </section>
  );
}

/** The groups shown on the phone before „Alle <N> anzeigen“ (§7.6 Haushalt (2)). */
const PHONE_GROUPS = 6;

/** On the phone (§7.6 Haushalt (2)): each group one row, its month's amount beside its name and
 * a thin share bar under it, no table and no sideways scroll; the mean and the plan open with
 * the group in the detail panel. The six largest first, the rest behind „Alle <N> anzeigen“.
 * Hidden from 720 px, where the table is shown. */
function PhoneGroups({
  groups,
  amountOf,
  bar,
  plansOf,
  heading,
  groupLink,
}: {
  /** Largest first, as the table orders them. */
  groups: string[];
  amountOf: (group: string) => string;
  bar: (amount: string) => ReactNode;
  plansOf: (group: string) => number;
  /** The month's name, „bisher“ while it runs. */
  heading: string;
  groupLink: (group: string) => LinkProps;
}) {
  const t = useT(household);
  const f = useFormat();
  const [all, setAll] = useState(false);
  const shown = all ? groups : groups.slice(0, PHONE_GROUPS);
  return (
    <div className="household-groups">
      <p className="muted household-groups-month">{heading}</p>
      <ul className="household-groups-list">
        {shown.map((group) => {
          const plans = plansOf(group);
          return (
            <li key={group}>
              <a {...groupLink(group)} className="household-group-row">
                <span className="household-group-line">
                  <span className="household-group-name">{groupLabel(group)}</span>
                  {plans > 0 ? <span className="badge badge-muted">{t.plannedCount(plans)}</span> : null}
                  <strong>{f.money(amountOf(group))}</strong>
                </span>
                {bar(amountOf(group))}
              </a>
            </li>
          );
        })}
      </ul>
      {groups.length > PHONE_GROUPS && !all ? (
        <button type="button" className="secondary" onClick={() => setAll(true)}>
          {t.showAllGroups(groups.length)}
        </button>
      ) : null}
    </div>
  );
}

/** A tapped group in the panel: its month, share and mean, and its subcategories with their plan
 * (D263): each code's share, mean, „Geplant … · <the server's words>“ and amount. */
function GroupPanel({
  group,
  month,
  windowMonths,
  comparison,
}: {
  group: string;
  month: HouseholdCashflow;
  windowMonths: HouseholdCashflow[];
  comparison: SpendingComparison;
}) {
  const t = useT(household);
  const f = useFormat();
  const planned = comparison.rows ?? [];
  const total = toCents(spent(month));
  const amount = month.spending_by_group[group] ?? "0.00";
  const both = windowMonths.length > 0 ? [month, ...windowMonths] : [month];
  const codes = orderedCodes(group, both, planned, month);
  const codeNow = (code: string) => month.spending_by_category?.[group]?.[code] ?? "0.00";
  const meanOf = (value: (m: HouseholdCashflow) => string) => f.money(averageMoney(windowMonths.map(value)));
  const groupPlan = plannedFor(planned, group, null);
  const anyPlan = planned.some((row) => row.group === group);
  return (
    <div className="stack">
      <p className="muted">{t.monthByCategory(f.month(month.month))}</p>
      <dl className="facts">
        <div>
          <dt>{f.month(month.month)}</dt>
          <dd>
            <strong>{f.money(amount)}</strong>
          </dd>
        </div>
        <div>
          <dt>{t.shareOfSpending}</dt>
          <dd>{f.percent(shareOf(amount, total))}</dd>
        </div>
        {windowMonths.length > 0 ? (
          <div>
            <dt>{t.meanOver(windowMonths.length, monthSpan(f, windowMonths.map((m) => m.month)))}</dt>
            <dd>{meanOf((m) => m.spending_by_group[group] ?? "0.00")}</dd>
          </div>
        ) : null}
        {groupPlan ? (
          <div>
            <dt>{t.planned}</dt>
            <dd>{t.plannedLine(f.money(groupPlan.planned), groupPlan.text)}</dd>
          </div>
        ) : null}
      </dl>
      {codes.length > 0 ? (
        <>
          <h3>{t.subcategories}</h3>
          <ul className="detail-rows household-panel-rows">
            {codes.map((code) => {
              const plan = plannedFor(planned, group, code);
              const sub = [f.percent(shareOf(codeNow(code), total))];
              if (windowMonths.length > 0) sub.push(`Ø ${meanOf((m) => m.spending_by_category?.[group]?.[code] ?? "0.00")}`);
              return (
                <li key={code}>
                  <div className="detail-row">
                    <span className="detail-row-text">
                      <span className="detail-row-title">{categoryLabel(code)}</span>
                      <span className="detail-row-sub">{sub.join(" · ")}</span>
                      {plan ? <span className="detail-row-sub">{t.plannedLine(f.money(plan.planned), plan.text)}</span> : null}
                    </span>
                    <strong>{f.money(codeNow(code))}</strong>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
      <p className="muted hint">
        {anyPlan && comparison.scenario_created_at ? t.planFooter(longDay(f, comparison.scenario_created_at)) : t.planFooterNone}
        <Link href={HOUSEHOLD_BUDGET}>{t.planningBudget}</Link>.
      </p>
    </div>
  );
}

/** A planned code shown on its group's row: the group's fallback, which stands for the whole
 * group (§7.6 (1)), or the only code of a group without subcategories. */
function onGroupRow(row: SpendingComparisonRow): boolean {
  const fallback = fallbackOf(row.group);
  return fallback === null || fallback === row.code;
}

/** The group's fallback code (the last of its codes in HOUSEHOLD_SPENDING_GROUPS), or null for
 * a group without subcategories. */
function fallbackOf(group: string): string | null {
  const categories = HOUSEHOLD_SPENDING_GROUPS.find((entry) => entry.group === group)?.categories;
  return categories ? (categories[categories.length - 1] ?? null) : null;
}

/** The codes a group was booked under in the twelve months, when they say more than the group
 * row does: two or more codes, or one that is not the group's fallback inside a group with
 * subcategories. Otherwise none, and the row does not open. */
function groupCodes(months: HouseholdCashflow[], group: string): string[] {
  const codes = [...new Set(months.flatMap((m) => Object.keys(m.spending_by_category?.[group] ?? {})))];
  if (codes.length > 1) return codes;
  const fallback = fallbackOf(group);
  return codes.length === 1 && fallback !== null && codes[0] !== fallback ? codes : [];
}

function IncomeCard({ month, household: figures }: { month: HouseholdCashflow; household: HouseholdMonthFigures | null }) {
  const t = useT(household);
  const f = useFormat();
  const rows = Object.entries(month.income_by_category ?? {});
  // The flat's cash result before repayment (R, D233): part of the household's income, signed.
  const flat = figures && toCents(figures.rental_result) !== 0n ? figures.rental_result : null;
  return (
    <section className="card stack household-side" aria-labelledby="income-title">
      <h2 id="income-title">{t.income}</h2>
      {rows.length === 0 && flat === null ? (
        <p className="muted">{t.noIncome}</p>
      ) : (
        <ul className="household-rows">
          {rows.map(([category, amount]) => (
            <li key={category}>
              <span>{categoryLabel(category)}</span>
              <strong>{f.money(amount)}</strong>
            </li>
          ))}
          {flat !== null ? (
            <li>
              <span>{t.flatResult}</span>
              <strong>{signed(f, flat)}</strong>
            </li>
          ) : null}
        </ul>
      )}
      {flat !== null ? <p className="muted">{t.flatHint}</p> : null}
    </section>
  );
}

/** „seit dem Abschluss geändert“ (D242): the month's stored closing beside the month on read. */
function ChangedSinceClosing({ month, now, stored }: { month: string; now: HouseholdMonthFigures; stored: HouseholdMonthFigures }) {
  const t = useT(household);
  const f = useFormat();
  const rows: [keyof HouseholdMonthFigures, string][] = [
    ["income", t.incomeWithFlat],
    ["spending", t.spending],
    ["saved", t.saved],
  ];
  return (
    <div className="stack">
      <p className="muted">{f.month(month)}</p>
      <p>{t.changedText(f.month(month))}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{t.figure}</th>
              <th scope="col" className="num">
                {t.atClosing}
              </th>
              <th scope="col" className="num">
                {t.today}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, label]) => (
              <tr key={key}>
                <th scope="row">{label}</th>
                <td className="num">{f.money(stored[key] as string)}</td>
                <td className="num">{f.money(now[key] as string)}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">{t.savingsRate}</th>
              <td className="num">{stored.savings_rate != null ? f.percent(stored.savings_rate) : "–"}</td>
              <td className="num">{now.savings_rate != null ? f.percent(now.savings_rate) : "–"}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** „Gespart, nicht ausgegeben“ (D162): what went to own savings, depot or exchange accounts,
 * from the transfer links, plus payments booked „Geldanlage“ to an investment the app holds no
 * account of (D159 amended 13 Nov 2026, in `saved` from the API), and what of the surplus
 * stayed on the current accounts. Since D159 amended (6) (migration 0036) the Geldanlage part
 * is its own row that opens to its subcategories (Immobilien, Wertpapiere & ETF, Krypto,
 * Sparen & Tagesgeld, „Sonstige Geldanlage“ last), as a spending group opens in the table;
 * only codes with an amount this month are listed. Since phase 10 §7.6 (Haushalt (1)) no longer a
 * card on the page: the surplus card's line „Davon angelegt“ opens it in the detail panel, whose
 * title is the card's former heading. */
function SavedPanel({ month, transfers }: { month: HouseholdCashflow; transfers: TransferLinkView[] }) {
  const t = useT(household);
  const f = useFormat();
  const [open, setOpen] = useState(false);
  const stayed = fromCents(toCents(month.surplus) - toCents(month.saved));
  const invested = Object.entries(month.invested_by_category ?? {}).filter(([, amount]) => toCents(amount) !== 0n);
  invested.sort(([a, x], [b, y]) => {
    if (a === INVESTMENT_FALLBACK || b === INVESTMENT_FALLBACK) return a === INVESTMENT_FALLBACK ? 1 : -1;
    const diff = toCents(y) - toCents(x);
    return diff > 0n ? 1 : diff < 0n ? -1 : categoryLabel(a).localeCompare(categoryLabel(b));
  });
  const investedTotal = sumMoney(invested.map(([, amount]) => amount));
  // As groupCodes: the row opens when its codes say more than the row does.
  const opens = invested.length > 1 || (invested.length === 1 && invested[0]?.[0] !== INVESTMENT_FALLBACK);
  const rowId = "saved-investment";
  return (
    <div className="stack">
      <p className="muted">{f.month(month.month)}</p>
      <p>
        {t.savedText}
        {toCents(month.saved) > 0n ? t.savedAmount(f.money(month.saved)) : ""}
      </p>
      {invested.length > 0 || transfers.length > 0 ? (
        <ul className="household-rows">
          {invested.length > 0 ? (
            <li>
              {opens ? (
                <button
                  type="button"
                  className="household-toggle"
                  aria-expanded={open}
                  aria-controls={invested.map(([code]) => `${rowId}-${code}`).join(" ")}
                  onClick={() => setOpen((current) => !current)}
                >
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span> {HOUSEHOLD_GROUP.investment}
                </button>
              ) : (
                <span>{HOUSEHOLD_GROUP.investment}</span>
              )}
              <span>{f.money(investedTotal)}</span>
            </li>
          ) : null}
          {opens && open
            ? invested.map(([code, amount]) => (
                <li key={code} id={`${rowId}-${code}`} className="household-subitem">
                  <span>{categoryLabel(code)}</span>
                  <span>{f.money(amount)}</span>
                </li>
              ))
            : null}
          {transfers.map((link) => (
            <li key={link.id}>
              <span>
                {link.out_leg.account_label} → {link.in_leg.account_label}
              </span>
              <span>{link.in_leg.amount != null ? f.money(link.in_leg.amount) : null}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {toCents(month.saved) > 0n ? (
        <p>
          {toCents(stayed) >= 0n
            ? t.stayed(signed(f, month.surplus), f.money(stayed))
            : t.overInvested(f.money(stayed.replace(/^-/, "")))}
        </p>
      ) : null}
    </div>
  );
}
