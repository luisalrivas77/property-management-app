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
      reserve_target: "16800.00",
      reserve_gap: "0.00",
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

  // The reserve as „X € von Y €“ with „erreicht“, its months in the panel (§7.13 (d) 2).
  const heute = main.locator(".goal-c", { has: page.getByRole("link", { name: /^Heute$/ }) });
  await expect(heute).toContainText(/21\.340\s€ von 16\.800\s€/);
  await expect(heute).toContainText("erreicht");
  await main.getByRole("link", { name: /^Heute$/ }).click();
  const panel = detail(page, "Heute");
  await expect(panel).toContainText("7,6 Mon. Reserve, voll");
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

// „Aufteilung ändern“ in the plan card's panel (phase 10 §7.11 (i) item 10): the split section
// reads GET /household-plan, which „Euer Plan“'s own read does not carry. Only the fields the
// section reads are served; the effective split follows the saved lever (invented figures).
type HouseholdPlan = components["schemas"]["HouseholdPlan"];
async function splitApi(page: Page, posted: Posted[], levers: HouseholdLevers) {
  await page.route(
    (url) => url.pathname === "/api/household-plan",
    (route: Route) => {
      const saved = posted.at(-1)?.levers.investment_split ?? null;
      const answer = {
        levers: { ...levers, investment_split: saved },
        investment_split: saved
          ? { source: "saved", shares: saved, left_out: [], todos: [] }
          : { source: "holdings", shares: { depot: "70.0", crypto: "30.0" }, left_out: [], todos: [] },
        investment_split_prefill: null,
      } as unknown as HouseholdPlan;
      return route.fulfill({ json: answer });
    },
  );
}

test("„Aufteilung ändern“ on the plan card's panel saves the split and reads the plan again", async ({ page, signedIn }) => {
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
  await splitApi(page, posted, levers);
  const asked = await planApi(page);
  await page.goto("/household/plan#d-plan-weg");
  const split = detail(page, "Der Plan · ausgewogen").getByTestId("investment-split");
  await expect(split).toContainText(/70\s?% in Fonds, 30\s?% in Krypto/);
  await expect(split).toContainText("Bis ihr eine Aufteilung speichert, rechnet der Plan mit dem heutigen Wert eurer Fonds und Krypto.");

  await split.getByRole("button", { name: "Aufteilung ändern" }).click();
  // Nothing saved and no purchases to prefill from: the form starts empty, never with a split of
  // the app's own (D247).
  await expect(split.getByLabel("Fonds (%)")).toHaveValue("");
  await split.getByLabel("Fonds (%)").fill("90");
  await expect(split.getByLabel("Krypto (%)")).toHaveValue("10");
  const before = asked.length;
  await split.getByRole("button", { name: "Aufteilung speichern" }).click();

  await expect.poll(() => posted.length).toBe(1);
  const body = posted[0]!;
  expect(body.supersedes_id).toBe(saved.id);
  expect(body.levers.investment_split).toEqual({ depot: "90.0", crypto: "10.0" });
  expect(body.levers.goal).toEqual(goal);
  expect(body.levers.reserve_target_months).toBe("6");
  await expect(split).toContainText(/90\s?% in Fonds, 10\s?% in Krypto/);
  await expect(split).toContainText("Eure gespeicherte Aufteilung.");
  // „Euer Plan“ is read again after the save.
  await expect.poll(() => asked.length).toBeGreaterThan(before);
});

// The goal's path (phase 10 §7.11 (i), D311; mockup 19j): GET /household-goal carries
// `goal_path` per state, GET /household-goal/tracking the rows since the day adopted, and POST
// /household-goal/path/adopt is mocked by its answer. The figures are invented; the ceiling's
// rows add up to it as the server's identity says (5.200 + 300 − 2.600 − 870 − 180 − 270).
type GoalPath = components["schemas"]["GoalPath"];
type GoalTracking = components["schemas"]["GoalTracking"];
type GoalPathRow = components["schemas"]["GoalPathRow"];

const CEILING: GoalPath["ceiling"] = {
  income: "5200.00",
  rental_cash: "300.00",
  need: "2600.00",
  wants_kept: "870.00",
  other_spending: "180.00",
  suggested: true,
  buffer: "270.00",
  ceiling: "1580.00",
};

