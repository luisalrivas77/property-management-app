// Anlagen in four tabs (docs/phases/phase-10.md §7.6, mockup 20d): Übersicht, ETF, Krypto and
// Immobilie, each portfolio with its gain since the start. The reads of the holdings and of the
// gain (GET /assets/overview, /assets/performance, /assets/etf, /holdings/crypto,
// /properties/payback, /health) are mocked, since a workspace with a depot, an exchange and a
// flat needs a full ledger; every other read is the real API of a fresh workspace. Invented
// figures and names only.
//
// - Übersicht: the total large with the split of the assets inside it, no card „Aufteilung des
//   Vermögens“, no fund or coin listed, a card per kind that opens its tab, the Immobilie card
//   with „X von Y eigenem Geld zurück“ (Luis, 23 Nov 2026).
// - At phone width (390 × 844) Anlagen, „Vermögen im Verlauf“ included, has no sideways scroll.
// - ETF: „ETF gesamt“, the gain since the start, a card per depot opening its payments; no
//   Sparer-Pauschbetrag or Vorabpauschale card.
// - Krypto: the total with the gain since the start, „Heute steuerfrei verkaufbar“ opening the
//   tax-free lots, a card per exchange, „Bestandsnachweis 31.12.“ as a button at the foot.
// - Planung's old Immobilie links land on Anlagen › Immobilie with their levers.
// - „Für euer Ziel“ (§7.9 (e) 3) sits after the gain card when the household follows a route
//   (GET /household-goal/tracking mocked), with no sideways scroll on a phone, light and dark.
// - „Wertzuwachs fürs Ziel“ (§7.9 (e) items 1, 2) on Anlagen › Immobilie for a property the goal's
//   walk sells (GET /household-goal/properties mocked; the property is real), its windows and
//   source in the side panel, no sideways scroll on a phone, light and dark.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { apiCall, expect, test } from "./support";

type AssetsOverview = components["schemas"]["AssetsOverview"];
type AssetsPerformance = components["schemas"]["AssetsPerformance"];
type PortfolioFigures = components["schemas"]["PortfolioFigures"];
type PropertiesPayback = components["schemas"]["PropertiesPayback"];
type EtfToday = components["schemas"]["EtfToday"];
type CryptoHoldings = components["schemas"]["CryptoHoldings"];
type NetWorthPoint = components["schemas"]["NetWorthPoint"];
type GoalTracking = components["schemas"]["GoalTracking"];
type GoalProperties = components["schemas"]["GoalProperties"];

const DEPOT = "00000000-0000-4000-8000-000000000a01";
const EXCHANGE = "00000000-0000-4000-8000-000000000a02";
const FUND = "00000000-0000-4000-8000-000000000a03";
const FLAT = "00000000-0000-4000-8000-000000000a04";
const ON = "2026-11-20";

function figures(value: string, paidIn: string, gain: string, ratio: string, yearly: string, start: string): PortfolioFigures {
  return {
    start,
    value_eur: value,
    paid_in_eur: paidIn,
    taken_out_eur: "0.00",
    gain_eur: gain,
    gain_ratio: ratio,
    yearly_return: yearly,
    no_rate: null,
    unpriced_flows: 0,
    unvalued_positions: 0,
  };
}

const performance: AssetsPerformance = {
  as_of: ON,
  portfolios: [
    {
      account_id: DEPOT,
      label: "Depot Beispiel",
      kind: "depot",
      figures: figures("24000.00", "20000.00", "4000.00", "0.2", "0.05", "2023-01-16"),
      flows: [{ on: "2023-01-16", amount_eur: "20000.00", kind: "transfer", internal: true }],
      unbooked_lines: 0,
      waiting: false,
    },
    {
      account_id: EXCHANGE,
      label: "Börse Beispiel",
      kind: "exchange",
      figures: figures("5000.00", "4000.00", "1000.00", "0.25", "0.08", "2023-03-01"),
      flows: [{ on: "2023-03-01", amount_eur: "4000.00", kind: "transfer", internal: true }],
      unbooked_lines: 0,
      waiting: false,
    },
  ],
  etf: figures("24000.00", "20000.00", "4000.00", "0.2", "0.05", "2023-01-16"),
  crypto: figures("5000.00", "4000.00", "1000.00", "0.25", "0.08", "2023-03-01"),
  invested: figures("29000.00", "24000.00", "5000.00", "0.2083", "0.055", "2023-01-16"),
  property_estimate_eur: "300000.00",
  property_loans_eur: "200000.00",
  property_equity_eur: "100000.00",
  total_eur: "129000.00",
  distributions_year: 2026,
  distributions_eur: "120.00",
  distributions_tax_eur: "0.00",
};

