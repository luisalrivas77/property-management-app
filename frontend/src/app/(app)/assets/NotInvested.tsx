"use client";

// „Noch nicht angelegt“ on Anlagen › Übersicht (phase 10 §7.11 (a) 6, (i) item 11): once the
// household has saved a split (Haushalt › Ziel, „Aufteilung ändern“), one card after „Für euer
// Ziel“ with one row per kind whose purchases over the closed months fell short of its share,
// from GET /household-plan's `not_invested`; the server sends it only while the amount is above 0
// and leaves out the kinds in `generic` mode (D251), where the card names the amount only. A
// fact, never advice: no button, no colour, no reminder; how the amount is computed sits in the
// side panel („Cards, not text“). Nothing is summed here. Its own read, outside the tab's, so a
// slow plan never holds the rest of Anlagen.
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { DetailPanel, useDetail } from "@/components/DetailPanel";
import { GOAL_BASIS_HREF, Stat, Stats } from "@/components/householdGoal";
import { usePlanFormat } from "@/components/planFormat";
import { useLoad } from "@/components/useLoad";
import { type Translation, useT } from "@/i18n";
import { assets } from "@/i18n/assets";

import { TRACKING_DETAIL } from "./GoalTracking";

export type NotInvested = components["schemas"]["NotInvested"];
type T = Translation<(typeof assets)["de"]>;

/** The card's side panel, beside „Für euer Ziel“'s (`#d-fuerziel-nochnicht`). */
export const NOT_INVESTED_ID = "nochnicht";

/** The household plan's `not_invested`, or null (none, or the plan could not be read). */
export function useNotInvested(): NotInvested | null {
  const load = useCallback(() => ok(api.GET("/api/household-plan")).catch(() => null), []);
  const state = useLoad(load);
  return state.data?.not_invested ?? null;
}

function rows(t: T, p: ReturnType<typeof usePlanFormat>, value: NotInvested): string[] {
  const lines = value.lines ?? [];
  if (lines.length === 0) return [t.notInvestedGeneric(p.euro(value.amount))];
  return lines.map((line) =>
    t.notInvestedLine(
      line.asset_class === "crypto" ? t.notInvestedCrypto : t.notInvestedFunds,
      p.percentValue(line.actual_percent),
      p.percentValue(line.target_percent),
      p.euro(line.amount),
    ),
  );
}

export function NotInvestedCard({ value }: { value: NotInvested }) {
  const t = useT(assets);
  const p = usePlanFormat();
  const detail = useDetail(TRACKING_DETAIL);
  return (
    <a {...detail.linkProps(NOT_INVESTED_ID)} className="planning-kpi asset-kind-card" data-testid="not-invested" aria-label={t.openTab(t.notInvestedTitle)}>
      <p className="planning-kpi-label">{t.notInvestedTitle} ›</p>
      {rows(t, p, value).map((row) => (
        <p key={row} className="planning-kpi-sub">
          {row}
        </p>
      ))}
    </a>
  );
}

export function NotInvestedPanel({ value, backLabel }: { value: NotInvested; backLabel: string }) {
  const t = useT(assets);
  const p = usePlanFormat();
  const detail = useDetail(TRACKING_DETAIL);
  return (
    <DetailPanel title={t.notInvestedTitle} backLabel={backLabel} onClose={detail.close}>
      <div className="stack goal-panel" data-testid="not-invested-panel">
        {rows(t, p, value).map((row) => (
          <p key={row}>{row}</p>
        ))}
        <div className="goal-c">
          <Stats count={2}>
            <Stat value={p.euro(value.invested)} label={t.notInvestedInvested} />
            <Stat value={p.euro(value.amount)} label={t.notInvestedAmount} />
          </Stats>
          <p className="goal-note">{t.notInvestedWindow(p.month(value.from_month), p.month(value.to_month))}</p>
        </div>
        <p className="goal-note">{t.notInvestedHow}</p>
        <p className="goal-note">{t.notInvestedReserve}</p>
        <p>
          <Link href={GOAL_BASIS_HREF} className="button-link secondary">
            {t.notInvestedChange} ›
          </Link>
        </p>
        <p className="goal-note">{t.goalFootnote}</p>
      </div>
    </DetailPanel>
  );
}
