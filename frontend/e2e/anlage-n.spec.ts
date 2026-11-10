// Steuern › Anlage N (phase-4b.md §10, step 3): the tab shows one owner's draft as the API
// computes it, every Eingaben block saves an append-only return_input row (a change
// supersedes the row, Entfernen withdraws it), the owner switch changes only the URL, and the
// Gesamt card above the tabs renders the whole-return estimate the API computed. Synthetic
// data only (owners "A" and "B", invented amounts).
import type { Locator, Page } from "@playwright/test";

import { apiCall, expect, test } from "./support";

type Owner = { id: string };
type Row = { id: string; kind: string; tax_year: number; supersedes_id: string | null; value: Record<string, unknown> };
type Estimate = { result: string; zve: string; questionnaire: { answered: number; total: number } };

const YEAR = 2026;

/** "365698.33" → "365.698,33 €", the way the screen formats money (format.ts formatMoney). */
function euro(value: string): string {
  const [int = "", frac = ""] = value.replace(/^-/, "").split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${value.startsWith("-") ? "-" : ""}${grouped},${frac.padEnd(2, "0")} €`;
}

async function expectedPay(page: Page, ownerId: string, amount: string): Promise<void> {
  await apiCall(page, "POST", "/api/income-inputs", {
    kind: "expected_gross_pay",
    tax_year: YEAR,
    owner_id: ownerId,
    value: { amount },
    supersedes_id: null,
    withdrawn: false,
  });
}

/** Open a block's form with its button, fill the given fields, save, and wait for the row. */
async function save(page: Page, button: string, form: string, fields: Record<string, string>): Promise<void> {
  const panel = page.getByRole("region", { name: "Eingaben", exact: true });
  await panel.getByRole("button", { name: button, exact: true }).click();
  const dialog: Locator = panel.getByRole("form", { name: form });
  for (const [label, value] of Object.entries(fields)) {
    const input = dialog.getByLabel(label, { exact: true });
    if ((await input.evaluate((el) => el.tagName)) === "SELECT") await input.selectOption(value);
    else await input.fill(value);
  }
  await dialog.getByRole("button", { name: "Speichern" }).click();
  await expect(dialog).toBeHidden();
}

test("the Anlage N tab saves every kind of input, switches owner and shows the estimate", async ({ page, signedIn }) => {
  void signedIn;
  const a = await apiCall<Owner>(page, "POST", "/api/owners", { full_name: "A. Muster", church_tax: false });
  const b = await apiCall<Owner>(page, "POST", "/api/owners", { full_name: "B. Muster", church_tax: false });
  await expectedPay(page, a.id, "52000.00");
  await expectedPay(page, b.id, "38000.00");

  await page.goto(`/tax/${YEAR}?tab=anlage-n`);
  await expect(page.getByRole("heading", { level: 1, name: "A. Muster" })).toBeVisible();
  const tabs = page.getByRole("navigation", { name: "Formulare" });
  await expect(tabs.getByRole("link", { name: "Anlage N" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("region", { name: "Arbeitslohn" })).toContainText("aus dem erwarteten Bruttolohn");
  await expect(page.getByText("Ein Entwurf zum Prüfen, keine Steuerberatung.")).toBeVisible();
  // The field sheet (step 4) is queued: the e2e API runs without its job worker
  // (playwright.config.ts), so the export stays queued and the card says it is being made;
  // the written file and the history are covered by backend/tests/api/test_anlage_n_exports.py.
  const handOver = page.getByRole("region", { name: "Übergabe" });
  await handOver.getByRole("button", { name: "Feldblatt (PDF)" }).click();
  await expect(handOver.getByRole("status")).toHaveText("Feldblatt Anlage N (PDF) wird erstellt …");
  await expect(handOver.getByRole("button", { name: "Feldblatt (PDF)" })).toBeDisabled();
  await expect(handOver.getByRole("alert")).toHaveCount(0);

  // One input per block.
  await save(page, "Arbeitsweg hinzufügen", "Arbeitsweg eintragen", {
    "Arbeitsstätte (Ort, Straße)": "Werk Nord",
    "Tage an der Arbeitsstätte": "200",
    "Einfache Entfernung (volle km)": "20",
    "davon mit dem Auto (km)": "20",
  });
  await save(page, "Homeoffice-Tage eintragen", "Homeoffice-Tage eintragen", {
    "Tage zu Hause, obwohl ein Arbeitsplatz beim Arbeitgeber da war": "40",
  });
  await save(page, "Arbeitszimmer eintragen", "Arbeitszimmer eintragen", {
    "Monate genutzt": "3",
    "Zimmer (m²)": "12",
    "Wohnung (m²)": "80",
    "Jahresmiete (oder Kosten des Eigentums)": "9600",
  });
  await save(page, "Arbeitsmittel hinzufügen", "Arbeitsmittel eintragen", {
    Bezeichnung: "Laptop",
    "Preis brutto": "1200",
    "Kaufdatum (auch Vorjahre)": `${YEAR}-02-01`,
  });
  await save(page, "Telekommunikation eintragen", "Telekommunikation eintragen", { "Rechnungen im Jahr": "600" });
  await save(page, "Beiträge zu Berufsverbänden hinzufügen", "Beiträge zu Berufsverbänden eintragen", {
    Verband: "Testverband",
    "Beiträge im Jahr": "120",
  });
  await save(page, "Fortbildung hinzufügen", "Fortbildung eintragen", { Bezeichnung: "Fachbuch", Betrag: "45" });
  await save(page, "Sonstige Werbungskosten hinzufügen", "Sonstige Werbungskosten eintragen", {});
  await save(page, "Lohnersatzleistungen hinzufügen", "Lohnersatzleistungen eintragen", { "Betrag im Jahr": "1500" });
  await save(page, "Weitere Versicherungen eintragen", "Weitere Versicherungen eintragen", { "Beiträge im Jahr": "90" });
  await save(page, "Vorsorge manuell eintragen", "Vorsorge manuell eintragen", {
    "Rentenversicherung (Arbeitnehmeranteil)": "4800",
    "Kranken- und Pflegeversicherung (Basis)": "4100",
  });

  const rowsPath = `/api/return-inputs?tax_year=${YEAR}&owner_id=${a.id}`;
  const rows = await apiCall<Row[]>(page, "GET", rowsPath);
  expect(rows.map((row) => row.kind).sort()).toEqual(
    [
      "commute",
      "home_office",
      "other_expense",
      "other_insurance",
      "professional_association",
      "replacement_income",
      "telecom",
      "training",
      "vorsorge_override",
      "work_equipment",
      "work_room",
    ].sort(),
  );
  expect(rows.find((row) => row.kind === "work_equipment")?.tax_year).toBe(YEAR);
  expect(rows.find((row) => row.kind === "commute")?.value).toMatchObject({ workplace_label: "Werk Nord", days: 200, km: 20 });

  // The draft now itemises: the Werbungskosten card explains each block and lists the totals;
  // the form lines (route 1 as days and km) fold out below.
  const costs = page.getByRole("region", { name: "Werbungskosten", exact: true });
  await expect(costs).toContainText("Werk Nord: Auto: 200 Tage × 20 km × 0,38 € = 1.520,00 €");
  await expect(costs).toContainText("40 Tage × 6,00 € = 240,00 €");
  await expect(costs).toContainText("Arbeitsmittel: Laptop");
  await expect(costs).toContainText("Fortbildung: Fachbuch");
  await costs.getByText("Formularzeilen").click();
  await expect(costs).toContainText("Arbeitsweg 1");
  const draft = await apiCall<{ wk_itemised: string; wk_applied: string }>(
    page,
    "GET",
    `/api/owners/${a.id}/anlage-n?tax_year=${YEAR}`,
  );
  await expect(costs).toContainText(euro(draft.wk_itemised));
  await expect(costs).toContainText(euro(draft.wk_applied));

  // A change is a new row naming the old one; Entfernen withdraws a row.
  const training = rows.find((row) => row.kind === "training");
  const panel = page.getByRole("region", { name: "Eingaben", exact: true });
  const trainingBlock = panel.getByRole("group", { name: "Fortbildung", exact: true });
  await trainingBlock.getByRole("button", { name: "ändern" }).click();
  const change = trainingBlock.getByRole("form", { name: "Fortbildung ändern" });
  await change.getByLabel("Betrag", { exact: true }).fill("65");
  await change.getByRole("button", { name: "Speichern" }).click();
  await expect(change).toBeHidden();
  await expect(trainingBlock).toContainText("65,00 €");
  await panel.getByRole("group", { name: "Weitere Versicherungen", exact: true }).getByRole("button", { name: "Entfernen" }).click();
  await expect(panel.getByRole("button", { name: "Weitere Versicherungen eintragen" })).toBeVisible();
  const after = await apiCall<Row[]>(page, "GET", rowsPath);
  const changed = after.find((row) => row.kind === "training");
  expect(changed?.supersedes_id).toBe(training?.id);
  expect(changed?.value).toMatchObject({ amount: "65.00" });
  expect(after.some((row) => row.kind === "other_insurance")).toBe(false);

  // The owner switch changes only the URL; B has none of A's inputs.
  await page.getByRole("navigation", { name: "Eigentümer", exact: true }).getByRole("link", { name: "B. Muster" }).click();
  await expect(page).toHaveURL(new RegExp(`eigentuemer=${b.id}`));
  await expect(page.getByRole("heading", { level: 1, name: "B. Muster" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Arbeitsweg hinzufügen" })).toBeVisible();
  await expect(panel.getByRole("group", { name: "Arbeitsweg", exact: true })).not.toContainText("Werk Nord");

  // The Gesamt card renders the API's estimate, the figures as sent.
  const estimate = await apiCall<Estimate>(page, "GET", `/api/tax/return-estimate?tax_year=${YEAR}`);
  const card = page.getByRole("region", { name: "Gesamt" });
  await expect(card).toContainText(
    estimate.result.startsWith("-") ? "Voraussichtliche Erstattung" : "Voraussichtliche Nachzahlung",
  );
  await expect(card).toContainText(euro(estimate.result.replace(/^-/, "")));
  await expect(card).toContainText(
    `Jahresfragen ${YEAR}: ${estimate.questionnaire.answered} von ${estimate.questionnaire.total} beantwortet`,
  );
  await card.getByText("So rechnet die Schätzung").click();
  await expect(card).toContainText("Anlage N A. Muster");
  await expect(card).toContainText(euro(estimate.zve));

  // The Anlage V tab keeps the card, and says there is no property yet.
  await tabs.getByRole("link", { name: "Anlage V" }).click();
  await expect(page.getByText("Noch keine Immobilie.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Gesamt" })).toContainText(euro(estimate.result.replace(/^-/, "")));
});

async function taxClass(page: Page, ownerId: string, choice: string): Promise<void> {
  await apiCall(page, "POST", "/api/tax/questionnaire/answers", {
    answer: { question_key: "owner.tax_class", tax_year: YEAR, owner_id: ownerId, answer: "yes", choice },
  });
}

test("the Anlage N tab names the open questions, the Gesamt card the tax classes and each line's sources", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const a = await apiCall<Owner>(page, "POST", "/api/owners", { full_name: "A. Muster", church_tax: false });
  const b = await apiCall<Owner>(page, "POST", "/api/owners", { full_name: "B. Muster", church_tax: false });
  await expectedPay(page, a.id, "52000.00");
  await expectedPay(page, b.id, "38000.00");
  // No certificate yet, so the classes come from the Jahresfragen.
  await taxClass(page, a.id, "iii");
  await taxClass(page, b.id, "v");

  await page.goto(`/tax/${YEAR}?tab=anlage-n`);
  await expect(page.getByRole("heading", { level: 1, name: "A. Muster" })).toBeVisible();

  // Offene Punkte: A's open questions and the household's, by their text; the answered tax
  // class is not among them, nor a property question.
  const open = page.getByRole("region", { name: "Offene Punkte" });
  await expect(open).toContainText("Liegt die Lohnsteuerbescheinigung für das Jahr vor, oder Gehaltsabrechnungen?");
  await expect(open).toContainText("Leben Kinder im Haushalt, und kam in diesem Jahr ein Kind dazu?");
  await expect(open).not.toContainText("Welche Steuerklasse gilt?");

  // The Gesamt card explains the refund or back-payment by the classes; no figure depends on it.
  const card = page.getByRole("region", { name: "Gesamt" });
  await expect(card).toContainText("Steuerklassen III/V: die Erstattung oder Nachzahlung gleicht den Lohnsteuerabzug aus.");

  // Each line of the fold-out opens its own sources: Vorsorge from each owner's expected pay.
  await card.getByText("So rechnet die Schätzung").click();
  const vorsorge = card.getByRole("listitem").filter({ hasText: "Vorsorgeaufwendungen" });
  await vorsorge.getByRole("button").click();
  const sources = page.getByRole("dialog", { name: "Quellen: Vorsorgeaufwendungen" });
  await expect(sources).toContainText("Einkommensangabe");
  const incomeLinks = sources.getByRole("link", { name: "Einkommen ansehen" });
  await expect(incomeLinks).toHaveCount(2);
  await expect(incomeLinks.first()).toHaveAttribute("href", "/owners");
  await sources.getByRole("button", { name: "Schließen" }).click();
  await expect(card.getByRole("listitem").filter({ hasText: /^Einkommensteuer/ }).getByRole("button", { name: "2 Quellen" })).toBeVisible();

  // A question's link opens the Jahresfragen at that question.
  await open
    .getByRole("listitem")
    .filter({ hasText: "Leben Kinder im Haushalt" })
    .getByRole("link", { name: "Beantworten" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/tax/${YEAR}/fragen\\?frage=household\\.children$`));
  await expect(page.locator("h2.fragen-question")).toHaveText("Leben Kinder im Haushalt, und kam in diesem Jahr ein Kind dazu?");
});
