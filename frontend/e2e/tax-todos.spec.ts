// Was noch fehlt (docs/phases/phase-3.md D136): every gap is a plain to-do with an action. A
// flat without a Kaufpreisaufteilung shows the gap at the top of the Anlage V tab with what to
// do and the upload, and Übersicht lists it with the property's name; with one Kaufvertrag
// split recorded but not selected (a state that exists only from before phase 2 D133, so it is
// mocked here) the button selects it through the existing command; a real split, recorded
// through the API, closes the to-do. Synthetic data only.
import path from "node:path";

import { apiCall, expect, test } from "./support";

const YEAR = 2026;
const address = { street: "Teststraße", house_number: "9", postal_code: "10115", city: "Berlin", country_code: "DE" };
const GAP = "Gebäudeanteil am Kaufpreis fehlt";

test("a missing price split is a to-do with its button, and recording one closes it", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung To-do",
    kind: "etw",
    address,
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });

  await page.goto(`/tax/${YEAR}`);
  const todos = page.getByRole("region", { name: "Was noch fehlt" });
  const split = todos.getByRole("listitem").filter({ hasText: GAP });
  await expect(split).toContainText("er steht meist im Kaufvertrag");
  await expect(split.getByRole("button", { name: "Kaufvertrag hochladen" })).toBeVisible();
  await expect(split.getByRole("link", { name: "Aufteilung selbst eintragen" })).toHaveAttribute(
    "href",
    `/properties/${property.id}?tab=aufteilung`,
  );
  // One gap, one entry: once the checklist is in (the flat has no Grundsteuerbescheid), its
  // Kaufpreisaufteilung is not listed beside the draft's gap.
  await expect(todos.getByRole("listitem").filter({ hasText: "Grundsteuerbescheid: fehlt" })).toBeVisible();
  await expect(todos.getByRole("listitem").filter({ hasText: "Kaufpreisaufteilung: fehlt" })).toHaveCount(0);
  // The Offene Punkte entry says what to do as well.
  const open = page.getByRole("region", { name: "Offene Punkte" });
  await expect(open.getByRole("listitem").filter({ hasText: "Gebäudeanteil" }).first()).toContainText("Was tun:");

  // Übersicht gathers the household's to-dos, each with its property and the same action.
  await page.goto("/");
  const overview = page.getByRole("region", { name: "Was noch fehlt" });
  const entry = overview.getByRole("listitem").filter({ hasText: GAP });
  await expect(entry).toContainText("Wohnung To-do");
  await expect(entry.getByRole("button", { name: "Kaufvertrag hochladen" })).toBeVisible();

  // A recorded Kaufvertrag split that is not selected: the button selects exactly that one.
  const SPLIT_ID = "00000000-0000-4000-8000-000000000136";
  const selected: string[] = [];
  await page.route(`**/api/properties/${property.id}/price-splits`, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({
          json: [{ id: SPLIT_ID, property_id: property.id, source: "purchase_contract", building_share: "0.8", workspace_id: SPLIT_ID }],
        })
      : route.fallback(),
  );
  await page.route(`**/api/properties/${property.id}/price-splits/*/select`, (route) => {
    selected.push(new URL(route.request().url()).pathname);
    return route.fulfill({ json: {} });
  });
  await page.goto(`/tax/${YEAR}`);
  await todos
    .getByRole("listitem")
    .filter({ hasText: GAP })
    .getByRole("button", { name: /^Gebäudeanteil .* aus dem Kaufvertrag übernehmen$/ })
    .click();
  await expect.poll(() => selected).toEqual([`/api/properties/${property.id}/price-splits/${SPLIT_ID}/select`]);
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // A real split, recorded through the API (selected on record, D133): the to-do is gone.
  await apiCall(page, "POST", "/api/price-splits", {
    property_id: property.id,
    source: "purchase_contract",
    building_share: "0.8",
  });
  await page.reload();
  await expect(page.getByRole("region", { name: "Offene Punkte" })).toBeVisible();
  await expect(todos).toBeVisible();
  await expect(todos.getByRole("listitem").filter({ hasText: GAP })).toHaveCount(0);
});

