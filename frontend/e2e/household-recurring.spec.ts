// The recurring payments on Haushalt › Monat and the one-off check on Haushalt › Budget (phase 10
// §7.9 (b), (c); D296, D297; mockup 19c panels 4 to 7). GET /household/recurring and GET
// /household/one-off-candidates and their two writes are mocked, since a series needs months of
// bookings and a candidate a window of three closed months; Budget's reads of phase 9 (GET
// /health, /health/months, /household-plan, /household-budget) are mocked for the same reason.
// Every other read is the real API of a fresh workspace. Invented payees and figures only.
//
// - The card shows the server's count and yearly total, the price changes first, how many may
//   have ended and from when yearly payments can be found; a tapped payment opens its payments in
//   the detail panel; „Kein Abo, ausblenden“ hides it, „Wieder anzeigen“ brings it back, and
//   neither changes a figure.
// - The Budget's line names the payments to check; an answer is stored and the plan, the Budget
//   and the key figures are read again, so „Anlegbar“ and the Budget move at once; an answer can be
//   changed; a payment that left the window (404) reloads the list with a plain sentence.
// - Nothing is shown when nothing is listed, never an error card.
// - Phone width, light and dark: no sideways scroll; a screenshot of each new panel.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type RecurringPayments = components["schemas"]["RecurringPayments"];
type RecurringSeries = components["schemas"]["RecurringSeries"];
type GroupPayment = components["schemas"]["GroupPayment"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type OneOffCandidates = components["schemas"]["OneOffCandidates"];
type OneOffCandidate = components["schemas"]["OneOffCandidate"];
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
async function recurringApi(page: Page, initial: RecurringSeries[] = recurringSeries()) {
  const state = { shown: [...initial], hidden: [] as RecurringSeries[] };
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
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    // A fresh load each time, so the panel opens from its link as a person would land on it.
    await page.goto("about:blank");
    await open();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: test.info().outputPath(`${name}-${scheme}.png`) });
  }
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
  await recurringApi(page, all);
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
});

// --- (c) the one-off check on Budget ------------------------------------------------------------

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

function health(marked: boolean): Health {
  return {
    as_of: "2026-11-24",
    window: { months: WINDOW, enough: true },
    savings_rate: {
      saved: "3900.00",
      income: "15600.00",
      one_off_spending: marked ? "480.00" : "0.00",
      principal: "0.00",
      rate: "0.2500",
      rate_regular: marked ? "0.2808" : "0.2500",
      rate_without_principal: "0.2500",
      months: WINDOW.map((month) => ({ month, rate: "0.2500" })),
    },
    reserve: null,
    debt: null,
    marks: {},
    proposals: [],
    todos: [],
  };
}

function plan(marked: boolean): HouseholdPlan {
  const investable = marked ? "1314.00" : "1170.00";
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
        { key: "mean_regular_spending", amount: marked ? "-3740.00" : "-3900.00" },
        { key: "free_cash", amount: marked ? "1460.00" : "1300.00" },
        { key: "buffer", amount: marked ? "146.00" : "130.00" },
        { key: "investable", amount: investable },
      ],
    },
    months: [],
    paths: [],
    projection: { points: [], todos: [] },
    todos: [],
  };
}

function budget(marked: boolean): BudgetView {
  const need = marked ? "2440.00" : "2600.00";
  return {
    as_of: "2026-11-24",
    scenario_id: null,
    window: { months: WINDOW, enough: true },
    months: [],
    income: { monthly: "5200.00", total: "15600.00", rental_monthly: "0.00", private_monthly: "5200.00" },
    lines: [
      { key: "need", monthly: need, total: (Number(need) * 3).toFixed(2), share: marked ? "0.4692" : "0.5000", codes: [] },
      { key: "want", monthly: "1300.00", total: "3900.00", share: "0.2500", codes: [] },
      { key: "one_off", monthly: marked ? "160.00" : "0.00", total: marked ? "480.00" : "0.00", share: marked ? "0.0308" : "0.0000", codes: [] },
      { key: "uncategorized", monthly: "0.00", total: "0.00", share: "0.0000", codes: [] },
      { key: "saving", monthly: "1300.00", total: "3900.00", share: "0.2500" },
    ],
    housing: null,
    targets: {},
    references: [],
    todos: [],
  };
}

const DOCTOR = payment(`${WINDOW[1]}-14`, "480.00", "hh_doctor", "Praxis Muster");
const GARAGE = payment(`${WINDOW[2]}-12`, "870.00", "hh_car_service", "Kfz-Werkstatt Muster");
const GONE = payment(`${WINDOW[0]}-09`, "350.00", "hh_home", "Möbelhaus Muster");

