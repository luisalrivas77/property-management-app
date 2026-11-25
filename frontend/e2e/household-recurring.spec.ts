// The recurring payments on Haushalt › Monat (phase 10 §7.9 (b); D296; mockup 19c panels 4 and 5)
// and „Einmalig im Zeitraum“ beside the Budget's split (§7.12 (c), D308). GET /household/recurring
// and its write are mocked, since a series needs months of bookings; Budget's reads of phases 9
// and 10 (GET /health, /health/months, /household-plan, /household-budget) are mocked since they
// need a window of three closed months.
// Every other read is the real API of a fresh workspace. Invented payees and figures only.
//
// - The card shows the server's count and yearly total, the price changes first, how many may
//   have ended and from when yearly payments can be found; a tapped payment opens its payments in
//   the detail panel; „Kein Abo, ausblenden“ hides it, „Wieder anzeigen“ brings it back, and
//   neither changes a figure.
// - The Budget shows the window's one-offs beside its split, not in it, and opens their panel.
// - Nothing is shown when nothing is listed, never an error card.
// - Phone width, light and dark: no sideways scroll; a screenshot of each new panel.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type RecurringPayments = components["schemas"]["RecurringPayments"];
type RecurringSeries = components["schemas"]["RecurringSeries"];
type GroupPayment = components["schemas"]["GroupPayment"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type Health = components["schemas"]["Health"];
type HealthMonthView = components["schemas"]["HealthMonthView"];
type HouseholdMonth = components["schemas"]["HouseholdMonth"];
type HouseholdPlan = components["schemas"]["HouseholdPlan"];
type BudgetView = components["schemas"]["BudgetView"];

// The KI panel beside the plan reads its explanation: served as the app's template with no text,
// so these tests never read a real explanation of a mocked plan.
test.beforeEach(async ({ page }) => {
  await serve(page, "/api/household-plan/explanation", () => ({
    id: null,
    scenario_id: null,
    mode: "own_use",
    status: "template",
    reason: null,
    paragraphs: [],
    created_at: null,
    stale: false,
  }));
});

const ACCOUNT = "00000000-0000-4000-8000-000000000c01";

/** Serve JSON for GET requests to exactly this API path; other methods go on to the real API. */
async function serve(page: Page, path: string, body: (url: URL) => unknown): Promise<void> {
  await page.route(
    (url) => url.pathname === path,
    (route: Route) =>
      route.request().method() === "GET" ? route.fulfill({ json: body(new URL(route.request().url())) }) : route.fallback(),
  );
}

/** A detail panel: the column beside the list on the computer, the sheet on the phone. */
function detail(page: Page, name: string) {
  return page.getByRole("complementary", { name }).or(page.getByRole("dialog", { name }));
}

let paymentSeq = 0;

function payment(day: string, amount: string, category: AllocationCategory, payee: string): GroupPayment {
  paymentSeq += 1;
  const n = String(paymentSeq).padStart(4, "0");
  return {
    allocation_id: `00000000-0000-4000-8000-00000000${n}`,
    transaction_id: `00000000-0000-4000-8000-00000001${n}`,
    account_id: ACCOUNT,
    account_label: "Girokonto Beispiel",
    booked_on: day,
    payee,
    purpose: "",
    category,
    amount,
  };
}

function series(
  label: string,
  interval: RecurringSeries["interval"],
  category: AllocationCategory,
  days: { day: string; amount: string }[],
  extra: Partial<RecurringSeries> = {},
): RecurringSeries {
  const lines = days.map((d) => payment(d.day, d.amount, category, label));
  const last = days[days.length - 1] ?? { day: "2026-01-01", amount: "0.00" };
  return {
    key: label.toLowerCase().replace(/\s+/g, "-"),
    label,
    interval,
    state: "active",
    last_amount: last.amount,
    last_on: last.day,
    yearly_total: "0.00",
    payments: lines.length,
    sources: lines.map((l) => l.allocation_id),
    lines,
    ...extra,
  };
}

function monthly(from: number, to: number, amount: (month: number) => string): { day: string; amount: string }[] {
  return Array.from({ length: to - from + 1 }, (_, index) => {
    const m = from + index;
    return { day: `2026-${String(m).padStart(2, "0")}-03`, amount: amount(m) };
  });
}

/** Four series: a price rise, a plain monthly one, a quarterly one and one that may have ended. */
function recurringSeries(): RecurringSeries[] {
  return [
    series("Streamingdienst Beispiel", "monthly", "hh_streaming", monthly(1, 10, (m) => (m >= 3 ? "15.99" : "13.99")), {
      change: "2.00",
      since: "2026-03",
      yearly_total: "191.88",
    }),
    series("Fitnessstudio Muster", "monthly", "hh_sport", monthly(1, 10, () => "39.90"), { yearly_total: "478.80" }),
    series(
      "Versicherung Muster",
      "quarterly",
      "hh_ins_liability_contents",
      [
        { day: "2026-01-15", amount: "48.00" },
        { day: "2026-04-15", amount: "48.00" },
        { day: "2026-07-15", amount: "48.00" },
        { day: "2026-10-15", amount: "48.00" },
      ],
      { yearly_total: "192.00" },
    ),
    series("Zeitschrift Muster", "monthly", "hh_news", monthly(1, 8, () => "9.99"), { state: "maybe_ended", yearly_total: "0.00" }),
  ];
}

/** GET /household/recurring from the state the marks change, and the marks' writes. */
async function recurringApi(page: Page, initial: RecurringSeries[] = recurringSeries(), hiddenAtStart: RecurringSeries[] = []) {
  const state = { shown: initial.filter((s) => !hiddenAtStart.includes(s)), hidden: [...hiddenAtStart] };
  const posted: { key: string; interval: string; answer: string }[] = [];
  const view = (): RecurringPayments => ({
    as_of: "2026-11-24",
    history_start: "2026-01-02",
    yearly_detectable: false,
    count: state.shown.length,
    // The server's sum; the browser adds nothing up.
    yearly_total: state.shown.length === initial.length ? "862.68" : "670.80",
    series: state.shown,
    hidden: state.hidden,
  });
  await page.route(
    (url) => url.pathname === "/api/household/recurring",
    (route: Route) => route.fulfill({ json: view() }),
  );
  await page.route(
    (url) => url.pathname === "/api/household/recurring-marks",
    (route: Route) => {
      const body = route.request().postDataJSON() as { key: string; interval: string; answer: string };
      posted.push(body);
      const from = body.answer === "hidden" ? state.shown : state.hidden;
      const found = from.find((s) => s.key === body.key && s.interval === body.interval);
      if (found) {
        if (body.answer === "hidden") {
          state.shown = state.shown.filter((s) => s !== found);
          state.hidden = [...state.hidden, found];
        } else {
          state.hidden = state.hidden.filter((s) => s !== found);
          state.shown = [...initial.filter((s) => s === found || state.shown.includes(s))];
        }
      }
      return route.fulfill({ status: 201, json: { id: "00000000-0000-4000-8000-000000000c99", created_at: "2026-11-24T09:00:00Z" } });
    },
  );
  return { posted };
}

async function phoneScreens(page: Page, name: string, open: () => Promise<void>) {
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    // A fresh load each time, so the panel opens from its link as a person would land on it.
    await page.goto("about:blank");
    await open();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    // No theme is stored, so the page follows the emulated scheme: the page's own background.
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await page.screenshot({ path: test.info().outputPath(`${name}-${scheme}.png`) });
  }
  // The dark screenshot is dark, not a second light one.
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
}

