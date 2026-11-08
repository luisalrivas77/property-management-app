// Beleg fotografieren (docs/phases/phase-2.md §9): the phone capture page and the manifest's
// home-screen shortcut. Photos go into a page strip and are uploaded as one PDF with source =
// photo. The e2e API runs with its AI off (HAUSBUCH_AI_ENABLED's default) and without its job
// worker (playwright.config.ts), so an upload queues no read: the row says so and links to the
// document page, where it is typed by hand.
import type { Page } from "@playwright/test";
import { PDFDocument } from "pdf-lib";

import { apiCall, expect, test } from "./support";

// Real PNGs, so the browser can decode them: 40×30 (landscape) and 30×40 (portrait).
const LANDSCAPE = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAIAAADRv8uKAAAAK0lEQVR4nO3NMQ0AAAgDsMmZfxXIQgYcTfo3056IWCwWi8VisVgsFov/xguJMCDM2TzTeAAAAABJRU5ErkJggg==",
  "base64",
);
const PORTRAIT = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAoCAIAAABmcd1FAAAALElEQVR4nO3MMQ0AAAgDsMmZfxXIQgUHSZPeTTtHolar1Wq1Wq1Wq9Xq//UCUOYgzBIZQOgAAAAASUVORK5CYII=",
  "base64",
);
const png = (name: string, buffer: Buffer) => ({ name, mimeType: "image/png", buffer });

interface Detail {
  document: { source: string; original_filename: string };
  media_type: string;
}

const uploadResponse = (page: Page) => page.waitForResponse((r) => r.url().endsWith("/api/documents") && r.request().method() === "POST");

/** The stored original as a PDF, opened with pdf-lib to read its pages. */
async function storedPdf(page: Page, documentId: string): Promise<PDFDocument> {
  const original = await page.request.get(`/api/documents/${documentId}/original?disposition=attachment`);
  expect(original.status()).toBe(200);
  return PDFDocument.load(await original.body());
}

function documentIdOf(href: string | null): string {
  expect(href).toMatch(/^\/documents\/[0-9a-f-]{36}$/);
  return (href ?? "").slice("/documents/".length);
}

test("one photo becomes a one-page PDF with source photo, listed with its status and linked to its page", async ({ page, signedIn }) => {
  void signedIn;
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Zu prüfen" }).click();
  await page.getByRole("link", { name: "Beleg fotografieren" }).click();
  await expect(page).toHaveURL("/capture");
  await expect(page.getByRole("heading", { name: "Beleg fotografieren" })).toBeVisible();
  // The capture page belongs to "Zu prüfen" in the sidebar.
  await expect(page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Zu prüfen" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  const camera = page.getByLabel("Beleg fotografieren");
  await expect(camera).toHaveAttribute("accept", "image/jpeg,image/png");
  await expect(camera).toHaveAttribute("capture", "environment");
  await expect(camera).toHaveAttribute("multiple", "");

  // A photo waits in the strip; nothing is uploaded before the button.
  await camera.setInputFiles(png("beleg.png", LANDSCAPE));
  const strip = page.getByRole("list", { name: "Seiten" });
  await expect(strip.getByRole("listitem")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: /1 Seite/ })).toBeVisible();

  const uploaded = uploadResponse(page);
  await page.getByRole("button", { name: "Als ein Dokument hochladen" }).click();
  expect((await uploaded).status()).toBe(201);
  await expect(strip).toHaveCount(0); // the strip is cleared after the upload

  // `ai` is left to the server's default (off here): an upload sending ai=true would have
  // queued a read, and the row would wait for it instead.
  const list = page.getByRole("list", { name: "Hochgeladene Belege" });
  const row = list.getByRole("listitem");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Nicht ausgelesen");
  await expect(row).toContainText("Automatisches Auslesen ist auf dem Server nicht eingeschaltet.");

  const link = row.getByRole("link", { name: /^Beleg .*\.pdf$/ });
  const href = await link.getAttribute("href");
  const documentId = documentIdOf(href);
  const detail = await apiCall<Detail>(page, "GET", `/api/documents/${documentId}`);
  expect(detail.document.source).toBe("photo");
  expect(detail.media_type).toBe("application/pdf");
  const pdf = await storedPdf(page, documentId);
  expect(pdf.getPageCount()).toBe(1);
  const { width, height } = pdf.getPage(0).getSize();
  expect(width).toBeGreaterThan(height); // the page has its photo's aspect ratio

  // The same photo again gives the same PDF bytes: the vault's document, already there (D9).
  await camera.setInputFiles(png("beleg.png", LANDSCAPE));
  const again = uploadResponse(page);
  await page.getByRole("button", { name: "Als ein Dokument hochladen" }).click();
  expect((await again).status()).toBe(200);
  await expect(row).toHaveCount(2);
  await expect(row.filter({ hasText: "Bereits vorhanden" })).toHaveCount(1);

  // The list outlives a visit to the document page.
  await link.first().click();
  await expect(page).toHaveURL(href ?? "");
  await page.goBack();
  await expect(page.getByRole("list", { name: "Hochgeladene Belege" }).getByRole("listitem")).toHaveCount(2);

  // A phone's width (mockup 06 is 390 px): nothing scrolls sideways, with pages in the strip.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("Beleg fotografieren").setInputFiles([png("a.png", LANDSCAPE), png("b.png", PORTRAIT)]);
  await expect(page.getByRole("list", { name: "Seiten" }).getByRole("listitem")).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("two photos become one two-page PDF, in the order of the strip", async ({ page, signedIn }) => {
  void signedIn;
  await page.goto("/capture");
  await page.getByLabel("Beleg fotografieren").setInputFiles(png("erste.png", LANDSCAPE));
  // With a page in the strip the same input adds pages.
  await page.getByLabel("Seite hinzufügen").setInputFiles(png("zweite.png", PORTRAIT));
  const strip = page.getByRole("list", { name: "Seiten" });
  await expect(strip.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: /2 Seiten/ })).toBeVisible();

  // Move the portrait page to the front: the PDF follows the strip.
  await expect(page.getByRole("button", { name: "Seite 1 nach vorn" })).toBeDisabled();
  await page.getByRole("button", { name: "Seite 2 nach vorn" }).click();

  const uploaded = uploadResponse(page);
  await page.getByRole("button", { name: "Als ein Dokument hochladen" }).click();
  expect((await uploaded).status()).toBe(201);

  const rows = page.getByRole("list", { name: "Hochgeladene Belege" }).getByRole("listitem");
  await expect(rows).toHaveCount(1);
  const documentId = documentIdOf(await rows.getByRole("link").first().getAttribute("href"));

  // Exactly one document came of the two photos.
  const documents = await apiCall<{ id: string }[]>(page, "GET", "/api/documents");
  expect(documents.map((entry) => entry.id)).toEqual([documentId]);
  const detail = await apiCall<Detail>(page, "GET", `/api/documents/${documentId}`);
  expect(detail.document.source).toBe("photo");
  expect(detail.media_type).toBe("application/pdf");

  const pdf = await storedPdf(page, documentId);
  expect(pdf.getPageCount()).toBe(2);
  const first = pdf.getPage(0).getSize();
  const second = pdf.getPage(1).getSize();
  expect(first.height).toBeGreaterThan(first.width); // the portrait photo, moved to the front
  expect(second.width).toBeGreaterThan(second.height);
});

