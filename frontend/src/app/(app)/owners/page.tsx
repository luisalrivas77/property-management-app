"use client";

// Eigentümer (§9.1): list with inline create and edit (Name, Steuer-ID-Referenz,
// Kirchensteuer, Bundesland). The typed Grenzsteuersatz is gone (phase 4 D90, migration 0016):
// the rate is derived per year from income inputs (phase-4.md §9): the Einkommen section, per
// owner the expected gross pay and the deductions, for the household a Bescheid's zvE and a
// typed tax rate (components/income.tsx).
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Owner, StateCode } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { IncomeSection } from "@/components/income";
import { blankToNull } from "@/components/input";
import { options, STATE_CODE } from "@/components/labels";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadOwners = () => ok(api.GET("/api/owners"));
const KEYS = ["full_name", "tax_id_ref", "church_tax", "tax_state"] as const;

export default function OwnersPage() {
  const owners = useLoad(loadOwners);
  const [editing, setEditing] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  return (
    <section>
      <h1>Eigentümer</h1>
      <p className="muted">
        Die Personen, denen die Immobilien gehören. Die Anteile je Immobilie stehen dort unter „Eigentümer“.
      </p>
      {saved ? <Notice>{saved}</Notice> : null}
      <Loading state={owners}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Eigentümer.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Steuer-ID-Referenz</th>
                    <th>Kirchensteuer</th>
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
                        <td>{owner.church_tax && owner.tax_state ? `ja (${STATE_CODE[owner.tax_state]})` : "nein"}</td>
                        <td>
                          <button
                            type="button"
                            className="link"
                            onClick={() => {
                              setSaved(null);
                              setEditing(owner.id);
                            }}
                          >
                            Bearbeiten
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
      <h2 id="einkommen">Einkommen</h2>
      <div className="card">
        <Loading state={owners}>
          {(list) =>
            list.length === 0 ? (
              <p className="muted">Zuerst die Eigentümer anlegen, dann ihr Einkommen eintragen.</p>
            ) : (
              <IncomeSection owners={list} />
            )
          }
        </Loading>
      </div>
      <h2>Eigentümer anlegen</h2>
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
      values.take("tax_state", { ok: false, message: "Für die Kirchensteuer bitte das Bundesland wählen." });
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
        onDone("Eigentümer gespeichert.");
      } else {
        await ok(api.POST("/api/owners", { body }));
        onDone("Eigentümer angelegt.");
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
          label="Name"
          description="Der Name der Eigentümerin oder des Eigentümers, wie im Kaufvertrag oder Grundbuch."
          errors={form.of("full_name")}
        >
          <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id={`${prefix}-tax`}
          label="Steuer-ID-Referenz"
          hint="Ein Verweis, z. B. „Steuer-ID Luis (Ordner Steuern)“, nicht die Nummer selbst."
          description="Ein Verweis darauf, wo Sie die steuerliche Identifikationsnummer finden; die Nummer selbst gehört nicht hierher."
          errors={form.of("tax_id_ref")}
        >
          <input type="text" value={taxIdRef} onChange={(e) => setTaxIdRef(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${prefix}-state`}
          label="Bundesland"
          description="Das Bundesland des Wohnsitzes. Es bestimmt den Kirchensteuersatz: 8 % in Bayern und Baden-Württemberg, sonst 9 %."
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
        Zahlt Kirchensteuer
      </label>
      <div className="row">
        <Submit busy={busy}>{owner ? "Speichern" : "Anlegen"}</Submit>
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
