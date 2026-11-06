// Every entity of the workspace with a display label, for the document page's link targets
// (its list of links and the target picker). One household holds a handful of each, so it
// is loaded whole: properties → units and loans → tenancies, plus owners and tenants.
import { api, ok } from "@/api/client";
import type { LinkTarget, Loan, Owner, Property, Tenancy, Tenant, Unit } from "@/api/schemas";

import { formatDate } from "./format";

export interface Directory {
  owners: Owner[];
  properties: Property[];
  units: Unit[];
  tenants: Tenant[];
  tenancies: Tenancy[];
  loans: Loan[];
}

export async function loadDirectory(): Promise<Directory> {
  const [owners, properties, tenants] = await Promise.all([
    ok(api.GET("/api/owners")),
    ok(api.GET("/api/properties")),
    ok(api.GET("/api/tenants")),
  ]);
  const perProperty = await Promise.all(
    properties.map(async (property) => {
      const path = { params: { path: { property_id: property.id } } };
      const [units, loans] = await Promise.all([
        ok(api.GET("/api/properties/{property_id}/units", path)),
        ok(api.GET("/api/properties/{property_id}/loans", path)),
      ]);
      return { units, loans };
    }),
  );
  const units = perProperty.flatMap((entry) => entry.units);
  const loans = perProperty.flatMap((entry) => entry.loans);
  const tenancies = (
    await Promise.all(
      units.map((unit) => ok(api.GET("/api/units/{unit_id}/tenancies", { params: { path: { unit_id: unit.id } } }))),
    )
  ).flat();
  return { owners, properties, units, tenants, tenancies, loans };
}

export function tenantName(tenant: Tenant | undefined): string {
  if (!tenant) return "Unbekannter Mieter";
  return tenant.full_name ?? "Mieter (Daten gelöscht)";
}

/** "Max Muster, Erika Muster" for a tenancy's parties, primary first. */
export function partyNames(tenancy: Tenancy, tenants: Tenant[]): string {
  const byId = new Map(tenants.map((tenant) => [tenant.id, tenant]));
  return [...tenancy.parties]
    .sort((a, b) => (a.role === b.role ? 0 : a.role === "primary" ? -1 : 1))
    .map((party) => tenantName(byId.get(party.tenant_id)))
    .join(", ");
}

/** Whether the tenancy runs on `today` (both dates inclusive). */
export function isRunning(tenancy: Tenancy, today: string): boolean {
  return tenancy.start_date <= today && (tenancy.end_date === null || tenancy.end_date >= today);
}

/** The tenancy in force today, else the next one to start. */
export function currentTenancy(tenancies: Tenancy[], today: string): { tenancy: Tenancy; upcoming: boolean } | null {
  const running = tenancies.find((t) => isRunning(t, today));
  if (running) return { tenancy: running, upcoming: false };
  const next = tenancies.find((t) => t.start_date > today);
  return next ? { tenancy: next, upcoming: true } : null;
}

/** "läuft", "beendet" or "beginnt am 01.07.2024", from the dates. */
export function tenancyStatus(tenancy: Tenancy, today: string): string {
  if (tenancy.start_date > today) return `beginnt am ${formatDate(tenancy.start_date)}`;
  return isRunning(tenancy, today) ? "läuft" : "beendet";
}

export interface Target {
  type: LinkTarget;
  id: string;
  label: string;
  href: string;
}

/** Every possible link target with its label and page, grouped in LinkTarget order. */
export function targets(directory: Directory): Target[] {
  const unitById = new Map(directory.units.map((unit) => [unit.id, unit]));
  const propertyById = new Map(directory.properties.map((property) => [property.id, property]));
  const list: Target[] = [];
  for (const owner of directory.owners) {
    list.push({ type: "owner", id: owner.id, label: owner.full_name, href: "/owners" });
  }
  for (const property of directory.properties) {
    list.push({ type: "property", id: property.id, label: property.label, href: `/properties/${property.id}` });
  }
  for (const unit of directory.units) {
    const property = propertyById.get(unit.property_id);
    list.push({ type: "unit", id: unit.id, label: `${property?.label ?? "?"} › ${unit.label}`, href: `/units/${unit.id}` });
  }
  for (const tenant of directory.tenants) {
    list.push({ type: "tenant", id: tenant.id, label: tenantName(tenant), href: `/tenants/${tenant.id}` });
  }
  for (const tenancy of directory.tenancies) {
    const unit = unitById.get(tenancy.unit_id);
    const label = `${unit?.label ?? "?"}: ${partyNames(tenancy, directory.tenants)} (ab ${formatDate(tenancy.start_date)})`;
    list.push({ type: "tenancy", id: tenancy.id, label, href: `/tenancies/${tenancy.id}` });
  }
  for (const loan of directory.loans) {
    const property = propertyById.get(loan.property_id);
    const label = `${loan.lender}${loan.contract_number ? ` ${loan.contract_number}` : ""} (${property?.label ?? "?"})`;
    list.push({ type: "loan", id: loan.id, label, href: `/loans/${loan.id}` });
  }
  return list;
}
