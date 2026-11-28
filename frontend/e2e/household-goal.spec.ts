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
// - „Diesem Weg folgen“ (§7.9 (e) 3): the route's return saved with its day, every other lever
//   kept, and removed again; none without a return to follow or in `generic` mode. Anlagen's
//   card „Für euer Ziel“ with the server's word (GET /household-goal/tracking mocked) and its
//   side panel, at phone width in both languages and themes.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { planTodoTarget, propertyPlanningHref } from "../src/lib/planningRoutes";
import { expect, test } from "./support";

type HouseholdGoal = components["schemas"]["HouseholdGoal"];
type Goal = components["schemas"]["Goal"];
type GoalScenario = components["schemas"]["GoalScenario"];
type HouseholdScenario = components["schemas"]["HouseholdScenario"];
type HouseholdLevers = components["schemas"]["HouseholdLevers"];
type GoalTracking = components["schemas"]["GoalTracking"];

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
  // On track, what keeps it there in one line (Luis, 26 Nov 2026: „it doesn't say what the plan is“).
  await expect(card).toContainText(/Weiter 1\.800\s€ im Monat sparen und anlegen/);
  await expect(card.getByRole("link", { name: "Ziel ändern" })).toBeVisible();

  await page.goto("/");
  const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /Euer Ziel/ });
  await expect(tile).toContainText("Auf Kurs");
  await expect(tile.locator(".goal-word")).toHaveAttribute("data-word", "on_track");
  await expect(tile).toContainText(/Weiter 1\.800\s€ im Monat sparen und anlegen/);
  // Straight to Haushalt › Ziel, no side panel (§7.13 (d) 6, D316).
  await expect(tile).toHaveAttribute("href", "/household?tab=ziel");
  await tile.click();
  await expect(page).toHaveURL(/\/household\?tab=ziel$/);
  await expect(page.getByRole("region", { name: "Euer Ziel" })).toContainText("Auf Kurs");
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
  await expect(tile).toHaveAttribute("href", "/household?tab=ziel");
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
  // With a panel open on the phone the page behind it keeps the full width: the two-column
  // grid is for the computer only (it had squeezed the page into the phone's left half).
  const main = await page.locator(".detail-main").first().boundingBox();
  expect(main?.width ?? 0).toBeGreaterThan(390 - 2 * 40);
  await phoneScreens(page, "goal-tile", async () => {
    await page.goto("/");
    await expect(page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /Euer Ziel/ })).toContainText("Machbar");
  });
});

