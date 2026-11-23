// The Steuer-Checkliste (docs/phases/phase-10.md §7.6 (f)): like every to-do list since wave 1,
// a missing paper shows its title, its hint and its button, and what to do waits behind a closed
// „Warum?“. A fresh flat without a Kaufpreisaufteilung, through the real API. Synthetic data only.
import { apiCall, expect, test } from "./support";

const WHAT_TO_DO = "Festlegen, welcher Teil des Kaufpreises auf das Gebäude entfällt.";

test("a missing paper keeps its title and button in view and its Was tun behind „Warum?“", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Checkliste",
    kind: "etw",
    address: { street: "Teststraße", house_number: "7", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });

  await page.goto(`/properties/${property.id}?tab=unterlagen`);
  await expect(page.getByRole("heading", { name: "Unterlagen für die Steuererklärung" })).toBeVisible();
  const item = page.getByRole("listitem").filter({ hasText: "Kaufpreisaufteilung" }).first();
  await expect(item).toContainText("Gebäudeanteil als AfA-Basis");
  await expect(item.getByText("Warum?")).toBeVisible();
  // Closed by default: the text is in the page but not shown, and no „Was tun:“ label any more.
  await expect(item.getByText(WHAT_TO_DO)).toBeHidden();
  await expect(item).not.toContainText("Was tun:");

  await item.getByText("Warum?").click();
  await expect(item.getByText(WHAT_TO_DO)).toBeVisible();
});
