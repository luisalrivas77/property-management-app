"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { AccountForm } from "../../AccountForm";

export default function EditAccountPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/accounts/{account_id}", { params: { path: { account_id: id } } })), [id]);
  const account = useLoad(load);
  return (
    <section>
      <Loading state={account}>
        {(data) => (
          <>
            <Breadcrumbs
              items={[
                { href: "/accounts", label: "Konten" },
                { href: `/accounts/${data.id}`, label: data.label },
                { label: "Bearbeiten" },
              ]}
            />
            <h1>Konto bearbeiten</h1>
            <div className="card">
              <AccountForm account={data} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
