"use client";

// Aktuelles Mietverhältnis (02 mockup, "Current tenancy"): per unit, the tenancy in force
// today (else the next one to start) with its tenants, start, Kaution and the rent row in
// force. The rent is the API's answer (GET …/rent?at=…), as on the tenancy page; nothing is
// computed here. A unit with neither gets "Mietverhältnis anlegen".
// One call per unit and per current tenancy on top of the property context: a handful at
// this scale (an ETW has one or two units).
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { RentScheduleEntry, Tenancy, Tenant, Unit } from "@/api/schemas";
import { currentTenancy, tenantName } from "@/components/entities";
import { formatDate, formatMoney, todayIso } from "@/components/format";
import { DEPOSIT_FORM, TENANCY_ROLE } from "@/components/labels";
import { loadPropertyContext } from "@/components/propertyContext";
import { Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

interface UnitTenancy {
  unit: Unit;
  current: { tenancy: Tenancy; upcoming: boolean; rent: RentScheduleEntry | null } | null;
}

async function loadCurrentTenancies(propertyId: string, today: string): Promise<{ units: UnitTenancy[]; tenants: Tenant[] }> {
  const context = await loadPropertyContext(propertyId);
  const units = await Promise.all(
    context.units.map(async (unit): Promise<UnitTenancy> => {
      const found = currentTenancy(
        context.tenancies.filter((t) => t.unit_id === unit.id),
        today,
      );
      if (!found) return { unit, current: null };
      // An upcoming tenancy has no rent in force today yet: show the rent it starts with.
      const at = found.upcoming ? found.tenancy.start_date : today;
      const rent = await ok(
        api.GET("/api/tenancies/{tenancy_id}/rent", { params: { path: { tenancy_id: found.tenancy.id }, query: { at } } }),
      );
      return { unit, current: { ...found, rent } };
    }),
  );
  return { units, tenants: context.tenants };
}

export function CurrentTenancies({ propertyId }: { propertyId: string }) {
  const today = todayIso();
  const load = useCallback(() => loadCurrentTenancies(propertyId, today), [propertyId, today]);
  const state = useLoad(load);
  return (
    <section className="stack" aria-labelledby="current-tenancy">
      <h2 id="current-tenancy">Aktuelles Mietverhältnis</h2>
      <Loading state={state}>
        {({ units, tenants }) =>
          units.length === 0 ? (
            <p className="muted">
              Noch keine Einheit. <Link href={`/properties/${propertyId}/units/new`}>Einheit anlegen</Link>
            </p>
          ) : (
            <div className="cards">
              {units.map((entry) => (
                <UnitTenancyCard key={entry.unit.id} entry={entry} tenants={tenants} />
              ))}
            </div>
          )
        }
      </Loading>
    </section>
  );
}

function UnitTenancyCard({ entry: { unit, current }, tenants }: { entry: UnitTenancy; tenants: Tenant[] }) {
  const byId = new Map(tenants.map((tenant) => [tenant.id, tenant]));
  return (
    <article className="card stack" aria-label={`Mietverhältnis ${unit.label}`}>
      <div className="row card-head">
        <h3>
          <Link href={`/units/${unit.id}`}>{unit.label}</Link>
        </h3>
        <span className="spacer" />
        {current ? <span className="badge">{statusBadge(current.tenancy, current.upcoming)}</span> : <span className="tag">leer</span>}
      </div>
      {current ? (
        <>
          <Facts
            rows={[
              [
                "Mieter",
                <>
                  {current.tenancy.parties.map((party, index) => (
                    <span key={party.tenant_id}>
                      {index > 0 ? ", " : ""}
                      <Link href={`/tenants/${party.tenant_id}`}>{tenantName(byId.get(party.tenant_id))}</Link>
                      {current.tenancy.parties.length > 1 ? ` (${TENANCY_ROLE[party.role]})` : ""}
                    </span>
                  ))}
                </>,
              ],
              ["Beginn", formatDate(current.tenancy.start_date)],
              ["Kaltmiete", current.rent ? formatMoney(current.rent.cold_rent) : "–"],
              ["NK-Vorauszahlung", current.rent ? formatMoney(current.rent.operating_costs_prepayment) : "–"],
              [
                "Kaution",
                current.tenancy.deposit_form === "none"
                  ? DEPOSIT_FORM.none
                  : `${formatMoney(current.tenancy.deposit_amount)}, ${DEPOSIT_FORM[current.tenancy.deposit_form]}`,
              ],
            ]}
          />
          <p>
            <Link href={`/tenancies/${current.tenancy.id}`}>Zum Mietverhältnis</Link>
          </p>
        </>
      ) : (
        <>
          <p className="muted">Kein laufendes Mietverhältnis.</p>
          <div>
            <Link href={`/tenancies/new?unit=${unit.id}`} className="button-link">
              Mietverhältnis anlegen
            </Link>
          </div>
        </>
      )}
    </article>
  );
}

/** "läuft · unbefristet", "läuft · befristet bis …", "ab … · unbefristet"; a recorded move-out
 * date shows as "endet am …". */
function statusBadge(tenancy: Tenancy, upcoming: boolean): string {
  const status = upcoming
    ? `ab ${formatDate(tenancy.start_date)}`
    : tenancy.end_date
      ? `endet am ${formatDate(tenancy.end_date)}`
      : "läuft";
  const term = tenancy.fixed_term_until ? `befristet bis ${formatDate(tenancy.fixed_term_until)}` : "unbefristet";
  return `${status} · ${term}`;
}
