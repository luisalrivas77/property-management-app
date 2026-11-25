// The recurring payments as the Budget's fixed payments inside Notwendiges and Wünsche (phase 10
// §7.10 (c), D300; mockup 19d frame 6, 19h frame 2), and „Einmalig im Zeitraum“ beside the Budget's
// split (§7.12 (c), D308). GET /household/recurring and its write are mocked, since a series needs
// months of bookings; Budget's reads of phases 9 and 10 (GET /health, /health/months,
// /household-plan, /household-budget) are mocked since they need a window of three closed months.
// Every other read is the real API of a fresh workspace. Invented payees and figures only.
//
// - A class panel lists its fixed payments with the server's monthly amount and price change,
//   „Schwankend“ and the sum line; a payment that may have ended asks, „Ja, beendet“ hides it and
//   „Wieder anzeigen“ brings it back, neither changing a figure. Haushalt › Monat has no card
//   „Regelmäßige Zahlungen“ any more (D300).
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
type BudgetFixedPayment = components["schemas"]["BudgetFixedPayment"];
type BudgetCode = components["schemas"]["BudgetCode"];
type OneOffCodes = components["schemas"]["OneOffCodes"];
type HouseholdScenarioCreate = components["schemas"]["HouseholdScenarioCreate"];

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

/** Wünsche's fixed payments as the server derives them from `recurringSeries()` (key = the
 * series' oldest payment); Notwendiges has its insurance only. */
function fixedOf(all: RecurringSeries[]) {
  const row = (label: string, monthly: string, extra: Partial<BudgetFixedPayment> = {}): BudgetFixedPayment => {
    const one = all.find((x) => x.label === label);
    if (!one) throw new Error(label);
    return {
      key: one.sources?.[0] ?? "",
      label,
      category: one.lines?.[0]?.category ?? "hh_streaming",
      interval: one.interval,
      state: one.state,
      monthly,
      mean: monthly,
      ...extra,
    };
  };
  return {
    need: [row("Versicherung Muster", "16.00")],
    want: [
      row("Streamingdienst Beispiel", "15.99", { change: "2.00", since: "2026-03" }),
      row("Fitnessstudio Muster", "39.90"),
      row("Zeitschrift Muster", "9.99"),
    ],
  };
}

