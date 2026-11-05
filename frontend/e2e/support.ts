// Shared fixtures for the e2e tests (docs/phases/phase-1-step-3.md §10.3).
//
// - `enrollmentUrl`: a fresh workspace per test, provisioned like production by `hausbuch
//   bootstrap` as the schema owner (scripts/e2e-db.sh bootstrap), so no two tests share data.
// - `authenticator`: a CDP virtual WebAuthn authenticator in the test's page (ctap2,
//   internal, resident keys, user verification), the standard way to drive passkeys
//   headlessly; Chromium only.
// - `signedIn`: both of the above, plus enrolling the first passkey through /enroll.
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

import { test as base, expect, type CDPSession, type Locator, type Page } from "@playwright/test";

const run = promisify(execFile);
const e2eDb = path.resolve(import.meta.dirname, "..", "..", "scripts", "e2e-db.sh");

const getCredentials = (cdp: CDPSession, authenticatorId: string) => cdp.send("WebAuthn.getCredentials", { authenticatorId });
/** A passkey as the CDP WebAuthn domain describes it (private key included). */
export type Credential = Awaited<ReturnType<typeof getCredentials>>["credentials"][number];

export class VirtualAuthenticator {
  private constructor(
    private readonly cdp: CDPSession,
    private readonly authenticatorId: string,
  ) {}

  static async attach(page: Page): Promise<VirtualAuthenticator> {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("WebAuthn.enable");
    const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
      options: {
        protocol: "ctap2",
        transport: "internal",
        hasResidentKey: true,
        hasUserVerification: true,
        isUserVerified: true,
        automaticPresenceSimulation: true,
      },
    });
    return new VirtualAuthenticator(cdp, authenticatorId);
  }

  /** The passkeys stored on this device, private keys included. */
  async credentials(): Promise<Credential[]> {
    return (await getCredentials(this.cdp, this.authenticatorId)).credentials;
  }

  /** Swap the device's passkeys, e.g. to act as a second device with the same browser. */
  async replaceCredentials(credentials: Credential[]): Promise<void> {
    await this.cdp.send("WebAuthn.clearCredentials", { authenticatorId: this.authenticatorId });
    for (const credential of credentials) {
      await this.cdp.send("WebAuthn.addCredential", { authenticatorId: this.authenticatorId, credential });
    }
  }
}

/** Provision a new workspace and its first user; returns the one-time enrollment URL. */
export async function bootstrapWorkspace(origin: string, name = "Luis"): Promise<string> {
  const { stdout } = await run(e2eDb, ["bootstrap", "--name", name, "--origin", origin], { encoding: "utf8" });
  const url = stdout.trim();
  expect(url).toMatch(/^http:\/\/localhost:\d+\/enroll#token=[A-Za-z0-9_-]{43}$/);
  return url;
}

/** Redeem an enrollment link with the page's authenticator; ends on the Übersicht. */
export async function enroll(page: Page, enrollmentUrl: string, label = "Laptop"): Promise<void> {
  await page.goto(enrollmentUrl);
  await expect(page.getByRole("heading", { name: "Passkey einrichten" })).toBeVisible();
  await page.getByLabel("Name des Passkeys").fill(label);
  await page.getByRole("button", { name: "Passkey einrichten" }).click();
  await expect(page.getByRole("heading", { name: "Übersicht" })).toBeVisible();
  await expect(page).toHaveURL("/");
}

/** The top bar's sign-out; ends on /login. */
export async function signOut(page: Page): Promise<void> {
  await page.getByRole("navigation", { name: "Hauptnavigation" }).getByRole("button", { name: "Abmelden" }).click();
  await expect(page.getByRole("heading", { name: "Anmelden" })).toBeVisible();
  await expect(page).toHaveURL("/login");
}

/** Sign in with whichever passkey the page's authenticator holds; ends on the Übersicht. */
export async function signIn(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Mit Passkey anmelden" }).click();
  await expect(page.getByRole("heading", { name: "Übersicht" })).toBeVisible();
  await expect(page).toHaveURL("/");
}

/** An error notice with this text. By text, because Next's route announcer is an alert too. */
export function alert(page: Page, text: string | RegExp): Locator {
  return page.getByRole("alert").filter({ hasText: text });
}

/**
 * A JSON request to the API through the web origin with the page's session cookie, for test
 * setup that is not itself under test. Unsafe methods carry the Origin a browser would send
 * (§6.6). Returns the parsed response after checking its status.
 */
export async function apiCall<T = Record<string, unknown>>(
  page: Page,
  method: "GET" | "POST",
  path: string,
  body?: unknown,
  expected = method === "POST" ? 201 : 200,
): Promise<T> {
  const origin = new URL(page.url()).origin;
  const response = await page.request.fetch(path, {
    method,
    data: body,
    headers: method === "GET" ? {} : { Origin: origin },
  });
  expect(response.status(), `${method} ${path}`).toBe(expected);
  return (await response.json()) as T;
}

/** Accept the next window.confirm() (removing a passkey, unlinking a document). */
export function acceptNextConfirm(page: Page): void {
  page.once("dialog", (dialog) => void dialog.accept());
}

export const test = base.extend<{
  enrollmentUrl: string;
  authenticator: VirtualAuthenticator;
  signedIn: void;
}>({
  // `provide` is Playwright's `use`; renamed so the React hooks lint rule leaves it alone.
  enrollmentUrl: async ({ baseURL }, provide) => {
    await provide(await bootstrapWorkspace(baseURL ?? "http://localhost:3100"));
  },
  authenticator: async ({ page }, provide) => {
    await provide(await VirtualAuthenticator.attach(page));
  },
  signedIn: async ({ page, authenticator, enrollmentUrl }, provide) => {
    void authenticator; // attached before the first navigation
    await enroll(page, enrollmentUrl);
    await provide();
  },
});

export { expect };
