// Steuern › Eure Angaben as topics (phase 10 §7.8 (b), D294, mockup 22c): the list with its
// proposals box and seven topic rows; „Jetzt dran“ opening the first open topic; a topic asking
// one question at a time, a form under a „Ja“ with every field's help, the example in the panel
// and the rare fields folded under „Weitere Angaben (selten nötig)“; a booked Lohnersatz line
// shown as its question's pre-filled answer and taken over with „Ja, übernehmen“. Then the old
// Jahresfragen address /tax/<year>/fragen landing on the first open topic, on a question in its
// topic, and on a property's question in its Immobilie step. Synthetic data only (owners A and
// B, invented amounts).
import { readFileSync } from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

type Owner = { id: string };

const YEAR = 2026;
const SAMPLE_ZIP = path.resolve(import.meta.dirname, "..", "..", "backend", "tests", "fixtures", "phase2", "bank_samples", "camt052.zip");
const address = { street: "Teststraße", house_number: "31", postal_code: "10115", city: "Berlin", country_code: "DE" };

async function twoOwners(page: Page): Promise<[Owner, Owner]> {
  const a = await apiCall<Owner>(page, "POST", "/api/owners", { full_name: "A. Muster", church_tax: false });
  const b = await apiCall<Owner>(page, "POST", "/api/owners", { full_name: "B. Muster", church_tax: false });
  return [a, b];
}

/** A's private account with the sample's credit booked as Elterngeld & Lohnersatz, which the
 * server proposes as a replacement_income row (phase 10 §7.8 (a), D293). */
async function bookedBenefit(page: Page, ownerId: string): Promise<void> {
  const origin = new URL(page.url()).origin;
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Girokonto A",
    bank: "Testbank",
    iban: "DE50100000000000000001", // the sample file's own
    purpose: "private",
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  await apiCall(page, "POST", `/api/accounts/${account.id}/ownership`, { shares: [{ owner_id: ownerId, share_numerator: 1, share_denominator: 1 }] }, 200);
  const imported = await page.request.post(`/api/accounts/${account.id}/imports`, {
    multipart: { file: { name: "umsaetze.zip", mimeType: "application/zip", buffer: readFileSync(SAMPLE_ZIP) } },
    headers: { Origin: origin },
  });
  expect(imported.status()).toBe(201);
  const lines = await apiCall<{ id: string; amount: string }[]>(page, "GET", `/api/accounts/${account.id}/transactions`);
  const line = lines.find((t) => !t.amount.startsWith("-") && t.amount !== "0.00");
  if (!line) throw new Error("the sample has no credit");
  const booked = await page.request.fetch(`/api/transactions/${line.id}/allocations`, {
    method: "PUT",
    data: {
      allocation_set: {
        transaction_id: line.id,
        allocations: [{ amount: line.amount, category: "hh_wage_replacement" }],
        method: "user",
        confidence: "1.000",
      },
      learn_rule: false,
    },
    headers: { Origin: origin },
  });
  expect(booked.status()).toBe(200);
}

