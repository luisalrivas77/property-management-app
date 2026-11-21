"use client";

// Immobilie (§9.1): one page, tabs as ?tab=… links. Stammdaten (with the two date
// corrections), Eigentümer, Nebenkosten, Kaufpreisaufteilung, Einheiten, Darlehen (with the
// Tilgungsplan per loan), Unterlagen (the document checklist per tax year), Dokumente,
// Verlauf (History), Protokoll (audit trail).
// Phase 10 (mockup 25): the tab keys stay; labels come from the propertyPage catalogue (de/en),
// the wide tables became rows whose details open in the panel, and the edit form slides in.
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import { LinkedDocuments } from "@/components/documents";
import { TaxFreeLine } from "@/components/tax";
import { Breadcrumbs, Loading, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

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

const TAB_KEYS: TabKey[] = [
  "stammdaten",
  "eigentuemer",
  "nebenkosten",
  "aufteilung",
  "einheiten",
  "darlehen",
  "unterlagen",
  "dokumente",
  "verlauf",
  "protokoll",
];

function isTab(value: string | null): value is TabKey {
  return TAB_KEYS.some((key) => key === value);
}

export default function PropertyPage() {
  const t = useT(propertyPage);
  return (
    <Suspense fallback={<p className="muted">{t.loading}</p>}>
      <PropertyDetail />
    </Suspense>
  );
}

function PropertyDetail() {
  const t = useT(propertyPage);
  const tabs: Tab<TabKey>[] = [
    { key: "stammdaten", label: t.tabStammdaten },
    { key: "eigentuemer", label: t.tabOwners },
    { key: "nebenkosten", label: t.tabCosts },
    { key: "aufteilung", label: t.tabSplit },
    { key: "einheiten", label: t.tabUnits },
    { key: "darlehen", label: t.tabLoans },
    { key: "unterlagen", label: t.tabChecklist },
    { key: "dokumente", label: t.tabDocuments },
    { key: "verlauf", label: t.tabHistory },
    { key: "protokoll", label: t.tabAudit },
  ];
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
            <Breadcrumbs items={[{ href: "/properties", label: t.crumb }, { label: data.label }]} />
            <div className="page-head">
              <h1>{data.label}</h1>
              <span className="muted">
                {data.address.street} {data.address.house_number}, {data.address.postal_code} {data.address.city}
              </span>
              <TaxFreeLine propertyId={id} />
            </div>
            <TabBar tabs={tabs} current={tab} href={(key) => `/properties/${id}?tab=${key}`} />
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
