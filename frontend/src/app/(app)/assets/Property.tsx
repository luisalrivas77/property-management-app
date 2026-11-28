"use client";

// Anlagen › Immobilie (phase 10 §7.6, D288): the property's Planung moved in from Planung, figure
// for figure, on the same endpoints (components/planning.tsx: the actual years, the projection to
// a sale with its levers in the URL, the sale and its bridge, the saved scenarios), under four
// cards: Eigenkapital (GET /assets/performance's property estimate less its loans, the net
// worth's own figures), „Geld zurück“ (GET /properties/payback's `cash_back` of `paid_in`), the
// yearly return to the sale (the projection's `sale.irr`) and „Steuerfrei verkaufbar ab“ (the
// projection's `sale.tax_free_from`, ten years after the notarised purchase). With several
// properties, one card per property, each opening its own Planung. Every figure is the server's.
// The old links (/planning?section=immobilie, ?immobilie=<id>) land here with their levers.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { PlanningScreen } from "@/components/planning";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { assets } from "@/i18n/assets";

import { type AssetsPerformance, loadPerformance, percentOne } from "./Performance";

type PropertiesPayback = components["schemas"]["PropertiesPayback"];
type PropertyPayback = components["schemas"]["PropertyPayback"];
type Projection = components["schemas"]["Projection"];

/** „X € von Y € eigenem Geld zurück“: the payback read's own figures, nothing summed here. */
export function MoneyBack({ payback }: { payback: Pick<PropertiesPayback | PropertyPayback, "cash_back" | "paid_in"> }) {
  const t = useT(assets);
  const f = useFormat();
  if (payback.cash_back == null || payback.paid_in == null) return <p className="planning-kpi-sub">{t.moneyBackNone}</p>;
  return <p className="planning-kpi-sub">{t.moneyBackOf(f.money(payback.cash_back), f.money(payback.paid_in))}</p>;
}

export function PropertyTab() {
  const search = useSearchParams();
  const requested = search.get("immobilie");
  const load = useCallback(async () => {
    const [properties, performance, payback] = await Promise.all([
      ok(api.GET("/api/properties")),
      loadPerformance(),
      ok(api.GET("/api/properties/payback")).catch(() => null),
    ]);
    return { properties, performance, payback };
  }, []);
  const state = useLoad(load);
  const t = useT(assets);
  return (
    <Loading state={state}>
      {({ properties, performance, payback }) => {
        const property = properties.find((p) => p.id === requested) ?? properties[0];
        if (!property) {
          return (
            <section className="card stack">
              <p className="muted">{t.noProperty}</p>
              <p>
                <Link href="/properties/new">{t.openProperties}</Link>
              </p>
            </section>
          );
        }
        const perProperty = payback?.properties ?? [];
        return (
          <div className="stack">
              {properties.length > 1 ? (
                <div className="planning-kpis">
                  {properties.map((p) => {
                    const own = perProperty.find((row) => row.property_id === p.id);
                    return (
                      <Link
                        key={p.id}
                        href={`/assets?tab=immobilie&immobilie=${p.id}`}
                        className="planning-kpi asset-kind-card"
                        aria-current={p.id === property.id ? "true" : undefined}
                      >
                        <p className="planning-kpi-label">{p.label} ›</p>
                        {own ? <MoneyBack payback={own} /> : null}
                      </Link>
                    );
                  })}
                </div>
              ) : null}
              <PlanningScreen
                key={property.id}
                property={property}
                properties={properties}
                head={(projection) => (
                  <PropertyKpis
                    performance={performance}
                    payback={properties.length > 1 ? (perProperty.find((row) => row.property_id === property.id) ?? null) : payback}
                    projection={projection}
                    showEquity={properties.length === 1}
                  />
                )}
              />
          </div>
        );
      }}
    </Loading>
  );
}

function PropertyKpis({
  performance,
  payback,
  projection,
  showEquity,
}: {
  performance: AssetsPerformance;
  payback: Pick<PropertiesPayback | PropertyPayback, "cash_back" | "paid_in"> | null;
  projection: Projection | undefined;
  /** The equity is the household's over every property; shown on its own only for one. */
  showEquity: boolean;
}) {
  const t = useT(assets);
  const f = useFormat();
  const sale = projection?.sale;
  return (
    <div className="planning-kpis">
      {showEquity ? (
        <div className="planning-kpi planning-kpi-main" role="group" aria-label={t.equityTitle}>
          <p className="planning-kpi-label">{t.equityTitle}</p>
          <p className="planning-kpi-value asset-total-value">{f.money(performance.property_equity_eur)}</p>
          <p className="planning-kpi-sub">{t.equitySub(f.money(performance.property_estimate_eur), f.money(performance.property_loans_eur))}</p>
        </div>
      ) : null}
      <div className="planning-kpi" role="group" aria-label={t.moneyBack}>
        <p className="planning-kpi-label">{t.moneyBack}</p>
        {payback ? <MoneyBack payback={payback} /> : <p className="planning-kpi-sub">{t.moneyBackNone}</p>}
      </div>
      {sale ? (
        <div className="planning-kpi" role="group" aria-label={t.returnToSale}>
          <p className="planning-kpi-label">{t.returnToSale}</p>
          <p className="planning-kpi-value">{sale.irr != null ? percentOne(f, sale.irr) : "–"}</p>
          <p className="planning-kpi-sub">{t.returnToSaleSub(Number(sale.sale_date.slice(0, 4)))}</p>
        </div>
      ) : null}
      {sale?.tax_free_from ? (
        <div className="planning-kpi" role="group" aria-label={t.taxFreeSaleFrom}>
          <p className="planning-kpi-label">{t.taxFreeSaleFrom}</p>
          <p className="planning-kpi-value">{f.date(sale.tax_free_from)}</p>
          <p className="planning-kpi-sub">{t.taxFreeSaleSub}</p>
        </div>
      ) : null}
    </div>
  );
}
