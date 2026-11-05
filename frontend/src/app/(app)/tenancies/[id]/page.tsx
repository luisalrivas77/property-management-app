"use client";

// Mietverhältnis (§9.1): parties and terms, the Mietstaffel (superseded rows struck through,
// "korrigieren" per row, "Mietänderung"), the actions Kaution erhalten, Kündigung, Auszug and
// Einzug korrigieren, and the linked documents. The move-in date changes only through its
// correction; the page says when it was corrected (from History, §9.4).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { Property, Tenancy, Tenant, Unit } from "@/api/schemas";
import { DateCorrectionForm } from "@/components/corrections";
import { LinkedDocuments } from "@/components/documents";
import { tenantName } from "@/components/entities";
import { formatDate, formatMoney, todayIso } from "@/components/format";
import { findMilestone } from "@/components/history";
import { DEPOSIT_FORM, RENT_MODEL, TENANCY_ROLE } from "@/components/labels";
import { ActionDialog, Breadcrumbs, CorrectedNote, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { DepositForm, EndForm, NoticeForm } from "./actions";
import { RentSchedule } from "./RentSchedule";

interface TenancyData {
  tenancy: Tenancy;
  unit: Unit;
  property: Property;
  tenants: Tenant[];
}

export default function TenancyPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(async (): Promise<TenancyData> => {
    const tenancy = await ok(api.GET("/api/tenancies/{tenancy_id}", { params: { path: { tenancy_id: id } } }));
    const unit = await ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: tenancy.unit_id } } }));
    const [property, tenants] = await Promise.all([
      ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: unit.property_id } } })),
      ok(api.GET("/api/tenants")),
    ]);
    return { tenancy, unit, property, tenants };
  }, [id]);
  const data = useLoad(load);

  return (
    <section>
      <Loading state={data}>
        {(found) => <TenancyDetail {...found} onChanged={data.reload} />}
      </Loading>
    </section>
  );
}

function TenancyDetail({ tenancy, unit, property, tenants, onChanged }: TenancyData & { onChanged: () => void }) {
  const loadHistory = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/history", {
          params: { path: { property_id: property.id }, query: { unit_id: unit.id } },
        }),
      ),
    [property.id, unit.id],
  );
  const loadDeposits = useCallback(
    () => ok(api.GET("/api/events", { params: { query: { entity_type: "tenancy", entity_id: tenancy.id } } })),
    [tenancy.id],
  );
  const history = useLoad(loadHistory);
  const events = useLoad(loadDeposits);
  const moveIn = findMilestone(history.data, "tenancy.started", tenancy.id);
  const deposits = (events.data ?? []).filter((event) => event.type === "tenancy.deposit_received");
  const ended = tenancy.end_date !== null && tenancy.end_date < todayIso();
  const byId = new Map(tenants.map((tenant) => [tenant.id, tenant]));
  const path = { params: { path: { tenancy_id: tenancy.id } } };

  function changed() {
    onChanged();
    history.reload();
    events.reload();
  }

  return (
    <>
      <Breadcrumbs
        items={[
          { href: `/properties/${property.id}`, label: property.label },
          { href: `/units/${unit.id}`, label: unit.label },
          { label: "Mietverhältnis" },
        ]}
      />
      <h1>
        Mietverhältnis {unit.label}
        {ended ? <span className="muted"> (beendet)</span> : null}
      </h1>
      <Facts
        rows={[
          [
            "Mieter",
            <>
              {tenancy.parties.map((party, index) => (
                <span key={party.tenant_id}>
                  {index > 0 ? ", " : ""}
                  <Link href={`/tenants/${party.tenant_id}`}>{tenantName(byId.get(party.tenant_id))}</Link> ({TENANCY_ROLE[party.role]})
                </span>
              ))}
            </>,
          ],
          ["Vertragsdatum", formatDate(tenancy.signed_on)],
          [
            "Einzug",
            <>
              {formatDate(tenancy.start_date)}
              {moveIn?.corrected && moveIn.original_effective_date ? (
                <CorrectedNote original={formatDate(moveIn.original_effective_date)} />
              ) : null}
            </>,
          ],
          ["Befristet bis", tenancy.fixed_term_until ? formatDate(tenancy.fixed_term_until) : "unbefristet"],
          ["Mietmodell", RENT_MODEL[tenancy.rent_model]],
          ["Kündigung erhalten/ausgesprochen am", formatDate(tenancy.notice_given_on)],
          ["Auszug", formatDate(tenancy.end_date)],
          [
            "Kaution",
            tenancy.deposit_form === "none"
              ? DEPOSIT_FORM.none
              : `${formatMoney(tenancy.deposit_amount)}, ${DEPOSIT_FORM[tenancy.deposit_form]}`,
          ],
          ["Kautionskonto", [tenancy.deposit_bank, tenancy.deposit_iban_masked].filter(Boolean).join(", ") || "–"],
          [
            "Kaution erhalten",
            deposits.length === 0
              ? "–"
              : deposits
                  .map((event) => {
                    const amount = event.payload.amount;
                    return `${formatMoney(typeof amount === "string" ? amount : null)} am ${formatDate(event.effective_date)}`;
                  })
                  .join("; "),
          ],
        ]}
      />
      <div className="row section">
        <ActionDialog label="Einzug korrigieren" title="Einzugsdatum korrigieren">
          {(close) => (
            <DateCorrectionForm
              idPrefix="move-in"
              label="Richtiges Einzugsdatum"
              current={tenancy.start_date}
              onSubmit={(values) => ok(api.POST("/api/tenancies/{tenancy_id}/corrections/move-in", { ...path, body: { new_date: values.new_date } }))}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
        <ActionDialog label="Kaution erhalten" title="Kaution erhalten">
          {(close) => (
            <DepositForm
              tenancy={tenancy}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
        <ActionDialog label="Kündigung" title="Kündigung erfassen" disabled={tenancy.notice_given_on != null || tenancy.end_date != null}>
          {(close) => (
            <NoticeForm
              tenancy={tenancy}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
        <ActionDialog label="Auszug" title="Auszug erfassen" disabled={tenancy.end_date != null}>
          {(close) => (
            <EndForm
              tenancy={tenancy}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
      </div>
      <h2>Mietstaffel</h2>
      <RentSchedule tenancy={tenancy} onChanged={() => history.reload()} />
      <h2>Dokumente</h2>
      <LinkedDocuments targetType="tenancy" targetId={tenancy.id} />
    </>
  );
}
