"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { PropertyForm } from "../../PropertyForm";

export default function EditPropertyPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: id } } })), [id]);
  const property = useLoad(load);

  return (
    <section>
      <Loading state={property}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: `/properties/${data.id}`, label: data.label }, { label: "Bearbeiten" }]} />
            <h1>Immobilie bearbeiten</h1>
            <div className="card">
              <PropertyForm property={data} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
