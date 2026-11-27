"use client";

// „Aufteilung ändern“ (phase 10 §7.11 (i) item 10; (a) 2, (f) 1, D301): the household's own split
// of the money it invests between funds (`depot`) and crypto, in the side panel of Haushalt ›
// Ziel's „Womit gerechnet wird“ and of „Euer Plan“'s plan card. Every figure is GET
// /household-plan's: the effective split with its source (`investment_split`), the form's prefill
// from the purchases of the closed months (`investment_split_prefill`). The app proposes no split
// (D247): the form starts from the saved lever, else from what was bought, else empty, and saves
// only what a member typed. The one calculation here is the other share as 100 − x, on whole
// tenths of a percent from the typed string, never a float. The split is written into the
// default household scenario with every other lever as saved (fillLevers), through the
// scenarios' own endpoint; the server checks that the shares add up to 100.
import { type FormEvent, useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { Field } from "./form";
import { HealthTodoList } from "./healthFigures";
import { currentDefault, fillLevers, type HouseholdPlan, loadScenarioHistory, saveDefaultScenario } from "./healthShared";
import { toDecimalString } from "./input";
import { usePlanFormat } from "./planFormat";
import { useLoad } from "./useLoad";

export type InvestmentSplit = components["schemas"]["InvestmentSplit"];

/** A share as whole tenths of a percent ("75,5" → 755), or null when it is no number from 0 to
 * 100 with at most one decimal place. Integer arithmetic on the typed digits only. */
function tenths(raw: string): number | null {
  const parsed = toDecimalString(raw, 1);
  if (!parsed.ok) return null;
  const [whole = "0", fraction = ""] = parsed.value.split(".");
  const value = Number(whole) * 10 + Number(fraction || "0");
  return Number.isSafeInteger(value) && value <= 1000 ? value : null;
}

/** Tenths as the field shows them: 755 → de „75,5“, en "75.5"; 750 → „75“. */
function shown(value: number, decimalMark: string): string {
  const fraction = value % 10;
  return `${Math.floor(value / 10)}${fraction ? `${decimalMark}${fraction}` : ""}`;
}

/** Tenths as the API takes them, one decimal place: 750 → "75.0". */
function posted(value: number): string {
  return `${Math.floor(value / 10)}.${value % 10}`;
}

/** The server's share ("75.0") as the field shows it; empty when it is not a share. */
function fieldValue(share: string | null | undefined, decimalMark: string): string {
  const value = share == null ? null : tenths(share);
  return value == null ? "" : shown(value, decimalMark);
}

/** Writes the split into the default scenario in force now, every other lever as saved. */
export async function saveSplit(split: InvestmentSplit): Promise<void> {
  const current = currentDefault(await loadScenarioHistory());
  await saveDefaultScenario({ ...fillLevers(current?.levers), investment_split: split }, current);
}

/** The split the plan invests in, what it uses until one is saved, and „Aufteilung ändern“.
 * `save` writes the split and reads the plan again; `blocked` holds the save while the Haushalt
 * tabs' unsaved draft of the levers (the Budget's class panel), whose save posts every lever of
 * the draft, would write the old split back. Durchspielen and the Reserve form read the stored
 * scenario fresh when they save, so they keep a saved split and do not hold it. */
export function SplitSection({
  plan,
  save,
  blocked = false,
  onChanged,
}: {
  plan: HouseholdPlan;
  save: (split: InvestmentSplit) => Promise<void>;
  blocked?: boolean;
  onChanged: () => void;
}) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const [open, setOpen] = useState(false);
  const effective = plan.investment_split ?? null;
  const shares = effective?.shares ?? null;
  const source = effective?.source ?? "none";
  const leftOut = effective?.left_out ?? [];
  const kind = (k: string) => (k === "crypto" ? t.splitCrypto : t.splitFunds);
  const meanwhile =
    source === "saved"
      ? t.splitSaved
      : source === "savings_plans"
        ? t.splitMeanwhilePlans
        : source === "holdings"
          ? t.splitMeanwhileHoldings
          : null;
  return (
    <div className="stack" data-testid="investment-split">
      <p>{shares ? t.basisSplit(p.percentValue(shares.depot), p.percentValue(shares.crypto)) : source === "none" ? t.splitMeanwhileNone : t.basisNoSplit}</p>
      {meanwhile ? <p className="goal-note">{meanwhile}</p> : null}
      {shares && leftOut.length > 0 ? <p className="goal-note">{t.splitLeftOut(leftOut.map(kind).join(" · "))}</p> : null}
      <HealthTodoList todos={effective?.todos ?? []} onChanged={onChanged} />
      {open ? (
        <SplitForm
          plan={plan}
          blocked={blocked}
          onCancel={() => setOpen(false)}
          onSave={async (split) => {
            await save(split);
            setOpen(false);
          }}
        />
      ) : (
        <p>
          <button type="button" className="secondary" onClick={() => setOpen(true)}>
            {t.changeSplit}
          </button>
        </p>
      )}
    </div>
  );
}

