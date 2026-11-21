"use client";

// Übersicht as mockup 15-B2 (phase 10 D268): the tiles first (the net worth with the month's cash
// flow under it, a tile per kind of asset, the three key figures with their mark as a dot, Zu
// prüfen; components/overviewTiles.tsx), a tapped tile's summary in the detail panel with a button
// into its section. Every card the page showed before stays, on the page or in a panel (§5.3):
// "Was noch fehlt" (phase-3.md D136, components/overviewTodos.tsx), the coming dates (phase-5.md
// §11, GET /reminders), the phase 9 block with the reserve, the limits, the key figures and the
// net worth over time (components/healthFigures.tsx) and the property cards on the page; the
// Nettovermögen card with its split (phase 6 step 6, components/netWorth.tsx) in the net worth's
// panel and the Cashflow card (phase 6 §13, components/cashflow.tsx) in the cash flow's panel.
import Link from "next/link";

import { api, ok } from "@/api/client";
import type { Loan, Property, Tenancy, Tenant, Unit } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";
import { DetailLayout, useDetail } from "@/components/DetailPanel";
import { currentTenancy, partyNames } from "@/components/entities";
import { todayIso } from "@/components/format";
import { HealthSection } from "@/components/healthFigures";
import { PROPERTY_KIND } from "@/components/labels";
import {
  OverviewTiles,
  TilePanel,
  isTileId,
  loadHealthOnly,
  loadInbox,
  loadPaidIn,
  loadMonthCashflow,
  loadNetWorthToday,
} from "@/components/overviewTiles";
import { OverviewTodos } from "@/components/overviewTodos";
import { NextDates } from "@/components/reminders";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { overview as catalogue } from "@/i18n/overview";

interface PropertyCard {
  property: Property;
  units: { unit: Unit; tenancies: Tenancy[] }[];
  loans: Loan[];
}

interface Overview {
  cards: PropertyCard[];
  tenants: Tenant[];
}

const loadReminders = () => ok(api.GET("/api/reminders"));

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

export default function OverviewPage() {
  const { me } = useAuth();
  const t = useT(catalogue);
  const f = useFormat();
  const overview = useLoad(loadOverview);
  const reminders = useLoad(loadReminders);
  const netWorth = useLoad(loadNetWorthToday);
  const health = useLoad(loadHealthOnly);
  const cashflow = useLoad(loadMonthCashflow);
  const inbox = useLoad(loadInbox);
  const paidIn = useLoad(loadPaidIn);
  const detail = useDetail("kachel");
  const today = todayIso();
  const tiles = { netWorth, health, cashflow, inbox, paidIn };
  const properties = (overview.data?.cards ?? []).map(({ property }) => ({ id: property.id, label: property.label }));

  const panel = isTileId(detail.id) ? <TilePanel id={detail.id} data={tiles} detail={detail} properties={properties} /> : null;

  return (
    <DetailLayout panel={panel}>
      <section>
        <div className="page-head">
          <h1>{t.title}</h1>
          <span className="muted">
            {t.household(me.workspace.name)} · {t.asOf(f.date(today))}
          </span>
          <span className="spacer" />
          <Link href="/tenants" className="button-link secondary">
            {t.tenants}
          </Link>
          <Link href="/properties/new" className="button-link">
            {t.newProperty}
          </Link>
        </div>
        <OverviewTiles data={tiles} detail={detail} />
        <OverviewTodos reminders={reminders.data ?? []} onRemindersChanged={reminders.reload} />
        <Loading state={reminders}>{(list) => <NextDates reminders={list} onChanged={reminders.reload} />}</Loading>
        <HealthSection onChanged={health.reload} />
        <Loading state={overview}>
          {({ cards, tenants }) =>
            cards.length === 0 ? (
              <div className="card stack">
                <p>{t.noProperty}</p>
                <p className="muted">{t.noPropertyHint}</p>
                <p>
                  <Link href="/owners">{t.owners}</Link>
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
                      {PROPERTY_KIND[property.kind]} · {t.buildYear(property.build_year)} · {t.handover(f.date(property.purchase_date))}
                    </p>
                    <div>
                      <strong>{t.units}</strong>
                      {units.length === 0 ? (
                        <p className="muted">
                          {t.none} <Link href={`/properties/${property.id}/units/new`}>{t.newUnit}</Link>
                        </p>
                      ) : (
                        <ul>
                          {units.map(({ unit, tenancies }) => {
                            const current = currentTenancy(tenancies, today);
                            return (
                              <li key={unit.id}>
                                <Link href={`/units/${unit.id}`}>{unit.label}</Link> ({f.area(unit.floor_area_m2)}):{" "}
                                {current ? (
                                  <Link href={`/tenancies/${current.tenancy.id}`}>
                                    {partyNames(current.tenancy, tenants)}
                                    {current.upcoming ? t.from(f.date(current.tenancy.start_date)) : ""}
                                  </Link>
                                ) : (
                                  <span className="tag">{t.notLet}</span>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                    <div>
                      <strong>{t.loans}</strong>
                      {loans.length === 0 ? (
                        <p className="muted">{t.none}</p>
                      ) : (
                        <ul>
                          {loans.map((loan) => (
                            <li key={loan.id}>
                              <Link href={`/loans/${loan.id}`}>{loan.lender}</Link>: {f.money(loan.terms.principal)}
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
    </DetailLayout>
  );
}
