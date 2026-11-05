"use client";

// Mieter (§9.1): contact data (IBAN write-only, mask shown), documents, and erasure (GDPR).
// Erasure is refused with 409 retention_hold while the data must still be kept; the page then
// says until when, or why no date can be given yet.
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { RetentionResult, Tenant } from "@/api/schemas";
import { LinkedDocuments } from "@/components/documents";
import { tenantName } from "@/components/entities";
import { formatDate, formatDateTime } from "@/components/format";
import { RETENTION_CLASS, RETENTION_HOLD } from "@/components/labels";
import { ActionDialog, Breadcrumbs, Facts, Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { TenantForm } from "../TenantForm";

export default function TenantPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/tenants/{tenant_id}", { params: { path: { tenant_id: id } } })), [id]);
  const tenant = useLoad(load);
  const [saved, setSaved] = useState(false);

  return (
    <section>
      <Loading state={tenant}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: "/tenants", label: "Mieter" }, { label: tenantName(data) }]} />
            <h1>{tenantName(data)}</h1>
            {data.erased_at ? (
              <Notice>Die personenbezogenen Daten wurden am {formatDateTime(data.erased_at)} gelöscht.</Notice>
            ) : (
              <>
                <Facts
                  rows={[
                    ["E-Mail", data.email ?? "–"],
                    ["Telefon", data.phone ?? "–"],
                    ["IBAN", data.iban_masked ?? "–"],
                  ]}
                />
                <h2>Bearbeiten</h2>
                {saved ? <Notice>Gespeichert.</Notice> : null}
                <div className="card">
                  <TenantForm
                    key={data.updated_at}
                    tenant={data}
                    onSaved={() => {
                      setSaved(true);
                      tenant.reload();
                    }}
                  />
                </div>
              </>
            )}
            <h2>Dokumente</h2>
            <LinkedDocuments targetType="tenant" targetId={data.id} />
            {data.erased_at ? null : (
              <>
                <h2>Daten löschen</h2>
                <EraseTenant tenant={data} onErased={tenant.reload} />
              </>
            )}
          </>
        )}
      </Loading>
    </section>
  );
}

function retentionExplanation(result: RetentionResult): string {
  const kind = result.retention_class ? ` (${RETENTION_CLASS[result.retention_class]})` : "";
  if (result.retain_until) return `Die Daten müssen bis ${formatDate(result.retain_until)} aufbewahrt werden${kind}.`;
  if (result.hold) return `Die Daten müssen aufbewahrt werden, ${RETENTION_HOLD[result.hold]}${kind}.`;
  return "Die Aufbewahrungsfrist läuft noch.";
}

function EraseTenant({ tenant, onErased }: { tenant: Tenant; onErased: () => void }) {
  return (
    <div className="stack">
      <p className="muted">
        Löscht Name, Kontaktdaten und IBAN. Der Verlauf bleibt, er enthält keine personenbezogenen Daten. Möglich, sobald die
        Aufbewahrungsfrist (Mietende + 4 Jahre) abgelaufen ist, für Interessenten ohne Mietverhältnis, oder nach dem Auszug auf
        Wunsch des Mieters.
      </p>
      <ActionDialog label="Daten löschen …" title="Mieterdaten löschen" buttonClass="danger">
        {(close) => (
          <EraseForm
            tenant={tenant}
            onDone={() => {
              close();
              onErased();
            }}
          />
        )}
      </ActionDialog>
    </div>
  );
}

function EraseForm({ tenant, onDone }: { tenant: Tenant; onDone: () => void }) {
  const [onRequest, setOnRequest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function erase() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/tenants/{tenant_id}/erase", { params: { path: { tenant_id: tenant.id } }, body: { on_request: onRequest } }));
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const hold = error instanceof ApiError && error.kind === "retention_hold" ? error.body?.result : null;
  return (
    <div className="stack">
      <p>Die personenbezogenen Daten von „{tenantName(tenant)}“ werden endgültig gelöscht.</p>
      <label className="inline">
        <input type="checkbox" checked={onRequest} onChange={(e) => setOnRequest(e.target.checked)} />
        Auf Wunsch des Mieters (Art. 17 DSGVO), nach dem Auszug
      </label>
      <div>
        <button type="button" className="danger" onClick={() => void erase()} disabled={busy}>
          {busy ? "Löscht …" : "Endgültig löschen"}
        </button>
      </div>
      {hold ? (
        <div className="notice notice-warn" role="alert">
          <p>Löschen noch nicht möglich. {retentionExplanation(hold)}</p>
        </div>
      ) : (
        <ErrorNotice error={error} />
      )}
    </div>
  );
}
