// The Unterlagen tab (docs/phases/phase-1.md, spec amendment 3): a property with only its
// Kaufvertrag linked shows that row as present, with a link to the document, and the
// Grundsteuerbescheid as missing, with a link to where it is fixed.
import { apiCall, expect, test } from "./support";

test("the Unterlagen tab shows a present and a missing item", async ({ page, signedIn }) => {
  void signedIn;
  const origin = new URL(page.url()).origin;
  // Setup through the API: a flat bought on 1 May 2024 and its Kaufvertrag, typed and linked.
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Unterlagen",
    kind: "etw",
    address: { street: "Teststraße", house_number: "3", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  const upload = await page.request.post("/api/documents", {
    multipart: { file: { name: "Kaufvertrag.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% Kaufvertrag\n%%EOF\n") } },
    headers: { Origin: origin },
  });
  expect(upload.status()).toBe(201);
  const document = (await upload.json()) as { id: string };
  const typed = await page.request.fetch(`/api/documents/${document.id}/typing`, {
    method: "PUT",
    data: { typing: { type: "contract", subtype: "purchase_contract", document_date: "2024-03-15" } },
    headers: { Origin: origin },
  });
  expect(typed.status()).toBe(200);
  await apiCall(page, "POST", `/api/documents/${document.id}/links`, { target_type: "property", target_id: property.id });

  await page.goto(`/properties/${property.id}`);
  await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Unterlagen" }).click();
  await expect(page).toHaveURL(`/properties/${property.id}?tab=unterlagen`);
  await expect(page.getByRole("heading", { name: "Unterlagen für die Steuererklärung" })).toBeVisible();

  const contract = page.getByRole("listitem").filter({ hasText: "Grundlage für AfA und Spekulationsfrist" });
  await expect(contract).toContainText("Kaufvertrag: vorhanden");
  await expect(contract.getByRole("link", { name: "Kaufvertrag · 15.03.2024" })).toHaveAttribute("href", `/documents/${document.id}`);

  const grundsteuer = page.getByRole("listitem").filter({ hasText: "Grundsteuerbescheid" });
  await expect(grundsteuer).toContainText("Grundsteuerbescheid: fehlt");
  await expect(grundsteuer.getByRole("link", { name: "Unter Dokumente verknüpfen" })).toHaveAttribute(
    "href",
    `/properties/${property.id}?tab=dokumente`,
  );

  // The purchase year is offered too, and the Kaufvertrag counts there as well.
  await page.getByLabel("Steuerjahr").selectOption("2024");
  await expect(page.getByRole("heading", { name: "Unterlagen für die Steuererklärung" })).toBeVisible();
  await expect(contract).toContainText("Kaufvertrag: vorhanden");
  await expect(page.getByText("Planungshilfe, keine Steuerberatung.")).toBeVisible();
});