// One gap, one entry: a let flat without a Mietspiegel value is shown by the draft's warning
// ("Ortsübliche Miete fehlt", with its button), and the checklist's Ortsübliche Miete is not
// listed beside it, neither on the Anlage V tab nor on Übersicht.
test("a missing market rent is one to-do, not two", async ({ page, signedIn }) => {
  void signedIn;
  const label = "Wohnung Mietspiegel";
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label,
    kind: "etw",
    address: { ...address, house_number: "11" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  const unit = await apiCall<{ id: string }>(page, "POST", "/api/units", {
    property_id: property.id,
    label: "WE 1",
    floor_area_m2: "60.00",
    rooms: null,
    mea: null,
  });
  const tenant = await apiCall<{ id: string }>(page, "POST", "/api/tenants", {
    full_name: "Erika Muster",
    email: null,
    phone: null,
    iban: null,
  });
  await apiCall(page, "POST", "/api/tenancies", {
    unit_id: unit.id,
    parties: [{ tenant_id: tenant.id, role: "primary" }],
    signed_on: null,
    start_date: "2024-07-01",
    end_date: null,
    fixed_term_until: null,
    rent_model: "fixed",
    deposit_amount: "0.00",
    deposit_form: "none",
    deposit_bank: null,
    deposit_iban: null,
    initial_rent: { cold_rent: "950.00", operating_costs_prepayment: "220.00", effective_from: null, document_id: null },
  });

  await page.goto(`/tax/${YEAR}?immobilie=${property.id}`);
  const todos = page.getByRole("region", { name: "Was noch fehlt" });
  const gap = todos.getByRole("listitem").filter({ hasText: "Ortsübliche Miete fehlt" });
  await expect(gap).toHaveCount(1);
  await expect(gap.getByRole("link", { name: "Mietspiegel-Wert eintragen" })).toBeVisible();
  await expect(todos.getByRole("listitem").filter({ hasText: "Grundsteuerbescheid: fehlt" })).toBeVisible();
  await expect(todos.getByRole("listitem").filter({ hasText: "Ortsübliche Miete: fehlt" })).toHaveCount(0);

  await page.goto("/");
  const overview = page.getByRole("region", { name: "Was noch fehlt" });
  const mine = overview.getByRole("listitem").filter({ hasText: label });
  await expect(mine.filter({ hasText: "Ortsübliche Miete fehlt" })).toHaveCount(1);
  await expect(mine.filter({ hasText: "Ortsübliche Miete" })).toHaveCount(1);
});

// D140: an Einnahme ohne Zuordnung (other_income) after the handover is a to-do with "Als Miete
// buchen": the dialog names the payment's date and amount, and confirming re-books it as
// Kaltmiete through the inbox's answer command. The camt sample's "Partei D" credit (28 Sep
// 2026) is split into two other_income parts through the same command first: re-booking one
// supersedes every row of the line, and the other part's to-do, which now cites a new row, still
// offers the button without a page reload (the cited payments reload with the draft).
const SAMPLE = path.resolve(import.meta.dirname, "..", "..", "backend", "tests", "fixtures", "phase2", "bank_samples", "camt052.zip");

test("an Einnahme ohne Zuordnung is booked as rent from its to-do", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Verkäufermiete",
    kind: "etw",
    address: { ...address, house_number: "13" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  // A room, so the flat's Z13 row has an area (Σ the rooms' m² while the Wohnfläche is missing).
  await apiCall(page, "POST", "/api/units", {
    property_id: property.id,
    label: "Zimmer 1",
    floor_area_m2: "18.00",
    rooms: null,
    mea: null,
  });
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Mietkonto",
    bank: "Sparkasse",
    iban: "DE50100000000000000001",
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  await page.goto(`/accounts/${account.id}`);
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(SAMPLE);
  await expect(page.getByRole("status").filter({ hasText: "Importiert: camt052.zip" })).toContainText("abgestimmt");
  const lines = await apiCall<{ id: string; amount: string }[]>(page, "GET", `/api/accounts/${account.id}/transactions`);
  const line = lines.find((l) => l.amount === "740.18");
  expect(line, "the sample's Partei D credit").toBeDefined();
  const transactionId = line?.id ?? "";
  const allocationsPath = `/api/transactions/${transactionId}/allocations`;
  const put = await page.request.fetch(allocationsPath, {
    method: "PUT",
    data: {
      allocation_set: {
        transaction_id: transactionId,
        allocations: [
          { amount: "700.00", category: "other_income", property_id: property.id, recurring: false },
          { amount: "40.18", category: "other_income", property_id: property.id, recurring: false },
        ],
        method: "user",
        confidence: "1",
      },
      learn_rule: false,
      same_amount_only: false,
    },
    headers: { Origin: new URL(page.url()).origin },
  });
  expect(put.status()).toBe(200);

  await page.goto(`/tax/${YEAR}?immobilie=${property.id}`);
  const todos = page.getByRole("region", { name: "Was noch fehlt" });
  const entry = todos.getByRole("listitem").filter({ hasText: "Einnahme ohne Zuordnung" });
  await expect(entry).toHaveCount(2);
  await expect(entry.first().getByRole("link", { name: "Zahlung öffnen" })).toHaveAttribute("href", `/inbox?umsatz=${transactionId}`);
  await entry.first().getByRole("button", { name: "Als Miete buchen" }).click();
  const dialog = page.getByRole("dialog", { name: "Einnahme als Miete buchen" });
  await expect(dialog).toContainText("28.09.2026");
  await expect(dialog).toContainText(/(700,00|40,18) €/);
  await expect(dialog).toContainText("als Kaltmiete gebucht");
  const saved = page.waitForResponse((r) => r.url().endsWith(allocationsPath) && r.request().method() === "PUT");
  await dialog.getByRole("button", { name: "Ja, als Miete buchen" }).click();
  expect((await saved).status()).toBe(200);
  // No page.reload(): the remaining part's to-do keeps its button.
  await expect(entry).toHaveCount(1);
  await expect(entry.getByRole("button", { name: "Als Miete buchen" })).toBeVisible();

  const stored = await apiCall<{ allocations: { amount: string; category: string; status: string }[] }>(page, "GET", allocationsPath);
  expect(stored.allocations.map((a) => a.status)).toEqual(["confirmed", "confirmed"]);
  expect(stored.allocations.map((a) => a.category).sort()).toEqual(["other_income", "rent_cold"]);
});

// D142: a deposit paid into the account proves it. The camt sample's "Partei D" credit (28 Sep
// 2026) is booked as 600,00 € Kaution erhalten without a tenancy plus the rest as rent; three
// rooms, each with a 200,00 € cash deposit, get a Kautionsnachweis to-do with „Kaution aus
// Bankumsatz zuordnen“ (on Übersicht too). The deposits sum to the line exactly, so one click
// splits it to all three through the inbox's answer command; the rent part is sent back as it
// was, the three to-dos close without a reload, and Unterlagen lists the bank line as proof.
test("one deposit line is split to three tenancies from the to-do", async ({ page, signedIn }) => {
  void signedIn;
  const label = "Wohnung Kautionen";
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label,
    kind: "etw",
    address: { ...address, house_number: "15" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  const rooms: { tenancy: string; unit: string }[] = [];
  for (const [index, start] of ["2024-07-01", "2024-08-01", "2024-09-01"].entries()) {
    const unit = await apiCall<{ id: string }>(page, "POST", "/api/units", {
      property_id: property.id,
      label: `Zimmer ${index + 1}`,
      floor_area_m2: "18.00",
      rooms: null,
      mea: null,
    });
    const tenant = await apiCall<{ id: string }>(page, "POST", "/api/tenants", {
      full_name: `Mieterin ${index + 1}`,
      email: null,
      phone: null,
      iban: null,
    });
    const tenancy = await apiCall<{ id: string }>(page, "POST", "/api/tenancies", {
      unit_id: unit.id,
      parties: [{ tenant_id: tenant.id, role: "primary" }],
      signed_on: null,
      start_date: start,
      end_date: null,
      fixed_term_until: null,
      rent_model: "fixed",
      deposit_amount: "200.00",
      deposit_form: "cash_account",
      deposit_bank: null,
      deposit_iban: null,
      initial_rent: { cold_rent: "400.00", operating_costs_prepayment: "80.00", effective_from: null, document_id: null },
    });
    rooms.push({ tenancy: tenancy.id, unit: unit.id });
  }
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Mietkonto",
    bank: "Sparkasse",
    iban: "DE50100000000000000001",
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  await page.goto(`/accounts/${account.id}`);
  await page.getByLabel("Kontoauszug hochladen").setInputFiles(SAMPLE);
  await expect(page.getByRole("status").filter({ hasText: "Importiert: camt052.zip" })).toContainText("abgestimmt");
  const lines = await apiCall<{ id: string; amount: string }[]>(page, "GET", `/api/accounts/${account.id}/transactions`);
  const line = lines.find((l) => l.amount === "740.18");
  expect(line, "the sample's Partei D credit").toBeDefined();
  const transactionId = line?.id ?? "";
  const allocationsPath = `/api/transactions/${transactionId}/allocations`;
  const put = await page.request.fetch(allocationsPath, {
    method: "PUT",
    data: {
      allocation_set: {
        transaction_id: transactionId,
        allocations: [
          { amount: "600.00", category: "deposit_in", property_id: property.id, recurring: false },
          { amount: "140.18", category: "rent_cold", property_id: property.id, recurring: false },
        ],
        method: "user",
        confidence: "1",
      },
      learn_rule: false,
      same_amount_only: false,
    },
    headers: { Origin: new URL(page.url()).origin },
  });
  expect(put.status()).toBe(200);

  // Übersicht offers the same button beside the property's name.
  await page.goto("/");
  const overview = page.getByRole("region", { name: "Was noch fehlt" });
  const first = overview.getByRole("listitem").filter({ hasText: "Kautionsnachweis · Zimmer 1, ab 01.07.2024: fehlt" });
  await expect(first).toContainText(label);
  await expect(first.getByRole("button", { name: "Kaution aus Bankumsatz zuordnen" })).toBeVisible();

  await page.goto(`/tax/${YEAR}?immobilie=${property.id}`);
  const todos = page.getByRole("region", { name: "Was noch fehlt" });
  const deposits = todos.getByRole("listitem").filter({ hasText: "Kautionsnachweis" });
  await expect(deposits).toHaveCount(3);
  await deposits.first().getByRole("button", { name: "Kaution aus Bankumsatz zuordnen" }).click();
  const dialog = page.getByRole("dialog", { name: "Kaution zuordnen" });
  await expect(dialog).toContainText(
    "Die Zahlung vom 28.09.2026 (Kaution erhalten, 600,00 €) auf 3 Mietverhältnisse aufteilen: je 200,00 €?",
  );
  await expect(dialog).toContainText("Zimmer 3, ab 01.09.2024 · 200,00 €");
  const saved = page.waitForResponse((r) => r.url().endsWith(allocationsPath) && r.request().method() === "PUT");
  await dialog.getByRole("button", { name: "Übernehmen" }).click();
  const response = await saved;
  expect(response.status()).toBe(200);
  // The reply holds what was stored (Playwright cannot read the body of a request the API
  // client sends as a Request object, so the stored rows are checked instead) and no rule.
  const reply = (await response.json()) as {
    allocations: { amount: string; category: string; tenancy_id: string | null; unit_id: string | null }[];
    rule: unknown;
  };
  expect(reply.rule).toBeNull();
  const rows = reply.allocations.map((a) => [a.amount, a.category, a.tenancy_id ?? null, a.unit_id ?? null].join(" "));
  expect(rows.sort()).toEqual(
    [...rooms.map(({ tenancy, unit }) => `200.00 deposit_in ${tenancy} ${unit}`), "140.18 rent_cold  "].sort(),
  );

  // No page.reload(): the three to-dos close, and Unterlagen names the bank line as the proof.
  await expect(deposits).toHaveCount(0);
  const unterlagen = page.getByRole("region", { name: "Unterlagen für die Steuererklärung" });
  const proven = unterlagen.getByRole("listitem").filter({ hasText: "Kautionsnachweis · Zimmer 2" });
  await expect(proven).toContainText("Nachgewiesen durch die Zahlung auf dem Konto");
  await expect(proven.getByRole("link", { name: "Bankumsatz vom 28.09.2026 · 200,00 €" })).toHaveAttribute(
    "href",
    `/inbox?umsatz=${transactionId}`,
  );

  const stored = await apiCall<{ allocations: { category: string; tenancy_id: string | null; status: string }[] }>(
    page,
    "GET",
    allocationsPath,
  );
  expect(stored.allocations.map((a) => a.status)).toEqual(["confirmed", "confirmed", "confirmed", "confirmed"]);
  expect(stored.allocations.filter((a) => a.category === "deposit_in").map((a) => a.tenancy_id).sort()).toEqual(
    rooms.map(({ tenancy }) => tenancy).sort(),
  );
});

// Phase 3 D143: a bill the draft counts without a bank line that paid it is a to-do
// „Zahlungsnachweis fehlt“; the file uploaded from its button is typed as the payment proof by
// the server and linked to the bill and the property, and the to-do closes. The e2e API runs
// without AI, so the proof stays unread and counts as seen.
test("a bill without a bank line asks for its payment proof, and the upload closes it", async ({ page, signedIn }) => {
  void signedIn;
  const origin = new URL(page.url()).origin;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Nachweis",
    kind: "etw",
    address: { ...address, house_number: "11" },
    build_year: 1994,
    notarized_on: "2026-03-10",
    purchase_date: "2026-07-01",
    purchase_price: "250000.00",
  });
  const bill = await page.request.post("/api/documents", {
    multipart: {
      file: { name: "Notarrechnung.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% Notarrechnung D143\n%%EOF\n") },
    },
    headers: { Origin: origin },
  });
  expect(bill.status()).toBe(201);
  const billId = ((await bill.json()) as { id: string }).id;
  await apiCall(page, "POST", "/api/acquisition-costs", {
    property_id: property.id,
    kind: "notary",
    amount: "1785.00",
    document_id: billId,
  });

  await page.goto(`/tax/${YEAR}`);
  const todos = page.getByRole("region", { name: "Was noch fehlt" });
  const proof = todos.getByRole("listitem").filter({ hasText: "Zahlungsnachweis fehlt: Die Rechnung Notar" });
  await expect(proof).toContainText("Überweisungsbeleg");
  await proof.getByRole("button", { name: "Zahlungsnachweis hochladen" }).click();
  const uploaded = page.waitForResponse((r) => r.url().endsWith("/api/documents") && r.request().method() === "POST");
  await proof.getByLabel("Zahlungsnachweis hochladen").setInputFiles({
    name: "Ueberweisung.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n% Ueberweisung Notar D143\n%%EOF\n"),
  });
  const response = await uploaded;
  expect(response.status()).toBe(201);
  // The stored document, not the request body (an API-client request's body is not readable).
  const proofId = ((await response.json()) as { id: string }).id;
  const detail = await apiCall<{
    document: { typing: { type: string; subtype: string | null } | null };
    links: { target_type: string; target_id: string }[];
  }>(page, "GET", `/api/documents/${proofId}`);
  expect(detail.document.typing).toMatchObject({ type: "other", subtype: "payment_proof" });
  expect(detail.links.map((l) => `${l.target_type} ${l.target_id}`).sort()).toEqual(
    [`document ${billId}`, `property ${property.id}`].sort(),
  );

  // The proof is in: the bill keeps counting and its to-do is gone.
  await expect(todos.getByRole("listitem").filter({ hasText: "Zahlungsnachweis fehlt" })).toHaveCount(0);
});

