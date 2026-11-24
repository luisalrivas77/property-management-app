// The shell of phase 10 step 2: the menu (D266), the detail panel (D267), light or dark (D283)
// and the language (D284).
import { apiCall, expect, mainNav, navigateTo, openMore, test } from "./support";

// Planung left „Mehr“ in phase 10 wave 3 (a) (D288): its parts are Haushalt's tabs and on Anlagen.
const MORE = ["Konten", "Immobilien", "Steuern", "Dokumente", "Einstellungen"];

async function createProperty(page: import("@playwright/test").Page): Promise<string> {
  const property = await apiCall<{ id: string }>(page, "POST", "/api/properties", {
    label: "Wohnung Panel",
    kind: "etw",
    address: { street: "Teststraße", house_number: "7", postal_code: "10115", city: "Berlin", country_code: "DE" },
    build_year: 1994,
    notarized_on: "2024-03-15",
    purchase_date: "2024-05-01",
    purchase_price: "289000.00",
  });
  return property.id;
}

test("the computer's menu: four tabs and „Mehr“, Mieter and Eigentümer reached through Immobilien", async ({ page, signedIn }) => {
  void signedIn;
  const nav = mainNav(page);
  for (const name of ["Übersicht", "Zu prüfen", "Haushalt", "Anlagen"]) await expect(nav.getByRole("link", { name })).toBeVisible();
  // The other five only behind „Mehr“, and no entry for Mieter, Eigentümer or Planung.
  await expect(nav.getByRole("link")).toHaveCount(4);
  const more = nav.getByRole("button", { name: "Mehr" });
  await more.click();
  for (const name of MORE) await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Mieter", exact: true })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Eigentümer", exact: true })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Planung", exact: true })).toHaveCount(0);
  // Escape closes it and gives the focus back to its button.
  await page.keyboard.press("Escape");
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await expect(more).toBeFocused();

  await navigateTo(page, "Immobilien");
  await expect(page).toHaveURL("/properties");
  await expect(more).toHaveAttribute("data-current", "true");
  await page.getByRole("link", { name: /^Eigentümer\b/ }).click();
  await expect(page).toHaveURL("/owners");
  // A page inside Immobilien marks „Mehr“ as the current area.
  await expect(more).toHaveAttribute("data-current", "true");
  await page.goto("/tenants");
  await expect(page.getByRole("heading", { level: 1, name: "Mieter" })).toBeVisible();
});

test("the phone's menu is a bar at the bottom that covers nothing", async ({ page, signedIn }) => {
  void signedIn;
  await page.setViewportSize({ width: 390, height: 844 });
  await navigateTo(page, "Einstellungen");
  await expect(page).toHaveURL("/settings");
  const bar = await mainNav(page).boundingBox();
  expect(bar).not.toBeNull();
  if (!bar) return;
  // At the bottom of the screen, the full width, and the page starts with its title.
  expect(Math.round(bar.y + bar.height)).toBe(844);
  expect(Math.round(bar.width)).toBe(390);
  const title = await page.getByRole("heading", { level: 1, name: "Einstellungen" }).boundingBox();
  expect(title?.y ?? 999).toBeLessThan(60);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  // Scrolled to the end, the page's last element ends above the bar (polled: the page's
  // sections load one after the other and grow it).
  await expect(page.getByRole("button", { name: "Überall abmelden" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => {
        window.scrollTo(0, document.documentElement.scrollHeight);
        const inner = document.querySelector(".content-inner");
        return inner ? inner.getBoundingClientRect().bottom : Infinity;
      }),
    )
    .toBeLessThanOrEqual(bar.y);

  // „Mehr“ is a sheet above the bar, with Abmelden in it.
  await openMore(page);
  await expect(mainNav(page).getByRole("button", { name: "Abmelden" })).toBeVisible();
  await expect(mainNav(page).getByRole("link", { name: "Foto aufnehmen" })).toBeVisible();
});

test("the detail panel opens from a row, keeps its state in the hash and closes with back and Escape", async ({ page, signedIn }) => {
  void signedIn;
  const id = await createProperty(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/properties");
  const row = page.getByRole("link", { name: /^Wohnung Panel/ });
  await row.click();
  await expect(page).toHaveURL(`/properties#d-property-${id}`);
  const panel = page.getByRole("complementary", { name: "Wohnung Panel" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Wohnung Panel" })).toBeFocused();
  await expect(panel).toContainText("15.03.2024");
  await expect(row).toHaveAttribute("aria-current", "true");

  // The browser's back button closes it, without leaving the page.
  await page.goBack();
  await expect(page).toHaveURL("/properties");
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "Immobilien" })).toBeVisible();

  // A link with the hash opens it, and Escape closes it.
  await page.goto(`/properties#d-property-${id}`);
  await expect(panel).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(page).toHaveURL("/properties");

  // With „Mehr“ open over the panel, the first Escape closes the menu only, the second the panel.
  await row.click();
  await expect(panel).toBeVisible();
  await openMore(page);
  await page.keyboard.press("Escape");
  await expect(mainNav(page).getByRole("button", { name: "Mehr" })).toHaveAttribute("aria-expanded", "false");
  await expect(panel).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);

  // Opened from the row, Escape gives the row the focus back.
  await row.click();
  await expect(panel.getByRole("heading", { name: "Wohnung Panel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(page).toHaveURL("/properties");
  await expect(row).toBeFocused();

  // On the phone it slides in over the page, a dialog with „‹ Immobilien“.
  await page.setViewportSize({ width: 390, height: 844 });
  await row.click();
  const sheet = page.getByRole("dialog", { name: "Wohnung Panel" });
  await expect(sheet).toBeVisible();
  await sheet.getByRole("button", { name: "Immobilien" }).click();
  await expect(sheet).toHaveCount(0);
  await expect(page).toHaveURL("/properties");
  await expect(row).toBeFocused();
});

test("„Hell“ wins over a dark device, and English changes the menu and the request language", async ({ page, signedIn }) => {
  void signedIn;
  await page.emulateMedia({ colorScheme: "dark" });
  await navigateTo(page, "Einstellungen");
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const darkBackground = await background();

  await page.getByRole("link", { name: /^Darstellung\b/ }).click();
  await page.getByRole("radio", { name: "Hell", exact: true }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await background()).not.toBe(darkBackground);
  // Applied before the first paint on the next load.
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("radio", { name: /^Wie das Gerät/ }).check();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /./);
  expect(await background()).toBe(darkBackground);

  await page.getByRole("radio", { name: "English" }).check();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("link", { name: "Overview" })).toBeVisible();
  const request = page.waitForRequest((r) => r.url().includes("/api/inbox"));
  await nav.getByRole("link", { name: "To review" }).click();
  expect((await request).headers()["accept-language"]).toBe("en");

  await page.goto("/settings#d-setting-language");
  await page.getByRole("radio", { name: "Deutsch" }).check();
  await expect(mainNav(page).getByRole("link", { name: "Übersicht" })).toBeVisible();
});
