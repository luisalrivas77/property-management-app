// Einstellungen, the KI switches (docs/phases/phase-2.md D42): both start off; switching one
// on is saved (it survives a reload) and recorded as one workspace.settings_changed event
// naming only that setting; the other switch is untouched.
import { apiCall, expect, test } from "./support";

test("switching on Mieterdokumente is saved and recorded as one event", async ({ page, signedIn }) => {
  void signedIn;
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Einstellungen" }).click();
  await expect(page.getByRole("heading", { name: "KI", level: 2 })).toBeVisible();

  const tenantDocuments = page.getByRole("switch", { name: "Mieterdokumente von der KI lesen lassen" });
  const autoAccept = page.getByRole("switch", { name: "KI-Zuordnungen automatisch bestätigen" });
  await expect(tenantDocuments).not.toBeChecked();
  await expect(autoAccept).not.toBeChecked();
  await expect(page.getByText("Anthropic verwendet sie nicht zum Training")).toBeVisible();

  const saved = page.waitForResponse((r) => r.url().endsWith("/api/workspace/settings") && r.request().method() === "PATCH");
  // The switch shows the saved state: it flips once the PATCH has answered (nothing in the
  // app updates optimistically), so click and wait instead of check(), which expects the
  // state to change with the click itself.
  await tenantDocuments.click();
  expect((await saved).status()).toBe(200);
  await expect(tenantDocuments).toBeChecked();

  await page.reload();
  await expect(page.getByRole("switch", { name: "Mieterdokumente von der KI lesen lassen" })).toBeChecked();
  await expect(page.getByRole("switch", { name: "KI-Zuordnungen automatisch bestätigen" })).not.toBeChecked();

  const settings = await apiCall<{
    advice_mode: string;
    ai_private_accounts: boolean;
    ai_private_accounts_active: boolean;
    ai_private_accounts_consented_by: string[];
    ai_tenant_documents: boolean;
    auto_accept_llm: boolean;
    reminder_emails: boolean;
    reminder_emails_available: boolean;
  }>(page, "GET", "/api/workspace/settings");
  // reminder_emails_available: the e2e server has no Transactional Email settings.
  // advice_mode: phase 9 D251, `own_use` by default.
  expect(settings).toEqual({
    advice_mode: "own_use",
    ai_private_accounts: false,
    ai_private_accounts_active: false,
    ai_private_accounts_consented_by: [],
    ai_tenant_documents: true,
    auto_accept_llm: false,
    reminder_emails: false,
    reminder_emails_available: false,
  });
  const events = await apiCall<{ type: string; payload: unknown }[]>(page, "GET", "/api/events?entity_type=workspace");
  expect(events.filter((e) => e.type === "workspace.settings_changed").map((e) => e.payload)).toEqual([
    { changes: { ai_tenant_documents: true }, consented_by: null },
  ]);
});

// Erinnerungen (docs/phases/phase-5.md D123): the reminder email switch is saved like the KI
// switches; without Transactional Email settings the page says sending is not set up yet.
test("the reminder email switch is saved and says when sending is not set up", async ({ page, signedIn }) => {
  void signedIn;
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Einstellungen" }).click();
  await expect(page.getByRole("heading", { name: "Erinnerungen" })).toBeVisible();

  const reminders = page.getByRole("switch", { name: "Erinnerungen per E-Mail" });
  await expect(reminders).not.toBeChecked();
  await expect(page.getByTestId("reminder-emails-unavailable")).toContainText("noch nicht eingerichtet");

  const saved = page.waitForResponse((r) => r.url().endsWith("/api/workspace/settings") && r.request().method() === "PATCH");
  await reminders.click();
  expect((await saved).status()).toBe(200);
  await expect(reminders).toBeChecked();

  await page.reload();
  await expect(page.getByRole("switch", { name: "Erinnerungen per E-Mail" })).toBeChecked();
  const events = await apiCall<{ type: string; payload: unknown }[]>(page, "GET", "/api/events?entity_type=workspace");
  expect(events.filter((e) => e.type === "workspace.settings_changed").map((e) => e.payload)).toEqual([
    { changes: { reminder_emails: true }, consented_by: null },
  ]);
});

// Hinweis-Modus (docs/phases/phase-9.md §10, §14, D251): „Eigene Nutzung“ by default; choosing
// „Allgemein“ is saved (it survives a reload) and recorded as one workspace.advice_mode_changed
// event, never as a settings_changed one.
test("the Hinweis-Modus is saved and recorded as its own event", async ({ page, signedIn }) => {
  void signedIn;
  await page.goto("/settings");
  const own = page.getByRole("radio", { name: "Eigene Nutzung" });
  const generic = page.getByRole("radio", { name: "Allgemein" });
  await expect(own).toBeChecked();
  await expect(generic).not.toBeChecked();
  await expect(page.getByTestId("advice-mode-saved")).toHaveText("Gespeichert: Eigene Nutzung");
  await expect(page.getByText("nennt nur Beträge und Anlageklassen, keine Konten und keine Produkte")).toBeVisible();

  const saved = page.waitForResponse((r) => r.url().endsWith("/api/workspace/settings") && r.request().method() === "PATCH");
  await generic.click();
  expect((await saved).status()).toBe(200);
  await expect(generic).toBeChecked();
  await expect(page.getByTestId("advice-mode-saved")).toHaveText("Gespeichert: Allgemein");

  await page.reload();
  await expect(page.getByRole("radio", { name: "Allgemein" })).toBeChecked();
  const settings = await apiCall<{ advice_mode: string }>(page, "GET", "/api/workspace/settings");
  expect(settings.advice_mode).toBe("generic");
  const events = await apiCall<{ type: string; payload: unknown }[]>(page, "GET", "/api/events?entity_type=workspace");
  expect(events.filter((e) => e.type === "workspace.advice_mode_changed").map((e) => e.payload)).toEqual([
    { from_mode: "own_use", to_mode: "generic" },
  ]);
  expect(events.filter((e) => e.type === "workspace.settings_changed")).toEqual([]);
});