const overview: AssetsOverview = {
  on: ON,
  accounts: [
    {
      account_id: DEPOT,
      label: "Depot Beispiel",
      kind: "depot",
      counts_to_reserve: false,
      positions: [
        {
          asset_id: FUND,
          kind: "etf",
          name: "Testfonds Welt",
          units: "100",
          value_eur: "24000.00",
          stale: false,
          from_report: false,
        },
      ],
      value_eur: "24000.00",
      cash: [],
      total_eur: "24000.00",
    },
  ],
  // The invested value of GET /assets/performance: the overview's total, cash included.
  total_eur: "29000.00",
  by_kind: { etf: "24000.00", crypto: "4000.00", cash: "1000.00" },
  stale_positions: 0,
  unvalued_positions: 0,
  waiting: [],
};

const payback: PropertiesPayback = {
  properties: [{ property_id: FLAT, label: "Wohnung Beispiel", paid_in: "60000.00", cash_back: "6000.00" }],
  paid_in: "60000.00",
  cash_back: "6000.00",
  arrived: "6000.00",
  expected_tax_refund: "0.00",
  profit_today: "0.00",
};

const etf: EtfToday = {
  on: ON,
  accounts: [{ account_id: DEPOT, label: "Depot Beispiel", positions: [], value_eur: "24000.00", cash: [], total_eur: "24000.00" }],
  total_eur: "24000.00",
  documents: [],
};

const crypto: CryptoHoldings = {
  on: ON,
  total_eur: "5000.00",
  unvalued_coins: 0,
  unrealized_eur: "1000.00",
  unrealized_excluded: 0,
  tax_free_today_eur: "0.00",
  tax_free_today_excluded: 0,
  next_tranche: null,
  accounts: [{ account_id: EXCHANGE, label: "Börse Beispiel", kind: "exchange", value_eur: "5000.00", unvalued_coins: 0 }],
  coins: [],
  unfilled_movements: 0,
  waiting: [],
};

/** Twelve month-ends of „Vermögen im Verlauf“, the longest list a phone has to fit. */
function series(): NetWorthPoint[] {
  return Array.from({ length: 12 }, (_, index) => {
    const month = new Date(Date.UTC(2025, 11 + index, 1));
    const end = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + 1, 0));
    return {
      month_end: end.toISOString().slice(0, 10),
      total: `${1120000 + index * 1300}.00`,
      change: "1300.00",
      saved: "1300.00",
      other: "0.00",
    };
  });
}

/** Serve JSON for GET requests to exactly this API path; other methods go on to the real API. */
async function serve(page: Page, path: string, body: () => unknown): Promise<void> {
  await page.route(
    (url) => url.pathname === path,
    (route: Route) => (route.request().method() === "GET" ? route.fulfill({ json: body() }) : route.fallback()),
  );
}

async function holdings(page: Page): Promise<void> {
  await serve(page, "/api/assets/overview", () => overview);
  await serve(page, "/api/assets/performance", () => performance);
  await serve(page, "/api/properties/payback", () => payback);
  await serve(page, "/api/assets/etf", () => etf);
  await serve(page, "/api/holdings/crypto", () => crypto);
  await serve(page, "/api/health", () => ({ series: series() }));
}

