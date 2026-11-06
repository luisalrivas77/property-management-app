// The Vorschlag section of a document page (docs/phases/phase-2.md §6.4). The e2e stack runs
// with automatic reading off, so the document itself is real (uploaded through the API) and the
// proposal endpoints are answered by the browser-side route mocks below: the list, the poll,
// apply and reject. That tests what the screen shows and what it sends; the apply itself is
// proven by the backend's own tests.
import type { Page, Route } from "@playwright/test";

import { expect, test } from "./support";

const PROPOSAL_ID = "00000000-0000-4000-8000-0000000000a1";
const PROPERTY_ID = "00000000-0000-4000-8000-0000000000b1";
const NOW = "2026-11-06T10:00:00Z";

type Json = Record<string, unknown>;

async function uploadPdf(page: Page, name: string): Promise<string> {
  const origin = new URL(page.url()).origin;
  const upload = await page.request.post("/api/documents", {
    multipart: { file: { name, mimeType: "application/pdf", buffer: Buffer.from(`%PDF-1.4\n% ${name}\n%%EOF\n`) } },
    headers: { Origin: origin },
  });
  expect(upload.status()).toBe(201);
  const body = (await upload.json()) as { id?: string; document?: { id: string } };
  return body.document?.id ?? body.id ?? "";
}

function proposal(documentId: string, overrides: Json): Json {
  return {
    id: PROPOSAL_ID,
    workspace_id: "00000000-0000-4000-8000-0000000000c1",
    created_at: NOW,
    updated_at: NOW,
    document_id: documentId,
    kind: "purchase_contract",
    status: "ready",
    error_code: null,
    target_mode: "create",
    contains_tenant_data: false,
    records: [],
    extraction_id: null,
    ai_call_id: null,
    decided_at: null,
    ...overrides,
  };
}

const cited = (value: unknown, page: number, quote: string) => ({
  value,
  source: { page, quote },
  derived_from: [],
  checks: ["cited", "value_in_quote", "validates"],
  confidence: "high",
});

/** A Kaufvertrag read for a property that does not exist yet. */
const CREATE_RECORDS = [
  {
    key: "typing",
    entity_type: "typing",
    mode: "create",
    fields: {
      type: cited("contract", 1, "Kaufvertrag"),
      subtype: cited("purchase_contract", 1, "Kaufvertrag über Wohnungseigentum"),
      document_date: cited("2024-03-15", 1, "Berlin, den 15. März 2024"),
    },
    notes: [],
  },
  {
    key: "property",
    entity_type: "property",
    mode: "create",
    fields: {
      kind: { ...cited("etw", 1, "Wohnungseigentum"), confidence: "medium", checks: ["cited", "validates"] },
      "address.street": cited("Teststraße", 2, "Teststraße 3, 10115 Berlin"),
      "address.house_number": cited("3", 2, "Teststraße 3, 10115 Berlin"),
      "address.postal_code": cited("10115", 2, "Teststraße 3, 10115 Berlin"),
      "address.city": cited("Berlin", 2, "Teststraße 3, 10115 Berlin"),
      build_year: cited(1994, 2, "errichtet 1994"),
      notarized_on: cited("2024-03-15", 1, "Berlin, den 15. März 2024"),
      purchase_date: cited("2024-05-01", 7, "Besitz, Nutzen und Lasten gehen am 1. Mai 2024 über"),
      purchase_price: cited("289000.00", 12, "Der Kaufpreis beträgt EUR 289.000,00"),
    },
    notes: [],
  },
  {
    key: "unit",
    entity_type: "unit",
    mode: "create",
    fields: {
      label: cited("WE 3", 2, "Wohnung Nr. 3"),
      floor_area_m2: { value: null, source: null, derived_from: [], checks: [], confidence: "low" },
    },
    notes: ["Die Wohnfläche steht nicht im Kaufvertrag; bitte aus dem Exposé oder der Teilungserklärung eintragen."],
  },
  {
    key: "cost:notary",
    entity_type: "acquisition_cost",
    mode: "create",
    fields: { kind: cited("notary", 13, "Notarkosten"), amount: cited("2312.00", 13, "Notarkosten EUR 2.312,00") },
    notes: [],
  },
  { key: "link:property", entity_type: "link", mode: "create", fields: {}, notes: [] },
];

/** The same contract read again for a property that exists, with one value differing. */
const PATCH_RECORDS = [
  {
    key: "property",
    entity_type: "property",
    mode: "patch",
    target_id: PROPERTY_ID,
    fields: { build_year: cited(1995, 2, "errichtet 1995"), "address.city": cited("Berlin-Mitte", 2, "10115 Berlin-Mitte") },
    current: { build_year: 1994, "address.city": "Berlin" },
    notes: [],
  },
  {
    key: "unit",
    entity_type: "unit",
    mode: "unchanged",
    target_id: "00000000-0000-4000-8000-0000000000b2",
    fields: { label: cited("WE 3", 2, "Wohnung Nr. 3") },
    current: { label: "WE 3" },
    notes: [],
  },
];

