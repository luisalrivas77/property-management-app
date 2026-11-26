// „Euer Plan“ (phase 10 §7.11 (h), D306; mockup 19i) at /household/plan. GET
// /household-goal/plan is mocked by its query (a plan needs months of bookings, the projection
// and the reference series; its views and switches are tested on the server in
// backend/tests/api/test_plan_page_api.py), and so are GET /household-goal and the household
// scenarios for the one write and the links. Every other read is the real API of a fresh
// workspace. Invented figures and names only.
//
// - „Für euch“: the seven cards, a card opening its explanation in the side panel.
// - „Zum Teilen“: the toggle puts the view in the address and asks for the shared view with the
//   default switches; a switch in „Was geteilt wird“ asks again and is kept on this device.
// - „Übernehmen“ on this month's wants step posts the balanced route's spending changes after the
//   in-page confirmation.
// - The links „Euer Plan ›“ from Haushalt › Ziel and from Übersicht's goal tile.
// - Phone and computer width, light and dark: no sideways scroll; screenshots.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type PlanPage = components["schemas"]["PlanPage"];
type HouseholdGoal = components["schemas"]["HouseholdGoal"];
type HouseholdScenario = components["schemas"]["HouseholdScenario"];
type HouseholdLevers = components["schemas"]["HouseholdLevers"];
type Goal = components["schemas"]["Goal"];

const WORKSPACE = "00000000-0000-4000-8000-00000000a001";
const FLAT = "Beispielweg 7, Wohnung 3"; // invented
const GIRO = "Girokonto Muster"; // invented

