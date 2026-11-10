// Was noch fehlt (docs/phases/phase-3.md D136): every gap is a plain to-do with an action. A
// flat without a Kaufpreisaufteilung shows the gap at the top of the Anlage V tab with what to
// do and the upload, and Übersicht lists it with the property's name; with one Kaufvertrag
// split recorded but not selected (a state that exists only from before phase 2 D133, so it is
// mocked here) the button selects it through the existing command; a real split, recorded
// through the API, closes the to-do. Synthetic data only.
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
  // One gap, one entry: the checklist's Kaufpreisaufteilung is not listed beside it.
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
