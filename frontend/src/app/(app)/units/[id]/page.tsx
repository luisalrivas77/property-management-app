"use client";

// Einheit (§9.1): its data, its tenancies with "Mietverhältnis anlegen", History scoped to
// the unit (which keeps the property-level milestones), and its documents.
// Phase 10 (mockup 25): „Bearbeiten“ slides in from the right; each tenancy is a row (its
// tenants, the period, the rent model) that opens the tenancy's page.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { Property, Tenant, Unit } from "@/api/schemas";
import { LinkedDocuments } from "@/components/documents";
import { partyNames } from "@/components/entities";
import { formatShare } from "@/components/format";
import { HistoryTimeline } from "@/components/history";
import { RENT_MODEL } from "@/components/labels";
import { historySubject, loadPropertyContext } from "@/components/propertyContext";
import { SheetDialog } from "@/components/SheetDialog";
import { MarketRentSection } from "@/components/taxInputs";
import { Breadcrumbs, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { units } from "@/i18n/units";
import { Icon } from "@/lib/icons";

import { UnitForm } from "../UnitForm";

import "@/components/pageRows.css";

interface UnitData {
  unit: Unit;
  property: Property;
  tenants: Tenant[];
}

export default function UnitPage() {
  const t = useT(units);
  const f = useFormat();
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
  // Bumped after a save, so the history reloads with the edit.
  const [version, setVersion] = useState(0);

  return (
    <section>
      <Loading state={data}>
        {({ unit, property, tenants }) => (
          <>
            <Breadcrumbs
              items={[
                { href: `/properties/${property.id}?tab=uebersicht#card-einheiten`, label: property.label },
                { label: unit.label },
              ]}
            />
            <div className="page-head">
              <h1>{unit.label}</h1>
              <span className="spacer" />
              <SheetDialog label={t.edit} title={t.editTitle}>
                {(close) => (
                  <UnitForm
                    key={unit.updated_at}
                    propertyId={property.id}
                    unit={unit}
                    onCancel={close}
                    onDone={() => {
                      close();
                      data.reload();
                      setVersion((v) => v + 1);
                    }}
                  />
                )}
              </SheetDialog>
            </div>
            <Facts
              rows={[
                [t.area, f.area(unit.floor_area_m2)],
                [t.rooms, f.decimal(unit.rooms)],
                [t.mea, formatShare(unit.mea)],
              ]}
            />
            <h2>{t.tenancies}</h2>
            <UnitTenancies unitId={unit.id} tenants={tenants} />
            <h2 id="ortsuebliche-miete">{t.marketRent}</h2>
            <MarketRentSection propertyId={property.id} unitId={unit.id} perUnit={property.kind === "mfh"} />
            <h2>{t.history}</h2>
            <UnitHistory key={version} propertyId={property.id} unitId={unit.id} />
            <h2>{t.documents}</h2>
            <LinkedDocuments targetType="unit" targetId={unit.id} />
          </>
        )}
      </Loading>
    </section>
  );
}

function UnitTenancies({ unitId, tenants }: { unitId: string; tenants: Tenant[] }) {
  const t = useT(units);
  const f = useFormat();
  const load = useCallback(() => ok(api.GET("/api/units/{unit_id}/tenancies", { params: { path: { unit_id: unitId } } })), [unitId]);
  const tenancies = useLoad(load);
  return (
    <div className="stack">
      <Loading state={tenancies}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">{t.noTenancy}</p>
          ) : (
            <ul className="detail-rows" aria-label={t.tenancies}>
              {list.map((tenancy) => (
                <li key={tenancy.id}>
                  <Link href={`/tenancies/${tenancy.id}`} className="detail-row">
                    <span className="detail-row-text">
                      <span className="detail-row-title">{partyNames(tenancy, tenants)}</span>
                      <span className="detail-row-sub">
                        {t.period(f.date(tenancy.start_date), tenancy.end_date ? f.date(tenancy.end_date) : null)}
                      </span>
                    </span>
                    <span className="row-amount">{RENT_MODEL[tenancy.rent_model]}</span>
                    <Icon name="chevronRight" size={18} />
                  </Link>
                </li>
              ))}
            </ul>
          )
        }
      </Loading>
      <div>
        <Link href={`/tenancies/new?unit=${unitId}`} className="button-link">
          {t.addTenancy}
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
