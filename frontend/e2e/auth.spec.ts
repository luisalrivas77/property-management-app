// Flow 1 (§10.3): enroll from the bootstrap link, sign out and back in with the passkey, add
// a second passkey and remove it again (§6.3, §6.7).
import { acceptNextConfirm, alert, enroll, expect, signIn, signOut, test } from "./support";

test("enroll from the bootstrap link, sign in again, add and remove a second passkey", async ({
  page,
  authenticator,
  enrollmentUrl,
}) => {
  await test.step("enroll the first passkey from the one-time link", async () => {
    await enroll(page, enrollmentUrl, "Laptop");
    // The token sits in the fragment and is dropped from the address bar once read.
    expect(page.url()).not.toContain("token=");
    expect(await authenticator.credentials()).toHaveLength(1);
  });

  await test.step("the link is used up", async () => {
    await signOut(page);
    await page.goto(enrollmentUrl);
    await page.getByRole("button", { name: "Passkey einrichten" }).click();
    await expect(alert(page, "Nicht angemeldet oder Sitzung abgelaufen.")).toBeVisible();
    await expect(page).toHaveURL(/\/enroll$/);
  });

  await test.step("sign back in with the passkey", async () => {
    await page.goto("/login");
    await signIn(page);
  });

  const [laptop] = await authenticator.credentials();
  if (!laptop) throw new Error("the authenticator holds no passkey");

  await test.step("add a second passkey (another device)", async () => {
    // The API excludes the user's registered credentials, so the same authenticator would
    // refuse; empty it to act as a second device, and keep the first passkey to restore.
    await authenticator.replaceCredentials([]);
    await page.getByRole("link", { name: "Einstellungen" }).click();
    await expect(page.getByRole("heading", { name: "Einstellungen" })).toBeVisible();
    await page.getByLabel("Passkey hinzufügen").fill("Telefon");
    await page.getByRole("button", { name: "Passkey hinzufügen" }).click();
    const rows = page.getByRole("table").first().getByRole("row");
    await expect(rows).toHaveCount(3); // header + two passkeys
    await expect(rows.filter({ hasText: "Laptop" })).toContainText("(diese Sitzung)");
    await expect(rows.filter({ hasText: "Telefon" })).toBeVisible();
  });

  await test.step("remove the second passkey", async () => {
    const telefon = page.getByRole("row").filter({ hasText: "Telefon" });
    acceptNextConfirm(page);
    await telefon.getByRole("button", { name: "Entfernen" }).click();
    await expect(telefon).toHaveCount(0);
    const rows = page.getByRole("table").first().getByRole("row");
    await expect(rows).toHaveCount(2);
    // The last passkey cannot be removed (409 in the API; the button is disabled here).
    await expect(rows.filter({ hasText: "Laptop" }).getByRole("button", { name: "Entfernen" })).toBeDisabled();
  });

  await test.step("the removed passkey no longer signs in", async () => {
    await signOut(page);
    await page.getByRole("button", { name: "Mit Passkey anmelden" }).click();
    await expect(alert(page, "Dieser Passkey wurde nicht erkannt.")).toBeVisible();
    await expect(page).toHaveURL("/login");
  });

  await test.step("the first passkey still does", async () => {
    await authenticator.replaceCredentials([laptop]);
    await signIn(page);
  });
});
