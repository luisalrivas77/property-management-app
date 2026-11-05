"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { UnitForm } from "@/app/(app)/units/UnitForm";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

export default function NewUnitPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: id } } })), [id]);
  const property = useLoad(load);
  return (
    <section>
      <Loading state={property}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: `/properties/${data.id}?tab=einheiten`, label: data.label }, { label: "Einheit anlegen" }]} />
            <h1>Einheit anlegen</h1>
            <div className="card">
              <UnitForm propertyId={data.id} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
