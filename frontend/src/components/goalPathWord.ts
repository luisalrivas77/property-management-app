// The goal's path as the screens show it (phase 10 §7.11 (i), D311): one helper for „Euer Plan“,
// Übersicht's goal tile and Haushalt › Ziel, so all three read the same word and amounts. A leaf
// module: it imports no component, so householdGoal.tsx and goalPathCards.tsx both use it
// without an import cycle.
//
// Once a path is adopted, the live goal is read under the adopted wants budget and mostly reads
// „Auf Kurs“, while `adopted` keeps the path as it was adopted (the spec: „the screens show the
// adopted path“). So while the adopted path is followed and nothing it was computed from has
// moved, its stored state and amounts are shown; otherwise the path as read today. Nothing is
// computed here; every figure is the server's.
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { goalPathText } from "@/i18n/goalPath";

import { usePlanFormat } from "./planFormat";

type HouseholdGoal = components["schemas"]["HouseholdGoal"];
export type GoalPath = components["schemas"]["GoalPath"];
export type GoalPathState = components["schemas"]["GoalPathState"];
export type GoalPathAdopted = components["schemas"]["GoalPathAdopted"];
type GoalWord = components["schemas"]["GoalWord"];

export interface PathView {
  path: GoalPath;
  state: GoalPathState;
  monthly: string | null;
  extra: string | null;
  /** The return the path counts on: the typed mix at step 1, the return needed at step 2. */
  rate: string | null;
  return_needed: string | null;
  windows: number | null;
  reached_windows: number | null;
  wants_budget_total: string | null;
  adopted: GoalPathAdopted | null;
  /** The household follows the adopted path („Diesem Weg folgen“ is `path`). */
  followed: boolean;
  /** Adopted, followed and nothing moved: the figures shown are the adopted ones. */
  current: boolean;
}

/** The household follows the adopted path: tracking rows exist only then. */
export function isFollowed(goal: HouseholdGoal | null | undefined): boolean {
  return goal?.choice?.investment === "path";
}

export function pathView(goal: HouseholdGoal | null | undefined): PathView | null {
  const path = goal?.goal_path;
  if (!goal || !path || goal.issue != null) return null;
  const adopted = path.adopted ?? null;
  const followed = isFollowed(goal);
  const current = adopted != null && followed && !adopted.moved;
  if (current) {
    return {
      path,
      state: adopted.state,
      monthly: adopted.monthly,
      extra: adopted.extra,
      rate: adopted.rate ?? null,
      return_needed: adopted.return_needed ?? null,
      windows: adopted.windows ?? null,
      reached_windows: adopted.reached_windows ?? null,
      wants_budget_total: adopted.wants_budget_total,
      adopted,
      followed,
      current,
    };
  }
  return {
    path,
    state: path.state,
    monthly: path.monthly ?? null,
    extra: path.extra ?? null,
    rate: path.rate ?? path.typed_rate ?? null,
    return_needed: path.return_needed ?? null,
    windows: path.windows ?? null,
    reached_windows: path.reached_windows ?? null,
    wants_budget_total: path.wants_budget_total ?? null,
    adopted,
    followed,
    current,
  };
}

/** The pill's colour: both „Machbar“ states read as feasible. */
export function pathWord(state: GoalPathState): GoalWord {
  if (state === "doable_rates" || state === "doable_return") return "feasible";
  return state;
}

/** The path's word and its pill colour; „Machbar mit 9,4 %“ at a higher return. */
export function usePathWord(goal: HouseholdGoal | null | undefined): { word: GoalWord; text: string; view: PathView } | null {
  const g = useT(goalText);
  const t = useT(goalPathText);
  const p = usePlanFormat();
  const view = pathView(goal);
  if (!view) return null;
  const texts: Record<GoalPathState, string> = {
    on_track: g.onTrack,
    doable_rates: g.feasible,
    doable_return: view.rate != null ? t.feasibleAt(p.ratio(view.rate)) : g.feasible,
    ambitious: g.ambitious,
    hardly: g.hardly,
  };
  return { word: pathWord(view.state), text: texts[view.state], view };
}

/** A gap the server reports: an amount above 0, or nothing. */
export function hasGap(value: string | null | undefined): value is string {
  return value != null && value !== "" && !/^-?0*(\.0*)?$/.test(value) && !value.startsWith("-");
}
