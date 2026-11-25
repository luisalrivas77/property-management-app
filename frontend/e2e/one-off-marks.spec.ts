// A payment's one-off mark in its detail panel on Konten › Umsätze (phase 10 §7.12 (b), D307).
// The account and its lines are the real API of a fresh workspace (a private account with the
// sample camt file); GET /household/one-off-marks and its write are mocked, since which parts are
// markable depends on the household's months of spending. Invented figures only.
//
// - A part that may be marked shows „Als einmalig markieren“; the POST names the part and the
//   answer, and the panel reads the parts again: „Als einmalig markiert …“ and „Doch normal zählen“.
// - A part whose category already counts as one-off says why, with no button.
// - A payment of several parts names each part on its button.
// - A payment with no part (income, a transfer, a rental line) shows nothing.
// - Phone width, light and dark: no sideways scroll.
import { readFileSync } from "node:fs";
import path from "node:path";

import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { apiCall, expect, test } from "./support";

type PaymentOneOff = components["schemas"]["PaymentOneOff"];
type OneOffMarkRequest = components["schemas"]["OneOffMarkRequest"];
type Transaction = { id: string; amount: string; booking_date: string };

const SAMPLE_ZIP = path.resolve(import.meta.dirname, "..", "..", "backend", "tests", "fixtures", "phase2", "bank_samples", "camt052.zip");
const PART_A = "00000000-0000-4000-8000-0000000000a1";
const PART_B = "00000000-0000-4000-8000-0000000000b2";

/** A private account with the sample's lines; returns its id and one debit of the sample. */
async function privateLine(page: Page): Promise<{ accountId: string; line: Transaction }> {
  const origin = new URL(page.url()).origin;
  const owner = await apiCall<{ id: string }>(page, "POST", "/api/owners", { full_name: "A. Muster", church_tax: false });
  const account = await apiCall<{ id: string }>(page, "POST", "/api/accounts", {
    label: "Girokonto A",
    bank: "Testbank",
    iban: "DE50100000000000000001", // the sample file's own
    purpose: "private",
    opening_balance: "0.00",
    opening_balance_on: "2026-03-31",
  });
  await apiCall(page, "POST", `/api/accounts/${account.id}/ownership`, { shares: [{ owner_id: owner.id, share_numerator: 1, share_denominator: 1 }] }, 200);
  const imported = await page.request.post(`/api/accounts/${account.id}/imports`, {
    multipart: { file: { name: "umsaetze.zip", mimeType: "application/zip", buffer: readFileSync(SAMPLE_ZIP) } },
    headers: { Origin: origin },
  });
  expect(imported.status()).toBe(201);
  const lines = await apiCall<Transaction[]>(page, "GET", `/api/accounts/${account.id}/transactions`);
  const line = lines.find((t) => t.amount.startsWith("-"));
  if (!line) throw new Error("the sample has no debit");
  return { accountId: account.id, line };
}

/** The parts GET serves for `line`; each POST flips the part's `marked` as a real write would. */
async function marks(page: Page, line: Transaction, initial: PaymentOneOff[]): Promise<{ parts: PaymentOneOff[]; posted: OneOffMarkRequest[] }> {
  const state = { parts: initial, posted: [] as OneOffMarkRequest[] };
  await page.route(
    (url) => url.pathname === "/api/household/one-off-marks",
    async (route: Route) => {
      const request = route.request();
      if (request.method() === "GET") {
        const asked = new URL(request.url()).searchParams.get("transaction_id");
        return route.fulfill({ json: asked === line.id ? state.parts : [] });
      }
      const body = request.postDataJSON() as OneOffMarkRequest;
      state.posted.push(body);
      state.parts = state.parts.map((part) => (part.allocation_id === body.allocation_id ? { ...part, marked: body.answer === "one_off" } : part));
      return route.fulfill({ status: 201, json: { allocation_id: body.allocation_id, answer: body.answer } });
    },
  );
  return state;
}

