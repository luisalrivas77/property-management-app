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
import { useT } from "@/i18n";
import { accountForms } from "@/i18n/accountForms";
import { common } from "@/i18n/common";

const KEYS = ["shares", "reason", "effective_from"] as const;
const CODE_FIELDS = { "ownership.": "shares" };

const loadOwners = () => ok(api.GET("/api/owners"));

export function AccountOwnership({ account, onSaved }: { account: Account; onSaved: () => void }) {
  const owners = useLoad(loadOwners);
  const t = useT(accountForms);
  const [editing, setEditing] = useState(false);
  const done = useCallback(() => {
    setEditing(false);
    onSaved();
  }, [onSaved]);

  return (
    <div className="card stack">
      <h2>{t.owners}</h2>
      <Loading state={owners}>
        {(list) =>
          list.length === 0 ? (
            <p>
              {t.noOwnersBefore}
              <Link href="/owners">{t.noOwnersLink}</Link>
              {t.noOwnersAfter}
            </p>
          ) : editing || account.ownership_missing ? (
            <>
              {account.ownership_missing ? (
                <div className="notice notice-warn">
                  <p>
                    <strong>{t.setOwnersLead}</strong> {t.setOwnersText}
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
                {t.changeShares}
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
  const t = useT(accountForms);
  const c = useT(common);

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
        label={t.reasonOptional}
        hint={t.reasonHint}
        description={t.reasonDescription}
        errors={form.of("reason")}
      >
        <input type="text" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} autoComplete="off" />
      </Field>
      <Field
        id="account-ownership-from"
        label={t.validFromOptional}
        hint={t.validFromHint}
        description={t.validFromDescription}
        errors={form.of("effective_from")}
      >
        <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} />
      </Field>
      <div className="row">
        <Submit busy={busy}>{t.saveShares}</Submit>
        {onCancel ? (
          <button type="button" className="secondary" onClick={onCancel}>
            {c.cancel}
          </button>
        ) : null}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