function stepOne(): GoalPath {
  return {
    state: "doable_rates",
    ceiling: CEILING,
    anlegbar: "1100.00",
    room: "480.00",
    extra: "250.00",
    monthly: "1350.00",
    typed_rate: "0.068",
    rate: "0.068",
    wants_budget: [{ code: "hh_restaurant", budget_class: "want", mean: "400.00", budget: "150.00" }],
    wants_budget_total: "1220.00",
    spending_changes: SPENDING_CHANGES,
    adoptable: true,
    why: null,
    today: { invested: "600.00", saved: "1840.00", saved_not_invested: "1240.00", plans: [] },
    adopted: null,
  };
}

function withPath(path: GoalPath, choice: HouseholdGoal["choice"] = null): HouseholdGoal {
  return { ...goalAnswer(), choice, goal_path: path };
}

const ADOPTED: NonNullable<GoalPath["adopted"]> = {
  adopted_on: TODAY,
  state: "doable_rates",
  monthly: "1350.00",
  extra: "250.00",
  rate: "0.068",
  wants_budget_total: "1220.00",
  moved: false,
};

const FOLLOWED: HouseholdGoal["choice"] = { spending: null, spending_chosen_on: null, investment: "path", investment_chosen_on: TODAY };

function tracking(rows: GoalPathRow[]): GoalTracking {
  return { shown: true, route: "path", chosen_on: TODAY, rate: "0.068", reserve_depots: 0, rows, points: [] };
}

const ROW: GoalPathRow = {
  month_end: `${monthsAhead(0)}-01`,
  word: "behind",
  planned: "1350.00",
  invested: "1100.00",
  waiting: "0.00",
  invested_gap: "250.00",
  wants: "1180.00",
  wants_budget: "1220.00",
  wants_gap: "0.00",
  net_worth: "314000.00",
  path_net_worth: "315000.00",
  net_worth_gap: "1000.00",
  value_change: "900.00",
  implied_change: "800.00",
  actual_percent: "0.6",
  needed_percent: "0.5",
  value_behind: false,
};

/** GET /household-goal and /tracking from `state`; POST …/path/adopt answered by `adopt`. */
async function pathApi(page: Page, state: { goal: HouseholdGoal; tracking: GoalTracking }, adopt: () => { status: number; json: unknown }) {
  const posts: number[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-goal",
    (route: Route) => (route.request().method() === "GET" ? route.fulfill({ json: state.goal }) : route.fallback()),
  );
  await page.route(
    (url) => url.pathname === "/api/household-goal/tracking",
    (route: Route) => route.fulfill({ json: state.tracking }),
  );
  await page.route(
    (url) => url.pathname === "/api/household-goal/path/adopt",
    (route: Route) => {
      if (route.request().method() !== "POST") return route.fallback();
      posts.push(posts.length + 1);
      const answer = adopt();
      return route.fulfill({ status: answer.status, json: answer.json });
    },
  );
  return posts;
}

