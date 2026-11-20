// Spending in play and the plan beside the real month (docs/phases/phase-9.md D263, §7.6, §7.7;
// mockups 12d and 10b). The reads of phase 9 (GET /health, /health/months, /household-plan, its
// preview and its comparison) and the scenario writes are mocked, since a workspace with three
// closed months needs a full ledger; every other read is the real API of a fresh workspace.
// Synthetic figures only.
//
// - A typed amount moves „Was eure Änderungen bewirken“ through the preview, while the cards at
//   the top keep the saved figures and nothing is stored (no POST to /household-scenarios).
// - „Szenario speichern“ stores the change, as an amount or a percent, beside the other levers.
// - Haushalt shows „Geplant“ and „Unterschied“ only for a code the saved plan names, „bisher“
//   while the month is open, and the lines that still wait in Zu prüfen.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type Health = components["schemas"]["Health"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type HouseholdMonth = components["schemas"]["HouseholdMonth"];
type HouseholdPlan = components["schemas"]["HouseholdPlan"];
type HouseholdScenario = components["schemas"]["HouseholdScenario"];
type HouseholdScenarioCreate = components["schemas"]["HouseholdScenarioCreate"];
type HouseholdLevers = components["schemas"]["HouseholdLevers"];
type SpendingComparison = components["schemas"]["SpendingComparison"];
type SpendingPlay = components["schemas"]["SpendingPlay"];

// The KI panel beside the plan (step 5) reads its explanation: served here as the app's template
// with no text, so these tests, which are not about it, never read a real explanation of a
// mocked plan (e2e/plan-explanation.spec.ts covers the panel).
test.beforeEach(async ({ page }) => {
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
});

const WORKSPACE = "00000000-0000-4000-8000-000000000a01";

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
    as_of: "2026-11-18",
    window: { months: WINDOW, enough: true },
    savings_rate: {
      saved: "3900.00",
      income: "15600.00",
      one_off_spending: "0.00",
      principal: "0.00",
      rate: "0.2500",
      rate_regular: "0.2500",
      rate_without_principal: "0.2500",
      months: WINDOW.map((month) => ({ month, rate: "0.2500" })),
    },
    reserve: null,
    debt: null,
    marks: {},
    proposals: [],
    todos: [],
  };
}

const LEVERS: HouseholdLevers = {
  reserve_target_months: "6",
  buffer_percent: "10",
  horizon_months: 12,
  net_worth_years: 10,
  value_change: { depot: "0.05", crypto: null },
  spending_changes: [],
};

/** The play: „Essen gehen & bestellen“ with Restaurant (200 €) and its fallback (100 €), and
 * „Lebensmittel und Drogerie“ with Supermarkt; with a change, the rate and „Anlegbar“ of the
 * months it is in force. */