interface Mock {
  list: Json[];
  poll: Json | null;
  polls: number;
  applies: Json[];
  apply: (body: Json, attempt: number) => { status: number; json: Json };
}

async function mockProposals(page: Page, mock: Mock): Promise<void> {
  await page.route("**/api/documents/*/proposals", async (route: Route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: mock.list });
    return route.fallback();
  });
  await page.route("**/api/proposals/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path === `/api/proposals/${PROPOSAL_ID}`) {
      mock.polls += 1;
      return route.fulfill({ json: mock.poll ?? {} });
    }
    if (request.method() === "POST" && path === `/api/proposals/${PROPOSAL_ID}/apply`) {
      const body = request.postDataJSON() as Json;
      mock.applies.push(body);
      const answer = mock.apply(body, mock.applies.length);
      return route.fulfill({ status: answer.status, json: answer.json });
    }
    return route.fallback();
  });
}

function vorschlag(page: Page) {
  return page.getByRole("region", { name: /^Vorschlag/ });
}

test("a read Kaufvertrag is reviewed, corrected at the field and applied", async ({ page, signedIn }) => {
  void signedIn;
  const documentId = await uploadPdf(page, "Kaufvertrag-Vorschlag.pdf");
  const mock: Mock = {
    list: [proposal(documentId, { status: "queued", records: [] })],
    poll: proposal(documentId, { status: "ready", records: CREATE_RECORDS }),
    polls: 0,
    applies: [],
    apply: (_body, attempt) =>
      attempt === 1
        ? {
            status: 422,
            json: {
              error: "validation",
              message: "Request validation failed",
              detail: [{ loc: ["unit", "floor_area_m2"], msg: "Bitte die Wohnfläche angeben.", type: "missing" }],
            },
          }
        : {
            status: 200,
            json: {
              proposal: proposal(documentId, { status: "applied", records: CREATE_RECORDS, decided_at: NOW }),
              applied: [
                { key: "typing", entity_type: "typing", mode: "create", skipped: false, entity_id: documentId },
                { key: "property", entity_type: "property", mode: "create", skipped: false, entity_id: PROPERTY_ID },
                { key: "unit", entity_type: "unit", mode: "create", skipped: false, entity_id: "00000000-0000-4000-8000-0000000000b2" },
                { key: "cost:notary", entity_type: "acquisition_cost", mode: "create", skipped: true, entity_id: null },
                { key: "link:property", entity_type: "link", mode: "create", skipped: false, entity_id: PROPERTY_ID },
              ],
            },
          },
  };
  await mockProposals(page, mock);

  await page.goto(`/documents/${documentId}`);
  const section = vorschlag(page);
  // Queued: a status line, then the poll (every 2 s) brings the ready proposal.
  await expect(section.getByRole("status")).toContainText("Wartet auf das Auslesen");
  const form = section.getByRole("form", { name: "Vorschlag prüfen" });
  await expect(form).toBeVisible();
  const pollsWhenReady = mock.polls;
  expect(pollsWhenReady).toBeGreaterThan(0);

  // One card per record, values in inputs of their type, nothing computed by the screen.
  const property = form.getByRole("region", { name: "Immobilie", exact: true });
  await expect(property.getByLabel("Kaufpreis (€)")).toHaveValue("289000,00");
  await expect(property.getByLabel("Ort", { exact: true })).toHaveValue("Berlin");
  await expect(property.getByLabel("Art", { exact: true })).toHaveValue("etw");
  await expect(property.getByRole("img", { name: "Sicherheit mittel (Fundstelle im Dokument, Format gültig)" })).toBeVisible();
  await expect(form.getByRole("region", { name: "Nebenkosten: Notar" }).getByLabel("Betrag (€)")).toHaveValue("2312,00");
  await expect(form.getByRole("region", { name: "Verknüpfung: Immobilie" })).toContainText("der neu angelegten Immobilie");

  // The page badge opens the quote by keyboard, with the link to the original at that page.
  const badge = property.getByRole("button", { name: /^S\. 12/ });
  await expect(badge).toHaveAttribute("aria-expanded", "false");
  await badge.focus();
  await page.keyboard.press("Enter");
  await expect(badge).toHaveAttribute("aria-expanded", "true");
  await expect(property.getByText("„Der Kaufpreis beträgt EUR 289.000,00“")).toBeVisible();
  await expect(property.getByRole("link", { name: "Original auf S. 12 öffnen" })).toHaveAttribute(
    "href",
    `/api/documents/${documentId}/original?disposition=inline#page=12`,
  );

  // A value the document does not state points to the record's note.
  const unit = form.getByRole("region", { name: "Einheit WE 3" });
  await expect(unit).toContainText("Die Wohnfläche steht nicht im Kaufvertrag");
  await expect(unit.getByLabel("Fläche (m²)")).toHaveValue("");
  await expect(unit.getByText("Im Dokument nicht als Wert gefunden; siehe Hinweis oben.")).toBeVisible();

  // Leave the notary cost out; the first apply answers 422 at the unit's area.
  await form.getByRole("region", { name: "Nebenkosten: Notar" }).getByLabel("nicht anlegen").check();
  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(unit.getByText("Bitte die Wohnfläche angeben.")).toBeVisible();
  await expect(unit.getByLabel("Fläche (m²)")).toHaveAttribute("aria-invalid", "true");

  const first = mock.applies[0] as { records: Json[] };
  expect(first.records.map((r) => r.key)).toEqual(["typing", "property", "unit", "cost:notary", "link:property"]);
  expect(first.records.find((r) => r.key === "cost:notary")).toEqual({ key: "cost:notary", skip: true });
  expect(first.records.find((r) => r.key === "property")).toMatchObject({
    skip: false,
    values: { purchase_price: "289000.00", "address.city": "Berlin", build_year: 1994, notarized_on: "2024-03-15" },
  });
  expect(first.records.find((r) => r.key === "unit")).toEqual({ key: "unit", skip: false, values: { label: "WE 3" }, decisions: {} });

  // Type the area and apply again: saved, with links to what was created.
  await unit.getByLabel("Fläche (m²)").fill("54,3");
  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(section.getByText("Übernommen.")).toBeVisible();
  await expect(section.getByRole("link", { name: "Immobilie angelegt" })).toHaveAttribute("href", `/properties/${PROPERTY_ID}`);
  const second = mock.applies[1] as { records: Json[] };
  expect(second.records.find((r) => r.key === "unit")).toMatchObject({ values: { label: "WE 3", floor_area_m2: "54.3" } });

  // Polling stopped once the proposal was ready.
  await page.waitForTimeout(2_500);
  expect(mock.polls).toBe(pollsWhenReady);
});