function SplitForm({
  plan,
  blocked,
  onCancel,
  onSave,
}: {
  plan: HouseholdPlan;
  blocked: boolean;
  onCancel: () => void;
  onSave: (split: InvestmentSplit) => Promise<void>;
}) {
  const t = useT(goalText);
  const p = usePlanFormat();
  const ids = useId();
  const decimalMark = useLocale() === "de" ? "," : ".";
  const saved = plan.levers.investment_split ?? null;
  const prefill = plan.investment_split_prefill ?? null;
  // From the saved lever, else from what was bought, else empty: never a split of the app's own.
  const start = saved ?? prefill?.split ?? null;
  const [depot, setDepot] = useState(() => fieldValue(start?.depot, decimalMark));
  const [crypto, setCrypto] = useState(() => fieldValue(start?.crypto, decimalMark));
  const [errors, setErrors] = useState<{ depot?: string; crypto?: string; sum?: string }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Typing one share fills the other with 100 − x; a share that is no number leaves it alone.
  const type = (raw: string, set: (v: string) => void, other: (v: string) => void) => {
    set(raw);
    setErrors({});
    const value = tenths(raw);
    if (value != null) other(shown(1000 - value, decimalMark));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const d = tenths(depot);
    const c = tenths(crypto);
    const next: typeof errors = {};
    if (d == null) next.depot = t.splitInvalid;
    if (c == null) next.crypto = t.splitInvalid;
    if (d != null && c != null && d + c !== 1000) next.sum = t.splitSum;
    setErrors(next);
    if (d == null || c == null || next.sum) return;
    setBusy(true);
    setError(null);
    try {
      await onSave({ depot: posted(d), crypto: posted(c) });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="stack goal-form" noValidate onSubmit={(event) => void submit(event)} aria-label={t.changeSplit}>
      {!saved && prefill ? <p className="goal-note">{t.splitPrefilled(p.month(prefill.from_month), p.month(prefill.to_month))}</p> : null}
      {saved && prefill ? (
        <p className="goal-note">
          {t.splitBought(p.month(prefill.from_month), p.month(prefill.to_month), p.percentValue(prefill.split.depot), p.percentValue(prefill.split.crypto))}
        </p>
      ) : null}
      <Field id={`${ids}-depot`} label={t.splitFundsField} errors={errors.depot ? [errors.depot] : []}>
        <input inputMode="decimal" autoComplete="off" value={depot} onChange={(event) => type(event.target.value, setDepot, setCrypto)} />
      </Field>
      <Field id={`${ids}-crypto`} label={t.splitCryptoField} hint={t.splitHint} errors={errors.crypto ? [errors.crypto] : []}>
        <input inputMode="decimal" autoComplete="off" value={crypto} onChange={(event) => type(event.target.value, setCrypto, setDepot)} />
      </Field>
      {errors.sum ? (
        <ul className="field-errors" role="alert">
          <li>{errors.sum}</li>
        </ul>
      ) : null}
      {blocked ? <p className="muted">{t.adoptDirty}</p> : null}
      <ErrorNotice error={error} />
      <div className="row">
        <button type="submit" disabled={busy || blocked}>
          {t.splitSave}
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
    </form>
  );
}

/** „Euer Plan“ reads GET /household-goal/plan, which carries no split: this reads GET
 * /household-plan for the section and again after a save; `onWritten` reads the page again. */
export function SplitOnPlanPage({ onWritten }: { onWritten: () => void }) {
  const t = useT(goalText);
  const load = useCallback(() => ok(api.GET("/api/household-plan")).catch(() => null), []);
  const state = useLoad(load);
  const plan = state.data ?? null;
  if (!plan) return null;
  return (
    <>
      <h3 className="goal-kicker">{t.splitLabel}</h3>
      <SplitSection
        plan={plan}
        onChanged={state.reload}
        save={async (split) => {
          await saveSplit(split);
          state.reload();
          onWritten();
        }}
      />
    </>
  );
}