test("on track, the goal tile at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(24), set_on: TODAY };
  await goalApi(page, { goal: onTrack(goal) }, () => onTrack(goal));
  await phoneScreens(page, "goal-tile-on-track", async () => {
    await page.goto("/");
    const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /Euer Ziel/ });
    await expect(tile).toContainText(/Weiter 1\.800\s€ im Monat sparen und anlegen/);
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
  // The card (19f panel 1: the link to Haushalt › Ziel and „Euer Plan ›“ beside it).
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

// „Diesem Weg folgen“ and „Für euer Ziel“ (§7.9 (e) 3, D298 (5)–(7)): following a route's return
// writes goal_choice.investment with its day; Anlagen shows one card with the server's word.
// GET /household-goal/tracking is mocked: its word needs months of depot movements and prices.

function withRoutes(goal: Goal, choice: HouseholdGoal["choice"]): HouseholdGoal {
  return {
    ...feasible(goal, choice),
    balanced: {
      cut: "150.00",
      saving_extra: "200.00",
      moved_to_saving: "0.00",
      saving: "1500.00",
      closes: true,
      return_rate: "0.07",
      reached: monthsAhead(24),
    },
    more_return: {
      available: true,
      needed_return: "0.061",
      window_years: 2,
      windows: 4,
      reached_windows: 3,
      median: "0.07",
      best: "0.12",
      verdict: "within_history",
      thin: false,
    },
  };
}

function tracking(over: Partial<GoalTracking> = {}): GoalTracking {
  return {
    shown: true,
    reason: null,
    route: "more_return",
    chosen_on: "2026-01-15",
    days: 120,
    rate: "0.061",
    planned_per_year: "6000.00",
    planned_by_now: "1972.60",
    paid_in: "2400.00",
    value_then: "20000.00",
    value_now: "23000.00",
    value_change: "600.00",
    implied_change: "410.00",
    yearly_return: null,
    no_rate: "short_history",
    word: "on_path",
    short: [],
    reserve_depots: 0,
    ...over,
  };
}

async function trackingApi(page: Page, body: GoalTracking) {
  await page.route(
    (url) => url.pathname === "/api/household-goal/tracking",
    (route: Route) => route.fulfill({ json: body }),
  );
}

const leversWith = (goal: Goal): HouseholdLevers => ({
  reserve_target_months: "6",
  buffer_percent: "10",
  horizon_months: 60,
  net_worth_years: 10,
  goal,
  goal_choice: null,
  spending_changes: [],
});

test("„Diesem Weg folgen“ saves the route's return with its day, and „Nicht mehr folgen“ removes it", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(24), set_on: TODAY };
  const saved = scenario(leversWith(goal), "00000000-0000-4000-8000-0000000b0001", null);
  const { posted } = await goalApi(page, { goal: withRoutes(goal, null), history: [saved] }, (body) =>
    withRoutes(body.levers.goal as Goal, body.levers.goal_choice ?? null),
  );

  await page.goto("/household?tab=ziel#d-ziel-rendite");
  const panel = detail(page, "Nur mehr Rendite");
  const follow = panel.getByTestId("goal-follow-more_return");
  await expect(follow).toContainText("Diesem Weg folgen");
  // The panel no longer says „Nichts wird gespeichert“: following a route is the one write.
  await expect(panel).toContainText("Gespeichert wird nur, welchem Weg ihr folgt.");
  await expect(panel).not.toContainText("Nichts wird gespeichert");
  await follow.getByRole("button", { name: "Diesem Weg folgen" }).click();

  await expect.poll(() => posted.length).toBe(1);
  const first = posted[0]!;
  expect(first.supersedes_id).toBe(saved.id);
  // Every other lever stays as it was.
  expect(first.levers.goal).toEqual(goal);
  expect(first.levers.spending_changes).toEqual([]);
  expect(first.levers.goal_choice?.investment).toBe("more_return");
  expect(first.levers.goal_choice?.investment_chosen_on).toBe(TODAY);
  expect(first.levers.goal_choice?.spending ?? null).toBeNull();

  await expect(follow).toContainText("ihr folgt ihm");
  await expect(follow.getByRole("link", { name: /Auf Anlagen ansehen/ })).toHaveAttribute("href", "/assets");

  // The balanced route's panel says it would replace the followed one.
  await page.goto("/household?tab=ziel#d-ziel-ausgewogen");
  const balanced = detail(page, "Ausgewogener Weg").getByTestId("goal-follow-balanced");
  await expect(balanced).toContainText("dieser ersetzt ihn ab heute");

  await page.goto("/household?tab=ziel#d-ziel-rendite");
  await detail(page, "Nur mehr Rendite").getByTestId("goal-follow-more_return").getByRole("button", { name: "Nicht mehr folgen" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]!.levers.goal_choice?.investment ?? null).toBeNull();
  expect(posted[1]!.levers.goal_choice?.investment_chosen_on ?? null).toBeNull();
  expect(posted[1]!.levers.goal).toEqual(goal);
});

test("a route without a return to follow offers no button, and generic mode none at all", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(24), set_on: TODAY };
  const routes = withRoutes(goal, null);
  // The balanced route on the typed rates (no median return): nothing to follow.
  const typed: HouseholdGoal = { ...routes, balanced: { ...routes.balanced!, return_rate: null } };
  await goalApi(page, { goal: typed }, () => typed);
  await page.goto("/household?tab=ziel#d-ziel-ausgewogen");
  const balanced = detail(page, "Ausgewogener Weg");
  await expect(balanced).toBeVisible();
  await expect(balanced.getByTestId("goal-follow-balanced")).toHaveCount(0);

  // „Allgemein“ set through the API (setup, not under test), as on Einstellungen.
  const settings = await page.request.fetch("/api/workspace/settings", {
    method: "PATCH",
    data: { advice_mode: "generic" },
    headers: { Origin: new URL(page.url()).origin },
  });
  expect(settings.status()).toBe(200);
  // Only the hash differs from the page open now, so goto alone would not read the settings again.
  await page.goto("/household?tab=ziel#d-ziel-rendite");
  await page.reload();
  await expect(detail(page, "Nur mehr Rendite")).toBeVisible();
  await expect(page.getByTestId("goal-follow-more_return")).toHaveCount(0);
});

