// Einstellungen, the KI switches (docs/phases/phase-2.md D42): both start off; switching one
// on is saved (it survives a reload) and recorded as one workspace.settings_changed event
// naming only that setting; the other switch is untouched.
import { apiCall, expect, test } from "./support";

test("switching on Mieterdokumente is saved and recorded as one event", async ({ page, signedIn }) => {
  void signedIn;
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Einstellungen" }).click();
  await expect(page.getByRole("heading", { name: "KI" })).toBeVisible();

  const tenantDocuments = page.getByRole("switch", { name: "Mieterdokumente von der KI lesen lassen" });
  const autoAccept = page.getByRole("switch", { name: "KI-Zuordnungen automatisch bestätigen" });
  await expect(tenantDocuments).not.toBeChecked();
  await expect(autoAccept).not.toBeChecked();
  await expect(page.getByText("Anthropic verwendet sie nicht zum Training")).toBeVisible();

  const saved = page.waitForResponse((r) => r.url().endsWith("/api/workspace/settings") && r.request().method() === "PATCH");
  await tenantDocuments.check();
  expect((await saved).status()).toBe(200);
  await expect(tenantDocuments).toBeChecked();

  await page.reload();
  await expect(page.getByRole("switch", { name: "Mieterdokumente von der KI lesen lassen" })).toBeChecked();
  await expect(page.getByRole("switch", { name: "KI-Zuordnungen automatisch bestätigen" })).not.toBeChecked();

  const settings = await apiCall<{ ai_tenant_documents: boolean; auto_accept_llm: boolean }>(page, "GET", "/api/workspace/settings");
  expect(settings).toEqual({ ai_tenant_documents: true, auto_accept_llm: false });
  const events = await apiCall<{ type: string; payload: unknown }[]>(page, "GET", "/api/events?entity_type=workspace");
  expect(events.filter((e) => e.type === "workspace.settings_changed").map((e) => e.payload)).toEqual([
    { changes: { ai_tenant_documents: true } },
  ]);
});
