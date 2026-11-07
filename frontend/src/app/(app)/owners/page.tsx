"use client";

// Eigentümer (§9.1): list with inline create and edit (Name, Steuer-ID-Referenz,
// Grenzsteuersatz). The marginal rate is entered in percent and stored as a fraction.
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Owner } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatPercent } from "@/components/format";
import { blankToNull, percentInput, toRate } from "@/components/input";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadOwners = () => ok(api.GET("/api/owners"));
const KEYS = ["full_name", "tax_id_ref", "marginal_tax_rate"] as const;

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
                    <th>Grenzsteuersatz</th>
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
                        <td>{formatPercent(owner.marginal_tax_rate)}</td>
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
  const [rate, setRate] = useState(percentInput(owner?.marginal_tax_rate));
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const marginalTaxRate = values.optional("marginal_tax_rate", rate, (raw) => toRate(raw, 4));
    if (!values.ok || marginalTaxRate === undefined) return form.invalid(values.messages);
    const body = { full_name: fullName.trim(), tax_id_ref: blankToNull(taxIdRef), marginal_tax_rate: marginalTaxRate };
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
          id={`${prefix}-rate`}
          label="Grenzsteuersatz (%)"
          hint="Für die Steuerwirkung, z. B. 42."
          description="Der Steuersatz, mit dem ein zusätzlicher Euro Einkommen besteuert wird, z. B. aus einem Steuerrechner. Hausbuch speichert ihn bisher nur; die geplante Renditeansicht soll damit die Steuerwirkung zeigen."
          errors={form.of("marginal_tax_rate")}
        >
          <input type="text" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} autoComplete="off" />
        </Field>
      </div>
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
