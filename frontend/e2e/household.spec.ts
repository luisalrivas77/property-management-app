// Eigentümer › Einkommen, phase 4b (docs/phases/phase-4b.md §10): the gross pay's source chip
// (a typed estimate, and a payslip's projection naming its month), a child added on the Kinder
// block shown by its birth month only, a §35a cost and a donation added on the Haushalt block,
// and the links to the Anlage N tab and the Jahresfragen. Synthetic data only (owners A and B).
import { apiCall, expect, test } from "./support";

const YEAR = 2026;

test("the Einkommen section shows the pay source and adds a child and household costs", async ({ page, signedIn }) => {
  void signedIn;
  const origin = new URL(page.url()).origin;
  const ownerA = await apiCall<{ id: string }>(page, "POST", "/api/owners", { full_name: "A", church_tax: false });
  const ownerB = await apiCall<{ id: string }>(page, "POST", "/api/owners", { full_name: "B", church_tax: false });
  await apiCall(page, "POST", "/api/income-inputs", {
    kind: "expected_gross_pay",
    tax_year: YEAR,
    owner_id: ownerA.id,
    value: { amount: "48000.00" },
    withdrawn: false,
  });
  // B's August payslip (a Gehaltsabrechnung document), projected over the 4 months left.
  const upload = await page.request.post("/api/documents", {
    multipart: { file: { name: "Gehalt.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% Gehalt\n%%EOF\n") } },
    headers: { Origin: origin },
  });
  expect(upload.status()).toBe(201);
  const document = (await upload.json()) as { id: string };
  const typed = await page.request.fetch(`/api/documents/${document.id}/typing`, {
    method: "PUT",
    data: { typing: { type: "gehaltsabrechnung", document_date: "2026-08-31" } },
    headers: { Origin: origin },
  });
  expect(typed.status()).toBe(200);
  await apiCall(page, "POST", "/api/return-inputs", {
    kind: "payslip",
    tax_year: YEAR,
    owner_id: ownerB.id,
    document_id: document.id,
    value: {
      period_month: "2026-08-01",
      employer: "Arbeitgeber B",
      tax_class: 4,
      months_remaining_paid: 4,
      gross: { month: "3000.00", ytd: "24000.00" },
      income_tax: { month: "400.00", ytd: "3200.00" },
    },
    withdrawn: false,
  });

  await page.goto("/owners");
  await page.getByLabel("Steuerjahr").selectOption(String(YEAR));
  const income = page.getByRole("table").filter({ hasText: `Erwartetes Bruttogehalt ${YEAR}` });
  const rowA = income.getByRole("row").filter({ hasText: /^A/ });
  await expect(rowA).toContainText("Schätzung");
  const rowB = income.getByRole("row").filter({ hasText: /^B/ });
  await expect(rowB).toContainText("Hochrechnung aus Gehaltsabrechnung August (4 Monate verbleibend)");

  await expect(page.getByRole("link", { name: `Anlage N ${YEAR} ansehen` })).toHaveAttribute("href", `/tax/${YEAR}?tab=anlage-n`);
  await expect(page.getByRole("link", { name: `Jahresfragen ${YEAR} beantworten` })).toHaveAttribute(
    "href",
    `/tax/${YEAR}/fragen`,
  );

  // A child: only the month is shown, and the row holds a birth date and nothing else.
  await expect(page.getByText(`Für ${YEAR} sind keine Kinder erfasst.`)).toBeVisible();
  const childForm = page.getByRole("form", { name: "Kind hinzufügen" });
  await childForm.getByLabel("Geburtsdatum").fill("2026-03-14");
  await childForm.getByRole("button", { name: "Kind hinzufügen" }).click();
  await expect(page.getByText("Kind 1 (geboren März 2026) · Kindergeld ab März 2026")).toBeVisible();
  await expect(page.getByText("14.03.2026")).toHaveCount(0);
  const children = await apiCall<{ tax_year: number; owner_id: string | null; value: Record<string, unknown> }[]>(
    page,
    "GET",
    `/api/return-inputs?tax_year=${YEAR}&kind=child`,
  );
  expect(children).toHaveLength(1);
  expect(children[0]).toMatchObject({ tax_year: YEAR, owner_id: null, value: { born_on: "2026-03-14" } });

  // A §35a cost and a donation.
  const services = page.getByRole("form", { name: "Handwerker oder Dienstleistung hinzufügen" });
  await services.getByLabel("Arbeitskosten (€)").fill("600,00");
  await services.getByRole("button", { name: "Hinzufügen" }).click();
  await expect(page.getByText("Handwerkerleistung · Arbeitskosten 600,00 € · eigene Rechnung")).toBeVisible();

  const donation = page.getByRole("form", { name: "Spende hinzufügen" });
  await donation.getByLabel("Empfänger").fill("Verein X");
  await donation.getByLabel("Betrag (€)").fill("50");
  await donation.getByRole("button", { name: "Hinzufügen" }).click();
  await expect(page.getByText("Verein X · 50,00 €")).toBeVisible();

  // Childcare names the child; then the donation is removed (a withdrawal row).
  const childcare = page.getByRole("form", { name: "Betreuungskosten hinzufügen" });
  await expect(childcare.getByLabel("Kind")).toContainText("Kind 1 (geboren März 2026)");
  await childcare.getByLabel("Kosten im Jahr (€)").fill("1200");
  await childcare.getByRole("button", { name: "Hinzufügen" }).click();
  await expect(page.getByText("Kind 1 (geboren März 2026) · 1.200,00 €")).toBeVisible();

  await page.getByRole("listitem").filter({ hasText: "Verein X" }).getByRole("button", { name: "Entfernen" }).click();
  await expect(page.getByText(`Für ${YEAR} sind keine Spenden erfasst.`)).toBeVisible();
  const donations = await apiCall<unknown[]>(page, "GET", `/api/return-inputs?tax_year=${YEAR}&kind=special_expense`);
  expect(donations).toHaveLength(0);

  // A row with a document opens it as its Beleg (gap 3); a row without one has no link.
  await apiCall(page, "POST", "/api/return-inputs", {
    kind: "special_expense",
    tax_year: YEAR,
    document_id: document.id,
    value: { expense_class: "donation", amount: "30.00", recipient_label: "Verein Y" },
    withdrawn: false,
  });
  await page.reload();
  await page.getByLabel("Steuerjahr").selectOption(String(YEAR));
  const withReceipt = page.getByRole("listitem").filter({ hasText: "Verein Y" });
  await expect(withReceipt.getByRole("link", { name: "Beleg" })).toHaveAttribute("href", `/documents/${document.id}`);
  await expect(page.getByRole("listitem").filter({ hasText: "Handwerkerleistung" }).getByRole("link", { name: "Beleg" })).toHaveCount(0);
});
