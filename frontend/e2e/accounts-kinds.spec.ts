// Konten's tabs show what each kind is worth (docs/phases/phase-10.md §7.13 (a), D313): the
// depot its whole value (funds, cash and money on its way, the Anlagen portfolio card's total),
// the credit card what is owed with the payment on its way, the exchange the state its own page
// shows. The accounts are real accounts of a fresh workspace; GET /net-worth/overview and the
// exchange's GET /accounts/{id}/exchange are the real answers with the parts a full ledger would
// give put in, since a depot with funds, a card with a statement and an exchange with an export
// need a whole ledger. Invented names and figures only.
import type { Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { apiCall, expect, test } from "./support";

type Account = components["schemas"]["Account"];
type NetWorthOverview = components["schemas"]["NetWorthOverview"];
type ExchangeOverview = components["schemas"]["ExchangeOverview"];

test("Konten: the depot's whole value, the card's debt with its payment on its way, the exchange's state", async ({ page, signedIn }) => {
  void signedIn;
  const open = { opening_balance: "0.00", opening_balance_on: "2026-01-01" };
  const depot = await apiCall<Account>(page, "POST", "/api/accounts", { label: "Depot Beispiel", bank: "Depotbank", kind: "depot", ...open });
  const card = await apiCall<Account>(page, "POST", "/api/accounts", { label: "Karte Beispiel", bank: "Kartenbank", kind: "credit_card", ...open });
  const exchange = await apiCall<Account>(page, "POST", "/api/accounts", {
    label: "Börse Beispiel",
    bank: "BISON",
    provider: "BISON",
    kind: "exchange",
    ...open,
  });

  await page.route("**/api/net-worth/overview", async (route: Route) => {
    const real = (await (await route.fetch()).json()) as NetWorthOverview;
    const parts = (value: string, balance: string, positions: string, transit: string) => ({
      value_eur: value,
      balance_eur: balance,
      positions_eur: positions,
      in_transit_eur: transit,
    });
    await route.fulfill({
      json: {
        ...real,
        today: { ...real.today, by_account_tab: { card: "0.00", depot: "8000.00", exchange: "1500.00" } },
        today_accounts: {
          [depot.id]: parts("8000.00", "200.00", "7300.00", "500.00"),
          [card.id]: parts("0.00", "-300.00", "0.00", "300.00"),
          [exchange.id]: parts("1500.00", "0.00", "1500.00", "0.00"),
        },
        today_tabs: {
          card: parts("0.00", "-300.00", "0.00", "300.00"),
          depot: parts("8000.00", "200.00", "7300.00", "500.00"),
          exchange: parts("1500.00", "0.00", "1500.00", "0.00"),
        },
      } satisfies NetWorthOverview,
    });
  });
  await page.route(`**/api/accounts/${exchange.id}/exchange`, async (route: Route) => {
    const real = (await (await route.fetch()).json()) as ExchangeOverview;
    await route.fulfill({ json: { ...real, reconciled: true } satisfies ExchangeOverview });
  });

  // The depot: its whole value as the row's Stand and the tab's total, its parts in the panel.
  await page.goto("/accounts?art=depot");
  await expect(page.locator(".kind-total")).toHaveText("8.000,00 €");
  const depotRow = page.locator(".account-row").filter({ hasText: "Depot Beispiel" });
  await expect(depotRow.locator(".account-balance")).toContainText("8.000,00 €");
  await expect(depotRow.locator(".account-balance")).toContainText("heute, mit Fonds");
  await depotRow.click();
  const panel = page.getByRole("dialog", { name: "Depot Beispiel" }).or(page.getByRole("complementary", { name: "Depot Beispiel" }));
  await expect(panel.locator(".detail-row").filter({ hasText: "Fonds" })).toContainText("7.300,00 €");
  await expect(panel.locator(".detail-row").filter({ hasText: "Bargeld" })).toContainText("200,00 €");
  await expect(panel.locator(".detail-row").filter({ hasText: "Unterwegs" })).toContainText("500,00 €");

  // The card: what is owed, not the 0 its net worth line nets to, and the payment on its way.
  await page.goto("/accounts?art=karte");
  await expect(page.locator(".kind-total")).toHaveText("300,00 €");
  await expect(page.locator(".kind-summary")).toContainText("offen");
  await expect(page.locator(".kind-summary")).toContainText("Einzahlung unterwegs 300,00 €");
  const cardRow = page.locator(".account-row").filter({ hasText: "Karte Beispiel" });
  await expect(cardRow.locator(".account-balance")).toContainText("300,00 €");
  await expect(cardRow.locator(".account-balance")).toContainText("offen · unterwegs 300,00 €");

  // The exchange: the state its own page shows, never „noch nicht abgestimmt“ from statements
  // it does not have.
  await page.goto("/accounts?art=boerse");
  const exchangeRow = page.locator(".account-row").filter({ hasText: "Börse Beispiel" });
  await expect(exchangeRow.locator(".account-balance")).toContainText("1.500,00 €");
  await expect(exchangeRow.locator(".account-balance .badge")).toHaveText("stimmt");
  await expect(exchangeRow).not.toContainText("noch nicht abgestimmt");
});
