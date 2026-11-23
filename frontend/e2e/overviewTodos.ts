// Übersicht's „Was noch fehlt“ shows its first five to-dos and „Alle N anzeigen“ (phase 10 §7.6).
// A test that looks for one to-do there opens the whole list first, once it has loaded.
import { expect, type Locator, type Page } from "@playwright/test";

/** The list on Übersicht, opened in full; `page` is on Übersicht. */
export async function overviewTodos(page: Page): Promise<Locator> {
  const region = page.getByRole("region", { name: /^(Was noch fehlt|Still missing)$/ });
  await showAllTodos(region);
  return region;
}

/** Waits until the list has loaded, then opens the entries past the fifth if there are any. The
 * list's own entries come before the reminders', so a reminder read that arrives later never
 * hides one of them; once opened, the list stays open until the page is left or reloaded. */
export async function showAllTodos(region: Locator): Promise<void> {
  await expect(region).toBeVisible();
  await expect(region.getByText(/^(Lädt …|Loading …)$/)).toHaveCount(0);
  const more = region.getByRole("button", { name: /^(Alle \d+ anzeigen|Show all \d+)$/ });
  if ((await more.count()) > 0) await more.click();
}