test("the path at step 1: the plan card, „Woher“ and „Diesen Plan übernehmen“, then the adopted plan tracked", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  const state = { goal: withPath(stepOne()), tracking: tracking([]) };
  const posts = await pathApi(page, state, () => {
    state.goal = withPath({ ...stepOne(), adopted: ADOPTED }, FOLLOWED);
    state.tracking = tracking([ROW]);
    return { status: 201, json: state.goal };
  });
  await page.goto("/household/plan");
  const main = page.locator(".planp-cards");
  await expect(main.locator(".planp-goal")).toContainText("Machbar");
  const plan = main.getByTestId("path-plan");
  await expect(plan).toHaveAttribute("data-state", "doable_rates");
  await expect(plan).toContainText(/1\.350\s€/);
  await expect(plan).toContainText(/250\s€ mehr als heute/);
  await expect(plan).toContainText(/Mischung 6,8\s%/);
  await expect(main.getByTestId("path-today")).toContainText(/600\s€/);
  // The balanced route and the old „Diesen Monat“ make way for the path.
  await expect(main.getByRole("link", { name: /^Der Plan · ausgewogen$/ })).toHaveCount(0);
  await expect(main.getByTestId("path-month")).toHaveCount(0);

  // „Woher“: the ceiling's rows, signed, the wants budget marked as the app's suggestion.
  await page.goto("/household/plan#d-plan-woher");
  const woher = page.getByTestId("path-woher");
  await expect(woher).toContainText(/Einkommen im Monat\s*5\.200\s€/);
  await expect(woher).toContainText(/Überschuss der Wohnung\s*\+ 300\s€/);
  await expect(woher).toContainText(/Notwendiges\s*− 2\.600\s€/);
  await expect(woher).toContainText(/Wünsche-Budget\s*Vorschlag\s*− 870\s€/);
  await expect(woher).toContainText(/Andere Ausgaben\s*− 180\s€/);
  await expect(woher).toContainText(/Puffer\s*− 270\s€/);
  await expect(woher).toContainText(/Höchstens anlegen\s*1\.580\s€/);

  await page.goto("/household/plan");
  await plan.getByRole("button", { name: "Diesen Plan übernehmen" }).click();
  await expect.poll(() => posts.length).toBe(1);
  await expect(plan).toContainText(/Plan übernommen am/);
  await expect(plan.getByRole("button", { name: /übernehmen/ })).toHaveCount(0);
  const month = main.getByTestId("path-month");
  await expect(month).toContainText(/1\.350\s€ anlegen/);
  await expect(month).toContainText(/Wünsche bis 1\.220\s€/);
  const row = month.getByTestId("path-row");
  await expect(row).toContainText(/250\s€ zu wenig/);
  await expect(row).toContainText(/1\.000\s€ dahinter/);
  await expect(main.getByTestId("path-since")).toContainText(/Angelegt 1\.100\s€/);
});

test("the path at a higher return: the card says Machbar and the return with its label, the sentence in the panel", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  const higher: GoalPath = { ...stepOne(), state: "doable_return", rate: "0.094", return_needed: "0.094", window_years: 5, windows: 4, reached_windows: 3 };
  const state = { goal: withPath(higher), tracking: tracking([]) };
  await pathApi(page, state, () => ({ status: 201, json: {} }));
  await page.goto("/household/plan");
  const card = page.locator(".planp-cards .planp-goal");
  // No sentence and no bare percent on the card (§7.13 (d) 1, D316).
  await expect(card.locator(".goal-word")).toHaveText("Machbar");
  await expect(card).toContainText(/9,4\s%\sRendite nötig/);
  await expect(card).not.toContainText("wenn eure Anlagen");
  await page.goto("/household/plan#d-plan-ziel");
  const panel = detail(page, "Euer Ziel");
  await expect(panel).toContainText(/Machbar, wenn eure Anlagen 9,4\s% im Jahr bringen/);
  await expect(panel).toContainText("erreicht in 3 von 4");
});

test("the path when it cannot hold: no adopt button; Ehrgeizig and Kaum erreichbar say why", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  const ambitious: GoalPath = {
    ...stepOne(),
    state: "ambitious",
    extra: null,
    monthly: null,
    rate: null,
    return_needed: "0.142",
    window_years: 5,
    windows: 4,
    reached_windows: 1,
    short_history: true,
    years: [2018, 2025],
    wants_budget: [],
    wants_budget_total: null,
    spending_changes: [],
    adoptable: false,
    why: { due_value: "441000.00", reached: monthsAhead(80) },
  };
  const state = { goal: withPath(ambitious), tracking: tracking([]) };
  const posts = await pathApi(page, state, () => ({ status: 201, json: {} }));
  await page.goto("/household/plan");
  const main = page.locator(".planp-cards");
  await expect(main.locator(".planp-goal")).toContainText("Ehrgeizig");
  await expect(main.getByTestId("path-ceiling")).toContainText(/1\.580\s€/);
  const ret = main.getByTestId("path-return");
  await expect(ret).toContainText(/14,2\s%/);
  await expect(ret).toContainText("1 von 4");
  await expect(ret).toContainText("kurze Historie 2018–2025");
  await expect(main.getByTestId("path-realistic")).toContainText(/441\.000\s€/);
  await expect(main.getByRole("button", { name: /übernehmen/ })).toHaveCount(0);

  // A return above the search's 100 % a year comes without a return and without a count, as
  // rules/goal/goal.py `_step_2` sends it: the card says so and draws no „x von y“.
  state.goal = withPath({ ...ambitious, state: "hardly", return_needed: null, reached_windows: null, why: { due_value: "402000.00", reached: null } });
  await page.reload();
  await expect(main.locator(".planp-goal")).toContainText("Kaum erreichbar");
  const why = main.getByTestId("path-why");
  await expect(why).toContainText(/über 100\s%/);
  await expect(why).not.toContainText(" von 4");
  await expect(main.getByTestId("path-realistic")).toContainText("nicht erreicht");
  await expect(main.getByTestId("path-realistic").getByRole("link", { name: /Budget ansehen/ })).toBeVisible();
  await expect(main.getByRole("button", { name: /übernehmen/ })).toHaveCount(0);
  expect(posts).toHaveLength(0);
});

