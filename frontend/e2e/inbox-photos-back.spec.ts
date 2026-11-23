// Zu prüfen's photos panel (docs/phases/phase-10.md §7.6 (f)): like every detail panel (D267) it
// is the URL hash, so the browser's (the phone's) back button closes it and keeps the photos;
// „Fotos (n)“ opens it again. A fresh workspace; a synthetic 40×30 PNG.
import { expect, test } from "./support";

const PHOTO = {
  name: "beleg.png",
  mimeType: "image/png",
  buffer: Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAIAAADRv8uKAAAAK0lEQVR4nO3NMQ0AAAgDsMmZfxXIQgYcTfo3056IWCwWi8VisVgsFov/xguJMCDM2TzTeAAAAABJRU5ErkJggg==",
    "base64",
  ),
};

test("the back button closes the photos panel and keeps the photos", async ({ page, signedIn }) => {
  void signedIn;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Hochladen, bestätigen, fertig" })).toBeVisible();

  await page.getByLabel("Foto", { exact: true }).setInputFiles(PHOTO);
  // Below 1100 px the panel is a modal sheet sliding in from the right (D267).
  const panel = page.getByRole("dialog", { name: "Fotos" });
  await expect(panel.getByRole("list", { name: "Seiten" }).getByRole("listitem")).toHaveCount(1);
  await expect(page).toHaveURL(/\/inbox#d-fotos-seiten$/);

  await page.goBack();
  await expect(panel).toHaveCount(0);
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "Hochladen, bestätigen, fertig" })).toBeVisible();

  await page.getByRole("button", { name: "Fotos (1)" }).click();
  await expect(panel.getByRole("list", { name: "Seiten" }).getByRole("listitem")).toHaveCount(1);
  await expect(page).toHaveURL(/\/inbox#d-fotos-seiten$/);
});
