"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { units } from "@/i18n/units";

import { UnitForm } from "../../UnitForm";

export default function EditUnitPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: id } } })), [id]);
  const unit = useLoad(load);
  const t = useT(units);
  return (
    <section>
      <Loading state={unit}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: `/units/${data.id}`, label: data.label }, { label: t.edit }]} />
            <h1>{t.editTitle}</h1>
            <div className="card">
              <UnitForm propertyId={data.property_id} unit={data} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
