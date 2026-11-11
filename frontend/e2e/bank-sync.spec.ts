// The Bankabruf card (docs/phases/phase-5.md §11): the FinTS connection form on the account
// page, and a start refused in plain words when the server holds no PIN and product id (the
// e2e API runs without them, so no bank is ever contacted here). Synthetic values only.
import type { Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

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

test("the Bankabruf card sets up a connection and explains a server without the secrets", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await createAccountViaApi(page);
  await page.goto(`/accounts/${accountId}`);
  const card = page.getByRole("region", { name: "Bankabruf" });
  await expect(card).toContainText("PIN und Produkt-ID liegen in der Serverkonfiguration");

  // A pasted IBAN becomes its Bankleitzahl (characters 5–12).
  await card.getByLabel("BLZ").fill(SAMPLE_IBAN);
  await expect(card.getByLabel("BLZ")).toHaveValue("10000000");
  await card.getByLabel("FinTS-Adresse").fill("http://fints.example.org/fints");
  await card.getByLabel("Anmeldename").fill("loginname-xyz");
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("Die FinTS-Adresse muss mit https:// beginnen.")).toBeVisible();

  await card.getByLabel("FinTS-Adresse").fill("https://fints.example.org/fints");
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("lo***yz")).toBeVisible();
  await expect(card).not.toContainText("loginname-xyz");
  await expect(card).toContainText("noch keine: beim ersten Abruf fragt die Bank");

  await card.getByRole("button", { name: "Jetzt abrufen" }).click();
  await expect(card.getByRole("alert")).toContainText("PIN oder Produkt-ID fehlen in der Serverkonfiguration");
  await expect(card.getByRole("table")).toHaveCount(0); // a refused start leaves no run
});
