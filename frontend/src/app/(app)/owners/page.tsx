"use client";

// Eigentümer (§9.1): list with inline create and edit (Name, Steuer-ID-Referenz,
// Kirchensteuer, Bundesland). The typed Grenzsteuersatz is gone (phase 4 D90, migration 0016):
// the rate is derived per year from income inputs (phase-4.md §9). Since phase 10 D281 the
// income inputs, Kinder und Haushalt are typed under Steuern › Eure Angaben; the section
// „Einkommen“ here (#einkommen, #kinder, #haushalt kept for old links) points there.
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Owner, StateCode } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import Link from "next/link";

import { blankToNull } from "@/components/input";
import { options, STATE_CODE } from "@/components/labels";
import { taxFormHref } from "@/components/taxYear";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { owners as ownersText } from "@/i18n/owners";

const loadOwners = () => ok(api.GET("/api/owners"));
const KEYS = ["full_name", "tax_id_ref", "church_tax", "tax_state"] as const;

export default function OwnersPage() {
  const t = useT(ownersText);
  const owners = useLoad(loadOwners);
  const [editing, setEditing] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  return (
    <section>
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      {saved ? <Notice>{saved}</Notice> : null}
      <Loading state={owners}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">
              {t.noneYetBefore}
              <a href="#eigentuemer-anlegen">{t.createOwner}</a>
              {t.noneYetAfter}
            </p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.colName}</th>
                    <th>{t.colTaxIdRef}</th>
                    <th>{t.colChurchTax}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((owner) =>
                    editing === owner.id ? (
                      <tr key={owner.id}>
                        <td colSpan={4}>
                          <OwnerForm
                            owner={owner}
                            onDone={(message) => {
                              setEditing(null);
                              setSaved(message);
                              owners.reload();
                            }}
                            onCancel={() => setEditing(null)}
                          />
                        </td>
                      </tr>
                    ) : (
                      <tr key={owner.id}>
                        <td>{owner.full_name}</td>
                        <td>{owner.tax_id_ref ?? "–"}</td>
                        <td>{owner.church_tax && owner.tax_state ? t.churchTaxYes(STATE_CODE[owner.tax_state]) : t.churchTaxNo}</td>
                        <td>
                          <button
                            type="button"
                            className="link"
                            onClick={() => {
                              setSaved(null);
                              setEditing(owner.id);
                            }}
                          >
                            {t.edit}
                          </button>
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      <h2 id="einkommen">{t.income}</h2>
      <div className="card stack">
        <p id="kinder" className="muted">
          {t.incomeMoved}
        </p>
        <p id="haushalt">
          <Link className="button-link" href={taxFormHref("angaben", { year: new Date().getFullYear(), propertyId: null, ownerId: null })}>
            {t.openYourDetails}
          </Link>
        </p>
      </div>
      <h2 id="eigentuemer-anlegen">{t.createOwner}</h2>
      <div className="card">
        <OwnerForm
          key={owners.data?.length ?? 0}
          onDone={(message) => {
            setSaved(message);
            owners.reload();
          }}
        />
      </div>
    </section>
  );
}

function OwnerForm({ owner, onDone, onCancel }: { owner?: Owner; onDone: (message: string) => void; onCancel?: () => void }) {
  const t = useT(ownersText);
  const prefix = owner ? `owner-${owner.id}` : "owner-new";
  const [fullName, setFullName] = useState(owner?.full_name ?? "");
  const [taxIdRef, setTaxIdRef] = useState(owner?.tax_id_ref ?? "");
  const [churchTax, setChurchTax] = useState(owner?.church_tax ?? false);
  const [taxState, setTaxState] = useState<StateCode | "">(owner?.tax_state ?? "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    if (churchTax && taxState === "") {
      values.take("tax_state", { ok: false, message: t.churchTaxNeedsState });
    }
    if (!values.ok) return form.invalid(values.messages);
    const body = {
      full_name: fullName.trim(),
      tax_id_ref: blankToNull(taxIdRef),
      church_tax: churchTax,
      tax_state: taxState === "" ? null : taxState,
    };
    setBusy(true);
    form.clear();
    try {
      if (owner) {
        await ok(api.PATCH("/api/owners/{owner_id}", { params: { path: { owner_id: owner.id } }, body }));
        onDone(t.saved);
      } else {
        await ok(api.POST("/api/owners", { body }));
        onDone(t.created);
      }
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="grid">
        <Field
          id={`${prefix}-name`}
          label={t.colName}
          description={t.nameDescription}
          errors={form.of("full_name")}
        >
          <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id={`${prefix}-tax`}
          label={t.colTaxIdRef}
          hint={t.taxIdRefHint}
          description={t.taxIdRefDescription}
          errors={form.of("tax_id_ref")}
        >
          <input type="text" value={taxIdRef} onChange={(e) => setTaxIdRef(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${prefix}-state`}
          label={t.state}
          description={t.stateDescription}
          errors={form.of("tax_state")}
        >
          <select value={taxState} onChange={(e) => setTaxState(e.target.value as StateCode | "")}>
            <option value="">–</option>
            {options(STATE_CODE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <label className="inline">
        <input type="checkbox" checked={churchTax} onChange={(e) => setChurchTax(e.target.checked)} />
        {t.paysChurchTax}
      </label>
      <div className="row">
        <Submit busy={busy}>{owner ? t.save : t.create}</Submit>
        {onCancel ? (
          <button type="button" className="secondary" onClick={onCancel}>
            {t.cancel}
          </button>
        ) : null}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
