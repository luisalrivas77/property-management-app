"use client";

// „Wertzuwachs fürs Ziel“ on Anlagen › Immobilie (phase 10 §7.9 (e) items 1, 2; D289, D298): for a
// property the goal's walk sells at or after its due month, one card with the yearly value growth
// that property alone would need for the goal to be reached at today's saving (funds and crypto
// at their typed rates) and one line, how often German house prices rose that much over windows
// of the goal's length. The windows, the source and what the comparison leaves out sit in its
// side panel („Cards, not text“, 25 Nov 2026). Every figure is GET /household-goal/properties's;
// nothing is computed here. No card in `generic` mode, without a net-worth goal, for a property
// without a default scenario or sold before the due month (the server lists none).
import type { components } from "@/api/types";
import { api, ok } from "@/api/client";
import { DetailPanel, useDetail } from "@/components/DetailPanel";
import { Stat, Stats } from "@/components/householdGoal";
import { usePlanFormat } from "@/components/planFormat";
import { useT } from "@/i18n";
import { assets } from "@/i18n/assets";

export type GoalProperties = components["schemas"]["GoalProperties"];
export type GoalPropertyGrowth = components["schemas"]["GoalPropertyGrowth"];

/** The card's side panel, its state in the URL hash like every panel (D267); the id is the
 * property's. */
export const GROWTH_DETAIL = "wertzuwachs";

/** The property cards' read; Anlagen › Immobilie shows without it. */
export const loadGrowth = (): Promise<GoalProperties | null> => ok(api.GET("/api/household-goal/properties")).catch(() => null);

export function growthOf(growth: GoalProperties | null, propertyId: string): GoalPropertyGrowth | null {
  if (!growth?.shown) return null;
  return (growth.properties ?? []).find((card) => card.property_id === propertyId) ?? null;
}

export function PropertyGrowthCard({ card, label }: { card: GoalPropertyGrowth; label: string }) {
  const t = useT(assets);
  const p = usePlanFormat();
  const detail = useDetail(GROWTH_DETAIL);
  const line = card.available && card.verdict ? t.growthLine(card.verdict) : t.growthShort(card.reason ?? "");
  return (
    <a {...detail.linkProps(card.property_id)} className="planning-kpi asset-kind-card" data-testid="property-growth" aria-label={t.growthAria(label)}>
      <p className="planning-kpi-label">{t.growthTitle} ›</p>
      <p className="planning-kpi-value">{card.needed_growth != null ? t.growthValue(p.ratio(card.needed_growth)) : t.growthNone}</p>
      <p className="planning-kpi-sub">{line}</p>
    </a>
  );
}

export function PropertyGrowthPanel({ card, label, backLabel }: { card: GoalPropertyGrowth; label: string; backLabel: string }) {
  const t = useT(assets);
  const p = usePlanFormat();
  const detail = useDetail(GROWTH_DETAIL);
  const counted = card.windows > 0;
  const source = card.source;
  return (
    <DetailPanel title={t.growthTitle} backLabel={backLabel} onClose={detail.close}>
      <div className="stack goal-panel" data-testid="property-growth-panel">
        {card.needed_growth != null ? <p>{t.growthText(label, p.ratio(card.needed_growth), p.ratio(card.typed_growth))}</p> : null}
        {card.available && card.verdict ? <p>{t.growthVerdict(card.verdict)}</p> : null}
        {!card.available && card.reason ? <p>{t.growthReason(card.reason)}</p> : null}
        <div className="goal-c">
          <Stats count={2}>
            <Stat value={card.needed_growth != null ? p.ratio(card.needed_growth) : "–"} label={t.growthNeeded} />
            <Stat value={p.ratio(card.typed_growth)} label={t.growthTyped} />
          </Stats>
          <p className="goal-note">{t.growthSale(card.sale_year)}</p>
        </div>
        {counted ? (
          <div className="goal-c">
            {card.reached_windows != null ? (
              <Stats count={1}>
                <Stat value={t.growthWindows(card.reached_windows, card.windows)} label={t.growthWindowsLabel(card.window_years)} />
              </Stats>
            ) : null}
            <Stats count={2}>
              <Stat value={p.ratio(card.median)} label={t.growthMedian} />
              <Stat value={p.ratio(card.best)} label={t.growthBest} />
            </Stats>
          </div>
        ) : null}
        {card.thin ? <p className="goal-note">{t.growthThin}</p> : null}
        <p className="goal-note">{t.growthPriceOnly}</p>
        {source ? (
          <div className="goal-c">
            <p className="planning-kpi-label">{t.growthSource}</p>
            <ul className="goal-sources">
              <li>
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.source}
                </a>{" "}
                <span className="muted">
                  {source.first_year}–{source.last_year}
                </span>
                <span className="goal-note">
                  {source.licence} · {source.attribution} · {source.own_calculation}
                </span>
              </li>
            </ul>
          </div>
        ) : null}
        <p className="goal-note">{t.growthFootnote}</p>
      </div>
    </DetailPanel>
  );
}