function play(levers: HouseholdLevers): SpendingPlay {
  const change = (levers.spending_changes ?? [])[0];
  const planned = change ? (change.monthly ?? "180.00") : "200.00";
  const delta = change ? (change.monthly ? (Number(change.monthly) - 200).toFixed(2) : "-20.00") : "0.00";
  const regular = (3900 + Number(delta)).toFixed(2);
  const investable = ((5200 - Number(regular)) * 0.9).toFixed(2);
  const rate = ((5200 - Number(regular)) / 5200).toFixed(4);
  return {
    mean_regular_spending: "3900.00",
    rate_regular_now: "0.2500",
    rates: [{ from_month: FIRST, to_month: LAST, rate, regular_spending: regular, investable }],
    addable: ["hh_cafe"],
    groups: [
      {
        group: "dining",
        code: "hh_dining",
        mean: "300.00",
        planned_first: (300 + Number(delta)).toFixed(2),
        change_first: delta,
        rows: [
          {
            code: "hh_restaurant",
            mean: "200.00",
            planned_first: planned,
            change_first: delta,
            playable: true,
            changes: change ? [{ change, planned, delta }] : [],
          },
          { code: "hh_dining", mean: "100.00", planned_first: "100.00", change_first: "0.00", playable: false },
        ],
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

function plan(levers: HouseholdLevers, scenarioId: string | null): HouseholdPlan {
  const p = play(levers);
  const investable = p.rates?.[0]?.investable ?? "1170.00";
  return {
    as_of: "2026-11-18",
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
    months: HORIZON.map((month) => ({ month, flat: "0.00", free: "1300.00", investable, spending_delta: "0.00" })),
    paths: [{ key: "as_is", to_reserve_first_month: investable, reached: LAST, reserve_at_horizon: "20000.00" }],
    projection: { points: [], todos: [] },
    spending_play: p,
    todos: [],
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

/** The scenario history and its writes, and the plan that reads the newest one. */
async function planWithScenarios(page: Page): Promise<{ posted: HouseholdScenarioCreate[]; previews: HouseholdLevers[] }> {
  const posted: HouseholdScenarioCreate[] = [];
  const previews: HouseholdLevers[] = [];
  const rows: HouseholdScenario[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-scenarios",
    async (route: Route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as HouseholdScenarioCreate;
        posted.push(body);
        // The server writes each change's mean_at_save from today's window (§7.6 (2)).
        const levers = body.levers && {
          ...body.levers,
          spending_changes: (body.levers.spending_changes ?? []).map((change) => ({ ...change, mean_at_save: "200.00" })),
        };
        const row: HouseholdScenario = {
          ...body,
          levers,
          id: `00000000-0000-4000-8000-000000000a1${posted.length}`,
          workspace_id: WORKSPACE,
          created_at: "2026-11-18T09:00:00Z",
          created_by: null,
        };
        rows.push(row);
        return route.fulfill({ status: 201, json: row });
      }
      return route.fulfill({ json: rows });
    },
  );
  await serve(page, "/api/household-plan", () => {
    const last = rows[rows.length - 1];
    return plan({ ...LEVERS, ...(last?.levers ?? {}) }, last?.id ?? null);
  });
  await page.route(
    (url) => url.pathname === "/api/household-plan/preview",
    (route: Route) => {
      const body = route.request().postDataJSON() as { levers: HouseholdLevers };
      previews.push(body.levers);
      return route.fulfill({ json: plan(body.levers, null) });
    },
  );
  await serve(page, "/api/health", () => health());
  await serve(page, "/api/health/net-worth-series", () => []);
  await serve(page, "/api/health/months", (url) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return [...WINDOW, monthsBack(1), monthsBack(0)]
      .filter((month) => month >= from && month <= to)
      .map((month) => monthView(month, WINDOW.includes(month)));
  });
  return { posted, previews };
}

test("a typed amount moves the savings rate and „Anlegbar“ through the preview, and nothing is stored until „Szenario speichern“", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted, previews } = await planWithScenarios(page);

  await page.goto("/planning?tab=haushalt#ausgaben-durchspielen");
  const section = page.getByRole("region", { name: "Ausgaben durchspielen" });
  // „Was eure Änderungen bewirken“ (D271) heads the scenario sheet, above the save and the play.
  const rate = page.getByTestId("effect-rate");
  const investable = page.getByTestId("effect-investable");
  await expect(rate).toContainText("25,0 %");
  await expect(investable).toContainText("1.170,00 €");
  // The fields start empty: the mean is shown beside them, never filled in.
  await section.getByRole("button", { name: "Essen gehen & bestellen" }).click();
  const restaurant = section.getByLabel("Geplant pro Monat: Restaurant");
  await expect(restaurant).toHaveValue("");
  await expect(section.getByText("zählt mit der ganzen Gruppe")).toBeVisible();

  await restaurant.fill("100");
  await expect(investable).toContainText("1.260,00 €");
  await expect(rate).toContainText("26,9 %");
  await expect(section.getByText("100,00 € weniger als der Durchschnitt")).toBeVisible();
  // A subcategory planned: the group's own field is closed, so the two never overlap.
  await expect(section.getByLabel("Geplant pro Monat: Essen gehen & bestellen")).toBeDisabled();
  // The cards at the top keep the saved plan, and nothing was stored.
  await expect(page.locator(".planning-kpi-main")).toContainText("1.170 €");
  expect(previews.length).toBeGreaterThan(0);
  expect(posted).toHaveLength(0);

  await page.getByRole("button", { name: "Szenario speichern" }).first().click();
  await expect.poll(() => posted.length).toBe(1);
  const levers = posted[0]?.levers;
  expect(levers?.spending_changes).toEqual([
    { code: "hh_restaurant", monthly: "100.00", percent: null, from_month: FIRST, to_month: null, mean_at_save: null },
  ]);
  // The other levers are kept.
  expect(levers?.value_change?.depot).toBe("0.05");
  expect(levers?.reserve_target_months).toBe("6");
  // The page reads the stored row back: nothing is left unsaved, and the field keeps the amount.
  await expect(page.getByText("Geändert, noch nicht gespeichert")).toHaveCount(0);
  await expect(section.getByLabel("Geplant pro Monat: Restaurant")).toHaveValue("100,00");
  await expect(page.getByRole("button", { name: "Szenario speichern" }).first()).toBeDisabled();
});

test("a change as a percent of the mean, for some months, is saved as a percent", async ({ page, signedIn }) => {
  void signedIn;
  const { posted } = await planWithScenarios(page);

  await page.goto("/planning?tab=haushalt#ausgaben-durchspielen");
  const section = page.getByRole("region", { name: "Ausgaben durchspielen" });
  await section.getByRole("button", { name: "Essen gehen & bestellen" }).click();
  await section.getByLabel("Geplant pro Monat: Restaurant").fill("-10");
  await section.getByRole("group", { name: "Betrag oder Prozent: Restaurant" }).getByRole("button", { name: "%" }).click();
  await section.getByLabel("bis: Restaurant").selectOption(HORIZON[2] ?? "");
  await expect(page.getByTestId("effect-investable")).toContainText("1.188,00 €");
  // An entry the field cannot take keeps the draft's old change, so no save until it is fixed.
  const save = page.getByRole("button", { name: "Szenario speichern" }).first();
  const restaurant = section.getByLabel("Geplant pro Monat: Restaurant");
  await restaurant.fill("-10x");
  await expect(save).toBeDisabled();
  await restaurant.fill("-10");
  await expect(save).toBeEnabled();

  await save.click();
  await expect.poll(() => posted.length).toBe(1);
  const change = posted[0]?.levers?.spending_changes?.[0];
  expect(change?.code).toBe("hh_restaurant");
  expect(change?.monthly ?? null).toBeNull();
  expect(Number(change?.percent)).toBe(-0.1);
  expect(change?.from_month).toBe(FIRST);
  expect(change?.to_month).toBe(HORIZON[2]);
});

test("Haushalt shows „Geplant“ and „Unterschied“ only for the codes the saved plan names", async ({ page, signedIn }) => {
  void signedIn;
  await planWithScenarios(page);
  const month = monthsBack(0);
  const comparison: SpendingComparison = {
    month,
    final: false,
    open_lines: 2,
    scenario_id: "00000000-0000-4000-8000-000000000a11",
    scenario_created_at: "2026-11-18T09:00:00Z",
    rows: [
      {
        code: "hh_restaurant",
        group: "dining",
        planned: "100.00",
        actual: "40.00",
        difference: "-60.00",
        words: "less",
        text: "60,00 € weniger als geplant",
        sources: [],
      },
    ],
  };
  await serve(page, "/api/household-plan/comparison", () => comparison);

  await page.goto(`/household?monat=${month}`);
  const table = page.getByRole("region", { name: "Ausgaben nach Kategorie" });
  await expect(table.getByRole("columnheader", { name: "Geplant" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Unterschied" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: /bisher/ })).toBeVisible();
  // The planned subcategory's group is open, and its row carries the plan and the difference.
  const row = table.getByRole("row", { name: /^Restaurant/ });
  await expect(row).toContainText("100,00 €");
  await expect(row).toContainText("60,00 € weniger als geplant");
  await expect(table.getByText("gespeichert am 18. November 2026")).toBeVisible();
  await expect(table.getByRole("link", { name: "Planung › Haushalt › Ausgaben durchspielen" })).toHaveAttribute(
    "href",
    "/planning?tab=haushalt#ausgaben-durchspielen",
  );
  await expect(table.getByRole("link", { name: "2 Umsätze warten in Zu prüfen" })).toHaveAttribute("href", "/inbox");
});

// --- Planung › Budget: the household's own targets (phase 10 D274) ------------------------------

type BudgetView = components["schemas"]["BudgetView"];
type BudgetTargets = components["schemas"]["BudgetTargets"];

function budget(targets: BudgetTargets): BudgetView {
  const code = (category: "hh_groceries" | "hh_restaurant", budgetClass: "need" | "want", monthly: string) => ({
    category,
    group: category === "hh_groceries" ? ("groceries" as const) : ("dining" as const),
    budget_class: budgetClass,
    default_class: budgetClass,
    overridden: false,
    monthly,
    total: (Number(monthly) * 3).toFixed(2),
  });
  const wants = targets.wants_max ?? null;
  return {
    as_of: "2026-11-18",
    scenario_id: null,
    window: { months: WINDOW, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    lines: [
      { key: "need", monthly: "2600.00", total: "7800.00", share: "0.5000", codes: [code("hh_groceries", "need", "2600.00")] },
      {
        key: "want",
        monthly: "1300.00",
        total: "3900.00",
        share: "0.2500",
        codes: [code("hh_restaurant", "want", "1300.00")],
        target: wants,
        mark: wants
          ? {
              colour: "green",
              target: wants,
              gap_points: "-5.0",
              gap_monthly: "-260.00",
              text: "Wünsche liegen 5 Prozentpunkte unter eurem Ziel, rund 260 € im Monat.",
            }
          : null,
      },
      { key: "one_off", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
      { key: "uncategorized", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
      { key: "saving", monthly: "1300.00", total: "3900.00", share: "0.2500" },
    ],
    housing: null,
    targets,
    references: [],
    todos: [],
  };
}

test("a budget target saved under Budget survives a later „Szenario speichern“ from Reserve, and an unsaved change stays out of it", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted } = await planWithScenarios(page);
  await serve(page, "/api/household-budget", () => budget(posted[posted.length - 1]?.levers?.budget_targets ?? {}));

  // An unsaved spending change in the scenario sheet first.
  await page.goto("/planning?tab=haushalt#ausgaben-durchspielen");
  const section = page.getByRole("region", { name: "Ausgaben durchspielen" });
  await section.getByRole("button", { name: "Essen gehen & bestellen" }).click();
  await section.getByLabel("Geplant pro Monat: Restaurant").fill("150");
  await expect(page.getByText("1 Änderung, nicht gespeichert")).toBeVisible();

  // „Eigene Ziele festlegen“ stores the target alone, on the saved levers.
  await page.getByRole("link", { name: "Eigene Ziele festlegen" }).click();
  await page.getByLabel("Wünsche höchstens").fill("30");
  await page.getByRole("button", { name: "Ziele speichern" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.levers?.budget_targets?.wants_max).toBe("30");
  expect(posted[0]?.levers?.spending_changes ?? []).toEqual([]);
  await expect(page.getByText("Wünsche liegen 5 Prozentpunkte unter eurem Ziel, rund 260 € im Monat.")).toBeVisible();
  await expect(page.getByText("Ziel höchstens 30 %")).toBeVisible();
  // The spending change is still a draft.
  await expect(page.getByText("1 Änderung, nicht gespeichert")).toBeVisible();

  // A lever changed under Reserve and saved there keeps the target and the spending change.
  await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Reserve" }).click();
  await page.getByRole("button", { name: "Reserveziel erhöhen" }).click();
  await expect(page.getByText("2 Änderungen, nicht gespeichert")).toBeVisible();
  await page.getByRole("button", { name: "Szenario speichern" }).first().click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]?.levers?.budget_targets?.wants_max).toBe("30");
  expect(posted[1]?.levers?.reserve_target_months).toBe("6.5");
  expect(posted[1]?.levers?.spending_changes?.map((change) => change.code)).toEqual(["hh_restaurant"]);
  expect(posted[1]?.supersedes_id).toBe("00000000-0000-4000-8000-000000000a11");
  await expect(page.getByText("gespeichert", { exact: true })).toBeVisible();
});
