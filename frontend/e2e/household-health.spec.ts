// The household's key figures and plan (docs/phases/phase-9.md §14, step 3; mockups 12, 12b,
// 12c, 13, 13b). The reads of phase 9 (GET /health, /health/months, /household-plan) and the
// scenario writes are mocked, since a workspace with three closed months needs a full ledger;
// every other read is the real API of a fresh workspace. Synthetic figures only.
//
// - Since phase 10 §7.6 (Finanzielle Gesundheit) Haushalt's Kennzahlen are its three figures,
//   each with its dot against the standard limits until the household stores its own with
//   „Grenzen ändern“; a band stored for a figure no longer shown is kept. (Until then no figure
//   had a colour before „Übernehmen“, phase 9 D250.)
// - The plan's rate fields start empty with no placeholder, and the projection of the net worth
//   appears only once a rate is saved (D247): the app proposes none.
// - Below three closed months both screens show what holds the next month open, as to-dos whose
//   buttons work (D231, 12c).
// - Every figure opens what it is made of: its months, each a link to that month on Haushalt.
// - Since phase 10 §7.6 the reserve, the limits and the Kennzahlen are on Haushalt, and the
//   month-end card, the split of the assets and „Vermögen im Verlauf“ on Anlagen › Übersicht; an
//   old link `/?month_end=…` is forwarded there.
//
// §15.2's worked example (the figures of health_worked.yaml on screen) is not here: it needs the
// fixture's ledger seeded through the API, and the backend test of that fixture covers the
// figures themselves.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type Health = components["schemas"]["Health"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type HealthTodo = components["schemas"]["HealthTodo"];
type HouseholdMonth = components["schemas"]["HouseholdMonth"];
type HouseholdPlan = components["schemas"]["HouseholdPlan"];
type HouseholdScenario = components["schemas"]["HouseholdScenario"];
type HouseholdScenarioCreate = components["schemas"]["HouseholdScenarioCreate"];
type SnapshotResult = components["schemas"]["SnapshotResult"];
type Verdict = components["schemas"]["Verdict"];

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

const WORKSPACE = "00000000-0000-4000-8000-000000000901";
const SAVINGS = "00000000-0000-4000-8000-000000000902";
const FLAT = "00000000-0000-4000-8000-000000000903";

/** YYYY-MM, `back` months before the current one. */
function monthsBack(back: number): string {
  const now = new Date();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return day.toISOString().slice(0, 7);
}

function householdMonth(month: string, income = "5200.00", spending = "3900.00"): HouseholdMonth {
  const saved = (Number(income) - Number(spending)).toFixed(2);
  return {
    month,
    private_income: income,
    private_spending: spending,
    one_off_spending: "0.00",
    regular_spending: spending,
    rental_result: "0.00",
    rental_cash: "0.00",
    income,
    spending,
    saved,
    principal: "0.00",
    invested_property: "0.00",
    invested: "0.00",
    free_cash: saved,
    savings_rate: (Number(saved) / Number(income)).toFixed(4),
  };
}

function monthView(month: string, closed: boolean, todos: HealthTodo[] = []): HealthMonthView {
  return { month, household: householdMonth(month), closed: { month, closed, todos } };
}

const TOO_FEW: HealthTodo = {
  code: "too_few_months",
  text: "Erst 1 Monat ist abgeschlossen; die Durchschnitte brauchen 3.",
  action: "Zu prüfen öffnen",
  href: "/inbox",
};

const TEXTS = { question: "Frage", headline: "Kurzfassung", why: "Warum", reference: "Bezugspunkt" };

/** Finanzielle Gesundheit as rules/verdict states it for these figures: the savings rate of 25 %
 * good on the standard limits (ok once the household's own green of 30 % is stored), the reserve
 * between half its target and the target, the loans' 15,4 % good. Below three closed months every
 * figure is missing. */
