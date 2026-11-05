"use client";

// Übersicht (§9.1): the household's properties at a glance (label, address, units, the
// tenancy in force per unit, loans) and "Immobilie anlegen".
import Link from "next/link";

import { api, ok } from "@/api/client";
import type { Loan, Property, Tenancy, Tenant, Unit } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";
import { partyNames } from "@/components/entities";
import { formatArea, formatDate, formatMoney, todayIso } from "@/components/format";
import { PROPERTY_KIND } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

interface PropertyCard {
  property: Property;
  units: { unit: Unit; tenancies: Tenancy[] }[];
  loans: Loan[];
}

interface Overview {
  cards: PropertyCard[];
  tenants: Tenant[];
}

async function loadOverview(): Promise<Overview> {
  const [properties, tenants] = await Promise.all([ok(api.GET("/api/properties")), ok(api.GET("/api/tenants"))]);
  const cards = await Promise.all(
    properties.map(async (property) => {
      const path = { params: { path: { property_id: property.id } } };
      const [units, loans] = await Promise.all([
        ok(api.GET("/api/properties/{property_id}/units", path)),
        ok(api.GET("/api/properties/{property_id}/loans", path)),
      ]);
      const withTenancies = await Promise.all(
        units.map(async (unit) => ({
          unit,
          tenancies: await ok(api.GET("/api/units/{unit_id}/tenancies", { params: { path: { unit_id: unit.id } } })),
        })),
      );
      return { property, units: withTenancies, loans };
    }),
  );
  return { cards, tenants };
}

/** The tenancy in force today, else the next one to start. */
function currentTenancy(tenancies: Tenancy[], today: string): { tenancy: Tenancy; upcoming: boolean } | null {
  const running = tenancies.find((t) => t.start_date <= today && (t.end_date === null || t.end_date >= today));
  if (running) return { tenancy: running, upcoming: false };
  const next = tenancies.find((t) => t.start_date > today);
  return next ? { tenancy: next, upcoming: true } : null;
}

export default function OverviewPage() {
  const { me } = useAuth();
  const overview = useLoad(loadOverview);
  const today = todayIso();

  return (
    <section>
      <div className="page-head">
        <h1>Übersicht</h1>
        <span className="muted">Haushalt „{me.workspace.name}“</span>
        <span className="spacer" />
        <Link href="/tenants" className="button-link secondary">
          Mieter
        </Link>
        <Link href="/properties/new" className="button-link">
          Immobilie anlegen
        </Link>
      </div>
      <Loading state={overview}>
        {({ cards, tenants }) =>
          cards.length === 0 ? (
            <div className="card stack">
              <p>Noch keine Immobilie erfasst.</p>
              <p className="muted">
                Am besten zuerst die <Link href="/owners">Eigentümer</Link> anlegen, dann die Immobilie mit Kaufvertrag und Übergang.
              </p>
            </div>
          ) : (
            <div className="cards">
              {cards.map(({ property, units, loans }) => (
                <article key={property.id} className="card stack">
                  <h2>
                    <Link href={`/properties/${property.id}`}>{property.label}</Link>
                  </h2>
                  <p className="muted">
                    {property.address.street} {property.address.house_number}, {property.address.postal_code} {property.address.city}
                    <br />
                    {PROPERTY_KIND[property.kind]} · Baujahr {property.build_year} · Übergang {formatDate(property.purchase_date)}
                  </p>
                  <div>
                    <strong>Einheiten</strong>
                    {units.length === 0 ? (
                      <p className="muted">
                        Keine. <Link href={`/properties/${property.id}/units/new`}>Einheit anlegen</Link>
                      </p>
                    ) : (
                      <ul>
                        {units.map(({ unit, tenancies }) => {
                          const current = currentTenancy(tenancies, today);
                          return (
                            <li key={unit.id}>
                              <Link href={`/units/${unit.id}`}>{unit.label}</Link> ({formatArea(unit.floor_area_m2)}):{" "}
                              {current ? (
                                <Link href={`/tenancies/${current.tenancy.id}`}>
                                  {partyNames(current.tenancy, tenants)}
                                  {current.upcoming ? ` ab ${formatDate(current.tenancy.start_date)}` : ""}
                                </Link>
                              ) : (
                                <span className="muted">nicht vermietet</span>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                  <div>
                    <strong>Darlehen</strong>
                    {loans.length === 0 ? (
                      <p className="muted">Keine.</p>
                    ) : (
                      <ul>
                        {loans.map((loan) => (
                          <li key={loan.id}>
                            <Link href={`/loans/${loan.id}`}>{loan.lender}</Link>: {formatMoney(loan.terms.principal)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )
        }
      </Loading>
    </section>
  );
}
