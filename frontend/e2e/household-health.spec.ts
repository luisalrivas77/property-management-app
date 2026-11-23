// The household's key figures and plan (docs/phases/phase-9.md §14, step 3; mockups 12, 12b,
// 12c, 13, 13b). The reads of phase 9 (GET /health, /health/months, /household-plan) and the
// scenario writes are mocked, since a workspace with three closed months needs a full ledger;
// every other read is the real API of a fresh workspace. Synthetic figures only.
//
// - No key figure has a colour before the household's limits are confirmed with „Übernehmen“
//   (D250).
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

test("no key figure has a colour before „Übernehmen“, and the confirmed limits colour it", async ({ page, signedIn }) => {
  void signedIn;
  const window = [monthsBack(4), monthsBack(3), monthsBack(2)];
  const posted = await scenarios(page);
  await serve(page, "/api/health", () => health(window, { coloured: posted.length > 0 }));
  await serve(page, "/api/health/net-worth-series", () => []);

  await page.goto("/household");
  const figure = page.locator('div.health-figure[aria-label="Sparquote"]');
  await expect(figure).toContainText("25,0 %");
  await expect(page.locator(".health-mark")).toHaveCount(0);
  await expect(page.getByText("ohne Farben, bis ihr eure Grenzen übernehmt")).toBeVisible();

  const bands = page.getByRole("region", { name: "Ampel-Grenzen festlegen" });
  await bands.getByRole("button", { name: "Übernehmen" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.is_default).toBe(true);
  const bands0 = posted[0]?.levers?.bands ?? [];
  expect(bands0.map((band) => [band.figure, band.green ?? null, Number(band.red)])).toEqual([["savings_rate", null, 0]]);

  await expect(figure.locator(".health-mark")).toHaveText("Grün");
  await expect(page.getByText("Eure Ampel-Grenzen sind übernommen.")).toBeVisible();
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

  await page.goto("/planning?section=vermoegen");
  const card = page.getByRole("region", { name: "Vermögen in einigen Jahren: eure Annahme" });
  const depot = card.getByLabel("Fonds und Wertpapiere (% pro Jahr)");
  const crypto = card.getByLabel("Krypto (% pro Jahr)");
  for (const field of [depot, crypto]) {
    await expect(field).toHaveValue("");
    await expect(field).not.toHaveAttribute("placeholder");
  }
  await expect(card.getByText("Annahme fehlt")).toBeVisible();
  await expect(card.getByRole("link", { name: "Annahme eintragen" })).toHaveAttribute("href", "/planning?section=vermoegen#annahme");
  await expect(card.getByText(/nach eurer Annahme:/)).toHaveCount(0);

  await depot.fill("5");
  await expect(page.getByText("Geändert, noch nicht gespeichert")).toBeVisible();
  // An entry the field cannot take keeps the draft's 5 %: no save until it is fixed (review C).
  await depot.fill("60");
  await expect(card.getByText("Zwischen −50 % und 50 % pro Jahr.")).toBeVisible();
  await expect(card.getByRole("button", { name: "Szenario speichern" })).toBeDisabled();
  expect(posted).toHaveLength(0);
  await depot.fill("5");
  await card.getByRole("button", { name: "Szenario speichern" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(Number(posted[0]?.levers?.value_change?.depot)).toBe(0.05);
  expect(posted[0]?.levers?.value_change?.crypto ?? null).toBeNull();

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

  // Planung › Haushalt (12c): no figure, the to-dos instead.
  await page.goto("/planning?tab=haushalt");
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

  // Haushalt's Kennzahlen: the savings rate's months, each a link to that month on Haushalt.
  await page.goto("/household");
  const rate = page.locator('div.health-figure[aria-label="Sparquote"]');
  await rate.getByText("Monate ansehen").click();
  for (const month of window) {
    await expect(rate.locator(`a[href="/household?monat=${month}"]`)).toBeVisible();
  }
  // The reserve and the four debt figures: each opens its rows and links to where they come from.
  const reserve = page.getByRole("region", { name: "Liquiditätsreserve" });
  await reserve.getByText("Woraus die Reserve besteht").click();
  await expect(reserve.locator(`a[href="/accounts/${SAVINGS}"]`)).toBeVisible();
  const links: Record<string, string> = {
    Schuldendienstquote: "/planning",
    Beleihungsauslauf: `/properties/${FLAT}`,
    Verschuldungsgrad: "/assets",
    "Schulden zu Jahreseinkommen": "/loans",
  };
  for (const [title, href] of Object.entries(links)) {
    const figure = page.locator(`div.health-figure[aria-label="${title}"]`);
    await figure.getByText("Woraus die Zahl besteht").click();
    await expect(figure.locator(`details a[href="${href}"]`)).toBeVisible();
  }

  // Planung › Haushalt: each row of „So entsteht die Zahl“ that has months opens them.
  await page.goto("/planning?section=anlegen");
  const made = page.getByRole("region", { name: "So entsteht die Zahl" });
  const rows = made.locator("details");
  await expect(rows).toHaveCount(3); // mean income, mean regular spending, free cash; the buffer and the result have none
  for (const row of await rows.all()) {
    await row.locator("summary").click();
    for (const month of window) {
      await expect(row.locator(`a[href="/household?monat=${month}"]`)).toBeVisible();
    }
  }
  // A month opens Haushalt on that month: the means over the 3 closed months with their basis,
  // the flat's result as an income row, and the chip of a month changed since its closing.
  await rows.first().locator(`a[href="/household?monat=${first}"]`).click();
  await expect(page).toHaveURL(`/household?monat=${first}`);
  await expect(page.getByRole("combobox", { name: "Monat" })).toHaveValue(first);
  await expect(page.getByRole("group", { name: "Einnahmen" })).toContainText("Ø 3 Monate");
  await expect(page.getByText("Basis der Durchschnitte: 3 abgeschlossene Monate")).toBeVisible();
  await expect(page.getByRole("region", { name: "Noch keine Durchschnitte" })).toHaveCount(0);
  const income = page.getByRole("region", { name: "Einnahmen" });
  await expect(income.getByRole("listitem").filter({ hasText: "Wohnung (Ergebnis des Mietkontos)" })).toContainText("250");
  // The chip opens both values in the detail panel (mockup 19).
  await page.getByRole("link", { name: /seit dem Abschluss geändert/ }).click();
  const chip = page.getByRole("complementary", { name: "Seit dem Abschluss geändert" });
  await expect(chip.getByRole("row", { name: /Einnahmen mit der Wohnung/ })).toContainText("5.000");
  await expect(chip.getByRole("row", { name: /Einnahmen mit der Wohnung/ })).toContainText("5.200");
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

  // The total (with the split inside it) and „Vermögen im Verlauf“ follow the positions, and leave Übersicht.
  await page.goto("/assets");
  await expect(page.getByRole("region", { name: "Vermögen in Anlagen" })).toBeVisible(); // §7.6: the split sits inside the total
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

test("the Budget in one line on Haushalt: only once a target is saved, coloured as on Planung › Budget", async ({ page, signedIn }) => {
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
      { key: "one_off", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
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
  const line = page.getByRole("region", { name: "Budget", exact: true });
  await expect(line).toContainText("Ø je Monat über 3 abgeschlossene Monate");
  await expect(line).toContainText("Notwendiges 2.600 € von 2.340 €");
  await expect(line).toContainText("Wünsche 1.300 € von 1.560 €");
  await expect(line).toContainText("Sparen 1.300 € von mindestens 1.040 €");
  await expect(line.getByRole("img", { name: "Ziel verfehlt" })).toHaveCount(1);
  await expect(line.getByRole("img", { name: "Ziel erreicht" })).toHaveCount(2);
  await expect(line.locator(".budget-dot-amber")).toHaveCount(1);
  await expect(line.locator(".budget-dot-green")).toHaveCount(2);
  await expect(line.locator(".budget-dot-red")).toHaveCount(0);
  await expect(line.getByRole("link", { name: /Planung › Budget/ })).toHaveAttribute("href", "/planning?section=budget");

  // Before a target is saved there is no line.
  targets = false;
  await page.goto("/household");
  await expect(page.getByRole("region", { name: "Ausgaben nach Kategorie" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Budget", exact: true })).toHaveCount(0);
});
