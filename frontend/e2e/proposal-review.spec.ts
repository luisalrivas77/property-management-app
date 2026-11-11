// The Vorschlag section of a document page (docs/phases/phase-2.md §6.4). The e2e stack runs
// with automatic reading off, so the document itself is real (uploaded through the API) and the
// proposal endpoints are answered by the browser-side route mocks below: the list, the poll,
// apply and reject. That tests what the screen shows and what it sends; the apply itself is
// proven by the backend's own tests.
import type { Page, Route } from "@playwright/test";

import { apiCall, expect, test } from "./support";

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
  await expect(property.getByLabel("Baujahr", { exact: true })).toHaveValue("1995");
  await expect(property).toContainText("Gespeichert: 1994");
  await expect(form.getByRole("region", { name: "Einheit WE 3" })).toContainText("Stimmt mit dem Gespeicherten überein");

  // Keep the stored build year; take the city.
  await property.getByRole("radiogroup", { name: "Baujahr: übernehmen oder behalten" }).getByLabel("behalten").check();
  await expect(property.getByLabel("Baujahr", { exact: true })).toBeDisabled();
  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(form.getByText("Über diesen Vorschlag wurde schon entschieden (übernommen oder verworfen).")).toBeVisible();

  const sent = mock.applies[0] as { records: Json[] };
  expect(sent.records).toEqual([
    { key: "property", skip: false, values: { "address.city": "Berlin-Mitte" }, decisions: { build_year: "skipped" } },
    { key: "unit", skip: true },
  ]);
});

const missing = { value: null, source: null, derived_from: [], checks: [], confidence: "low" };

/** A room lease whose room Hausbuch could not place, with no Nebenkosten amount stated. */
const LEASE_RECORDS = [
  {
    key: "typing",
    entity_type: "typing",
    mode: "create",
    fields: { type: cited("contract", 1, "Mietvertrag"), subtype: cited("lease", 1, "Mietvertrag über ein Zimmer") },
    notes: [],
  },
  { key: "tenant:1", entity_type: "tenant", mode: "create", fields: { full_name: cited("Lena Beispiel", 1, "Mieterin: Lena Beispiel") }, notes: [] },
  {
    key: "tenancy",
    entity_type: "tenancy",
    mode: "create",
    fields: {
      unit_id: { ...missing, derived_from: ["street", "house_number", "postal_code", "unit_label"], checks: ["derived", "validates"] },
      start_date: cited("2026-05-01", 1, "Das Mietverhältnis beginnt am 01.05.2026"),
      rent_model: { value: "fixed", source: null, derived_from: ["rent_clause"], checks: ["derived", "validates"], confidence: "low" },
      deposit_amount: cited("1000.00", 3, "eine Kaution in Höhe von 1.000,00 €"),
      deposit_form: { value: "cash_account", source: null, derived_from: ["deposit_amount", "deposit_clause"], checks: ["derived", "validates"], confidence: "low" },
    },
    notes: [],
  },
  {
    key: "rent",
    entity_type: "rent_schedule",
    mode: "create",
    fields: { cold_rent: cited("450.00", 2, "Die Miete beträgt monatlich 450,00 €"), effective_from: cited("2026-05-01", 1, "beginnt am 01.05.2026") },
    notes: [],
  },
  { key: "link:tenancy", entity_type: "link", mode: "create", fields: {}, notes: [] },
];