test("„Jetzt anlegen“ on the path's plan card in every state, its split in the panel", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  // The server's figures: 12.340 € above the reserve target, 60/40 into funds and crypto, and
  // „Was ihr tun müsst“ as goal_path.actions sends it.
  const above = { above_target: "12340.00", above_target_depot: "7404.00", above_target_crypto: "4936.00" };
  const actions: GoalPath["actions"] = [
    { kind: "save_monthly", amount: "1350.00", amount_depot: "810.00", amount_crypto: "540.00" },
    { kind: "invest_now", amount: "12340.00", amount_depot: "7404.00", amount_crypto: "4936.00" },
    { kind: "keep_reserve", amount: "18000.00", within_target: true },
    { kind: "wants_cut", code: "hh_restaurant", mean: "400.00", amount: "150.00" },
  ];
  const reached = monthsAhead(50);
  const state = { goal: withPath({ ...stepOne(), ...above, actions, plan_reached: reached }), tracking: tracking([]) };
  await pathApi(page, state, () => ({ status: 201, json: {} }));
  await page.goto("/household/plan");
  const main = page.locator(".planp-cards");
  const now = main.getByTestId("path-plan").getByTestId("path-invest-now");
  await expect(now).toContainText(/Jetzt anlegen\s*12\.340\s€/);
  await page.goto("/household/plan#d-plan-weg");
  const steps = detail(page, "Der Plan").getByTestId("path-steps");
  await expect(steps.locator("li")).toHaveCount(4);
  await expect(steps.locator("li").nth(0)).toContainText(/Jeden Monat 1\.350\s€ sparen und anlegen\s*davon 810\s€ in Fonds, 540\s€ in Krypto/);
  await expect(steps.locator("li").nth(1)).toContainText(/Jetzt 12\.340\s€ anlegen\s*davon 7\.404\s€ in Fonds, 4\.936\s€ in Krypto/);
  await expect(steps.locator("li").nth(2)).toContainText(/Reserve von 18\.000\s€ halten/);
  await expect(steps.locator("li").nth(3)).toContainText(/Restaurant: 400\s€ → 150\s€ im Monat/);
  await expect(detail(page, "Der Plan")).toContainText(/Dann erreicht ihr 500\.000\s€ im/);

  const notHeld = { extra: null, monthly: null, rate: null, adoptable: false, why: { due_value: "441000.00", reached: null } };
  state.goal = withPath({ ...stepOne(), ...above, ...notHeld, state: "ambitious", return_needed: "0.142", windows: 4, reached_windows: 1 });
  await page.goto("/household/plan");
  await expect(main.getByTestId("path-return").getByTestId("path-invest-now")).toContainText(/12\.340\s€/);
  await expect(main.getByTestId("path-invest-now")).toHaveCount(1);

  state.goal = withPath({ ...stepOne(), ...above, ...notHeld, state: "hardly", return_needed: null, windows: null, reached_windows: null });
  await page.reload();
  await expect(main.getByTestId("path-realistic").getByTestId("path-invest-now")).toContainText(/12\.340\s€/);
  await expect(main.getByTestId("path-invest-now")).toHaveCount(1);

  // The reserve below its target and no want cut: no row, no step to invest now, the monthly
  // saving without a split (the walk fills the reserve first), the reserve to fill, and one step
  // that says the wants need no cut, below the app's suggestion (no wants target saved).
  const kept: GoalPath["actions"] = [
    { kind: "save_monthly", amount: "1350.00", amount_depot: null, amount_crypto: null },
    { kind: "keep_reserve", amount: "18000.00", within_target: false },
    { kind: "wants_none", amount: "640.00", within_target: true, wants_percent: null },
  ];
  state.goal = withPath({ ...stepOne(), above_target: "0.00", above_target_depot: null, above_target_crypto: null, actions: kept, plan_reached: reached });
  // a hash change alone does not load the page again, so load it first for the new answer
  await page.goto("/household/plan");
  await page.goto("/household/plan#d-plan-weg");
  await expect(main.getByTestId("path-plan")).toBeVisible();
  await expect(main.getByTestId("path-invest-now")).toHaveCount(0);
  const none = detail(page, "Der Plan").getByTestId("path-steps");
  await expect(none.locator("li")).toHaveCount(3);
  await expect(none).not.toContainText(/Jetzt .* anlegen/);
  await expect(none.locator("li").nth(0)).not.toContainText("davon");
  await expect(none.locator("li").nth(1)).toContainText(/Reserve zuerst auf 18\.000\s€ auffüllen/);
  await expect(none.locator("li").nth(2)).toContainText(/Wünsche: nichts zu kürzen – ihr liegt mit 640\s€ unter dem Vorschlag von 870\s€/);
});

