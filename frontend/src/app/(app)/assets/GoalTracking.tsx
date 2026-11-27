"use client";

// „Für euer Ziel“ on Anlagen › Übersicht (phase 10 §7.9 (e) 3, D298 (5)–(7)): once the household
// follows a return on Haushalt › Ziel („Diesem Weg folgen“ in the side panel of „Mehr Rendite“ or
// „Ausgewogener Weg“), one card with one word by code, „Auf dem Pfad“ or „Dahinter“, and one line;
// the figures and what they mean sit in its side panel („Cards, not text“, 25 Nov 2026). Every
// figure is GET /household-goal/tracking's; nothing is summed here. Not shown in `generic` mode,
// without a goal or without a return followed (the server says `shown: false`).
//
// Following the adopted path (§7.11 (i), D311, mockup 19j frame 8), the card draws the
// investments' growth against the growth needed per closed month-end (`points`), and the panel
// the same chart with its axis and legend; the points are the server's.
import Link from "next/link";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { DetailPanel, useDetail } from "@/components/DetailPanel";
import { PathChartLast, PathGrowthChart } from "@/components/goalPathCards";
import { goalRouteHref, Stat, Stats, WordPill } from "@/components/householdGoal";
import { useFormat, useT, type Translation } from "@/i18n";
import { assets } from "@/i18n/assets";

export type GoalTracking = components["schemas"]["GoalTracking"];
type T = Translation<(typeof assets)["de"]>;

/** The card's side panel, its state in the URL hash like every panel (D267). */
export const TRACKING_DETAIL = "fuerziel";
export const TRACKING_ID = "pfad";

/** The tracking read; Anlagen shows without it. */
export const loadTracking = (): Promise<GoalTracking | null> =>
  ok(api.GET("/api/household-goal/tracking")).catch(() => null);

function wordText(t: T, tracking: GoalTracking): string {
  if (tracking.word === "on_path") return t.goalOnPath;
  if (tracking.word === "behind") return t.goalBehind;
  return t.goalNoWord(tracking.reason ?? "");
}

export function GoalTrackingCard({ tracking }: { tracking: GoalTracking }) {
  const t = useT(assets);
  const f = useFormat();
  const detail = useDetail(TRACKING_DETAIL);
  if (!tracking.shown || !tracking.route) return null;
  const line =
    tracking.paid_in != null && tracking.planned_by_now != null
      ? t.goalPaidOf(f.money(tracking.paid_in), f.money(tracking.planned_by_now))
      : t.goalSince(t.goalRoute(tracking.route), f.date(tracking.chosen_on));
  return (
    <a {...detail.linkProps(TRACKING_ID)} className="planning-kpi asset-kind-card" data-testid="goal-tracking" aria-label={t.openTab(t.goalTitle)}>
      <p className="planning-kpi-label">{t.goalTitle} ›</p>
      {tracking.word ? (
        <WordPill word={tracking.word === "on_path" ? "on_track" : "ambitious"} text={wordText(t, tracking)} />
      ) : (
        <p className="planning-kpi-sub">
          <strong>{wordText(t, tracking)}</strong>
        </p>
      )}
      {tracking.route === "path" && (tracking.points ?? []).length > 1 ? <PathGrowthChart points={tracking.points ?? []} rate={tracking.rate ?? null} compact /> : null}
      <p className="planning-kpi-sub">{line}</p>
    </a>
  );
}

function noRateText(t: T, reason: GoalTracking["no_rate"]): string {
  if (reason === "short_history") return t.noRateShort;
  if (reason === "no_flows") return t.noRateFlows;
  return t.noRateOther;
}

export function GoalTrackingPanel({ tracking, backLabel }: { tracking: GoalTracking; backLabel: string }) {
  const t = useT(assets);
  const f = useFormat();
  const detail = useDetail(TRACKING_DETAIL);
  if (!tracking.shown || !tracking.route) return null;
  const short = tracking.short ?? [];
  const sentences: string[] = [];
  if (tracking.word === "on_path") sentences.push(t.goalWordOnPath);
  if (short.includes("paid")) sentences.push(t.goalWordBehindPaid);
  if (short.includes("value")) sentences.push(t.goalWordBehindValue);
  if (!tracking.word && tracking.reason) sentences.push(t.goalReasonText(tracking.reason));
  return (
    <DetailPanel title={t.goalTitle} backLabel={backLabel} onClose={detail.close}>
      <div className="stack goal-panel" data-testid="goal-tracking-panel">
        {tracking.word ? (
          <WordPill word={tracking.word === "on_path" ? "on_track" : "ambitious"} text={wordText(t, tracking)} />
        ) : null}
        {sentences.map((sentence) => (
          <p key={sentence}>{sentence}</p>
        ))}
        {tracking.route === "path" ? (
          <div className="goal-c">
            <PathGrowthChart points={tracking.points ?? []} rate={tracking.rate ?? null} />
            <PathChartLast points={tracking.points ?? []} />
          </div>
        ) : null}
        <div className="goal-c">
          <Stats count={2}>
            <Stat value={t.goalRoute(tracking.route)} label={t.goalFollowed} />
            <Stat value={tracking.rate != null ? t.goalRate(f.percent(tracking.rate)) : "–"} label={t.goalRateLabel} />
          </Stats>
          {tracking.chosen_on ? <p className="goal-note">{t.goalSinceDay(f.date(tracking.chosen_on))}</p> : null}
        </div>
        {tracking.paid_in != null || tracking.planned_by_now != null ? (
          <div className="goal-c">
            <Stats count={2}>
              <Stat value={f.money(tracking.paid_in)} label={t.goalPaidIn} warn={short.includes("paid")} />
              <Stat value={f.money(tracking.planned_by_now)} label={t.goalPlannedByNow} />
            </Stats>
            {tracking.planned_per_year != null ? <p className="goal-note">{t.goalPlannedPerYear(f.money(tracking.planned_per_year))}</p> : null}
          </div>
        ) : null}
        {tracking.value_change != null ? (
          <div className="goal-c">
            <Stats count={2}>
              <Stat value={f.money(tracking.value_change)} label={t.goalValueChange} warn={short.includes("value")} />
              <Stat value={f.money(tracking.implied_change)} label={t.goalImpliedChange} />
            </Stats>
            <Stats count={2}>
              <Stat value={f.money(tracking.value_then)} label={t.goalValueThen(f.date(tracking.chosen_on))} />
              <Stat value={f.money(tracking.value_now)} label={t.goalValueNow} />
            </Stats>
          </div>
        ) : null}
        <div className="goal-c">
          <Stats count={1}>
            <Stat value={tracking.yearly_return != null ? f.percent(tracking.yearly_return) : noRateText(t, tracking.no_rate)} label={t.goalYearly} />
          </Stats>
        </div>
        <p className="goal-note">{t.goalCounted}</p>
        {tracking.reserve_depots > 0 ? <p className="goal-note">{t.goalReserveLeftOut(tracking.reserve_depots)}</p> : null}
        <p>
          <Link href={goalRouteHref(tracking.route)} className="button-link secondary">
            {t.goalOpenRoute} ›
          </Link>
        </p>
        <p className="goal-note">{t.goalFootnote}</p>
      </div>
    </DetailPanel>
  );
}
