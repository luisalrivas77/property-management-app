// The Nebenkostenpauschale rise test (docs/phases/phase-4.md D155): a flat_rate room whose
// Grundsteuer rose by 22 € a month since its Pauschale was set shows the card on Planung, the
// to-do „… kann steigen“ on Übersicht, a link that opens „Mietänderung“ pre-filled with the
// backend's new Pauschale, and the letter downloaded as a PDF; once that change is saved (from
// a later month), the to-do is gone and the card says the change is recorded. Synthetic data
// only.
import { apiCall, expect, test } from "./support";

const address = { street: "Teststraße", house_number: "11", postal_code: "10115", city: "Berlin", country_code: "DE" };

function schedule(propertyId: string, documentId: string, year: number, quarter: string) {
  return {
    property_id: propertyId,
    kind: "grundsteuer_schedule",
    tax_year: year,
    document_id: documentId,
    value: {
      annual_amount: `${4 * Number(quarter)}.00`,
      instalments: ["02", "05", "08", "11"].map((month) => ({ due_on: `${year}-${month}-15`, amount: `${quarter}.00` })),
      rhythm: "quarterly",
    },
  };
}

test("a Grundsteuer rise shows the card, the to-do, the pre-filled change and the letter", async ({ page, signedIn }) => {
  void signedIn;
  const origin = new URL(page.url()).origin;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Pauschale",
    kind: "etw",
    address,
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  const unit = await apiCall<{ id: string }>(page, "POST", "/api/units", { property_id: property.id, label: "Zimmer 1", floor_area_m2: "14.00" });
  const tenant = await apiCall<{ id: string }>(page, "POST", "/api/tenants", { full_name: "Mieterin Beispiel" });
  const tenancy = await apiCall<{ id: string }>(page, "POST", "/api/tenancies", {
    unit_id: unit.id,
    parties: [{ tenant_id: tenant.id, role: "primary" }],
    start_date: "2024-07-01",
    rent_model: "fixed",
    operating_costs_terms: "flat_rate",
  });
  await apiCall(page, "POST", "/api/rent-schedule", {
    tenancy_id: tenancy.id,
    effective_from: "2024-07-01",
    cold_rent: "400.00",
    operating_costs_prepayment: "100.00",
    reason: "initial",
  });
  const upload = await page.request.post("/api/documents", {
    multipart: { file: { name: "Grundsteuer.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% Grundsteuer\n%%EOF\n") } },
    headers: { Origin: origin },
  });
  expect(upload.status()).toBe(201);
  const document = (await upload.json()) as { id: string };
  const inputs = `/api/properties/${property.id}/tax-inputs`;
  await apiCall(page, "POST", inputs, schedule(property.id, document.id, 2024, "120")); // 40,00 € a month
  await apiCall(page, "POST", inputs, schedule(property.id, document.id, 2025, "186")); // 62,00 € a month

  // Planung: the card, above the actuals, with the room's verdict.
  await page.goto(`/planning?immobilie=${property.id}`);
  const card = page.getByRole("region", { name: "Nebenkostenpauschalen" });
  await expect(card).toBeVisible();
  await expect(card.getByRole("row").filter({ hasText: "Grundsteuer" }).first()).toContainText("+22,00 €");
  await expect(card.getByRole("row").filter({ hasText: "Zimmer 1" })).toContainText("kann steigen");

  // Übersicht: the to-do with its link to the pre-filled „Mietänderung“.
  await page.goto("/");
  const todo = page.getByRole("listitem").filter({ hasText: "Nebenkostenpauschale Zimmer 1 kann steigen" });
  await expect(todo).toContainText("+22,00 € pro Monat");
  await todo.getByRole("link", { name: "Mietänderung" }).click();
  await expect(page).toHaveURL(`/tenancies/${tenancy.id}?mietaenderung=pauschale`);
  const change = page.getByRole("dialog", { name: "Mietänderung erfassen" });
  await expect(change.getByLabel("Nebenkostenpauschale (€ / Monat)")).toHaveValue("122,00");
  await expect(change.getByLabel("Kaltmiete (€ / Monat)")).toHaveValue("400,00");
  await expect(change.getByLabel("Gültig ab")).not.toHaveValue("");
  await change.getByRole("button", { name: "Schließen" }).click();

  // The letter: the preview names the new Pauschale, the PDF is downloaded.
  await page.getByRole("button", { name: "Schreiben erstellen" }).click();
  const letter = page.getByRole("dialog", { name: "Schreiben an den Mieter" });
  await expect(letter).toContainText("100,00 € → 122,00 €");
  await letter.getByLabel("Absender", { exact: true }).fill("Eigentümer A");
  await letter.getByLabel("Anschrift des Absenders").fill("Musterallee 1\n12345 Musterstadt");
  const download = page.waitForEvent("download");
  await letter.getByRole("button", { name: "PDF erstellen" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.pdf$/);

  // Saving the pre-filled change records it from a later month: from then on the room has no
  // to-do and no letter, and the card names the recorded change.
  await page.goto(`/tenancies/${tenancy.id}?mietaenderung=pauschale`);
  const save = page.getByRole("dialog", { name: "Mietänderung erfassen" });
  await expect(save.getByLabel("Nebenkostenpauschale (€ / Monat)")).toHaveValue("122,00");
  await save.getByRole("button", { name: "Speichern" }).click();
  await expect(save).toBeHidden(); // closed once the row is saved

  await page.goto(`/planning?immobilie=${property.id}`);
  await page.reload();
  const room = card.getByRole("row").filter({ hasText: "Zimmer 1" });
  await expect(room).toContainText(/Änderung ab \d{2}\.\d{2}\.\d{4} erfasst \(122,00 €\)/);
  await expect(room).not.toContainText("kann steigen");

  await page.goto("/");
  const overview = page.getByRole("region", { name: "Was noch fehlt" });
  // The list is loaded once the property's other to-dos are in (it loads all at once).
  await expect(
    overview.getByRole("listitem").filter({ hasText: "Gebäudeanteil am Kaufpreis fehlt" }).filter({ hasText: "Wohnung Pauschale" }),
  ).toBeVisible();
  await expect(overview.getByRole("listitem").filter({ hasText: "Nebenkostenpauschale Zimmer 1 kann steigen" })).toHaveCount(0);
});
