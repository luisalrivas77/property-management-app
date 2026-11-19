// The KI panel beside the household plan (docs/phases/phase-9.md §10, §14, §16 step 5; mockup
// 12). The reads of phase 9 (GET /health, /health/months, /household-plan) and the explanation
// (GET and POST /household-plan/explanation) are mocked: a stored explanation needs a model run,
// and no test here calls a model. The workspace settings are the real API of a fresh workspace,
// so „Private Konten mit KI zuordnen“ and the Hinweis-Modus are switched for real. Synthetic
// figures and texts only.
//
// - A template text (the model's text rejected, or any other reason) shows „Standardtext“ and one
//   plain sentence on why; a stale one says its figures have changed.
// - With the AI off, „Neu erklären“ cannot be pressed, sends nothing, and the panel links to the
//   switch on Einstellungen; a POST answered `queued: false` says the same, never a spinner.
// - A text written while the AI was off says, once the AI is on, that „Neu erklären“ asks it now
//   (review finding 9 of step 4b).
// - „Neu erklären“ waits for a stored row other than the one shown and then shows it.
// - Switching the Hinweis-Modus shows only that mode's explanation and footer, and the paths
//   table follows the plan's two rows. The collapse itself is the backend's
//   (rules/plan/paths.py, tests/fixtures/phase9/paths.yaml `generic_two_rows`); this only shows
//   that the screen follows what the API answers.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type Health = components["schemas"]["Health"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type HouseholdMonth = components["schemas"]["HouseholdMonth"];
type HouseholdPlan = components["schemas"]["HouseholdPlan"];
type Path = components["schemas"]["Path"];
type PlanExplanation = components["schemas"]["PlanExplanation"];
type AdviceMode = components["schemas"]["AdviceMode"];

/** YYYY-MM, `back` months before the current one. */
function monthsBack(back: number): string {
  const now = new Date();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return day.toISOString().slice(0, 7);
}

const WINDOW = [monthsBack(4), monthsBack(3), monthsBack(2)];
const STORED_ID = "00000000-0000-4000-8000-000000000e01";
const NEW_ID = "00000000-0000-4000-8000-000000000e02";

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

function path(key: string, paused: string[], reached: string): Path {
  return { key, paused, to_reserve_first_month: "1170.00", reached, reserve_at_horizon: "20000.00" };
}

/** The plan with three savings plans, the third without a row of its own (`paths_others`): four
 * paths under `own_use`, the two of D251 under `generic`. */
function plan(mode: AdviceMode): HouseholdPlan {
  const last = WINDOW[WINDOW.length - 1] ?? null;
  const reachedLater = monthsBack(-20);
  const all = [
    path("reserve_first", ["p1", "p2"], monthsBack(-8)),
    path("pause:p1", ["p1"], monthsBack(-12)),
    path("pause:p2", ["p2"], monthsBack(-14)),
    path("as_is", [], reachedLater),
  ];
  return {
    as_of: "2026-11-18",
    scenario_id: null,
    levers: { reserve_target_months: "6", buffer_percent: "10", horizon_months: 60, net_worth_years: 10, value_change: { depot: null, crypto: null } },
    newest_closed: last,
    surplus: {
      rows: [
        { key: "mean_income", amount: "5200.00" },
        { key: "mean_regular_spending", amount: "-3900.00" },
        { key: "free_cash", amount: "1300.00" },
        { key: "buffer", amount: "130.00" },
        { key: "investable", amount: "1170.00" },
      ],
    },
    plans: [
      { key: "p1", destination_payee: "Fondsdepot A", asset_class: "depot", monthly: "300.00", months: WINDOW, overridden: false },
      { key: "p2", destination_payee: "Kryptobörse B", asset_class: "crypto", monthly: "100.00", months: WINDOW, overridden: false },
      { key: "p3", destination_payee: "Fondsdepot C", asset_class: "depot", monthly: "50.00", months: WINDOW, overridden: false },
    ],
    already_invested: "450.00",
    investable_left: "720.00",
    paths_others: ["p3"],
    months: [],
    paths: mode === "generic" ? all.filter((p) => p.key === "reserve_first" || p.key === "as_is") : all,
    projection: { points: [], todos: [] },
    todos: [],
  };
}

function explanation(fields: Partial<PlanExplanation>): PlanExplanation {
  return {
    id: STORED_ID,
    scenario_id: null,
    mode: "own_use",
    status: "model",
    reason: null,
    paragraphs: [{ topic: "investable", text: "Im Schnitt sind 1.170 € im Monat anlegbar." }],
    created_at: "2026-11-18T09:00:00Z",
    stale: false,
    ...fields,
  };
}