const pad = (n: number) => String(n).padStart(2, "0");
function monthsAhead(ahead: number): string {
  const now = new Date();
  const day = new Date(now.getFullYear(), now.getMonth() + ahead, 1);
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}`;
}
const NOW = new Date();
const TODAY = `${NOW.getFullYear()}-${pad(NOW.getMonth() + 1)}-${pad(NOW.getDate())}`;

const SPENDING_CHANGES = [
  { code: "hh_restaurant" as const, monthly: "250.00", percent: null, from_month: monthsAhead(1), to_month: monthsAhead(60) },
];

function ownPlan(): PlanPage {
  return {
    view: "own",
    word: "feasible",
    issue: null,
    goal: { kind: "net_worth", target: "500000.00", due_month: monthsAhead(62), word: "feasible", value_today: "312400.00", percent: "62.5" },
    heute: {
      net_worth: "312400.00",
      reserve: "21340.00",
      reserve_months: "7.6",
      reserve_full: true,
      saved_mean: "1840.00",
      window_from: monthsAhead(-12),
      window_to: monthsAhead(-1),
      reserve_accounts: [GIRO, "Tagesgeld Muster"],
    },
    plan: {
      on_track: false,
      reached: monthsAhead(60),
      without_route: "468000.00",
      gap: "32000.00",
      saving_extra: "250.00",
      by_class: { depot: "190.00", crypto: "60.00" },
      cut: "250.00",
      wants: [{ code: "hh_restaurant", from: "400.00", to: "250.00" }],
    },
    assumptions: {
      rates: { depot: "0.060", crypto: "0.080", reserve: "0.020" },
      properties: [{ key: "p1", kind: "etw", kept: true, sale_year: null, value_change: "0.015000", label: FLAT }],
    },
    risks: {
      base_reached: monthsAhead(60),
      weak_markets: {
        available: true,
        reason: null,
        window_years: 5,
        rates: { depot: "0.030", crypto: "0.030" },
        worst: { depot: "0.030", crypto: "-0.120" },
        capped: [],
        left_out: [],
        reached: monthsAhead(80),
        delay_months: 20,
        not_until: null,
      },
      income_gap: { available: true, months: 6, reached: monthsAhead(65), delay_months: 5, not_until: null, drawn: "0.00", used_up: null },
      repair: {
        available: true,
        reason: null,
        per_m2: "13.50",
        years: 10,
        by_property: { p1: "8100.00" },
        amount: "8100.00",
        reached: monthsAhead(63),
        delay_months: 3,
        not_until: null,
        used_up: null,
      },
      sources: [{ name: "ImmoWertV Anlage 3", url: "https://www.gesetze-im-internet.de/immowertv_2022/anlage_3.html", read_on: TODAY }],
      todos: [],
    },
    other_routes: { more_saving: "500.00", needed_return: "0.112", less_spending: "500.00", return_windows: 4, return_reached_windows: 1, return_verdict: "good_times" },
    this_month: {
      steps: [
        { kind: "invest", amount: "250.00", done: false },
        { kind: "wants_budget", amount: "1380.00", done: false },
      ],
      done: 0,
      of: 2,
    },
    todos: [],
  };
}

/** What the server answers for ?view=share: rounded, the labels and what the switches keep back
 * absent (the server's own rule, tested in test_plan_page_api.py; here only its shape). */
function sharedPlan(query: URLSearchParams, base: () => PlanPage = ownPlan): PlanPage {
  const on = (name: string, fallback: boolean) => (query.has(name) ? query.get(name) === "true" : fallback);
  const switches = {
    round_amounts: on("round_amounts", true),
    accounts: on("accounts", false),
    wants: on("wants", false),
    risks: on("risks", true),
    assumptions: on("assumptions", true),
  };
  const own = base();
  const { todos: _todos, ...rest } = own;
  void _todos;
  const page: PlanPage = { ...rest, view: "share", switches };
  if (switches.round_amounts) {
    page.goal = { ...own.goal!, value_today: "310000.00" };
    // „Jetzt anlegen“ on the reserve's step (1.000 €), as rules/goal/share.py rounds it
    const above = own.heute!.above_target;
    page.heute = {
      ...own.heute!,
      net_worth: "310000.00",
      reserve: "21000.00",
      saved_mean: "1850.00",
      above_target: above == null ? above : (Math.round(Number(above) / 1000) * 1000).toFixed(2),
    };
  }
  const { reserve_accounts: _accounts, ...heute } = page.heute!;
  void _accounts;
  page.heute = switches.accounts ? { ...heute, reserve_accounts: own.heute!.reserve_accounts } : heute;
  const { wants: _wants, ...route } = own.plan!;
  void _wants;
  page.plan = switches.wants ? own.plan : route;
  const { todos: _riskTodos, ...risks } = own.risks!;
  void _riskTodos;
  if (switches.risks) page.risks = risks;
  else delete page.risks;
  if (switches.assumptions) page.assumptions = { ...own.assumptions!, properties: own.assumptions!.properties!.map((p) => ({ ...p, label: undefined })) };
  else delete page.assumptions;
  return page;
}

/** GET /household-goal/plan by its query; the queries it was asked are returned. */
async function planApi(page: Page, own: () => PlanPage = ownPlan) {
  const asked: URLSearchParams[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-goal/plan",
    (route: Route) => {
      const query = new URL(route.request().url()).searchParams;
      asked.push(query);
      return route.fulfill({ json: query.get("view") === "share" ? sharedPlan(query, own) : own() });
    },
  );
  return asked;
}

/** The walk reaches the goal without change (Luis, 26 Nov 2026: „it doesn't say what the plan
 * is“): the plan card names what keeps it there. `above` the money above the reserve target. */
function onTrackPlan(above: string): () => PlanPage {
  return () => {
    const plan = ownPlan();
    return {
      ...plan,
      word: "on_track",
      goal: { ...plan.goal!, word: "on_track" },
      heute: { ...plan.heute!, above_target: above },
      plan: { on_track: true, reached: monthsAhead(58), without_route: "512000.00", gap: null, saving_extra: null, by_class: {}, cut: null, wants: [] },
    };
  };
}

function goalAnswer(): HouseholdGoal {
  return {
    word: "feasible",
    goal: { kind: "net_worth", target: "500000.00", due_month: monthsAhead(62), set_on: TODAY },
    choice: null,
    issue: null,
    target: "500000.00",
    value_today: "312400.00",
    percent: "62.5",
    months_left: 62,
    heute: "1840.00",
    noetig: "2340.00",
    projected: "468000.00",
    reached: null,
    reached_with_budget: null,
    used_up: null,
    scenarios: [],
    unreachable: null,
    todos: [],
    balanced: {
      cut: "250.00",
      saving_extra: "250.00",
      moved_to_saving: "250.00",
      saving: "2340.00",
      closes: true,
      budgets: [{ code: "hh_restaurant", budget_class: "want", mean: "400.00", budget: "250.00" }],
      spending_changes: SPENDING_CHANGES,
      reached: monthsAhead(60),
    },
  };
}

interface Posted {
  name: string;
  is_default: boolean;
  levers: HouseholdLevers;
  supersedes_id: string | null;
  withdrawn: boolean;
}

/** GET /household-goal, and the scenarios: GET from the history, POST into it. */
async function goalApi(page: Page, history: HouseholdScenario[]) {
  const posted: Posted[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-goal",
    (route: Route) => (route.request().method() === "GET" ? route.fulfill({ json: goalAnswer() }) : route.fallback()),
  );
  await page.route(
    (url) => url.pathname === "/api/household-scenarios",
    (route: Route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ json: history });
      if (method !== "POST") return route.fallback();
      const body = route.request().postDataJSON() as Posted;
      posted.push(body);
      return route.fulfill({ status: 201, json: { ...history[0]!, id: "00000000-0000-4000-8000-0000000b0002", levers: body.levers, supersedes_id: body.supersedes_id } });
    },
  );
  return posted;
}

/** A detail panel: the column beside the list on the computer, the sheet on the phone. */
function detail(page: Page, name: string) {
  return page.getByRole("complementary", { name }).or(page.getByRole("dialog", { name }));
}

// A card head is a link named by its title (and its tag: „Diesen Monat 0 von 2“).
const CARDS = [/^Heute$/, /^Der Plan · ausgewogen$/, /^Eure Annahmen$/, /^Wenn es schlechter läuft$/, /^Andere Wege$/, /^Diesen Monat/];

test("Für euch: seven cards, a card opens its explanation in the side panel", async ({ page, signedIn }) => {
  void signedIn;
  const asked = await planApi(page);
  await page.goto("/household/plan");
  await expect(page.getByRole("heading", { level: 1, name: "Euer Plan" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Für euch" })).toHaveAttribute("aria-pressed", "true");
  const main = page.locator(".planp-cards");
  await expect(main.getByRole("link", { name: /^500\.000\s€ bis / })).toBeVisible();
  await expect(main.locator(".planp-goal")).toContainText("Machbar");
  for (const name of CARDS) await expect(main.getByRole("link", { name })).toBeVisible();
  await expect(main).toContainText("+ mehr anlegen");
  await expect(main).toContainText("Märkte schwach (3,0 %)");
  await expect(main).toContainText("0 von 2");
  expect(asked.every((query) => query.get("view") === "own")).toBe(true);

  await main.getByRole("link", { name: /^Heute$/ }).click();
  const panel = detail(page, "Heute");
  await expect(panel).toContainText("Konten der Reserve");
  await expect(panel).toContainText(GIRO);
  await expect(page).toHaveURL(/#d-plan-heute$/);

  await main.getByRole("link", { name: /^Der Plan · ausgewogen$/ }).click();
  const route = detail(page, "Der Plan · ausgewogen");
  await expect(route).toContainText("Diese Wünsche werden kleiner");
  await expect(route).toContainText("Restaurant");
  await expect(route).toContainText("in Fonds");
});

test("Zum Teilen: the shared view with the default switches; a switch asks again and stays on this device", async ({ page, signedIn }) => {
  void signedIn;
  const asked = await planApi(page);
  // „Für euch“ names the flat in the assumptions' panel.
  await page.goto("/household/plan#d-plan-annahmen");
  await expect(detail(page, "Eure Annahmen")).toContainText(FLAT);

  await page.getByRole("button", { name: "Zum Teilen" }).click();
  await expect(page).toHaveURL(/\?ansicht=teilen/);
  await expect(page.getByRole("button", { name: "Zum Teilen" })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => asked.at(-1)?.get("view")).toBe("share");
  const first = asked.at(-1)!;
  expect(Object.fromEntries(first)).toEqual({
    view: "share",
    round_amounts: "true",
    accounts: "false",
    wants: "false",
    risks: "true",
    assumptions: "true",
  });
  const main = page.locator(".planp-cards");
  await expect(main).toContainText("rund 310.000 €");
  // The shared view carries no label: the panel names the kind of property only.
  await main.getByRole("link", { name: /^Eure Annahmen$/ }).click();
  await expect(detail(page, "Eure Annahmen")).toContainText("Wohnung");
  await expect(page.locator("body")).not.toContainText(FLAT);

  await page.getByRole("link", { name: /Gerundet, ohne Konten/ }).click();
  const shared = detail(page, "Was geteilt wird");
  await expect(shared).toContainText("Nie geteilt: Namen, Adresse, Mieter, IBANs, Wallet-Adressen.");
  const wants = shared.getByRole("switch", { name: "Kategorien der Wünsche" });
  await expect(wants).not.toBeChecked();
  await wants.check();
  await expect.poll(() => asked.at(-1)?.get("wants")).toBe("true");
  await shared.getByRole("switch", { name: "Wenn es schlechter läuft" }).uncheck();
  await expect.poll(() => asked.at(-1)?.get("risks")).toBe("false");
  await expect(main.getByRole("link", { name: /^Wenn es schlechter läuft$/ })).toHaveCount(0);

  // Kept on this device: a new load asks with the same switches.
  const before = asked.length;
  await page.reload();
  await expect.poll(() => asked.length).toBeGreaterThan(before);
  await expect.poll(() => Object.fromEntries(asked.at(-1)!)).toMatchObject({ view: "share", wants: "true", risks: "false" });

  await page.getByRole("button", { name: "Für euch" }).click();
  await expect(page).not.toHaveURL(/ansicht=/);
  await expect.poll(() => asked.at(-1)?.get("view")).toBe("own");
  await expect(main.getByRole("link", { name: /^Wenn es schlechter läuft$/ })).toBeVisible();
});

test("while the shared answer loads, „Für euch“'s exact figures and account names are gone", async ({ page, signedIn }) => {
  void signedIn;
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    (url) => url.pathname === "/api/household-goal/plan",
    async (route: Route) => {
      const query = new URL(route.request().url()).searchParams;
      if (query.get("view") !== "share") return route.fulfill({ json: ownPlan() });
      await held;
      return route.fulfill({ json: sharedPlan(query) });
    },
  );
  await page.goto("/household/plan#d-plan-heute");
  await expect(detail(page, "Heute")).toContainText(GIRO);
  await expect(page.locator(".planp-cards")).toContainText("312.400");

  await page.getByRole("button", { name: "Zum Teilen" }).click();
  await expect(page).toHaveURL(/\?ansicht=teilen/);
  await expect(page.locator("body")).not.toContainText("312.400");
  await expect(page.locator("body")).not.toContainText(GIRO);

  release();
  await expect(page.locator(".planp-cards")).toContainText("rund 310.000 €");
  await expect(page.locator("body")).not.toContainText(GIRO);
});

test("Übernehmen posts the balanced route's spending changes after the in-page confirmation", async ({ page, signedIn }) => {
  void signedIn;
  const goal: Goal = { kind: "net_worth", target: "500000.00", due_month: monthsAhead(62), set_on: TODAY };
  const levers: HouseholdLevers = { reserve_target_months: "6", buffer_percent: "10", horizon_months: 60, net_worth_years: 10, goal, goal_choice: null, spending_changes: [] };
  const saved: HouseholdScenario = {
    id: "00000000-0000-4000-8000-0000000b0001",
    workspace_id: WORKSPACE,
    name: "Standard",
    is_default: true,
    levers,
    supersedes_id: null,
    withdrawn: false,
    created_at: `${TODAY}T09:00:00Z`,
    created_by: null,
  };
  const posted = await goalApi(page, [saved]);
  const asked = await planApi(page);
  await page.goto("/household/plan");
  const month = page.locator(".planp-cards");
  await month.getByRole("link", { name: "Übernehmen" }).click();
  const panel = detail(page, "Diesen Monat");
  await expect(panel).toContainText("Das Budget des Plans übernehmen?");
  expect(posted).toHaveLength(0);
  const before = asked.length;
  await panel.getByRole("button", { name: "Ja, übernehmen" }).click();

  await expect.poll(() => posted.length).toBe(1);
  const body = posted[0]!;
  expect(body.supersedes_id).toBe(saved.id);
  expect(body.levers.goal).toEqual(goal);
  expect(body.levers.spending_changes).toEqual(SPENDING_CHANGES);
  expect(body.levers.goal_choice?.spending).toBe("own");
  expect(body.levers.goal_choice?.spending_chosen_on).toBe(TODAY);
  await expect(panel).toHaveCount(0);
  await expect.poll(() => asked.length).toBeGreaterThan(before);
});

test("„Euer Plan ›“ from Haushalt › Ziel and from Übersicht's goal tile", async ({ page, signedIn }) => {
  void signedIn;
  await goalApi(page, []);
  await page.goto("/household?tab=ziel");
  const card = page.getByRole("region", { name: "Euer Ziel" });
  await expect(card.getByRole("link", { name: "Euer Plan" })).toHaveAttribute("href", "/household/plan");
  await page.goto("/");
  const tiles = page.getByRole("region", { name: "Auf einen Blick" });
  await expect(tiles.getByRole("link", { name: "Euer Plan" })).toHaveAttribute("href", "/household/plan");
});

test("on track, the plan card says what keeps you there: saving, investing now, your rates", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page, onTrackPlan("12340.00"));
  await page.goto("/household/plan");
  const main = page.locator(".planp-cards");
  const card = main.locator(".goal-c-main");
  await expect(card.getByRole("link", { name: /^Ihr seid auf Kurs$/ })).toBeVisible();
  await expect(card).toContainText("Weiter sparen");
  await expect(card).toContainText(/1\.840\s€ im Monat/);
  await expect(card).toContainText("Jetzt anlegen");
  await expect(card).toContainText(/12\.340\s€/);
  await expect(card).toContainText("Bei euren Raten");
  await expect(card).toContainText(/Fonds 6,0\s%\s·\sKrypto 8,0\s%/);
  await expect(card).toContainText("Am Ziel");
  await expect(card).not.toContainText("+ mehr anlegen");

  await card.getByRole("link", { name: /^Ihr seid auf Kurs$/ }).click();
  const panel = detail(page, "Ihr seid auf Kurs");
  await expect(panel).toContainText(/vorausgesetzt ihr spart weiter im Schnitt 1\.840\s€ im Monat/);
  await expect(panel).toContainText(/legt das Geld über dem Reserve-Ziel an \(heute 12\.340\s€\)/);
  await expect(panel).toContainText("„Wenn es schlechter läuft“ zeigt, um wie viel.");
  await expect(panel).not.toContainText("Es braucht keinen Schritt");

  // Zum Teilen: rounded like the reserve; without the assumptions no rates row.
  await page.goto("/household/plan?ansicht=teilen");
  await expect(card).toContainText(/rund 12\.000\s€/);
  await expect(card).toContainText("Bei euren Raten");
  await page.goto("/household/plan?ansicht=teilen#d-plan-teilen");
  await detail(page, "Was geteilt wird").getByRole("switch", { name: "Eure Annahmen" }).uncheck();
  await expect(main.getByRole("link", { name: /^Eure Annahmen$/ })).toHaveCount(0);
  await expect(card).toContainText("Weiter sparen");
  await expect(card).not.toContainText("Bei euren Raten");
});

test("on track with nothing above the reserve target: no „Jetzt anlegen“", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page, onTrackPlan("0.00"));
  await page.goto("/household/plan#d-plan-weg");
  const card = page.locator(".planp-cards .goal-c-main");
  await expect(card).toContainText("Weiter sparen");
  await expect(card).not.toContainText("Jetzt anlegen");
  const panel = detail(page, "Ihr seid auf Kurs");
  await expect(panel).toContainText("legt das Geld über dem Reserve-Ziel an und eure Anlagen");
  await expect(panel).not.toContainText("(heute");
});

async function screens(page: Page, name: string, width: number, open: () => Promise<void>) {
  await page.setViewportSize({ width, height: width < 800 ? 844 : 900 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("about:blank");
    await open();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    // The whole page, or with a panel open what the screen shows (on the phone the sheet covers it).
    await page.screenshot({ path: test.info().outputPath(`${name}-${scheme}.png`), fullPage: !page.url().includes("#d-") });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
}

test("Euer Plan at phone and computer width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  const cards = page.locator(".planp-cards");
  for (const [suffix, width] of [
    ["phone", 390],
    ["computer", 1280],
  ] as const) {
    await screens(page, `plan-own-${suffix}`, width, async () => {
      await page.goto("/household/plan");
      await expect(cards).toContainText("0 von 2");
    });
    await screens(page, `plan-share-${suffix}`, width, async () => {
      await page.goto("/household/plan?ansicht=teilen");
      await expect(cards).toContainText("rund 310.000 €");
    });
    await screens(page, `plan-switches-${suffix}`, width, async () => {
      await page.goto("/household/plan?ansicht=teilen#d-plan-teilen");
      await expect(detail(page, "Was geteilt wird")).toContainText("Nie geteilt");
    });
    await screens(page, `plan-risks-${suffix}`, width, async () => {
      await page.goto("/household/plan#d-plan-risiken");
      await expect(detail(page, "Wenn es schlechter läuft")).toContainText("ImmoWertV");
    });
  }
});

test("on track, the plan card at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page, onTrackPlan("12340.00"));
  await screens(page, "plan-on-track-phone", 390, async () => {
    await page.goto("/household/plan");
    await expect(page.locator(".planp-cards .goal-c-main")).toContainText("Jetzt anlegen");
  });
});
