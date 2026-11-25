"use client";

// „Was als einmalig zählt“ (phase 10 §7.12 (a), D307; mockup 19h frame 3), reached from „Ziele
// ändern“ on Haushalt › Budget. Each category the household may switch, the one-off ones first,
// with what was paid in it this calendar year (the server's, GET /household/one-off-codes) and a
// switch. A switch stores a new default-scenario row whose `one_off_overrides` hold the change,
// every other lever as saved (as the goals sheet does); the server refuses a code it does not take
// (`household_scenario.one_off_code_invalid`).
//
// Switches are saved one after another: a tap shows at once, and each save starts from the row
// the previous one returned, so two quick taps never overwrite each other. A failed save puts the
// switch back and shows the error.
import { useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { monthBudget } from "@/i18n/monthBudget";
import { planning } from "@/i18n/planning";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { DetailPanel } from "./DetailPanel";
import { fillLevers, type HouseholdLevers, type HouseholdScenario, saveDefaultScenario } from "./healthShared";
import { ALLOCATION_CATEGORY } from "./labels";
import { usePlanFormat } from "./planFormat";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

type OneOffCodes = components["schemas"]["OneOffCodes"];
type OneOffCode = components["schemas"]["OneOffCode"];
type AllocationCategory = components["schemas"]["AllocationCategory"];
type Overrides = NonNullable<HouseholdLevers["one_off_overrides"]>;

async function loadCodes(): Promise<OneOffCodes> {
  return ok(api.GET("/api/household/one-off-codes"));
}

function categoryName(code: string): string {
  return ALLOCATION_CATEGORY[code as AllocationCategory] ?? code;
}

function zero(value: string): boolean {
  return /^[-+]?0(\.0+)?$/.test(value.trim());
}

export function OneOffSheet({
  saved,
  current,
  onClose,
  onSaved,
}: {
  /** The saved levers (the default scenario's, else the defaults). */
  saved: HouseholdLevers;
  current: HouseholdScenario | null;
  onClose: () => void;
  /** After each stored row: the page reads the plan, the Budget and the key figures again. */
  onSaved: (row: HouseholdScenario) => void;
}) {
  const t = useT(planning);
  const m = useT(monthBudget);
  const f = usePlanFormat();
  const state = useLoad(loadCodes);
  // The row the next save supersedes, and the switches as the household wants them now.
  const row = useRef<HouseholdScenario | null>(current);
  const [initial] = useState(() => fillLevers(current?.levers ?? saved));
  const base = useRef<HouseholdLevers>(initial);
  const wanted = useRef<Overrides>({ ...(initial.one_off_overrides ?? {}) });
  const queue = useRef<Promise<void>>(Promise.resolve());
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState(0);
  const [error, setError] = useState<unknown>(null);

  function toggle(code: OneOffCode, on: boolean) {
    const had = Object.prototype.hasOwnProperty.call(wanted.current, code.category);
    const was = wanted.current[code.category];
    const next = { ...wanted.current };
    if (on === code.default) delete next[code.category];
    else next[code.category] = on;
    wanted.current = next;
    setShown((previous) => ({ ...previous, [code.category]: on }));
    setError(null);
    setPending((n) => n + 1);
    queue.current = queue.current.then(async () => {
      try {
        const levers = { ...base.current, one_off_overrides: { ...wanted.current } };
        const stored = await saveDefaultScenario(levers, row.current);
        row.current = stored;
        base.current = fillLevers(stored.levers);
        onSaved(stored);
      } catch (caught) {
        // Only this switch goes back; a later tap on another category stays as asked.
        const back = { ...wanted.current };
        if (had && was !== undefined) back[code.category] = was;
        else delete back[code.category];
        wanted.current = back;
        setShown((previous) => ({ ...previous, [code.category]: !on }));
        setError(caught);
      } finally {
        setPending((n) => n - 1);
      }
    });
    void queue.current.then(() => state.reload());
  }

  return (
    <DetailPanel title={m.oneOffTitle} backLabel={t.back} onClose={onClose}>
      <div className="stack">
        <p className="muted">{m.oneOffIntro}</p>
        {error != null ? <ErrorNotice error={error} /> : null}
        <Loading state={state}>
          {(data) =>
            (data.codes ?? []).length === 0 ? (
              <p className="muted">{m.oneOffEmpty}</p>
            ) : (
              <ul className="one-off-codes">
                {(data.codes ?? []).map((code) => {
                  const on = shown[code.category] ?? code.one_off;
                  const label = categoryName(code.category);
                  return (
                    <li key={code.category}>
                      <label className="one-off-code">
                        <span className="one-off-code-text">
                          <strong>{label}</strong>
                          <span className="muted">
                            {zero(code.year_total) ? m.yearNothing(data.year) : m.yearSum(data.year, f.euro(code.year_total))}
                            {on !== code.default ? ` · ${code.default ? m.defaultOneOff : m.defaultBudget}` : ""}
                          </span>
                        </span>
                        <input
                          type="checkbox"
                          role="switch"
                          aria-checked={on}
                          checked={on}
                          aria-label={m.switchAria(label)}
                          onChange={(event) => toggle(code, event.target.checked)}
                        />
                      </label>
                    </li>
                  );
                })}
              </ul>
            )
          }
        </Loading>
        {pending > 0 ? <p className="muted">{m.saving}</p> : null}
        <p className="muted">{m.oneOffFoot}</p>
      </div>
    </DetailPanel>
  );
}
