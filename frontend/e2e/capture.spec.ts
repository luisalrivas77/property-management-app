// Beleg fotografieren (docs/phases/phase-2.md §9, D30): the phone capture page and the
// manifest's home-screen shortcut. The e2e API runs with its AI off (HAUSBUCH_AI_ENABLED's
// default) and without its job worker (playwright.config.ts), so an upload queues no read:
// the row says so and links to the document page, where it is typed by hand.
import { apiCall, expect, test } from "./support";

// A real 1×1 PNG, so the bytes match the declared media type.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

test("a photo becomes a document with source photo, listed with its status and linked to its page", async ({ page, signedIn }) => {
  void signedIn;
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Posteingang" }).click();
  await page.getByRole("link", { name: "Beleg fotografieren" }).click();
  await expect(page).toHaveURL("/capture");
  await expect(page.getByRole("heading", { name: "Beleg fotografieren" })).toBeVisible();
  // The capture page belongs to the Posteingang in the sidebar.
  await expect(page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Posteingang" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  const camera = page.getByLabel("Beleg fotografieren");
  await expect(camera).toHaveAttribute("accept", "image/jpeg,image/png");
  await expect(camera).toHaveAttribute("capture", "environment");
  await expect(camera).toHaveAttribute("multiple", "");

  const uploaded = page.waitForResponse((r) => r.url().endsWith("/api/documents") && r.request().method() === "POST");
  await camera.setInputFiles({ name: "beleg.png", mimeType: "image/png", buffer: PNG });
  const response = await uploaded;
  expect(response.status()).toBe(201);

  // `ai` is left to the server's default (off here): an upload sending ai=true would have
  // queued a read, and the row would wait for it instead.
  const list = page.getByRole("list", { name: "Fotografierte Belege" });
  const row = list.getByRole("listitem").filter({ hasText: "beleg.png" });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Nicht ausgelesen");
  await expect(row).toContainText("Automatisches Auslesen ist auf dem Server nicht eingeschaltet.");

  const link = row.getByRole("link", { name: "beleg.png" });
  const href = await link.getAttribute("href");
  expect(href).toMatch(/^\/documents\/[0-9a-f-]{36}$/);
  const documentId = (href ?? "").slice("/documents/".length);
  const detail = await apiCall<{ document: { source: string; original_filename: string } }>(page, "GET", `/api/documents/${documentId}`);
  expect(detail.document.source).toBe("photo");
  expect(detail.document.original_filename).toBe("beleg.png");

  // The same bytes again: the vault's document, marked as already there (D9).
  const again = page.waitForResponse((r) => r.url().endsWith("/api/documents") && r.request().method() === "POST");
  await camera.setInputFiles({ name: "beleg.png", mimeType: "image/png", buffer: PNG });
  expect((await again).status()).toBe(200);
  await expect(row).toHaveCount(2);
  await expect(row.filter({ hasText: "Bereits vorhanden" })).toHaveCount(1);

  // The list outlives a visit to the document page.
  await link.first().click();
  await expect(page).toHaveURL(href ?? "");
  await page.goBack();
  await expect(page.getByRole("list", { name: "Fotografierte Belege" }).getByRole("listitem")).toHaveCount(2);

  // A phone's width (mockup 06 is 390 px): nothing scrolls sideways.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("Beleg fotografieren")).toBeAttached();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("a file the API refuses shows the German error on its own row", async ({ page, signedIn }) => {
  void signedIn;
  await page.goto("/capture");
  const refused = page.waitForResponse((r) => r.url().endsWith("/api/documents") && r.request().method() === "POST");
  // The picker filters by type, but a file can still arrive with another one.
  await page.getByLabel("Beleg fotografieren").setInputFiles({ name: "notiz.exe", mimeType: "application/x-msdownload", buffer: Buffer.from("MZ") });
  expect((await refused).status()).toBe(415);
  const row = page.getByRole("list", { name: "Fotografierte Belege" }).getByRole("listitem").filter({ hasText: "notiz.exe" });
  await expect(row.getByRole("alert")).toContainText("Dieser Dateityp wird nicht angenommen.");
  await row.getByRole("button", { name: "Ausblenden" }).click();
  await expect(page.getByText("Noch keine Fotos.")).toBeVisible();
});

test("the manifest offers a home-screen shortcut to the capture page", async ({ page }) => {
  const response = await page.request.get("/manifest.webmanifest");
  expect(response.status()).toBe(200);
  const manifest = (await response.json()) as { start_url: string; shortcuts?: { name: string; url: string }[] };
  expect(manifest.start_url).toBe("/");
  expect(manifest.shortcuts).toContainEqual(expect.objectContaining({ name: "Beleg fotografieren", url: "/capture" }));
});
