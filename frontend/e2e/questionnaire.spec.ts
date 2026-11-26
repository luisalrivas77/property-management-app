// Jahresfragen (docs/phases/phase-4b.md §10, D110), asked per topic of Steuern › Eure Angaben and
// the property's in its Immobilie step under „Fragen zum Jahr“ since phase 10 §7.8 (b) (D294;
// /tax/<year>/fragen forwards there, e2e/angaben-topics.spec.ts): the 2026 questions walked
// topic by topic for a two-owner household with one property until every slot is answered: Ja with facts for
// A's commute, home office and a work item and for a child; a choice for the tax class, the
// insurance and the wage-replacement benefits; Nein / Trifft nicht zu for the rest; one slot
// left for later and answered from the last screen. Then 2027, which reuses the 2026 questions:
// A's commute is proposed from last year with its carried values marked, and the work item
// bought in 2026 is reconfirmed as no longer used, which records a superseding row with the
// disposal date. In between, two answers are changed: A's commute keeps "Ja" with a new
// distance (its row superseded), A's home office goes from "Ja" to "Nein" (its row withdrawn).
// Synthetic data only (owners A and B, invented figures).
import type { Locator, Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

const YEAR = 2026;
const address = { street: "Teststraße", house_number: "11", postal_code: "10115", city: "Berlin", country_code: "DE" };

type Row = { id: string; kind: string; tax_year: number; owner_id: string | null; supersedes_id: string | null; value: Record<string, unknown> };

function answers(page: Page): Locator {
  return page.getByRole("group", { name: "Antwort" });
}

async function answer(page: Page, label: string): Promise<void> {
  await answers(page).getByRole("button", { name: label, exact: true }).click();
}

/** Whether the topic's (or the property's) questions ended: its short summary, a paragraph where
 * a question is a heading. */
async function atEnd(page: Page): Promise<boolean> {
  return (await page.locator("p.fragen-question").count()) > 0;
}

/** The question on screen, and which group ("Fragen für A") it belongs to. */
async function screen(page: Page): Promise<{ question: string; group: string; position: number }> {
  const heading = page.locator("h2.fragen-question");
  await expect(heading).toBeVisible();
  const head = (await page.locator(".fragen-progress-head strong").textContent()) ?? "";
  return {
    question: ((await heading.textContent()) ?? "").trim(),
    group: ((await page.locator(".fragen-group").textContent()) ?? "").trim(),
    position: Number(/Frage (\d+) von/.exec(head)?.[1] ?? "0"),
  };
}

async function waitPast(page: Page, position: number): Promise<void> {
  await expect(async () => {
    const summary = await page.locator("p.fragen-question").count();
    const head = (await page.locator(".fragen-progress-head strong").allTextContents()).join("");
    const now = Number(/Frage (\d+) von/.exec(head)?.[1] ?? "0");
    expect(summary > 0 || now !== position).toBe(true);
  }).toPass();
}

async function fill(form: Locator, label: RegExp, value: string): Promise<void> {
  await form.getByLabel(label).fill(value);
}

/** Answers the question on screen for the synthetic household. */
async function answerScreen(page: Page, question: string, group: string): Promise<void> {
  const isA = group.startsWith("Fragen für A");
  const form = page.getByRole("form", { name: "Angaben" });
  switch (question) {
    case "Welche Steuerklasse gilt?":
      return answer(page, "IV");
    case "Gab es Lohnersatzleistungen, etwa Elterngeld, Mutterschaftsgeld oder Krankengeld?":
      return answer(page, "Keine");
    case "Wie ist die Krankenversicherung?":
      return answer(page, "Gesetzlich versichert");
    case "Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?":
      if (!isA) return answer(page, "Nein");
      await answer(page, "Ja");
      await fill(form, /^Arbeitsstätte/, "Büro A");
      await fill(form, /^Tage an der Arbeitsstätte/, "180");
      await fill(form, /^Entfernung/, "25");
      return form.getByRole("button", { name: "Speichern und weiter" }).click();
    case "An wie vielen Tagen wurde überwiegend zu Hause gearbeitet?":
      if (!isA) return answer(page, "Trifft nicht zu");
      await answer(page, "Ja");
      await fill(form, /^Homeoffice-Tage, an denen ein Arbeitsplatz/, "40");
      return form.getByRole("button", { name: "Speichern und weiter" }).click();
    case "Wurden Arbeitsmittel gekauft, etwa ein Computer, ein Bildschirm oder ein Schreibtisch?":
      if (!isA) return answer(page, "Nein");
      await answer(page, "Ja");
      // A subscription has its own question, so it is not offered here.
      await expect(form.getByLabel(/^Art/).locator("option", { hasText: "Abonnement" })).toHaveCount(0);
      await fill(form, /^Bezeichnung/, "Schreibtisch");
      await form.getByLabel(/^Art/).selectOption({ label: "Möbel" });
      // Above the low-value limit, so it is spread over years and comes back in 2027.
      await fill(form, /^Preis brutto/, "1500,00");
      await fill(form, /^Kaufdatum/, "2026-02-02");
      return form.getByRole("button", { name: "Speichern und weiter" }).click();
    case "Leben Kinder im Haushalt, und kam in diesem Jahr ein Kind dazu?":
      await answer(page, "Ja");
      await fill(form, /^Geburtsdatum/, "2026-03-14");
      return form.getByRole("button", { name: "Speichern und weiter" }).click();
    case "Gab es einen Umzug aus beruflichen Gründen?":
      // A reminder question shows the sentence it prints.
      await expect(page.getByText("Berufliche Umzugskosten von Hand in der Anlage N eintragen.")).toBeVisible();
      return answer(page, isA ? "Ja" : "Nein");
    case "Kontoführungsgebühren pauschal ansetzen?":
      return answer(page, isA ? "Nein" : "Trifft nicht zu");
    case "Gab es Spenden oder Mitgliedsbeiträge an gemeinnützige Organisationen?":
      return answer(page, "Trifft nicht zu");
    case "Gab es hohe Krankheitskosten oder andere außergewöhnliche Belastungen?":
      // The zumutbare Belastung from the estimate, a reminder figure (gap 6); 2027 has no
      // Anlage N rule set, so no estimate and no figure there.
      if (page.url().includes(`/tax/${YEAR}?`)) await expect(page.getByText(`Ihre zumutbare Belastung ${YEAR}:`)).toBeVisible();
      return answer(page, "Nein");
    default:
      return answer(page, "Nein");
  }
}

test("the 2026 questions are walked to all answered, and 2027 carries and reconfirms", async ({ page, signedIn }) => {
  void signedIn;
  test.setTimeout(360_000);
  const ownerA = await apiCall<{ id: string }>(page, "POST", "/api/owners", { full_name: "A", church_tax: false });
  await apiCall(page, "POST", "/api/owners", { full_name: "B", church_tax: false });
  await apiCall(page, "POST", "/api/properties", {
    label: "Wohnung Fragen",
    kind: "etw",
    address,
    build_year: 1994,
    notarized_on: "2026-03-10",
    purchase_date: "2026-07-01",
    purchase_price: "250000.00",
  });

  // Arbeitslohn first: A's first question is left for later.
  await page.goto(`/tax/${YEAR}?tab=angaben&thema=pay`);
  const first = await screen(page);
  expect(first.position).toBe(1);
  expect(first.question).toBe("Liegt die Lohnsteuerbescheinigung für das Jahr vor, oder Gehaltsabrechnungen?");
  expect(first.group).toContain("Fragen für A");
  await expect(page.getByText(/^Frage 1 von \d+$/)).toBeVisible();
  await expect(page.getByText(/^Warum:/)).toBeVisible();
  await page.getByRole("button", { name: "Überspringen" }).click();
  await waitPast(page, 1);

  const seen = new Set<string>();
  const walk = async () => {
    for (let step = 0; step < 60; step += 1) {
      if (await atEnd(page)) return;
      const { question, group, position } = await screen(page);
      seen.add(group.split(" · ")[0] ?? group);
      await answerScreen(page, question, group);
      await waitPast(page, position);
    }
    throw new Error("the questions did not end");
  };
  await walk();
  // The topic's end: the one question left open, reachable from there.
  await expect(page.locator("p.fragen-question")).toHaveText("1 Frage noch offen");
  await page.getByRole("button", { name: /^Fragen für A: Liegt die Lohnsteuerbescheinigung/ }).click();
  await answer(page, "Nein");
  await expect(page.locator("p.fragen-question")).toHaveText("Alle Fragen dieses Themas sind beantwortet.");

  for (const topic of ["couple", "work_costs", "insurance", "benefits", "children", "household"]) {
    await page.goto(`/tax/${YEAR}?tab=angaben&thema=${topic}`);
    await expect(page.locator("h2.fragen-question, p.fragen-question").first()).toBeVisible();
    await walk();
    await expect(page.locator("p.fragen-question")).toHaveText("Alle Fragen dieses Themas sind beantwortet.");
  }
  // The property's questions, in its Immobilie step.
  const properties = await apiCall<{ id: string }[]>(page, "GET", "/api/properties");
  await page.goto(`/tax/${YEAR}?schritt=immobilie&immobilie=${properties[0]!.id}`);
  const yearQuestions = page.getByRole("region", { name: "Fragen zum Jahr" });
  await expect(yearQuestions.locator("h2.fragen-question, p.fragen-question").first()).toBeVisible();
  await walk();
  expect([...seen].sort()).toEqual(["Fragen für A", "Fragen für B", "Fragen für den Haushalt", "Fragen zur Immobilie Wohnung Fragen"]);
  // Back to the Werbungskosten, whose questions are all answered, with what a „Ja“ leaves to type
  // by hand.
  await page.goto(`/tax/${YEAR}?tab=angaben&thema=work_costs`);
  await expect(page.locator("p.fragen-question")).toHaveText("Alle Fragen dieses Themas sind beantwortet.");
  await expect(page.getByText("Berufliche Umzugskosten von Hand in der Anlage N eintragen.")).toBeVisible();

  // Change two answers. An answered slot shows its answer and "Antwort ändern".
  await page.getByRole("button", { name: "Zur ersten Frage" }).click();
  await expect(page.getByText(/^Frage 1 von \d+$/)).toBeVisible();
  await expect(page.getByText("Ihre Antwort:", { exact: false }).first()).toBeVisible();
  const forward = async (text: string) => {
    for (let step = 0; step < 20; step += 1) {
      const { question, position } = await screen(page);
      if (question === text) return;
      await page.getByRole("button", { name: "Weiter", exact: true }).click();
      await waitPast(page, position);
    }
    throw new Error(`question not reached: ${text}`);
  };
  await forward("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");
  await expect(page.getByText("Ihre Antwort: Ja")).toBeVisible();
  await page.getByRole("button", { name: "Antwort ändern" }).click();
  const change = page.getByRole("form", { name: "Angaben" });
  await expect(change.getByLabel(/^Arbeitsstätte/)).toHaveValue("Büro A");
  await fill(change, /^Entfernung/, "30");
  await change.getByRole("button", { name: "Speichern und weiter" }).click();
  await expect(page.getByRole("heading", { name: "An wie vielen Tagen wurde überwiegend zu Hause gearbeitet?" })).toBeVisible();
  await expect(page.getByText("Ihre Antwort: Ja")).toBeVisible();
  await page.getByRole("button", { name: "Antwort ändern" }).click();
  await page.getByRole("form", { name: "Angaben" }).getByRole("button", { name: "Abbrechen" }).click();
  await expect(page.getByText("Bei „Nein“ oder „Trifft nicht zu“ wird die Angabe zu dieser Frage zurückgezogen.")).toBeVisible();
  await answer(page, "Nein");
  await expect(page.getByRole("heading", { name: "Gibt es ein eigenes, abgeschlossenes Arbeitszimmer, das der Mittelpunkt der ganzen Arbeit ist?" })).toBeVisible();
  await page.getByRole("button", { name: "Zurück" }).click();
  await expect(page.getByText("Ihre Antwort: Nein")).toBeVisible();

  const view = await apiCall<{ progress: { answered: number; total: number } }>(
    page,
    "GET",
    `/api/tax/questionnaire?tax_year=${YEAR}`,
  );
  expect(view.progress).toMatchObject({ answered: 42, total: 42 });
  const commute = await apiCall<Row[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&kind=commute&owner_id=${ownerA.id}`);
  expect(commute).toHaveLength(1);
  expect(commute[0]?.supersedes_id).not.toBeNull();
  expect(commute[0]?.value).toMatchObject({ workplace_label: "Büro A", days: 180, km: 30, km_car: 30 });
  const homeOffice = await apiCall<Row[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&kind=home_office&owner_id=${ownerA.id}`);
  expect(homeOffice).toHaveLength(0);
  const answers = await apiCall<{ slots: { key: string; owner_id: string | null; current_answer: { answer: string; supersedes_id: string | null } | null }[] }>(
    page,
    "GET",
    `/api/tax/questionnaire?tax_year=${YEAR}`,
  );
  const homeOfficeAnswer = answers.slots.find((s) => s.key === "owner.home_office_days" && s.owner_id === ownerA.id);
  expect(homeOfficeAnswer?.current_answer).toMatchObject({ answer: "no" });
  expect(homeOfficeAnswer?.current_answer?.supersedes_id).not.toBeNull();
  const children = await apiCall<Row[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&kind=child`);
  expect(children).toHaveLength(1);
  expect(children[0]?.value).toMatchObject({ born_on: "2026-03-14" });

  // 2027 reuses the 2026 questions. A's commute is proposed from 2026, its values marked.
  await page.goto(`/tax/${YEAR + 1}?tab=angaben&thema=work_costs`);
  await expect(page.getByText(`es gelten die von ${YEAR}`)).toBeVisible();
  const skipTo = async (text: string) => {
    for (let step = 0; step < 20; step += 1) {
      const { question, position } = await screen(page);
      if (question === text) return;
      await page.getByRole("button", { name: "Überspringen" }).click();
      await waitPast(page, position);
    }
    throw new Error(`question not reached: ${text}`);
  };
  await skipTo("Ging es regelmäßig zur Arbeitsstätte, und hat sich der Weg geändert?");
  await expect(page.getByText("Vorjahr: Ja")).toBeVisible();
  await answer(page, "Ja");
  const form = page.getByRole("form", { name: "Angaben" });
  await expect(form.getByText("Vorjahr übernommen — prüfen und bestätigen")).toBeVisible();
  await expect(form.getByLabel(/^Arbeitsstätte/)).toHaveValue("Büro A");
  // The carried value is marked "Vorjahr" next to its label.
  await expect(form.getByLabel(/^Arbeitsstätte\s*Vorjahr$/)).toBeVisible();
  await expect(form.getByLabel(/^Entfernung/)).toHaveValue("30");
  await form.getByRole("button", { name: "Abbrechen" }).click();
  await page.getByRole("button", { name: "Überspringen" }).click();

  // A's desk from 2026 is reconfirmed: no longer used since the end of June.
  await skipTo("Werden Arbeitsmittel aus früheren Jahren noch genutzt?");
  const desk = page.getByRole("radiogroup", { name: /^Schreibtisch/ });
  await expect(desk).toContainText("weiter genutzt");
  await desk.getByLabel("nicht mehr genutzt seit …").check();
  await page.getByLabel("Nicht mehr genutzt seit", { exact: true }).fill("2027-06-30");
  await answer(page, "Ja");
  await page.getByRole("form", { name: "Angaben" }).getByRole("button", { name: "Speichern und weiter" }).click();
  await expect(page.getByRole("heading", { name: "Gab es beruflich genutzte Abonnements, etwa Software oder Fachzeitschriften?" })).toBeVisible();

  const equipment = await apiCall<Row[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&kind=work_equipment&owner_id=${ownerA.id}`);
  const disposal = equipment.find((row) => row.supersedes_id !== null);
  expect(disposal?.value).toMatchObject({ label: "Schreibtisch", item_class: "furniture", disposed_on: "2027-06-30" });
});
