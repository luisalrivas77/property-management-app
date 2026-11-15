// The Bankabruf card (docs/phases/phase-5.md §11): the FinTS connection form on the account
// page, and a start refused in plain words when the server holds no product id (the e2e API
// runs without it and without a PIN variable, so no bank is ever contacted here). A PIN typed
// into the app (phase 6 D196) is stored encrypted and never shown again. Synthetic values only.
import type { Page, Route } from "@playwright/test";

import { apiCall, expect, test } from "./support";

const SAMPLE_IBAN = "DE50100000000000000001";
const SAMPLE_PIN = "e2e-PIN-24680";

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
  await expect(card).toContainText("zeigt sie nie wieder an");

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
  await expect(card.getByRole("alert")).toContainText("die Produkt-ID fehlt in der Serverkonfiguration");
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

// Phase 6 D163: one PIN per online-banking login, named by the connection's slot; since D196
// the card asks for it (optional while setting up) when neither the app nor the server has it.
test("a connection on its own login asks for that login's PIN", async ({ page, signedIn }) => {
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
  await expect(card.getByLabel("PIN (Online-Banking)")).toBeVisible();
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("lo***yz")).toBeVisible();
  await expect(card).toContainText("„sparkasse_b“");
  await expect(card).toContainText("Noch keine PIN hinterlegt.");
  await expect(card.getByLabel("PIN (Online-Banking)")).toHaveValue("");
});

// D196: the PIN typed while setting up is saved encrypted; the card then shows only when, and
// „PIN löschen“ brings the input back. The PIN is never on the page again.
test("a PIN typed into the app is saved, never shown, and can be deleted", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await createAccountViaApi(page);
  await page.goto(`/accounts/${accountId}`);
  const card = page.getByRole("region", { name: "Bankabruf" });
  await card.getByLabel("BLZ").fill("10000000");
  await card.getByLabel("FinTS-Adresse").fill("https://fints.example.org/fints");
  await card.getByLabel("Anmeldename").fill("loginname-xyz");
  await card.getByLabel("PIN (Online-Banking)").fill("1234");
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("Die PIN hat 5 bis 64 Zeichen.")).toBeVisible();

  await card.getByLabel("PIN (Online-Banking)").fill(SAMPLE_PIN);
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("lo***yz")).toBeVisible();
  await expect(card).toContainText("PIN in der App gespeichert am");
  await expect(card.getByRole("button", { name: "PIN ändern" })).toBeVisible();
  await expect(card.getByLabel("PIN (Online-Banking)")).toHaveCount(0);
  expect(await page.content()).not.toContain(SAMPLE_PIN);

  page.once("dialog", (dialog) => void dialog.accept());
  await card.getByRole("button", { name: "PIN löschen" }).click();
  await expect(card).toContainText("Noch keine PIN hinterlegt.");
  await expect(card.getByLabel("PIN (Online-Banking)")).toHaveValue("");
});

// D196 review: a PIN saved under a name no connection uses (here put there through the API,
// as a refused setup or a renamed connection leaves it) is listed on the card with
// „PIN löschen“; it stays until a person deletes it.
test("a PIN under a name no connection uses is listed and can be deleted", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await createAccountViaApi(page);
  await apiCall(page, "PUT", "/api/bank-credentials/spare_x", { pin: SAMPLE_PIN });
  await page.goto(`/accounts/${accountId}`);
  const card = page.getByRole("region", { name: "Bankabruf" });
  await card.getByLabel("BLZ").fill("10000000");
  await card.getByLabel("FinTS-Adresse").fill("https://fints.example.org/fints");
  await card.getByLabel("Anmeldename").fill("loginname-xyz");
  await card.getByLabel("Name des Zugangs").fill("used_y");
  await card.getByRole("button", { name: "Bankabruf einrichten" }).click();
  await expect(card.getByText("lo***yz")).toBeVisible();
  await expect(card).toContainText("„used_y“");

  const unused = card.getByRole("list", { name: "Gespeicherte PINs ohne Bankzugang" });
  await expect(unused.getByRole("listitem")).toHaveCount(1);
  await expect(unused).toContainText("Zugang „spare_x“");
  expect(await page.content()).not.toContain(SAMPLE_PIN);

  page.once("dialog", (dialog) => void dialog.accept());
  await unused.getByRole("button", { name: "PIN löschen" }).click();
  await expect(unused).toHaveCount(0);
  const slots = await apiCall<{ app_slots: unknown[] }>(page, "GET", "/api/bank-credential-slots");
  expect(slots.app_slots).toEqual([]); // deleted on the server, not only hidden
});
