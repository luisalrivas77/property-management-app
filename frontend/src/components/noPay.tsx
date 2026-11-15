"use client";

// „Kein Arbeitslohn in <Jahr>“ (docs/phases/phase-4b.md D195, Luis 15 Nov 2026: "Button on the
// to-do"): an owner who had no salary for a whole year (parental leave) says so with one click.
// The click writes a return input of kind `no_pay` for the owner and the year (empty value, no
// document) through POST /api/return-inputs; „Aufheben“ withdraws it (a new row naming it,
// withdrawn = true), as every return input is corrected. The server answers
// return_input.not_current when a current marker already exists. What the marker does to the
// Anlage N, the estimate and the zvE is the backend's (the rules); this file only writes it.
//
// - NoPayButton: the to-do's button, on Übersicht and the Anlage N tab.
// - WithdrawNoPayButton: „Aufheben“, beside the marker and on the warning that a pay document
//   of the year overrules it (anlage_n.no_pay_overruled).
// - NoPayMarker: the Eigentümer page's per-owner and per-year cell: the button, or
//   „Kein Arbeitslohn angegeben“ with „Aufheben“.
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { useLoad } from "./useLoad";

type ReturnInput = components["schemas"]["ReturnInput"];

/** The one sentence beside the button. */
export const NO_PAY_HINT =
  "Wer im Jahr kein Gehalt hatte (etwa in Elternzeit), klickt hier; Elterngeld und andere Lohnersatzleistungen zählen trotzdem.";

/** The owner's current marker for the year, or null. */
async function currentNoPay(ownerId: string, year: number): Promise<ReturnInput | null> {
  const rows = await ok(
    api.GET("/api/return-inputs", {
      params: { query: { tax_year: year, kind: "no_pay", owner_id: ownerId } },
    }),
  );
  return rows[0] ?? null;
}

function recordNoPay(ownerId: string, year: number): Promise<ReturnInput> {
  return ok(
    api.POST("/api/return-inputs", {
      body: { kind: "no_pay", tax_year: year, owner_id: ownerId, document_id: null, value: {}, withdrawn: false },
    }),
  );
}

function withdrawNoPay(row: ReturnInput): Promise<ReturnInput> {
  return ok(
    api.POST("/api/return-inputs", {
      body: {
        kind: "no_pay",
        tax_year: row.tax_year,
        owner_id: row.owner_id ?? null,
        document_id: null,
        value: {},
        supersedes_id: row.id,
        withdrawn: true,
      },
    }),
  );
}

/** „Kein Arbeitslohn in <Jahr>“: one click writes the marker, then `onDone` reloads. */
export function NoPayButton({
  ownerId,
  year,
  onDone,
  className,
}: {
  ownerId: string;
  year: number;
  onDone?: () => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function click() {
    setBusy(true);
    setError(null);
    try {
      await recordNoPay(ownerId, year);
      onDone?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button type="button" className={className} disabled={busy} onClick={() => void click()} title={NO_PAY_HINT}>
        {busy ? "Speichert …" : `Kein Arbeitslohn in ${year}`}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** „Aufheben“: withdraws the owner's current marker for the year (`returnInputId`, when the
 * caller knows it, must still be the current one; a marker already withdrawn just reloads). */
export function WithdrawNoPayButton({
  ownerId,
  year,
  returnInputId,
  label = "Aufheben",
  onDone,
  className = "link",
}: {
  ownerId: string;
  year: number;
  returnInputId?: string | null;
  label?: string;
  onDone?: () => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function click() {
    setBusy(true);
    setError(null);
    try {
      const row = await currentNoPay(ownerId, year);
      if (row && (!returnInputId || row.id === returnInputId)) await withdrawNoPay(row);
      onDone?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button type="button" className={className} disabled={busy} onClick={() => void click()}>
        {busy ? "Speichert …" : label}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** The Eigentümer page's cell: the marker in force („Kein Arbeitslohn angegeben“ with
 * „Aufheben“), or the button that sets it. `onChanged` reloads the page's estimates. */
export function NoPayMarker({ ownerId, year, onChanged }: { ownerId: string; year: number; onChanged?: () => void }) {
  const load = useCallback(() => currentNoPay(ownerId, year), [ownerId, year]);
  const marker = useLoad(load);
  const reload = marker.reload;
  const changed = useCallback(() => {
    reload();
    onChanged?.();
  }, [reload, onChanged]);
  if (marker.data === undefined) return null;
  if (marker.data) {
    return (
      <span className="row">
        <span className="badge">Kein Arbeitslohn angegeben</span>
        <WithdrawNoPayButton ownerId={ownerId} year={year} returnInputId={marker.data.id} onDone={changed} />
      </span>
    );
  }
  return <NoPayButton ownerId={ownerId} year={year} onDone={changed} className="link" />;
}