test("a lease whose room was not found: the unit is picked and a missing value typed by hand", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Zimmer",
    kind: "etw",
    address: { street: "Teststraße", house_number: "5", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  const room = (label: string) =>
    apiCall<{ id: string }>(page, "POST", "/api/units", { property_id: property.id, label, floor_area_m2: "14.00", rooms: null, mea: null });
  const room1 = await room("Zimmer 1");
  await room("Zimmer 2");
  const documentId = await uploadPdf(page, "Mietvertrag-Zimmer.pdf");
  const mock: Mock = {
    list: [proposal(documentId, { kind: "lease", contains_tenant_data: true, records: LEASE_RECORDS })],
    poll: null,
    polls: 0,
    applies: [],
    apply: () => ({
      status: 200,
      json: {
        proposal: proposal(documentId, { kind: "lease", status: "applied", records: LEASE_RECORDS, decided_at: NOW }),
        applied: LEASE_RECORDS.map((r) => ({ key: r.key, entity_type: r.entity_type, mode: "create", skipped: false, entity_id: null })),
      },
    }),
  };
  await mockProposals(page, mock);

  await page.goto(`/documents/${documentId}`);
  const form = vorschlag(page).getByRole("form", { name: "Vorschlag prüfen" });
  const tenancy = form.getByRole("region", { name: "Mietverhältnis", exact: true });
  const unit = tenancy.getByLabel("Einheit", { exact: true });
  await expect(unit).toHaveValue("");
  await expect(tenancy.getByText("Keine Einheit passt eindeutig zum Mietvertrag; bitte wählen.")).toBeVisible();
  // Every field of the tenancy is there, also those the lease did not state.
  await expect(tenancy.getByLabel("Auszug", { exact: true })).toHaveValue("");
  await unit.selectOption({ label: "Wohnung Zimmer · Zimmer 1" });

  // The lease states no Nebenkosten amount: the field is still there, empty, to fill by hand.
  const rent = form.getByRole("region", { name: "Anfangsmiete" });
  const prepayment = rent.getByLabel("NK-Vorauszahlung (€ / Monat)");
  await expect(prepayment).toHaveValue("");
  await expect(rent.getByText("Nicht im Vorschlag; bei Bedarf von Hand eintragen.")).toBeVisible();
  await prepayment.fill("150,00");

  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(vorschlag(page).getByText("Übernommen.")).toBeVisible();
  const sent = mock.applies[0] as { records: Json[] };
  expect(sent.records.find((r) => r.key === "tenancy")).toEqual({
    key: "tenancy",
    skip: false,
    values: { unit_id: room1.id, start_date: "2026-05-01", rent_model: "fixed", deposit_amount: "1000.00", deposit_form: "cash_account" },
    decisions: {},
  });
  expect(sent.records.find((r) => r.key === "rent")).toEqual({
    key: "rent",
    skip: false,
    values: { cold_rent: "450.00", effective_from: "2026-05-01", operating_costs_prepayment: "150.00" },
    decisions: {},
  });
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
  await expect(blocked.getByRole("button", { name: "Neu auslesen" })).toHaveCount(0);
  // Nothing to review in the card (no typing was proposed), so the page's own form is the one form.
  await expect(page.getByRole("form", { name: "Typisierung bearbeiten" })).toBeVisible();

  // Once the household lets the AI read tenant documents, the blocked document can be read again.
  const origin = new URL(page.url()).origin;
  const patched = await page.request.fetch("/api/workspace/settings", {
    method: "PATCH",
    data: { ai_tenant_documents: true },
    headers: { Origin: origin },
  });
  expect(patched.status()).toBe(200);
  await page.goto(`/documents/${leaseId}`);
  await expect(blocked.getByRole("status")).toContainText("Inzwischen ist das für den Haushalt freigegeben");
  await expect(blocked.getByRole("button", { name: "Neu auslesen" })).toBeVisible();
  await expect(blocked.getByRole("link", { name: "Von Hand eingeben" })).toHaveAttribute("href", "/tenancies/new");
});

const empty = { value: null, source: null, derived_from: [], checks: [], confidence: "low" };
const POINTER = "Prüfen Sie den Vorschlag oben";
const LINK_POINTER = "Die vorgeschlagene Verknüpfung prüfen Sie im Vorschlag oben";

/** A Verwaltervertrag read with its fee (phase 2 D132): the typing record carries the hand
 * fields, the Betrag computed by code (PR #137), the others read or empty; and the link to its
 * property, found by address. */
const managementRecords = (propertyId: string) => [
  {
    key: "typing",
    entity_type: "typing",
    mode: "create",
    fields: {
      type: cited("contract", 1, "Vertrag"),
      subtype: cited("management_contract", 1, "Mietverwaltungsvertrag"),
      title: cited("Mietverwaltungsvertrag", 1, "Mietverwaltungsvertrag"),
      amount_total: { value: "35.70", source: null, derived_from: ["fee_net", "monthly_fee"], checks: ["derived", "validates"], confidence: "high" },
      counterparty: cited("Muster Hausverwaltung GmbH", 1, "Muster Hausverwaltung GmbH"),
      period_start: cited("2026-05-01", 1, "Der Vertrag beginnt am 01.05.2026."),
      period_end: empty,
      reference: empty,
      notes: empty,
    },
    notes: [],
  },
  { key: "link:property", entity_type: "link", mode: "create", target_id: propertyId, fields: {}, notes: [] },
];

