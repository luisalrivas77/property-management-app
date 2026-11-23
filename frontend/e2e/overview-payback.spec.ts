// Übersicht's property tile (docs/phases/phase-10.md §7.6 (d)): „Zurück bisher“ is the money that
// has arrived, and the tax of the years without their Einkommensteuerbescheid stands beside it as
// the expected refund (or back-payment), the percent counting both. /api/properties/payback is
// answered by the browser-side mock below; the figures are invented sample data.
import type { Page } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type PropertiesPayback = components["schemas"]["PropertiesPayback"];
type PaybackYear = components["schemas"]["PaybackYear"];

const PROPERTY_ID = "00000000-0000-4000-8000-0000000000d1";

/** 2025 with its Bescheid: 150.00 before tax + 500.00 refund, arrived. 2026 so far: 0.00 before
 * tax and the expected tax (700.00 refund, or 200.00 back-payment). */
function payback(expected: string): PropertiesPayback {
  const later = Number(expected);
  const cashBack = (650 + later).toFixed(2);
  const percent = ((650 + later) / 100).toFixed(1);
  const years: PaybackYear[] = [
    { year: 2025, kind: "actual", net_cash: "650.00", cumulative: "650.00", cash_before_tax: "150.00", tax_refund: "500.00", tax_assessed: true },
    { year: 2026, kind: "actual_partial", net_cash: expected, cumulative: cashBack, cash_before_tax: "0.00", tax_refund: expected, tax_assessed: false },
  ];
  return {
    properties: [
      {
        property_id: PROPERTY_ID,
        label: "Wohnung A",
        paid_in: "10000.00",
        cash_back: cashBack,
        arrived: "650.00",
        expected_tax_refund: expected,
        percent,
        years,
        break_even_year: null,
        sale_year: 2040,
        tax_estimated_years: [2026],
        income_missing_years: [],
        sale_today: {
          value: "300000.00",
          selling_costs_rate: "0.0357",
          selling_costs: "10710.00",
          taxable: false,
          sale_tax: "0.00",
          tax_not_computed: false,
          loan_balance: "250000.00",
          net_proceeds: "39290.00",
          profit: (39290 + 650 + later - 10000).toFixed(2),
        },
      },
    ],
    paid_in: "10000.00",
    cash_back: cashBack,
    arrived: "650.00",
    expected_tax_refund: expected,
    percent,
    profit_today: (39290 + 650 + later - 10000).toFixed(2),
    break_even_year: null,
    sale_year: 2040,
    tax_estimated_years: [2026],
    income_missing_years: [],
  };
}

async function mockPayback(page: Page, body: PropertiesPayback): Promise<void> {
  await page.route("**/api/properties/payback", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: body }) : route.fallback(),
  );
}

test("the property tile: the money arrived, and the expected refund with the percent of both", async ({ page, signedIn }) => {
  void signedIn;
  await mockPayback(page, payback("700.00"));
  await page.goto("/");
  const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /^Immobilie/ });
  await expect(tile.locator(".tile-value")).toHaveText("650,00 €");
  await expect(tile.locator(".tile-sub")).toHaveText("+ 700,00 € erwartete Steuererstattung · 13,5 % mit Erstattung");

  await tile.click();
  const panel = page.getByRole("dialog", { name: "Immobilie" }).or(page.getByRole("complementary", { name: "Immobilie" }));
  const row = (label: string) => panel.locator(".tile-panel-rows > div").filter({ has: page.getByText(label, { exact: true }) }).first();
  await expect(row("Zurück bisher").locator("dd")).toHaveText("650,00 €");
  await expect(row("+ erwartete Steuererstattung").locator("dd")).toHaveText("700,00 €");
  await expect(row("= mit erwarteter Erstattung").locator("dd")).toHaveText("1.350,00 €");
  await expect(row("2025").locator("dd")).toHaveText("150,00 € vor Steuern · Steuer +500,00 €");
  await expect(row("2026 (bis heute)").locator("dd")).toHaveText("0,00 € vor Steuern · Steuer +700,00 € erwartet");
  await expect(row("+ Zurück bisher, mit erwarteter Steuer").locator("dd")).toHaveText("1.350,00 €");
  await expect(panel).toContainText("Der Prozentsatz und „Eigenkapital zurück“ rechnen die erwartete Steuer mit.");
});

test("the property tile: an expected back-payment is named as one", async ({ page, signedIn }) => {
  void signedIn;
  await mockPayback(page, payback("-200.00"));
  await page.goto("/");
  const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /^Immobilie/ });
  await expect(tile.locator(".tile-value")).toHaveText("650,00 €");
  await expect(tile.locator(".tile-sub")).toHaveText("− 200,00 € erwartete Nachzahlung · 4,5 % mit Nachzahlung");
});

test("the property tile: nothing expected keeps the line against the own money", async ({ page, signedIn }) => {
  void signedIn;
  await mockPayback(page, payback("0.00"));
  await page.goto("/");
  const tile = page.getByRole("region", { name: "Auf einen Blick" }).getByRole("link", { name: /^Immobilie/ });
  await expect(tile.locator(".tile-value")).toHaveText("650,00 €");
  await expect(tile.locator(".tile-sub")).toHaveText("zurück von 10.000,00 € Eigenkapital · 6,5 %");
});
