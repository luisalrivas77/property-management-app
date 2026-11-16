// The review page (Zu prüfen) and the allocation panel (docs/phases/phase-2.md §8.1, §8.3, step 4). The
// anonymised Sparkasse camt sample (backend/tests/fixtures/phase2/bank_samples/) imports
// reconciled. The e2e API runs without its job worker (playwright.config.ts), so nothing is
// matched and its 22 lines wait in the inbox as "Nicht zugeordnet": the state a line is in when
// no rule, matcher or Beleg fits.
import path from "node:path";

import type { Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

const ZIP = path.resolve(import.meta.dirname, "..", "..", "backend", "tests", "fixtures", "phase2", "bank_samples", "camt052.zip");
const CSV = path.resolve(import.meta.dirname, "..", "..", "backend", "tests", "fixtures", "phase2", "bank_samples", "csv_camt.csv");

/** A rental account with the camt sample imported through Konten; returns the account id. */
async function importSample(page: Page): Promise<string> {
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Mietkonto",
    bank: "Sparkasse",
    iban: "DE50100000000000000001",
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  await page.goto(`/accounts/${account.id}`);
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(ZIP);
  await expect(page.getByRole("status").filter({ hasText: "Importiert: camt052.zip" })).toContainText("22 neue Umsätze · abgestimmt");
  return account.id;
}

async function openInbox(page: Page): Promise<void> {
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Zu prüfen" }).click();
  await expect(page.getByRole("heading", { name: "Hochladen, bestätigen, fertig" })).toBeVisible();
  await openSection(page, "Zahlungen zuordnen");
}

/** A section of Zu prüfen folds from its heading row; opens it when it is folded. */
async function openSection(page: Page, title: string): Promise<void> {
  const toggle = sectionToggle(page, title);
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
}

function sectionToggle(page: Page, title: string) {
  return page.getByRole("region", { name: "Zu erledigen" }).getByRole("button", { name: new RegExp(`^${title}`) });
}

test("an unmatched line is split by hand into two rows; Bestätigen waits until Rest is 0", async ({ page, signedIn }) => {
  void signedIn;
  const accountId = await importSample(page);
  await openInbox(page);

  const queue = page.getByRole("list", { name: "Zahlungen zuordnen" });
  await expect(queue.getByRole("button")).toHaveCount(22);
  // The sidebar counts the same items the page lists.
  const navEntry = page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Zu prüfen" });
  await expect(navEntry).toHaveAccessibleName(/22 offen/);
  await expect(page.getByRole("heading", { name: /^Zahlungen zuordnen/ })).toContainText("22");
  // The section folds from its heading row and opens again on a click; the only section here
  // is the first, so it starts open, and „Alle aufklappen“ is not offered for one section.
  const lines = sectionToggle(page, "Zahlungen zuordnen");
  await expect(page.getByRole("button", { name: "Alle aufklappen" })).toHaveCount(0);
  await lines.click();
  await expect(lines).toHaveAttribute("aria-expanded", "false");
  await expect(queue).toBeHidden();
  await lines.click();
  await expect(lines).toHaveAttribute("aria-expanded", "true");
  await expect(queue.getByRole("button")).toHaveCount(22);
  // „Empfänger suchen“ narrows the lines by payee, booking text or purpose, case ignored, and
  // says so; clearing it shows them all again.
  const search = page.getByLabel("Empfänger suchen");
  await search.fill("verwendungszweck 22");
  await expect(queue.getByRole("button")).toHaveCount(1);
  await expect(page.getByText("1 von 22", { exact: true })).toBeVisible();
  await search.fill("Partei D");
  await expect(queue.getByRole("button")).toHaveCount(1);
  await expect(queue.getByRole("button")).toContainText("Partei D");
  await search.fill("Niemand Beispiel");
  await expect(page.getByText("Keine Zahlung passt zu „Niemand Beispiel“.")).toBeVisible();
  await expect(queue).toHaveCount(0);
  await search.fill("");
  await expect(queue.getByRole("button")).toHaveCount(22);
  // D50: unmatched lines can be sent to the model again; this API runs with the AI layer off,
  // so the request is refused with that reason.
  const queueCard = page.getByRole("region", { name: "Zu erledigen" });
  await queueCard.getByRole("button", { name: "KI-Vorschläge holen" }).click();
  await expect(queueCard.getByRole("alert")).toContainText("Die KI ist nicht eingerichtet");
  await expect(queue.getByRole("button")).toHaveCount(22);
  const line = queue.getByRole("button").filter({ hasText: "Partei D" });
  await expect(line).toContainText("Nicht zugeordnet");
  await expect(line).toContainText("+740,18 €");
  await line.click();
  await expect(line).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/\/inbox\?umsatz=/);
  const transactionId = new URL(page.url()).searchParams.get("umsatz") ?? "";

  const panel = page.getByRole("complementary", { name: "Zuordnung" });
  await expect(panel.getByRole("heading", { name: /Partei D/ })).toBeVisible();
  await expect(panel.getByText("Wofür war diese Buchung?")).toBeVisible();
  const confirm = panel.getByRole("button", { name: "Bestätigen" });

  // One row over the whole amount to start with; making it smaller opens a Rest.
  const first = panel.getByRole("group", { name: "Zeile 1" });
  await expect(first.getByLabel("Betrag (€)")).toHaveValue("740,18");
  await first.getByLabel("Betrag (€)").fill("600,00");
  await first.getByLabel("Kategorie").selectOption({ label: "Kaltmiete" });
  await expect(panel.getByText("Rest: 140,18 €")).toBeVisible();
  await expect(confirm).toBeDisabled();

  // A new row starts with the Rest; a cent off keeps Bestätigen disabled.
  await panel.getByRole("button", { name: "Zeile hinzufügen" }).click();
  const second = panel.getByRole("group", { name: "Zeile 2" });
  await expect(second.getByLabel("Betrag (€)")).toHaveValue("140,18");
  await second.getByLabel("Betrag (€)").fill("140,17");
  await second.getByLabel("Kategorie").selectOption({ label: "Nebenkostenvorauszahlung" });
  await expect(panel.getByText("Rest: 0,01 €")).toBeVisible();
  await expect(confirm).toBeDisabled();
  await second.getByLabel("Betrag (€)").fill("140,18");
  await expect(panel.getByText("Rest: 0,00 €")).toBeVisible();
  await expect(confirm).toBeEnabled();

  // Partei D has an IBAN, so the rule box is offered; a rent split without a tenancy cannot
  // become a rule, so it stays off.
  const learn = panel.getByLabel("Künftig immer so zuordnen");
  await expect(learn).toBeVisible();
  await learn.uncheck();

  const saved = page.waitForResponse((r) => r.url().endsWith(`/api/transactions/${transactionId}/allocations`) && r.request().method() === "PUT");
  await confirm.click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByRole("status").filter({ hasText: "Zuordnung bestätigt." })).toBeVisible();
  await expect(queue.getByRole("button")).toHaveCount(21);
  await expect(queue.getByRole("button").filter({ hasText: "Partei D" })).toHaveCount(0);
  await expect(navEntry).toHaveAccessibleName(/21 offen/);

  const stored = await apiCall<{ allocations: { amount: string; category: string; status: string }[] }>(
    page,
    "GET",
    `/api/transactions/${transactionId}/allocations`,
  );
  expect(stored.allocations.map((a) => [a.amount, a.category, a.status]).sort()).toEqual([
    ["140.18", "operating_costs_prepayment", "confirmed"],
    ["600.00", "rent_cold", "confirmed"],
  ]);

  // Konten › Umsätze says what the line was booked as (the larger amount first), the other
  // lines are still open, and the status opens the same panel, ready for a correction.
  await page.goto(`/accounts/${accountId}?tab=umsaetze`);
  const booked = page.getByRole("row").filter({ hasText: "Partei D" }).getByRole("link", { name: /^Gebucht: / });
  await expect(booked).toHaveText("Gebucht: Kaltmiete + Nebenkostenvorauszahlung");
  await expect(page.getByRole("button", { name: "Gebucht (1)" })).toBeVisible();
  await page.getByRole("button", { name: "Offen (21)" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Partei D" })).toHaveCount(0);
  await page.getByRole("button", { name: "Alle (22)" }).click();
  await booked.click();
  await expect(page).toHaveURL(`/inbox?umsatz=${transactionId}`);
  await expect(panel.getByText(/^Bestätigt am/)).toBeVisible();
  await expect(panel.getByRole("group", { name: "Zeile 2" })).toBeVisible();
  await expect(panel.getByText("Rest: 0,00 €")).toBeVisible();
});

test("a line without an IBAN offers no rule box, only a purpose pattern", async ({ page, signedIn }) => {
  void signedIn;
  await importSample(page);
  await openInbox(page);

  // The Rechnungsabschluss lines name no counterparty and no IBAN in camt; such a line is
  // labelled by its booking text instead of „Ohne Gegenseite“.
  const queue = page.getByRole("list", { name: "Zahlungen zuordnen" });
  const line = queue.getByRole("button").filter({ hasText: /Verwendungszweck 5\b/ });
  await expect(line).toContainText("ABSCHLUSS");
  await line.click();

  const panel = page.getByRole("complementary", { name: "Zuordnung" });
  await expect(panel.getByRole("heading", { name: /ABSCHLUSS/ })).toBeVisible();
  await expect(panel.getByLabel("Künftig immer so zuordnen")).toHaveCount(0);
  const pattern = panel.getByLabel("Muster im Verwendungszweck (optional)");
  await expect(pattern).toBeVisible();

  await panel.getByRole("group", { name: "Zeile 1" }).getByLabel("Kategorie").selectOption({ label: "Sonstige Einnahme" });
  await expect(panel.getByText("Rest: 0,00 €")).toBeVisible();

  // A pattern that does not compile is refused, and nothing is written.
  await pattern.fill("Abschluss (");
  await panel.getByRole("button", { name: "Bestätigen" }).click();
  await expect(panel.getByText("Das Muster ist kein gültiger Suchausdruck", { exact: false })).toBeVisible();
  await expect(queue.getByRole("button").filter({ hasText: /Verwendungszweck 5\b/ })).toHaveCount(1);

  await pattern.fill("Verwendungszweck 5");
  await panel.getByRole("button", { name: "Bestätigen" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Zuordnung bestätigt." })).toContainText("Regel gespeichert");
  await expect(queue.getByRole("button").filter({ hasText: /Verwendungszweck 5\b/ })).toHaveCount(0);
});

test("the rule box follows the category until it is touched: on for bank fees, off for repairs", async ({ page, signedIn }) => {
  void signedIn;
  await importSample(page);
  await openInbox(page);

  // Partei H has an IBAN and no matcher owns it (no tenant, loan or owner account here).
  await page.getByRole("list", { name: "Zahlungen zuordnen" }).getByRole("button").filter({ hasText: "Partei H" }).click();
  const panel = page.getByRole("complementary", { name: "Zuordnung" });
  await expect(panel.getByRole("heading", { name: /Partei H/ })).toBeVisible();
  const learn = panel.getByLabel("Künftig immer so zuordnen");
  const category = panel.getByRole("group", { name: "Zeile 1" }).getByLabel("Kategorie");

  await expect(learn).toBeChecked();
  await category.selectOption({ label: "Kontoführung und Bankgebühren" });
  await expect(learn).toBeChecked();
  // Repairs never auto-confirm, so one answer must not silently become a standing rule.
  await category.selectOption({ label: "Erhaltungsaufwand (Reparatur)" });
  await expect(learn).not.toBeChecked();

  // Once ticked by hand, the choice sticks whatever the category.
  await learn.check();
  await category.selectOption({ label: "Kontoführung und Bankgebühren" });
  await category.selectOption({ label: "Erhaltungsaufwand (Reparatur)" });
  await expect(learn).toBeChecked();
});

test("a payer with other waiting amounts is asked whether the rule covers them (D49)", async ({ page, signedIn }) => {
  void signedIn;
  await importSample(page);
  await openInbox(page);

  // Partei C debits four times from one IBAN: 1.073,89 € twice, 862,15 € and 573,79 €.
  const queue = page.getByRole("list", { name: "Zahlungen zuordnen" });
  await queue.getByRole("button").filter({ hasText: "Partei C" }).filter({ hasText: "573,79" }).click();
  const panel = page.getByRole("complementary", { name: "Zuordnung" });
  await expect(panel.getByRole("heading", { name: /Partei C/ })).toBeVisible();
  const transactionId = new URL(page.url()).searchParams.get("umsatz") ?? "";
  const confirm = panel.getByRole("button", { name: "Bestätigen" });
  const learn = panel.getByLabel("Künftig immer so zuordnen");

  await panel.getByRole("group", { name: "Zeile 1" }).getByLabel("Kategorie").selectOption({ label: "Kontoführung und Bankgebühren" });
  await expect(learn).toBeChecked();
  const question = panel.getByRole("group", { name: /Gehören die auch zu/ });
  await expect(question).toHaveAccessibleName(
    "Von Partei C warten noch 3 Zahlungen mit anderen Beträgen: 1.073,89 € (2×), 862,15 € (1×). Gehören die auch zu Kontoführung und Bankgebühren?",
  );
  const all = question.getByLabel("Ja, alle so zuordnen");
  // Each answer says how many waiting payments its rule covers; none waits at 573,79 €.
  await expect(all).toHaveAccessibleName("Ja, alle so zuordnen: die Regel gilt dann auch für 3 wartende Zahlungen");
  const only = question.getByLabel("Nein, nur Zahlungen über 573,79 €");
  // No answer is preselected, and Bestätigen waits for one.
  await expect(all).not.toBeChecked();
  await expect(only).not.toBeChecked();
  await expect(confirm).toBeDisabled();

  // Without the rule there is nothing to ask.
  await learn.uncheck();
  await expect(question).toHaveCount(0);
  await expect(confirm).toBeEnabled();
  await learn.check();
  await expect(confirm).toBeDisabled();

  await only.check();
  await expect(confirm).toBeEnabled();
  const saved = page.waitForResponse((r) => r.url().endsWith(`/api/transactions/${transactionId}/allocations`) && r.request().method() === "PUT");
  await confirm.click();
  const response = await saved;
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { rule: { same_amount_only: boolean } | null };
  expect(body.rule?.same_amount_only).toBe(true);
  await expect(page.getByRole("status").filter({ hasText: "Zuordnung bestätigt." })).toContainText("Regel gespeichert");
});

test("a folded section opens on a click of its heading, and the choice is remembered", async ({ page, signedIn }) => {
  void signedIn;
  // The same payments as CSV and camt: the imports wait under Kontoauszüge and the 19 pairs
  // under Mögliche Doppelbuchungen, which is not the first section, so it starts folded.
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Mietkonto",
    bank: "Sparkasse",
    iban: "DE50100000000000000001",
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  await page.goto(`/accounts/${account.id}`);
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(CSV);
  await expect(page.getByRole("row").filter({ hasText: "csv_camt.csv" })).toBeVisible();
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(ZIP);
  await expect(page.getByRole("row").filter({ hasText: "camt052.zip" }).locator(".badge")).toHaveText("nicht abgestimmt");
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Zu prüfen" }).click();

  const duplicates = sectionToggle(page, "Mögliche Doppelbuchungen");
  const pairs = page.getByRole("list", { name: "Mögliche Doppelbuchungen" });
  await expect(duplicates).toHaveAttribute("aria-expanded", "false");
  await expect(pairs).toBeHidden();
  await duplicates.click();
  await expect(duplicates).toHaveAttribute("aria-expanded", "true");
  await expect(pairs.getByRole("listitem").first()).toBeVisible();

  // Remembered in this browser across a reload; „Alle zuklappen“ folds every section.
  await page.reload();
  await expect(duplicates).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "Alle zuklappen" }).click();
  await expect(duplicates).toHaveAttribute("aria-expanded", "false");
  await expect(sectionToggle(page, "Kontoauszüge")).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "Alle aufklappen" }).click();
  await expect(pairs.getByRole("listitem").first()).toBeVisible();
});