test("Anlagen shows „Für euer Ziel“ only for a followed route, its figures in the side panel", async ({ page, signedIn }) => {
  void signedIn;
  // The real read of a fresh workspace: no goal, so no card.
  const read = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/household-goal/tracking");
  await page.goto("/assets");
  expect((await (await read).json()) as GoalTracking).toMatchObject({ shown: false, reason: "no_goal" });
  await expect(page.getByRole("heading", { name: "Positionen" })).toBeVisible();
  await expect(page.getByTestId("goal-tracking")).toHaveCount(0);

  await trackingApi(page, tracking());
  await page.goto("/assets");
  const card = page.getByTestId("goal-tracking");
  await expect(card).toContainText("Für euer Ziel");
  await expect(card.locator(".goal-word")).toHaveAttribute("data-word", "on_track");
  await expect(card).toContainText("Auf dem Pfad");
  await expect(card).toContainText("2.400,00 € von 1.972,60 € eingezahlt");
  await card.click();
  const panel = detail(page, "Für euer Ziel");
  await expect(panel).toContainText("Mehr Rendite");
  await expect(panel).toContainText("6,1 %");
  await expect(panel).toContainText("Jahresrendite ab einem Jahr Verlauf");
  await expect(panel).toContainText("kein Rat");
  await expect(panel.getByRole("link", { name: /Weg ansehen/ })).toHaveAttribute("href", "/household?tab=ziel#d-ziel-rendite");

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await trackingApi(page, tracking({ route: "balanced", word: "behind", short: ["paid"], paid_in: "900.00", reserve_depots: 1 }));
  await page.goto("/assets");
  const behind = page.getByTestId("goal-tracking");
  await expect(behind).toContainText("Dahinter");
  await behind.click();
  const behindPanel = detail(page, "Für euer Ziel");
  await expect(behindPanel).toContainText("weniger eingezahlt, als der Weg plant");
  await expect(behindPanel).toContainText("Ein Depot zählt zur Reserve");
  await expect(behindPanel.getByRole("link", { name: /Weg ansehen/ })).toHaveAttribute("href", "/household?tab=ziel#d-ziel-ausgewogen");

  // A followed route that no longer computes: no word, its reason instead.
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await trackingApi(page, tracking({ word: null, reason: "route_gone", rate: null, paid_in: null, planned_by_now: null, value_change: null }));
  await page.goto("/assets");
  const gone = page.getByTestId("goal-tracking");
  await expect(gone).toContainText("Weg nicht mehr berechnet");
  await expect(gone.locator(".goal-word")).toHaveCount(0);
});

test("„Für euer Ziel“ and „Diesem Weg folgen“ at phone width, light and dark, German and English", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(24), set_on: TODAY };
  const followed = withRoutes(goal, { spending: null, spending_chosen_on: null, investment: "more_return", investment_chosen_on: TODAY });
  const history = [scenario({ ...leversWith(goal), goal_choice: followed.choice }, "00000000-0000-4000-8000-0000000b0001", null)];
  await goalApi(page, { goal: followed, history }, () => followed);
  await trackingApi(page, tracking({ word: "behind", short: ["paid", "value"], paid_in: "900.00", value_change: "120.00" }));
  for (const locale of ["de", "en"] as const) {
    await page.goto("/");
    await page.evaluate((value) => window.localStorage.setItem("hausbuch.locale", value), locale);
    await phoneScreens(page, `goal-track-card-${locale}`, async () => {
      await page.goto("/assets");
      await expect(page.getByTestId("goal-tracking")).toContainText(locale === "de" ? "Dahinter" : "Behind");
    });
    await phoneScreens(page, `goal-track-panel-${locale}`, async () => {
      await page.goto("/assets#d-fuerziel-pfad");
      await expect(page.getByTestId("goal-tracking-panel")).toBeVisible();
    });
    await phoneScreens(page, `goal-follow-${locale}`, async () => {
      await page.goto("/household?tab=ziel#d-ziel-rendite");
      await expect(page.getByTestId("goal-follow-more_return")).toContainText(locale === "de" ? "ihr folgt ihm" : "you follow it");
    });
  }
  await page.evaluate(() => window.localStorage.removeItem("hausbuch.locale"));
});