test("a file that is no photo is refused in the browser; an upload the server refuses keeps the pages", async ({ page, signedIn }) => {
  void signedIn;
  await page.goto("/capture");
  await page.getByLabel("Beleg fotografieren").setInputFiles({ name: "notiz.txt", mimeType: "text/plain", buffer: Buffer.from("kein Foto") });
  await expect(page.getByRole("alert").filter({ hasText: "Ein Foto ließ sich nicht öffnen" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Seiten" })).toHaveCount(0);

  // A PDF past the server's limit: the API's 413 is shown with its German text (the real limit
  // is exercised by upload-limit.spec.ts; here the answer is stubbed).
  await page.route("**/api/documents", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 413, contentType: "application/json", json: { error: "payload_too_large", message: "upload too large" } })
      : route.fallback(),
  );
  await page.getByLabel("Beleg fotografieren").setInputFiles(png("gross.png", LANDSCAPE));
  await page.getByRole("button", { name: "Als ein Dokument hochladen" }).click();
  const row = page.getByRole("list", { name: "Hochgeladene Belege" }).getByRole("listitem");
  await expect(row.getByRole("alert")).toContainText("Die Datei ist zu groß.");
  await expect(page.getByRole("list", { name: "Seiten" }).getByRole("listitem")).toHaveCount(1);
  await row.getByRole("button", { name: "Ausblenden" }).click();
  await expect(page.getByText("Noch keine Belege.")).toBeVisible();
});

test("the strip holds at most 20 pages", async ({ page, signedIn }) => {
  void signedIn;
  await page.goto("/capture");
  const photos = Array.from({ length: 21 }, (_, index) => png(`seite-${index + 1}.png`, index % 2 ? PORTRAIT : LANDSCAPE));
  await page.getByLabel("Beleg fotografieren").setInputFiles(photos);
  await expect(page.getByRole("list", { name: "Seiten" }).getByRole("listitem")).toHaveCount(20);
  await expect(page.getByRole("alert").filter({ hasText: "Ein Dokument hat höchstens 20 Seiten." })).toContainText("Ein Foto wurde nicht hinzugefügt");
  await expect(page.getByLabel(/Seite hinzufügen/)).toBeDisabled();
});

test("the manifest offers a home-screen shortcut to the capture page", async ({ page }) => {
  const response = await page.request.get("/manifest.webmanifest");
  expect(response.status()).toBe(200);
  const manifest = (await response.json()) as { start_url: string; shortcuts?: { name: string; url: string }[] };
  expect(manifest.start_url).toBe("/");
  expect(manifest.shortcuts).toContainEqual(expect.objectContaining({ name: "Beleg fotografieren", url: "/capture" }));
});
