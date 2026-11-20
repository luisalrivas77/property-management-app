"use client";

// Mieter (§9.1): contact data (IBAN write-only, mask shown), the tenancies they are a party
// of (GET /tenants/{id}/tenancies, with property, unit and role), documents, and erasure
// (GDPR). Erasure is refused with 409 retention_hold while the data must still be kept; the
// page then says until when, or why no date can be given yet.
//
// Phase 10 (mockup 25): a tenancy is a row (unit, property, role and move-in, its status) that
// opens the rest in the detail panel; "Bearbeiten" slides the tenant form in from the right (its
// PATCH body unchanged).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { Property, RetentionResult, Tenancy, Tenant, Unit } from "@/api/schemas";
import { LinkedDocuments } from "@/components/documents";
import { tenancyStatus, tenantName } from "@/components/entities";
import { DetailLayout, DetailPanel, useDetail, type Detail } from "@/components/DetailPanel";
import { todayIso } from "@/components/format";
import { RETENTION_CLASS, RETENTION_HOLD, TENANCY_ROLE } from "@/components/labels";
import { SheetDialog } from "@/components/SheetDialog";
import { ActionDialog, Breadcrumbs, Facts, Loading, Notice } from "@/components/ui";
import { useLoad, type Loaded } from "@/components/useLoad";
import { formatter, translate, useFormat, useT } from "@/i18n";
import { tenants } from "@/i18n/tenants";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { Icon } from "@/lib/icons";

import "@/components/pageRows.css";

import { TenantForm } from "../TenantForm";

export default function TenantPage() {
  const { id } = useParams<{ id: string }>();
  const t = useT(tenants);
  const f = useFormat();
  const load = useCallback(() => ok(api.GET("/api/tenants/{tenant_id}", { params: { path: { tenant_id: id } } })), [id]);
  const tenant = useLoad(load);
  const loadRows = useCallback(() => loadTenantTenancies(id), [id]);
  const rows = useLoad(loadRows);
  const detail = useDetail("tenancy");
  const [saved, setSaved] = useState(false);

  const open = detail.id ? rows.data?.find((row) => row.tenancy.id === detail.id) : undefined;
  const panel = open ? <TenancyPanel row={open} tenantId={id} onClose={detail.close} /> : null;

  return (
    <section>
      <DetailLayout panel={panel}>
        <Loading state={tenant}>
          {(data) => (
            <>
              <Breadcrumbs items={[{ href: "/tenants", label: t.list }, { label: tenantName(data) }]} />
              <div className="page-head">
                <h1>{tenantName(data)}</h1>
                <span className="spacer" />
                {data.erased_at ? null : (
                  <SheetDialog label={t.edit} title={t.editTitle}>
                    {(close) => (
                      <TenantForm
                        key={data.updated_at}
                        tenant={data}
                        onSaved={() => {
                          close();
                          setSaved(true);
                          tenant.reload();
                        }}
                      />
                    )}
                  </SheetDialog>
                )}
              </div>
              {data.erased_at ? (
                <Notice>{t.erased(f.dateTime(data.erased_at))}</Notice>
              ) : (
                <>
                  {saved ? <Notice>{t.saved}</Notice> : null}
                  <Facts
                    rows={[
                      [t.email, data.email ?? "–"],
                      [t.phone, data.phone ?? "–"],
                      [t.iban, data.iban_masked ?? "–"],
                    ]}
                  />
                </>
              )}
              <h2>{t.tenancies}</h2>
              <TenantTenancies rows={rows} tenantId={data.id} detail={detail} />
              <h2>{t.documents}</h2>
              <LinkedDocuments targetType="tenant" targetId={data.id} />
              {data.erased_at ? null : (
                <>
                  <h2>{t.erase}</h2>
                  <EraseTenant tenant={data} onErased={tenant.reload} />
                </>
              )}
            </>
          )}
        </Loading>
      </DetailLayout>
    </section>
  );
}

interface TenancyRow {
  tenancy: Tenancy;
  unit: Unit | undefined;
  property: Property | undefined;
}

/** The tenant's tenancies with their unit and property: one call per distinct unit and
 * property, a handful at this scale. */
