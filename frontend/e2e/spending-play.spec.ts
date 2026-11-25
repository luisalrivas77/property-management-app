// Spending in play and the plan beside the real month (docs/phases/phase-9.md D263, §7.6, §7.7;
// mockups 12d and 10b). The reads of phase 9 (GET /health, /health/months, /household-plan, its
// preview and its comparison) and the scenario writes are mocked, since a workspace with three
// closed months needs a full ledger; every other read is the real API of a fresh workspace.
// Synthetic figures only.
//
// - A typed amount on Haushalt › Durchspielen moves „Was das bewirkt“ through the preview
//   (phase 10 §7.10 (a), D299: the panel „Annahmen“ is gone), and nothing is stored (no POST to
//   /household-scenarios) until „Als Plan behalten“, which stores the change, as an amount or a
//   percent, beside the other levers; Budget then counts it and names the planned change.
// - The levers beside their figures (§7.10 (a) 7) save on their own and leave Durchspielen's
//   draft and the Budget's targets as they are.
// - Haushalt shows „Geplant“ and „Unterschied“ only for a code the saved plan names, „bisher“
//   while the month is open, and the lines that still wait in Zu prüfen.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, navigateTo, test } from "./support";

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

/** The scenario history and its writes, and the plan that reads the newest one; `initial` is the
 * history before the test. `previewIds` is the scenario id each preview request named. */
async function planWithScenarios(
  page: Page,
  initial: HouseholdScenario[] = [],
): Promise<{
  posted: HouseholdScenarioCreate[];
  previews: HouseholdLevers[];
  previewIds: (string | null)[];
  rows: HouseholdScenario[];
}> {
  const posted: HouseholdScenarioCreate[] = [];
  const previews: HouseholdLevers[] = [];
  const previewIds: (string | null)[] = [];
  const rows: HouseholdScenario[] = [...initial];
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
      const body = route.request().postDataJSON() as { levers: HouseholdLevers; scenario_id?: string | null };
      previews.push(body.levers);
      previewIds.push(body.scenario_id ?? null);
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
  return { posted, previews, previewIds, rows };
}

test("a typed amount moves the savings rate and „Anlegbar“ through the preview, and nothing is stored until „Als Plan behalten“", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted, previews, previewIds } = await planWithScenarios(page);
  await serve(page, "/api/household-budget", () => budget({}));

  // Planung's old link to „Ausgaben durchspielen“ lands on Haushalt › Durchspielen (§7.10 (a)).
  await page.goto("/planning?section=anlegen#ausgaben-durchspielen");
  await expect(page).toHaveURL("/household?tab=durchspielen");
  const investable = page.locator('[data-effect="investable"]');
  const rate = page.locator('[data-effect="rate"]');
  await expect(investable.getByRole("cell").nth(0)).toHaveText("1.170 €");
  // The fields start empty: the mean is shown beside them, never filled in.
  await page.getByRole("button", { name: "Wünsche" }).click();
  const restaurant = page.getByLabel("Geplant pro Monat: Restaurant");
  await expect(restaurant).toHaveValue("");

  await restaurant.fill("100");
  await expect(investable.getByRole("cell").nth(1)).toHaveText("1.260 €");
  await expect(rate.getByRole("cell").nth(1)).toHaveText("26,9 %");
  await expect(page.getByText("100,00 € weniger als der Durchschnitt")).toBeVisible();
  await expect(page.getByText("1 Änderung · noch nicht behalten")).toBeVisible();
  // Today's column and Budget keep the saved plan's figures, and nothing was stored.
  await expect(investable.getByRole("cell").nth(0)).toHaveText("1.170 €");
  expect(previews.length).toBeGreaterThan(0);
  // No default is saved yet, so the preview names no scenario.
  expect(new Set(previewIds)).toEqual(new Set([null]));
  expect(posted).toHaveLength(0);

  await page.getByRole("button", { name: "Als Plan behalten" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const levers = posted[0]?.levers;
  expect(levers?.spending_changes).toEqual([
    { code: "hh_restaurant", monthly: "100.00", percent: null, from_month: FIRST, to_month: null, mean_at_save: null },
  ]);
  // The other levers are kept.
  expect(levers?.value_change?.depot).toBe("0.05");
  expect(levers?.reserve_target_months).toBe("6");
  // Kept: nothing is left in the draft, and today's column is the stored plan.
  await expect(page.getByText("Noch nichts geändert.")).toBeVisible();
  await expect(investable.getByRole("cell").nth(0)).toHaveText("1.260 €");

  // Budget now counts the planned change and says so, with the way back to Durchspielen.
  await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Budget" }).click();
  const rest = page.getByRole("region", { name: "Was mit dem Rest passiert" });
  await expect(rest.getByTestId("rest-with-changes")).toContainText("1.260,00 €");
  const planned = page.getByTestId("planned-changes").first();
  await expect(planned).toContainText("mit 1 geplanten Änderung");
  await planned.getByRole("link", { name: /ansehen/ }).click();
  await expect(page).toHaveURL("/household?tab=durchspielen");
});