/** Budget's mocked reads, the candidates and their writes; an answer marks the figures moved. */
async function oneOffApi(page: Page, initial: OneOffCandidate[]) {
  const state = { candidates: [...initial], answered: [] as OneOffCandidate[], marked: false };
  const posted: { allocation_id: string; answer: string }[] = [];
  await serve(page, "/api/health", () => health(state.marked));
  await serve(page, "/api/health/net-worth-series", () => []);
  await serve(page, "/api/health/months", (url) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return [...WINDOW, monthsBack(1), monthsBack(0)]
      .filter((month) => month >= from && month <= to)
      .map((month) => monthView(month, WINDOW.includes(month)));
  });
  await serve(page, "/api/household-plan", () => plan(state.marked));
  await serve(page, "/api/household-budget", () => budget(state.marked));
  await serve(
    page,
    "/api/household/one-off-candidates",
    (): OneOffCandidates => ({
      as_of: "2026-11-24",
      window: { months: WINDOW, enough: true },
      candidates: state.candidates,
      answered: state.answered,
    }),
  );
  await page.route(
    (url) => url.pathname === "/api/household/one-off-marks",
    (route: Route) => {
      const body = route.request().postDataJSON() as { allocation_id: string; answer: "one_off" | "regular" };
      posted.push(body);
      const all = [...state.candidates, ...state.answered];
      const found = all.find((c) => c.payment.allocation_id === body.allocation_id);
      if (!found || body.allocation_id === GONE.allocation_id) {
        // The payment left the window since the list was read.
        state.candidates = state.candidates.filter((c) => c.payment.allocation_id !== body.allocation_id);
        return route.fulfill({ status: 404, json: { error: "not_found", message: "Nicht gefunden." } });
      }
      state.candidates = state.candidates.filter((c) => c !== found);
      state.answered = [...state.answered.filter((c) => c.payment.allocation_id !== body.allocation_id), { ...found, answer: body.answer }];
      state.marked = state.answered.some((c) => c.answer === "one_off");
      return route.fulfill({ status: 201, json: { id: "00000000-0000-4000-8000-000000000c98", created_at: "2026-11-24T09:00:00Z" } });
    },
  );
  return { posted };
}

test("a payment answered „einmalig“ moves the Budget and „Anlegbar“ at once, and the answer can be changed", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  const { posted } = await oneOffApi(page, [
    { payment: DOCTOR, code_median: "40.00" },
    { payment: GARAGE, code_median: "60.00" },
  ]);
  await page.goto("/household?tab=budget");

  const investable = page.getByTestId("rest-investable");
  await expect(investable).toContainText("1.170");
  await expect(page.locator('a.budget-line[data-key="need"]')).toContainText("2.600");
  const line = page.getByRole("region", { name: "2 Zahlungen vielleicht einmalig" });
  await expect(line).toContainText("Praxis Muster 480,00 €");
  await expect(line).toContainText("Kfz-Werkstatt Muster 870,00 €");
  await line.getByRole("link").click();

  const panel = detail(page, "Vielleicht einmalig?");
  await expect(panel).toContainText("Diese Zahlungen sind viel größer als sonst in ihrer Kategorie.");
  await expect(panel).toContainText("sonst 60,00 € im Monat");
  const doctor = panel.getByRole("group", { name: "Einmalig? Praxis Muster" });
  await doctor.getByRole("button", { name: "Ja, einmalig" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual({ allocation_id: DOCTOR.allocation_id, answer: "one_off" });

  // The plan, the Budget and the key figures were read again.
  await expect(investable).toContainText("1.314");
  await expect(page.locator('a.budget-line[data-key="need"]')).toContainText("2.440");
  await expect(doctor.getByRole("button", { name: "Ja, einmalig" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: "1 Zahlung vielleicht einmalig" })).toBeVisible();

  // The answer can be changed.
  await doctor.getByRole("button", { name: "Nein, normal" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual({ allocation_id: DOCTOR.allocation_id, answer: "regular" });
  await expect(doctor.getByRole("button", { name: "Nein, normal" })).toHaveAttribute("aria-pressed", "true");
  await expect(investable).toContainText("1.170");
});

test("a payment that left the window reloads the list with a plain sentence", async ({ page, signedIn }) => {
  void signedIn;
  const { posted } = await oneOffApi(page, [
    { payment: GONE, code_median: "30.00" },
    { payment: GARAGE, code_median: "60.00" },
  ]);
  await page.goto("/household?tab=budget#d-einmalig-pruefen");
  const panel = detail(page, "Vielleicht einmalig?");
  await panel.getByRole("group", { name: "Einmalig? Möbelhaus Muster" }).getByRole("button", { name: "Ja, einmalig" }).click();
  await expect.poll(() => posted.length).toBe(1);
  await expect(panel).toContainText("Eine Zahlung steht nicht mehr zur Prüfung");
  await expect(panel).not.toContainText("Möbelhaus Muster");
  await expect(panel).toContainText("Kfz-Werkstatt Muster");
  // A 404 is no error card.
  await expect(panel.getByRole("alert")).toHaveCount(0);
});

test("no one-off line while nothing is to check", async ({ page, signedIn }) => {
  void signedIn;
  await oneOffApi(page, []);
  await page.goto("/household?tab=budget");
  await expect(page.getByTestId("rest-investable")).toBeVisible();
  await expect(page.getByText(/vielleicht einmalig/)).toHaveCount(0);
});

test("the one-off panel at phone width, light and dark", async ({ page, signedIn }) => {
  void signedIn;
  await oneOffApi(page, [
    { payment: DOCTOR, code_median: "40.00" },
    { payment: GARAGE, code_median: "60.00" },
  ]);
  await phoneScreens(page, "one-off", async () => {
    await page.goto("/household?tab=budget#d-einmalig-pruefen");
    const sheet = page.getByRole("dialog", { name: "Vielleicht einmalig?" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole("button", { name: "Ja, einmalig" })).toHaveCount(2);
  });
});