async function loadTenantTenancies(tenantId: string): Promise<TenancyRow[]> {
  const tenancies = await ok(api.GET("/api/tenants/{tenant_id}/tenancies", { params: { path: { tenant_id: tenantId } } }));
  const units = await Promise.all(
    [...new Set(tenancies.map((t) => t.unit_id))].map((unitId) =>
      ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: unitId } } })),
    ),
  );
  const properties = await Promise.all(
    [...new Set(units.map((u) => u.property_id))].map((propertyId) =>
      ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: propertyId } } })),
    ),
  );
  const unitById = new Map(units.map((unit) => [unit.id, unit]));
  const propertyById = new Map(properties.map((property) => [property.id, property]));
  return tenancies.map((tenancy) => {
    const unit = unitById.get(tenancy.unit_id);
    return { tenancy, unit, property: unit && propertyById.get(unit.property_id) };
  });
}

function roleOf(row: TenancyRow, tenantId: string): string {
  const role = row.tenancy.parties.find((party) => party.tenant_id === tenantId)?.role;
  return role ? TENANCY_ROLE[role] : "–";
}

/** The tenancies as rows: the unit, then property, role and move-in, and the status; the rest
 * (links to the property and the unit, the move-out) in the panel. */
function TenantTenancies({ rows, tenantId, detail }: { rows: Loaded<TenancyRow[]>; tenantId: string; detail: Detail }) {
  const t = useT(tenants);
  const f = useFormat();
  const today = todayIso();
  return (
    <Loading state={rows}>
      {(list) =>
        list.length === 0 ? (
          <p className="muted">{t.noTenancy}</p>
        ) : (
          <ul className="detail-rows" aria-label={t.tenancies}>
            {list.map((row) => (
              <li key={row.tenancy.id}>
                <a {...detail.linkProps(row.tenancy.id)} className="detail-row">
                  <span className="detail-row-text">
                    <span className="detail-row-title">{row.unit?.label ?? "–"}</span>
                    <span className="detail-row-sub">
                      {t.rowSub(row.property?.label ?? "–", roleOf(row, tenantId), f.date(row.tenancy.start_date))}
                    </span>
                  </span>
                  <span className="row-amount">{tenancyStatus(row.tenancy, today)}</span>
                  <Icon name="chevronRight" size={18} />
                </a>
              </li>
            ))}
          </ul>
        )
      }
    </Loading>
  );
}

function TenancyPanel({ row, tenantId, onClose }: { row: TenancyRow; tenantId: string; onClose: () => void }) {
  const t = useT(tenants);
  const f = useFormat();
  const { tenancy, unit, property } = row;
  return (
    <DetailPanel title={unit?.label ?? t.tenancies} backLabel={t.tenancies} onClose={onClose}>
      <Facts
        rows={[
          [t.property, property ? <Link href={`/properties/${property.id}`}>{property.label}</Link> : "–"],
          [t.unit, unit ? <Link href={`/units/${unit.id}`}>{unit.label}</Link> : "–"],
          [t.role, roleOf(row, tenantId)],
          [t.moveIn, f.date(tenancy.start_date)],
          [t.moveOut, f.date(tenancy.end_date)],
          [t.status, <Link key="status" href={`/tenancies/${tenancy.id}`}>{tenancyStatus(tenancy, todayIso())}</Link>],
        ]}
      />
    </DetailPanel>
  );
}

function retentionExplanation(result: RetentionResult): string {
  const t = translate(tenants);
  const f = formatter();
  const kind = result.retention_class ? t.retainClass(RETENTION_CLASS[result.retention_class]) : "";
  if (result.retain_until) return t.retainUntil(f.date(result.retain_until), kind);
  if (result.hold) return t.retainHold(RETENTION_HOLD[result.hold], kind);
  return t.retainRunning(kind);
}

function EraseTenant({ tenant, onErased }: { tenant: Tenant; onErased: () => void }) {
  const t = useT(tenants);
  return (
    <div className="stack">
      <p className="muted">{t.eraseIntro}</p>
      <ActionDialog label={t.eraseButton} title={t.eraseTitle} buttonClass="danger">
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
  const t = useT(tenants);
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
      <p>{t.eraseConfirm(tenantName(tenant))}</p>
      <label className="inline">
        <input type="checkbox" checked={onRequest} onChange={(e) => setOnRequest(e.target.checked)} />
        {t.onRequest}
      </label>
      <div>
        <button type="button" className="danger" onClick={() => void erase()} disabled={busy}>
          {busy ? t.erasing : t.eraseNow}
        </button>
      </div>
      {hold ? (
        <div className="notice notice-warn" role="alert">
          <p>{t.notYet(retentionExplanation(hold))}</p>
        </div>
      ) : (
        <ErrorNotice error={error} />
      )}
    </div>
  );
}
