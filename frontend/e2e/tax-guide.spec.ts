// Steuern as one guided path of five steps (phase 10 §7.6, D290, mockup 22b). For a household
// with one flat: the start page with its progress, deadline, „Jetzt dran“, the five steps and
// the forms line; the Immobilie step's rows with their ⓘ; an old bookmark to one of those
// inputs opening the step; Prüfen und abgeben with its points opened one at a time in the panel
// („Später“) and the ELSTER button greyed with its reason; the Anlage V view
// keeping its field sheet and archive; and the start page in English. Synthetic data only.
import type { Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

const YEAR = 2026;
const LABEL = "Wohnung Leitfaden";
const address = { street: "Teststraße", house_number: "21", postal_code: "10115", city: "Berlin", country_code: "DE" };

function pointPanel(page: Page, name: string | RegExp) {
  return page.getByRole("complementary", { name }).or(page.getByRole("dialog", { name }));
}

test("Steuern leads through five steps, one point at a time", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: LABEL,
    kind: "etw",
    address,
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });

  // The start page.
  await page.goto(`/tax/${YEAR}`);
  await expect(page.getByRole("heading", { level: 1, name: `Eure Steuererklärung ${YEAR}` })).toBeVisible();
  const bar = page.getByRole("progressbar", { name: /von 5 Schritten erledigt$/ });
  await expect(bar).toHaveAttribute("aria-valuemax", "5");
  await expect(page.getByText(/^abgeben bis \d/)).toBeVisible();
  const now = page.getByRole("region", { name: "Jetzt dran" });
  await expect(now.getByRole("link", { name: /^Weiter: / })).toBeVisible();
  const steps = page.getByRole("region", { name: "Schritte", exact: true }).getByRole("link");
  await expect(steps).toHaveCount(5);
  await expect(steps.nth(0)).toContainText("Eure Angaben");
  await expect(steps.nth(1)).toContainText(`Immobilie: ${LABEL}`);
  await expect(steps.nth(2)).toContainText("Jahresfragen");
  await expect(steps.nth(3)).toContainText("Belege");
  await expect(steps.nth(4)).toContainText("Prüfen und abgeben");
  await expect(steps.nth(1)).toContainText("1 offen");
  await expect(now.getByRole("link", { name: `Weiter: Immobilie: ${LABEL}` })).toHaveAttribute(
    "href",
    `/tax/${YEAR}?schritt=immobilie&immobilie=${property.id}`,
  );
  await expect(steps.nth(0)).toHaveAttribute("href", `/tax/${YEAR}?tab=angaben`);
  await expect(steps.nth(2)).toHaveAttribute("href", `/tax/${YEAR}/fragen`);
  const forms = page.locator(".tax-guide-forms");
  await expect(forms.getByRole("link", { name: "Anlage V" })).toHaveAttribute("href", `/tax/${YEAR}?tab=anlage-v`);
  await expect(forms.getByRole("link", { name: "Anlage N" })).toHaveAttribute("href", `/tax/${YEAR}?tab=anlage-n`);
  await expect(forms).toContainText("KAP und SO folgen später");

  // The Immobilie step: the property's inputs as short rows, each explained behind its ⓘ.
  await steps.nth(1).click();
  await expect(page).toHaveURL(`/tax/${YEAR}?schritt=immobilie&immobilie=${property.id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(LABEL);
  const inputs = page.getByRole("region", { name: "Angaben zur Immobilie" });
  await expect(inputs).toContainText("Nutzungsdauer des Gebäudes");
  await inputs.getByLabel("Was „Nutzungsdauer des Gebäudes“ bedeutet").click();
  await expect(inputs.getByText(/Wie viele Jahre das Gebäude abgeschrieben wird/)).toBeVisible();

  // A bookmark to one of these inputs from before D290 (on the Anlage V page) opens the step at it.
  await page.goto(`/tax/${YEAR}?immobilie=${property.id}#tax-useful-life`);
  await expect(page).toHaveURL(`/tax/${YEAR}?schritt=immobilie&immobilie=${property.id}#tax-useful-life`);
  await expect(page.locator("details#tax-useful-life")).toHaveAttribute("open", "");

  // Prüfen und abgeben: every open point in one list, each opened alone in the panel.
  await page.goto(`/tax/${YEAR}?schritt=pruefen`);
  const points = page.getByRole("region", { name: /Punkte? noch zu klären$/ }).getByRole("link");
  await expect(points.first()).toBeVisible();
  const total = await points.count();
  const elster = page.getByRole("button", { name: "ELSTER-Ausfüllhilfe erstellen" });
  await expect(elster).toBeDisabled();
  await expect(elster).toHaveAccessibleDescription(/^Geht, sobald/);

  const firstTitle = ((await points.first().locator(".detail-row-title").textContent()) ?? "").trim();
  await points.first().click();
  const first = pointPanel(page, firstTitle);
  await expect(first).toContainText(`Punkt 1 von ${total}`);
  await expect(first.getByText("Warum?")).toBeVisible();
  // „Später“ closes the panel; nothing is cleared.
  await first.getByRole("button", { name: "Später" }).click();
  await expect(first).toBeHidden();
  await expect(points).toHaveCount(total);
  // („Nächster Punkt“ with two points of one kind: tax-todos.spec.ts, Einnahme ohne Zuordnung.)

  // The Anlage V view is a view: its field sheet and archive stay, and „Alle Schritte“ leads back.
  await page.goto(`/tax/${YEAR}?tab=anlage-v&immobilie=${property.id}`);
  await expect(page.getByText("Ausfüllhilfe PDF").first()).toBeVisible();
  await expect(page.getByText("Archiv ZIP").first()).toBeVisible();
  await page.getByRole("link", { name: "‹ Alle Schritte" }).click();
  await expect(page).toHaveURL(`/tax/${YEAR}`);

  // In English (phase 10 D284).
  await page.evaluate(() => window.localStorage.setItem("hausbuch.locale", "en"));
  await page.reload();
  await expect(page.getByRole("progressbar", { name: /of 5 steps done$/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "Next up" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Steps", exact: true }).getByRole("link").nth(4)).toContainText("Check and file");
  await page.goto(`/tax/${YEAR}?schritt=pruefen`);
  await expect(page.getByRole("button", { name: "Make the ELSTER filling aid" })).toBeDisabled();
});
