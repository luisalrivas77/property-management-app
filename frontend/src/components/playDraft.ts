// The draft of Haushalt › Durchspielen (phase 10 §7.10 (a), D299): changes held on top of the
// saved levers, never a copy of them (Q-O179), so a lever saved elsewhere in the meantime (the
// reserve target on Reserve, the buffer on Budget, the rates on Anlagen) is never written back
// with an old value when „Als Plan behalten“ stores the draft. Only the three levers the tab
// writes are held: the spending changes per code, the income changes and the one-offs.
import type { components } from "@/api/types";

import type { HouseholdLevers } from "./healthShared";

export type SpendingChange = components["schemas"]["SpendingChange"];
export type IncomeChange = components["schemas"]["IncomeChange"];
export type OneOff = components["schemas"]["OneOff"];

/** The income row: the monthly net income assumed (an absolute amount, typed against today's
 * mean) from a month, open-ended unless `to` is set. Its difference to the mean is one more
 * income change (§7.10 (a) 3). */
export interface IncomeRow {
  monthly: string;
  from: string;
  to: string | null;
}

export interface PlayChanges {
  /** Per code: the change its row sets, or null when the row drops the saved change. A code
   * missing here keeps the saved change as it is (its percent and its months untouched). */
  spending: Record<string, SpendingChange | null>;
  /** The saved income changes the tab keeps, only while one of them was removed. */
  income?: IncomeChange[];
  incomeRow?: IncomeRow;
  /** The one-offs as the tab holds them, only while they differ from the saved ones. */
  oneOffs?: OneOff[];
}

export const NO_PLAY: PlayChanges = { spending: {} };

const json = (value: unknown) => JSON.stringify(value);

/** The income change the income row stands for: its difference to today's mean, or nothing
 * when the row says today's mean (a row typed back to its Heute value is no change). */
export function incomeRowChange(row: IncomeRow | undefined, meanIncome: string | null): IncomeChange | null {
  if (!row || meanIncome === null) return null;
  const difference = moneyDifference(row.monthly, meanIncome);
  if (difference === null || /^-?0\.00$/.test(difference)) return null;
  return { monthly_net: difference, from_month: row.from, to_month: row.to, note_code: "other" };
}

/** The saved levers with the tab's changes applied: what the preview and the save send. */
export function applyPlay(saved: HouseholdLevers, play: PlayChanges, meanIncome: string | null): HouseholdLevers {
  const kept = (saved.spending_changes ?? []).filter((change) => !(change.code in play.spending));
  const set = Object.values(play.spending).filter((change): change is SpendingChange => change !== null);
  const typed = incomeRowChange(play.incomeRow, meanIncome);
  return {
    ...saved,
    spending_changes: [...kept, ...set],
    income_changes: [...(play.income ?? saved.income_changes ?? []), ...(typed ? [typed] : [])],
    one_offs: play.oneOffs ?? saved.one_offs ?? [],
  };
}

/** How many entries the draft adds, changes or drops: one per code, per income change and per
 * one-off that is not in the saved levers as it stands. */
export function playCount(saved: HouseholdLevers, play: PlayChanges, meanIncome: string | null): number {
  const differing = <V>(before: readonly V[], after: readonly V[]) => {
    const was = before.map(json);
    const now = after.map(json);
    return now.filter((item) => !was.includes(item)).length + was.filter((item) => !now.includes(item)).length;
  };
  const income = play.income ? differing(saved.income_changes ?? [], play.income) : 0;
  const typed = incomeRowChange(play.incomeRow, meanIncome) ? 1 : 0;
  const oneOffs = play.oneOffs ? differing(saved.one_offs ?? [], play.oneOffs) : 0;
  return Object.keys(play.spending).length + income + typed + oneOffs;
}

/** The planned changes a saved scenario holds (§7.10 (a) 6): income changes, spending changes
 * and one-offs, each one entry. */
export function plannedCount(levers: HouseholdLevers | null | undefined): number {
  return (levers?.income_changes ?? []).length + (levers?.spending_changes ?? []).length + (levers?.one_offs ?? []).length;
}

/** A row's spending change: set to `change`, or back to the saved state when the row says the
 * same as the saved levers (a code typed back to its saved change, or to today without one). */
export function withSpending(saved: HouseholdLevers, play: PlayChanges, code: string, change: SpendingChange | null): PlayChanges {
  const before = (saved.spending_changes ?? []).find((c) => c.code === code) ?? null;
  const spending = { ...play.spending };
  const same =
    before === null
      ? change === null
      : change !== null && json({ ...before, mean_at_save: null }) === json({ ...change, mean_at_save: null });
  if (same) delete spending[code];
  else spending[code] = change;
  return { ...play, spending };
}

/** The saved income changes the tab keeps, dropped from the draft when it keeps them all. */
export function withIncome(saved: HouseholdLevers, play: PlayChanges, income: IncomeChange[]): PlayChanges {
  const rest: PlayChanges = { ...play };
  delete rest.income;
  return json(income) === json(saved.income_changes ?? []) ? rest : { ...rest, income };
}

/** The draft after a lever component (the spending fields, the one-offs) changed the levers
 * `after` that `applyPlay` gave it: its spending changes per code and its one-offs, kept only
 * where they differ from the saved levers. The income changes stay the tab's own rows. */
export function fromLevers(saved: HouseholdLevers, play: PlayChanges, after: HouseholdLevers): PlayChanges {
  let next: PlayChanges = play;
  const codes = new Set([
    ...(saved.spending_changes ?? []).map((c) => c.code),
    ...(after.spending_changes ?? []).map((c) => c.code),
    ...Object.keys(play.spending),
  ]);
  for (const code of codes) {
    next = withSpending(saved, next, code, (after.spending_changes ?? []).find((c) => c.code === code) ?? null);
  }
  const oneOffs = after.one_offs ?? [];
  const rest: PlayChanges = { ...next };
  delete rest.oneOffs;
  return json(oneOffs) === json(saved.one_offs ?? []) ? rest : { ...rest, oneOffs };
}

/** Every changed row of the draft from `month` (§7.10 (a) 3, „Für alle übernehmen“): the
 * spending changes and the income row; a one-off keeps its own month. A row whose end would lie
 * before the month loses its end. */
export function fromMonthForAll(play: PlayChanges, month: string): PlayChanges {
  const spending: Record<string, SpendingChange | null> = {};
  for (const [code, change] of Object.entries(play.spending)) {
    spending[code] =
      change === null ? null : { ...change, from_month: month, to_month: change.to_month && change.to_month < month ? null : change.to_month };
  }
  const row = play.incomeRow;
  return {
    ...play,
    spending,
    ...(row ? { incomeRow: { ...row, from: month, to: row.to && row.to < month ? null : row.to } } : {}),
  };
}

/** "4056.00" − "3776" as a two-place decimal string, in whole cents (money is never a float). */
export function moneyDifference(minuend: string, subtrahend: string): string | null {
  const cents = (value: string): bigint | null => {
    const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim());
    if (!match) return null;
    const whole = BigInt(match[2]!) * 100n + BigInt((match[3] ?? "").padEnd(2, "0"));
    return match[1] === "-" ? -whole : whole;
  };
  const a = cents(minuend);
  const b = cents(subtrahend);
  if (a === null || b === null) return null;
  const diff = a - b;
  const negative = diff < 0n;
  const abs = negative ? -diff : diff;
  return `${negative ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

/** The month after `month` (YYYY-MM). */
export function monthAfter(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  return mon === 12 ? `${year + 1}-01` : `${year}-${String(mon + 1).padStart(2, "0")}`;
}