test("one form: the hand fields and the link are in the card, and the page's forms wait for the decision", async ({ page, signedIn }) => {
  void signedIn;
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Verwaltung",
    kind: "etw",
    address: { street: "Teststraße", house_number: "7", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  const documentId = await uploadPdf(page, "Verwaltervertrag.pdf");
  // A link the document already has: shown, but not removable while the card proposes one.
  await apiCall(page, "POST", `/api/documents/${documentId}/links`, { target_type: "property", target_id: property.id });
  const MANAGEMENT_RECORDS = managementRecords(property.id);
  const mock: Mock = {
    list: [proposal(documentId, { kind: "management_contract", records: MANAGEMENT_RECORDS })],
    poll: null,
    polls: 0,
    applies: [],
    apply: () => ({
      status: 200,
      json: {
        proposal: proposal(documentId, { kind: "management_contract", status: "applied", records: MANAGEMENT_RECORDS, decided_at: NOW }),
        applied: [{ key: "typing", entity_type: "typing", mode: "create", skipped: false, entity_id: documentId }],
      },
    }),
  };
  await mockProposals(page, mock);
  await page.goto(`/documents/${documentId}`);

  const form = vorschlag(page).getByRole("form", { name: "Vorschlag prüfen" });
  const typingForm = page.getByRole("form", { name: "Typisierung bearbeiten" });
  const linkForm = page.getByRole("form", { name: "Verknüpfung hinzufügen" });
  const remove = page.getByRole("button", { name: "entfernen", exact: true });
  await expect(form).toBeVisible();
  // Never both: while the proposal waits, the page's own typing and link forms are not there.
  await expect(typingForm).toHaveCount(0);
  await expect(page.getByText(POINTER)).toBeVisible();
  await expect(linkForm).toHaveCount(0);
  await expect(remove).toHaveCount(0);
  await expect(page.getByText(LINK_POINTER)).toBeVisible();
  await expect(page.getByText(/\(seit /)).toHaveCount(1); // the existing link, read-only

  const card = form.getByRole("region", { name: /^Typisierung/ });
  await expect(card.getByLabel("Vergütung pro Monat (€)")).toHaveValue("35,70");
  await expect(card.getByText("Berechnet aus: Vergütung laut Vertrag (netto), Vergütungsklausel (z. B. „zzgl. MwSt.“).")).toBeVisible();
  await expect(card.getByLabel("Gegenpartei", { exact: true })).toHaveValue("Muster Hausverwaltung GmbH");
  await expect(card.getByLabel("Zeitraum von", { exact: true })).toHaveValue("2026-05-01");
  await expect(card.getByLabel("Referenz", { exact: true })).toHaveValue("");
  await card.getByLabel("Referenz", { exact: true }).fill("MV-2026-7");
  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(vorschlag(page).getByText("Übernommen.")).toBeVisible();

  // The hand fields go with the typing; an empty one as null, so a clear is explicit.
  const sent = (mock.applies[0] as { records: Json[] }).records.find((r) => r.key === "typing");
  expect(sent).toMatchObject({
    skip: false,
    values: {
      type: "contract",
      subtype: "management_contract",
      amount_total: "35.70",
      counterparty: "Muster Hausverwaltung GmbH",
      period_start: "2026-05-01",
      period_end: null,
      reference: "MV-2026-7",
      notes: null,
    },
  });
  // Decided: the card is gone and the page's form is the one place to change values again.
  await expect(form).toHaveCount(0);
  await expect(typingForm).toBeVisible();
  await expect(page.getByText(POINTER)).toHaveCount(0);
  await expect(linkForm).toBeVisible();
  await expect(remove).toHaveCount(1);
  await expect(page.getByText(LINK_POINTER)).toHaveCount(0);
});

test("one form: a tenant document's typing-only proposal hides the page's form too", async ({ page, signedIn }) => {
  void signedIn;
  const documentId = await uploadPdf(page, "Kautionsnachweis.pdf");
  const records = [
    {
      key: "typing",
      entity_type: "typing",
      mode: "create",
      fields: {
        type: { ...cited("letter", 1, "Kautionsnachweis"), confidence: "medium" },
        amount_total: empty,
        period_start: empty,
        period_end: empty,
        counterparty: empty,
        reference: empty,
        notes: empty,
      },
      notes: [],
    },
  ];
  const mock: Mock = {
    list: [proposal(documentId, { status: "blocked_tenant_data", kind: null, contains_tenant_data: true, records })],
    poll: null,
    polls: 0,
    applies: [],
    apply: () => ({ status: 500, json: {} }),
  };
  await mockProposals(page, mock);
  await page.goto(`/documents/${documentId}`);

  const form = vorschlag(page).getByRole("form", { name: "Vorschlag prüfen" });
  await expect(form).toBeVisible();
  await expect(page.getByRole("form", { name: "Typisierung bearbeiten" })).toHaveCount(0);
  await expect(page.getByText(POINTER)).toBeVisible();
  const card = form.getByRole("region", { name: /^Typisierung/ });
  await expect(card.getByLabel("Betrag gesamt (€)")).toHaveValue("");
  await expect(card.getByLabel("Notiz", { exact: true })).toBeVisible();
  // No link proposed: linking stays on the page, so the link form shows.
  await expect(page.getByRole("form", { name: "Verknüpfung hinzufügen" })).toBeVisible();
  await expect(page.getByText(LINK_POINTER)).toHaveCount(0);
});

/** A Wohnflächenberechnung listing several Wohnungen, none of which the code could pick (D139). */
const DWELLING_RECORDS = [
  {
    key: "property",
    entity_type: "property",
    mode: "patch",
    target_id: PROPERTY_ID,
    fields: { living_area_m2: { ...missing, source: { page: 2, quote: "WE 2 1. OG links 68,40 m² 85/1000" }, checks: ["cited"] } },
    current: { living_area_m2: null },
    options: {
      living_area_m2: [
        { value: "68.40", label: "WE 2 · 1. OG links · MEA 85/1000 · 68,40 m²" },
        { value: "61.40", label: "WE 3 · 1. OG rechts · MEA 85/1000 · 61,40 m²" },
      ],
    },
    notes: ["Welche Wohnung ist Ihre? Die Wohnflächenberechnung nennt 2 Wohnungen; bitte Ihre unten anklicken, dann „Übernehmen“."],
  },
];

test("a Wohnflächenberechnung of several flats: one click picks the household's Wohnfläche", async ({ page, signedIn }) => {
  void signedIn;
  const documentId = await uploadPdf(page, "Wohnflaechenberechnung.pdf");
  const mock: Mock = {
    list: [proposal(documentId, { kind: "general", target_mode: "patch", records: DWELLING_RECORDS })],
    poll: null,
    polls: 0,
    applies: [],
    apply: () => ({ status: 409, json: { error: "conflict", code: "proposal.already_decided", message: "already decided" } }),
  };
  await mockProposals(page, mock);

  await page.goto(`/documents/${documentId}`);
  const form = vorschlag(page).getByRole("form", { name: "Vorschlag prüfen" });
  const property = form.getByRole("region", { name: "Immobilie", exact: true });
  await expect(property).toContainText("Welche Wohnung ist Ihre?");
  const area = property.getByLabel("Wohnfläche (m²)", { exact: true });
  await expect(area).toHaveValue("");

  await property
    .getByRole("radiogroup", { name: "Wohnfläche (m²): Vorschläge aus dem Dokument" })
    .getByLabel("WE 3 · 1. OG rechts · MEA 85/1000 · 61,40 m²")
    .check();
  await expect(area).toHaveValue("61,40");
  await form.getByRole("button", { name: "Übernehmen" }).click();
  await expect(form.getByText("Über diesen Vorschlag wurde schon entschieden (übernommen oder verworfen).")).toBeVisible();

  const sent = (mock.applies[0] as { records: { key: string; values: { living_area_m2?: string } }[] }).records;
  expect(sent).toHaveLength(1);
  expect(sent[0]?.key).toBe("property");
  expect(Number(sent[0]?.values.living_area_m2)).toBe(61.4);
});