function budget(withOneOff: boolean, fixed: ReturnType<typeof fixedOf> | null = null): BudgetView {
  const code = (category: AllocationCategory, budgetClass: "need" | "want", monthly: string): BudgetCode => ({
    category,
    group: "subscriptions",
    budget_class: budgetClass,
    default_class: budgetClass,
    overridden: false,
    monthly,
    total: (Number(monthly) * 3).toFixed(2),
  });
  return {
    as_of: "2026-11-24",
    scenario_id: null,
    window: { months: WINDOW, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    // need + want + uncategorized + saving = income; the one-offs beside them (D308).
    lines: [
      fixed
        ? {
            key: "need",
            monthly: "2440.00",
            total: "7320.00",
            share: "0.4692",
            codes: [code("hh_ins_liability_contents", "need", "16.00")],
            fixed: fixed.need,
            fixed_monthly: "16.00",
            variable: "2424.00",
          }
        : { key: "need", monthly: "2440.00", total: "7320.00", share: "0.4692", codes: [] },
      fixed
        ? {
            key: "want",
            monthly: "1300.00",
            total: "3900.00",
            share: "0.2500",
            codes: [code("hh_streaming", "want", "15.99"), code("hh_sport", "want", "39.90"), code("hh_news", "want", "9.99")],
            fixed: fixed.want,
            fixed_monthly: "65.88",
            variable: "1234.12",
          }
        : { key: "want", monthly: "1300.00", total: "3900.00", share: "0.2500", codes: [] },
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
async function budgetApi(page: Page, withOneOff: boolean, fixed: ReturnType<typeof fixedOf> | null = null) {
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
  await serve(page, "/api/household-budget", () => budget(withOneOff, fixed));
}

test("the one-offs stand beside the Budget's split as „Einmalig im Zeitraum“ and open their panel", async ({ page, signedIn }) => {
  void signedIn;
  await budgetApi(page, true);
  await page.goto("/household?tab=budget");

  const beside = page.getByRole("list", { name: "Einmalig im Zeitraum" });
  const oneOff = beside.locator('a.budget-line[data-key="one_off"]');
  await expect(oneOff).toContainText("160");
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

// --- „Was als einmalig zählt“ (§7.12 (a), D307; mockup 19h frame 3) --------------------------------

/** GET /household/one-off-codes and the scenario writes its switches make; each POST answers as
 * the server would, with the levers it was sent. */
async function oneOffCodesApi(page: Page) {
  const codes: OneOffCodes = {
    year: 2026,
    codes: [
      { category: "hh_electronics", default: true, one_off: true, year_total: "499.00" },
      { category: "hh_clothing", default: false, one_off: false, year_total: "0.00" },
    ],
  };
  await serve(page, "/api/household/one-off-codes", () => codes);
  const posted: HouseholdScenarioCreate[] = [];
  await page.route(
    (url) => url.pathname === "/api/household-scenarios",
    (route: Route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const body = route.request().postDataJSON() as HouseholdScenarioCreate;
      posted.push(body);
      return route.fulfill({
        status: 201,
        json: {
          ...body,
          id: `00000000-0000-4000-8000-0000000005${String(posted.length).padStart(2, "0")}`,
          workspace_id: "00000000-0000-4000-8000-000000000001",
          created_at: "2026-11-25T09:00:00Z",
        },
      });
    },
  );
  return { posted };
}

test("„Was als einmalig zählt“ lists each category with its year and stores a switch on the default", async ({ page, signedIn }) => {
  void signedIn;
  await budgetApi(page, false);
  const { posted } = await oneOffCodesApi(page);
  await page.goto("/household?tab=budget");
  await page.getByRole("link", { name: "Was als einmalig zählt ›" }).click();
  const sheet = detail(page, "Was als einmalig zählt");

  const electronics = sheet.getByRole("switch", { name: "Elektronik & Technik als einmalig zählen" });
  const clothing = sheet.getByRole("switch", { name: "Kleidung & Schuhe als einmalig zählen" });
  await expect(electronics).toBeChecked();
  await expect(clothing).not.toBeChecked();
  await expect(sheet).toContainText("2026: 499");
  await expect(sheet).toContainText("2026: nichts");

  // Clothing switched on: a new default row whose overrides hold it, and the default is named.
  await clothing.check();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.is_default).toBe(true);
  expect(posted[0]?.levers?.one_off_overrides).toEqual({ hh_clothing: true });
  await expect(sheet).toContainText("Standard: im Budget");
  // Back to its default: the override goes, on top of the row just stored.
  await clothing.uncheck();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]?.levers?.one_off_overrides).toEqual({});
  expect(posted[1]?.supersedes_id).toBe("00000000-0000-4000-8000-000000000501");
});

test("„Was als einmalig zählt“ at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  await budgetApi(page, false);
  await oneOffCodesApi(page);
  await phoneScreens(page, "one-off-codes", async () => {
    await page.goto("/household?tab=budget#d-goals-einmalig");
    const sheet = page.getByRole("dialog", { name: "Was als einmalig zählt" });
    await expect(sheet.getByRole("switch")).toHaveCount(2);
  });
});

// --- the fixed payments inside Notwendiges and Wünsche (§7.10 (c), D300) ---------------------------

test("Wünsche lists its fixed payments; one that may have ended is hidden and brought back", async ({ page, signedIn }) => {
  void signedIn;
  const all = recurringSeries();
  const { posted } = await recurringApi(page, all);
  await budgetApi(page, false, fixedOf(all));
  await page.goto("/household?tab=budget");
  await page.locator('a.budget-line[data-key="want"]').click();
  const panel = detail(page, "Wünsche");

  const fixed = panel.getByRole("region", { name: "Feste Zahlungen (erwartet)" });
  const rows = fixed.locator("li.recurring-fixed");
  await expect(rows).toHaveCount(3);
  const streaming = rows.filter({ hasText: "Streamingdienst Beispiel" });
  await expect(streaming).toContainText("monatlich");
  await expect(streaming).toContainText("+2,00 € seit März 2026");
  await expect(streaming).toContainText("15,99 €");
  // The server's sums, formatted only.
  await expect(panel.getByRole("region", { name: "Schwankend" })).toContainText("1.234,12 €");
  await expect(panel.getByTestId("fixed-sum")).toHaveText("65,88 € fest + 1.234,12 € schwankend = 1.300,00 €");

  // The magazine may have ended: it asks, „Läuft weiter“ only closes the question.
  const magazine = rows.filter({ hasText: "Zeitschrift Muster" });
  await expect(magazine).toContainText("zuletzt am 03.08.2026 abgebucht");
  await expect(magazine).toContainText("Vielleicht beendet?");
  await magazine.getByRole("button", { name: "Läuft weiter" }).click();
  await expect(magazine).not.toContainText("Vielleicht beendet?");
  expect(posted).toHaveLength(0);

  // On a fresh load it asks again; „Ja, beendet“ hides it at once and posts `hidden`.
  await page.reload();
  await expect(magazine).toContainText("Vielleicht beendet?");
  await magazine.getByRole("button", { name: "Ja, beendet" }).click();
  await expect(rows).toHaveCount(2);
  await expect(fixed).toContainText("Ausgeblendet: sie zählt ab jetzt zum schwankenden Teil.");
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual({ key: "zeitschrift-muster", interval: "monthly", answer: "hidden" });

  // „1 ausgeblendete anzeigen“ lists it with „Wieder anzeigen“, which posts `shown`.
  await panel.getByRole("button", { name: "1 ausgeblendete anzeigen" }).click();
  await panel.getByRole("button", { name: "Wieder anzeigen: Zeitschrift Muster" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual({ key: "zeitschrift-muster", interval: "monthly", answer: "shown" });

  // Notwendiges holds its own fixed payment only.
  await page.goto("/household?tab=budget");
  await page.locator('a.budget-line[data-key="need"]').click();
  const need = detail(page, "Notwendiges").getByRole("region", { name: "Feste Zahlungen (erwartet)" });
  await expect(need.locator("li.recurring-fixed")).toHaveCount(1);
  await expect(need).toContainText("Versicherung Muster");
  await expect(need).toContainText("vierteljährlich");
});

test("no fixed payments without the server's split, and no recurring card on Haushalt › Monat", async ({ page, signedIn }) => {
  void signedIn;
  await recurringApi(page);
  await budgetApi(page, false);
  await page.goto("/household?tab=budget");
  await page.locator('a.budget-line[data-key="want"]').click();
  const panel = detail(page, "Wünsche");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("region", { name: "Feste Zahlungen (erwartet)" })).toHaveCount(0);
  await expect(panel.getByTestId("fixed-sum")).toHaveCount(0);

  await page.goto("/household");
  await expect(page.getByRole("region", { name: "Finanzielle Gesundheit" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Regelmäßige Zahlungen" })).toHaveCount(0);
});

test("the fixed payments at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  const all = recurringSeries();
  await recurringApi(page, all);
  await budgetApi(page, false, fixedOf(all));
  await phoneScreens(page, "fixed-payments", async () => {
    await page.goto("/household?tab=budget#d-budget-want");
    const sheet = page.getByRole("dialog", { name: "Wünsche" });
    const fixed = sheet.getByRole("region", { name: "Feste Zahlungen (erwartet)" });
    await expect(fixed.locator("li.recurring-fixed")).toHaveCount(3);
    await fixed.scrollIntoViewIfNeeded();
  });
});
