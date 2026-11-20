"use client";

// Immobilien (phase 10 step 2, interim): the menu's „Mehr“ › Immobilien had no page of its own,
// and Mieter and Eigentümer left the menu (D266), so this list holds the way to both and to each
// property. A tapped property opens its facts in the detail panel (D267), the first screen to
// use it. Step 4b replaces this page with one card per property, its tenancies and its owners'
// shares (mockup 25).
import Link from "next/link";

import { api, ok } from "@/api/client";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { properties as messages } from "@/i18n/properties";
import { Icon } from "@/lib/icons";

const loadProperties = () => ok(api.GET("/api/properties"));

export default function PropertiesPage() {
  const t = useT(messages);
  const f = useFormat();
  const state = useLoad(loadProperties);
  const detail = useDetail("property");

  return (
    <section>
      <div className="page-head">
        <h1>{t.title}</h1>
        <span className="spacer" />
        <Link href="/properties/new" className="button-link">
          {t.add}
        </Link>
      </div>
      <Loading state={state}>
        {(list) => {
          const selected = list.find((property) => property.id === detail.id);
          return (
            <DetailLayout
              panel={
                selected ? (
                  <DetailPanel title={selected.label} backLabel={t.title} onClose={detail.close}>
                    <div className="stack">
                      <dl className="facts">
                        <div>
                          <dt>{t.address}</dt>
                          <dd>
                            {selected.address.street} {selected.address.house_number}, {selected.address.postal_code}{" "}
                            {selected.address.city}
                          </dd>
                        </div>
                        <div>
                          <dt>{t.notarizedOn}</dt>
                          <dd>{f.date(selected.notarized_on)}</dd>
                        </div>
                        <div>
                          <dt>{t.purchaseDate}</dt>
                          <dd>{f.date(selected.purchase_date)}</dd>
                        </div>
                        <div>
                          <dt>{t.livingArea}</dt>
                          <dd>{f.area(selected.living_area_m2)}</dd>
                        </div>
                      </dl>
                      <Link href={`/properties/${selected.id}`} className="button-link">
                        {t.openProperty}
                      </Link>
                    </div>
                  </DetailPanel>
                ) : null
              }
            >
              <div className="stack">
                {list.length === 0 ? <p className="muted">{t.none}</p> : null}
                <ul className="detail-rows">
                  {list.map((property) => (
                    <li key={property.id}>
                      <a {...detail.linkProps(property.id)} className="detail-row">
                        <Icon name="properties" />
                        <span className="detail-row-text">
                          <span className="detail-row-title">{property.label}</span>
                          <span className="detail-row-sub">
                            {property.address.street} {property.address.house_number}, {property.address.city}
                          </span>
                        </span>
                        <Icon name="chevronRight" size={18} />
                      </a>
                    </li>
                  ))}
                  <li>
                    <Link href="/owners" className="detail-row">
                      <Icon name="household" />
                      <span className="detail-row-text">
                        <span className="detail-row-title">{t.owners}</span>
                        <span className="detail-row-sub">{t.ownersHint}</span>
                      </span>
                      <Icon name="chevronRight" size={18} />
                    </Link>
                  </li>
                  <li>
                    <Link href="/tenants" className="detail-row">
                      <Icon name="overview" />
                      <span className="detail-row-text">
                        <span className="detail-row-title">{t.tenants}</span>
                        <span className="detail-row-sub">{t.tenantsHint}</span>
                      </span>
                      <Icon name="chevronRight" size={18} />
                    </Link>
                  </li>
                </ul>
              </div>
            </DetailLayout>
          );
        }}
      </Loading>
    </section>
  );
}