test("an owner without any pay this year is a to-do, and an expected gross pay closes it", async ({ page, signedIn }) => {
  void signedIn;
  const year = new Date().getFullYear(); // Übersicht's to-dos are for today's year
  const owner = await apiCall<{ id: string }>(page, "POST", "/api/owners", { full_name: "A. Lohnlos", church_tax: false });

  await page.goto("/");
  const overview = page.getByRole("region", { name: "Was noch fehlt" });
  const entry = overview.getByRole("listitem").filter({ hasText: `Arbeitslohn ${year} fehlt` });
  await expect(entry).toContainText("A. Lohnlos");
  await expect(entry).toContainText("letzte Gehaltsabrechnung hochladen");
  await expect(entry.getByRole("button", { name: "Gehaltsabrechnung hochladen" })).toBeVisible();
  await expect(entry.getByRole("link", { name: "Bruttogehalt eintragen" })).toHaveAttribute("href", "/owners");

  // Typing the expected pay by hand waives the payslip.
  await apiCall(page, "POST", "/api/income-inputs", {
    kind: "expected_gross_pay",
    tax_year: year,
    owner_id: owner.id,
    value: { amount: "45000.00" },
    supersedes_id: null,
    withdrawn: false,
  });
  await page.reload();
  await expect(overview.getByText("Lädt …")).toHaveCount(0); // loaded, so the next check is not vacuous
  await expect(overview.getByRole("listitem").filter({ hasText: `Arbeitslohn ${year} fehlt` })).toHaveCount(0);
});
