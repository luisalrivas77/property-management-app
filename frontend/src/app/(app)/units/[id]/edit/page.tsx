"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { UnitForm } from "../../UnitForm";

export default function EditUnitPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: id } } })), [id]);
  const unit = useLoad(load);
  return (
    <section>
      <Loading state={unit}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: `/units/${data.id}`, label: data.label }, { label: "Bearbeiten" }]} />
            <h1>Einheit bearbeiten</h1>
            <div className="card">
              <UnitForm propertyId={data.property_id} unit={data} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
