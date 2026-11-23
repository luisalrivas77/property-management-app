// Finanzielle Gesundheit (docs/phases/phase-10.md §7.6, Luis 23 Nov 2026: „something that simply
// says financial health -> excellent, good, or bad … Then in a side panel the value can be
// explained“): Übersicht's tile shows one word with its dot and the weakest point; its panel holds
// the three questions as cards and „Grenzen ändern“, which opens the sheet on Haushalt. GET
// /health is mocked with a verdict as rules/verdict states it; every other read is the real API
// of a fresh workspace. Synthetic figures only.
import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/types";
import { expect, test } from "./support";

type Health = components["schemas"]["Health"];
type Verdict = components["schemas"]["Verdict"];

const TEXTS = { why: "Warum", reference: "Bezugspunkt" };

const VERDICT: Verdict = {
  word: "good",
  weakest: "reserve",
  line: "Am knappsten: die Reserve reicht für 3,1 von 6 Monaten.",
  parts: [
    {
      figure: "savings_rate",
      state: "good",
      value: "0.2500",
      good: "0.10",
      strained: "0",
      good_origin: "default",
      strained_origin: "default",
      origin: "default",
      no_loan: false,
      question: "Gebt ihr weniger aus, als ihr verdient?",
      headline: "Ja, ein Viertel bleibt übrig.",
      ...TEXTS,
    },
    {
      figure: "reserve",
      state: "ok",
      amount: "12000.00",
      months: "3.08",
      target: "23400.00",
      target_months: "6",
      strained: "0.5",
      strained_amount: "11700.00",
      strained_origin: "default",
      origin: "default",
      no_loan: false,
      question: "Könnt ihr eine Überraschung auffangen?",
      headline: "Zum Teil: die Reserve liegt unter eurem Ziel.",
      ...TEXTS,
    },
    {
      figure: "debt_service_ratio",
      state: "good",
      no_loan: true,
      question: "Tragt ihr euren Kredit bequem?",
      headline: "Ihr habt keinen Kredit.",
      ...TEXTS,
    },
  ],
  todos: [],
};

/** YYYY-MM, `back` months before the current one. */
function monthsBack(back: number): string {
  const now = new Date();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return day.toISOString().slice(0, 7);
}

function health(): Health {
  return {
    as_of: "2026-11-18",
    window: { months: [monthsBack(4), monthsBack(3), monthsBack(2)], enough: true },
    todos: [],
    verdict: VERDICT,
  };
}

async function serve(page: Page, path: string, body: () => unknown): Promise<void> {
  await page.route(
    (url) => url.pathname === path,
    (route: Route) => (route.request().method() === "GET" ? route.fulfill({ json: body() }) : route.fallback()),
  );
}

test("Übersicht shows one word with its dot, the panel explains it with three questions and leads to the limits", async ({
  page,
  signedIn,
}) => {
  void signedIn;
  await serve(page, "/api/health", health);
  await serve(page, "/api/health/net-worth-series", () => []);

  await page.goto("/");
  const tiles = page.getByRole("region", { name: "Auf einen Blick" });
  const tile = tiles.getByRole("link", { name: /Finanzielle Gesundheit/ });
  await expect(tile.locator(".tile-dot")).toHaveAttribute("data-mark", "amber");
  await expect(tile).toContainText("Gut");
  await expect(tile).toContainText(VERDICT.line);
  // The key figures' own tiles are gone; the one word stands for them.
  for (const gone of [/^Sparquote/, /^Liquiditätsreserve/, /^Verschuldung/]) {
    await expect(tiles.getByRole("link", { name: gone })).toHaveCount(0);
  }

  await tile.click();
  await expect(page).toHaveURL(/#d-kachel-gesundheit$/);
  const panel = page
    .getByRole("dialog", { name: "Finanzielle Gesundheit" })
    .or(page.getByRole("complementary", { name: "Finanzielle Gesundheit" }));
  for (const [label, question] of <const [string, string][]>[
    ["Sparquote", "Gebt ihr weniger aus, als ihr verdient?"],
    ["Liquiditätsreserve", "Könnt ihr eine Überraschung auffangen?"],
    ["Schuldendienstquote", "Tragt ihr euren Kredit bequem?"],
  ]) {
    await expect(panel.getByRole("group", { name: label })).toContainText(question);
  }
  const rate = panel.getByRole("group", { name: "Sparquote" });
  await expect(rate).toHaveAttribute("data-state", "good");
  await expect(rate).toContainText("25,0 %");
  await expect(rate).toContainText("Standard-Grenze");
  // No loan: no figure and no limits, the question answered by its headline.
  const loans = panel.getByRole("group", { name: "Schuldendienstquote" });
  await expect(loans).toContainText("Ihr habt keinen Kredit.");
  await expect(loans.locator(".verdict-limits")).toHaveCount(0);

  const change = panel.getByRole("link", { name: "Grenzen ändern" });
  await expect(change).toHaveAttribute("href", "/household#ampel-grenzen");
  await change.click();
  await expect(page).toHaveURL(/\/household#ampel-grenzen$/);
  // Haushalt opens the sheet for the three figures at once.
  const bands = page.getByRole("region", { name: "Ampel-Grenzen festlegen" });
  await expect(bands).toBeVisible();
  await expect(bands.getByRole("group", { name: "Sparquote" })).toBeVisible();
  await expect(bands.getByRole("group", { name: "Beleihungsauslauf" })).toHaveCount(0);
  // Closing it drops the hash, so a reload does not open it again.
  await bands.getByRole("button", { name: "Abbrechen" }).click();
  await expect(bands).toHaveCount(0);
  await expect(page).toHaveURL(/\/household$/);
  await page.reload();
  await expect(page.getByRole("region", { name: "Kennzahlen" })).toBeVisible();
  await expect(bands).toHaveCount(0);
});