test("the recurring card lists the price changes first, opens a payment's payments, hides it and brings it back", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted } = await recurringApi(page);
  await page.goto("/household");

  const card = page.getByRole("region", { name: "Regelmäßige Zahlungen" });
  await expect(card).toContainText("4 Zahlungen");
  await expect(card).toContainText("862,68 € im Jahr");
  await expect(card).toContainText("+2,00 € seit März 2026");
  await expect(card).toContainText("1 vielleicht beendet");
  await expect(card).toContainText("ab Januar 2027");
  // Only the price change is on the card itself.
  await expect(card).not.toContainText("Fitnessstudio Muster");

  await card.getByRole("link", { name: "Alle regelmäßigen Zahlungen" }).click();
  const list = detail(page, "Regelmäßige Zahlungen");
  await expect(list).toBeVisible();
  const headings = await list.getByRole("heading", { level: 3 }).allTextContents();
  expect(headings).toEqual(["Preis geändert", "Monatlich", "Vierteljährlich", "Vielleicht beendet"]);
  await expect(list).toContainText("monatlich · jetzt 15,99 €");
  await expect(list).toContainText("Jährliche Zahlungen erkennt Hausbuch erst, wenn sie zweimal da waren: ab Januar 2027.");

  await list.getByRole("link", { name: /Fitnessstudio Muster/ }).click();
  const one = detail(page, "Fitnessstudio Muster");
  await expect(one).toBeVisible();
  await expect(one).toContainText("monatlich · seit Januar 2026 · jetzt 39,90 €");
  await expect(one).toContainText("03.10.2026");
  await expect(one).toContainText("Kategorie:");
  await expect(page).toHaveURL(/#d-abo-/);
  // The payee's words never reach the URL; the series is named by an opaque id.
  expect(page.url()).not.toContain("Fitness");

  await one.getByRole("button", { name: "Kein Abo, ausblenden" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual({ key: "fitnessstudio-muster", interval: "monthly", answer: "hidden" });
  // Back on the list, without it; the card says three.
  await expect(list).toBeVisible();
  await expect(list).not.toContainText("Fitnessstudio Muster");
  await expect(card).toContainText("3 Zahlungen");

  await list.getByRole("link", { name: "1 ausgeblendete anzeigen" }).click();
  const hidden = detail(page, "Ausgeblendete Zahlungen");
  await expect(hidden).toContainText("Fitnessstudio Muster");
  await hidden.getByRole("button", { name: "Wieder anzeigen: Fitnessstudio Muster" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual({ key: "fitnessstudio-muster", interval: "monthly", answer: "shown" });
  await expect(hidden).toContainText("Keine Zahlung ist ausgeblendet.");
  await expect(card).toContainText("4 Zahlungen");
});

test("no recurring card while nothing is listed", async ({ page, signedIn }) => {
  void signedIn;
  await recurringApi(page, []);
  const read = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/household/recurring");
  await page.goto("/household");
  await read;
  await expect(page.getByRole("group", { name: "Einnahmen" }).first()).toBeVisible();
  await expect(page.getByRole("region", { name: "Regelmäßige Zahlungen" })).toHaveCount(0);
});

test("the recurring panels at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  const all = recurringSeries();
  const hidden = all.find((s) => s.label === "Fitnessstudio Muster");
  await recurringApi(page, all, hidden ? [hidden] : []);
  const first = all[0]?.sources?.[0] ?? "";
  await phoneScreens(page, "recurring-list", async () => {
    await page.goto("/household#d-abos-alle");
    await expect(page.getByRole("dialog", { name: "Regelmäßige Zahlungen" })).toBeVisible();
  });
  await phoneScreens(page, "recurring-series", async () => {
    await page.goto(`/household#d-abo-${first}`);
    const sheet = page.getByRole("dialog", { name: "Streamingdienst Beispiel" });
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText("2,00 € mehr seit März 2026.");
  });
  await phoneScreens(page, "recurring-hidden", async () => {
    await page.goto("/household#d-abos-ausgeblendet");
    const sheet = page.getByRole("dialog", { name: "Ausgeblendete Zahlungen" });
    await expect(sheet.getByRole("button", { name: "Wieder anzeigen: Fitnessstudio Muster" })).toBeVisible();
  });
});

// --- „Einmalig im Zeitraum“ beside the Budget's split (§7.12 (c), D308) ---------------------------
// The one-off check of §7.9 (c) is gone (D307): a category is one-off by default or by its switch.
// The Budget shows the window's one-offs beside need + want + saving = income, never in it.

/** YYYY-MM, `back` months before the current one. */
function monthsBack(back: number): string {
  const now = new Date();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return day.toISOString().slice(0, 7);
}

const WINDOW = [monthsBack(4), monthsBack(3), monthsBack(2)];

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

function monthView(month: string, closed: boolean): HealthMonthView {
  return { month, household: householdMonth(month), closed: { month, closed, todos: [] } };
}

function health(): Health {
  return {
    as_of: "2026-11-24",
    window: { months: WINDOW, enough: true },
    savings_rate: {
      saved: "4380.00",
      income: "15600.00",
      one_off_spending: "480.00",
      principal: "0.00",
      rate: "0.2808",
      rate_regular: "0.2808",
      rate_without_principal: "0.2808",
      months: WINDOW.map((month) => ({ month, rate: "0.2808" })),
      one_offs_left_out: true,
    },
    reserve: null,
    debt: null,
    marks: {},
    proposals: [],
    todos: [],
  };
}

function plan(): HouseholdPlan {
  return {
    as_of: "2026-11-24",
    scenario_id: null,
    levers: {
      reserve_target_months: "6",
      buffer_percent: "10",
      horizon_months: 12,
      net_worth_years: 10,
      value_change: { depot: null, crypto: null },
      spending_changes: [],
    },
    newest_closed: WINDOW[WINDOW.length - 1] ?? null,
    surplus: {
      rows: [
        { key: "mean_income", amount: "5200.00" },
        { key: "mean_regular_spending", amount: "-3740.00" },
        { key: "free_cash", amount: "1460.00" },
        { key: "buffer", amount: "146.00" },
        { key: "investable", amount: "1314.00" },
      ],
    },
    months: [],
    paths: [],
    projection: { points: [], todos: [] },
    todos: [],
  };
}

function budget(withOneOff: boolean): BudgetView {
  return {
    as_of: "2026-11-24",
    scenario_id: null,
    window: { months: WINDOW, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    // need + want + uncategorized + saving = income; the one-offs beside them (D308).
    lines: [
      { key: "need", monthly: "2440.00", total: "7320.00", share: "0.4692", codes: [] },
      { key: "want", monthly: "1300.00", total: "3900.00", share: "0.2500", codes: [] },
      { key: "uncategorized", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
      { key: "saving", monthly: "1460.00", total: "4380.00", share: "0.2808" },
    ],
    one_off: withOneOff ? { key: "one_off", monthly: "160.00", total: "480.00", share: "0.0308", codes: [] } : null,
    housing: null,
    targets: {},
    references: [],
    todos: [],
  };
}

/** Budget's mocked reads of phases 9 and 10. */
async function budgetApi(page: Page, withOneOff: boolean) {
  await serve(page, "/api/health", () => health());
  await serve(page, "/api/health/net-worth-series", () => []);
  await serve(page, "/api/health/months", (url) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return [...WINDOW, monthsBack(1), monthsBack(0)]
      .filter((month) => month >= from && month <= to)
      .map((month) => monthView(month, WINDOW.includes(month)));
  });
  await serve(page, "/api/household-plan", () => plan());
  await serve(page, "/api/household-budget", () => budget(withOneOff));
}

test("the one-offs stand beside the Budget's split as „Einmalig im Zeitraum“ and open their panel", async ({ page, signedIn }) => {
  void signedIn;
  await budgetApi(page, true);
  await page.goto("/household?tab=budget");

  const beside = page.getByRole("list", { name: "Einmalig im Zeitraum" });
  const oneOff = beside.locator('a.budget-line[data-key="one_off"]');
  await expect(oneOff).toContainText("160,00");
  await expect(oneOff).toContainText("neben der Aufteilung, nicht im Schnitt");
  // Once, beside the split, not among its lines.
  await expect(page.locator('a.budget-line[data-key="one_off"]')).toHaveCount(1);
  await oneOff.click();
  const panel = detail(page, "Einmalig im Zeitraum");
  await expect(panel).toContainText("stehen neben der Aufteilung");
});

test("no one-off line while the window holds none", async ({ page, signedIn }) => {
  void signedIn;
  await budgetApi(page, false);
  await page.goto("/household?tab=budget");
  await expect(page.locator('a.budget-line[data-key="need"]')).toBeVisible();
  await expect(page.locator('a.budget-line[data-key="one_off"]')).toHaveCount(0);
});

test("the one-off panel at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  await budgetApi(page, true);
  await phoneScreens(page, "one-off", async () => {
    await page.goto("/household?tab=budget#d-budget-one_off");
    const sheet = page.getByRole("dialog", { name: "Einmalig im Zeitraum" });
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText("stehen neben der Aufteilung");
  });
});