test("a change as a percent of the mean, for some months, is saved as a percent", async ({ page, signedIn }) => {
  void signedIn;
  const { posted } = await planWithScenarios(page);
  await serve(page, "/api/household-budget", () => budget({}));

  await page.goto("/planning?tab=haushalt#ausgaben-durchspielen");
  await expect(page).toHaveURL("/household?tab=durchspielen");
  await page.getByRole("button", { name: "Wünsche" }).click();
  await page.getByLabel("Geplant pro Monat: Restaurant").fill("-10");
  await page.getByRole("group", { name: "Betrag oder Prozent: Restaurant" }).getByRole("button", { name: "%" }).click();
  await page.getByLabel("bis: Restaurant").selectOption(HORIZON[2] ?? "");
  await expect(page.locator('[data-effect="investable"]').getByRole("cell").nth(1)).toHaveText("1.188 €");
  // An entry the field cannot take keeps the draft's old change, so no save until it is fixed.
  const save = page.getByRole("button", { name: "Als Plan behalten" });
  const restaurant = page.getByLabel("Geplant pro Monat: Restaurant");
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
  await expect(table.getByRole("link", { name: "Haushalt › Budget" })).toHaveAttribute("href", "/household?tab=budget");
  await expect(table.getByRole("link", { name: "2 Umsätze warten in Zu prüfen" })).toHaveAttribute("href", "/inbox");
  // A tapped group opens the panel (mockup 19): its subcategories, each with its plan.
  await table.getByRole("link", { name: "Essen gehen & bestellen" }).click();
  await expect(page).toHaveURL(/#d-gruppe-dining$/);
  const panel = page.getByRole("complementary", { name: "Essen gehen & bestellen" });
  const restaurant = panel.getByRole("listitem").filter({ hasText: "Restaurant" });
  await expect(restaurant).toContainText("Geplant 100,00 € · 60,00 € weniger als geplant");
  await expect(panel.getByText("Geplant laut eurem Szenario vom 18. November 2026.")).toBeVisible();
  // „Gespart, nicht ausgegeben“ is no card any more (phase 10 §7.6): the surplus card's line
  // „Davon angelegt“ opens it in the panel.
  await expect(page.getByRole("region", { name: "Gespart, nicht ausgegeben" })).toHaveCount(0);
  const surplus = page.getByRole("region", { name: "Überschuss je Monat" });
  await surplus.getByRole("link", { name: /^Davon angelegt: / }).click();
  await expect(page).toHaveURL(/#d-angelegt-/);
  await expect(page.getByRole("complementary", { name: "Gespart, nicht ausgegeben" })).toBeVisible();
});

// --- Planung › Budget: the household's own targets (phase 10 D274) ------------------------------

type BudgetView = components["schemas"]["BudgetView"];
type BudgetTargets = components["schemas"]["BudgetTargets"];

/** The budget with Sonstige Lebensmittel (a need by default) and Restaurant (a want); a saved
 * class move in `overrides` serves the code in its new line, as the server does. */
function budget(targets: BudgetTargets, overrides: Record<string, "need" | "want"> = {}): BudgetView {
  const code = (category: "hh_groceries" | "hh_restaurant", defaultClass: "need" | "want", monthly: string) => ({
    category,
    group: category === "hh_groceries" ? ("groceries" as const) : ("dining" as const),
    budget_class: overrides[category] ?? defaultClass,
    default_class: defaultClass,
    overridden: category in overrides,
    monthly,
    total: (Number(monthly) * 3).toFixed(2),
  });
  const codes = [code("hh_groceries", "need", "2600.00"), code("hh_restaurant", "want", "1300.00")];
  const of = (cls: "need" | "want") => codes.filter((c) => c.budget_class === cls);
  const wants = targets.wants_max ?? null;
  return {
    as_of: "2026-11-18",
    scenario_id: null,
    window: { months: WINDOW, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    lines: [
      { key: "need", monthly: "2600.00", total: "7800.00", share: "0.5000", codes: of("need") },
      {
        key: "want",
        monthly: "1300.00",
        total: "3900.00",
        share: "0.2500",
        codes: of("want"),
        target: wants,
        mark: wants
          ? {
              colour: "green",
              target: wants,
              gap_points: "-5.0",
              gap_monthly: "-260.00",
              at_target: "1560.00",
              text: "Wünsche liegen 5 Prozentpunkte unter eurem Ziel, rund 260 € im Monat.",
            }
          : null,
      },
      { key: "uncategorized", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
      { key: "saving", monthly: "1300.00", total: "3900.00", share: "0.2500" },
    ],
    housing: null,
    targets,
    references: [],
    todos: [],
  };
}

test("a budget target and the reserve target, each saved in its place, keep Durchspielen's draft, and „Als Plan behalten“ keeps both", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted } = await planWithScenarios(page);
  await serve(page, "/api/household-budget", () => budget(posted[posted.length - 1]?.levers?.budget_targets ?? {}));
  const tabs = page.getByRole("navigation", { name: "Bereiche" });

  // An unsaved spending change in Durchspielen first.
  await page.goto("/household?tab=durchspielen");
  await page.getByRole("button", { name: "Wünsche" }).click();
  await page.getByLabel("Geplant pro Monat: Restaurant").fill("150");
  await expect(page.getByText("1 Änderung · noch nicht behalten")).toBeVisible();

  // „Eigene Ziele festlegen“ stores the target alone, on the saved levers.
  await tabs.getByRole("link", { name: "Budget" }).click();
  await page.getByRole("link", { name: "Eigene Ziele festlegen" }).click();
  await page.getByLabel("Wünsche höchstens").fill("30");
  await page.getByRole("button", { name: "Ziele speichern" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.levers?.budget_targets?.wants_max).toBe("30");
  expect(posted[0]?.levers?.spending_changes ?? []).toEqual([]);
  await expect(page.getByText("Wünsche liegen 5 Prozentpunkte unter eurem Ziel, rund 260 € im Monat.")).toBeVisible();
  await expect(page.getByText("Ziel höchstens 30 %")).toBeVisible();

  // The reserve target, changed beside its figure on Haushalt › Reserve (§7.10 (a) 7), stores
  // only itself over the stored default: the target stays and the spending draft stays a draft.
  await tabs.getByRole("link", { name: "Reserve", exact: true }).click();
  await expect(page).toHaveURL("/household?tab=reserve");
  await page.locator('.planning-kpi a[href="#reserve-ziel"]').click();
  const lever = page.getByRole("form", { name: "Reserve-Ziel ändern" });
  await lever.getByRole("button", { name: "Reserveziel erhöhen" }).click();
  await lever.getByRole("button", { name: "Speichern" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]?.levers?.budget_targets?.wants_max).toBe("30");
  expect(posted[1]?.levers?.reserve_target_months).toBe("6.5");
  expect(posted[1]?.levers?.spending_changes ?? []).toEqual([]);
  expect(posted[1]?.supersedes_id).toBe("00000000-0000-4000-8000-000000000a11");
  await expect(lever).toHaveCount(0);
  await expect(page.getByText(/Gespeichert als neue Fassung eures Plans/)).toBeVisible();

  // Back in Durchspielen the draft is still there, and keeping it keeps both saved levers.
  await tabs.getByRole("link", { name: "Durchspielen" }).click();
  await expect(page.getByText("1 Änderung · noch nicht behalten")).toBeVisible();
  await page.getByRole("button", { name: "Als Plan behalten" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]?.levers?.budget_targets?.wants_max).toBe("30");
  expect(posted[2]?.levers?.reserve_target_months).toBe("6.5");
  expect(posted[2]?.levers?.spending_changes?.map((change) => change.code)).toEqual(["hh_restaurant"]);
  expect(posted[2]?.supersedes_id).toBe("00000000-0000-4000-8000-000000000a12");
});

// --- Class moves (D272) and a field left invalid in another section ----------------------------

const SAVED_ID = "00000000-0000-4000-8000-000000000a00";

/** A default saved before the test, with these extra levers. */
function savedDefault(levers: Partial<HouseholdLevers>): HouseholdScenario {
  return {
    name: "Standard",
    is_default: true,
    withdrawn: false,
    supersedes_id: null,
    levers: { ...LEVERS, ...levers },
    id: SAVED_ID,
    workspace_id: WORKSPACE,
    created_at: "2026-11-17T09:00:00Z",
    created_by: null,
  };
}

test("a saved class move set back to its default shows the default pressed, and the save drops the override", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted, previewIds, rows } = await planWithScenarios(page, [savedDefault({ budget_class_overrides: { hh_groceries: "want" } })]);
  await serve(page, "/api/household-budget", () => budget({}, rows[rows.length - 1]?.levers?.budget_class_overrides ?? {}));

  await page.goto("/planning?section=budget");
  await expect(page).toHaveURL("/household?tab=budget");
  await page.getByRole("link", { name: "Wünsche öffnen" }).click();
  const switcher = page.getByRole("group", { name: "Klasse von Sonstige Lebensmittel" });
  await expect(switcher.getByRole("button", { name: "Wünsche" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("verschoben, zählt nach dem Speichern")).toHaveCount(0);

  await switcher.getByRole("button", { name: "Notwendiges" }).click();
  await expect(switcher.getByRole("button", { name: "Notwendiges" })).toHaveAttribute("aria-pressed", "true");
  await expect(switcher.getByRole("button", { name: "Wünsche" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("verschoben, zählt nach dem Speichern")).toBeVisible();
  await expect(page.getByText(/^Geändert, noch nicht gespeichert/)).toBeVisible();
  // The preview names the saved default.
  await expect.poll(() => previewIds.length).toBeGreaterThan(0);
  expect(new Set(previewIds)).toEqual(new Set([SAVED_ID]));

  // The class panel's own „Szenario speichern“ stores the move back (the panel „Annahmen“, which
  // listed it, is gone since §7.10 (a)).
  await page.getByRole("button", { name: "Szenario speichern" }).first().click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.supersedes_id).toBe(SAVED_ID);
  expect(posted[0]?.levers?.budget_class_overrides).toEqual({});
});

test("a new class move is saved as an override", async ({ page, signedIn }) => {
  void signedIn;
  const { posted, rows } = await planWithScenarios(page);
  await serve(page, "/api/household-budget", () => budget({}, rows[rows.length - 1]?.levers?.budget_class_overrides ?? {}));

  await page.goto("/planning?section=budget");
  await expect(page).toHaveURL("/household?tab=budget");
  await page.getByRole("link", { name: "Notwendiges öffnen" }).click();
  const switcher = page.getByRole("group", { name: "Klasse von Sonstige Lebensmittel" });
  await expect(switcher.getByRole("button", { name: "Notwendiges" })).toHaveAttribute("aria-pressed", "true");
  await switcher.getByRole("button", { name: "Wünsche" }).click();
  await expect(switcher.getByRole("button", { name: "Wünsche" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("verschoben, zählt nach dem Speichern")).toBeVisible();

  await page.getByRole("button", { name: "Szenario speichern" }).first().click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.levers?.budget_class_overrides).toEqual({ hh_groceries: "want" });
});

test("a rate left invalid on Anlagen does not block a save from Haushalt › Reserve, which keeps the old rate", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted } = await planWithScenarios(page);
  await serve(page, "/api/household-budget", () => budget({}));

  // Planung › Vermögen's „Annahme“ link opens the rates beside Anlagen's projection (§7.10 (a)).
  // A bookmark of the panel „Annahmen“ (`#d-annahmen-<group>`, before §7.10 (a)) opens the
  // group's new place: the reserve target's form on Reserve.
  await page.goto("/household#d-annahmen-reserve");
  await expect(page).toHaveURL("/household?tab=reserve#reserve-ziel");
  await expect(page.getByRole("form", { name: "Reserve-Ziel ändern" })).toBeVisible();
  // On Anlagen too, from a tab that does not read the plan.
  await page.goto("/assets?tab=etf#d-annahmen-rendite");
  await expect(page).toHaveURL("/assets#rendite");
  await expect(page.getByRole("form", { name: "Rendite ändern" })).toBeVisible();

  await page.goto("/planning?section=vermoegen#annahme");
  await expect(page).toHaveURL("/assets#rendite");
  const rates = page.getByRole("form", { name: "Rendite ändern" });
  await rates.getByLabel("Fonds und Wertpapiere (% pro Jahr)").fill("80");
  await expect(rates.getByText("Zwischen −50 % und 50 % pro Jahr.")).toBeVisible();
  await expect(rates.getByRole("button", { name: "Speichern" })).toBeDisabled();

  // Each lever saves its own keys: the invalid rate left on Anlagen does not reach Reserve's.
  await navigateTo(page, "Haushalt");
  await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Reserve", exact: true }).click();
  await page.locator('.planning-kpi a[href="#reserve-ziel"]').click();
  const lever = page.getByRole("form", { name: "Reserve-Ziel ändern" });
  await lever.getByRole("button", { name: "Reserveziel erhöhen" }).click();
  const save = lever.getByRole("button", { name: "Speichern" });
  await expect(save).toBeEnabled();
  await save.click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.levers?.reserve_target_months).toBe("6.5");
  expect(posted[0]?.levers?.value_change?.depot).toBe("0.05");
});

// --- Phone width, light and dark (phase 10 §7.9 (a)) -------------------------------------------

// One test per colour scheme, so each stays inside the test timeout.
for (const scheme of ["light", "dark"] as const) {
  test(`on the phone (${scheme}), Haushalt › Budget with a planned change, Durchspielen, Reserve, Anlagen and each lever's form scroll no page sideways`, async ({
    page,
    signedIn,
  }) => {
    void signedIn;
    const change = { code: "hh_restaurant" as const, monthly: "100.00", percent: null, from_month: FIRST, to_month: null, mean_at_save: "200.00" };
    await planWithScenarios(page, [savedDefault({ spending_changes: [change] })]);
    await serve(page, "/api/household-budget", () => budget({}));
    await page.setViewportSize({ width: 390, height: 844 });
    const pageWidth = () => page.evaluate(() => document.documentElement.scrollWidth);

    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/household?tab=budget");
    const rest = page.getByRole("region", { name: "Was mit dem Rest passiert" });
    // The saved plan's change shows its stretch of the coming months in the rest card.
    await expect(rest.getByTestId("rest-with-changes")).toContainText("1.260,00 €");
    await expect(page.getByTestId("planned-changes").first()).toContainText("mit 1 geplanten Änderung");
    await rest.getByRole("button", { name: "So entsteht die Zahl" }).click();
    await expect(rest.getByRole("region", { name: "So entsteht die Zahl" })).toBeVisible();
    expect(await pageWidth()).toBeLessThanOrEqual(390);

    // Durchspielen with the saved change's class open: each category is a stacked row on the
    // phone, and the planned Restaurant's field, its unit and its month are inside the screen
    // without a sideways scroll (Luis, 18 Nov 2026).
    await page.goto("/household?tab=durchspielen");
    const wants = page.getByRole("button", { name: "Wünsche" });
    if ((await wants.getAttribute("aria-expanded")) !== "true") await wants.click();
    await expect(wants).toHaveAttribute("aria-expanded", "true");
    for (const field of [
      page.getByLabel("Geplant pro Monat: Restaurant"),
      page.getByRole("group", { name: "Betrag oder Prozent: Restaurant" }),
      page.getByLabel("von: Restaurant"),
    ]) {
      await field.scrollIntoViewIfNeeded();
      const box = await field.boundingBox();
      expect(box).not.toBeNull();
      expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.x ?? 0) + (box?.width ?? 999)).toBeLessThanOrEqual(390);
    }
    expect(await page.evaluate(() => window.scrollX)).toBe(0);
    expect(await pageWidth()).toBeLessThanOrEqual(390);
    await page.screenshot({ path: test.info().outputPath(`durchspielen-planned-${scheme}.png`) });

    await page.goto("/household?tab=reserve");
    await expect(page.getByRole("region", { name: "Wege zum Reserveziel" })).toBeVisible();
    // The saved plan's change is named on Reserve, Ziel and Anlagen too (§7.10 (a) 6).
    await expect(page.getByTestId("planned-changes").first()).toContainText("mit 1 geplanten Änderung");
    expect(await pageWidth()).toBeLessThanOrEqual(390);

    await page.goto("/household?tab=ziel");
    await expect(page.getByTestId("planned-changes").first()).toContainText("mit 1 geplanten Änderung");
    expect(await pageWidth()).toBeLessThanOrEqual(390);

    await page.goto("/assets");
    await expect(page.getByRole("region", { name: "Vermögen in einigen Jahren: eure Annahme" })).toBeVisible();
    await expect(page.getByTestId("planned-changes").first()).toContainText("mit 1 geplanten Änderung");
    expect(await pageWidth()).toBeLessThanOrEqual(390);

    // Each lever's own form, opened by a cold load of its link (§7.10 (a) 7).
    for (const [url, name] of [
      ["/household?tab=reserve#reserve-ziel", "Reserve-Ziel ändern"],
      ["/household?tab=budget#puffer", "Puffer ändern"],
      ["/assets#rendite", "Rendite ändern"],
    ] as const) {
      await page.goto(url);
      const form = page.getByRole("form", { name });
      await expect(form).toBeVisible();
      const box = await form.boundingBox();
      expect(box).not.toBeNull();
      expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.x ?? 0) + (box?.width ?? 999)).toBeLessThanOrEqual(390);
      expect(await page.evaluate(() => window.scrollX)).toBe(0);
      expect(await pageWidth()).toBeLessThanOrEqual(390);
      await page.screenshot({ path: test.info().outputPath(`lever-${url.split("#")[1]}-${scheme}.png`) });
    }
  });
}