function verdictOf(enough: boolean, ownSavingsGreen: boolean): Verdict {
  if (!enough) {
    return {
      word: "too_little_data",
      weakest: "savings_rate",
      line: "Noch zu wenig abgeschlossene Monate.",
      parts: [
        { figure: "savings_rate", state: "missing", no_loan: false, ...TEXTS },
        { figure: "reserve", state: "missing", no_loan: false, ...TEXTS },
        { figure: "debt_service_ratio", state: "missing", no_loan: false, ...TEXTS },
      ],
      todos: [TOO_FEW],
    };
  }
  return {
    word: "good",
    weakest: "reserve",
    line: "Am knappsten: die Reserve.",
    parts: [
      {
        figure: "savings_rate",
        state: ownSavingsGreen ? "ok" : "good",
        value: "0.2500",
        good: ownSavingsGreen ? "0.30" : "0.10",
        strained: "0",
        good_origin: ownSavingsGreen ? "household" : "default",
        strained_origin: "default",
        origin: ownSavingsGreen ? "household" : "default",
        no_loan: false,
        ...TEXTS,
      },
      {
        figure: "reserve",
        state: "ok",
        amount: "12000.00",
        months: "3.08",
        target: "23400.00",
        target_months: "6",
        strained: "0.5",
        strained_amount: "11700.00",
        strained_origin: "default",
        origin: "default",
        no_loan: false,
        ...TEXTS,
      },
      {
        figure: "debt_service_ratio",
        state: "good",
        value: "0.1538",
        good: "0.30",
        strained: "0.40",
        good_origin: "default",
        strained_origin: "default",
        origin: "default",
        no_loan: false,
        ...TEXTS,
      },
    ],
    todos: [],
  };
}

function health(window: string[], options: { coloured?: boolean } = {}): Health {
  const enough = window.length >= 3;
  return {
    as_of: "2026-11-18",
    window: { months: window, enough },
    savings_rate: enough
      ? {
          saved: "3900.00",
          income: "15600.00",
          one_off_spending: "0.00",
          principal: "0.00",
          rate: "0.2500",
          rate_regular: "0.2500",
          rate_without_principal: "0.2500",
          one_offs_left_out: true,
          months: window.map((month) => ({ month, rate: "0.2500" })),
        }
      : null,
    reserve: enough
      ? {
          accounts: [{ account_id: SAVINGS, kind: "bank_savings", balance: "12000.00", as_of: "2026-10-31", origin: "default" }],
          amount: "12000.00",
          need: "3900.00",
          months: "3.08",
          target_months: "6",
          target: "23400.00",
          gap: "11400.00",
          state: "below",
        }
      : null,
    debt: enough
      ? {
          properties: [
            { property_id: FLAT, loan_balance: "200000.00", estimate: "300000.00", estimate_source: "purchase_price", ltv: "0.6667" },
          ],
          liabilities: "200000.00",
          gross_assets: "312000.00",
          debt_ratio: "0.6410",
          interest: "1500.00",
          principal: "900.00",
          debt_service_year: "9600.00",
          income_year: "62400.00",
          debt_service_ratio: "0.1538",
          debt_to_income: "3.2051",
          months: 3,
        }
      : null,
    marks: {
      savings_rate: {
        figure: "savings_rate",
        colour: options.coloured ? "green" : null,
        red: options.coloured ? "0" : null,
        origin: options.coloured ? "source" : null,
        texts: [],
      },
    },
    proposals: [{ figure: "savings_rate", direction: "higher", red: { value: "0", origin: "source" } }],
    todos: enough
      ? options.coloured
        ? []
        : [{ code: "bands_unconfirmed", text: "Eure Ampel-Grenzen fehlen.", action: "Grenzen festlegen", href: "/" }]
      : [TOO_FEW],
    verdict: verdictOf(enough, options.coloured ?? false),
  };
}

function scenario(id: string, body: HouseholdScenarioCreate): HouseholdScenario {
  return { ...body, id, workspace_id: WORKSPACE, created_at: "2026-11-18T09:00:00Z", created_by: null };
}

function plan(window: string[], scenarioId: string | null, depot: string | null): HouseholdPlan {
  const last = window[window.length - 1] ?? null;
  return {
    as_of: "2026-11-18",
    scenario_id: scenarioId,
    levers: {
      reserve_target_months: "6",
      buffer_percent: "10",
      horizon_months: 60,
      net_worth_years: 10,
      value_change: { depot, crypto: null },
    },
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
    months: [],
    projection:
      depot === null
        ? { points: [], todos: [] }
        : {
            start: total(`${last}-28`, "20000.00"),
            points: [total("2027-09-30", "35000.00"), total("2028-09-30", "51000.00")],
            assumptions: [{ kind: "depot_rate", value: depot, scenario_id: scenarioId ?? "", created_at: "2026-11-18T09:00:00Z" }],
            todos: [],
          },
    todos:
      depot === null
        ? [
            {
              code: "rate_missing",
              text: "Für die Rechnung zu eurem Vermögen fehlt eure Annahme zur Wertänderung.",
              action: "Annahme eintragen",
              href: "/planning?tab=haushalt",
            },
          ]
        : [],
  };
}

