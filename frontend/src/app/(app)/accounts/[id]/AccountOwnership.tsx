"use client";

// Eigentümer of an account (phase 6 D157): the current owner shares, or the to-do „Eigentümer
// festlegen“ when none is set, and the form that records a full new set (POST
// /accounts/{id}/ownership; the old rows are kept, superseded). The shares are fractions that
// must sum to exactly 1, checked here exactly (whole numbers, no floats) before the API checks it
// again (ownership.sum_not_one). Offered when none is set: 1/1 with one owner, 1/2 each with two.
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Account, Owner, OwnershipShare } from "@/api/schemas";
import { ownerChip } from "@/components/accounts";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatFraction } from "@/components/format";
import { toFraction } from "@/components/input";
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
  const [shares, setShares] = useState<Record<string, string>>(() => initialShares(account, owners));
  const [reason, setReason] = useState(account.ownership?.[0]?.reason ?? "");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const body: OwnershipShare[] = [];
    for (const owner of owners) {
      const raw = shares[owner.id] ?? "";
      if (raw.trim() === "") continue;
      const share = values.take(`share-${owner.id}`, toFraction(raw));
      if (share) {
        body.push({
          owner_id: owner.id,
          share_numerator: share.numerator,
          share_denominator: share.denominator,
          reason: reason.trim() || null,
        });
      }
    }
    if (values.ok && body.length === 0) values.messages.shares = ["Mindestens einen Anteil eingeben."];
    else if (values.ok && !sumsToOne(body)) {
      values.messages.shares = ["Die Anteile müssen zusammen genau 1 (100 %) ergeben, z. B. 1/2 und 1/2."];
    }
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

  const sharesErrors = form.of("shares");
  return (
    <form className="stack" onSubmit={submit} noValidate>
      <fieldset className="stack" aria-describedby={sharesErrors.length > 0 ? "account-shares-error" : undefined}>
        <legend>Anteile (als Bruch oder Prozent, z. B. 1/2 oder 50 %; leer = kein Anteil)</legend>
        <div className="grid">
          {owners.map((owner) => (
            <Field
              key={owner.id}
              id={`share-${owner.id}`}
              label={owner.full_name}
              description="Der Anteil dieser Person am Konto, z. B. 1/2 bei einem gemeinsamen Konto, 1/1 bei einem eigenen. Alle Anteile zusammen müssen genau 1 (100 %) ergeben."
              errors={form.of(`share-${owner.id}`)}
            >
              <input
                type="text"
                value={shares[owner.id] ?? ""}
                onChange={(e) => setShares((current) => ({ ...current, [owner.id]: e.target.value }))}
                placeholder="1/2"
                autoComplete="off"
              />
            </Field>
          ))}
        </div>
        {sharesErrors.length > 0 ? (
          <ul className="field-errors" id="account-shares-error">
            {sharesErrors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        ) : null}
      </fieldset>
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

/** The inputs to start from: the current shares, else the offer of D157 (1/1 for one owner, 1/2
 * each for two), else empty. */
function initialShares(account: Account, owners: Owner[]): Record<string, string> {
  const current = account.ownership ?? [];
  if (current.length > 0) {
    return Object.fromEntries(
      owners.map((owner) => {
        const share = current.find((s) => s.owner_id === owner.id);
        return [owner.id, share ? formatFraction({ numerator: share.share_numerator, denominator: share.share_denominator }) : ""];
      }),
    );
  }
  const offer = owners.length === 1 ? "1/1" : owners.length === 2 ? "1/2" : "";
  return Object.fromEntries(owners.map((owner) => [owner.id, offer]));
}

/** Σ n/d = 1 exactly, in whole numbers (BigInt), the rule the API applies (D157). */
function sumsToOne(shares: OwnershipShare[]): boolean {
  let numerator = 0n;
  let denominator = 1n;
  for (const share of shares) {
    const n = BigInt(share.share_numerator);
    const d = BigInt(share.share_denominator);
    numerator = numerator * d + n * denominator;
    denominator = denominator * d;
  }
  return numerator === denominator;
}
