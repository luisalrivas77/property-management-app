"use client";

// Einheiten and Darlehen of a property. Each unit shows who rents it now (the tenancy in force
// today, else the next to start), or "leer". Each loan shows its year totals from the
// Tilgungsplan (GET /loans/{id}/schedule); the loan page has every payment.
// Phase 10 (mockup 25): a unit is a row (its label; area, rooms and MEA; who rents it now) that
// opens the rest in the detail panel: its figures, its tenancies and the link to its page.
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { Tenancy, Tenant, Unit } from "@/api/schemas";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { currentTenancy, partyNames } from "@/components/entities";
import { formatShare, todayIso } from "@/components/format";
import { LoanBalanceToday, LoanYearTotals } from "@/components/loans";
import { loadPropertyContext } from "@/components/propertyContext";
import { Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { formatter, translate, useFormat, useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";
import { Icon } from "@/lib/icons";

import "@/components/pageRows.css";

import { PropertyCard } from "./PropertyCard";

export function UnitsTab({ propertyId }: { propertyId: string }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const load = useCallback(() => loadPropertyContext(propertyId), [propertyId]);
  const context = useLoad(load);
  const detail = useDetail("unit");
  const today = todayIso();

  const openUnit = detail.id ? context.data?.units.find((unit) => unit.id === detail.id) : undefined;
  const panel =
    openUnit && context.data ? (
      <UnitPanel
        unit={openUnit}
        tenancies={context.data.tenancies.filter((tenancy) => tenancy.unit_id === openUnit.id)}
        tenants={context.data.tenants}
        onClose={detail.close}
      />
    ) : null;

  return (
    <DetailLayout panel={panel}>
      <PropertyCard card="einheiten" title={t.tabUnits}>
        <Loading state={context}>
          {({ units: list, tenancies, tenants }) =>
            list.length === 0 ? (
              <p className="muted">{t.noUnits}</p>
            ) : (
              <ul className="detail-rows" aria-label={t.tabUnits}>
                {list.map((unit) => {
                  const current = currentTenancy(
                    tenancies.filter((tenancy) => tenancy.unit_id === unit.id),
                    today,
                  );
                  return (
                    <li key={unit.id}>
                      <a {...detail.linkProps(unit.id)} className="detail-row">
                        <span className="detail-row-text">
                          <span className="detail-row-title">{unit.label}</span>
                          <span className="detail-row-sub">{unitFigures(unit)}</span>
                          {/* Who rents it on its own line, so long names wrap instead of widening the row. */}
                          {current ? (
                            <span className="detail-row-sub">
                              {current.upcoming
                                ? t.tenantFrom(partyNames(current.tenancy, tenants), f.date(current.tenancy.start_date))
                                : partyNames(current.tenancy, tenants)}
                            </span>
                          ) : null}
                        </span>
                        {current ? null : (
                          <span className="row-amount">
                            <span className="tag">{t.vacant}</span>
                          </span>
                        )}
                        <Icon name="chevronRight" size={18} />
                      </a>
                    </li>
                  );
                })}
              </ul>
            )
          }
        </Loading>
        <div>
          <Link href={`/properties/${propertyId}/units/new`} className="button-link">
            {t.addUnit}
          </Link>
        </div>
      </PropertyCard>
    </DetailLayout>
  );
}

/** "68,40 m² · 2,5 Zimmer · MEA 85/1000 (8,5 %)", leaving out what the unit has not. */
function unitFigures(unit: Unit): string {
  const t = translate(propertyPage);
  const f = formatter();
  return [
    f.area(unit.floor_area_m2),
    unit.rooms ? t.roomsCount(f.decimal(unit.rooms)) : null,
    unit.mea ? t.meaShort(formatShare(unit.mea)) : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");
}

function UnitPanel({ unit, tenancies, tenants, onClose }: { unit: Unit; tenancies: Tenancy[]; tenants: Tenant[]; onClose: () => void }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const current = currentTenancy(tenancies, todayIso());
  return (
    <DetailPanel title={unit.label} backLabel={t.tabUnits} onClose={onClose}>
      <Facts
        rows={[
          [t.area, f.area(unit.floor_area_m2)],
          [t.rooms, f.decimal(unit.rooms)],
          [t.mea, formatShare(unit.mea)],
          [
            t.tenants,
            current ? (
              <Link key="current" href={`/tenancies/${current.tenancy.id}`}>
                {partyNames(current.tenancy, tenants)}
                {current.upcoming ? ` ${t.statusFrom(f.date(current.tenancy.start_date))}` : ""}
              </Link>
            ) : (
              <span key="vacant" className="tag">
                {t.vacant}
              </span>
            ),
          ],
        ]}
      />
      {tenancies.length > 0 ? (
        <>
          <h3>{t.tenancies}</h3>
          <ul className="detail-rows" aria-label={t.tenancies}>
            {tenancies.map((tenancy) => (
              <li key={tenancy.id}>
                <Link href={`/tenancies/${tenancy.id}`} className="detail-row">
                  <span className="detail-row-text">
                    <span className="detail-row-title">{partyNames(tenancy, tenants)}</span>
                    <span className="detail-row-sub">
                      {t.period(f.date(tenancy.start_date), tenancy.end_date ? f.date(tenancy.end_date) : null)}
                    </span>
                  </span>
                  <Icon name="chevronRight" size={18} />
                </Link>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <div>
        <Link href={`/units/${unit.id}`} className="button-link">
          {t.openUnit}
        </Link>
      </div>
    </DetailPanel>
  );
}

export function LoansTab({ propertyId }: { propertyId: string }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/loans", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const loans = useLoad(load);
  return (
    <div className="stack">
      <Loading state={loans}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">{t.noLoans}</p>
          ) : (
            list.map((loan) => (
              <article key={loan.id} className="card stack">
                <h2>
                  <Link href={`/loans/${loan.id}`}>
                    {loan.lender}
                    {loan.contract_number ? ` · ${loan.contract_number}` : ""}
                  </Link>
                </h2>
                <LoanBalanceToday loanId={loan.id} />
                <p className="muted">
                  {t.loanTerms(
                    f.money(loan.terms.principal),
                    f.percent(loan.terms.nominal_rate),
                    f.money(loan.terms.monthly_payment),
                    f.date(loan.terms.disbursed_on),
                  )}
                  {loan.terms.fixed_until ? t.loanFixedUntil(f.date(loan.terms.fixed_until)) : ""}
                </p>
                <h3>{t.yearTotals}</h3>
                <LoanYearTotals loanId={loan.id} />
                <p>
                  <Link href={`/loans/${loan.id}`}>{t.allRates}</Link>
                </p>
              </article>
            ))
          )
        }
      </Loading>
      <div>
        <Link href={`/loans/new?property=${propertyId}`} className="button-link">
          {t.addLoan}
        </Link>
      </div>
    </div>
  );
}