// Phase 6 D260 amended (9): a key typed under Zugangsschlüssel that no longer decrypts. The e2e
// API cannot rotate its session key mid-run, so GET /inbox answers with the card as the API
// builds it (backend tests/db/test_app_secrets.py checks its text).
test("an unreadable typed key shows its card under Automatisch, linking to Zugangsschlüssel", async ({ page, signedIn }) => {
  void signedIn;
  const card = {
    kind: "app_secret_unreadable",
    created_at: "2026-11-16T09:00:00Z",
    reasoning:
      "In der App gespeicherte Zugangsschlüssel lassen sich nicht mehr lesen, meist weil der Sitzungsschlüssel des Servers gewechselt wurde. Betroffen – Bank: FinTS-Produkt-ID. Der Bankabruf läuft nicht, weder täglich noch mit „Jetzt abrufen“. Hausbuch nimmt dafür nicht den Wert aus der Server-Konfiguration.",
    question: "Unter Einstellungen › Zugangsschlüssel neu eintragen oder löschen.",
  };
  await page.route("**/api/inbox", (route) => (route.request().method() === "GET" ? route.fulfill({ json: [card] }) : route.fallback()));
  await page.goto("/inbox");
  await expect(sectionToggle(page, "Automatisch")).toHaveAttribute("aria-expanded", "true");
  const queue = page.getByRole("region", { name: "Zu erledigen" });
  await expect(queue.getByText("Gespeicherte Zugangsschlüssel lassen sich nicht mehr lesen")).toBeVisible();
  await expect(queue.getByText("Schlüssel unlesbar")).toBeVisible();
  await expect(queue).toContainText("Der Bankabruf läuft nicht");
  await queue.getByRole("link", { name: "Zugangsschlüssel öffnen" }).click();
  await expect(page).toHaveURL(/\/settings#zugangsschluessel$/);
  await expect(page.getByRole("heading", { name: "Zugangsschlüssel (API keys)" })).toBeInViewport();
});
