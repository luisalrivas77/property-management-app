"use client";

// Einheit (§9.1): its data, its tenancies with "Mietverhältnis anlegen", History scoped to
// the unit (which keeps the property-level milestones), and its documents.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { Property, Tenant, Unit } from "@/api/schemas";
import { LinkedDocuments } from "@/components/documents";
import { partyNames } from "@/components/entities";
import { formatArea, formatDate, formatDecimal, formatShare } from "@/components/format";
import { HistoryTimeline } from "@/components/history";
import { historySubject, loadPropertyContext } from "@/components/propertyContext";
import { RENT_MODEL } from "@/components/labels";
import { Breadcrumbs, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

interface UnitData {
  unit: Unit;
  property: Property;
  tenants: Tenant[];
}

export default function UnitPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(async (): Promise<UnitData> => {
    const unit = await ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: id } } }));
    const [property, tenants] = await Promise.all([
      ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: unit.property_id } } })),
      ok(api.GET("/api/tenants")),
    ]);
    return { unit, property, tenants };
  }, [id]);
  const data = useLoad(load);

  return (
    <section>
      <Loading state={data}>
        {({ unit, property, tenants }) => (
          <>
            <Breadcrumbs
              items={[
                { href: `/properties/${property.id}?tab=einheiten`, label: property.label },
                { label: unit.label },
              ]}
            />
            <div className="page-head">
              <h1>{unit.label}</h1>
              <span className="spacer" />
              <Link href={`/units/${unit.id}/edit`} className="button-link secondary">
                Bearbeiten
              </Link>
            </div>
            <Facts
              rows={[
                ["Fläche", formatArea(unit.floor_area_m2)],
                ["Zimmer", formatDecimal(unit.rooms)],
                ["MEA", formatShare(unit.mea)],
              ]}
            />
            <h2>Mietverhältnisse</h2>
            <UnitTenancies unitId={unit.id} tenants={tenants} />
            <h2>Verlauf</h2>
            <UnitHistory propertyId={property.id} unitId={unit.id} />
            <h2>Dokumente</h2>
            <LinkedDocuments targetType="unit" targetId={unit.id} />
          </>
        )}
      </Loading>
    </section>
  );
}

function UnitTenancies({ unitId, tenants }: { unitId: string; tenants: Tenant[] }) {
  const load = useCallback(() => ok(api.GET("/api/units/{unit_id}/tenancies", { params: { path: { unit_id: unitId } } })), [unitId]);
  const tenancies = useLoad(load);
  return (
    <div className="stack">
      <Loading state={tenancies}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch kein Mietverhältnis.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Mieter</th>
                    <th>Einzug</th>
                    <th>Auszug</th>
                    <th>Mietmodell</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((tenancy) => (
                    <tr key={tenancy.id}>
                      <td>
                        <Link href={`/tenancies/${tenancy.id}`}>{partyNames(tenancy, tenants)}</Link>
                      </td>
                      <td>{formatDate(tenancy.start_date)}</td>
                      <td>{formatDate(tenancy.end_date)}</td>
                      <td>{RENT_MODEL[tenancy.rent_model]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      <div>
        <Link href={`/tenancies/new?unit=${unitId}`} className="button-link">
          Mietverhältnis anlegen
        </Link>
      </div>
    </div>
  );
}

function UnitHistory({ propertyId, unitId }: { propertyId: string; unitId: string }) {
  const loadHistory = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/history", { params: { path: { property_id: propertyId }, query: { unit_id: unitId } } })),
    [propertyId, unitId],
  );
  const loadContext = useCallback(() => loadPropertyContext(propertyId), [propertyId]);
  const history = useLoad(loadHistory);
  const context = useLoad(loadContext);
  return <Loading state={history}>{(entries) => <HistoryTimeline entries={entries} subject={historySubject(context.data)} />}</Loading>;
}
