// What a property's History entries are about: its units, loans, tenancies and the tenants,
// so a line can say "Einzug · WE 3, Max Muster" instead of an id.
import { api, ok } from "@/api/client";
import type { Loan, Tenancy, Tenant, TimelineEntry, Unit } from "@/api/schemas";

import { partyNames } from "./entities";

export interface PropertyContext {
  units: Unit[];
  loans: Loan[];
  tenancies: Tenancy[];
  tenants: Tenant[];
}

export async function loadPropertyContext(propertyId: string): Promise<PropertyContext> {
  const path = { params: { path: { property_id: propertyId } } };
  const [units, loans, tenants] = await Promise.all([
    ok(api.GET("/api/properties/{property_id}/units", path)),
    ok(api.GET("/api/properties/{property_id}/loans", path)),
    ok(api.GET("/api/tenants")),
  ]);
  const tenancies = (
    await Promise.all(units.map((unit) => ok(api.GET("/api/units/{unit_id}/tenancies", { params: { path: { unit_id: unit.id } } }))))
  ).flat();
  return { units, loans, tenancies, tenants };
}

/** "WE 3, Max Muster" for a tenancy entry, the lender for a loan entry. */
export function historySubject(context: PropertyContext | undefined) {
  return (entry: TimelineEntry): string | null => {
    if (!context) return null;
    if (entry.entity_type === "tenancy") {
      const tenancy = context.tenancies.find((t) => t.id === entry.entity_id);
      const unit = tenancy && context.units.find((u) => u.id === tenancy.unit_id);
      return tenancy ? `${unit?.label ?? "?"}, ${partyNames(tenancy, context.tenants)}` : null;
    }
    if (entry.entity_type === "loan") return context.loans.find((l) => l.id === entry.entity_id)?.lender ?? null;
    return null;
  };
}