test("a refused adopt (the path moved) shows the server's reason, and nothing is adopted", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  const state = { goal: withPath(stepOne()), tracking: tracking([]) };
  await pathApi(page, state, () => ({
    status: 422,
    json: { error: "household_scenario.path_invalid", message: "path invalid" },
  }));
  await page.goto("/household/plan");
  const plan = page.locator(".planp-cards").getByTestId("path-plan");
  await plan.getByRole("button", { name: "Diesen Plan übernehmen" }).click();
  await expect(plan.getByRole("alert")).toContainText("household_scenario.path_invalid");
  await expect(plan).not.toContainText("Plan übernommen am");
  await expect(plan.getByRole("button", { name: "Diesen Plan übernehmen" })).toBeEnabled();
});

test("the adopted path on Übersicht's tile and on Haushalt › Ziel", async ({ page, signedIn }) => {
  void signedIn;
  const state = { goal: withPath({ ...stepOne(), adopted: ADOPTED }, FOLLOWED), tracking: tracking([ROW]) };
  await pathApi(page, state, () => ({ status: 201, json: {} }));
  await page.goto("/");
  const rows = page.getByTestId("goal-tile-path");
  await expect(rows).toHaveAttribute("href", "/household/plan");
  await expect(rows).toContainText(/1\.350\s€ im Monat anlegen/);
  await expect(rows).toContainText(/Wünsche bis 1\.220\s€/);

  await page.goto("/household?tab=ziel");
  const summary = page.getByTestId("goal-path-summary");
  await expect(summary).toContainText(/1\.350\s€/);
  await expect(summary).toContainText(/Plan übernommen am/);
  await expect(summary.getByRole("link", { name: /Euer Plan/ })).toHaveAttribute("href", "/household/plan");
});

test("the path's cards at phone width, light and dark: no sideways scroll", async ({ page, signedIn }) => {
  void signedIn;
  await planApi(page);
  const state = { goal: withPath({ ...stepOne(), adopted: ADOPTED }, FOLLOWED), tracking: tracking([ROW]) };
  await pathApi(page, state, () => ({ status: 201, json: {} }));
  await screens(page, "plan-path-phone", 390, async () => {
    await page.goto("/household/plan");
    await expect(page.locator(".planp-cards").getByTestId("path-row")).toBeVisible();
  });
  await screens(page, "plan-path-woher-phone", 390, async () => {
    await page.goto("/household/plan#d-plan-woher");
    await expect(page.getByTestId("path-woher")).toBeVisible();
  });
});
