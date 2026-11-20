"use client";

// Mieter: every tenant of the workspace (erased ones flagged), and "Mieter anlegen".
// Phase 10 (mockup 25): one row per tenant (name, then e-mail and phone, the masked IBAN) that
// opens the tenant's page, so nothing scrolls sideways on a phone.
import Link from "next/link";

import { api, ok } from "@/api/client";
import { tenantName } from "@/components/entities";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { tenants as messages } from "@/i18n/tenants";
import { Icon } from "@/lib/icons";

import "@/components/pageRows.css";

const loadTenants = () => ok(api.GET("/api/tenants"));

export default function TenantsPage() {
  const t = useT(messages);
  const f = useFormat();
  const tenants = useLoad(loadTenants);
  return (
    <section>
      <div className="page-head">
        <h1>{t.list}</h1>
        <span className="spacer" />
        <Link href="/tenants/new" className="button-link">
          {t.create}
        </Link>
      </div>
      <Loading state={tenants}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">{t.empty}</p>
          ) : (
            <ul className="detail-rows" aria-label={t.list}>
              {list.map((tenant) => {
                const contact = [tenant.email, tenant.phone].filter(Boolean).join(" · ");
                return (
                  <li key={tenant.id}>
                    <Link href={`/tenants/${tenant.id}`} className="detail-row">
                      <span className="detail-row-text">
                        <span className="detail-row-title">
                          {tenantName(tenant)}
                          {tenant.erased_at ? <span className="muted">{t.erasedOn(f.date(tenant.erased_at))}</span> : null}
                        </span>
                        {contact ? <span className="detail-row-sub">{contact}</span> : null}
                      </span>
                      <span className="row-amount">{tenant.iban_masked ?? "–"}</span>
                      <Icon name="chevronRight" size={18} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )
        }
      </Loading>
    </section>
  );
}
