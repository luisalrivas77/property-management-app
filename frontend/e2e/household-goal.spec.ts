// The household goal on Übersicht and Haushalt › Ziel (phase 10 §7.9 (d), D289, D298; mockups
// 19b, 19c; since §7.11 (h) the cards of mockup 19f, the spending scenarios in the side panel of
// „Nur weniger ausgeben“). GET /household-goal and the household scenarios (GET and POST
// /household-scenarios) are mocked: a goal's word needs months of bookings and the projection,
// and the writes are checked by what the page posts. Every other read is the real API of a fresh
// workspace. Invented figures only.
//
// - No goal: Übersicht's tile „Ziel festlegen“ leads to the form on Haushalt › Ziel; saving posts
//   the goal on the default scenario, and the word shows on the tab and on Übersicht.
// - A goal the projection cannot reach yet (no_projection): a plain sentence, not an error, and its
//   to-do leads to Anlagen › Immobilie (the server's href `/planning`, D271).
// - A spending scenario adopted as the budget: the in-page confirmation, then its spending
//   changes and the choice posted with the goal unchanged.
// - Phone width, light and dark: no sideways scroll; a screenshot of the tab, the form and the
//   tile.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { planTodoTarget, propertyPlanningHref } from "../src/lib/planningRoutes";
import { expect, test } from "./support";

type HouseholdGoal = components["schemas"]["HouseholdGoal"];
type Goal = components["schemas"]["Goal"];
type GoalScenario = components["schemas"]["GoalScenario"];
type HouseholdScenario = components["schemas"]["HouseholdScenario"];
type HouseholdLevers = components["schemas"]["HouseholdLevers"];

const WORKSPACE = "00000000-0000-4000-8000-00000000a001";

// Local dates, as the page's own todayIso() reads them (the browser runs in the test's time
// zone), so the spec agrees with the page at midnight and at a month's end.
const pad = (n: number) => String(n).padStart(2, "0");