/** Serve JSON for GET requests to exactly this API path; other methods go on to the real API. */
async function serve(page: Page, at: string, body: (url: URL) => unknown | Promise<unknown>): Promise<void> {
  await page.route(
    (url) => url.pathname === at,
    async (route: Route) =>
      route.request().method() === "GET" ? route.fulfill({ json: await body(new URL(route.request().url())) }) : route.fallback(),
  );
}

/** The mocked reads of phase 9, the plan under the workspace's real Hinweis-Modus. */
async function planReads(page: Page): Promise<void> {
  await serve(page, "/api/health", () => health());
  await serve(page, "/api/health/net-worth-series", () => []);
  await serve(page, "/api/health/months", (url) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return [...WINDOW, monthsBack(1), monthsBack(0)]
      .filter((month) => month >= from && month <= to)
      .map((month) => monthView(month, WINDOW.includes(month)));
  });
  await serve(page, "/api/household-plan", async () => plan(await adviceMode(page)));
}

async function adviceMode(page: Page): Promise<AdviceMode> {
  const response = await page.request.get("/api/workspace/settings");
  return ((await response.json()) as { advice_mode: AdviceMode }).advice_mode;
}

/** PATCH /workspace/settings through the API (setup, not under test). */
async function patchSettings(page: Page, body: Record<string, unknown>): Promise<void> {
  const origin = new URL(page.url()).origin;
  const response = await page.request.fetch("/api/workspace/settings", { method: "PATCH", data: body, headers: { Origin: origin } });
  expect(response.status()).toBe(200);
}

/** The explanation's POSTs, answered with `queued`; the GET answers `next()`. */
async function explanationRoute(
  page: Page,
  next: () => PlanExplanation,
  queued = true,
): Promise<{ posts: unknown[] }> {
  const posts: unknown[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-plan/explanation",
    (route: Route) => {
      if (route.request().method() === "POST") {
        posts.push(route.request().postDataJSON());
        return route.fulfill({ status: 202, json: { scenario_id: null, queued } });
      }
      return route.fulfill({ json: next() });
    },
  );
  return { posts };
}

const panelOf = (page: Page) => page.getByRole("complementary", { name: "Erklärung zu euren Zahlen" });

test("a template text shows „Standardtext“ and one sentence on why, a stale one that its figures changed", async ({ page, signedIn }) => {
  void signedIn;
  await planReads(page);
  let shown = explanation({
    status: "template",
    reason: "rejected",
    paragraphs: [{ topic: "investable", text: "Nach dem Puffer sind 1.170 € im Monat anlegbar." }],
    // 23:30 UTC is already the next day in Berlin: the date is the household's, not UTC's.
    created_at: "2026-11-18T23:30:00Z",
    stale: true,
  });
  await explanationRoute(page, () => shown);

  await page.goto("/planning?tab=haushalt");
  const panel = panelOf(page);
  await expect(panel.getByText("Standardtext", { exact: true })).toBeVisible();
  await expect(panel.getByText("Zahlen haben sich seitdem geändert", { exact: true })).toBeVisible();
  await expect(panel.getByText("Erklärung vom 19. November 2026")).toBeVisible();
  await expect(panel.getByText("Diesen Text hat die App selbst aus euren Zahlen geschrieben.")).toBeVisible();
  await expect(panel.getByTestId("plan-ai-reason")).toHaveText(
    "Der Text der KI hat die Prüfung der App nicht bestanden, deshalb steht hier der Text der App.",
  );
  await expect(panel.getByText("Nach dem Puffer sind 1.170 € im Monat anlegbar.")).toBeVisible();
  await expect(panel.getByText("(Modus: eigene Nutzung). Keine Anlageberatung.")).toBeVisible();

  // Each other reason has its own sentence; the month's AI limit says so.
  const sentences: [PlanExplanation["reason"], string][] = [
    ["refused", "Die KI hat keinen Text geliefert"],
    ["limit", "Das KI-Limit dieses Monats ist aufgebraucht"],
    ["error", "Beim Fragen der KI ist ein Fehler aufgetreten"],
  ];
  for (const [reason, sentence] of sentences) {
    shown = explanation({ status: "template", reason });
    await page.reload();
    await expect(panel.getByTestId("plan-ai-reason")).toContainText(sentence);
    await expect(panel.getByText("Standardtext", { exact: true })).toBeVisible();
    await expect(panel.getByText("Zahlen haben sich seitdem geändert", { exact: true })).toHaveCount(0);
  }

  // A model text has neither chip and no reason.
  shown = explanation({});
  await page.reload();
  await expect(panel.getByText("Im Schnitt sind 1.170 € im Monat anlegbar.")).toBeVisible();
  await expect(panel.getByText("Standardtext", { exact: true })).toHaveCount(0);
  await expect(panel.getByTestId("plan-ai-reason")).toHaveCount(0);
});