test("Übersicht shows the total with its split, a card per kind and no fund", async ({ page, signedIn }) => {
  void signedIn;
  await holdings(page);
  await page.goto("/assets");

  const total = page.getByRole("region", { name: "Vermögen in Anlagen" });
  await expect(total).toContainText("129.000,00 €");
  // The split sits inside the total card; the old card is gone.
  await expect(total).toContainText("Immobilie (Eigenkapital)");
  await expect(total).toContainText("100.000,00 €");
  await expect(page.getByRole("region", { name: "Aufteilung des Vermögens" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Vermögen im Verlauf" })).toBeVisible();
  // No fund and no coin is listed on Übersicht.
  await expect(page.getByText("Testfonds Welt")).toHaveCount(0);

  await expect(page.getByRole("group", { name: "ETF und Krypto: Gewinn seit Beginn" })).toContainText("5.000,00");
  await expect(page.getByRole("link", { name: "Immobilie (Eigenkapital) öffnen" })).toContainText(
    "6.000,00 € von 60.000,00 € eigenem Geld zurück",
  );
  await page.getByRole("link", { name: "ETF öffnen" }).click();
  await expect(page).toHaveURL("/assets?tab=etf");
});

test("Anlagen has no sideways scroll on a phone, „Vermögen im Verlauf“ included", async ({ page, signedIn }) => {
  void signedIn;
  await holdings(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/assets");
  const series = page.getByRole("region", { name: "Vermögen im Verlauf" });
  await expect(series).toBeVisible();
  // The months as a list on the phone; the table that scrolled sideways inside its card is hidden.
  await expect(series.getByRole("list", { name: "Vermögen im Verlauf" })).toBeVisible();
  await expect(series.getByRole("table")).toBeHidden();
  await expect(page.getByRole("region", { name: "Vermögen in Anlagen" })).toContainText("129.000,00 €");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  for (const tab of ["etf", "krypto"]) {
    await page.goto(`/assets?tab=${tab}`);
    await expect(page.getByRole("link", { name: tab === "etf" ? "Depot Beispiel" : "Börse Beispiel" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
});

test("ETF shows its total, the gain since the start and a card per depot", async ({ page, signedIn }) => {
  void signedIn;
  await holdings(page);
  await page.goto("/assets?tab=etf");

  await expect(page.getByRole("region", { name: "ETF gesamt" })).toContainText("24.000,00 €");
  await expect(page.getByRole("group", { name: "Gewinn seit Beginn" })).toContainText("4.000,00");
  await expect(page.getByRole("group", { name: "Ausschüttungen 2026" })).toContainText("120,00 €");
  await expect(page.getByText("Sparer-Pauschbetrag")).toHaveCount(0);
  await expect(page.getByText("Vorabpauschale")).toHaveCount(0);

  await page.getByRole("link", { name: "Depot Beispiel" }).click();
  const panel = page.getByRole("complementary", { name: "Depot Beispiel" });
  await expect(panel.getByRole("heading", { name: "Ein- und Auszahlungen" })).toBeVisible();
  await expect(panel).toContainText("20.000,00 €");
});

test("Krypto keeps its cards, opens the tax-free lots and has the holdings statement at its foot", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  await holdings(page);
  await page.goto("/assets?tab=krypto");

  const total = page.getByRole("group", { name: "Krypto gesamt" });
  await expect(total).toContainText("5.000,00 €");
  await expect(total).toContainText("Gewinn seit Beginn");

  await page.getByRole("link", { name: "Heute steuerfrei verkaufbar" }).click();
  await expect(page.getByRole("complementary", { name: "Steuerfrei verkaufbar" })).toBeVisible();

  await page.getByRole("link", { name: "Bestandsnachweis 31.12." }).click();
  await expect(page.getByRole("complementary", { name: "Bestandsnachweis zum Jahresende" })).toBeVisible();

  await page.getByRole("link", { name: "Börse Beispiel" }).click();
  await expect(page.getByRole("complementary", { name: "Börse Beispiel" })).toBeVisible();
});

test("Planung's old links land where their part is now, Immobilie with its levers", async ({ page, signedIn }) => {
  void signedIn;
  await page.goto("/planning?section=immobilie&wertsteigerung=0.02&verkauf=2036");
  await expect(page).toHaveURL("/assets?tab=immobilie&wertsteigerung=0.02&verkauf=2036");
  await expect(page.getByRole("link", { name: "Immobilie", exact: true })).toHaveAttribute("aria-current", "page");

  await page.goto(`/planning?immobilie=${FLAT}`);
  await expect(page).toHaveURL(`/assets?tab=immobilie&immobilie=${FLAT}`);

  // Wave 3 (a) (D288): the rest of Planung lives on Haushalt's tabs and Anlagen › Übersicht.
  await page.goto("/planning?section=vermoegen");
  await expect(page).toHaveURL("/assets#annahme");
  await page.goto("/planning?section=reserve");
  await expect(page).toHaveURL("/household?tab=reserve");
  await page.goto("/planning?section=anlegen");
  await expect(page).toHaveURL("/household?tab=budget#was-mit-dem-rest");
  await page.goto("/planning?section=budget");
  await expect(page).toHaveURL("/household?tab=budget");
  // Since §7.10 (a) (D299) the spending play is Haushalt › Durchspielen, and the rates sit on
  // Anlagen's projection card (`#rendite`).
  await page.goto("/planning?tab=haushalt#ausgaben-durchspielen");
  await expect(page).toHaveURL("/household?tab=durchspielen");
  await page.goto("/planning?section=vermoegen#annahme");
  await expect(page).toHaveURL("/assets#rendite");
});

test("„Für euer Ziel“ sits after the gain card, with no sideways scroll on a phone", async ({ page, signedIn }) => {
  void signedIn;
  await holdings(page);
  const tracking: GoalTracking = {
    shown: true,
    reason: null,
    route: "more_return",
    chosen_on: "2026-06-01",
    days: 178,
    rate: "0.061",
    planned_per_year: "6000.00",
    planned_by_now: "2926.03",
    paid_in: "3000.00",
    value_then: "20000.00",
    value_now: "23900.00",
    value_change: "900.00",
    implied_change: "620.00",
    yearly_return: null,
    no_rate: "short_history",
    word: "on_path",
    short: [],
    reserve_depots: 0,
  };
  await serve(page, "/api/household-goal/tracking", () => tracking);
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/assets");
    const card = page.getByTestId("goal-tracking");
    await expect(card).toContainText("Auf dem Pfad");
    await expect(card).toContainText("3.000,00 € von 2.926,03 € eingezahlt");
    // After the gain card, in the same row of cards.
    const gain = page.getByRole("group", { name: "ETF und Krypto: Gewinn seit Beginn" });
    const after = await gain.evaluate((node, other) => Boolean(node.compareDocumentPosition(other!) & Node.DOCUMENT_POSITION_FOLLOWING), await card.elementHandle());
    expect(after).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await card.evaluate((node) => node.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: test.info().outputPath(`goal-track-held-${scheme}.png`) });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
});

test("following the adopted path, „Für euer Ziel“ draws the growth against the growth needed, on the card and in its panel", async ({ page, signedIn }) => {
  void signedIn;
  await holdings(page);
  // The path's tracking (§7.11 (i), D311, mockup 19j frame 8): `points` per closed month-end,
  // the growth so far and the growth needed in percent points; invented figures.
  const tracking: GoalTracking = {
    shown: true,
    reason: null,
    route: "path",
    chosen_on: "2026-08-03",
    rate: "0.068",
    word: "behind",
    short: ["value"],
    reserve_depots: 0,
    rows: [],
    points: [
      { month_end: "2026-08-31", needed: "0.5", actual: "0.9", behind: false },
      { month_end: "2026-09-30", needed: "1.1", actual: "0.7", behind: true },
      { month_end: "2026-10-31", needed: "1.6", actual: "1.2", behind: true },
    ],
  };
  await serve(page, "/api/household-goal/tracking", () => tracking);
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/assets");
    const card = page.getByTestId("goal-tracking");
    await expect(card).toContainText("Dahinter");
    await expect(card).toContainText("Euer Plan");
    await expect(card.getByTestId("path-chart")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await card.evaluate((node) => node.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: test.info().outputPath(`goal-track-path-${scheme}.png`) });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
  await page.getByTestId("goal-tracking").click();
  const panel = page.getByTestId("goal-tracking-panel");
  await expect(panel.getByTestId("path-chart")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: test.info().outputPath("goal-track-path-panel.png") });
});

test("„Wertzuwachs fürs Ziel“ on Immobilie opens its windows, with no sideways scroll on a phone", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Ziel",
    kind: "etw",
    address: { street: "Teststraße", house_number: "7", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2026-03-10",
    purchase_date: "2026-07-01",
    purchase_price: "300000.00",
  });
  const growth: GoalProperties = {
    shown: true,
    reason: null,
    properties: [
      {
        property_id: property.id,
        available: true,
        reason: null,
        typed_growth: "0.020",
        needed_growth: "0.030",
        sale_year: 2036,
        window_years: 5,
        windows: 16,
        reached_windows: 10,
        median: "0.039",
        best: "0.087",
        verdict: "within_history",
        thin: false,
        source: {
          key: "house_prices",
          source: "Eurostat, Hauspreisindex (prc_hpi_q)",
          url: "https://ec.europa.eu/eurostat/databrowser/view/prc_hpi_q/default/table",
          licence: "CC BY 4.0",
          attribution: "Eurostat (prc_hpi_q), Statistisches Bundesamt",
          read_on: "2026-11-26",
          own_calculation: "Eigene Berechnung",
          first_year: 2005,
          last_year: 2025,
        },
      },
    ],
  };
  await serve(page, "/api/household-goal/properties", () => growth);
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/assets?tab=immobilie");
    const card = page.getByTestId("property-growth");
    await expect(card).toContainText("Wertzuwachs fürs Ziel");
    await expect(card).toContainText("3,0 % im Jahr");
    await expect(card).toContainText("Hauspreise: oft erreicht");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await card.evaluate((node) => node.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: test.info().outputPath(`property-growth-${scheme}.png`) });
    await card.click();
    const panel = page.getByTestId("property-growth-panel");
    await expect(panel).toContainText("10 von 16");
    await expect(panel).toContainText("Zeiträume à 5 Jahre");
    await expect(panel).toContainText("ohne Miete und Kosten");
    await expect(panel).toContainText("Eurostat (prc_hpi_q), Statistisches Bundesamt");
    await page.screenshot({ path: test.info().outputPath(`property-growth-panel-${scheme}.png`) });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
});
