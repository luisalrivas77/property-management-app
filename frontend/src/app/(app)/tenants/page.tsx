"use client";

// Mieter: every tenant of the workspace (erased ones flagged), and "Mieter anlegen".
import Link from "next/link";

import { api, ok } from "@/api/client";
import { tenantName } from "@/components/entities";
import { formatDate } from "@/components/format";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadTenants = () => ok(api.GET("/api/tenants"));

export default function TenantsPage() {
  const tenants = useLoad(loadTenants);
  return (
    <section>
      <div className="page-head">
        <h1>Mieter</h1>
        <span className="spacer" />
        <Link href="/tenants/new" className="button-link">
          Mieter anlegen
        </Link>
      </div>
      <Loading state={tenants}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Mieter.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>E-Mail</th>
                    <th>Telefon</th>
                    <th>IBAN</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((tenant) => (
                    <tr key={tenant.id}>
                      <td>
                        <Link href={`/tenants/${tenant.id}`}>{tenantName(tenant)}</Link>
                        {tenant.erased_at ? <span className="muted"> (gelöscht am {formatDate(tenant.erased_at)})</span> : null}
                      </td>
                      <td>{tenant.email ?? "–"}</td>
                      <td>{tenant.phone ?? "–"}</td>
                      <td>{tenant.iban_masked ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
    </section>
  );
}
