// The field help of Steuern › Eure Angaben (phase 10 §7.8 (b) item 3, D294): every field a form
// renders says what it is and where to find it. Two checks fail when a field has none:
// the forms' field list (components/returnInputFields.ts, which types the renderers) against
// GET /api/tax/{year}/field-help, in German and English; and every topic screen, its first
// question's form open, carrying no `data-help-missing` marker (components/fieldHelp.tsx renders
// one for a field the help does not name). Synthetic owners A and B only.
import { FORM_FIELDS } from "../src/components/returnInputFields";
import { apiCall, expect, test } from "./support";

const YEAR = 2026;
const TOPICS = ["couple", "pay", "work_costs", "insurance", "benefits", "children", "household"] as const;

type Text = { what: string; where: string; example: string };
type Help = { forms: Record<string, Record<string, { de: Text; en: Text; rare: boolean; source: string }>>; sources: Record<string, unknown> };

test("every field of Eure Angaben has its help, in German and English", async ({ page, signedIn }) => {
  void signedIn;
  const help = await apiCall<Help>(page, "GET", `/api/tax/${YEAR}/field-help`);
  const missing: string[] = [];
  for (const [form, fields] of Object.entries(FORM_FIELDS)) {
    for (const field of fields) {
      const entry = help.forms[form]?.[field];
      if (!entry) {
        missing.push(`${form}.${field}`);
        continue;
      }
      for (const text of [entry.de, entry.en]) {
        if (!text.what.trim() || !text.where.trim() || !text.example.trim()) missing.push(`${form}.${field} (empty text)`);
      }
      if (!(entry.source in help.sources)) missing.push(`${form}.${field} (unknown source ${entry.source})`);
    }
  }
  expect(missing).toEqual([]);
});

test("no topic screen renders a field without help", async ({ page, signedIn }) => {
  void signedIn;
  test.setTimeout(120_000);
  await apiCall(page, "POST", "/api/owners", { full_name: "A. Muster", church_tax: false });
  await apiCall(page, "POST", "/api/owners", { full_name: "B. Muster", church_tax: false });
  for (const topic of TOPICS) {
    const loaded = page.waitForResponse((r) => r.url().includes(`/api/tax/${YEAR}/field-help`) && r.ok());
    await page.goto(`/tax/${YEAR}?tab=angaben&thema=${topic}`);
    await loaded;
    await expect(page.locator("h2.fragen-question, p.fragen-question").first()).toBeVisible();
    const without = page.getByText("Ohne Dokument eintragen");
    if (await without.count()) await without.first().click();
    // The first question's form, where „Ja“ opens one (a „Ja“ that needs no form answers the
    // question and moves on).
    const yes = page.getByRole("group", { name: "Antwort" }).getByRole("button", { name: "Ja", exact: true });
    if (await yes.count()) {
      // „Frage N von M“: two owners' slots of one question share its text.
      const position = page.locator(".fragen-progress-head strong");
      const before = await position.textContent();
      const form = page.getByRole("form", { name: "Angaben" });
      await yes.click();
      await expect
        .poll(async () => (await form.count()) > 0 || (await position.count()) === 0 || (await position.textContent()) !== before)
        .toBe(true);
      if (await form.count()) await expect(form.locator(".field-help").first()).toBeVisible();
    }
    const markers = await page.locator("[data-help-missing]").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-help-missing")));
    expect(markers, `topic ${topic}`).toEqual([]);
  }
});