test("Eure Angaben: the topics, one question at a time, every field's help and a booked proposal", async ({ page, signedIn }) => {
  void signedIn;
  test.setTimeout(180_000);
  const [a] = await twoOwners(page);
  await bookedBenefit(page, a.id);

  // „Jetzt dran“ opens the first open topic, not the list.
  await page.goto(`/tax/${YEAR}`);
  const next = page.getByRole("region", { name: "Jetzt dran" }).getByRole("link", { name: /^Weiter: Eure Angaben › / });
  await expect(next).toHaveAttribute("href", new RegExp(`^/tax/${YEAR}\\?tab=angaben&thema=`));
  await expect(page.getByRole("region", { name: "Schritte", exact: true }).getByRole("link").first()).toHaveAttribute(
    "href",
    `/tax/${YEAR}?tab=angaben`,
  );

  // The list: the proposals box and the seven topics in the server's order, each with its state.
  await page.goto(`/tax/${YEAR}?tab=angaben`);
  const box = page.getByRole("region", { name: "Aus euren Konten: 1 Vorschlag" });
  await expect(box).toBeVisible();
  const topics = page.getByRole("region", { name: "Themen" }).getByRole("link");
  await expect(topics).toHaveCount(7);
  const names = ["Ihr beide", "Arbeitslohn", "Werbungskosten", "Versicherungen", "Lohnersatzleistungen", "Kinder", "Haushalt und Spenden"];
  for (const [at, name] of names.entries()) await expect(topics.nth(at)).toContainText(name);
  await expect(topics.nth(0)).toContainText("A. Muster: Bundesland fehlt");
  await expect(topics.nth(4)).toContainText("1 Vorschlag aus euren Konten");
  // „Der Reihe nach prüfen“ walks the proposals one at a time.
  await box.getByRole("button", { name: "Der Reihe nach prüfen" }).click();
  const walk = page.getByRole("dialog", { name: "Der Reihe nach prüfen" });
  await expect(walk).toContainText("Vorschlag 1 von 1");
  await expect(walk).toContainText("aus euren Konten");
  await walk.getByRole("button", { name: "Später" }).click();
  await expect(walk).toContainText("Alle Vorschläge sind geprüft.");
  // The walk's own „Schließen“ (the dialog's head has a close button of the same name).
  await walk.getByRole("button", { name: "Schließen" }).last().click();

  // A topic: one question at a time, „Überspringen“ and „Zurück“ as drawn.
  await topics.nth(2).click();
  await expect(page).toHaveURL(`/tax/${YEAR}?tab=angaben&thema=work_costs`);
  await expect(page.getByRole("heading", { level: 2, name: "Werbungskosten" })).toBeVisible();
  const question = page.locator("h2.fragen-question");
  await expect(question).toHaveText("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");
  await expect(page.locator(".fragen-group")).toContainText("Fragen für A");
  await page.getByRole("button", { name: "Überspringen" }).click();
  await expect(question).not.toHaveText("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");
  await page.getByRole("button", { name: "Zurück" }).click();
  await expect(question).toHaveText("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");

  // „Ja“ opens the form: every field says what it is and where to find it, the example behind ⓘ,
  // and the rare fields wait under „Weitere Angaben (selten nötig)“.
  await page.getByRole("group", { name: "Antwort" }).getByRole("button", { name: "Ja", exact: true }).click();
  const form = page.getByRole("form", { name: "Angaben" });
  await expect(form.locator(".field-help").first()).toBeVisible();
  await expect(page.locator("[data-help-missing]")).toHaveCount(0);
  const rare = form.locator("details.field-rare");
  await expect(rare.locator("summary")).toHaveText("Weitere Angaben (selten nötig)");
  await expect(rare).not.toHaveAttribute("open", "");
  await form.locator(".field-help-info").first().click();
  const helpPanel = page.getByRole("complementary").filter({ hasText: "Wo steht das?" }).or(page.getByRole("dialog").filter({ hasText: "Wo steht das?" }));
  await expect(helpPanel).toContainText("Beispiel");
  await form.getByLabel(/^Arbeitsstätte/).fill("Büro A");
  await form.getByLabel(/^Tage an der Arbeitsstätte/).fill("180");
  await form.getByLabel(/^Entfernung/).fill("25");
  await form.getByRole("button", { name: "Speichern und weiter" }).click();
  await expect(question).not.toHaveText("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");
  const commute = await apiCall<{ value: Record<string, unknown> }[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&kind=commute&owner_id=${a.id}`);
  expect(commute).toHaveLength(1);
  expect(commute[0]?.value).toMatchObject({ workplace_label: "Büro A", days: 180, km: 25 });

  // Lohnersatzleistungen: the booked line is its question's pre-filled answer.
  await page.goto(`/tax/${YEAR}?tab=angaben`);
  await topics.nth(4).click();
  await expect(question).toHaveText("Gab es Lohnersatzleistungen, etwa Elterngeld, Mutterschaftsgeld oder Krankengeld?");
  const prefill = page.locator(".angaben-prefill");
  await expect(prefill).toContainText("aus euren Konten");
  await expect(prefill.getByRole("button", { name: "Anders eintragen" })).toBeVisible();
  // The sample's purpose text names no benefit, so the person picks it.
  await prefill.getByRole("combobox", { name: "Art der Leistung" }).selectOption("elterngeld");
  await prefill.getByRole("button", { name: "Ja, übernehmen" }).click();
  await expect(prefill).toBeHidden();
  const rows = await apiCall<{ kind: string }[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&owner_id=${a.id}`);
  expect(rows.filter((row) => row.kind === "replacement_income")).toHaveLength(1);

  // Back on the list the proposal is gone.
  await page.goto(`/tax/${YEAR}?tab=angaben`);
  await expect(page.getByRole("region", { name: "Themen" })).toBeVisible();
  await expect(page.getByRole("region", { name: /^Aus euren Konten/ })).toHaveCount(0);
});

test("the old Jahresfragen address opens Eure Angaben at its question", async ({ page, signedIn }) => {
  void signedIn;
  const [, b] = await twoOwners(page);
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Alt",
    kind: "etw",
    address,
    build_year: 1994,
    notarized_on: "2026-03-10",
    purchase_date: "2026-07-01",
    purchase_price: "250000.00",
  });
  const question = page.locator("h2.fragen-question");

  // Without a question: the first open topic.
  await page.goto(`/tax/${YEAR}/fragen`);
  await expect(page).toHaveURL(new RegExp(`/tax/${YEAR}\\?tab=angaben&thema=\\w+$`));
  await expect(question).toBeVisible();

  // A household question: its topic, at that question.
  await page.goto(`/tax/${YEAR}/fragen?frage=household.children`);
  await expect(page).toHaveURL(`/tax/${YEAR}?tab=angaben&thema=children&frage=household.children`);
  await expect(question).toHaveText("Leben Kinder im Haushalt, und kam in diesem Jahr ein Kind dazu?");

  // An owner's question: its topic, for that owner.
  await page.goto(`/tax/${YEAR}/fragen?frage=owner.commute&eigentuemer=${b.id}`);
  await expect(page).toHaveURL(`/tax/${YEAR}?tab=angaben&thema=work_costs&frage=owner.commute&eigentuemer=${b.id}`);
  await expect(question).toHaveText("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");
  await expect(page.locator(".fragen-group")).toContainText("Fragen für B");

  // A property's question: its Immobilie step, under „Fragen zum Jahr“.
  await page.goto(`/tax/${YEAR}/fragen?frage=property.trips&immobilie=${property.id}`);
  await expect(page).toHaveURL(`/tax/${YEAR}?schritt=immobilie&immobilie=${property.id}&frage=property.trips`);
  const yearQuestions = page.getByRole("region", { name: "Fragen zum Jahr" });
  await expect(yearQuestions.locator(".fragen-group")).toContainText("Wohnung Alt");

  // An old anchor of the one-page form opens the topic of its kind.
  await page.goto(`/tax/${YEAR}?tab=angaben#eingabe-other_insurance`);
  await expect(page).toHaveURL(`/tax/${YEAR}?tab=angaben&thema=insurance#eingabe-other_insurance`);
  await expect(page.locator("#eingabe-other_insurance")).toBeVisible();
});