test("an existing property: übernehmen / behalten per field, unchanged records sent as skipped", async ({ page, signedIn }) => {
  void signedIn;
  const documentId = await uploadPdf(page, "Kaufvertrag-Abgleich.pdf");
  const mock: Mock = {
    list: [proposal(documentId, { target_mode: "patch", records: PATCH_RECORDS })],
    poll: null,
    polls: 0,
    applies: [],
    apply: () => ({ status: 409, json: { error: "conflict", code: "proposal.already_decided", message: "already decided" } }),
  };
  await mockProposals(page, mock);

  await page.goto(`/documents/${documentId}`);
  const form = vorschlag(page).getByRole("form", { name: "Vorschlag prüfen" });
  const property = form.getByRole("region", { name: "Immobilie", exact: true });
  await expect(property.getByLabel("Baujahr")).toHaveValue("1995");
  await expect(property).toContainText("Gespeichert: 1994");
  await expect(form.getByRole("region", { name: "Einheit WE 3" })).toContainText("Stimmt mit dem Gespeicherten überein");

  // Keep the stored build year; take the city.
  await property.getByRole("radiogroup", { name: "Baujahr: übernehmen oder behalten" }).getByLabel("behalten").check();
  await expect(property.getByLabel("Baujahr")).toBeDisabled();
  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(form.getByText("Über diesen Vorschlag wurde schon entschieden (übernommen oder verworfen).")).toBeVisible();

  const sent = mock.applies[0] as { records: Json[] };
  expect(sent.records).toEqual([
    { key: "property", skip: false, values: { "address.city": "Berlin-Mitte" }, decisions: { build_year: "skipped" } },
    { key: "unit", skip: true },
  ]);
});

test("a failed read and a tenant document offer manual entry", async ({ page, signedIn }) => {
  void signedIn;
  const failedId = await uploadPdf(page, "Kaufvertrag-unlesbar.pdf");
  const mock: Mock = { list: [], poll: null, polls: 0, applies: [], apply: () => ({ status: 500, json: {} }) };
  await mockProposals(page, mock);

  mock.list = [proposal(failedId, { status: "failed", error_code: "ai.unparsable" })];
  await page.goto(`/documents/${failedId}`);
  const failed = vorschlag(page);
  await expect(failed.getByRole("alert")).toContainText("Die Antwort der KI ließ sich auch beim zweiten Versuch nicht auswerten.");
  await expect(failed.getByRole("link", { name: "Von Hand eingeben" })).toHaveAttribute("href", "/properties/new");
  await expect(failed.getByRole("button", { name: "Neu auslesen" })).toBeVisible();

  const leaseId = await uploadPdf(page, "Mietvertrag.pdf");
  mock.list = [proposal(leaseId, { status: "blocked_tenant_data", kind: "lease", contains_tenant_data: true })];
  await page.goto(`/documents/${leaseId}`);
  const blocked = vorschlag(page);
  await expect(blocked.getByRole("status")).toContainText("erst an die KI geschickt, wenn das für den Haushalt freigegeben ist");
  await expect(blocked.getByRole("link", { name: "Von Hand eingeben" })).toHaveAttribute("href", "/tenancies/new");
  await expect(blocked.getByRole("form", { name: "Vorschlag prüfen" })).toHaveCount(0);
});
