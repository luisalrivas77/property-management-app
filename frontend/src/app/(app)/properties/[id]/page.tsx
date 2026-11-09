"use client";

// Immobilie (§9.1): one page, tabs as ?tab=… links. Stammdaten (with the two date
// corrections), Eigentümer, Nebenkosten, Kaufpreisaufteilung, Einheiten, Darlehen (with the
// Tilgungsplan per loan), Unterlagen (the document checklist per tax year), Dokumente,
// Verlauf (History), Protokoll (audit trail).
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import { LinkedDocuments } from "@/components/documents";
import { TaxFreeLine } from "@/components/tax";
import { Breadcrumbs, Loading, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { ChecklistTab } from "./ChecklistTab";
import { AuditTab, HistoryTab } from "./HistoryTabs";
import { MasterDataTab } from "./MasterDataTab";
import { OwnershipTab } from "./OwnershipTab";
import { CostsTab, SplitsTab } from "./PurchaseTabs";
import { LoansTab, UnitsTab } from "./StructureTabs";

type TabKey =
  | "stammdaten"
  | "eigentuemer"
  | "nebenkosten"
  | "aufteilung"
  | "einheiten"
  | "darlehen"
  | "unterlagen"
  | "dokumente"
  | "verlauf"
  | "protokoll";

const TABS: Tab<TabKey>[] = [
  { key: "stammdaten", label: "Stammdaten" },
  { key: "eigentuemer", label: "Eigentümer" },
  { key: "nebenkosten", label: "Nebenkosten" },
  { key: "aufteilung", label: "Kaufpreisaufteilung" },
  { key: "einheiten", label: "Einheiten" },
  { key: "darlehen", label: "Darlehen" },
  { key: "unterlagen", label: "Unterlagen" },
  { key: "dokumente", label: "Dokumente" },
  { key: "verlauf", label: "Verlauf" },
  { key: "protokoll", label: "Protokoll" },
];

function isTab(value: string | null): value is TabKey {
  return TABS.some((tab) => tab.key === value);
}

export default function PropertyPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <PropertyDetail />
    </Suspense>
  );
}

function PropertyDetail() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const requested = search.get("tab");
  const tab: TabKey = isTab(requested) ? requested : "stammdaten";
  const load = useCallback(() => ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: id } } })), [id]);
  const property = useLoad(load);

  return (
    <section>
      <Loading state={property}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: "/", label: "Übersicht" }, { label: data.label }]} />
            <div className="page-head">
              <h1>{data.label}</h1>
              <span className="muted">
                {data.address.street} {data.address.house_number}, {data.address.postal_code} {data.address.city}
              </span>
              <TaxFreeLine propertyId={id} />
            </div>
            <TabBar tabs={TABS} current={tab} href={(key) => `/properties/${id}?tab=${key}`} />
            {tab === "stammdaten" ? <MasterDataTab property={data} onChanged={property.reload} /> : null}
            {tab === "eigentuemer" ? <OwnershipTab propertyId={id} justCreated={search.get("neu") === "1"} /> : null}
            {tab === "nebenkosten" ? <CostsTab propertyId={id} /> : null}
            {tab === "aufteilung" ? <SplitsTab property={data} onSelected={property.reload} /> : null}
            {tab === "einheiten" ? <UnitsTab propertyId={id} /> : null}
            {tab === "darlehen" ? <LoansTab propertyId={id} /> : null}
            {tab === "unterlagen" ? <ChecklistTab propertyId={id} /> : null}
            {tab === "dokumente" ? <LinkedDocuments targetType="property" targetId={id} /> : null}
            {tab === "verlauf" ? <HistoryTab propertyId={id} /> : null}
            {tab === "protokoll" ? <AuditTab propertyId={id} /> : null}
          </>
        )}
      </Loading>
    </section>
  );
}
