"use client";

// Eigentümer of an account (phase 6 D157): the current owner shares, or the to-do „Eigentümer
// festlegen“ when none is set, and the form that records a full new set (POST
// /accounts/{id}/ownership; the old rows are kept, superseded). The shares are fractions that
// must sum to exactly 1, checked here exactly (whole numbers, no floats) before the API checks it
// again (ownership.sum_not_one). Offered when none is set: 1/1 with one owner, 1/2 each with two.
// The share inputs are shared with „Konto anlegen und einlesen“ (components/ownershipShares).
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Account, Owner } from "@/api/schemas";
import { ownerChip } from "@/components/accounts";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { collectShares, initialShares, OwnerShareInputs } from "@/components/ownershipShares";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const KEYS = ["shares", "reason", "effective_from"] as const;
const CODE_FIELDS = { "ownership.": "shares" };

const loadOwners = () => ok(api.GET("/api/owners"));

export function AccountOwnership({ account, onSaved }: { account: Account; onSaved: () => void }) {
  const owners = useLoad(loadOwners);
  const [editing, setEditing] = useState(false);
  const done = useCallback(() => {
    setEditing(false);
    onSaved();
  }, [onSaved]);

  return (
    <div className="card stack">
      <h2>Eigentümer</h2>
      <Loading state={owners}>
        {(list) =>
          list.length === 0 ? (
            <p>
              Noch keine Eigentümer erfasst. Zuerst unter <Link href="/owners">Eigentümer</Link> anlegen.
            </p>
          ) : editing || account.ownership_missing ? (
            <>
              {account.ownership_missing ? (
                <div className="notice notice-warn">
                  <p>
                    <strong>Eigentümer festlegen:</strong> Wem gehört dieses Konto, und zu welchem Anteil? Ein gemeinsames Konto
                    gehört meist beiden je zur Hälfte (1/2), ein eigenes Konto einer Person ganz (1/1).
                  </p>
                </div>
              ) : null}
              <OwnershipForm account={account} owners={list} onSaved={done} onCancel={editing ? () => setEditing(false) : null} />
            </>
          ) : (
            <div className="row">
              <span className="badge">{ownerChip(account.ownership ?? [], list)}</span>
              <span className="spacer" />
              <button type="button" className="secondary" onClick={() => setEditing(true)}>
                Anteile ändern
              </button>
            </div>
          )
        }
      </Loading>
    </div>
  );
}

function OwnershipForm({
  account,
  owners,
  onSaved,
  onCancel,
}: {
  account: Account;
  owners: Owner[];
  onSaved: () => void;
  onCancel: (() => void) | null;
}) {
  const [shares, setShares] = useState<Record<string, string>>(() => initialShares(account.ownership, owners));
  const [reason, setReason] = useState(account.ownership?.[0]?.reason ?? "");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const body = collectShares(values, owners, shares, { reason: reason.trim() || null });
    if (!values.ok) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/accounts/{account_id}/ownership", {
          params: { path: { account_id: account.id } },
          body: { shares: body, effective_from: effectiveFrom || null },
        }),
      );
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <OwnerShareInputs owners={owners} shares={shares} onChange={setShares} form={form} errorId="account-shares-error" />
      <Field
        id="account-ownership-reason"
        label="Begründung (optional)"
        hint="z. B. „gemeinsames Konto“ oder „vom gemeinsamen Konto bezahlt“."
        description="Warum das Konto diesen Personen zu diesen Anteilen gehört. Hausbuch speichert es mit den Anteilen, damit es später nachvollziehbar bleibt."
        errors={form.of("reason")}
      >
        <input type="text" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} autoComplete="off" />
      </Field>
      <Field
        id="account-ownership-from"
        label="Gültig ab (optional)"
        hint="Leer lassen: ab heute."
        description="Ab wann diese Anteile gelten. Die bisherigen Anteile bleiben im Protokoll erhalten."
        errors={form.of("effective_from")}
      >
        <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} />
      </Field>
      <div className="row">
        <Submit busy={busy}>Anteile speichern</Submit>
        {onCancel ? (
          <button type="button" className="secondary" onClick={onCancel}>
            Abbrechen
          </button>
        ) : null}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
