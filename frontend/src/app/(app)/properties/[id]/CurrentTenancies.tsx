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
import { todayIso } from "@/components/format";
import { DEPOSIT_FORM, TENANCY_ROLE } from "@/components/labels";
import { loadPropertyContext } from "@/components/propertyContext";
import { Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { formatter, translate, useFormat, useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

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
  const t = useT(propertyPage);
  const today = todayIso();
  const load = useCallback(() => loadCurrentTenancies(propertyId, today), [propertyId, today]);
  const state = useLoad(load);
  return (
    <section className="stack" aria-labelledby="current-tenancy">
      <h2 id="current-tenancy">{t.currentTitle}</h2>
      <Loading state={state}>
        {({ units, tenants }) =>
          units.length === 0 ? (
            <p className="muted">
              {t.noUnit} <Link href={`/properties/${propertyId}/units/new`}>{t.addUnit}</Link>
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
  const t = useT(propertyPage);
  const f = useFormat();
  const byId = new Map(tenants.map((tenant) => [tenant.id, tenant]));
  return (
    <article className="card stack" aria-label={t.cardLabel(unit.label)}>
      <div className="row card-head">
        <h3>
          <Link href={`/units/${unit.id}`}>{unit.label}</Link>
        </h3>
        <span className="spacer" />
        {current ? <span className="badge">{statusBadge(current.tenancy, current.upcoming)}</span> : <span className="tag">{t.vacant}</span>}
      </div>
      {current ? (
        <>
          <Facts
            rows={[
              [
                t.tenants,
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
              [t.start, f.date(current.tenancy.start_date)],
              [t.coldRent, current.rent ? f.money(current.rent.cold_rent) : "–"],
              [t.prepayment, current.rent ? f.money(current.rent.operating_costs_prepayment) : "–"],
              [
                t.deposit,
                current.tenancy.deposit_form === "none"
                  ? DEPOSIT_FORM.none
                  : `${f.money(current.tenancy.deposit_amount)}, ${DEPOSIT_FORM[current.tenancy.deposit_form]}`,
              ],
            ]}
          />
          <p>
            <Link href={`/tenancies/${current.tenancy.id}`}>{t.toTenancy}</Link>
          </p>
        </>
      ) : (
        <>
          <p className="muted">{t.noRunning}</p>
          <div>
            <Link href={`/tenancies/new?unit=${unit.id}`} className="button-link">
              {t.addTenancy}
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
  const t = translate(propertyPage);
  const f = formatter();
  const status = upcoming
    ? t.statusFrom(f.date(tenancy.start_date))
    : tenancy.end_date
      ? t.statusEnds(f.date(tenancy.end_date))
      : t.running;
  const term = tenancy.fixed_term_until ? t.fixedUntil(f.date(tenancy.fixed_term_until)) : t.unlimited;
  return `${status} · ${term}`;
}
