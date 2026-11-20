"use client";

// Immobilien (phase 10 D266, mockup 14-C2): one card per property with its tenancies and its
// owners' shares, and the rows about the property (Darlehen, Kauf & Nebenkosten, Einheiten,
// Unterlagen, Dokumente, Verlauf) that open its page's tabs. Mieter and Eigentümer left the
// menu, so a tapped tenancy or owner opens in the detail panel (D267) with a link to its own
// page; /owners and /tenants keep working. The rent shown is the API's answer for today
// (GET /tenancies/{id}/rent?at=…), as on the property page; nothing is summed here (a total
// rent per property would need its own read, principle 1).
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { Loan, Owner, Property, PropertyOwnership, RentScheduleEntry, Tenancy, Tenant, Unit } from "@/api/schemas";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { currentTenancy } from "@/components/entities";
import { formatSharePercent, todayIso } from "@/components/format";
import { loadPropertyContext } from "@/components/propertyContext";
import { TaxFreeLine } from "@/components/tax";
import { taxFormHref } from "@/components/taxYear";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { properties as messages } from "@/i18n/properties";
import { Icon } from "@/lib/icons";

import "./properties.css";

interface CardTenancy {
  tenancy: Tenancy;
  unit: Unit;
  upcoming: boolean;
  rent: RentScheduleEntry | null;
}

interface PropertyCard {
  property: Property;
  units: Unit[];
  loans: Loan[];
  tenants: Tenant[];
  tenancies: CardTenancy[];
  ownership: PropertyOwnership | null;
}

interface Data {
  cards: PropertyCard[];
  owners: Owner[];
}

async function loadCard(property: Property, today: string): Promise<PropertyCard> {
  const context = await loadPropertyContext(property.id);
  const tenancies = (
    await Promise.all(
      context.units.map(async (unit): Promise<CardTenancy | null> => {
        const found = currentTenancy(
          context.tenancies.filter((t) => t.unit_id === unit.id),
          today,
        );
        if (!found) return null;
        const at = found.upcoming ? found.tenancy.start_date : today;
        const rent = await ok(
          api.GET("/api/tenancies/{tenancy_id}/rent", { params: { path: { tenancy_id: found.tenancy.id }, query: { at } } }),
        );
        return { tenancy: found.tenancy, unit, upcoming: found.upcoming, rent };
      }),
    )
  ).filter((entry): entry is CardTenancy => entry !== null);
  let ownership: PropertyOwnership | null = null;
  try {
    ownership = await ok(api.GET("/api/properties/{property_id}/ownership", { params: { path: { property_id: property.id } } }));
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) throw error; // 404: no shares set yet
  }
  return { property, units: context.units, loans: context.loans, tenants: context.tenants, tenancies, ownership };
}

async function loadData(today: string): Promise<Data> {
  const [list, owners] = await Promise.all([ok(api.GET("/api/properties")), ok(api.GET("/api/owners"))]);
  const cards = await Promise.all(list.map((property) => loadCard(property, today)));
  return { cards, owners };
}