/** YYYY-MM, `ahead` months after the current one. */
function monthsAhead(ahead: number): string {
  const now = new Date();
  const day = new Date(now.getFullYear(), now.getMonth() + ahead, 1);
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}`;
}

const NOW = new Date();
const TODAY = `${NOW.getFullYear()}-${pad(NOW.getMonth() + 1)}-${pad(NOW.getDate())}`;

function emptyGoal(): HouseholdGoal {
  return { word: "none", goal: null, choice: null, issue: null, scenarios: [], unreachable: null, todos: [] };
}

function onTrack(goal: Goal): HouseholdGoal {
  return {
    word: "on_track",
    goal,
    choice: null,
    issue: null,
    target: "500000.00",
    value_today: "125000.00",
    percent: "25.0",
    months_left: 24,
    heute: "1800.00",
    noetig: "1500.00",
    projected: "520000.00",
    reached: monthsAhead(22),
    reached_with_budget: null,
    used_up: null,
    scenarios: [],
    unreachable: null,
    todos: [],
  };
}

const SCENARIOS: GoalScenario[] = [
  {
    kind: "wants_only",
    closes: true,
    cut: "320.00",
    rest: "0.00",
    saving: "2120.00",
    budgets: [
      { code: "hh_restaurant", budget_class: "want", mean: "400.00", budget: "180.00" },
      { code: "hh_streaming", budget_class: "want", mean: "90.00", budget: "60.00" },
    ],
    spending_changes: [
      { code: "hh_restaurant", monthly: "180.00", percent: null, from_month: monthsAhead(1), to_month: monthsAhead(24) },
      { code: "hh_streaming", monthly: "60.00", percent: null, from_month: monthsAhead(1), to_month: monthsAhead(24) },
    ],
  },
  {
    kind: "all_equal",
    closes: false,
    cut: "200.00",
    rest: "120.00",
    saving: "2000.00",
    budgets: [{ code: "hh_supermarket", budget_class: "need", mean: "600.00", budget: "540.00" }],
    spending_changes: [{ code: "hh_supermarket", monthly: "540.00", percent: null, from_month: monthsAhead(1), to_month: monthsAhead(24) }],
  },
];

function feasible(goal: Goal, choice: HouseholdGoal["choice"]): HouseholdGoal {
  return {
    ...onTrack(goal),
    word: "feasible",
    heute: "1100.00",
    noetig: "1400.00",
    reached: monthsAhead(30),
    choice,
    reached_with_budget: choice?.spending ? monthsAhead(23) : null,
    scenarios: SCENARIOS,
  };
}

function scenario(levers: HouseholdLevers, id: string, supersedes: string | null): HouseholdScenario {
  return {
    id,
    workspace_id: WORKSPACE,
    name: "Standard",
    is_default: true,
    levers,
    supersedes_id: supersedes,
    withdrawn: false,
    created_at: `${TODAY}T09:00:00Z`,
    created_by: null,
  };
}

interface Posted {
  name: string;
  is_default: boolean;
  levers: HouseholdLevers;
  supersedes_id: string | null;
  withdrawn: boolean;
}

/** GET /household-goal from `state`, and the scenarios: GET from the history, POST into it,
 * `onPost` turning a write into the goal the server would then answer. */
async function goalApi(page: Page, initial: { goal: HouseholdGoal; history?: HouseholdScenario[] }, onPost: (body: Posted) => HouseholdGoal) {
  const state = { goal: initial.goal, history: [...(initial.history ?? [])] };
  const posted: Posted[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-goal",
    (route: Route) => (route.request().method() === "GET" ? route.fulfill({ json: state.goal }) : route.fallback()),
  );
  await page.route(
    (url) => url.pathname === "/api/household-scenarios",
    (route: Route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ json: state.history });
      if (method !== "POST") return route.fallback();
      const body = route.request().postDataJSON() as Posted;
      posted.push(body);
      const row = scenario(body.levers, `00000000-0000-4000-8000-0000000b00${posted.length}0`, body.supersedes_id);
      state.history = [...state.history.map((s) => (s.id === body.supersedes_id ? { ...s, is_default: false } : s)), row];
      state.goal = onPost(body);
      return route.fulfill({ status: 201, json: row });
    },
  );
  return { posted, state };
}

/** A detail panel: the column beside the list on the computer, the sheet on the phone. */
function detail(page: Page, name: string) {
  return page.getByRole("complementary", { name }).or(page.getByRole("dialog", { name }));
}

async function phoneScreens(page: Page, name: string, open: () => Promise<void>) {
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("about:blank");
    await open();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await page.screenshot({ path: test.info().outputPath(`${name}-${scheme}.png`) });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
}

test("a plan to-do's old /planning href leads to Anlagen › Immobilie", () => {
  expect(planTodoTarget("/planning", "no_default_scenario", "/household?tab=ziel")).toBe("/assets?tab=immobilie");
  expect(propertyPlanningHref()).toBe("/assets?tab=immobilie");
  // A to-do that names no Planung place keeps its own href.
  expect(planTodoTarget("/household?tab=reserve", "reserve_missing", "/household?tab=ziel")).toBe("/household?tab=reserve");
});

test("no goal: Übersicht leads to the form, saving the goal shows its word there and on the tab", async ({ page, signedIn }) => {
  void signedIn;
  const due = monthsAhead(24);
  const { posted } = await goalApi(page, { goal: emptyGoal() }, (body) => onTrack(body.levers.goal as Goal));

  await page.goto("/");
  const tiles = page.getByRole("region", { name: "Auf einen Blick" });
  const empty = tiles.getByRole("link", { name: /Euer Ziel/ });
  await expect(empty).toContainText("Ziel festlegen");
  await expect(empty).toHaveAttribute("href", "/household?tab=ziel#d-ziel-festlegen");
  await empty.click();

  const form = detail(page, "Ziel festlegen");
  await expect(form).toBeVisible();
  // Nothing is proposed: the amount and the month start empty.
  await expect(form.getByLabel("Betrag", { exact: true })).toHaveValue("");
  await expect(form.getByLabel("Bis", { exact: true })).toHaveValue("");
  await expect(form.getByRole("radio", { name: /^Vermögen/ })).toBeChecked();

  // A month that is not after the current one is refused in the page, nothing posted.
  // The hint's own example, German thousands separator included.
  await form.getByLabel("Betrag", { exact: true }).fill("500.000");
  await form.getByLabel("Bis", { exact: true }).fill(monthsAhead(0));
  await form.getByRole("button", { name: "Ziel speichern" }).click();
  await expect(form).toContainText("Bitte einen Monat nach dem laufenden wählen.");
  expect(posted).toHaveLength(0);

  await form.getByLabel("Bis", { exact: true }).fill(due);
  await form.getByRole("button", { name: "Ziel speichern" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const body = posted[0]!;
  expect(body.is_default).toBe(true);
  expect(body.supersedes_id).toBeNull();
  expect(body.levers.goal?.kind).toBe("net_worth");
  expect(body.levers.goal?.target).toBe("500000.00");
  expect(body.levers.goal?.due_month).toBe(due);
  expect(body.levers.goal?.set_on).toBe(TODAY);
  expect(body.levers.goal_choice ?? null).toBeNull();

  // The panel closes; the tab shows the word and the two monthly figures.
  await expect(form).toHaveCount(0);
  const card = page.getByRole("region", { name: "Euer Ziel" });
  await expect(card).toContainText("Auf Kurs");
  await expect(card).toContainText("Ihr seid auf Kurs");
  await expect(card).toContainText("am Ziel");
  await expect(card.getByRole("link", { name: "Ziel ändern" })).toBeVisible();

  await page.goto("/");
  const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /Euer Ziel/ });
  await expect(tile).toContainText("Auf Kurs");
  await expect(tile.locator(".goal-word")).toHaveAttribute("data-word", "on_track");
  await tile.click();
  const panel = detail(page, "Euer Ziel");
  await expect(panel).toContainText("Auf Kurs");
  await expect(panel.getByRole("link", { name: "Zum Ziel" })).toHaveAttribute("href", "/household?tab=ziel");
});

test("a goal the projection cannot reach yet says why, and its to-do leads to Anlagen › Immobilie", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(36), set_on: TODAY };
  const waiting: HouseholdGoal = {
    word: null,
    goal,
    choice: null,
    issue: "no_projection",
    target: "500000.00",
    value_today: "125000.00",
    percent: "25.0",
    months_left: 36,
    scenarios: [],
    unreachable: null,
    todos: [
      {
        code: "no_default_scenario",
        text: "Für die Wohnung ist noch kein Szenario gespeichert.",
        action: "Szenario anlegen",
        href: "/planning",
      },
    ],
  };
  await goalApi(page, { goal: waiting }, () => waiting);

  await page.goto("/household?tab=ziel");
  const card = page.getByRole("region", { name: "Euer Ziel" });
  await expect(card).toContainText("Noch nicht berechnet");
  await expect(card).toContainText("sobald es die kommenden Monate rechnen kann");
  await expect(card).toContainText("Für die Wohnung ist noch kein Szenario gespeichert.");
  await expect(card.getByRole("link", { name: "Szenario anlegen" })).toHaveAttribute("href", "/assets?tab=immobilie");
  // Useful, never an error.
  await expect(card.locator(".notice-error")).toHaveCount(0);

  await page.goto("/");
  const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /Euer Ziel/ });
  await expect(tile).toContainText("Noch nicht berechnet");
  await expect(tile.locator(".goal-word")).toHaveCount(0);
  await tile.click();
  const panel = detail(page, "Euer Ziel");
  await expect(panel.getByRole("link", { name: "Szenario anlegen" })).toHaveAttribute("href", "/assets?tab=immobilie");
});

test("a spending scenario is adopted as the budget after the in-page confirmation", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "saved_sum", target: "60000.00", due_month: monthsAhead(24), set_on: TODAY };
  const levers: HouseholdLevers = {
    reserve_target_months: "6",
    buffer_percent: "10",
    horizon_months: 60,
    net_worth_years: 10,
    goal,
    goal_choice: null,
    spending_changes: [],
  };
  const saved = scenario(levers,"00000000-0000-4000-8000-0000000b0001", null);
  const { posted } = await goalApi(page, { goal: feasible(goal, null), history: [saved] }, (body) =>
    feasible(body.levers.goal as Goal, body.levers.goal_choice ?? null),
  );

  await page.goto("/household?tab=ziel");
  const card = page.getByRole("region", { name: "Euer Ziel" });
  await expect(card).toContainText("Machbar");
  await card.getByRole("link", { name: /^Nur weniger ausgeben/ }).click();
  const scenarios = detail(page, "Nur weniger ausgeben");
  await expect(scenarios.getByRole("link", { name: /^Nur Wünsche kürzen/ })).toContainText("schließt die Lücke");
  await expect(scenarios.getByRole("link", { name: /^Alles gleich kürzen/ })).toContainText("reicht nicht");

  await scenarios.getByRole("link", { name: /^Nur Wünsche kürzen/ }).click();
  const panel = detail(page, "Nur Wünsche kürzen");
  await expect(panel).toContainText("Restaurant");
  await panel.getByRole("button", { name: "Als Budget übernehmen", exact: true }).click();
  // Nothing is written before the confirmation.
  await expect(panel).toContainText("Als Budget übernehmen?");
  expect(posted).toHaveLength(0);
  await panel.getByRole("button", { name: "Ja, übernehmen" }).click();

  await expect.poll(() => posted.length).toBe(1);
  const body = posted[0]!;
  expect(body.supersedes_id).toBe(saved.id);
  expect(body.levers.goal).toEqual(goal);
  expect(body.levers.spending_changes).toEqual(SCENARIOS[0]!.spending_changes);
  expect(body.levers.goal_choice?.spending).toBe("wants_only");
  expect(body.levers.goal_choice?.spending_chosen_on).toBe(TODAY);

  await expect(panel).toHaveCount(0);
  await expect(card).toContainText("Mit eurem Budget „Nur Wünsche kürzen“ erreicht ihr es");
  await card.getByRole("link", { name: /^Nur weniger ausgeben/ }).click();
  await expect(scenarios.getByRole("link", { name: /^Nur Wünsche kürzen/ })).toContainText("Euer Budget");
});

test("the goal at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(24), set_on: TODAY };
  await goalApi(page, { goal: feasible(goal, null) }, () => feasible(goal, null));
  await phoneScreens(page, "goal-tab", async () => {
    await page.goto("/household?tab=ziel");
    await expect(page.getByRole("region", { name: "Euer Ziel" })).toContainText("Andere Wege");
  });
  await phoneScreens(page, "goal-form", async () => {
    await page.goto("/household?tab=ziel#d-ziel-festlegen");
    await expect(detail(page, "Ziel ändern")).toBeVisible();
  });
  await phoneScreens(page, "goal-scenario", async () => {
    await page.goto("/household?tab=ziel#d-ziel-wants_only");
    await expect(detail(page, "Nur Wünsche kürzen")).toBeVisible();
  });
  await phoneScreens(page, "goal-tile", async () => {
    await page.goto("/");
    await expect(page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /Euer Ziel/ })).toContainText("Machbar");
  });
});

test("on the computer the goal sits under Finanzielle Gesundheit, without a sideways scroll", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(24), set_on: TODAY };
  await goalApi(page, { goal: onTrack(goal) }, () => onTrack(goal));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  const tiles = page.getByRole("region", { name: "Auf einen Blick" });
  await expect(tiles.getByRole("link", { name: /Euer Ziel/ })).toContainText("Auf Kurs");
  // The card (19f panel 1: the summary link and „Zum Ziel ›“ inside it).
  const goalTile = tiles.locator(".tile-goal");
  const health = await tiles.getByRole("link", { name: /Finanzielle Gesundheit/ }).boundingBox();
  const own = await goalTile.boundingBox();
  expect(health).not.toBeNull();
  expect(own).not.toBeNull();
  // The same column and width as the health word, below it.
  expect(Math.abs(own!.x - health!.x)).toBeLessThan(2);
  expect(Math.abs(own!.width - health!.width)).toBeLessThan(2);
  expect(own!.y).toBeGreaterThan(health!.y + health!.height - 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});
