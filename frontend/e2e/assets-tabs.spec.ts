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
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type AssetsOverview = components["schemas"]["AssetsOverview"];
type AssetsPerformance = components["schemas"]["AssetsPerformance"];
type PortfolioFigures = components["schemas"]["PortfolioFigures"];
type PropertiesPayback = components["schemas"]["PropertiesPayback"];
type EtfToday = components["schemas"]["EtfToday"];
type CryptoHoldings = components["schemas"]["CryptoHoldings"];
type NetWorthPoint = components["schemas"]["NetWorthPoint"];

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
  await page.goto("/planning?tab=haushalt#ausgaben-durchspielen");
  await expect(page).toHaveURL("/household?tab=budget#d-annahmen-ausgaben");
});
