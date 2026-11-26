// Steuern as one guided path of four steps (phase 10 §7.6, D290, mockup 22b; four since the
// Jahresfragen folded into „Eure Angaben“, §7.8 (b), D294). For a household with one flat: the
// start page with its progress, deadline, „Jetzt dran“, the four steps and
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

test("Steuern leads through four steps, one point at a time", async ({ page, signedIn }) => {
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
  const bar = page.getByRole("progressbar", { name: /von 4 Schritten erledigt$/ });
  await expect(bar).toHaveAttribute("aria-valuemax", "4");
  await expect(page.getByText(/^abgeben bis \d/)).toBeVisible();
  const now = page.getByRole("region", { name: "Jetzt dran" });
  await expect(now.getByRole("link", { name: /^Weiter: / })).toBeVisible();
  const steps = page.getByRole("region", { name: "Schritte", exact: true }).getByRole("link");
  await expect(steps).toHaveCount(4);
  await expect(steps.nth(0)).toContainText("Eure Angaben");
  await expect(steps.nth(1)).toContainText(`Immobilie: ${LABEL}`);
  await expect(steps.nth(2)).toContainText("Belege");
  await expect(steps.nth(3)).toContainText("Prüfen und abgeben");
  // The household's yearly questions are open in „Eure Angaben“, so it comes first; the
  // property's two yearly questions (trips, privately paid costs) count in its step.
  await expect(steps.nth(1)).toContainText("3 offen");
  await expect(now.getByRole("link", { name: "Weiter: Eure Angaben" })).toBeVisible();
  await expect(steps.nth(0)).toHaveAttribute("href", `/tax/${YEAR}?tab=angaben`);
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
  // („Nächster Punkt“: tax-todos.spec.ts, Einnahme ohne Zuordnung.)

  // A year with every point cleared (the guide mocked as ready, since a whole finished year is
  // out of reach here): three numbered steps, „fertig“ on the form card, the result final and
  // no greyed button.
  await page.route(`**/api/tax/${YEAR}/guide`, async (route) => {
    const response = await route.fetch();
    const guide = await response.json();
    guide.ready = true;
    guide.points = [];
    guide.next_step = guide.steps.length - 1;
    guide.steps = guide.steps.map((s: Record<string, unknown>) => ({ ...s, state: "done", open: 0, waiting_from: null }));
    guide.steps_done = guide.steps.length;
    guide.documents = (guide.documents ?? []).map((d: Record<string, unknown>) => ({ ...d, status: "present" }));
    guide.forms = (guide.forms ?? []).map((f: Record<string, unknown>) => ({ ...f, open: 0 }));
    await route.fulfill({ response, json: guide });
  });
  await page.reload();
  const ready = page.getByRole("region", { name: "Bereit zum Abgeben" });
  await expect(ready.locator("ol.tax-guide-ready > li")).toHaveCount(3);
  await expect(ready).toContainText("ELSTER-Ausfüllhilfe öffnen");
  await expect(ready).toContainText("In „Mein ELSTER“ übertragen");
  await expect(ready).toContainText("Archiv speichern");
  await expect(page.getByRole("region", { name: "Formulare" })).toContainText("fertig");
  await expect(page.getByText("Alle Punkte sind geklärt.")).toBeVisible();
  await expect(page.getByRole("button", { name: "ELSTER-Ausfüllhilfe erstellen" })).toHaveCount(0);
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // The Anlage V view is a view: its field sheet and archive stay, and „Alle Schritte“ leads back.
  await page.goto(`/tax/${YEAR}?tab=anlage-v&immobilie=${property.id}`);
  await expect(page.getByText("Ausfüllhilfe PDF").first()).toBeVisible();
  await expect(page.getByText("Archiv ZIP").first()).toBeVisible();
  await page.getByRole("link", { name: "‹ Alle Schritte" }).click();
  await expect(page).toHaveURL(`/tax/${YEAR}`);

  // In English (phase 10 D284).
  await page.evaluate(() => window.localStorage.setItem("hausbuch.locale", "en"));
  await page.reload();
  await expect(page.getByRole("progressbar", { name: /of 4 steps done$/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "Next up" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Steps", exact: true }).getByRole("link").nth(3)).toContainText("Check and file");
  await page.goto(`/tax/${YEAR}?schritt=pruefen`);
  await expect(page.getByRole("button", { name: "Make the ELSTER filling aid" })).toBeDisabled();
});
