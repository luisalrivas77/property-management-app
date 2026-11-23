// Übersicht as mockup 15-B2 (docs/phases/phase-10.md D268): the tiles, a key figure's mark as a
// dot before its name with no colour word, and a tapped tile's summary in the detail panel with a
// button into its section. The account and its balance are invented sample data.
import { apiCall, expect, test } from "./support";

test("Übersicht: tiles, an empty mark before its limit, a tile's summary opens beside the page", async ({ page, signedIn }) => {
  void signedIn;
  await apiCall(page, "POST", "/api/accounts", {
    label: "Girokonto A",
    bank: "Sparkasse",
    iban: "DE50100000000000000001",
    opening_balance: "1200.00",
    opening_balance_on: "2026-03-31",
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Übersicht", level: 1 })).toBeVisible();
  const tiles = page.getByRole("region", { name: "Auf einen Blick" });
  const bank = tiles.getByRole("link", { name: /^Bank/ });
  await expect(bank).toContainText("1 Konto");

  // No limit confirmed yet: the dot is an empty ring, read out as such, and no colour word.
  const rate = tiles.getByRole("link", { name: /Sparquote/ });
  await expect(rate.locator(".tile-dot")).toHaveAttribute("data-mark", "none");
  await expect(rate).toContainText("noch keine eigene Grenze festgelegt");
  await expect(tiles.getByText(/^(Grün|Gelb|Rot)$/)).toHaveCount(0);

  // A tapped tile opens its summary beside the page (the URL hash keeps it), with a button into
  // its section; the page itself does not change.
  await bank.click();
  await expect(page).toHaveURL(/#d-kachel-bank$/);
  const panel = page.getByRole("dialog", { name: "Bank" }).or(page.getByRole("complementary", { name: "Bank" }));
  await expect(panel).toContainText("Girokonto A");
  await expect(panel).toContainText("1.200,00 €");
  await panel.getByRole("link", { name: "Zu Konten › Bank" }).click();
  await expect(page).toHaveURL(/\/accounts\?art=bank/);

  // The panel closes back to the page.
  await page.goto("/#d-kachel-pruefen");
  const review = page.getByRole("dialog", { name: "Zu prüfen" }).or(page.getByRole("complementary", { name: "Zu prüfen" }));
  await expect(review.getByRole("link", { name: "Zu prüfen öffnen" })).toHaveAttribute("href", "/inbox");
  await review.getByRole("button", { name: /Übersicht|Schließen/ }).first().click();
  await expect(review).toHaveCount(0);
});