function part(allocationId: string, category: PaymentOneOff["category"], amount: string, extra: Partial<PaymentOneOff> = {}): PaymentOneOff {
  return { allocation_id: allocationId, category, amount, marked: false, category_one_off: false, ...extra };
}

function linePath(accountId: string, line: Transaction): string {
  return `/accounts/${accountId}?tab=umsaetze&monat=${line.booking_date.slice(0, 7)}#d-umsatz-${line.id}`;
}

test("„Als einmalig markieren“ marks the payment's part, and „Doch normal zählen“ puts it back", async ({ page, signedIn }) => {
  void signedIn;
  const { accountId, line } = await privateLine(page);
  const state = await marks(page, line, [part(PART_A, "hh_supermarket", "84.20")]);

  await page.goto(linePath(accountId, line));
  const panel = page.getByRole("complementary");
  const mark = panel.getByRole("button", { name: "Als einmalig markieren", exact: true });
  await expect(mark).toBeVisible();
  await mark.click();
  await expect(panel).toContainText("Als einmalig markiert: zählt nicht in Budget, Sparquote und Schnitt.");
  expect(state.posted).toEqual([{ allocation_id: PART_A, answer: "one_off" }]);

  const back = panel.getByRole("button", { name: "Doch normal zählen", exact: true });
  await back.click();
  await expect(mark).toBeVisible();
  await expect(panel).not.toContainText("Als einmalig markiert");
  expect(state.posted).toEqual([
    { allocation_id: PART_A, answer: "one_off" },
    { allocation_id: PART_A, answer: "regular" },
  ]);
});

test("a part whose category counts as one-off says so; several parts are named; no part shows nothing", async ({ page, signedIn }) => {
  void signedIn;
  const { accountId, line } = await privateLine(page);
  const state = await marks(page, line, [part(PART_A, "hh_electronics", "499.00", { category_one_off: true })]);

  await page.goto(linePath(accountId, line));
  const panel = page.getByRole("complementary");
  await expect(panel).toContainText("Zählt als einmalig, weil „Elektronik & Technik“ als einmalig zählt.");
  await expect(panel.getByRole("button", { name: /einmalig markieren|normal zählen/ })).toHaveCount(0);

  // Two parts: each button names its part, and only that part is written.
  state.parts = [part(PART_A, "hh_supermarket", "40.00"), part(PART_B, "hh_clothing", "60.00", { marked: true })];
  await page.reload();
  const first = panel.getByRole("button", { name: /^Als einmalig markieren: Supermarkt, 40\s€$/ });
  const second = panel.getByRole("button", { name: /^Doch normal zählen: Kleidung & Schuhe, 60\s€$/ });
  await expect(first).toBeVisible();
  await expect(second).toBeVisible();
  await expect(panel).toContainText("Kleidung & Schuhe, 60");
  await first.click();
  await expect(panel.getByRole("button", { name: /^Doch normal zählen: Supermarkt, 40\s€$/ })).toBeVisible();
  expect(state.posted).toEqual([{ allocation_id: PART_A, answer: "one_off" }]);

  // Nothing to mark: no button and no note.
  state.parts = [];
  await page.reload();
  await expect(panel.getByRole("heading").first()).toBeVisible();
  await expect(panel.getByRole("button", { name: /einmalig markieren|normal zählen/ })).toHaveCount(0);

  // Phone width, light and dark: the panel fits without a sideways scroll.
  state.parts = [part(PART_A, "hh_supermarket", "40.00"), part(PART_B, "hh_clothing", "60.00", { marked: true })];
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("about:blank");
    await page.goto(linePath(accountId, line));
    const shown = page.getByRole("button", { name: /^Doch normal zählen: Kleidung/ });
    await expect(shown).toBeVisible();
    await shown.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await page.screenshot({ path: test.info().outputPath(`one-off-mark-${scheme}.png`) });
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
});
