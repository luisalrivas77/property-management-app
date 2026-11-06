// Nebenkosten after the fact: a row saved without its Beleg gets it through "Bearbeiten"
// (PATCH /acquisition-costs/{id}), which the Unterlagen checklist then counts; a row entered
// by mistake is removed with "Entfernen" and its checklist item goes with it.
import { acceptNextConfirm, apiCall, expect, test } from "./support";

test("a Nebenkosten row is edited to attach its Beleg, then removed", async ({ page, signedIn }) => {
  void signedIn;
  const origin = new URL(page.url()).origin;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Nebenkosten",
    kind: "etw",
    address: { street: "Teststraße", house_number: "5", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  await apiCall(page, "POST", "/api/acquisition-costs", { property_id: property.id, kind: "notary", amount: "2100.00" });
  const upload = await page.request.post("/api/documents", {
    multipart: { file: { name: "Notarrechnung.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% Notarrechnung\n%%EOF\n") } },
    headers: { Origin: origin },
  });
  expect(upload.status()).toBe(201);
  const document = (await upload.json()) as { id: string };
  const typed = await page.request.fetch(`/api/documents/${document.id}/typing`, {
    method: "PUT",
    data: { typing: { type: "invoice", subtype: "cost_basis_evidence", title: "Notarrechnung" } },
    headers: { Origin: origin },
  });
  expect(typed.status()).toBe(200);

  await page.goto(`/properties/${property.id}?tab=nebenkosten`);
  const row = page.getByRole("row").filter({ hasText: "Notar" });
  await expect(row).toContainText("2.100,00 €");

  await row.getByRole("button", { name: "Bearbeiten" }).click();
  const dialog = page.getByRole("dialog", { name: "Nebenkosten bearbeiten" });
  await expect(dialog.getByLabel("Betrag (€)")).toHaveValue("2100,00");
  await dialog.getByLabel("Betrag (€)").fill("2100,50");
  await dialog.getByLabel("Beleg").selectOption({ label: "Notarrechnung (Rechnung)" });
  await dialog.getByRole("button", { name: "Speichern" }).click();
  await expect(dialog).toBeHidden();
  await expect(row).toContainText("2.100,50 €");
  await expect(row.getByRole("link", { name: "Notarrechnung" })).toHaveAttribute("href", `/documents/${document.id}`);

  // The Unterlagen checklist reads the row's Beleg.
  await page.goto(`/properties/${property.id}?tab=unterlagen`);
  const item = page.getByRole("listitem").filter({ hasText: "Beleg Notar" });
  await expect(item).toContainText("vorhanden");
  await expect(item.getByRole("link", { name: /^Nachweis Anschaffungskosten/ })).toHaveAttribute("href", `/documents/${document.id}`);

  // Removing the row (after a confirm) drops it, and its checklist item with it.
  await page.goto(`/properties/${property.id}?tab=nebenkosten`);
  acceptNextConfirm(page);
  await page.getByRole("row").filter({ hasText: "Notar" }).getByRole("button", { name: "Entfernen" }).click();
  await expect(page.getByText("Noch keine Nebenkosten erfasst.")).toBeVisible();
  await page.goto(`/properties/${property.id}?tab=unterlagen`);
  await expect(page.getByRole("heading", { name: "Unterlagen für die Steuererklärung" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Beleg Notar" })).toHaveCount(0);
});
