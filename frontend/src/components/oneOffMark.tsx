"use client";

// A payment's one-off mark in its detail panel (phase 10 §7.12 (b), D307): „Als einmalig
// markieren“ takes one part of a private payment out of the Budget, the savings rate and the
// averages, „Doch normal zählen“ puts it back. The server names the parts that may be marked (GET
// /household/one-off-marks?transaction_id=); a payment with none gets no button. A part whose
// category already counts as one-off (the household's switches under „Was als einmalig zählt“)
// shows why instead of a button, since its own answer would change nothing.
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { monthBudget } from "@/i18n/monthBudget";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { ALLOCATION_CATEGORY } from "./labels";
import { usePlanFormat } from "./planFormat";
import { useLoad } from "./useLoad";

type PaymentOneOff = components["schemas"]["PaymentOneOff"];
type AllocationCategory = components["schemas"]["AllocationCategory"];

function categoryName(code: AllocationCategory): string {
  return ALLOCATION_CATEGORY[code] ?? code;
}

export function OneOffMark({ transactionId, onChanged }: { transactionId: string; onChanged?: () => void }) {
  const m = useT(monthBudget);
  const f = usePlanFormat();
  const load = useCallback(
    () => ok(api.GET("/api/household/one-off-marks", { params: { query: { transaction_id: transactionId } } })),
    [transactionId],
  );
  const state = useLoad(load);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const parts: PaymentOneOff[] = state.data ?? [];
  // Nothing to mark (a rental line, a transfer, income) or not read yet: no button and no space.
  if (parts.length === 0) return error != null ? <ErrorNotice error={error} /> : null;
  const several = parts.length > 1;

  async function mark(part: PaymentOneOff, answer: "one_off" | "regular") {
    setBusy(part.allocation_id);
    setError(null);
    try {
      await ok(api.POST("/api/household/one-off-marks", { body: { allocation_id: part.allocation_id, answer } }));
      state.reload();
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="stack one-off-mark" aria-label={m.markOneOff}>
      {error != null ? <ErrorNotice error={error} /> : null}
      {parts.map((part) => {
        const name = categoryName(part.category);
        const amount = f.euro(part.amount);
        if (part.category_one_off) {
          return (
            <p key={part.allocation_id} className="muted">
              {several ? `${name}, ${amount}: ` : ""}
              {m.categoryOneOff(name)}
            </p>
          );
        }
        const answer = part.marked ? "regular" : "one_off";
        const label = part.marked
          ? several
            ? m.markRegularPart(name, amount)
            : m.markRegular
          : several
            ? m.markOneOffPart(name, amount)
            : m.markOneOff;
        return (
          <div key={part.allocation_id} className="stack">
            {part.marked ? <p className="muted">{several ? `${name}, ${amount}: ${m.markedNote}` : m.markedNote}</p> : null}
            <div className="row">
              <button
                type="button"
                className="secondary"
                disabled={busy !== null}
                aria-busy={busy === part.allocation_id}
                onClick={() => void mark(part, answer)}
              >
                {label}
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
