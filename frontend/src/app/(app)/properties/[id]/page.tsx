"use client";

// Immobilie (§9.1): one page, its sections as ?tab=… links.
// Phase 10 (mockup 25): the ten tabs became six sections of cards, each card a former tab with
// its heading: Übersicht (Stammdaten, Eigentümer, Einheiten, and the current tenancy per unit),
// Kauf & Nebenkosten (Nebenkosten, Kaufpreisaufteilung), Darlehen (with the Tilgungsplan per
// loan), Steuer-Checkliste (the document checklist per tax year; „Unterlagen“ until §7.6,
// its key still `unterlagen`), Dokumente, Verlauf (History, then the
// Protokoll). An old ?tab= key (a bookmark, a stored link; D266) opens its card's section and
// scrolls the card into view; a #card-… hash does the same for a new link
// (components/propertySections.ts). A row's details still open in the panel (#d-<kind>-<id>).
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import { LinkedDocuments } from "@/components/documents";
import { PropertyFlatRateCard } from "@/components/propertyFlatRate";
import {
  isFirstCard,
  type PropertySection,
  propertyCardId,
  propertyCardOfHash,
  resolvePropertyTab,
} from "@/components/propertySections";
import { TaxFreeLine } from "@/components/tax";
import { Breadcrumbs, Loading, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

import { ChecklistTab } from "./ChecklistTab";
import { CurrentTenancies } from "./CurrentTenancies";
import { AuditTab, HistoryTab } from "./HistoryTabs";
import { MasterDataTab } from "./MasterDataTab";
import { OwnershipTab } from "./OwnershipTab";
import { PropertyAnchor, PropertyCard } from "./PropertyCard";
import { CostsTab, SplitsTab } from "./PurchaseTabs";
import { LoansTab, UnitsTab } from "./StructureTabs";

export default function PropertyPage() {
  const t = useT(propertyPage);
  return (
    <Suspense fallback={<p className="muted">{t.loading}</p>}>
      <PropertyDetail />
    </Suspense>
  );
}

/** How long the cards above the target may still grow (their own lists load after the page). */
const SETTLE_MS = 1500;

/**
 * Scrolls the card an old ?tab= key or a #card-… hash names into view, once the page has
 * loaded and once per link: never on a plain section click, and never when a row's panel
 * opens (that changes the hash only, which this does not watch). The cards above it load their
 * lists after the page, which pushes it down, so the scroll is repeated while the page grows,
 * for at most SETTLE_MS and only until the person scrolls, taps or types themselves.
 */
function useScrollToCard(requested: string | null, loaded: boolean) {
  const done = useRef<string | null>(null);
  useEffect(() => {
    if (!loaded) return;
    // The old key first, so `?tab=nebenkosten#d-cost-<id>` still opens its panel and section.
    const card = resolvePropertyTab(requested).card ?? propertyCardOfHash(window.location.hash);
    const key = `${requested ?? ""}|${card ?? ""}`;
    if (done.current === key) return;
    done.current = key;
    if (!card || isFirstCard(card)) return;
    const scroll = () => document.getElementById(propertyCardId(card))?.scrollIntoView({ block: "start" });
    const frame = window.requestAnimationFrame(scroll);
    const observer = new ResizeObserver(() => scroll());
    observer.observe(document.body);
    const inputs = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
    const stop = () => {
      observer.disconnect();
      for (const input of inputs) window.removeEventListener(input, stop);
    };
    for (const input of inputs) window.addEventListener(input, stop, { passive: true });
    const timer = window.setTimeout(stop, SETTLE_MS);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      stop();
    };
  }, [requested, loaded]);
}

function PropertyDetail() {
  const t = useT(propertyPage);
  const sections: Tab<PropertySection>[] = [
    { key: "uebersicht", label: t.sectionOverview },
    { key: "kauf", label: t.sectionPurchase },
    { key: "darlehen", label: t.sectionLoans },
    { key: "unterlagen", label: t.sectionPaperwork },
    { key: "dokumente", label: t.sectionDocuments },
    { key: "verlauf", label: t.sectionHistory },
  ];
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const requested = search.get("tab");
  const { section } = resolvePropertyTab(requested);
  const load = useCallback(() => ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: id } } })), [id]);
  const property = useLoad(load);
  // A Nebenkosten row changed: the AfA line beside it reloads (its basis includes them).
  const [costsVersion, setCostsVersion] = useState(0);
  const bumpCosts = useCallback(() => setCostsVersion((n) => n + 1), []);
  useScrollToCard(requested, property.data !== undefined);

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
            <TabBar tabs={sections} current={section} href={(key) => `/properties/${id}?tab=${key}`} />
            {section === "uebersicht" ? (
              <div className="stack">
                <PropertyCard card="stammdaten" title={t.tabStammdaten}>
                  <MasterDataTab property={data} onChanged={property.reload} />
                </PropertyCard>
                <PropertyCard card="eigentuemer" title={t.tabOwners}>
                  <OwnershipTab propertyId={id} justCreated={search.get("neu") === "1"} />
                </PropertyCard>
                <UnitsTab propertyId={id} />
                <CurrentTenancies propertyId={id} />
                <PropertyFlatRateCard propertyId={id} />
              </div>
            ) : null}
            {section === "kauf" ? (
              <div className="stack">
                <CostsTab propertyId={id} onChanged={bumpCosts} />
                <SplitsTab property={data} onSelected={property.reload} refresh={costsVersion} />
              </div>
            ) : null}
            {section === "darlehen" ? (
              <PropertyAnchor card="darlehen">
                <LoansTab propertyId={id} />
              </PropertyAnchor>
            ) : null}
            {section === "unterlagen" ? (
              <PropertyAnchor card="unterlagen">
                <ChecklistTab propertyId={id} />
              </PropertyAnchor>
            ) : null}
            {section === "dokumente" ? (
              <PropertyCard card="dokumente" title={t.tabDocuments}>
                <LinkedDocuments targetType="property" targetId={id} />
              </PropertyCard>
            ) : null}
            {section === "verlauf" ? (
              <div className="stack">
                <PropertyCard card="verlauf" title={t.tabHistory}>
                  <HistoryTab propertyId={id} />
                </PropertyCard>
                <PropertyCard card="protokoll" title={t.tabAudit}>
                  <AuditTab propertyId={id} />
                </PropertyCard>
              </div>
            ) : null}
          </>
        )}
      </Loading>
    </section>
  );
}
