// The Bankabruf card (docs/phases/phase-5.md §11): the FinTS connection form on the account
// page, and a start refused in plain words when the server holds no PIN and product id (the
// e2e API runs without them, so no bank is ever contacted here). Synthetic values only.
import type { Page, Route } from "@playwright/test";

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

// A run waiting for the pushTAN approval (or queued, or running), as the API would list it;
// the runs are mocked because the e2e API holds no FinTS secrets and never reaches these
// states itself.
function approvalRun(accountId: string, connectionId: string, minutesAgo: number, status = "needs_approval") {
  const entered = new Date(Date.now() - minutesAgo * 60_000).toISOString();
  return {
    id: "00000000-0000-4000-8000-0000000005a1",
    workspace_id: "00000000-0000-4000-8000-0000000005a0",
    created_at: entered,
    updated_at: entered,
    account_id: accountId,
    connection_id: connectionId,
    trigger: "manual",
    status,
    requested_from: "2026-04-01",
    requested_to: "2026-11-11",
    balance_checked: false,
    started_at: entered,
  };
}

async function openWithApprovalRun(page: Page, minutesAgo: number, status = "needs_approval") {
  const accountId = await createAccountViaApi(page);
  const connection = await apiCall<{ id: string }>(page, "POST", `/api/accounts/${accountId}/bank-connection`, {
    account_id: accountId,
    bank_code: "10000000",
    fints_url: "https://fints.example.org/fints",
    login_name: "loginname-xyz",
  });
  const run = approvalRun(accountId, connection.id, minutesAgo, status);
  await page.route(`**/api/accounts/${accountId}/syncs`, (route: Route) =>
    route.request().method() === "GET" ? route.fulfill({ json: [run] }) : route.fallback(),
  );
  await page.route(`**/api/syncs/${run.id}`, (route: Route) => route.fulfill({ json: run }));
  await page.goto(`/accounts/${accountId}`);
  return page.getByRole("region", { name: "Bankabruf" });
}

test("an approval wait past its five minutes can be started again", async ({ page, signedIn }) => {
  void signedIn;
  const card = await openWithApprovalRun(page, 6);
  await expect(card).toContainText("Die Freigabe wurde nicht innerhalb von fünf Minuten bestätigt.");
  await expect(card.getByRole("button", { name: "Erneut abrufen" })).toBeEnabled();
});

test("an approval wait inside its five minutes keeps the button locked", async ({ page, signedIn }) => {
  void signedIn;
  const card = await openWithApprovalRun(page, 1);
  await expect(card).toContainText("In der S-pushTAN-App freigeben.");
  await expect(card).toContainText(/Noch [34]:\d\d Minuten/);
  await expect(card.getByRole("button", { name: "Abruf läuft …" })).toBeDisabled();
});

// A queued or running row untouched for the approval wait plus five minutes has lost its job
// (the API's is_stale); the card offers a restart instead of locking the button for good.
test("a running row stuck past ten minutes can be started again", async ({ page, signedIn }) => {
  void signedIn;
  const card = await openWithApprovalRun(page, 11, "running");
  await expect(card).toContainText("kommt nicht voran");
  await expect(card.getByRole("button", { name: "Erneut abrufen" })).toBeEnabled();
});

test("a running row inside ten minutes keeps the button locked", async ({ page, signedIn }) => {
  void signedIn;
  const card = await openWithApprovalRun(page, 1, "running");
  await expect(card).toContainText("Hausbuch fragt die Bank nach den Umsätzen");
  await expect(card.getByRole("button", { name: "Abruf läuft …" })).toBeDisabled();
});

// Phase 6 D163: one PIN per online-banking login, as the API container's secret named by the
// connection's slot; the card names that variable and where it goes, never asks for the PIN.
test("a connection on its own login names its PIN variable and the Scaleway console", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await createAccountViaApi(page);
  await page.goto(`/accounts/${accountId}`);
  const card = page.getByRole("region", { name: "Bankabruf" });
  await expect(card.getByLabel("Name des Zugangs")).toHaveValue("primary");
  await card.getByLabel("BLZ").fill("10000000");
  await card.getByLabel("FinTS-Adresse").fill("https://fints.example.org/fints");
  await card.getByLabel("Anmeldename").fill("loginname-xyz");
  await card.getByLabel("Name des Zugangs").fill("Sparkasse B");
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card).toContainText("Kleinbuchstaben, Ziffern und _");

  await card.getByLabel("Name des Zugangs").fill("sparkasse_b");
  await expect(card).toContainText("HAUSBUCH_FINTS_PIN_SPARKASSE_B");
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("lo***yz")).toBeVisible();
  await expect(card).toContainText("Zugang „sparkasse_b“");
  await expect(card).toContainText("noch nicht gesetzt");
  await expect(card).toContainText("Scaleway-Konsole");
});