test("with the AI off, „Neu erklären“ sends nothing and the panel links to the switch", async ({ page, signedIn }) => {
  void signedIn;
  await planReads(page);
  const { posts } = await explanationRoute(page, () =>
    explanation({ id: null, status: "template", reason: "consent_off", created_at: null }),
  );

  await page.goto("/planning?tab=haushalt");
  const panel = panelOf(page);
  const button = panel.getByRole("button", { name: "Neu erklären" });
  await expect(button).toBeDisabled();
  const off = panel.getByTestId("plan-ai-off");
  await expect(off).toContainText("Die KI ist ausgeschaltet");
  await expect(off.getByRole("link", { name: "Einstellungen › „Private Konten mit KI zuordnen“" })).toHaveAttribute("href", "/settings#ki");
  // Rendered on read from today's facts: the plan's day, not a stored one.
  await expect(panel.getByText("Erklärung vom 18. November 2026")).toBeVisible();
  // Not finding 9's sentence: the AI is still off.
  await expect(panel.getByTestId("plan-ai-reason")).toHaveCount(0);
  await expect(panel.getByRole("status")).toHaveCount(0);
  expect(posts).toEqual([]);

  await off.getByRole("link").click();
  await expect(page).toHaveURL(/\/settings#ki$/);
  await expect(page.getByRole("switch", { name: "Private Konten mit KI zuordnen" })).toBeVisible();
});

test("a POST that queues nothing says the AI is off, never a spinner", async ({ page, signedIn }) => {
  void signedIn;
  await patchSettings(page, { ai_private_accounts: true });
  await planReads(page);
  // The switch was turned off elsewhere after this page read it: the server queues nothing.
  const { posts } = await explanationRoute(page, () => explanation({ id: null, status: "template", reason: null, created_at: null }), false);

  await page.goto("/planning?tab=haushalt");
  const panel = panelOf(page);
  await expect(panel.getByTestId("plan-ai-reason")).toHaveText("Die KI hat zu diesen Zahlen noch nichts geschrieben; „Neu erklären“ fragt sie.");
  await patchSettings(page, { ai_private_accounts: false });
  await panel.getByRole("button", { name: "Neu erklären" }).click();
  await expect(panel.getByTestId("plan-ai-off")).toContainText("Die KI ist ausgeschaltet");
  await expect(panel.getByText("Die KI schreibt die Erklärung")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Neu erklären" })).toBeDisabled();
  expect(posts).toHaveLength(1);
});

test("a text written while the AI was off says „Neu erklären“ asks it, once the AI is on", async ({ page, signedIn }) => {
  void signedIn;
  await planReads(page);
  await explanationRoute(page, () => explanation({ status: "template", reason: "consent_off" }));

  await page.goto("/planning?tab=haushalt");
  const panel = panelOf(page);
  await expect(panel.getByTestId("plan-ai-off")).toBeVisible();
  await expect(panel.getByTestId("plan-ai-reason")).toHaveCount(0);

  await patchSettings(page, { ai_private_accounts: true });
  await page.reload();
  await expect(panel.getByTestId("plan-ai-reason")).toHaveText("Beim letzten Mal war die KI ausgeschaltet. „Neu erklären“ fragt sie jetzt.");
  await expect(panel.getByTestId("plan-ai-off")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Neu erklären" })).toBeEnabled();
});

test("„Neu erklären“ waits for the new text and shows it", async ({ page, signedIn }) => {
  void signedIn;
  await patchSettings(page, { ai_private_accounts: true });
  await planReads(page);
  let asked = false;
  const { posts } = await explanationRoute(page, () =>
    asked
      ? explanation({ id: NEW_ID, paragraphs: [{ topic: "reserve", text: "Eure Reserve reicht für 4,0 Monate." }] })
      : explanation({ status: "template", reason: "error", paragraphs: [{ topic: "reserve", text: "Alter Text der App." }] }),
  );

  await page.goto("/planning?tab=haushalt");
  const panel = panelOf(page);
  await expect(panel.getByText("Alter Text der App.")).toBeVisible();
  await panel.getByRole("button", { name: "Neu erklären" }).click();
  await expect(panel.getByRole("status")).toContainText("Die KI schreibt die Erklärung");
  await expect(panel.getByRole("button", { name: "Neu erklären" })).toBeDisabled();
  expect(posts).toEqual([{ scenario: null }]);
  asked = true;

  await expect(panel.getByText("Eure Reserve reicht für 4,0 Monate.")).toBeVisible();
  await expect(panel.getByText("Alter Text der App.")).toHaveCount(0);
  await expect(panel.getByText("Standardtext", { exact: true })).toHaveCount(0);
  await expect(panel.getByRole("status")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Neu erklären" })).toBeEnabled();
});

test("switching the Hinweis-Modus shows only that mode's explanation, and the paths follow the plan", async ({ page, signedIn }) => {
  void signedIn;
  await planReads(page);
  // The API answers with the explanation of the workspace's current mode only (§10).
  await serve(page, "/api/household-plan/explanation", async () =>
    (await adviceMode(page)) === "generic"
      ? explanation({ mode: "generic", paragraphs: [{ topic: "plans", text: "In Fonds und Wertpapiere fließen 300 € im Monat." }] })
      : explanation({ paragraphs: [{ topic: "plans", text: "In Fondsdepot A fließen 300 € im Monat." }] }),
  );

  await page.goto("/planning?tab=haushalt");
  const panel = panelOf(page);
  const paths = page.getByRole("region", { name: "Wege zum Reserveziel" });
  await expect(panel.getByText("In Fondsdepot A fließen 300 € im Monat.")).toBeVisible();
  await expect(panel.getByText("(Modus: eigene Nutzung)")).toBeVisible();
  await expect(paths.locator("tbody tr")).toHaveCount(4);
  // Eigene Nutzung names each plan by its account: the card, the paths and „Weitere Annahmen“.
  const invested = page.locator(".planning-kpi").filter({ hasText: "Schon angelegt pro Monat" });
  await expect(invested).toContainText("450 €");
  await expect(invested).toContainText("Fondsdepot A 300 € · Kryptobörse B 100 € · Fondsdepot C 50 €");
  await expect(paths.getByText("Sparplan Fondsdepot A pausiert")).toBeVisible();
  await expect(paths.getByText("Weitere Sparpläne ohne eigene Zeile: Fondsdepot C.")).toBeVisible();
  const more = page.getByRole("region", { name: "Weitere Annahmen" });
  await expect(more.getByText("Kryptobörse B", { exact: true })).toBeVisible();

  await page.goto("/settings");
  const saved = page.waitForResponse((r) => r.url().endsWith("/api/workspace/settings") && r.request().method() === "PATCH");
  await page.getByRole("radio", { name: "Allgemein" }).click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByTestId("advice-mode-saved")).toHaveText("Gespeichert: Allgemein");

  await page.goto("/planning?tab=haushalt");
  await expect(panel.getByText("In Fonds und Wertpapiere fließen 300 € im Monat.")).toBeVisible();
  await expect(panel.getByText("In Fondsdepot A fließen 300 € im Monat.")).toHaveCount(0);
  await expect(panel.getByText("nennt keine Produkte und keine Konten. Keine Anlageberatung.")).toBeVisible();
  await expect(panel.getByText("(Modus: eigene Nutzung)")).toHaveCount(0);
  await expect(paths.locator("tbody tr")).toHaveCount(2);
  await expect(paths.locator("tbody tr").nth(0)).toContainText("Reserve zuerst");
  await expect(paths.locator("tbody tr").nth(1)).toContainText("Alles weiter wie bisher");
  // Allgemein: the plans are one total, and no plan's account is named anywhere on the page.
  await expect(invested).toContainText("450 €");
  await expect(invested).toContainText("aus 3 Sparplänen zusammen");
  await expect(more.getByTestId("plans-total")).toHaveText("450 € im Monat, aus 3 Sparplänen zusammen");
  await expect(paths.getByText("Weitere Sparpläne ohne eigene Zeile")).toHaveCount(0);
  for (const label of ["Fondsdepot A", "Kryptobörse B", "Fondsdepot C"]) {
    await expect(page.getByText(label)).toHaveCount(0);
  }
  await expect(more.getByRole("button", { name: "Betrag ändern" })).toHaveCount(0);
});
