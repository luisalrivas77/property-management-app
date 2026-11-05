// Flow 3 (§10.3): how the API's refusals reach the screen (§9.5) and the same-origin check
// (§6.6). A 422 lands under its field and never repeats what was typed (§3.6); a 409 shows its
// message; a request from another origin is refused with 403 even with a valid session.
import { alert, apiCall, expect, test } from "./support";

test.describe("validation surfaces", () => {
  test("a 422 shows under its field and never echoes the input", async ({ page, signedIn }) => {
    void signedIn;

    await test.step("a bad IBAN: the rule code's German text under the IBAN field", async () => {
      // A well-formed German IBAN with wrong check digits (DE89 3704 0044 0532 0130 00 is valid).
      const badIban = "DE89 3704 0044 0532 0130 01";
      const compact = badIban.replaceAll(" ", "");
      await page.goto("/tenants/new");
      await page.getByLabel("Name").fill("Erika Beispiel");
      await page.getByLabel("IBAN (optional)").fill(badIban);
      const refused = page.waitForResponse((r) => r.url().endsWith("/api/tenants") && r.request().method() === "POST");
      await page.getByRole("button", { name: "Mieter anlegen" }).click();
      const response = await refused;
      expect(response.status()).toBe(422);
      const body = await response.text();
      expect(JSON.parse(body)).toMatchObject({ error: "rule_violation", code: "iban.bad_checksum" });

      await expect(page.getByLabel("IBAN (optional)")).toHaveAccessibleDescription(/Die Prüfziffern der IBAN stimmen nicht\./);
      await expect(page.getByLabel("IBAN (optional)")).toHaveAttribute("aria-invalid", "true");
      await expect(alert(page, "Bitte die markierten Felder prüfen.")).toBeVisible();
      // Neither the API nor the page repeats the IBAN or the name anywhere but in the inputs.
      for (const typed of [compact, badIban, "0532", "Erika Beispiel"]) {
        expect(body).not.toContain(typed);
        await expect(page.getByRole("main")).not.toContainText(typed);
      }
    });

    await test.step("a bad PLZ: Pydantic's message under the PLZ field", async () => {
      const badPostalCode = "98x76";
      await page.goto("/properties/new");
      await page.getByLabel("Bezeichnung").fill("Testwohnung");
      await page.getByLabel("Straße").fill("Teststraße");
      await page.getByLabel("Hausnummer").fill("1");
      await page.getByLabel("PLZ").fill(badPostalCode);
      await page.getByLabel("Ort").fill("Teststadt");
      await page.getByLabel("Baujahr").fill("1994");
      await page.getByLabel("Kaufpreis (€)").fill("100.000");
      await page.getByLabel("Notartermin").fill("2024-03-15");
      await page.getByLabel("Übergang Besitz, Nutzen und Lasten").fill("2024-05-01");
      const refused = page.waitForResponse((r) => r.url().endsWith("/api/properties") && r.request().method() === "POST");
      await page.getByRole("button", { name: "Immobilie anlegen" }).click();
      const response = await refused;
      expect(response.status()).toBe(422);
      const body = await response.text();
      const parsed = JSON.parse(body) as { error: string; detail: { loc: string[] }[] };
      expect(parsed.error).toBe("validation");
      expect(parsed.detail.map((d) => d.loc.join("."))).toEqual(["body.address.postal_code"]);
      expect(body).not.toContain(badPostalCode);

      await expect(page.getByLabel("PLZ")).toHaveAccessibleDescription(/should match pattern/);
      await expect(page.getByLabel("PLZ")).toHaveAttribute("aria-invalid", "true");
      await expect(page.getByLabel("Ort")).not.toHaveAttribute("aria-invalid", "true");
      await expect(page.getByRole("main")).not.toContainText(badPostalCode);
      await expect(page).toHaveURL("/properties/new"); // nothing was created
    });
  });

  test("a 409 shows its message", async ({ page, signedIn }) => {
    void signedIn;
    // Setup through the API: a flat let from 1 July 2024, open-ended.
    const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
      label: "Wohnung Konflikt",
      kind: "etw",
      address: { street: "Teststraße", house_number: "1", postal_code: "10115", city: "Berlin", country_code: "DE" },
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
      full_name: "Max Muster",
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

    // A second tenancy of the same unit from 2025 overlaps the open-ended first one.
    await page.goto(`/tenancies/new?unit=${unit.id}`);
    await expect(page.getByRole("heading", { name: "Mietverhältnis anlegen" })).toBeVisible();
    await page.getByLabel("Mieter 1").selectOption({ label: "Max Muster" });
    await page.getByLabel("Einzug", { exact: true }).fill("2025-01-01");
    await page.getByLabel("Kaltmiete (€ / Monat)").fill("1.000");
    const refused = page.waitForResponse((r) => r.url().endsWith("/api/tenancies") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Mietverhältnis anlegen" }).click();
    const response = await refused;
    expect(response.status()).toBe(409);
    expect(await response.json()).toMatchObject({ error: "conflict", code: "tenancy.overlap" });

    await expect(page.getByLabel("Einzug", { exact: true })).toHaveAccessibleDescription(
      "Für diese Einheit gibt es in diesem Zeitraum schon ein Mietverhältnis.",
    );
    await expect(page).toHaveURL(new RegExp(`/tenancies/new\\?unit=${unit.id}$`)); // nothing was created
    const tenancies = await apiCall<unknown[]>(page, "GET", `/api/units/${unit.id}/tenancies`);
    expect(tenancies).toHaveLength(1);
  });

  test("a request from another origin is refused, even with a valid session", async ({ page, signedIn, baseURL }) => {
    void signedIn;
    const owner = { full_name: "Eve Fremd" };

    await test.step("a forged Origin with the session cookie: 403 forbidden_origin", async () => {
      const forged: Record<string, string>[] = [{ Origin: "https://evil.example" }, { Origin: "http://localhost:1" }, {}];
      for (const headers of forged) {
        const response = await page.request.post("/api/owners", { data: owner, headers });
        expect(response.status(), JSON.stringify(headers)).toBe(403);
        expect(await response.json()).toMatchObject({ error: "forbidden_origin" });
      }
    });

    await test.step("a page on another site posting to the API: refused too", async () => {
      // 127.0.0.1 is another site than localhost: the browser sends its own Origin and,
      // SameSite=Lax, no session cookie. A text/plain POST needs no CORS preflight, so it
      // reaches the server, which refuses it before anything runs.
      const target = new URL("/api/owners", baseURL).toString();
      await page.goto(baseURL!.replace("localhost", "127.0.0.1") + "/login");
      const sent: Record<string, string>[] = [];
      await page.route(target, async (route) => {
        sent.push(await route.request().allHeaders());
        await route.continue();
      });
      const refused = page.waitForResponse((r) => r.url() === target && r.request().method() === "POST");
      await page.evaluate(
        (url) => fetch(url, { method: "POST", mode: "no-cors", credentials: "include", body: '{"full_name":"Eve Fremd"}' }),
        target,
      );
      // 403 is the Origin check (a missing session alone would be 401). The opaque response's
      // body is not readable here; the forged-Origin requests above show it is forbidden_origin.
      expect((await refused).status()).toBe(403);
      expect(sent).toHaveLength(1);
      expect(sent[0]?.origin).toBe(new URL(page.url()).origin);
    });

    await test.step("nothing was created; the same request from the app's own origin works", async () => {
      await page.goto("/owners");
      expect(await apiCall<unknown[]>(page, "GET", "/api/owners")).toEqual([]);
      await apiCall(page, "POST", "/api/owners", owner);
      expect(await apiCall<unknown[]>(page, "GET", "/api/owners")).toHaveLength(1);
    });
  });
});