function total(on: string, amount: string): components["schemas"]["ProjectionPoint"] {
  return {
    on,
    bank: amount,
    depot: "0.00",
    crypto: "0.00",
    property: "0.00",
    loans: "0.00",
    other_liabilities: "0.00",
    total: amount,
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

/** The scenario history and its writes: each POST becomes the default in force; `initial` is the
 * history before the test. */
async function scenarios(page: Page, initial: HouseholdScenario[] = []): Promise<HouseholdScenarioCreate[]> {
  const posted: HouseholdScenarioCreate[] = [];
  const rows: HouseholdScenario[] = [...initial];
  await page.route(
    (url) => url.pathname === "/api/household-scenarios",
    async (route: Route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as HouseholdScenarioCreate;
        posted.push(body);
        const row = scenario(`00000000-0000-4000-8000-00000000091${posted.length}`, body);
        rows.push(row);
        return route.fulfill({ status: 201, json: row });
      }
      return route.fulfill({ json: rows });
    },
  );
  return posted;
}

async function monthsOf(page: Page, views: (from: string, to: string) => HealthMonthView[]): Promise<void> {
  await serve(page, "/api/health/months", (url) =>
    views(url.searchParams.get("from") ?? "", url.searchParams.get("to") ?? ""),
  );
}

test("the three figures carry their dots on the standard limits, and „Grenzen ändern“ stores the household's own", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  // A band stored for the Beleihungsauslauf before it left the screen: kept by every save.
  const saved = scenario("00000000-0000-4000-8000-000000000900", {
    name: "Standard",
    is_default: true,
    withdrawn: false,
    supersedes_id: null,
    levers: {
      reserve_target_months: "6",
      buffer_percent: "10",
      horizon_months: 60,
      net_worth_years: 10,
      value_change: { depot: null, crypto: null },
      bands: [{ figure: "ltv", green: "0.60", red: "0.80" }],
    },
  });
  const posted = await scenarios(page, [saved]);
  await serve(page, "/api/health", () => health(window, { coloured: posted.length > 0 }));
  await serve(page, "/api/health/net-worth-series", () => []);
  // The card sits inside Haushalt › Monat, which waits for the month's own reads.
  await monthsOf(page, (from, to) => window.filter((month) => month >= from && month <= to).map((month) => monthView(month, true)));
  // Monat waits for GET /household-budget too, whose real read computes the household plan and
  // ran past the 10 s wait with four workers on one box; a failed read only leaves the Budget tile
  // out, which this test does not look at.
  await page.route(
    (url) => url.pathname === "/api/household-budget",
    (route: Route) => route.fulfill({ status: 404, json: { detail: "not mocked" } }),
  );

  await page.goto("/household");
  // „Finanzielle Gesundheit“ on Haushalt › Monat (mockup 19g): the word, the period and whose
  // limits, and the three figures as small cards with their green limit.
  const figures = page.getByRole("region", { name: "Finanzielle Gesundheit" });
  await expect(figures).toContainText("Gut");
  await expect(figures).toContainText("Standardgrenzen");
  const minis = figures.locator("a.health-mini");
  await expect(minis).toHaveCount(3);
  const rate = minis.filter({ hasText: "Sparquote Ø 3 Mon." });
  await expect(rate).toContainText("25,0 %");
  await expect(rate).toHaveAttribute("data-state", "good");
  await expect(rate).toContainText("ab 10,0 % grün");
  const reserve = minis.filter({ hasText: "Reserve" });
  await expect(reserve).toHaveAttribute("data-state", "ok");
  await expect(reserve).toContainText("3,1 Mon.");
  await expect(reserve).toContainText("ab 6 Mon. grün");
  const loans = minis.filter({ hasText: "Kreditraten" });
  await expect(loans).toHaveAttribute("data-state", "good");
  await expect(loans).toContainText("bis 30,0 % grün");
  for (const gone of ["Beleihungsauslauf", "Verschuldungsgrad", "Schulden zu Jahreseinkommen"]) {
    await expect(figures.getByText(gone)).toHaveCount(0);
  }
  // Nothing waits for the limits: the sheet opens only on „Grenzen ändern“.
  await expect(page.getByText("Eure Ampel-Grenzen fehlen.")).toHaveCount(0);
  const bands = page.getByRole("region", { name: "Ampel-Grenzen festlegen" });
  await expect(bands).toHaveCount(0);
  await figures.getByRole("button", { name: "Grenzen ändern" }).click();
  await expect(bands).toBeVisible();
  await expect(bands.getByRole("group")).toHaveCount(3);
  const savings = bands.getByRole("group", { name: "Sparquote" });
  const green = savings.getByLabel("Grün ab (%)");
  await expect(green).toHaveValue("");
  await expect(savings).toContainText(/leer: Standard-Grenze 10/);
  await green.fill("30");
  await bands.getByRole("button", { name: "Übernehmen" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.is_default).toBe(true);
  const stored = posted[0]?.levers?.bands ?? [];
  expect(stored.map((band) => [band.figure, band.green == null ? null : Number(band.green), band.red == null ? null : Number(band.red)])).toEqual([
    ["savings_rate", 0.3, null],
    ["ltv", 0.6, 0.8],
  ]);

  await expect(bands).toHaveCount(0);
  await expect(rate).toHaveAttribute("data-state", "ok");
  await expect(rate).toContainText("ab 30,0 % grün");
  await expect(figures).toContainText("eure Grenzen");
});

test("„Übernehmen“ on Haushalt keeps the default's budget targets and class moves", async ({ page, signedIn }) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  const saved = scenario("00000000-0000-4000-8000-000000000900", {
    name: "Standard",
    is_default: true,
    withdrawn: false,
    supersedes_id: null,
    levers: {
      reserve_target_months: "6",
      buffer_percent: "10",
      horizon_months: 60,
      net_worth_years: 10,
      value_change: { depot: null, crypto: null },
      budget_targets: { needs_max: "0.50", wants_max: "0.30", saving_min: "0.20", housing_max: null },
      budget_class_overrides: { hh_groceries: "want" },
    },
  });
  const posted = await scenarios(page, [saved]);
  await serve(page, "/api/health", () => health(window, { coloured: posted.length > 0 }));
  await serve(page, "/api/health/net-worth-series", () => []);

  await page.goto("/household");
  await page.getByRole("region", { name: "Finanzielle Gesundheit" }).getByRole("button", { name: "Grenzen ändern" }).click();
  await page.getByRole("region", { name: "Ampel-Grenzen festlegen" }).getByRole("button", { name: "Übernehmen" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.supersedes_id).toBe(saved.id);
  expect(posted[0]?.levers?.budget_targets).toEqual({ needs_max: "0.50", wants_max: "0.30", saving_min: "0.20", housing_max: null });
  expect(posted[0]?.levers?.budget_class_overrides).toEqual({ hh_groceries: "want" });
});

test("the rate fields start empty, and the projection appears only after a rate is saved", async ({ page, signedIn }) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  const posted = await scenarios(page);
  await serve(page, "/api/health", () => health(window));
  await monthsOf(page, () => window.map((month) => monthView(month, true)));
  await serve(page, "/api/household-plan", () => {
    const last = posted[posted.length - 1];
    return plan(window, last ? `scenario-${posted.length}` : null, last?.levers?.value_change?.depot ?? null);
  });

  // Planung › Vermögen's old link lands on the card on Anlagen › Übersicht (phase 10 §7.9 (a)).
  await page.goto("/planning?section=vermoegen");
  await expect(page).toHaveURL("/assets#annahme");
  const card = page.getByRole("region", { name: "Vermögen in einigen Jahren: eure Annahme" });
  await expect(card.getByText("Annahme fehlt")).toBeVisible();
  // The server's to-do href (`/planning?tab=haushalt`, rules/plan) opens the rates beside the
  // card itself (§7.10 (a) 7, D299: the panel „Annahmen“ is gone).
  const enter = card.getByRole("link", { name: "Annahme eintragen" });
  await expect(enter).toHaveAttribute("href", "#rendite");
  await expect(card.getByText(/nach eurer Annahme:/)).toHaveCount(0);
  await enter.click();

  const form = card.getByRole("form", { name: "Rendite ändern" });
  await expect(form).toBeVisible();
  const depot = form.getByLabel("Fonds und Wertpapiere (% pro Jahr)");
  const crypto = form.getByLabel("Krypto (% pro Jahr)");
  for (const field of [depot, crypto]) {
    await expect(field).toHaveValue("");
    await expect(field).not.toHaveAttribute("placeholder");
  }
  // Nothing typed yet: nothing to save.
  await expect(form.getByRole("button", { name: "Speichern" })).toBeDisabled();

  // An entry the field cannot take: no save until it is fixed (review C).
  await depot.fill("60");
  await expect(form.getByText("Zwischen −50 % und 50 % pro Jahr.")).toBeVisible();
  await expect(form.getByRole("button", { name: "Speichern" })).toBeDisabled();
  expect(posted).toHaveLength(0);
  await depot.fill("5");
  await form.getByRole("button", { name: "Speichern" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(Number(posted[0]?.levers?.value_change?.depot)).toBe(0.05);
  expect(posted[0]?.levers?.value_change?.crypto ?? null).toBeNull();
  // Saved: the form closes and says where the old version went.
  await expect(form).toHaveCount(0);
  await expect(card.getByText(/Gespeichert als neue Fassung eures Plans/)).toBeVisible();

  await expect(card.getByText(/nach eurer Annahme: 51\.000 €/)).toBeVisible();
  await expect(card.getByText("Annahme fehlt")).toHaveCount(0);
});

test("below three closed months both screens show what holds the next month open, with working buttons", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const window = [monthsBack(3)];
  const shown = monthsBack(2);
  const monthEnd: HealthTodo = {
    code: "month_end_missing",
    text: "Der Monatsabschluss fehlt.",
    action: "Monatsabschluss festhalten",
    href: "/?month_end=2026-10-31",
  };
  const snapshots: unknown[] = [];
  await page.route(
    (url) => url.pathname === "/api/net-worth/snapshots",
    (route: Route) => {
      snapshots.push(route.request().postDataJSON());
      const result: SnapshotResult = { outcome: "written", as_of: "2026-10-31", unvalued_positions: 0 };
      return route.fulfill({ status: 201, json: result });
    },
  );
  await scenarios(page);
  await serve(page, "/api/health", () => health(window));
  await monthsOf(page, (from, to) =>
    [...new Set([...window, shown])]
      .filter((month) => month >= from && month <= to)
      .map((month) =>
        // GET /health/months writes the month-end to-do's link to Anlagen (phase 10 §7.6); the
        // plan's (rules/plan) keeps `/?month_end=`. Both are the button.
        month === shown ? monthView(month, false, [{ ...monthEnd, href: "/assets?month_end=2026-10-31" }]) : monthView(month, true),
      ),
  );
  await serve(page, "/api/household-plan", () => ({ ...plan(window, null, null), surplus: null, todos: [TOO_FEW, monthEnd] }));

  // Haushalt › Budget (12c; Planung › Haushalt until phase 10 §7.9 (a), whose old link lands
  // there): no figure, the to-dos instead.
  await page.goto("/planning?tab=haushalt");
  await expect(page).toHaveURL("/household?tab=budget");
  const card = page.getByRole("region", { name: "Noch zu wenige abgeschlossene Monate" });
  await expect(card).toContainText("1 von 3 Monaten");
  await expect(page.getByText("Anlegbar pro Monat")).toHaveCount(0);
  await card.getByRole("button", { name: "Monatsabschluss festhalten" }).click();
  await expect.poll(() => snapshots).toEqual([{ as_of: "2026-10-31" }]);

  // Haushalt: the month from ?monat=, no averages, the to-dos of the month after the window.
  await page.goto(`/household?monat=${shown}`);
  await expect(page.getByRole("combobox", { name: "Monat" })).toHaveValue(shown);
  const averages = page.getByRole("region", { name: "Noch keine Durchschnitte" });
  await expect(averages.getByRole("button", { name: "Monatsabschluss festhalten" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Einnahmen" })).toContainText("Ø 1 Monat –");
  await averages.getByRole("link", { name: "Zu prüfen öffnen" }).click();
  await expect(page).toHaveURL("/inbox");
});

test("every figure opens what it is made of, and a month opens Haushalt with its averages, the flat and its closing", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  const first = window[0] ?? "";
  await scenarios(page);
  await serve(page, "/api/health", () => health(window));
  await serve(page, "/api/health/net-worth-series", () => []);
  // The first month: the flat lost 250 € on the rental account, and its stored closing differs.
  await monthsOf(page, (from, to) =>
    window
      .filter((month) => month >= from && month <= to)
      .map((month) => {
        const view = monthView(month, true);
        if (month !== first) return view;
        return {
          ...view,
          household: { ...view.household, rental_result: "-250.00" },
          snapshot_id: "00000000-0000-4000-8000-000000000904",
          stored: householdMonth(month, "5000.00", "3900.00"),
        };
      }),
  );
  await serve(page, "/api/household-plan", () => plan(window, null, null));

  // Haushalt › Monat's „Finanzielle Gesundheit“ (mockup 19g): a small card opens its side panel
  // with what it is made of and its green, amber and red ranges; the savings rate's months each
  // link to that month on Haushalt.
  await page.goto("/household");
  const minis = page.getByRole("region", { name: "Finanzielle Gesundheit" }).locator("a.health-mini");
  await minis.filter({ hasText: "Sparquote" }).click();
  await expect(page).toHaveURL(/#d-kennzahl-savings_rate$/);
  const rate = page.getByRole("complementary", { name: "Sparquote Ø 3 Monate" });
  await expect(rate.getByRole("heading", { name: "Woraus sie besteht" })).toBeVisible();
  const madeOf = rate.locator(".household-rows").first().getByRole("listitem");
  await expect(madeOf.nth(0)).toContainText("Einnahmen");
  await expect(madeOf.nth(0)).toContainText("15.600");
  await expect(madeOf.nth(1)).toContainText("Gespart");
  await expect(madeOf.nth(1)).toContainText("3.900");
  await expect(madeOf.nth(2)).toContainText("Gespart ÷ Einnahmen");
  await expect(madeOf.nth(2)).toContainText("25,0 %");
  const ranges = rate.locator(".health-ranges li");
  await expect(ranges).toHaveCount(3);
  await expect(ranges.nth(0)).toContainText("Grün");
  await expect(ranges.nth(0)).toContainText("ab 10,0 %");
  await expect(ranges.nth(1)).toContainText("Gelb");
  await expect(ranges.nth(2)).toContainText("Rot");
  await expect(rate).toContainText("Standardgrenzen, bis ihr eigene festlegt.");
  // GET /health names no month since which one-offs stay out, so there is no note.
  await expect(rate.locator(".health-one-offs")).toHaveCount(0);
  await rate.getByText("Monate ansehen").click();
  for (const month of window) {
    await expect(rate.locator(`a[href="/household?monat=${month}"]`)).toBeVisible();
  }
  // The reserve and the loans' figure: each opens its rows and links to where they come from.
  await minis.filter({ hasText: "Reserve" }).click();
  const reserve = page.getByRole("complementary", { name: "Liquiditätsreserve" });
  await reserve.getByText("Woraus die Reserve besteht").click();
  await expect(reserve.locator(`a[href="/accounts/${SAVINGS}"]`)).toBeVisible();
  // „Konten der Reserve ändern“ opens the reserve's own form beside its figure on Haushalt ›
  // Reserve, from another tab (phase 10 §7.10 (a) 7).
  await reserve.getByRole("link", { name: "Konten der Reserve ändern" }).click();
  await expect(page).toHaveURL("/household?tab=reserve#reserve-ziel");
  await expect(page.getByRole("form", { name: "Reserve-Ziel ändern" })).toBeVisible();
  await page.goto("/household");
  await minis.filter({ hasText: "Kreditraten" }).click();
  const debtService = page.getByRole("complementary", { name: "Schuldendienstquote" });
  await debtService.getByText("Woraus die Zahl besteht").click();
  // The property's Planung is Anlagen › Immobilie since phase 10 §7.6.
  await expect(debtService.locator('details a[href="/assets?tab=immobilie"]')).toBeVisible();

  // „Was übrig bleibt · im Monat“ at the foot of Haushalt › Budget (mockup 19h; Planung › Anlegen
  // until phase 10 §7.9 (a), whose old link lands there): each row of „So entsteht die Zahl“ that
  // has months opens them.
  await page.goto("/planning?section=anlegen");
  await expect(page).toHaveURL("/household?tab=budget#was-mit-dem-rest");
  const rest = page.getByRole("region", { name: "Was übrig bleibt · im Monat" });
  await rest.getByRole("button", { name: "Wie gerechnet ›" }).click();
  const made = rest.getByRole("region", { name: "So entsteht die Zahl" });
  const rows = made.locator("details");
  await expect(rows).toHaveCount(3); // mean income, mean regular spending, free cash; the buffer and the result have none
  for (const row of await rows.all()) {
    await row.locator("summary").click();
    for (const month of window) {
      await expect(row.locator(`a[href="/household?monat=${month}"]`)).toBeVisible();
    }
  }
  // A month opens Haushalt on that month: the means over the 3 closed months with their basis, and
  // the chip of a month changed since its closing (the income card went with mockup 19g).
  await rows.first().locator(`a[href="/household?monat=${first}"]`).click();
  await expect(page).toHaveURL(`/household?monat=${first}`);
  await expect(page.getByRole("combobox", { name: "Monat" })).toHaveValue(first);
  await expect(page.getByRole("group", { name: "Einnahmen" })).toContainText("Ø 3 Monate");
  await expect(page.getByText("Basis der Durchschnitte: 3 abgeschlossene Monate")).toBeVisible();
  await expect(page.getByRole("region", { name: "Noch keine Durchschnitte" })).toHaveCount(0);
  // The chip opens both values in the detail panel (mockup 19).
  await page.getByRole("link", { name: /seit dem Abschluss geändert/ }).click();
  const chip = page.getByRole("complementary", { name: "Seit dem Abschluss geändert" });
  await expect(chip.getByRole("row", { name: /Einnahmen mit der Wohnung/ })).toContainText("5.000");
  await expect(chip.getByRole("row", { name: /Einnahmen mit der Wohnung/ })).toContainText("5.200");
});

test("on the phone, Haushalt › Monat's tiles and a figure's side panel fit, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  await scenarios(page);
  await serve(page, "/api/health", () => health(window));
  await serve(page, "/api/health/net-worth-series", () => []);
  await monthsOf(page, (from, to) => window.filter((month) => month >= from && month <= to).map((month) => monthView(month, true)));
  // The Budget tile's real read computes the plan; a failed read leaves the tile out.
  await page.route(
    (url) => url.pathname === "/api/household-budget",
    (route: Route) => route.fulfill({ status: 404, json: { detail: "not mocked" } }),
  );

  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("about:blank");
    await page.goto(`/household?monat=${monthsBack(2)}`);
    await expect(page.getByRole("group", { name: "Einnahmen" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Finanzielle Gesundheit" }).locator("a.health-mini")).toHaveCount(3);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: test.info().outputPath(`monat-${scheme}.png`), fullPage: true });

    // A small card's panel slides in as a dialog and fits too.
    await page.getByRole("region", { name: "Finanzielle Gesundheit" }).locator("a.health-mini", { hasText: "Reserve" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.locator(".health-ranges li")).toHaveCount(3);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await page.screenshot({ path: test.info().outputPath(`monat-reserve-${scheme}.png`) });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
});

test("the month-end card, the split and the net worth over time are on Anlagen, and an old link lands there", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  const snapshots: unknown[] = [];
  await page.route(
    (url) => url.pathname === "/api/net-worth/snapshots",
    (route: Route) => {
      if (route.request().method() !== "POST") return route.fallback();
      snapshots.push(route.request().postDataJSON());
      const result: SnapshotResult = { outcome: "written", as_of: "2026-10-31", unvalued_positions: 0 };
      return route.fulfill({ status: 201, json: result });
    },
  );
  await scenarios(page);
  await serve(page, "/api/health", () => ({
    ...health(window),
    split: { property: "0.9615", bank: "0.0385" },
    series: [{ month_end: "2026-09-30", total: "112000.00", change: "1300.00", saved: "1300.00", other: "0.00" }],
  }));

  // A month-end to-do's old link on Übersicht opens the card on Anlagen › Übersicht.
  await page.goto("/?month_end=2026-10-31");
  await expect(page).toHaveURL("/assets?month_end=2026-10-31");
  const card = page.getByRole("region", { name: /^Monatsabschluss zum / });
  await card.getByRole("button", { name: "Monatsabschluss festhalten" }).click();
  await expect.poll(() => snapshots).toEqual([{ as_of: "2026-10-31" }]);

  // „Vermögen im Verlauf“ follows the positions and leaves Übersicht (the total card with its
  // split is covered by assets-tabs.spec.ts).
  await page.goto("/assets");
  await expect(page.getByRole("region", { name: "Vermögen im Verlauf" })).toContainText("112.000");
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Vermögen im Verlauf" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Liquiditätsreserve" })).toHaveCount(0);
});

test("„Was diesen Monat anders ist“ names the server's groups and opens their payments", async ({ page, signedIn }) => {
  void signedIn;
  const window = [monthsBack(5), monthsBack(4), monthsBack(3), monthsBack(2)];
  const newest = window[window.length - 1] ?? "";
  const payment = (id: string, day: string, payee: string, amount: string): components["schemas"]["GroupPayment"] => ({
    allocation_id: `00000000-0000-4000-8000-00000000092${id}`,
    transaction_id: `00000000-0000-4000-8000-00000000093${id}`,
    account_id: SAVINGS,
    account_label: "Girokonto A",
    booked_on: `${newest}-${day}`,
    payee,
    purpose: "",
    category: "hh_restaurant",
    amount,
  });
  let differences: components["schemas"]["MonthDifferences"] | null = {
    month: newest,
    baseline: window.slice(0, -1),
    groups: [
      {
        group: "dining",
        amount: "180.00",
        mean: "25.00",
        difference: "155.00",
        words: "more",
        payments: [payment("1", "10", "Pizzeria Beispiel", "120.00"), payment("2", "11", "Café Muster", "60.00")],
      },
      { group: "mobility", amount: "20.00", mean: "100.00", difference: "-80.00", words: "less", payments: [] },
    ],
  };
  await scenarios(page);
  await serve(page, "/api/health", () => ({ ...health(window), differences }));
  await serve(page, "/api/health/net-worth-series", () => []);
  await monthsOf(page, (from, to) => window.filter((month) => month >= from && month <= to).map((month) => monthView(month, true)));
  await serve(page, "/api/household-plan", () => plan(window, null, null));

  await page.goto("/household");
  const card = page.getByRole("region", { name: "Was diesen Monat anders ist" });
  await expect(card).toContainText("verglichen mit dem Schnitt der 3 abgeschlossenen Monate davor");
  await expect(card.getByRole("listitem")).toHaveCount(2);
  await expect(card.getByRole("listitem").nth(0)).toContainText("Essen gehen & bestellen: 155,00 € mehr als üblich");
  await expect(card.getByRole("listitem").nth(1)).toContainText("Mobilität: 80,00 € weniger als üblich");

  // A group opens its month, its mean and the payments behind the month in the panel.
  await card.getByRole("link", { name: /Essen gehen & bestellen/ }).click();
  const panel = page.getByRole("complementary", { name: "Essen gehen & bestellen" });
  await expect(panel).toContainText("Ø 3 Monate davor");
  await expect(panel).toContainText("25,00 €");
  await expect(panel.getByRole("listitem").filter({ hasText: "Pizzeria Beispiel" })).toContainText("120,00 €");
  await expect(panel.getByRole("listitem").filter({ hasText: "Café Muster" })).toContainText("Girokonto A");

  // No card when the server names no group, or compares nothing.
  differences = { month: newest, baseline: window.slice(0, -1), groups: [] };
  await page.goto("/household");
  await expect(page.getByRole("region", { name: "Ausgaben nach Kategorie" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Was diesen Monat anders ist" })).toHaveCount(0);
  differences = null;
  await page.goto("/household");
  await expect(page.getByRole("region", { name: "Ausgaben nach Kategorie" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Was diesen Monat anders ist" })).toHaveCount(0);
});

test("the Budget tile on Haushalt › Monat: „N von 3“ with a dot per class, coloured as on Haushalt › Budget", async ({ page, signedIn }) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  type BudgetView = components["schemas"]["BudgetView"];
  const mark = (colour: "green" | "amber", target: string, gapPoints: string, gapMonthly: string, atTarget: string) => ({
    colour,
    target,
    gap_points: gapPoints,
    gap_monthly: gapMonthly,
    at_target: atTarget,
    text: "",
  });
  let targets = true;
  const budget = (): BudgetView => ({
    as_of: "2026-11-18",
    scenario_id: null,
    window: { months: window, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    lines: [
      {
        key: "need",
        monthly: "2600.00",
        total: "7800.00",
        share: "0.5000",
        codes: [],
        target: targets ? "45.0" : null,
        mark: targets ? mark("amber", "45.0", "5.0", "260.00", "2340.00") : null,
      },
      {
        key: "want",
        monthly: "1300.00",
        total: "3900.00",
        share: "0.2500",
        codes: [],
        target: targets ? "30.0" : null,
        mark: targets ? mark("green", "30.0", "-5.0", "-260.00", "1560.00") : null,
      },
      {
        key: "saving",
        monthly: "1300.00",
        total: "3900.00",
        share: "0.2500",
        target: targets ? "20.0" : null,
        mark: targets ? mark("green", "20.0", "5.0", "260.00", "1040.00") : null,
      },
    ],
    housing: null,
    targets: targets ? { needs_max: "45.0", wants_max: "30.0", saving_min: "20.0" } : {},
    references: [],
    todos: [],
  });
  await scenarios(page);
  await serve(page, "/api/health", () => health(window));
  await serve(page, "/api/health/net-worth-series", () => []);
  await monthsOf(page, (from, to) => window.filter((month) => month >= from && month <= to).map((month) => monthView(month, true)));
  await serve(page, "/api/household-plan", () => plan(window, null, null));
  await serve(page, "/api/household-budget", budget);

  await page.goto("/household");
  // Two of the three classes meet their target; Notwendiges misses it by the server's 260 €.
  const tile = page.getByRole("link", { name: /^Budget: 2 von 3 Zielen erreicht/ });
  await expect(tile).toHaveAttribute("href", "/household?tab=budget");
  await expect(tile).toContainText("2 von 3");
  const classes = tile.getByRole("listitem");
  await expect(classes).toHaveCount(3);
  await expect(classes.nth(0)).toContainText(/Notwendiges \+260\s€/);
  await expect(classes.nth(1)).toHaveText(/^\s*Wünsche$/);
  await expect(classes.nth(2)).toHaveText(/^\s*Sparen$/);
  await expect(tile.locator(".budget-dot-amber")).toHaveCount(1);
  await expect(tile.locator(".budget-dot-green")).toHaveCount(2);
  await expect(tile.locator(".budget-dot-red")).toHaveCount(0);
  // „Ohne Kategorie“ is no longer a tile; the Budget took its place.
  await expect(page.getByRole("group", { name: "Ohne Kategorie" })).toHaveCount(0);

  // Before a target is saved the tile says so and still leads to the Budget.
  targets = false;
  await page.goto("/household");
  await expect(page.getByRole("region", { name: "Ausgaben nach Kategorie" })).toBeVisible();
  const empty = page.getByRole("link", { name: /^Budget: noch keine Ziele/ });
  await expect(empty).toContainText("noch keine Ziele");
  await expect(empty.locator(".budget-dot")).toHaveCount(0);
});
