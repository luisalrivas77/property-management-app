"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { UnitForm } from "@/app/(app)/units/UnitForm";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { units } from "@/i18n/units";

export default function NewUnitPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: id } } })), [id]);
  const property = useLoad(load);
  const t = useT(units);
  return (
    <section>
      <Loading state={property}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: `/properties/${data.id}?tab=uebersicht#card-einheiten`, label: data.label }, { label: t.create }]} />
            <h1>{t.create}</h1>
            <div className="card">
              <UnitForm propertyId={data.id} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
