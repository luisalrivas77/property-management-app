// Planung (phase-4.md §9, §11 step 3): the screen shows the projection the API computes, a
// lever (Wertsteigerung) changes only the URL and the screen recalculates through the API,
// and "Szenario speichern" writes a scenario row with the levers in force. A property whose
// purchase year has no rule set gets a plain notice instead of figures. Synthetic data only.
import { apiCall, expect, test } from "./support";

type Projection = { scenario: { sale_year: number; value_growth: string }; sale: { value: string } };
type Scenario = { name: string; levers: { value_growth: string } };

const address = { street: "Teststraße", house_number: "7", postal_code: "10115", city: "Berlin", country_code: "DE" };

/** "365698.33" → "365.698,33 €", the way the screen formats money (format.ts formatMoney). */
function euro(value: string): string {
  const [int = "", frac = ""] = value.replace(/^-/, "").split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${value.startsWith("-") ? "-" : ""}${grouped},${frac.padEnd(2, "0")} €`;
}

test("the projection renders, a lever recalculates and a scenario is saved", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Planung",
    kind: "etw",
    address,
    build_year: 1994,
    notarized_on: "2026-03-10",
    purchase_date: "2026-07-01",
    purchase_price: "300000.00",
  });
  await apiCall(page, "POST", "/api/loans", {
    property_id: property.id,
    lender: "Sparkasse Musterstadt",
    principal: "240000.00",
    nominal_rate: "0.0365",
    monthly_payment: "1130.00",
    disbursed_on: "2026-06-15",
    first_payment_on: "2026-07-30",
    fixed_until: "2034-04-30",
  });
  const projectionPath = `/api/properties/${property.id}/performance/projection`;
  const before = await apiCall<Projection>(page, "GET", projectionPath);
  const saleYear = before.scenario.sale_year;

  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Planung" }).click();
  await expect(page).toHaveURL("/planning");
  await expect(page.getByRole("heading", { name: "Rechnet sich die Immobilie?" })).toBeVisible();
  await expect(page.getByRole("group", { name: `Verkaufspreis ${saleYear}` })).toContainText(euro(before.sale.value));
  await expect(page.getByRole("group", { name: "Wertsteigerung pro Jahr" })).toContainText("2,0 %");
  // No household income yet: the tax effect asks for it instead of guessing.
  await expect(page.getByRole("link", { name: "Einkommen eintragen" }).first()).toHaveAttribute("href", "/owners");
  await expect(page.getByText("Eine Planungsschätzung, keine Steuer- oder Anlageberatung.")).toBeVisible();

  // One step up on Wertsteigerung: only the URL changes, the API recalculates.
  await page.getByRole("button", { name: "Wertsteigerung erhöhen" }).click();
  await expect(page).toHaveURL(/wertsteigerung=0\.025/);
  await expect(page.getByRole("group", { name: "Wertsteigerung pro Jahr" })).toContainText("2,5 %");
  const after = await apiCall<Projection>(page, "GET", `${projectionPath}?value_growth=0.025`);
  expect(after.sale.value).not.toBe(before.sale.value);
  await expect(page.getByRole("group", { name: `Verkaufspreis ${saleYear}` })).toContainText(euro(after.sale.value));
  await expect(page.getByText("Annahmen geändert, nicht gespeichert.")).toBeVisible();

  // Saving records the levers in force as a scenario row.
  await page.getByRole("button", { name: "Szenario speichern" }).click();
  const dialog = page.getByRole("dialog", { name: "Szenario speichern" });
  await dialog.getByLabel("Name").fill("Mehr Wertsteigerung");
  await dialog.getByRole("button", { name: "Speichern" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Szenario „Mehr Wertsteigerung“ gespeichert.")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Mehr Wertsteigerung" })).toContainText("2,5 %");
  const scenarios = await apiCall<Scenario[]>(page, "GET", `/api/properties/${property.id}/scenarios`);
  expect(scenarios).toHaveLength(1);
  expect(scenarios[0]?.name).toBe("Mehr Wertsteigerung");
  expect(scenarios[0]?.levers.value_growth).toBe("0.025");

  // A second property bought in a year without a rule set: a notice, no figures.
  await apiCall(page, "POST", "/api/properties", {
    label: "Wohnung Altbestand",
    kind: "etw",
    address: { ...address, house_number: "9" },
    build_year: 1972,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "210000.00",
  });
  await page.goto("/planning");
  await page.getByRole("combobox", { name: /^Immobilie/ }).selectOption({ label: "Wohnung Altbestand" });
  await expect(page.getByText(/Für das Kaufjahr dieser Immobilie gibt es noch kein Regelwerk\./)).toBeVisible();
  await expect(page.getByRole("group", { name: /^Verkaufspreis / })).toHaveCount(0);
});
