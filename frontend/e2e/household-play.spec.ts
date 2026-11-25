// Haushalt › Durchspielen (phase 10 §7.10 (a), D299; mockup 19e). The reads of the household
// plan (GET /household-plan, /health, /health/months, /household-budget, /household-goal), both
// previews and the scenario writes are mocked, since a workspace with three closed months needs a
// full ledger; every other read is the real API of a fresh workspace. Invented figures only.
//
// - A typed income and a typed category move „Was das bewirkt“ through both previews, the foot
//   counts the changes, and nothing is stored until „Als Plan behalten“, which stores the income
//   as its difference to today's mean and the category as a spending change, beside the other
//   levers.
// - „Für alle übernehmen“ sets one month on every changed row; „Zurücksetzen“ drops the draft
//   and stores nothing.
// - Phone width, light and dark: no sideways scroll; a screenshot of the tab.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { effectPeriods, mergePeriods, NO_PLAY, type PlayChanges } from "../src/components/playDraft";
import { expect, test } from "./support";

type BudgetView = components["schemas"]["BudgetView"];
type Goal = components["schemas"]["Goal"];
type Health = components["schemas"]["Health"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type HouseholdGoal = components["schemas"]["HouseholdGoal"];
type HouseholdLevers = components["schemas"]["HouseholdLevers"];
type HouseholdMonth = components["schemas"]["HouseholdMonth"];
type HouseholdPlan = components["schemas"]["HouseholdPlan"];
type HouseholdScenario = components["schemas"]["HouseholdScenario"];
type HouseholdScenarioCreate = components["schemas"]["HouseholdScenarioCreate"];
type ProjectionPoint = components["schemas"]["ProjectionPoint"];
type SpendingPlay = components["schemas"]["SpendingPlay"];

const WORKSPACE = "00000000-0000-4000-8000-000000000b01";
const SAVED_ID = "00000000-0000-4000-8000-000000000b02";

/** YYYY-MM, `back` months before the current one (negative: after it). */
function monthsBack(back: number): string {
  const now = new Date();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return day.toISOString().slice(0, 7);
}

const WINDOW = [monthsBack(4), monthsBack(3), monthsBack(2)];
const HORIZON = Array.from({ length: 12 }, (_, index) => monthsBack(1 - index));
const FIRST = HORIZON[0] ?? "";
const LAST = HORIZON[HORIZON.length - 1] ?? "";
const GOAL: Goal = { kind: "net_worth", target: "300000.00", due_month: monthsBack(-36), set_on: "2026-11-20" };

const LEVERS: HouseholdLevers = {
  reserve_target_months: "6",
  buffer_percent: "10",
  horizon_months: 12,
  net_worth_years: 10,
  value_change: { depot: "0.05", crypto: null },
  spending_changes: [],
  income_changes: [],
  one_offs: [],
  goal: GOAL,
};

// The plan for some levers: income 5,200 € plus the income changes, spending 3,900 € with
// Restaurant at 200 € unless a change plans it; „Anlegbar“ is 90 % of what is left. With a
// month, only the changes in force in that month count (the stretches of „Was das bewirkt“).
function figures(levers: HouseholdLevers, month?: string) {
  const inForce = (change: { from_month: string; to_month?: string | null }) =>
    month === undefined || (change.from_month <= month && (change.to_month == null || month <= change.to_month));
  const income = 5200 + (levers.income_changes ?? []).filter(inForce).reduce((sum, change) => sum + Number(change.monthly_net), 0);
  const restaurant = (levers.spending_changes ?? []).find((change) => change.code === "hh_restaurant" && inForce(change));
  const regular = 3900 + (restaurant?.monthly ? Number(restaurant.monthly) - 200 : 0);
  const left = income - regular;
  return { income, regular, left, investable: (left * 0.9).toFixed(2), rate: (left / income).toFixed(4) };
}

/** The stretches of the horizon that share their figures, as the server joins them. */
function rates(levers: HouseholdLevers): SpendingPlay["rates"] {
  const out: NonNullable<SpendingPlay["rates"]> = [];
  for (const month of HORIZON) {
    const { regular, investable, rate } = figures(levers, month);
    const prev = out.at(-1);
    if (prev && prev.rate === rate && prev.investable === investable) prev.to_month = month;
    else out.push({ from_month: month, to_month: month, rate, regular_spending: regular.toFixed(2), investable });
  }
  return out;
}

function play(levers: HouseholdLevers): SpendingPlay {
  return {
    mean_regular_spending: "3900.00",
    rate_regular_now: "0.2500",
    rates: rates(levers),
    addable: [],
    groups: [
      {
        group: "dining",
        code: "hh_dining",
        mean: "200.00",
        planned_first: "200.00",
        change_first: "0.00",
        rows: [{ code: "hh_restaurant", mean: "200.00", planned_first: "200.00", change_first: "0.00", playable: true }],
      },
      {
        group: "groceries",
        code: "hh_groceries",
        mean: "650.00",
        planned_first: "650.00",
        change_first: "0.00",
        rows: [{ code: "hh_supermarket", mean: "650.00", planned_first: "650.00", change_first: "0.00", playable: true }],
      },
    ],
  };
}

/** The projection's last point, ten years on: only its total is read by the tab. */
function projectionPoint(total: string): ProjectionPoint {
  return {
    on: `${monthsBack(-120)}-01`,
    bank: "0.00",
    depot: "0.00",
    crypto: "0.00",
    property: "0.00",
    loans: "0.00",
    other_liabilities: "0.00",
    total,
  };
}

function plan(levers: HouseholdLevers, scenarioId: string | null): HouseholdPlan {
  const { left, investable } = figures(levers);
  // The reserve is reached a month earlier for every 100 € more left over, never before FIRST.
  const earlier = Math.max(0, Math.min(11, Math.round((left - 1300) / 100)));
  return {
    as_of: "2026-11-20",
    scenario_id: scenarioId,
    levers,
    newest_closed: WINDOW[WINDOW.length - 1] ?? null,
    surplus: {
      rows: [
        { key: "mean_income", amount: "5200.00" },
        { key: "mean_regular_spending", amount: "-3900.00" },
        { key: "free_cash", amount: "1300.00" },
        { key: "buffer", amount: "130.00" },
        { key: "investable", amount: "1170.00" },
      ],
    },
    months: HORIZON.map((month) => ({ month, flat: "0.00", free: left.toFixed(2), investable, spending_delta: "0.00" })),
    paths: [{ key: "as_is", to_reserve_first_month: investable, reached: HORIZON[11 - earlier] ?? LAST, reserve_at_horizon: "20000.00" }],
    projection: { points: [projectionPoint((200000 + left * 120).toFixed(2))], todos: [] },
    spending_play: play(levers),
    todos: [],
  };
}

function goalFor(levers: HouseholdLevers): HouseholdGoal {
  const { left } = figures(levers);
  const reached = monthsBack(-(30 - Math.round((left - 1300) / 100)));
  return {
    word: "on_track",
    goal: levers.goal ?? GOAL,
    choice: levers.goal_choice ?? null,
    issue: null,
    target: "300000.00",
    value_today: "150000.00",
    percent: "50.0",
    months_left: 36,
    heute: "1300.00",
    noetig: "1200.00",
    projected: "310000.00",
    reached,
    reached_with_budget: levers.goal_choice?.spending ? reached : null,
    used_up: null,
    scenarios: [],
    unreachable: null,
    todos: [],
  };
}

function householdMonth(month: string): HouseholdMonth {
  return {
    month,
    private_income: "5200.00",
    private_spending: "3900.00",
    one_off_spending: "0.00",
    regular_spending: "3900.00",
    rental_result: "0.00",
    rental_cash: "0.00",
    income: "5200.00",
    spending: "3900.00",
    saved: "1300.00",
    principal: "0.00",
    invested_property: "0.00",
    invested: "0.00",
    free_cash: "1300.00",
    savings_rate: "0.2500",
  };
}

function monthView(month: string, closed: boolean): HealthMonthView {
  return { month, household: householdMonth(month), closed: { month, closed, todos: [] } };
}

function health(): Health {
  return {
    as_of: "2026-11-20",
    window: { months: WINDOW, enough: true },
    savings_rate: {
      saved: "3900.00",
      income: "15600.00",
      one_off_spending: "0.00",
      principal: "0.00",
      rate: "0.2500",
      rate_regular: "0.2500",
      rate_without_principal: "0.2500",
      one_offs_left_out: true,
      months: WINDOW.map((month) => ({ month, rate: "0.2500" })),
    },
    reserve: null,
    debt: null,
    marks: {},
    proposals: [],
    todos: [],
  };
}

function budget(): BudgetView {
  const code = (category: "hh_supermarket" | "hh_restaurant", cls: "need" | "want", monthly: string) => ({
    category,
    group: category === "hh_supermarket" ? ("groceries" as const) : ("dining" as const),
    budget_class: cls,
    default_class: cls,
    overridden: false,
    monthly,
    total: (Number(monthly) * 3).toFixed(2),
  });
  return {
    as_of: "2026-11-20",
    scenario_id: null,
    window: { months: WINDOW, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    lines: [
      { key: "need", monthly: "2600.00", total: "7800.00", share: "0.5000", codes: [code("hh_supermarket", "need", "2600.00")] },
      { key: "want", monthly: "1300.00", total: "3900.00", share: "0.2500", codes: [code("hh_restaurant", "want", "1300.00")] },
      { key: "uncategorized", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
      { key: "saving", monthly: "1300.00", total: "3900.00", share: "0.2500" },
    ],
    housing: null,
    targets: {},
    references: [],
    todos: [],
  };
}

function savedDefault(levers: HouseholdLevers): HouseholdScenario {
  return {
    name: "Standard",
    is_default: true,
    withdrawn: false,
    supersedes_id: null,
    levers,
    id: SAVED_ID,
    workspace_id: WORKSPACE,
    created_at: "2026-11-17T09:00:00Z",
    created_by: null,
  };
}

/** Serve JSON for GET requests to exactly this API path; other methods go on to the real API. */
async function serve(page: Page, path: string, body: (url: URL) => unknown): Promise<void> {
  await page.route(
    (url) => url.pathname === path,
    (route: Route) =>
      route.request().method() === "GET" ? route.fulfill({ json: body(new URL(route.request().url())) }) : route.fallback(),
  );
}

/** Every read the tab makes, the two previews and the scenario writes; the saved default holds
 * LEVERS with a goal. */
async function mockPlan(page: Page) {
  const posted: HouseholdScenarioCreate[] = [];
  const planPreviews: HouseholdLevers[] = [];
  const goalPreviews: HouseholdLevers[] = [];
  const rows: HouseholdScenario[] = [savedDefault(LEVERS)];
  await page.route(
    (url) => url.pathname === "/api/household-scenarios",
    async (route: Route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as HouseholdScenarioCreate;
        posted.push(body);
        const row: HouseholdScenario = {
          ...body,
          id: `00000000-0000-4000-8000-000000000b1${posted.length}`,
          workspace_id: WORKSPACE,
          created_at: "2026-11-20T09:00:00Z",
          created_by: null,
        };
        rows.push(row);
        return route.fulfill({ status: 201, json: row });
      }
      return route.fulfill({ json: rows });
    },
  );
  const newest = () => rows[rows.length - 1];
  await serve(page, "/api/household-plan", () => plan({ ...LEVERS, ...(newest()?.levers ?? {}) }, newest()?.id ?? null));
  await serve(page, "/api/household-goal", () => goalFor({ ...LEVERS, ...(newest()?.levers ?? {}) }));
  await page.route(
    (url) => url.pathname === "/api/household-plan/preview",
    (route: Route) => {
      const body = route.request().postDataJSON() as { levers: HouseholdLevers };
      planPreviews.push(body.levers);
      return route.fulfill({ json: plan(body.levers, null) });
    },
  );
  await page.route(
    (url) => url.pathname === "/api/household-goal/preview",
    (route: Route) => {
      const body = route.request().postDataJSON() as { levers: HouseholdLevers };
      goalPreviews.push(body.levers);
      return route.fulfill({ json: goalFor(body.levers) });
    },
  );
  await serve(page, "/api/household-budget", () => budget());
  await serve(page, "/api/health", () => health());
  await serve(page, "/api/health/net-worth-series", () => []);
  await serve(page, "/api/health/months", (url) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return [...WINDOW, monthsBack(1), monthsBack(0)]
      .filter((month) => month >= from && month <= to)
      .map((month) => monthView(month, WINDOW.includes(month)));
  });
  await serve(page, "/api/household-plan/explanation", () => ({
    id: null,
    scenario_id: null,
    mode: "own_use",
    status: "template",
    reason: null,
    paragraphs: [],
    created_at: null,
    stale: false,
  }));
  return { posted, planPreviews, goalPreviews };
}

const effect = (page: Page, key: string) => page.locator(`[data-effect="${key}"]`);

// The stretches „Anlegbar pro Monat“ and „Sparquote“ show (playDraft.ts), as plain functions.
const change = (from: string, to: string | null = null) => ({
  code: "hh_restaurant" as const,
  monthly: "100.00",
  percent: null,
  from_month: from,
  to_month: to,
  mean_at_save: null,
});

test("effectPeriods: a line where a row starts and one after it ends, from the earliest change", () => {
  // Income Jan–Nov 2027 and a spending change from Jan 2027: Jan–Nov, then from Dec.
  const both: PlayChanges = {
    spending: { hh_restaurant: change("2027-01") },
    incomeRow: { monthly: "5500", from: "2027-01", to: "2027-11" },
  };
  expect(effectPeriods(both, "2026-12", "2031-11")).toEqual([
    { from: "2027-01", to: "2027-11" },
    { from: "2027-12", to: null },
  ]);
  // No ends: one open period; nothing before the earliest change.
  expect(effectPeriods({ spending: { hh_restaurant: change("2027-03") } }, "2026-12")).toEqual([{ from: "2027-03", to: null }]);
  // A row that started before the first month counts from it; one that ended before it marks nothing.
  expect(effectPeriods({ spending: { hh_restaurant: change("2026-06") } }, "2026-12")).toEqual([{ from: "2026-12", to: null }]);
  expect(effectPeriods({ spending: { hh_restaurant: change("2026-06", "2026-08") } }, "2026-12")).toEqual([]);
  // The month after an end past the horizon's last month marks nothing.
  expect(effectPeriods({ spending: { hh_restaurant: change("2027-01", "2027-11") } }, "2026-12", "2027-11")).toEqual([
    { from: "2027-01", to: null },
  ]);
  // No rows, or only a dropped saved change: no periods (the card shows one line).
  expect(effectPeriods(NO_PLAY, "2026-12")).toEqual([]);
  expect(effectPeriods({ spending: { hh_restaurant: null } }, "2026-12")).toEqual([]);
  // The server's own stretch starts split a line only from the earliest change on.
  expect(effectPeriods({ spending: { hh_restaurant: change("2027-03") } }, "2026-12", null, ["2026-12", "2027-01", "2027-06"])).toEqual([
    { from: "2027-03", to: "2027-05" },
    { from: "2027-06", to: null },
  ]);
  // Five rows with ends: every start and every month after an end, sorted.
  const many: PlayChanges = {
    spending: {
      a: change("2027-01", "2027-02"),
      b: change("2027-04", "2027-05"),
      c: change("2027-07"),
    },
  };
  expect(effectPeriods(many, "2026-12").map((period) => period.from)).toEqual(["2027-01", "2027-03", "2027-04", "2027-06", "2027-07"]);
});

test("mergePeriods: consecutive equal lines become one, an open end stays open", () => {
  const periods = [
    { from: "2027-01", to: "2027-02" },
    { from: "2027-03", to: "2027-05" },
    { from: "2027-06", to: "2027-06" },
    { from: "2027-07", to: null },
  ];
  const values: Record<string, string> = { "2027-01": "a", "2027-03": "a", "2027-06": "b", "2027-07": "b" };
  expect(mergePeriods(periods, (period) => ({ today: "x", with: values[period.from] }))).toEqual([
    { period: { from: "2027-01", to: "2027-05" }, value: { today: "x", with: "a" } },
    { period: { from: "2027-06", to: null }, value: { today: "x", with: "b" } },
  ]);
});

test("an income change with an end shows its months and the months after it in „Was das bewirkt“", async ({ page, signedIn }) => {
  void signedIn;
  await mockPlan(page);
  await page.goto("/household?tab=durchspielen");
  const end = HORIZON[2] ?? "";
  const after = HORIZON[3] ?? "";
  await page.getByLabel("Netto im Monat: angenommen").fill("5500");
  await page.getByLabel("Netto im Monat: bis").selectOption(end);
  await page.getByRole("button", { name: "Wünsche" }).click();
  await page.getByLabel("Geplant pro Monat: Restaurant").fill("100");

  // FIRST … end: (5,500 − 3,800) × 0.9 = 1,530 €; from the month after: (5,200 − 3,800) × 0.9 = 1,260 €.
  const lines = effect(page, "investable").getByRole("row");
  await expect(lines).toHaveCount(2);
  await expect(lines.nth(0).getByRole("cell")).toHaveText(["1.170 €", "1.530 €"]);
  await expect(lines.nth(1).getByRole("cell")).toHaveText(["1.170 €", "1.260 €"]);
  await expect(lines.nth(0).getByRole("rowheader")).toContainText("Anlegbar pro Monat");
  await expect(lines.nth(0).getByRole("rowheader")).toContainText(" bis ");
  await expect(lines.nth(1).getByRole("rowheader")).toHaveText(new RegExp(`^ab .*${after.slice(0, 4)}$`));
  // The savings rate too: 1,700 ÷ 5,500 = 30.9 %, then 1,400 ÷ 5,200 = 26.9 %.
  const rate = effect(page, "rate").getByRole("row");
  await expect(rate).toHaveCount(2);
  await expect(rate.nth(0).getByRole("cell").nth(1)).toHaveText("30,9 %");
  await expect(rate.nth(1).getByRole("cell").nth(1)).toHaveText("26,9 %");
  // The other rows keep one line.
  await expect(effect(page, "reserve").getByRole("row")).toHaveCount(1);
});

test("a typed income and category move „Was das bewirkt“, and only „Als Plan behalten“ stores them", async ({ page, signedIn }) => {
  void signedIn;
  const { posted, planPreviews, goalPreviews } = await mockPlan(page);

  await page.goto("/household?tab=durchspielen");
  const tabs = page.getByRole("navigation", { name: "Bereiche" });
  await expect(tabs.getByRole("link", { name: "Durchspielen" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "Was wäre, wenn …?" })).toBeVisible();
  await expect(page.getByText("Noch nichts geändert.")).toBeVisible();
  // Today's figures before anything is typed: both columns the same.
  await expect(effect(page, "investable")).toContainText("1.170 €");
  await expect(effect(page, "goal")).toBeVisible();

  // The income row: an absolute amount against today's mean.
  await page.getByLabel("Netto im Monat: angenommen").fill("5500");
  // Wünsche opens into its categories; Restaurant to 100 €.
  await page.getByRole("button", { name: "Wünsche" }).click();
  await page.getByLabel("Geplant pro Monat: Restaurant").fill("100");

  // (5,500 − 3,800) × 0.9 = 1,530 € with the changes; today's column keeps 1,170 €.
  await expect(effect(page, "investable").getByRole("cell").nth(1)).toHaveText("1.530 €");
  await expect(effect(page, "investable").getByRole("cell").nth(0)).toHaveText("1.170 €");
  await expect(page.getByText("2 Änderungen · noch nicht behalten")).toBeVisible();
  // The goal is asked for the same levers, its spending counted as the household's own choice.
  await expect.poll(() => goalPreviews.length).toBeGreaterThan(0);
  expect(goalPreviews.at(-1)?.goal_choice?.spending).toBe("own");
  expect(planPreviews.at(-1)?.income_changes).toEqual([{ monthly_net: "300.00", from_month: FIRST, to_month: null, note_code: "other" }]);
  expect(posted).toHaveLength(0);

  // Another tab does not move: Budget's „Anlegbar“ is still the saved plan's.
  await tabs.getByRole("link", { name: "Budget" }).click();
  await expect(page.getByRole("region", { name: "Was übrig bleibt · im Monat" }).getByTestId("rest-left")).toContainText("1.170 €");
  // The draft is still there on return: it lives in the provider the Haushalt tabs share.
  await tabs.getByRole("link", { name: "Durchspielen" }).click();
  await expect(page.getByText("2 Änderungen · noch nicht behalten")).toBeVisible();
  await expect(page.getByLabel("Netto im Monat: angenommen")).toHaveValue("5500,00");

  await page.getByRole("button", { name: "Als Plan behalten" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const levers = posted[0]?.levers;
  expect(levers?.income_changes).toEqual([{ monthly_net: "300.00", from_month: FIRST, to_month: null, note_code: "other" }]);
  expect(levers?.spending_changes).toEqual([
    { code: "hh_restaurant", monthly: "100.00", percent: null, from_month: FIRST, to_month: null, mean_at_save: null },
  ]);
  // The other levers are kept as stored.
  expect(levers?.value_change?.depot).toBe("0.05");
  expect(levers?.reserve_target_months).toBe("6");
  expect(levers?.goal).toEqual(GOAL);
  // Kept: nothing is left in the draft, and today's column is the new plan.
  await expect(page.getByText("Noch nichts geändert.")).toBeVisible();
  await expect(effect(page, "investable").getByRole("cell").nth(0)).toHaveText("1.530 €");
});

test("„Für alle übernehmen“ sets one month on every changed row, and „Zurücksetzen“ stores nothing", async ({ page, signedIn }) => {
  void signedIn;
  const { posted, planPreviews } = await mockPlan(page);
  await page.goto("/household?tab=durchspielen");
  await page.getByLabel("Netto im Monat: angenommen").fill("5400");
  await page.getByRole("button", { name: "Wünsche" }).click();
  await page.getByLabel("Geplant pro Monat: Restaurant").fill("150");
  await expect(page.getByText("2 Änderungen · noch nicht behalten")).toBeVisible();
  // A one-off keeps its own month: „Für alle übernehmen“ moves only the changed rows.
  const oneOffMonth = HORIZON[1] ?? "";
  await page.getByRole("button", { name: "Betrag eintragen" }).click();
  const oneOff = page.getByRole("form", { name: "Einmaliger Betrag" });
  await oneOff.getByLabel("Monat").selectOption(oneOffMonth);
  await oneOff.getByLabel("Betrag (+ Einnahme, − Ausgabe)").fill("-2400");
  await oneOff.getByRole("button", { name: "Hinzufügen" }).click();
  await expect(page.getByText("3 Änderungen · noch nicht behalten")).toBeVisible();

  const month = HORIZON[3] ?? "";
  await page.getByLabel("Ab wann gilt das?").selectOption(month);
  await page.getByRole("button", { name: "Für alle übernehmen" }).click();
  await expect(page.getByLabel("Netto im Monat: ab")).toHaveValue(month);
  await expect.poll(() => planPreviews.at(-1)?.income_changes?.[0]?.from_month).toBe(month);
  expect(planPreviews.at(-1)?.spending_changes?.[0]?.from_month).toBe(month);
  expect(planPreviews.at(-1)?.one_offs?.[0]?.month).toBe(oneOffMonth);
  // The effect rows read the month the changes start.
  await expect(effect(page, "investable")).toContainText("ab");

  // A row typed back to today's figure is no change (§7.10 (a) 3).
  await page.getByLabel("Geplant pro Monat: Restaurant").fill("200");
  await expect(page.getByText("2 Änderungen · noch nicht behalten")).toBeVisible();
  await page.getByLabel("Netto im Monat: angenommen").fill("5200");
  await expect(page.getByText("1 Änderung · noch nicht behalten")).toBeVisible();
  await expect.poll(() => planPreviews.at(-1)?.spending_changes ?? []).toEqual([]);
  expect(planPreviews.at(-1)?.income_changes ?? []).toEqual([]);

  await page.getByRole("button", { name: "Zurücksetzen" }).click();
  await expect(page.getByText("Noch nichts geändert.")).toBeVisible();
  await expect(page.getByLabel("Netto im Monat: angenommen")).toHaveValue("");
  expect(posted).toHaveLength(0);
});

test("on the phone, Durchspielen scrolls no page sideways, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  await mockPlan(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const pageWidth = () => page.evaluate(() => document.documentElement.scrollWidth);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/household?tab=durchspielen");
    await page.getByLabel("Netto im Monat: angenommen").fill("5500");
    await page.getByRole("button", { name: "Wünsche" }).click();
    await page.getByLabel("Geplant pro Monat: Restaurant").fill("100");
    await expect(effect(page, "investable").getByRole("cell").nth(1)).toHaveText("1.530 €");
    for (const field of [page.getByLabel("Netto im Monat: angenommen"), page.getByLabel("Geplant pro Monat: Restaurant")]) {
      await field.scrollIntoViewIfNeeded();
      const box = await field.boundingBox();
      expect(box).not.toBeNull();
      expect((box?.x ?? 0) + (box?.width ?? 999)).toBeLessThanOrEqual(390);
    }
    expect(await page.evaluate(() => window.scrollX)).toBe(0);
    expect(await pageWidth()).toBeLessThanOrEqual(390);
    await page.screenshot({ path: test.info().outputPath(`durchspielen-${scheme}.png`), fullPage: true });
  }
  // The computer width, once, for the record.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/household?tab=durchspielen");
  await expect(effect(page, "investable")).toBeVisible();
  expect(await pageWidth()).toBeLessThanOrEqual(1280);
  await page.screenshot({ path: test.info().outputPath("durchspielen-desktop.png"), fullPage: true });
});
