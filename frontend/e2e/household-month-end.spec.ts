// Haushalt below three closed months (docs/phases/phase-10.md §7.6 (f)): what holds the next
// month open, its month-end to-do included, is listed once, in „Noch keine Durchschnitte“, and
// „Monatsabschluss festhalten“ there reads the reserve and the Kennzahlen below it again without
// a reload. GET /health, /health/months and the snapshot write are mocked; every other read is
// the real API of a fresh workspace. Synthetic figures only.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type Health = components["schemas"]["Health"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type HealthTodo = components["schemas"]["HealthTodo"];
type HouseholdMonth = components["schemas"]["HouseholdMonth"];
type SnapshotResult = components["schemas"]["SnapshotResult"];

/** YYYY-MM, `back` months before the current one. */
function monthsBack(back: number): string {
  const now = new Date();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return day.toISOString().slice(0, 7);
}

function householdMonth(month: string): HouseholdMonth {
  return {
    month,
    private_income: "5200.00",
    private_spending: "3900.00",
    one_off_spending: "0.00",
    regular_spending: "3900.00",
    rental_result: "0.00",
    rental_cash: "0.00",
    income: "5200.00",
    spending: "3900.00",
    saved: "1300.00",
    principal: "0.00",
    invested_property: "0.00",
    invested: "0.00",
    free_cash: "1300.00",
    savings_rate: "0.2500",
  };
}

const TOO_FEW: HealthTodo = {
  code: "too_few_months",
  text: "Erst 1 Monat ist abgeschlossen; die Durchschnitte brauchen 3.",
  action: "Zu prüfen öffnen",
  href: "/inbox",
};
const WAITING: HealthTodo = {
  code: "lines_waiting",
  text: "2 Umsätze aus dem Monat warten in „Zu prüfen“.",
  action: "Zu prüfen öffnen",
  href: "/inbox",
};
const MONTH_END: HealthTodo = {
  code: "month_end_missing",
  text: "Der Monatsabschluss fehlt.",
  action: "Monatsabschluss festhalten",
  href: "/assets?month_end=2026-10-31",
};

/** Before the month-end: one closed month, GET /health listing what holds the next month open
 * (as rules/health/window does); after it: three closed months and a savings rate. */
function health(captured: boolean): Health {
  const window = captured ? [monthsBack(4), monthsBack(3), monthsBack(2)] : [monthsBack(3)];
  return {
    as_of: "2026-11-18",
    window: { months: window, enough: captured },
    savings_rate: captured
      ? {
          saved: "3900.00",
          income: "15600.00",
          one_off_spending: "0.00",
          principal: "0.00",
          rate: "0.2500",
          rate_regular: "0.2500",
          rate_without_principal: "0.2500",
          months: window.map((month) => ({ month, rate: "0.2500" })),
        }
      : null,
    proposals: [{ figure: "savings_rate", direction: "higher", red: { value: "0", origin: "source" } }],
    todos: captured ? [] : [TOO_FEW, WAITING, MONTH_END],
  };
}

async function serve(page: Page, path: string, body: (url: URL) => unknown): Promise<void> {
  await page.route(
    (url) => url.pathname === path,
    (route: Route) =>
      route.request().method() === "GET" ? route.fulfill({ json: body(new URL(route.request().url())) }) : route.fallback(),
  );
}

test("below three closed months the month-end to-do shows once, and its button refreshes the Kennzahlen in place", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const open = monthsBack(2);
  const snapshots: unknown[] = [];
  await page.route(
    (url) => url.pathname === "/api/net-worth/snapshots",
    (route: Route) => {
      if (route.request().method() !== "POST") return route.fallback();
      snapshots.push(route.request().postDataJSON());
      const result: SnapshotResult = { outcome: "written", as_of: "2026-10-31", unvalued_positions: 0 };
      return route.fulfill({ status: 201, json: result });
    },
  );
  await serve(page, "/api/health", () => health(snapshots.length > 0));
  await serve(page, "/api/health/months", (url) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return [monthsBack(4), monthsBack(3), open]
      .filter((month) => month >= from && month <= to)
      .map(
        (month): HealthMonthView => ({
          month,
          household: householdMonth(month),
          closed:
            month === open && snapshots.length === 0
              ? { month, closed: false, todos: [WAITING, MONTH_END] }
              : { month, closed: true, todos: [] },
        }),
      );
  });

  await page.goto(`/household?monat=${open}`);
  const averages = page.getByRole("region", { name: "Noch keine Durchschnitte" });
  await expect(averages.getByRole("button", { name: "Monatsabschluss festhalten" })).toBeVisible();
  // Once on the page: the Kennzahlen below do not list GET /health's copy of the same to-dos.
  await expect(page.getByRole("button", { name: "Monatsabschluss festhalten" })).toHaveCount(1);
  await expect(page.getByText(WAITING.text)).toHaveCount(1);
  const rate = page.locator('div.health-figure[aria-label="Sparquote"]');
  await expect(rate).toHaveCount(0);
  await page.evaluate(() => {
    (window as unknown as { e2eKept?: boolean }).e2eKept = true;
  });

  await averages.getByRole("button", { name: "Monatsabschluss festhalten" }).click();
  await expect.poll(() => snapshots).toEqual([{ as_of: "2026-10-31" }]);
  // The Kennzahlen read GET /health again and show the three closed months, in the same page.
  await expect(rate).toContainText("25,0 %");
  await expect(page.getByRole("region", { name: "Noch keine Durchschnitte" })).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { e2eKept?: boolean }).e2eKept)).toBe(true);
});
