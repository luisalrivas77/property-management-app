"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { accountForms } from "@/i18n/accountForms";
import { pages } from "@/i18n/pages";

import { AccountForm } from "../../AccountForm";

export default function EditAccountPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/accounts/{account_id}", { params: { path: { account_id: id } } })), [id]);
  const account = useLoad(load);
  const t = useT(accountForms);
  const p = useT(pages);
  return (
    <section>
      <Loading state={account}>
        {(data) => (
          <>
            <Breadcrumbs
              items={[
                { href: "/accounts", label: t.breadcrumbAccounts },
                { href: `/accounts/${data.id}`, label: data.label },
                { label: t.edit },
              ]}
            />
            <h1>{p.editAccount}</h1>
            <div className="card">
              <AccountForm account={data} />
            </div>
          </>
        )}
      </Loading>
    </section>
  );
}
