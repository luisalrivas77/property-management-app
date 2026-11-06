// Konten and the statement import (docs/phases/phase-2.md §7, §14 step 3): the anonymised
// Sparkasse samples (backend/tests/fixtures/phase2/bank_samples/) hold the same 22 bookings,
// from 0,00 on 31.03.2026 to -3.028,51 on 06.11.2026, as a camt.052 ZIP and as a CSV-CAMT.
import { readFile } from "node:fs/promises";
import path from "node:path";

import type { Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

const SAMPLES = path.resolve(import.meta.dirname, "..", "..", "backend", "tests", "fixtures", "phase2", "bank_samples");
const ZIP = path.join(SAMPLES, "camt052.zip");
const CSV = path.join(SAMPLES, "csv_camt.csv");
const SAMPLE_IBAN = "DE50100000000000000001";

async function createAccountViaApi(page: Page): Promise<string> {
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Mietkonto",
    bank: "Sparkasse",
    iban: SAMPLE_IBAN,
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  return account.id;
}

test("a camt ZIP imports reconciled, a second upload adds nothing, Umsätze filter by date", async ({ page, signedIn }) => {
  void signedIn;
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Konten" }).click();
  await expect(page.getByRole("heading", { name: "Konten" })).toBeVisible();
  await page.getByRole("link", { name: "Konto anlegen" }).click();
  await page.getByLabel("Bezeichnung").fill("Mietkonto");
  await page.getByLabel("Bank", { exact: true }).fill("Sparkasse");
  await page.getByLabel("IBAN").fill(SAMPLE_IBAN);
  await page.getByLabel("Anfangssaldo (€)").fill("0,00");
  await page.getByLabel("Anfangssaldo am").fill("2026-03-31");
  await page.getByRole("button", { name: "Konto anlegen" }).click();
  await expect(page.getByRole("heading", { name: "Mietkonto" })).toBeVisible();
  // Masked: the API never returns the full IBAN.
  await expect(page.getByText("DE50 **** 0001").first()).toBeVisible();
  await expect(page.getByText(SAMPLE_IBAN)).toHaveCount(0);

  await page.getByLabel("Kontoauszug hochladen").setInputFiles(ZIP);
  await expect(page.getByRole("status").filter({ hasText: "Importiert: camt052.zip" })).toContainText("22 neue Umsätze · abgestimmt");
  const row = page.getByRole("row").filter({ hasText: "camt052.zip" });
  await expect(row).toContainText("01.04.2026 – 06.11.2026");
  await expect(row).toContainText("-3.028,51 €");
  await expect(row.locator(".badge")).toHaveText("abgestimmt");
  await expect(row.getByRole("button", { name: /Endsaldo/ })).toHaveCount(0); // balances come from the file

  // The same bytes again: 200 with the existing import.
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(ZIP);
  await expect(page.getByRole("status").filter({ hasText: "Bereits importiert: camt052.zip" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "camt052.zip" })).toHaveCount(1);

  await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Umsätze" }).click();
  await expect(page.getByText("22 Umsätze")).toBeVisible();
  await page.getByLabel("Buchungstag von").fill("2026-11-01");
  await page.getByRole("button", { name: "Anzeigen" }).click();
  await expect(page).toHaveURL(/von=2026-11-01/);
  await expect(page.getByText("5 Umsätze")).toBeVisible();
  await page.getByRole("button", { name: "Zurücksetzen" }).click();
  await expect(page.getByText("22 Umsätze")).toBeVisible();
});

test("a CSV labelled as Excel (Windows) imports, waits for its balance, then reconciles", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await createAccountViaApi(page);
  await page.goto(`/accounts/${accountId}`);

  const uploaded = page.waitForResponse((r) => r.url().endsWith(`/api/accounts/${accountId}/imports`) && r.request().method() === "POST");
  await page.getByLabel("Kontoauszug hochladen").setInputFiles({
    name: "Umsaetze.csv",
    mimeType: "application/vnd.ms-excel",
    buffer: await readFile(CSV),
  });
  expect((await uploaded).status()).toBe(201);
  const row = page.getByRole("row").filter({ hasText: "Umsaetze.csv" });
  await expect(row.locator(".badge")).toHaveText("Saldo fehlt");

  await row.getByRole("button", { name: "Endsaldo eingeben" }).click();
  await page.getByLabel("Endsaldo laut Online-Banking (€)").fill("-3.028,50");
  await page.getByRole("button", { name: "Abstimmen" }).click();
  await expect(row.locator(".badge")).toHaveText("nicht abgestimmt");
  await expect(row).toContainText("Endsaldo laut Bank -3.028,50 € · berechnet -3.028,51 €");

  await row.getByRole("button", { name: "Endsaldo korrigieren" }).click();
  await page.getByLabel("Endsaldo laut Online-Banking (€)").fill("-3.028,51");
  await page.getByRole("button", { name: "Abstimmen" }).click();
  await expect(row.locator(".badge")).toHaveText("abgestimmt");
});

test("the same payments in CSV and camt become possible duplicates to merge", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await createAccountViaApi(page);
  await page.goto(`/accounts/${accountId}`);
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(CSV);
  await expect(page.getByRole("row").filter({ hasText: "csv_camt.csv" })).toBeVisible();
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(ZIP);
  await expect(page.getByRole("row").filter({ hasText: "camt052.zip" }).locator(".badge")).toHaveText("nicht abgestimmt");

  // 19 of the 22 lines pair across formats; the 3 Rechnungsabschluss lines name no counterparty in camt.
  const tab = page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: /Mögliche Doppelbuchungen/ });
  await expect(tab).toHaveText("Mögliche Doppelbuchungen (19)");
  await tab.click();
  const merge = page.getByRole("button", { name: "Zusammenführen" });
  await expect(merge).toHaveCount(19);
  await merge.first().click();
  await expect(tab).toHaveText("Mögliche Doppelbuchungen (18)");
  await expect(page.getByText("1 bereits entschieden")).toBeVisible();

  await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Umsätze" }).click();
  await expect(page.getByText("44 Umsätze")).toBeVisible();
  await expect(page.getByText("Doppelbuchung, zählt nicht")).toHaveCount(1);
});
