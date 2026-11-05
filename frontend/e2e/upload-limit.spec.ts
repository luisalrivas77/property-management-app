// The upload limit through the web server (docs/phases/phase-1-step-3.md §10.8 item 3).
//
// The browser posts uploads to /api/documents on the web origin, and the Next server proxies
// them to the API. Next's proxy buffers only 10 MB of a body and times out after 30 s by
// default, so a scanned PDF above 10 MB reached the API cut short and the upload failed after
// half a minute. next.config.ts raises both (proxyClientMaxBodySize, proxyTimeout). This
// uploads a file of exactly the API's limit, 25 MiB, through the documents screen.
import { expect, test } from "./support";

const MAX_UPLOAD_BYTES = 25 * 1024 * 1024; // HAUSBUCH_MAX_UPLOAD_BYTES' default (settings.py)

function pdfOfSize(bytes: number): Buffer {
  const head = Buffer.from("%PDF-1.4\n% Hausbuch e2e: a large scan\n");
  const tail = Buffer.from("\n%%EOF\n");
  const body = Buffer.alloc(bytes - head.length - tail.length, "0");
  return Buffer.concat([head, body, tail]);
}

test("a 25 MiB upload through the web server reaches the API", async ({ page, signedIn }) => {
  void signedIn;
  const file = pdfOfSize(MAX_UPLOAD_BYTES);
  expect(file.length).toBe(MAX_UPLOAD_BYTES);

  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Dokumente" }).click();
  const uploaded = page.waitForResponse((r) => r.url().endsWith("/api/documents") && r.request().method() === "POST", {
    timeout: 60_000,
  });
  const started = Date.now();
  await page.getByLabel("Dokumente hochladen").setInputFiles({ name: "Grosser-Scan.pdf", mimeType: "application/pdf", buffer: file });
  const response = await uploaded;
  const seconds = (Date.now() - started) / 1000;

  // 201: the API received the whole file and stored it. (Before the fix: the proxy's 30 s
  // timeout, then a 500 from Next.) Well under 30 s, so a timeout cannot have been involved.
  expect(response.status()).toBe(201);
  expect(seconds).toBeLessThan(30);
  const notice = page.getByRole("status").filter({ hasText: "Hochgeladen:" });
  await expect(notice).toContainText("Grosser-Scan.pdf");

  // The stored original is the whole file, byte for byte.
  const href = await notice.getByRole("link", { name: "Grosser-Scan.pdf" }).getAttribute("href");
  expect(href).toMatch(/^\/documents\/[0-9a-f-]{36}$/);
  const original = await page.request.get(`/api${href}/original?disposition=attachment`);
  expect(original.status()).toBe(200);
  expect((await original.body()).equals(file)).toBe(true);
});