export default function PropertiesPage() {
  const t = useT(messages);
  const today = todayIso();
  const load = useCallback(() => loadData(today), [today]);
  const state = useLoad(load);
  const tenancyDetail = useDetail("tenancy");
  const ownerDetail = useDetail("owner");
  const propertyDetail = useDetail("property");
  const f = useFormat();

  return (
    <section>
      <Breadcrumbs items={[{ label: t.more }, { label: t.title }]} />
      <div className="page-head">
        <h1>{t.title}</h1>
        <span className="spacer" />
        <Link href="/properties/new" className="button-link secondary">
          {t.add}
        </Link>
      </div>
      <Loading state={state}>
        {({ cards, owners }) => {
          const tenancyEntry = cards
            .flatMap((card) => card.tenancies.map((entry) => ({ card, entry })))
            .find(({ entry }) => entry.tenancy.id === tenancyDetail.id);
          const ownerEntry = ownerDetail.id
            ? cards
                .map((card) => ({ card, share: card.ownership?.shares.find((s) => s.owner_id === ownerDetail.id) }))
                .find(({ share }) => share !== undefined)
            : undefined;
          const owner = owners.find((o) => o.id === ownerDetail.id);
          const selectedProperty = cards.find((card) => card.property.id === propertyDetail.id)?.property;
          let panel = null;
          if (selectedProperty) {
            panel = (
              <DetailPanel title={selectedProperty.label} backLabel={t.title} onClose={propertyDetail.close}>
                <div className="stack">
                  <dl className="facts">
                    <div>
                      <dt>{t.address}</dt>
                      <dd>
                        {selectedProperty.address.street} {selectedProperty.address.house_number},{" "}
                        {selectedProperty.address.postal_code} {selectedProperty.address.city}
                      </dd>
                    </div>
                    <div>
                      <dt>{t.notarizedOn}</dt>
                      <dd>{f.date(selectedProperty.notarized_on)}</dd>
                    </div>
                    <div>
                      <dt>{t.purchaseDate}</dt>
                      <dd>{f.date(selectedProperty.purchase_date)}</dd>
                    </div>
                    <div>
                      <dt>{t.livingArea}</dt>
                      <dd>{f.area(selectedProperty.living_area_m2)}</dd>
                    </div>
                  </dl>
                  <Link href={`/properties/${selectedProperty.id}`} className="button-link">
                    {t.openProperty}
                  </Link>
                </div>
              </DetailPanel>
            );
          } else if (tenancyEntry) {
            panel = (
              <DetailPanel
                title={tenancyTitle(tenancyEntry.entry, tenancyEntry.card.tenants, t)}
                backLabel={t.title}
                onClose={tenancyDetail.close}
              >
                <TenancyPanel entry={tenancyEntry.entry} tenants={tenancyEntry.card.tenants} />
              </DetailPanel>
            );
          } else if (owner && ownerEntry?.share) {
            panel = (
              <DetailPanel title={owner.full_name} backLabel={t.title} onClose={ownerDetail.close}>
                <div className="stack">
                  <div>
                    <p className="property-big num">{formatSharePercent(ownerEntry.share.share)}</p>
                    <p className="muted">{t.shareIn(ownerEntry.card.property.label)}</p>
                  </div>
                  <p className="muted">{t.ownerTaxHint}</p>
                  <Link
                    className="button-link secondary"
                    href={taxFormHref("angaben", { year: new Date().getFullYear(), propertyId: null, ownerId: owner.id })}
                  >
                    {t.ownerTaxLink}
                  </Link>
                  <Link href="/owners">{t.ownerPage} ›</Link>
                </div>
              </DetailPanel>
            );
          }
          return (
            <DetailLayout panel={panel}>
              <div className="stack">
                {cards.length === 0 ? <p className="muted">{t.none}</p> : null}
                {cards.map((card) => (
                  <PropertyCardView key={card.property.id} card={card} owners={owners} />
                ))}
                <ul className="detail-rows card">
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

type T = (typeof messages)["de"];

function tenantLabel(tenant: Tenant | undefined, t: T): string {
  if (!tenant) return t.unknownTenant;
  return tenant.full_name ?? t.erasedTenant;
}

function primaryTenant(tenancy: Tenancy, tenants: Tenant[]): Tenant | undefined {
  const party = tenancy.parties.find((p) => p.role === "primary") ?? tenancy.parties[0];
  return tenants.find((tenant) => tenant.id === party?.tenant_id);
}

function tenancyTitle(entry: CardTenancy, tenants: Tenant[], t: T): string {
  const names = entry.tenancy.parties.map((party) => tenantLabel(tenants.find((x) => x.id === party.tenant_id), t));
  return `${entry.unit.label} · ${names.join(", ")}`;
}

function PropertyCardView({ card, owners }: { card: PropertyCard; owners: Owner[] }) {
  const t = useT(messages);
  const f = useFormat();
  const tenancyDetail = useDetail("tenancy");
  const ownerDetail = useDetail("owner");
  const propertyDetail = useDetail("property");
  const { property } = card;
  const base = `/properties/${property.id}`;
  const ownerName = new Map(owners.map((owner) => [owner.id, owner.full_name]));
  const rows: { href: string; icon: Parameters<typeof Icon>[0]["name"]; title: string; hint: string }[] = [
    { href: `${base}?tab=darlehen`, icon: "accounts", title: t.loans, hint: t.loansHint(card.loans.length) },
    { href: `${base}?tab=nebenkosten`, icon: "tax", title: t.purchase, hint: t.purchaseHint },
    { href: `${base}?tab=einheiten`, icon: "properties", title: t.units, hint: t.unitsHint(card.units.length) },
    { href: `${base}?tab=unterlagen`, icon: "inbox", title: t.checklist, hint: t.checklistHint },
    { href: `${base}?tab=dokumente`, icon: "documents", title: t.documents, hint: t.documentsHint },
    { href: `${base}?tab=verlauf`, icon: "overview", title: t.history, hint: t.historyHint },
    { href: `${base}?tab=stammdaten`, icon: "settings", title: t.masterData, hint: t.masterDataHint },
  ];

  return (
    <article className="card property-card" aria-labelledby={`property-${property.id}`}>
      <header className="property-card-head">
        <span className="property-card-icon" aria-hidden="true">
          <Icon name="properties" />
        </span>
        <div>
          <h2 id={`property-${property.id}`}>
            <a {...propertyDetail.linkProps(property.id)}>{property.label}</a>
          </h2>
          <p className="muted">
            {t.kind(property.kind)} ·{" "}
            {property.purchase_date ? t.handedOver(f.date(property.purchase_date)) : t.notYetHandedOver}
          </p>
        </div>
      </header>
      <dl className="property-figures">
        <div>
          <dt>{t.price}</dt>
          <dd className="num">{f.money(property.purchase_price)}</dd>
        </div>
        <div>
          <dt>{t.livingArea}</dt>
          <dd className="num">{f.area(property.living_area_m2)}</dd>
        </div>
      </dl>
      <p className="property-taxfree">
        <TaxFreeLine propertyId={property.id} />
      </p>
      <div className="property-card-columns">
        <div className="stack">
          <section aria-labelledby={`tenants-${property.id}`}>
            <div className="property-col-head">
              <h3 id={`tenants-${property.id}`}>{t.tenants}</h3>
              {card.units[0] ? (
                <Link href={`/tenancies/new?unit=${card.units[0].id}`} className="property-col-action">
                  {t.addTenancy}
                </Link>
              ) : null}
            </div>
            {card.units.length === 0 ? (
              <p className="muted">
                {t.noUnit} <Link href={`${base}/units/new`}>{t.addUnit}</Link>
              </p>
            ) : (
              <ul className="detail-rows">
                {card.tenancies.map((entry) => (
                  <li key={entry.tenancy.id}>
                    <a {...tenancyDetail.linkProps(entry.tenancy.id)} className="detail-row">
                      <span className="detail-row-text">
                        <span className="detail-row-title">
                          {entry.unit.label} · {tenantLabel(primaryTenant(entry.tenancy, card.tenants), t)}
                        </span>
                        <span className="detail-row-sub">
                          {entry.upcoming ? t.startsOn(f.date(entry.tenancy.start_date)) : t.since(f.date(entry.tenancy.start_date))}
                          {entry.rent && entry.tenancy.operating_costs_terms !== "included"
                            ? ` · ${t.terms(entry.tenancy.operating_costs_terms)} ${f.money(entry.rent.operating_costs_prepayment)}`
                            : ""}
                        </span>
                      </span>
                      <span className="num property-row-amount">{entry.rent ? f.money(entry.rent.cold_rent) : "–"}</span>
                      <Icon name="chevronRight" size={18} />
                    </a>
                  </li>
                ))}
                {card.units
                  .filter((unit) => !card.tenancies.some((entry) => entry.unit.id === unit.id))
                  .map((unit) => (
                    <li key={unit.id}>
                      <Link href={`/units/${unit.id}`} className="detail-row">
                        <span className="detail-row-text">
                          <span className="detail-row-title">{unit.label}</span>
                          <span className="detail-row-sub">{t.vacant}</span>
                        </span>
                        <Icon name="chevronRight" size={18} />
                      </Link>
                    </li>
                  ))}
              </ul>
            )}
          </section>
          <section aria-labelledby={`owners-${property.id}`}>
            <div className="property-col-head">
              <h3 id={`owners-${property.id}`}>{t.owners}</h3>
              <span className="muted">{t.shares}</span>
            </div>
            {card.ownership && card.ownership.shares.length > 0 ? (
              <ul className="detail-rows">
                {card.ownership.shares.map((share) => (
                  <li key={share.owner_id}>
                    <a {...ownerDetail.linkProps(share.owner_id)} className="detail-row">
                      <span className="detail-row-text">
                        <span className="detail-row-title">{ownerName.get(share.owner_id) ?? "–"}</span>
                      </span>
                      <span className="num property-row-amount">{formatSharePercent(share.share)}</span>
                      <Icon name="chevronRight" size={18} />
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">
                {t.noShares} <Link href={`${base}?tab=eigentuemer`}>{t.setShares}</Link>
              </p>
            )}
          </section>
        </div>
        <section aria-labelledby={`about-${property.id}`}>
          <div className="property-col-head">
            <h3 id={`about-${property.id}`}>{t.aboutFlat}</h3>
          </div>
          <ul className="detail-rows">
            {rows.map((row) => (
              <li key={row.href}>
                <Link href={row.href} className="detail-row">
                  <Icon name={row.icon} />
                  <span className="detail-row-text">
                    <span className="detail-row-title">{row.title}</span>
                    <span className="detail-row-sub">{row.hint}</span>
                  </span>
                  <Icon name="chevronRight" size={18} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </article>
  );
}

function TenancyPanel({ entry, tenants }: { entry: CardTenancy; tenants: Tenant[] }) {
  const t = useT(messages);
  const f = useFormat();
  const { tenancy, rent } = entry;
  const term = tenancy.end_date
    ? t.ended(f.date(tenancy.end_date))
    : tenancy.fixed_term_until
      ? t.fixedUntil(f.date(tenancy.fixed_term_until))
      : t.open;
  const primary = primaryTenant(tenancy, tenants);
  return (
    <div className="stack">
      <div>
        <p className="property-big num">{rent ? f.money(rent.cold_rent) : "–"}</p>
        <p className="muted">
          {t.coldRent} · {entry.upcoming ? t.startsOn(f.date(tenancy.start_date)) : t.since(f.date(tenancy.start_date))}
        </p>
      </div>
      <dl className="facts">
        <div>
          <dt>{t.operatingCosts}</dt>
          <dd>
            {rent && tenancy.operating_costs_terms !== "included" ? `${f.money(rent.operating_costs_prepayment)} ` : ""}
            {t.terms(tenancy.operating_costs_terms)}
          </dd>
        </div>
        {tenancy.rent_due_day ? (
          <div>
            <dt>{t.dueDay}</dt>
            <dd>{t.dueDayValue(tenancy.rent_due_day)}</dd>
          </div>
        ) : null}
        <div>
          <dt>{t.deposit}</dt>
          <dd>{tenancy.deposit_form === "none" ? t.noDeposit : f.money(tenancy.deposit_amount)}</dd>
        </div>
        <div>
          <dt>{t.rentModel}</dt>
          <dd>
            {t.model(tenancy.rent_model)} · {term}
          </dd>
        </div>
      </dl>
      <Link href={`/tenancies/${tenancy.id}`} className="button-link secondary">
        {t.openTenancy}
      </Link>
      {primary ? <Link href={`/tenants/${primary.id}`}>{t.tenantLink(tenantLabel(primary, t))}</Link> : null}
      <Link href={`/units/${entry.unit.id}`}>{t.unitLink(entry.unit.label)} ›</Link>
    </div>
  );
}
