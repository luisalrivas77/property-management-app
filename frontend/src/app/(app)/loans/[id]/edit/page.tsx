"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { LoanForm } from "../../LoanForm";

export default function EditLoanPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/loans/{loan_id}", { params: { path: { loan_id: id } } })), [id]);
  const loan = useLoad(load);
  return (
    <section>
      <Loading state={loan}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: `/loans/${data.id}`, label: data.lender }, { label: "Bearbeiten" }]} />
            <h1>Darlehen bearbeiten</h1>
            <div className="card">
              <LoanForm propertyId={data.property_id} loan={data} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
