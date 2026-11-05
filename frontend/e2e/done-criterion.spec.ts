// Flow 2 (§10.3): the phase 1 done criterion through the UI. An ETW with a flat and a
// parking space, its loan, a tenant and a tenancy with rent, the lease uploaded as a PDF and
// linked to the tenancy, then the move-in corrected. The property's Verlauf (History) must
// show purchase → financing → possession → move-in (marked corrected) → rent, in that order.
// The dates are those of the timeline.yaml scenario (backend/tests/fixtures/phase1).
import { expect, test } from "./support";

const LEASE_PDF = Buffer.from(
  "%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n" +
    "2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n" +
    "trailer << /Root 1 0 R >>\n%%EOF\n",
);

test("the done criterion: ETW, loan, tenancy, lease, corrected move-in, History in order", async ({ page, signedIn }) => {
  void signedIn;
  let propertyUrl = "";

  await test.step("the property: an ETW bought in 2024", async () => {
    await page.getByRole("link", { name: "Immobilie anlegen" }).click();
    await page.getByLabel("Bezeichnung").fill("Wohnung Lindenstraße");
    await page.getByLabel("Art", { exact: true }).selectOption({ label: "Eigentumswohnung (ETW)" });
    await page.getByLabel("Straße").fill("Lindenstraße");
    await page.getByLabel("Hausnummer").fill("12");
    await page.getByLabel("PLZ").fill("10115");
    await page.getByLabel("Ort").fill("Berlin");
    await page.getByLabel("Baujahr").fill("1994");
    await page.getByLabel("Kaufpreis (€)").fill("289.000");
    await page.getByLabel("Notartermin").fill("2024-03-15");
    await page.getByLabel("Übergang Besitz, Nutzen und Lasten").fill("2024-05-01");
    await page.getByRole("button", { name: "Immobilie anlegen" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Wohnung Lindenstraße" })).toBeVisible();
    propertyUrl = new URL(page.url()).pathname;
    expect(propertyUrl).toMatch(/^\/properties\/[0-9a-f-]{36}$/);
  });

  await test.step("two units: the flat and a parking space", async () => {
    for (const unit of [
      { label: "WE 3", area: "68,40", rooms: "2,5", mea: "85/1000" },
      { label: "TG-Stellplatz 12", area: "12,50", rooms: "", mea: "" },
    ]) {
      await page.goto(`${propertyUrl}?tab=einheiten`);
      await page.getByRole("link", { name: "Einheit anlegen" }).click();
      await expect(page.getByRole("heading", { name: "Einheit anlegen" })).toBeVisible();
      await page.getByLabel("Bezeichnung").fill(unit.label);
      await page.getByLabel("Fläche (m²)").fill(unit.area);
      await page.getByLabel("Zimmer (optional)").fill(unit.rooms);
      await page.getByLabel("MEA (optional)").fill(unit.mea);
      await page.getByRole("button", { name: "Einheit anlegen" }).click();
      await expect(page.getByRole("link", { name: unit.label, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("table").getByRole("row")).toHaveCount(3);
  });

  await test.step("the loan", async () => {
    await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Darlehen" }).click();
    await page.getByRole("link", { name: "Darlehen anlegen" }).click();
    await expect(page.getByRole("heading", { name: "Darlehen anlegen" })).toBeVisible();
    await page.getByLabel("Bank").fill("Sparkasse Musterstadt");
    await page.getByLabel("Darlehenssumme (€)").fill("240.000");
    await page.getByLabel("Nominalzins (% p. a.)").fill("3,65");
    await page.getByLabel("Rate (€ / Monat)").fill("1.130");
    await page.getByLabel("Auszahlung am").fill("2024-04-30");
    await page.getByLabel("Erste Rate am").fill("2024-05-30");
    await page.getByLabel("Zinsbindung bis (optional)").fill("2034-04-30");
    await page.getByRole("button", { name: "Darlehen anlegen" }).click();
    await expect(page).toHaveURL(/\/loans\/[0-9a-f-]{36}$/);
    await expect(page.getByText("Sparkasse Musterstadt").first()).toBeVisible();
  });

  let tenancyUrl = "";
  await test.step("a tenant and the tenancy of the flat, with its initial rent", async () => {
    await page.goto(`${propertyUrl}?tab=einheiten`);
    await page.getByRole("link", { name: "WE 3", exact: true }).click();
    await page.getByRole("link", { name: "Mietverhältnis anlegen" }).click();
    await expect(page.getByRole("heading", { name: "Mietverhältnis anlegen" })).toBeVisible();

    await page.getByRole("button", { name: "Mieter anlegen" }).click();
    const dialog = page.getByRole("dialog", { name: "Mieter anlegen" });
    await dialog.getByLabel("Name").fill("Max Muster");
    await dialog.getByLabel("IBAN (optional)").fill("DE89 3704 0044 0532 0130 00");
    await dialog.getByRole("button", { name: "Mieter anlegen" }).click();
    await expect(dialog).toHaveCount(0);
    // The tenant created in the dialog is chosen as the first party at once.
    await expect(page.getByLabel("Mieter 1").getByRole("option", { name: "Max Muster", selected: true })).toBeAttached();

    await page.getByLabel("Einzug", { exact: true }).fill("2024-07-15"); // a typo, corrected below
    await page.getByLabel("Art", { exact: true }).selectOption({ label: "Barkaution / Kautionskonto" });
    await page.getByLabel("Betrag (€)").fill("2.850");
    await page.getByLabel("Kaltmiete (€ / Monat)").fill("950");
    await page.getByLabel("NK-Vorauszahlung (€ / Monat)").fill("220");
    await page.getByRole("button", { name: "Mietverhältnis anlegen" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Mietverhältnis WE 3" })).toBeVisible();
    tenancyUrl = new URL(page.url()).pathname;
    expect(tenancyUrl).toMatch(/^\/tenancies\/[0-9a-f-]{36}$/);
  });

  await test.step("upload the lease as a PDF, type it and link it to the tenancy", async () => {
    await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("link", { name: "Dokumente" }).click();
    await page.getByLabel("Dokumente hochladen").setInputFiles({
      name: "Mietvertrag-WE3.pdf",
      mimeType: "application/pdf",
      buffer: LEASE_PDF,
    });
    const uploaded = page.getByRole("status").filter({ hasText: "Hochgeladen:" });
    await expect(uploaded).toBeVisible();
    await uploaded.getByRole("link", { name: "Mietvertrag-WE3.pdf" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Mietvertrag-WE3.pdf" })).toBeVisible();

    await page.getByLabel("Typ", { exact: true }).selectOption({ label: "Vertrag" });
    await page.getByLabel("Untertyp (optional)").selectOption({ label: "Mietvertrag" });
    await page.getByLabel("Titel (optional)").fill("Mietvertrag WE 3");
    await page.getByLabel("Datum des Dokuments (optional)").fill("2024-06-10");
    await page.getByRole("button", { name: "Typisierung speichern" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Typisierung gespeichert." })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Mietvertrag WE 3" })).toBeVisible();

    await page.getByLabel("Verknüpfen mit").selectOption({ label: "Mietverhältnis" });
    await page.getByLabel("Mietverhältnis", { exact: true }).selectOption({ label: "WE 3: Max Muster (ab 15.07.2024)" });
    await page.getByRole("button", { name: "Verknüpfen" }).click();
    await expect(page.getByRole("listitem").filter({ hasText: "Mietverhältnis:" })).toContainText("WE 3: Max Muster");
  });

  await test.step("correct the move-in date", async () => {
    await page.goto(tenancyUrl);
    // The lease is listed among the tenancy's documents.
    await expect(page.getByRole("link", { name: "Mietvertrag WE 3" })).toBeVisible();
    await page.getByRole("button", { name: "Einzug korrigieren" }).click();
    const dialog = page.getByRole("dialog", { name: "Einzugsdatum korrigieren" });
    await dialog.getByLabel("Richtiges Einzugsdatum").fill("2024-07-01");
    await dialog.getByRole("button", { name: "Korrektur speichern" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText("korrigiert, ursprünglich 15.07.2024")).toBeVisible();
  });

  await test.step("Verlauf: purchase → financing → possession → move-in (corrected) → rent", async () => {
    await page.goto(propertyUrl);
    await page.getByRole("navigation", { name: "Bereiche" }).getByRole("link", { name: "Verlauf" }).click();
    const entries = page.getByRole("list", { name: "Verlauf" }).getByRole("listitem");
    await expect(entries).toHaveText([
      /^15\.03\.2024\s*Kaufvertrag beurkundet\s*Kaufpreis 289\.000,00 €$/,
      /^30\.04\.2024\s*Darlehen ausgezahlt · Sparkasse Musterstadt\s*240\.000,00 €$/,
      /^01\.05\.2024\s*Übergang Besitz, Nutzen und Lasten/,
      /^01\.07\.2024\s*Einzug · WE 3, Max Muster\s*korrigiert, ursprünglich 15\.07\.2024$/,
      // Luis, 5 Nov 2026: correcting the move-in also moves the first rent row, so the rent
      // starts on the corrected date and History marks it corrected (design §10.8 item 6).
      /^01\.07\.2024\s*Miete · WE 3, Max Muster\s*korrigiert, ursprünglich 15\.07\.2024\s*Korrektur: 950,00 € kalt \+ 220,00 € NK-Vorauszahlung/,
    ]);
  });
});
