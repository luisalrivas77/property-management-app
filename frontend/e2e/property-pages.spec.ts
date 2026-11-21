// The property, loan, unit, tenancy and tenant pages as drawn in mockup 25 (phase 10 step 4b):
// a wide table's row keeps its label and amount and opens the rest in the detail panel, an edit
// form slides in from the right and sends the same body as before, and the pages read in English.
// Invented owners and tenants only (the repo is public).
import type { Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

async function propertyWithLoan(page: Page) {
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Seitenansicht",
    kind: "etw",
    address: { street: "Teststraße", house_number: "7", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2026-03-15",
    purchase_date: "2026-05-01",
    purchase_price: "300000.00",
  });
  const loan = await apiCall<{ id: string }>(page, "POST", "/api/loans", {
    property_id: property.id,
    lender: "Sparkasse Musterstadt",
    principal: "240000.00",
    nominal_rate: "0.0365",
    monthly_payment: "1130.00",
    disbursed_on: "2026-06-15",
    first_payment_on: "2026-07-30",
    fixed_until: "2034-04-30",
  });
  return { property, loan };
}

test("a loan's instalment opens its interest and repayment in the panel, on a phone with no sideways scroll", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { loan } = await propertyWithLoan(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/loans/${loan.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Darlehen Sparkasse Musterstadt" })).toBeVisible();

  await page.getByText(/^Alle \d+ Raten$/).click();
  const rates = page.getByRole("list", { name: "Tilgungsplan" });
  const first = rates.getByRole("link").first();
  // The row keeps the due date and the payment only.
  await expect(first).toContainText("30.07.2026");
  await expect(first).toContainText("1.130,00 €");
  await expect(first).not.toContainText("Zinsen");
  await first.click();

  const panel = page.getByRole("dialog", { name: /^Tilgungsplan · Rate 1 von \d+$/ });
  await expect(panel).toBeVisible();
  for (const label of ["Zinsen", "Tilgung", "Restschuld vorher", "Restschuld nachher", "Zinssatz · Zinstage"]) {
    await expect(panel.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(panel.getByText("240.000,00 €", { exact: true })).toBeVisible(); // the balance before the first instalment
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await panel.getByRole("button", { name: "Schließen" }).click();
  await expect(panel).toHaveCount(0);
});

test("„Bearbeiten“ slides the loan form in and saves the same PATCH body as the edit page did", async ({ page, signedIn }) => {
  void signedIn;
  const { loan } = await propertyWithLoan(page);
  await page.goto(`/loans/${loan.id}`);
  await page.getByRole("button", { name: "Bearbeiten", exact: true }).click();
  const sheet = page.getByRole("dialog", { name: "Darlehen bearbeiten" });
  await expect(sheet).toBeVisible();
  await sheet.getByLabel("Nominalzins (% p. a.)").fill("3,65");
  await sheet.getByLabel("Rate (€ / Monat)").fill("1.200");

  // The body is read where the request is intercepted: on the request event Chromium had no
  // postData for this PATCH (openapi-fetch sends a Request object), so waitForRequest matched it
  // with a null body.
  const bodies: string[] = [];
  await page.route(`**/api/loans/${loan.id}`, async (route) => {
    if (route.request().method() === "PATCH") bodies.push(route.request().postDataBuffer()?.toString("utf8") ?? "");
    await route.continue();
  });
  await sheet.getByRole("button", { name: "Speichern" }).click();
  await expect(sheet).toHaveCount(0);
  // LoanForm's edit body, in its own key order, unchanged by phase 10.
  expect(bodies).toEqual([
    JSON.stringify({
      lender: "Sparkasse Musterstadt",
      contract_number: null,
      first_payment_on: "2026-07-30",
      fixed_until: "2034-04-30",
      interest_only_before_first_payment: false,
      nominal_rate: "0.0365",
      monthly_payment: "1200.00",
      follow_up_rate: null,
    }),
  ]);
  // The page reloads the loan: the new instalment shows without leaving it.
  await expect(page).toHaveURL(new RegExp(`/loans/${loan.id}$`));
  await expect(page.getByText("1.200,00 € / Monat")).toBeVisible();
});

test("the tenant page reads in English: contact facts, the tenancy as a row and its panel, the edit sheet", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { property } = await propertyWithLoan(page);
  const unit = await apiCall<{ id: string }>(page, "POST", "/api/units", { property_id: property.id, label: "Zimmer 2", floor_area_m2: "14.00" });
  const tenant = await apiCall<{ id: string }>(page, "POST", "/api/tenants", { full_name: "Mieterin B", email: "b@example.org" });
  const tenancy = await apiCall<{ id: string }>(page, "POST", "/api/tenancies", {
    unit_id: unit.id,
    parties: [{ tenant_id: tenant.id, role: "primary" }],
    start_date: "2026-05-01",
    rent_model: "fixed",
    operating_costs_terms: "flat_rate",
  });

  await page.goto("/settings#d-setting-language");
  await page.getByRole("radio", { name: "English" }).check();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  await page.goto(`/tenants/${tenant.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Mieterin B" })).toBeVisible();
  await expect(page.getByText("Email", { exact: true })).toBeVisible();
  await expect(page.getByText("b@example.org")).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Tenancies" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Erase data" })).toBeVisible();

  const row = page.getByRole("list", { name: "Tenancies" }).getByRole("link").filter({ hasText: "Zimmer 2" });
  await expect(row).toContainText("Wohnung Seitenansicht");
  await expect(row).toContainText(" · from ");
  await row.click();
  const panel = page.getByRole("complementary", { name: "Zimmer 2" });
  await expect(panel.getByText("Move-in", { exact: true })).toBeVisible();
  await expect(panel.getByRole("link", { name: "Wohnung Seitenansicht" })).toHaveAttribute("href", `/properties/${property.id}`);
  await expect(panel.locator(`a[href="/tenancies/${tenancy.id}"]`)).toBeVisible();

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const sheet = page.getByRole("dialog", { name: "Edit tenant" });
  await expect(sheet.getByLabel("Email (optional)")).toHaveValue("b@example.org");
  await expect(sheet.getByRole("button", { name: "Save" })).toBeVisible();
});
